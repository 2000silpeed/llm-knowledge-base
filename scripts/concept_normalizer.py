"""개념명 정규화 (P5-04)

wiki/concepts/ 내 유사/중복 개념을 탐지하고 정규화합니다.

기능:
  1. 전체 개념 로드 → LLM으로 유사/중복 그룹 탐지
  2. 그룹 내 canonical(정규) 이름 결정
  3. 비정규 개념 파일 → 리다이렉트 파일로 전환
  4. canonical 파일에 내용 병합 (LLM)
  5. 전체 wiki/ 백링크 업데이트 ([[old]] → [[canonical]])
  6. 정규화 보고서 저장 (wiki/_normalization_report.md)

사용 예:
    from scripts.concept_normalizer import normalize_wiki

    result = normalize_wiki()
    # {
    #   "concepts": 25,
    #   "groups_found": 4,
    #   "merged": 4,
    #   "redirects_created": 6,
    #   "backlinks_updated": 12,
    #   "report_path": "wiki/_normalization_report.md",
    # }

CLI:
    kb wiki reorg [--dry-run] [--no-merge] [--no-backlinks]
"""

import json
import logging
import re
from datetime import date
from pathlib import Path

import yaml

from scripts.llm import call_llm as _call_llm
from scripts.token_counter import load_settings, parse_frontmatter

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_PROMPTS_PATH = _PROJECT_ROOT / "config" / "prompts.yaml"

# 배치 처리 기준 (개념 수)
_BATCH_SIZE = 40


# ──────────────────────────────────────────────
# 내부 유틸
# ──────────────────────────────────────────────

