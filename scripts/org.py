"""조직 단위 지식 관리 (P3-03)

P2-06(팀)의 상위 계층으로 조직(Organization) > 팀(Team) > 멤버(Member) 구조를 제공합니다.

주요 기능:
  - 조직 단위 설정 (config/org.yaml)
  - RBAC: admin / editor / viewer 역할 기반 접근 제어
  - 조직 공유 위키 (wiki/_org/) — 전체 공유 지식
  - 활동 로그 (config/org_activity.jsonl) — 누가 무엇을 했는지 추적
  - 조직 전체 통계 (팀별, 멤버별)

org.yaml 형식:
  org_name: Acme Corp
  created_at: "2026-04-06"
  org_wiki: wiki/_org          # 조직 공유 위키 경로 (절대/상대)
  teams:
    - id: platform
      name: Platform Team
      shared_raw: ../shared/raw
      members:
        - id: alice
          role: admin           # admin | editor | viewer
          wiki: wiki/alice
        - id: bob
          role: editor
          wiki: wiki/bob
    - id: ml
      name: ML Team
      shared_raw: ../ml_raw
      members:
        - id: carol
          role: admin
          wiki: wiki/carol

역할 권한:
  admin  — 조직 설정 변경, 멤버 추가/삭제, 위키 컴파일, 인제스트
  editor — 위키 컴파일, 인제스트 (raw 추가)
  viewer — 위키 조회만 가능 (읽기 전용)

CLI 사용 예:
  kb org init "Acme Corp"                              # 조직 초기화
  kb org team create platform "Platform Team" ../raw   # 팀 생성
  kb org member add platform alice --role admin        # 멤버 추가
  kb org member add platform bob --role editor --wiki wiki/bob
  kb org stats                                         # 조직 전체 통계
  kb org log [--limit 20]                              # 최근 활동 로그
  kb org wiki compile [--team platform]                # 조직 공유 위키 컴파일
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_ORG_CONFIG_PATH = _PROJECT_ROOT / "config" / "org.yaml"
_DEFAULT_ACTIVITY_LOG_PATH = _PROJECT_ROOT / "config" / "org_activity.jsonl"

Role = Literal["admin", "editor", "viewer"]

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin":  {"view", "ingest", "compile", "manage_members", "manage_org"},
    "editor": {"view", "ingest", "compile"},
    "viewer": {"view"},
}


# ──────────────────────────────────────────────
# 설정 로드 / 저장
# ──────────────────────────────────────────────

def load_org_config(config_path: Path | str | None = None) -> dict | None:
    """org.yaml 로드. 파일이 없으면 None 반환."""
    p = Path(config_path) if config_path else _DEFAULT_ORG_CONFIG_PATH
    if not p.exists():
        return None
    with p.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_org_config(config: dict, config_path: Path | str | None = None) -> Path:
    """org.yaml 저장."""
    p = Path(config_path) if config_path else _DEFAULT_ORG_CONFIG_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return p


# ──────────────────────────────────────────────
# 경로 해석
# ──────────────────────────────────────────────

def _resolve(path_str: str, base: Path = _PROJECT_ROOT) -> Path:
    p = Path(path_str)
    if p.is_absolute():
        return p
    return (base / p).resolve()


def get_org_wiki_dir(
    org_config: dict,
    project_root: Path = _PROJECT_ROOT,
) -> Path:
    """조직 공유 위키 디렉토리 경로 반환."""
    return _resolve(org_config.get("org_wiki", "wiki/_org"), project_root)


# ──────────────────────────────────────────────
# 초기화
# ──────────────────────────────────────────────

def init_org(
    org_name: str,
    org_wiki: str = "wiki/_org",
    config_path: Path | str | None = None,
) -> Path:
    """조직을 초기화합니다.

    Args:
        org_name:   조직 이름 (예: "Acme Corp")
        org_wiki:   조직 공유 위키 경로 (기본: wiki/_org)
        config_path: 저장 경로 (기본: config/org.yaml)

    Returns:
        저장된 org.yaml 경로
    """
    existing = load_org_config(config_path)
    if existing is not None:
        raise FileExistsError(
            f"조직이 이미 초기화되어 있습니다 ({_DEFAULT_ORG_CONFIG_PATH}). "
            "덮어쓰려면 파일을 삭제 후 재실행하세요."
        )

    config: dict = {
        "org_name": org_name,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "org_wiki": org_wiki,
        "teams": [],
    }
    p = save_org_config(config, config_path)
    logger.info("조직 초기화: %s → %s", org_name, p)
    return p


# ──────────────────────────────────────────────
# 팀 관리
# ──────────────────────────────────────────────

def create_team(
    team_id: str,
    team_name: str,
    shared_raw: str,
    config_path: Path | str | None = None,
) -> dict:
    """org.yaml에 팀을 추가합니다.

    Args:
        team_id:    팀 식별자 (예: "platform")
        team_name:  팀 표시 이름 (예: "Platform Team")
        shared_raw: 이 팀의 공유 raw 디렉토리 경로

    Returns:
        업데이트된 조직 설정 dict
    """
    config = _require_org_config(config_path)
    teams = config.setdefault("teams", [])

    if any(t["id"] == team_id for t in teams):
        raise ValueError(f"팀 '{team_id}'가 이미 존재합니다.")

    teams.append({
        "id": team_id,
        "name": team_name,
        "shared_raw": shared_raw,
        "members": [],
    })
    save_org_config(config, config_path)
    logger.info("팀 생성: %s (%s)", team_id, team_name)
    return config


def list_teams(config_path: Path | str | None = None) -> list[dict]:
    """모든 팀 목록 반환."""
    config = load_org_config(config_path)
    if config is None:
        return []
    return config.get("teams", [])


# ──────────────────────────────────────────────
# 멤버 관리
# ──────────────────────────────────────────────

def add_member(
    team_id: str,
    member_id: str,
    role: Role = "viewer",
    wiki_path: str | None = None,
    config_path: Path | str | None = None,
) -> dict:
    """팀에 멤버를 추가합니다.

    Args:
        team_id:    소속 팀 ID
        member_id:  멤버 식별자 (예: "alice")
        role:       역할 (admin | editor | viewer)
        wiki_path:  개인 위키 경로 (기본: wiki/{member_id})

    Returns:
        업데이트된 조직 설정 dict
    """
    if role not in ROLE_PERMISSIONS:
        raise ValueError(f"유효하지 않은 역할: '{role}'. admin/editor/viewer 중 선택하세요.")

    config = _require_org_config(config_path)
    team = _find_team(config, team_id)

    members = team.setdefault("members", [])
    if any(m["id"] == member_id for m in members):
        raise ValueError(f"멤버 '{member_id}'가 팀 '{team_id}'에 이미 존재합니다.")

    members.append({
        "id": member_id,
        "role": role,
        "wiki": wiki_path or f"wiki/{member_id}",
    })
    save_org_config(config, config_path)
    log_activity(member_id, team_id, "member_added", f"role={role}")
    logger.info("멤버 추가: %s → 팀 %s (역할: %s)", member_id, team_id, role)
    return config


def update_member_role(
    team_id: str,
    member_id: str,
    new_role: Role,
    config_path: Path | str | None = None,
) -> dict:
    """멤버 역할을 변경합니다.

    Returns:
        업데이트된 조직 설정 dict
    """
    if new_role not in ROLE_PERMISSIONS:
        raise ValueError(f"유효하지 않은 역할: '{new_role}'")

    config = _require_org_config(config_path)
    team = _find_team(config, team_id)
    member = _find_member(team, member_id)

    old_role = member["role"]
    member["role"] = new_role
    save_org_config(config, config_path)
    log_activity(member_id, team_id, "role_changed", f"{old_role} → {new_role}")
    return config


def remove_member(
    team_id: str,
    member_id: str,
    config_path: Path | str | None = None,
) -> dict:
    """팀에서 멤버를 제거합니다."""
    config = _require_org_config(config_path)
    team = _find_team(config, team_id)
    members = team.get("members", [])
    if not any(m["id"] == member_id for m in members):
        raise ValueError(f"멤버 '{member_id}'를 팀 '{team_id}'에서 찾을 수 없습니다.")

    team["members"] = [m for m in members if m["id"] != member_id]
    save_org_config(config, config_path)
    log_activity(member_id, team_id, "member_removed", "")
    return config


def list_members(
    team_id: str | None = None,
    config_path: Path | str | None = None,
) -> list[dict]:
    """멤버 목록 반환 (팀 필터 선택).

    Returns:
        [{"id": str, "team": str, "role": str, "wiki": str}, ...]
    """
    config = load_org_config(config_path)
    if config is None:
        return []

    result = []
    for team in config.get("teams", []):
        if team_id and team["id"] != team_id:
            continue
        for m in team.get("members", []):
            result.append({
                "id": m["id"],
                "team": team["id"],
                "team_name": team.get("name", team["id"]),
                "role": m.get("role", "viewer"),
                "wiki": m.get("wiki", f"wiki/{m['id']}"),
            })
    return result


# ──────────────────────────────────────────────
# 권한 확인
# ──────────────────────────────────────────────

def check_permission(
    member_id: str,
    permission: str,
    config_path: Path | str | None = None,
) -> bool:
    """특정 멤버가 해당 권한을 가지고 있는지 확인합니다.

    permission: "view" | "ingest" | "compile" | "manage_members" | "manage_org"
    """
    for m in list_members(config_path=config_path):
        if m["id"] == member_id:
            role = m["role"]
            return permission in ROLE_PERMISSIONS.get(role, set())
    return False


def get_member_role(
    member_id: str,
    team_id: str | None = None,
    config_path: Path | str | None = None,
) -> str | None:
    """멤버 역할 반환. 팀 ID 지정 시 해당 팀에서만 조회."""
    for m in list_members(team_id, config_path=config_path):
        if m["id"] == member_id:
            return m["role"]
    return None


# ──────────────────────────────────────────────
# 활동 로그
# ──────────────────────────────────────────────

def log_activity(
    member_id: str,
    team_id: str,
    action: str,
    detail: str = "",
    log_path: Path | str | None = None,
) -> None:
    """활동 로그를 JSONL 파일에 기록합니다.

    Args:
        member_id: 작업 수행 멤버
        team_id:   소속 팀
        action:    작업 유형 (ingest, compile, member_added, role_changed, ...)
        detail:    추가 상세 정보
    """
    p = Path(log_path) if log_path else _DEFAULT_ACTIVITY_LOG_PATH
    p.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "member": member_id,
        "team": team_id,
        "action": action,
        "detail": detail,
    }
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def get_activity_log(
    limit: int = 50,
    member_id: str | None = None,
    team_id: str | None = None,
    log_path: Path | str | None = None,
) -> list[dict]:
    """활동 로그를 최신순으로 반환합니다.

    Args:
        limit:     최대 반환 건수
        member_id: 필터링할 멤버 ID (None이면 전체)
        team_id:   필터링할 팀 ID (None이면 전체)

    Returns:
        [{"ts": str, "member": str, "team": str, "action": str, "detail": str}, ...]
    """
    p = Path(log_path) if log_path else _DEFAULT_ACTIVITY_LOG_PATH
    if not p.exists():
        return []

    entries = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if member_id and entry.get("member") != member_id:
                continue
            if team_id and entry.get("team") != team_id:
                continue
            entries.append(entry)

    # 최신순 정렬 후 limit 적용
    entries.sort(key=lambda e: e.get("ts", ""), reverse=True)
    return entries[:limit]


# ──────────────────────────────────────────────
# 통계
# ──────────────────────────────────────────────

def org_stats(
    org_config: dict,
    project_root: Path = _PROJECT_ROOT,
) -> dict:
    """조직 전체 통계를 반환합니다.

    Returns:
        {
            "org_name": str,
            "org_wiki": str,
            "org_wiki_concepts": int,
            "teams": [
                {
                    "id": str,
                    "name": str,
                    "shared_raw": str,
                    "raw_count": int,
                    "member_count": int,
                    "members": [
                        {"id": str, "role": str, "wiki": str,
                         "concepts": int, "explorations": int}
                    ]
                }
            ],
            "total_raw": int,
            "total_members": int,
            "total_concepts": int,
        }
    """
    org_wiki_dir = get_org_wiki_dir(org_config, project_root)
    org_concepts_dir = org_wiki_dir / "concepts"
    org_wiki_count = (
        len(list(org_concepts_dir.glob("*.md")))
        if org_concepts_dir.exists()
        else 0
    )

    teams_info = []
    total_raw = 0
    total_concepts = 0

    for team in org_config.get("teams", []):
        raw_dir = _resolve(team.get("shared_raw", "raw"), project_root)
        images_dir = raw_dir / "images"
        if raw_dir.exists():
            raw_files = [
                f for f in raw_dir.rglob("*.md")
                if images_dir not in f.parents
            ]
            raw_count = len(raw_files)
        else:
            raw_count = 0

        members_info = []
        for m in team.get("members", []):
            wiki = _resolve(m.get("wiki", f"wiki/{m['id']}"), project_root)
            concepts_dir = wiki / "concepts"
            exp_dir = wiki / "explorations"
            n_concepts = len(list(concepts_dir.glob("*.md"))) if concepts_dir.exists() else 0
            n_exp = len(list(exp_dir.glob("*.md"))) if exp_dir.exists() else 0
            total_concepts += n_concepts
            members_info.append({
                "id": m["id"],
                "role": m.get("role", "viewer"),
                "wiki": str(wiki),
                "concepts": n_concepts,
                "explorations": n_exp,
            })

        total_raw += raw_count
        teams_info.append({
            "id": team["id"],
            "name": team.get("name", team["id"]),
            "shared_raw": str(raw_dir),
            "raw_count": raw_count,
            "member_count": len(members_info),
            "members": members_info,
        })

    return {
        "org_name": org_config.get("org_name", "(이름 없음)"),
        "created_at": org_config.get("created_at", ""),
        "org_wiki": str(org_wiki_dir),
        "org_wiki_concepts": org_wiki_count,
        "teams": teams_info,
        "total_raw": total_raw,
        "total_members": sum(t["member_count"] for t in teams_info),
        "total_concepts": total_concepts + org_wiki_count,
    }


# ──────────────────────────────────────────────
# 조직 공유 위키 컴파일
# ──────────────────────────────────────────────

def compile_org_wiki(
    org_config: dict,
    settings: dict,
    team_id: str | None = None,
    project_root: Path = _PROJECT_ROOT,
    cache=None,
) -> list[Path]:
    """각 팀의 개인 위키를 집계해 조직 공유 위키를 컴파일합니다.

    동작:
      1. 각 팀(또는 지정 팀)의 모든 멤버 wiki/concepts/*.md 파일을 수집
      2. 동일 개념 파일(슬러그 일치)이 여러 멤버에 있으면 LLM으로 병합
      3. 신규 개념은 org_wiki/concepts/ 에 바로 복사
      4. 결과를 org_wiki/_index.md 및 _summaries.md 로 갱신

    Args:
        org_config: 조직 설정 dict
        settings:   config/settings.yaml dict
        team_id:    None이면 전체 팀, 지정 시 해당 팀만
        cache:      CacheStore 인스턴스 (선택)

    Returns:
        생성/갱신된 파일 경로 목록
    """
    import shutil
    from scripts.compile import compile_document
    from scripts.index_updater import update_index

    org_wiki_dir = get_org_wiki_dir(org_config, project_root)
    org_concepts_dir = org_wiki_dir / "concepts"
    org_concepts_dir.mkdir(parents=True, exist_ok=True)

    # 팀별 모든 멤버 위키에서 개념 파일 수집
    # slug → [(member_id, Path), ...]
    concept_map: dict[str, list[tuple[str, Path]]] = {}

    for team in org_config.get("teams", []):
        if team_id and team["id"] != team_id:
            continue
        for m in team.get("members", []):
            wiki = _resolve(m.get("wiki", f"wiki/{m['id']}"), project_root)
            concepts_dir = wiki / "concepts"
            if not concepts_dir.exists():
                continue
            for f in sorted(concepts_dir.glob("*.md")):
                slug = f.stem
                concept_map.setdefault(slug, []).append((m["id"], f))

    generated: list[Path] = []

    for slug, sources in concept_map.items():
        dest = org_concepts_dir / f"{slug}.md"

        if len(sources) == 1:
            # 단일 소스 — 그대로 복사
            _member_id, src = sources[0]
            shutil.copy2(src, dest)
            generated.append(dest)
            logger.debug("복사: %s → %s", src, dest)

        else:
            # 다중 소스 — LLM으로 병합
            combined_text = f"# {slug} — 병합 컨텍스트\n\n"
            for member_id, src in sources:
                combined_text += f"## {member_id}의 버전\n\n"
                combined_text += src.read_text(encoding="utf-8") + "\n\n"

            # 임시 파일에 저장 후 compile
            import tempfile
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(combined_text)
                tmp_path = Path(tmp.name)

            try:
                merged = compile_document(
                    tmp_path,
                    settings,
                    output_dir=org_concepts_dir,
                    cache=cache,
                )
                if merged:
                    generated.append(merged)
            finally:
                tmp_path.unlink(missing_ok=True)

    # 인덱스 갱신
    if generated:
        update_index(org_wiki_dir, settings)

    logger.info("조직 공유 위키 컴파일 완료: %d개 개념", len(generated))
    return generated


# ──────────────────────────────────────────────
# 내부 헬퍼
# ──────────────────────────────────────────────

def _require_org_config(config_path: Path | str | None) -> dict:
    """org.yaml을 로드하거나, 없으면 예외를 발생시킵니다."""
    config = load_org_config(config_path)
    if config is None:
        raise FileNotFoundError(
            "org.yaml이 없습니다. 먼저 `kb org init <org-name>` 을 실행하세요."
        )
    return config


def _find_team(config: dict, team_id: str) -> dict:
    for t in config.get("teams", []):
        if t["id"] == team_id:
            return t
    raise ValueError(f"팀 '{team_id}'를 찾을 수 없습니다.")


def _find_member(team: dict, member_id: str) -> dict:
    for m in team.get("members", []):
        if m["id"] == member_id:
            return m
    raise ValueError(f"멤버 '{member_id}'를 팀 '{team['id']}'에서 찾을 수 없습니다.")
