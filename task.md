# task.md — 작업 목록

> 기준 문서: `LLM_지식베이스_기획서.md`
> 상태 범례: `[ ]` 미시작 · `[~]` 진행중 · `[x]` 완료 · `[!]` 블로킹

---

## Phase 1 — MVP (4주)

### INFRA — 프로젝트 기반 세팅

- [x] **INFRA-01** 프로젝트 디렉토리 구조 생성
  - `raw/`, `wiki/`, `config/`, `scripts/` 초기화
  - `pyproject.toml` 작성 (의존성: trafilatura, pymupdf, openpyxl, python-pptx, python-docx, typer, rich, watchdog, anthropic)
  - `config/settings.yaml` 초안 작성 (모델 프로필 포함)
  - `config/prompts.yaml` 초안 작성 (컴파일·쿼리 프롬프트 템플릿)

- [x] **INFRA-02** 토큰 카운터 유틸리티
  - 파일 → 토큰 수 추정 함수
  - 가용 토큰 예산 계산 함수 (settings.yaml 기반)
  - 청킹 필요 여부 판단 로직

---

### W1 — 인제스트 파이프라인

- [x] **W1-01** 웹 아티클 인제스터
  - URL → trafilatura로 본문 추출 → 마크다운 변환
  - 이미지 로컬 저장 (외부 URL → `raw/images/` 다운로드)
  - 메타데이터 기록: `source_url`, `collected_at`, `title`
  - 출력: `raw/articles/{날짜}_{슬러그}.md`

- [x] **W1-02** PDF 인제스터
  - pymupdf로 텍스트 + 이미지 추출
  - 페이지 구조 → 마크다운 헤딩 변환
  - 출력: `raw/papers/{파일명}.md` + `raw/images/` 하위 이미지

- [x] **W1-03** Excel 인제스터
  - openpyxl로 시트별 파싱
  - 셀 데이터 → 마크다운 테이블
  - 수식: 계산값 + `[formula: ...]` 주석 병기
  - 차트: 이미지 추출 → Vision API 캡션 생성
  - 청킹: 1000행 단위 분할, 컬럼 헤더 반복 포함
  - 출력: `raw/office/{파일명}.md` + `.meta.yaml`

- [x] **W1-04** PowerPoint 인제스터
  - python-pptx로 슬라이드별 파싱
  - `## Slide N: 제목` 형식 변환
  - 슬라이드 이미지 → Vision API 캡션
  - 발표자 노트 → `> Note:` 블록쿼트
  - 청킹: 10슬라이드 단위, 전체 목차 반복 포함
  - 출력: `raw/office/{파일명}.md` + `.meta.yaml`

- [x] **W1-05** Word 인제스터
  - python-docx로 제목 계층(H1/H2/H3) → #/##/### 변환
  - 표 → 마크다운 테이블
  - 각주/미주 → 문서 하단 모음
  - 청킹: H2 단위 분할, 200토큰 overlap
  - 출력: `raw/office/{파일명}.md` + `.meta.yaml`

- [x] **W1-06** 청킹 엔진 (공통)
  - 단일 패스 / Map-Reduce / 계층 트리 자동 선택 로직
  - 청크 헤더 자동 삽입 `[문서명 / 섹션명 / N개 중 K번째]`
  - overlap 삽입 (앞 청크 마지막 200토큰)
  - `.meta.yaml` 생성

---

### W2 — LLM 컴파일러

- [x] **W2-01** 단일 문서 컴파일러 (기본)
  - 마크다운 문서 → LLM → wiki 항목 생성
  - 출력: `wiki/concepts/{개념명}.md`
  - 항목 형식: frontmatter(`last_updated`, `source_files`) + 본문 + 백링크 섹션

- [x] **W2-02** 청크 Map-Reduce 컴파일러
  - 청크별 부분 요약 생성 (병렬 처리)
  - 부분 요약들 → 최종 통합 wiki 항목
  - 계층적 요약 트리 (초대형 문서)

- [x] **W2-03** 인덱스 자동 갱신
  - `wiki/_index.md` 항목 추가/갱신 로직
  - `wiki/_summaries.md` 재생성 로직
  - 개념 간 관계(유사/대립/상위/하위) 백링크 삽입

- [x] **W2-04** 증분 컴파일러
  - `raw/` 파일 해시 감시 (watchdog)
  - 변경된 파일과 연관 concept 파일만 선택적 갱신
  - 충돌(상충 내용) 감지 → `wiki/conflicts/` 기록

---

### W3 — 질의 엔진

