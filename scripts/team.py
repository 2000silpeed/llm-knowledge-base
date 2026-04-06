"""팀 지식베이스 관리 (P2-06)

공유 raw/ + 개인 wiki/ 구조 지원.

설계:
  - 팀원들은 동일한 raw/ 디렉토리(공유 드라이브, git 서브모듈 등)를 참조
  - 각 팀원은 독립적인 wiki/ 디렉토리를 가짐
  - 설정 파일: config/team.yaml

team.yaml 형식:
  shared_raw: ../shared/raw      # 공유 raw 디렉토리 (절대/상대 경로)
  member: alice                  # 현재 멤버 ID
  members:
    - id: alice
      wiki: wiki/alice           # 개인 wiki 경로 (기본: wiki/{id}/)
    - id: bob
      wiki: /absolute/path/wiki

CLI 사용 예:
  kb team init ../shared/raw alice          # 팀 설정 초기화
  kb team status                            # 팀 전체 현황
  kb team add bob ../bob_wiki               # 팀원 추가

  # 팀 모드에서 compile/query — 개인 wiki 자동 사용
  kb compile --changed
  kb query "딥러닝이 뭐야?"
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_TEAM_CONFIG_PATH = _PROJECT_ROOT / "config" / "team.yaml"


# ──────────────────────────────────────────────
# 설정 로드
# ──────────────────────────────────────────────

def load_team_config(config_path: Path | str | None = None) -> dict | None:
    """team.yaml 로드. 파일이 없으면 None 반환 (팀 모드 비활성)."""
    p = Path(config_path) if config_path else _DEFAULT_TEAM_CONFIG_PATH
    if not p.exists():
        return None
    with p.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_team_config(config: dict, config_path: Path | str | None = None) -> Path:
    """team.yaml 저장."""
    p = Path(config_path) if config_path else _DEFAULT_TEAM_CONFIG_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return p


# ──────────────────────────────────────────────
# 경로 해석
# ──────────────────────────────────────────────

def _resolve(path_str: str, project_root: Path) -> Path:
    """상대 경로면 project_root 기준 절대 경로로 변환."""
    p = Path(path_str)
    if p.is_absolute():
        return p
    return (project_root / p).resolve()


def get_raw_dir(
    settings: dict,
    team_config: dict | None,
    project_root: Path = _PROJECT_ROOT,
) -> Path:
    """현재 설정에서 raw/ 디렉토리 경로를 반환합니다.

    팀 모드 활성 시 shared_raw를, 아니면 settings.yaml 기준 raw 경로를 반환.
    """
    if team_config and team_config.get("shared_raw"):
        return _resolve(team_config["shared_raw"], project_root)
    return project_root / settings["paths"]["raw"]


def get_wiki_dir(
    settings: dict,
    team_config: dict | None,
    project_root: Path = _PROJECT_ROOT,
    member_id: str | None = None,
) -> Path:
    """현재 멤버의 wiki/ 디렉토리 경로를 반환합니다.

    팀 모드 활성 시 member_id(또는 team_config['member'])의 wiki 경로를,
    아니면 settings.yaml 기준 wiki 경로를 반환.
    """
    if team_config:
        mid = member_id or team_config.get("member")
        if mid:
            for m in team_config.get("members", []):
                if m.get("id") == mid:
                    return _resolve(m["wiki"], project_root)
            # members 목록에 없으면 기본 경로
            logger.warning("멤버 '%s'의 wiki 경로 미등록 → wiki/%s/ 사용", mid, mid)
            return project_root / settings["paths"]["wiki"] / mid

    return project_root / settings["paths"]["wiki"]


# ──────────────────────────────────────────────
# 초기화 / 팀원 관리
# ──────────────────────────────────────────────

def init_team(
    shared_raw: str,
    member_id: str,
    wiki_path: str | None = None,
    config_path: Path | str | None = None,
    project_root: Path = _PROJECT_ROOT,
) -> Path:
    """team.yaml을 초기화합니다.

    Args:
        shared_raw: 공유 raw 디렉토리 경로 (절대 또는 상대)
        member_id:  현재 멤버 ID (예: "alice")
        wiki_path:  개인 wiki 경로 (기본: wiki/{member_id}/)
        config_path: 저장 경로 (기본: config/team.yaml)
        project_root: 프로젝트 루트 (경로 검증용)

    Returns:
        저장된 team.yaml 경로
    """
    wiki = wiki_path or f"wiki/{member_id}"
    config: dict = {
        "shared_raw": shared_raw,
        "member": member_id,
        "members": [
            {"id": member_id, "wiki": wiki},
        ],
    }
    p = save_team_config(config, config_path)
    logger.info("팀 설정 초기화: %s", p)
    return p


def add_member(
    member_id: str,
    wiki_path: str | None = None,
    config_path: Path | str | None = None,
) -> dict:
    """team.yaml에 팀원을 추가합니다.

    Returns:
        업데이트된 팀 설정 dict
    """
    config = load_team_config(config_path)
    if config is None:
        raise FileNotFoundError("team.yaml이 없습니다. 먼저 `kb team init`을 실행하세요.")

    members = config.get("members", [])
    for m in members:
        if m.get("id") == member_id:
            raise ValueError(f"멤버 '{member_id}'가 이미 존재합니다.")

    wiki = wiki_path or f"wiki/{member_id}"
    members.append({"id": member_id, "wiki": wiki})
    config["members"] = members
    save_team_config(config, config_path)
    logger.info("멤버 추가: %s → %s", member_id, wiki)
    return config


# ──────────────────────────────────────────────
# 현황 조회
# ──────────────────────────────────────────────

def team_status(
    settings: dict,
    team_config: dict,
    project_root: Path = _PROJECT_ROOT,
) -> dict:
    """팀 전체 현황을 반환합니다.

    Returns:
        {
            "shared_raw": str,       # 공유 raw 경로
            "raw_count": int,        # 공유 raw 파일 수
            "current_member": str,   # 현재 멤버 ID
            "members": [
                {"id": str, "wiki": str, "concepts": int, "explorations": int}
            ]
        }
    """
    shared_raw = get_raw_dir(settings, team_config, project_root)
    images_dir = shared_raw / "images"
    if shared_raw.exists():
        raw_files = [
            f for f in shared_raw.rglob("*.md")
            if images_dir not in f.parents
        ]
    else:
        raw_files = []

    members_info = []
    for m in team_config.get("members", []):
        wiki = _resolve(m["wiki"], project_root)
        concepts_dir = wiki / "concepts"
        explorations_dir = wiki / "explorations"

        n_concepts = len(list(concepts_dir.glob("*.md"))) if concepts_dir.exists() else 0
        n_exp = len(list(explorations_dir.glob("*.md"))) if explorations_dir.exists() else 0

        members_info.append({
            "id": m["id"],
            "wiki": str(wiki),
            "concepts": n_concepts,
            "explorations": n_exp,
        })

    return {
        "shared_raw": str(shared_raw),
        "raw_count": len(raw_files),
        "current_member": team_config.get("member", "(미설정)"),
        "members": members_info,
    }
