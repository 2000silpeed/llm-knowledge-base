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

- [x] **W6-01** 위키 삭제 프로세스
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

## Phase 7 — 코드 품질

- [x] **CQ-01** frontmatter 파싱 중앙화
  - `parse_frontmatter` / `dump_frontmatter`를 `token_counter.py` 공유 유틸로 추출
  - 10개 파일 중복 정의 전량 제거

- [x] **CQ-02** Vision 재실행 병렬화
  - `retry_vision_pass()` 직렬 for 루프 → `ThreadPoolExecutor(max_workers=4)` 병렬화
  - 기존 `_run_vision_pass()` 패턴과 통일

- [x] **CQ-03** `slugify()` 공통 모듈 추출
  - `scripts/utils.py` 신규 생성 — `slugify(text, max_len, fallback)` 단일 구현
  - 7개 파일 로컬 `_slugify` 정의 전량 제거: `ingest_web`, `ingest_pdf`, `ingest_ppt`, `ingest_word`, `ingest_excel`, `ingest_github`, `ingest_youtube`
  - NFKD → NFKC 수정 (한글 음절 자모 분해 버그 수정)

- [x] **CQ-04** `_render()` / `find_unique_path()` 공통 모듈 통합
  - `render_template()` → `scripts/utils.py` 추출, 9개 파일 로컬 정의 전량 제거: `compile`, `query`, `concept_compiler`, `incremental`, `exploration`, `concept_normalizer`, `concept_extractor`, `index_updater`, `concept_graph`
  - `find_unique_path()` → `scripts/utils.py` 추출, numeric 접미사 루프 4곳 교체: `compile`, `incremental`, `exploration`, `concept_compiler`

- [x] **CQ-05** 상수 중앙화
  - MIME→확장자 매핑 (`ingest_web`, `ingest_pdf`, `ingest_ppt` 3곳 중복) → `scripts/constants.py`
  - YouTube 언어 우선순위 → settings.yaml `ingest.lang_priority` 로드

---

## Phase 8 — 기획서 미구현 항목 보완

- [x] **W8-01** wiki/ 자동 git 커밋
  - `scripts/wiki_git.py` — `auto_commit_wiki(wiki_root, message)` 함수
  - `settings.yaml`에 `wiki.auto_commit: true/false` 옵션 추가
  - `kb compile` 완료 후 wiki/ 변경 파일 자동 `git add` + `git commit`
  - 커밋 메시지 형식: `kb: auto-compile YYYY-MM-DD HH:MM`
  - git 미설치 또는 wiki/가 git repo 아닐 때 경고만 출력 (크래시 없음)

- [x] **W1-07** 직접 텍스트 입력 인제스터
  - `scripts/ingest_text.py` — `ingest_text(text, title, ...)` 함수
  - `kb ingest --text "내용"` 또는 `kb ingest -` (stdin 파이프)
  - 출력: `raw/notes/{날짜}_{슬러그}.md` + `.meta.yaml`
  - frontmatter: `source: inline`, `collected_at`, `title`

- [x] **P2-09** SQLite FTS5 검색 인덱스
  - `scripts/search_index.py` — `build_index(wiki_root, db_path)` 함수
    - wiki/concepts/ + wiki/explorations/ 스캔 → SQLite FTS5 테이블 구축
    - `kb index [--rebuild]` CLI 명령어
    - `kb compile` 완료 후 자동 인덱스 갱신
  - `web/app/api/search/route.ts` — `better-sqlite3`로 DB 조회
    - DB 없으면 Fuse.js fallback (하위 호환)
  - DB 경로: `.kb_search.db` (프로젝트 루트)

- [x] **W1-09** Word 트랙변경 내역 포함 옵션
  - `ingest_word(include_tracked_changes=False)` 파라미터 추가
  - `w:ins` → `++삽입 텍스트++` / `w:del` → `~~삭제 텍스트~~` 마크다운 변환
  - `kb ingest --track-changes file.docx` CLI 옵션 추가
  - frontmatter에 `tracked_changes: true` 기록

- [x] **W1-08** 로컬 이미지 파일 인제스터
  - 이미지 파일(`.jpg/.png/.gif/.webp`) → Vision API 캡션 생성 → 마크다운 저장
  - 출력: `raw/images/{날짜}_{슬러그}.md` (이미지 경로 + 캡션 포함)
  - `cli.py` ingest 라우팅에 이미지 확장자 분기 추가