def _load_prompts(prompts_path: Path | str | None = None) -> dict:
    if prompts_path is None:
        prompts_path = _DEFAULT_PROMPTS_PATH
    with Path(prompts_path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _render(template: str, variables: dict) -> str:
    def replace(m: re.Match) -> str:
        key = m.group(1).strip()
        return str(variables.get(key, m.group(0)))
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", replace, template)


def _render_frontmatter(meta: dict, body: str) -> str:
    fm = yaml.dump(meta, allow_unicode=True, default_flow_style=False).strip()
    return f"---\n{fm}\n---\n\n{body}"


def _concept_to_slug(name: str) -> str:
    """개념명 → 파일명 슬러그 변환."""
    slug = re.sub(r"[^\w가-힣\-]", "_", name)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "untitled"


def _extract_summary(content: str) -> str:
    """개념 파일 본문에서 요약 추출 (## 핵심 요약 섹션 또는 첫 단락)."""
    # ## 핵심 요약 섹션 찾기
    summary_match = re.search(r"##\s*핵심\s*요약\s*\n([\s\S]*?)(?=\n##|\Z)", content)
    if summary_match:
        return summary_match.group(1).strip()[:300]
    # 첫 비어있지 않은 단락 (# 헤딩 제외)
    for line in content.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("---"):
            return line[:300]
    return ""


def _parse_groups_json(llm_output: str) -> list[dict]:
    """LLM 출력에서 그룹 JSON 배열 파싱.

    기대 형식:
    [
      {"canonical": "정규 개념명", "members": ["유사명1", "유사명2", ...]},
      ...
    ]
    싱글톤(members 1개)은 무의미하므로 필터.
    """
    fence = re.search(r"```(?:json)?\s*\n([\s\S]*?)```", llm_output)
    raw = fence.group(1).strip() if fence else llm_output.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        arr_match = re.search(r"\[[\s\S]*\]", raw)
        if arr_match:
            try:
                data = json.loads(arr_match.group(0))
            except json.JSONDecodeError:
                logger.error("LLM 출력 JSON 파싱 실패:\n%s", llm_output[:500])
                return []
        else:
            logger.error("LLM 출력에서 JSON 배열을 찾을 수 없습니다:\n%s", llm_output[:500])
            return []

    if not isinstance(data, list):
        logger.error("LLM 출력이 JSON 배열이 아닙니다.")
        return []

    groups = []
    for item in data:
        if not isinstance(item, dict):
            continue
        canonical = str(item.get("canonical", "")).strip()
        members = item.get("members", [])
        if not isinstance(members, list):
            continue
        members = [str(m).strip() for m in members if str(m).strip()]
        if canonical and len(members) >= 2:
            groups.append({"canonical": canonical, "members": members})

    return groups


def _strip_fence(text: str) -> str:
    fence_match = re.search(r"```(?:markdown)?\s*\n([\s\S]*?)```", text)
    if fence_match:
        return fence_match.group(1).strip()
    return text.strip()


# ──────────────────────────────────────────────
# 핵심 기능
# ──────────────────────────────────────────────

def load_all_concepts(wiki_root: Path) -> list[dict]:
    """wiki/concepts/ 내 모든 개념 파일을 로드합니다.

    리다이렉트 파일(redirect_to 필드 보유)은 제외합니다.

    Returns:
        [{"slug": str, "name": str, "summary": str, "path": Path, "content": str}, ...]
    """
    concepts_dir = wiki_root / "concepts"
    if not concepts_dir.exists():
        return []

    concepts = []
    for path in sorted(concepts_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)

        # 리다이렉트 파일 건너뜀
        if meta.get("redirect_to"):
            continue

        # H1에서 이름 추출
        h1_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        name = h1_match.group(1).strip() if h1_match else path.stem.replace("_", " ")

        summary = _extract_summary(body)
        concepts.append({
            "slug": path.stem,
            "name": name,
            "summary": summary,
            "path": path,
            "content": text,
        })

    return concepts


def find_duplicate_groups(
    concepts: list[dict],
    settings: dict,
    prompts: dict,
    cache=None,
) -> list[dict]:
    """LLM으로 유사/중복 개념 그룹을 탐지합니다.

    대규모 개념 목록은 배치로 나눠 처리하고 결과를 병합합니다.

    Returns:
        [{"canonical": str, "members": [str, ...]}, ...]
        members는 현재 wiki에 존재하는 개념명 기준.
    """
    if len(concepts) < 2:
        return []

    tmpl = prompts.get("normalize_concepts")
    if not tmpl:
        logger.error("prompts.yaml에 normalize_concepts 프롬프트가 없습니다.")
        return []

    def _call_batch(batch: list[dict]) -> list[dict]:
        concepts_list = "\n".join(
            f"- {c['name']}: {c['summary'][:150]}" for c in batch
        )
        system_prompt = _render(tmpl["system"], {})
        user_prompt = _render(tmpl["user"], {"concepts_list": concepts_list})
        output = _call_llm(system_prompt, user_prompt, settings, cache=cache)
        return _parse_groups_json(output)

    all_known_names = {c["name"] for c in concepts}

    if len(concepts) <= _BATCH_SIZE:
        raw_groups = _call_batch(concepts)
    else:
        # 배치 분할 처리 후 그룹 병합
        raw_groups = []
        for i in range(0, len(concepts), _BATCH_SIZE):
            batch = concepts[i:i + _BATCH_SIZE]
            logger.info("  배치 %d/%d 처리 중...", i // _BATCH_SIZE + 1, (len(concepts) + _BATCH_SIZE - 1) // _BATCH_SIZE)
            batch_groups = _call_batch(batch)
            raw_groups.extend(batch_groups)

    # 필터: members가 실제로 wiki에 존재하는 개념명인지 확인
    # 존재하지 않는 이름은 드롭 (LLM 환각 방지)
    valid_groups = []
    for g in raw_groups:
        valid_members = [m for m in g["members"] if m in all_known_names]
        if len(valid_members) >= 2:
            valid_groups.append({"canonical": g["canonical"], "members": valid_members})

    # 중복 그룹 병합 (다른 배치에서 같은 개념이 여러 그룹에 배정된 경우)
    merged_groups = _merge_overlapping_groups(valid_groups)

    logger.info("  탐지된 중복 그룹: %d개", len(merged_groups))
    return merged_groups


def _merge_overlapping_groups(groups: list[dict]) -> list[dict]:
    """겹치는 멤버를 가진 그룹들을 합칩니다 (Union-Find 방식)."""
    # 각 멤버가 속한 그룹 인덱스 추적
    member_to_group: dict[str, int] = {}
    result: list[set] = []
    canonicals: list[str] = []

    for g in groups:
        involved = set()
        for m in g["members"]:
            if m in member_to_group:
                involved.add(member_to_group[m])

        if not involved:
            idx = len(result)
            result.append(set(g["members"]))
            canonicals.append(g["canonical"])
            for m in g["members"]:
                member_to_group[m] = idx
        else:
            # 겹치는 그룹들 모두 병합
            target_idx = min(involved)
            merged_set = set(g["members"])
            for i in involved:
                merged_set |= result[i]
                result[i] = set()  # 빈 셋으로 마킹 (삭제 예정)
            result[target_idx] = merged_set
            # canonical은 target 그룹의 것 유지, 새로 들어온 건 무시
            for m in merged_set:
                member_to_group[m] = target_idx

    final = []
    for idx, members in enumerate(result):
        if len(members) >= 2:
            final.append({"canonical": canonicals[idx], "members": list(members)})
    return final


def _find_concept_file(name: str, concepts: list[dict]) -> dict | None:
    """이름으로 개념 dict를 찾습니다."""
    for c in concepts:
        if c["name"] == name:
            return c
    return None


def _find_canonical_concept(group: dict, concepts: list[dict]) -> dict | None:
    """그룹의 canonical 이름이 기존 wiki에 있는지 확인하고 가장 적합한 파일 반환.

    1. canonical 이름과 정확히 일치하는 개념 파일 우선
    2. 없으면 가장 내용이 많은 멤버 파일 사용
    """
    canonical_name = group["canonical"]

    # 정확히 일치하는 파일 찾기
    exact = _find_concept_file(canonical_name, concepts)
    if exact:
        return exact

    # 없으면 가장 내용이 많은 멤버 파일
    member_concepts = [c for c in concepts if c["name"] in group["members"]]
    if not member_concepts:
        return None
    return max(member_concepts, key=lambda c: len(c["content"]))


def _merge_concept_files(
    canonical_name: str,
    canonical_file: dict,
    other_files: list[dict],
    settings: dict,
    prompts: dict,
    cache=None,
) -> str:
    """여러 개념 파일 내용을 LLM으로 병합하여 통합 마크다운을 반환합니다."""
    tmpl = prompts.get("merge_concept_files")
    if not tmpl or not other_files:
        return canonical_file["content"]

    others_text = "\n\n---\n\n".join(
        f"## 파일: {c['name']}\n\n{c['content']}" for c in other_files
    )

    system_prompt = _render(tmpl["system"], {})
    user_prompt = _render(tmpl["user"], {
        "canonical_name": canonical_name,
        "canonical_content": canonical_file["content"],
        "other_contents": others_text,
        "today": date.today().isoformat(),
    })

    output = _call_llm(system_prompt, user_prompt, settings, cache=cache)
    merged = _strip_fence(output)

    # LLM 출력이 유효한 마크다운인지 최소 검증
    if len(merged) < 50 or "#" not in merged:
        logger.warning("병합 LLM 출력이 비정상 — canonical 파일 원본 유지")
        return canonical_file["content"]

    return merged


def _write_redirect_file(
    path: Path,
    original_name: str,
    canonical_name: str,
    canonical_slug: str,
    dry_run: bool,
) -> None:
    """비정규 개념 파일을 리다이렉트 파일로 전환합니다."""
    meta = {
        "redirect_to": canonical_slug,
        "original_name": original_name,
        "last_updated": date.today().isoformat(),
    }
    fm = yaml.dump(meta, allow_unicode=True, default_flow_style=False).strip()
    body = (
        f"# {original_name}\n\n"
        f"> **[통합됨]** 이 개념은 [[{canonical_name}]]로 통합되었습니다.\n"
    )
    redirect_content = f"---\n{fm}\n---\n\n{body}"

    if not dry_run:
        path.write_text(redirect_content, encoding="utf-8")
    logger.info("  리다이렉트 생성: %s → %s", path.name, canonical_name)


def _update_backlinks_in_file(
    file_path: Path,
    name_map: dict[str, str],  # {old_name: canonical_name}
    dry_run: bool,
) -> bool:
    """단일 파일 내 [[old_name]] 참조를 [[canonical_name]]으로 교체합니다.

    Returns:
        True if any replacement was made.
    """
    text = file_path.read_text(encoding="utf-8")
    new_text = text

    for old_name, canonical_name in name_map.items():
        if old_name == canonical_name:
            continue
        # [[old_name]] → [[canonical_name]]
        pattern = r"\[\[" + re.escape(old_name) + r"\]\]"
        replacement = f"[[{canonical_name}]]"
        new_text = re.sub(pattern, replacement, new_text)

        # frontmatter related_concepts 배열 내 이름도 교체
        # YAML 배열 항목: "- old_name" 패턴
        yaml_pattern = r"(- )" + re.escape(old_name) + r"(\s*(?:#.*)?\n)"
        new_text = re.sub(yaml_pattern, rf"\g<1>{canonical_name}\g<2>", new_text)

    if new_text != text:
        if not dry_run:
            file_path.write_text(new_text, encoding="utf-8")
        return True
    return False


def _update_all_backlinks(
    wiki_root: Path,
    name_map: dict[str, str],
    dry_run: bool,
) -> list[str]:
    """wiki/ 내 모든 마크다운 파일에서 백링크를 업데이트합니다.

    Returns:
        업데이트된 파일 경로 목록.
    """
    updated = []
    for md_file in wiki_root.rglob("*.md"):
        if _update_backlinks_in_file(md_file, name_map, dry_run):
            updated.append(str(md_file))
    return updated


def _write_report(
    wiki_root: Path,
    groups: list[dict],
    merge_results: list[dict],
    backlinks_updated: list[str],
    dry_run: bool,
) -> str:
    """정규화 보고서를 wiki/_normalization_report.md에 저장합니다."""
    today = date.today().isoformat()
    lines = [
        f"# 개념 정규화 보고서",
        f"",
        f"**실행일:** {today}",
        f"**모드:** {'dry-run (파일 변경 없음)' if dry_run else '실제 적용'}",
        f"",
        f"## 탐지된 중복 그룹 ({len(groups)}개)",
        f"",
    ]

    for g in groups:
        lines.append(f"### {g['canonical']}")
        lines.append(f"- 정규명: **{g['canonical']}**")
        lines.append(f"- 통합된 이름: {', '.join(m for m in g['members'] if m != g['canonical'])}")
        lines.append("")

    lines += [
        f"## 수행 결과",
        f"",
    ]
    for r in merge_results:
        status = r.get("status", "ok")
        lines.append(f"- **{r['canonical']}**: {status} — {r.get('detail', '')}")

    lines += [
        f"",
        f"## 백링크 갱신 파일 ({len(backlinks_updated)}개)",
        f"",
    ]
    for fp in backlinks_updated:
        lines.append(f"- {Path(fp).relative_to(wiki_root) if Path(fp).is_relative_to(wiki_root) else fp}")

    report = "\n".join(lines)
    report_path = wiki_root / "_normalization_report.md"

    if not dry_run:
        report_path.write_text(report, encoding="utf-8")
    logger.info("정규화 보고서 저장: %s", report_path)
    return str(report_path)


# ──────────────────────────────────────────────
# 공개 API
# ──────────────────────────────────────────────

def normalize_wiki(
    wiki_root: Path | None = None,
    *,
    settings: dict | None = None,
    prompts: dict | None = None,
    dry_run: bool = False,
    merge: bool = True,
    update_backlinks: bool = True,
    cache=None,
) -> dict:
    """wiki/concepts/ 내 유사/중복 개념을 탐지하고 정규화합니다.

    Args:
        wiki_root: wiki/ 루트 디렉토리. None이면 settings 기반 자동 설정.
        settings: 설정 dict. None이면 자동 로드.
        prompts: 프롬프트 dict. None이면 자동 로드.
        dry_run: True면 파일을 실제로 변경하지 않음.
        merge: True면 비정규 파일 내용을 canonical에 병합.
        update_backlinks: True면 전체 wiki/ 백링크 업데이트.
        cache: LLM 응답 캐시. None이면 자동 초기화.

    Returns:
        {
            "concepts": int,           # 분석한 개념 수
            "groups_found": int,       # 탐지된 중복 그룹 수
            "merged": int,             # 병합된 그룹 수
            "redirects_created": int,  # 생성된 리다이렉트 파일 수
            "backlinks_updated": int,  # 백링크가 갱신된 파일 수
            "report_path": str,        # 보고서 파일 경로
        }
    """
    if settings is None:
        settings = load_settings()
    if prompts is None:
        prompts = _load_prompts()
    if wiki_root is None:
        wiki_root = _PROJECT_ROOT / settings["paths"]["wiki"]

    if cache is None:
        from scripts.cache import make_cache_from_settings
        cache = make_cache_from_settings(settings)

    wiki_root = Path(wiki_root)

    # ── Step 1: 전체 개념 로드 ──
    concepts = load_all_concepts(wiki_root)
    logger.info("개념 로드: %d개", len(concepts))

    if len(concepts) < 2:
        logger.info("개념이 2개 미만 — 정규화 불필요")
        return {
            "concepts": len(concepts),
            "groups_found": 0,
            "merged": 0,
            "redirects_created": 0,
            "backlinks_updated": 0,
            "report_path": "",
        }

    # ── Step 2: LLM으로 중복 그룹 탐지 ──
    logger.info("중복 그룹 탐지 중 (LLM)...")
    groups = find_duplicate_groups(concepts, settings, prompts, cache)

    if not groups:
        logger.info("중복 그룹이 없습니다.")
        return {
            "concepts": len(concepts),
            "groups_found": 0,
            "merged": 0,
            "redirects_created": 0,
            "backlinks_updated": 0,
            "report_path": "",
        }

    # ── Step 3: 각 그룹 정규화 ──
    # name_map: 비정규 이름 → canonical 이름 (백링크 업데이트용)
    name_map: dict[str, str] = {}
    merge_results = []
    total_redirects = 0

    for group in groups:
        canonical_name = group["canonical"]
        members = group["members"]

        logger.info("그룹 처리: [%s] ← %s", canonical_name, members)

        # canonical 파일 결정
        canonical_concept = _find_canonical_concept(group, concepts)
        if canonical_concept is None:
            logger.warning("  그룹 canonical 파일을 찾을 수 없음 — 건너뜀")
            merge_results.append({
                "canonical": canonical_name,
                "status": "skip",
                "detail": "canonical 파일 없음",
            })
            continue

        # 비정규 멤버 파일들
        other_concepts = [
            c for c in concepts
            if c["name"] in members and c["name"] != canonical_concept["name"]
        ]

        # canonical 파일이 현재 이름과 다르면 파일명 갱신 예정
        canonical_slug = _concept_to_slug(canonical_name)
        canonical_target_path = wiki_root / "concepts" / f"{canonical_slug}.md"

        # ── 병합: 다른 파일 내용 → canonical ──
        if merge and other_concepts:
            logger.info("  병합 중: %d개 파일 → %s", len(other_concepts), canonical_name)
            merged_content = _merge_concept_files(
                canonical_name,
                canonical_concept,
                other_concepts,
                settings,
                prompts,
                cache,
            )
            # canonical 파일에 병합 내용 반영
            if not dry_run:
                canonical_target_path.write_text(merged_content, encoding="utf-8")
                # 기존 파일이 다른 위치에 있으면 canonical_target_path로 이동
                if canonical_concept["path"] != canonical_target_path:
                    canonical_concept["path"].unlink(missing_ok=True)
        elif not dry_run and canonical_concept["path"] != canonical_target_path:
            # 병합 없이 canonical 파일명만 변경
            import shutil
            shutil.move(str(canonical_concept["path"]), str(canonical_target_path))

        # ── 리다이렉트 파일 생성 ──
        for other in other_concepts:
            _write_redirect_file(
                path=other["path"],
                original_name=other["name"],
                canonical_name=canonical_name,
                canonical_slug=canonical_slug,
                dry_run=dry_run,
            )
            total_redirects += 1
            name_map[other["name"]] = canonical_name

        # canonical 이름이 기존 개념명과 다른 경우도 매핑 추가
        if canonical_concept["name"] != canonical_name:
            name_map[canonical_concept["name"]] = canonical_name

        merge_results.append({
            "canonical": canonical_name,
            "status": "ok",
            "detail": f"{len(other_concepts)}개 비정규 파일 리다이렉트 처리",
        })

    # ── Step 4: 백링크 업데이트 ──
    backlinks_updated_files: list[str] = []
    if update_backlinks and name_map:
        logger.info("백링크 업데이트 중 (name_map %d개)...", len(name_map))
        backlinks_updated_files = _update_all_backlinks(wiki_root, name_map, dry_run)
        logger.info("  갱신된 파일: %d개", len(backlinks_updated_files))

    # ── Step 5: 보고서 저장 ──
    report_path = _write_report(
        wiki_root=wiki_root,
        groups=groups,
        merge_results=merge_results,
        backlinks_updated=backlinks_updated_files,
        dry_run=dry_run,
    )

    return {
        "concepts": len(concepts),
        "groups_found": len(groups),
        "merged": len([r for r in merge_results if r["status"] == "ok"]),
        "redirects_created": total_redirects,
        "backlinks_updated": len(backlinks_updated_files),
        "report_path": report_path,
    }