- [x] **W3-01** 기본 질의 처리
  - 우선순위 기반 컨텍스트 채우기 (토큰 예산 내)
  - Priority 1: `_index.md` + `_summaries.md`
  - Priority 2: 관련 concept 파일 (관련도 순)
  - Priority 3: explorations 관련 항목

- [x] **W3-02** 컨텍스트 압축 fallback
  - 초과 시 1단계: concept 파일 첫 단락만
  - 초과 시 2단계: summaries 버전으로 교체
  - 초과 시 3단계: 서브 질문 분해 → 다중 쿼리 → 통합

- [x] **W3-03** 탐색 결과 저장
  - 답변 → `wiki/explorations/YYYY-MM-DD_{질문요약}.md` 자동 저장
  - 새 개념 추출 → `wiki/concepts/` 자동 추가 제안
  - 갭 항목 → `wiki/gaps.md` 누적

---

### W4 — 통합 및 CLI

- [x] **W4-01** CLI 인터페이스 (typer + rich)
  - `kb ingest <파일/URL>` — 인제스트
  - `kb compile [--all | --changed]` — 위키 컴파일
  - `kb query "<질문>"` — 질의
  - `kb status` — 현황 요약 (raw 건수, wiki 건수, gaps 수)

- [x] **W4-02** Obsidian 연동 확인
  - `wiki/` 폴더를 Obsidian vault로 열기
  - 백링크 렌더링 확인
  - 그래프 뷰 동작 확인

- [x] **W4-03** MVP 통합 테스트
  - 자료 50건 인제스트 후 위키 자동 생성 검증
  - 복합 질문 5개 테스트
  - 탐색 결과 → 위키 재편입 루프 1회 완성

---

## Phase 2 — 제품화 (8주)

- [x] **P2-01** 웹 UI — 마크다운 렌더링 + 검색 (Next.js + MDX)
- [x] **P2-02** 개념 그래프 뷰 (D3.js)
- [x] **P2-03** YouTube 자막 인제스터
- [x] **P2-04** GitHub 레포 인제스터
- [x] **P2-05** 위키 공유 기능 (읽기 전용 링크)
- [x] **P2-06** 팀 지식베이스 (공유 raw/ + 개인 wiki/)
- [x] **P2-07** 대용량 성능 최적화 (1000건+)
- [x] **P2-08** API 비용 최적화 (응답 캐싱, 청크 전략)

---

## Phase 3 — SaaS (추후)

- [x] **P3-01** 클라우드 호스팅
- [x] **P3-02** 모바일 클리퍼 앱 (iOS/Android)
- [x] **P3-03** 조직 단위 지식 관리
- [x] **P3-04** 외부 연동 API

---

## Phase 4 — UX 개선

- [x] **P4-01** 원본 자료 전문 보기 및 편집 (`/raw`)
  - 인제스트 파일 목록 (섹션별, 최신순)
  - 마크다운 에디터 (편집 / 미리보기 / 분할 모드)
  - `GET|PUT /api/raw/[section]/[slug]` API
  - Ctrl+S 단축키, `.meta.yaml` 표시

- [x] **P4-02** 파일 업로드 UI (드래그 앤 드롭)
  - 웹에서 직접 PDF/Word/Excel 파일 업로드 → 자동 인제스트
  - 업로드 진행률 표시, 완료 알림

- [x] **P4-03** 동적 페이지 인제스트
  - Playwright fallback (trafilatura 실패 시 헤드리스 Chrome)
  - `rendered_with: playwright` frontmatter 기록

---

## Phase 5 — 컴파일러 핵심 재설계 ⭐ 최우선

> **배경:** 현재 컴파일은 "파일 1개 → 개념 파일 1개"로 사실상 파일 요약기.
> 진짜 지식 베이스는 개념 단위 분해 + 여러 출처 병합이 핵심.

- [x] **P5-01** 개념 추출 단계 (Step 1) 구현
  - LLM이 문서 1개에서 핵심 개념 목록 추출 (5~15개)
  - 각 개념의 범위·맥락 요약 (1~3문장)
  - 기존 `wiki/_index.md` 조회 → 유사 기존 개념 매핑
  - 결과: `{slug}.concepts.json` 임시 파일

- [x] **P5-02** 개념별 컴파일 (Step 2) 구현
  - 추출된 개념별로 `wiki/concepts/{개념명}.md` 생성 또는 병합
  - 병합 전략: 보완/상충/중복 3가지 케이스 처리
  - frontmatter에 `source_files`, `related` 관계 맵 유지
  - 상충 내용 → `wiki/conflicts/` 자동 기록

- [x] **P5-03** 개념 관계 맵 자동 생성
  - 개념 간 상위/하위/연관/상충 관계 추론
  - `wiki/_index.md` 관계 그래프 섹션 자동 갱신
  - 기존 그래프 뷰(D3.js)와 연동