---

## Phase O — 온톨로지 & 지식 그래프 (Kuzu 기반)

> **목표:** wiki 개념들을 formal triple로 구조화 → Kuzu 그래프 DB 저장 → AI가 추론 가능한 지식으로 승격
> **스택:** Kuzu (임베디드 그래프 DB, Cypher) + MCP 서버 (외부 AI 도구화)

- [x] **O1** 온톨로지 스키마 정의
  - `config/ontology_schema.yaml` — 관계 타입, 노드 클래스, Action Type, 메타엣지 규칙 정의
  - 관계 타입: `IS_A`, `PART_OF`, `ENABLES`, `REQUIRES`, `CONTRADICTS`, `EXEMPLIFIES`, `PRECEDES`, `CO_OCCURS`
  - 노드 클래스: `Concept`, `Domain`, `ActionType`
  - Action Type: AI가 개념에 대해 수행 가능한 동작 (`query`, `compare`, `apply`, `derive`, `summarize`)
  - 메타엣지: 관계 제약 규칙 (`IS_A`이면 속성 상속 등)

- [x] **O2** Kuzu 그래프 DB 초기화 + 스키마 마이그레이션
  - `scripts/graph_db.py` — Kuzu 연결·스키마 생성·마이그레이션 유틸
  - DB 경로: `.kb_graph/` (디렉토리, Kuzu 기본 형식)
  - 노드 테이블: `Concept(name STRING PRIMARY KEY, summary TEXT, source_files TEXT, last_updated STRING)`
  - 엣지 테이블: 관계 타입별 (`IS_A`, `PART_OF`, `ENABLES`, `REQUIRES`, `CONTRADICTS`, `EXEMPLIFIES`, `PRECEDES`, `CO_OCCURS`)
  - `kb graph init` CLI

- [x] **O3** 온톨로지 추출기
  - `scripts/ontology_extractor.py` — wiki/concepts/ → LLM → (subject, predicate, object) triple 추출
  - 기존 `related` frontmatter 업사이클 + 본문 심층 분석
  - `config/prompts.yaml`에 `ontology_extract` 프롬프트 추가
  - 출력: `.kb_concepts/{slug}.triples.json`
  - `kb ontology extract [--all | --file]`

- [x] **O4** 그래프 적재기
  - `scripts/graph_loader.py` — `.triples.json` → Kuzu 노드·엣지 MERGE
  - 증분 적재 (변경된 개념만 갱신)
  - `kb graph load [--rebuild]`
  - `kb compile` 후 자동 적재 훅

- [ ] **O5** 분석 엔진 (6개 분석공간)
  - `scripts/ontology_analyzer.py`
  - `get_hierarchy(concept)` — 계층 공간 (`IS_A`, `PART_OF` 재귀 탐색)
  - `get_causal_chain(concept)` — 인과 공간 (`ENABLES`, `REQUIRES` 체인)
  - `get_community(concept)` — 구조 공간 (연결 클러스터, GraphRAG 방식)
  - `get_contradictions(concept)` — 갈등 공간 (`CONTRADICTS` 탐색)
  - `wiki/_communities.json` — 커뮤니티 요약 사전 생성
  - `kb graph analyze`

- [ ] **O6** 쿼리 엔진 강화 (내부 활용)
  - 기존 `kb query` 파이프라인에 온톨로지 컨텍스트 주입
  - 질의 → 온톨로지 1~2홉 확장 → 관련 개념 자동 보강
  - 커뮤니티 요약을 Priority 1 컨텍스트로 활용

- [ ] **O7** MCP 서버 (외부 AI 도구화)
  - `scripts/mcp_server.py` — MCP 프로토콜 (stdio transport)
  - Tools:
    - `search_concepts(query)` — FTS5 검색
    - `get_concept(name)` — 개념 상세 + triple
    - `get_hierarchy(concept)` — 계층 탐색
    - `get_causal_chain(concept)` — 인과 체인
    - `get_community_summary(concept)` — 커뮤니티 요약
    - `query_knowledge(question)` — 기존 query 엔진 호출
  - `kb mcp serve` CLI
  - `CLAUDE.md` / MCP 설정 파일 생성 안내

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

**마지막 업데이트:** 2026-04-19
**현재 단계:** P2-09 완료 (기획서 미구현 전체 완료)
**블로킹 이슈:** 없음
**다음 태스크:** 신규 기획 또는 실사용 테스트