- [x] **P5-04** 개념명 정규화
  - 유사 개념 중복 방지 (예: "고객세분화" / "고객 세그먼트" → 동일 개념 판정)
  - LLM 기반 개념명 정규화 + 리다이렉트 처리
  - 주기적 위키 재구조화 CLI (`kb wiki reorg`)

---

## Phase 6 — 운영/유지보수

### W6 — 위키 삭제 프로세스

- [ ] **W6-01** 위키 삭제 프로세스
  - `scripts/wiki_delete.py` 신규 작성 — 핵심 삭제 로직
    - `find_concepts_by_source(raw_path, wiki_root)` — raw 파일의 `source_files` 기준 연관 concept 탐색
    - `find_concept_by_name(name, wiki_root)` — 이름/슬러그로 concept 파일 탐색
    - `remove_from_index(concept_name, wiki_root)` — `_index.md` 항목 제거
    - `remove_from_summaries(concept_name, wiki_root)` — `_summaries.md` 항목 제거
    - `clean_backlinks(concept_name, wiki_root)` — 다른 concept 파일의 백링크 정리
    - `delete_concept(concept_path, wiki_root, ...)` — 단일 concept 삭제
    - `delete_by_raw(raw_path, wiki_root, with_raw, ...)` — raw 파일 기반 통합 삭제
  - CLI 명령어 2개 추가 (`scripts/cli.py`)
    - `kb remove <source>` — raw 파일/URL 제거 + 연관 wiki 통합 삭제
      - `--wiki-only` : wiki만 삭제, raw 유지
      - `--dry-run` : 삭제 대상 목록만 출력 (실제 삭제 없음)
      - `--force` / `-f` : 확인 없이 삭제
      - `--no-index` : `_index.md` 갱신 생략
      - `--no-backlinks` : 백링크 정리 생략
    - `kb wiki delete <concept-name>` — wiki concept만 삭제 (raw 유지)
      - `--dry-run`, `--force`, `--no-index`, `--no-backlinks`
  - 삭제 후 자동 정리 대상:
    - `wiki/concepts/{name}.md`
    - `wiki/_index.md` 내 `[[개념명]]` 라인 (개념 목록 + 관계 맵)
    - `wiki/_summaries.md` 내 해당 라인
    - 다른 concept 파일의 `related_concepts` frontmatter + `## 관련 개념` 섹션
    - `.kb_concepts/{slug}.concepts.json` (raw 삭제 시)
    - `raw/` 파일 + `.meta.yaml` (with_raw=True 시)

---

## 현재 진행 상태

- [x] **W1-04b** PowerPoint 인제스터 — 멀티모달 2-패스 업그레이드
  - 텍스트 패스: 기존 python-pptx 추출 유지
  - 이미지 패스: LibreOffice → PyMuPDF(fitz) 슬라이드 PNG 렌더링 → Gemma 4 Vision 상세 분석
  - 조립: 슬라이드별 텍스트 + `### 시각 분석` 섹션 병합
  - `vision_llm` 설정 블록 추가 (settings.yaml) — 주 LLM과 독립적으로 Ollama Gemma 설정 가능
  - `ingest.slide_render` 플래그로 이미지 패스 on/off 제어
  - LibreOffice 미설치 시 graceful fallback (텍스트 패스만)

- [x] **W6-01** 위키 삭제 프로세스 — 구현 완료

- [x] **W1-04c** PPT Vision 캡션 재실행 기능
  - `retry_vision_pass(md_path, ...)` 함수 추가 (`scripts/ingest_ppt.py`)
    - `_parse_ppt_md()` — 기존 .md frontmatter/body 파싱
    - `_find_slides_without_vision(body)` — 시각 분석 누락 슬라이드 자동 탐지
    - `_inject_visual_analysis(body, slide_num, analysis)` — 분석 결과 주입/교체
    - `force=True` : 기존 분석도 덮어씀, `only_slides` : 특정 슬라이드만 지정
    - `dry_run` : 실제 실행 없이 대상 목록만 반환
  - `kb retry-vision <md-파일>` CLI 명령어 추가 (`scripts/cli.py`)
    - `--pptx` : 원본 PPTX 경로 (frontmatter source_file 자동 참조 가능)
    - `--slides "1,3,5-8"` : 특정 슬라이드만 지정
    - `--force` / `--dry-run` 지원

---

**마지막 업데이트:** 2026-04-17
**현재 단계:** W1-04c 완료
**블로킹 이슈:** 없음
**다음 태스크:** 실제 PPT 파일로 retry-vision 테스트 권장
