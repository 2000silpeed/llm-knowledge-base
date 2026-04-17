# handoff.md — 완료 작업 핸드오프 기록

> 세션 간 연속성 유지용. 새 세션 시작 시 마지막 항목부터 읽을 것.
> 형식: `## HO-{번호} | {날짜} | {태스크 ID}`

---

## HO-030 | 2026-04-17 | W1-04b — PPT 인제스터 멀티모달 2-패스 업그레이드

**완료:** `scripts/ingest_ppt.py` 전면 재설계 — 텍스트 패스 + 이미지 패스 + 조립

- `_render_slides_to_png(pptx_path, outdir)` 신규
  - LibreOffice headless → PPTX를 PDF로 변환
  - PyMuPDF(fitz) 2x 해상도(144 DPI)로 페이지별 PNG 렌더링
  - 임시 디렉토리 사용, 완료 후 PDF 삭제
- `_analyze_slide_image(image_bytes, slide_num, settings)` 신규
  - 슬라이드 전체 이미지 → Vision LLM 상세 분석 (주제·텍스트·차트·다이어그램·강조 항목)
  - `_get_vision_settings()` 통해 `vision_llm` 설정 자동 적용
- `_assemble_slide(text_md, visual_analysis)` 신규
  - 텍스트 패스 결과 + `### 시각 분석\n{analysis}` 조립
- `ingest_ppt()` 수정
  - 3단계 흐름: 텍스트 패스 → 이미지 패스 → 조립
  - `do_slide_render` 플래그: `ingest.slide_render` 설정 기반
  - 이미지 패스 실패 시 경고 로그 후 텍스트 패스 결과만 사용 (graceful fallback)
  - 결과 dict에 `visual_pass: bool` 추가
- `config/settings.yaml` 수정
  - `ingest.slide_render: true` 추가
  - `vision_llm` 블록 추가 (provider/model/base_url)
- `scripts/llm.py` 수정
  - `_vision_ollama` 타임아웃 120s → 300s (슬라이드 상세 분석 대응)

**결정사항:**
- `vision_llm` 설정이 없으면 기본 `llm` 설정으로 vision 호출 → 기존 Anthropic 사용자도 자동 동작
- LibreOffice 미설치 시 `RuntimeError`를 잡아 경고만 출력하고 텍스트 패스 결과 반환
- 슬라이드 PNG는 `tempfile.TemporaryDirectory`에 저장 → 처리 후 자동 삭제 (디스크 절약)
- 임베드 이미지 캡션(`_generate_caption`)은 그대로 유지 — 슬라이드 전체 분석과 별개

**주의:**
- LibreOffice 필수: `sudo apt install libreoffice` 또는 `brew install libreoffice`
- Gemma 4 사용 시: `ollama pull gemma3:4b` (또는 출시 모델명으로 업데이트)
- `settings.yaml`의 `vision_llm.model`을 실제 설치된 모델명과 일치시킬 것
- 슬라이드 수 × Vision 호출 시간 → 대형 PPT(50장+)는 수 분 소요 예상

**다음:** PPT 파일로 실제 테스트 후 출력 품질 확인

---

## HO-029 | 2026-04-17 | 위키 컴파일 파이프라인 재설계

**완료:** 정보 손실 및 단일 개념 생성 문제 해결

- `scripts/concept_extractor.py` — `_extract_from_chunks()`에서 개념별 `source_chunk_indices` 태깅
  - 각 청크에서 추출된 개념에 `_src_chunk_idx` 임시 필드 추가 후 병합
  - 동일 개념이 여러 청크에서 추출되면 indices 누적 (`[1, 3]` 등)
  - `_map_to_existing()` 통과 후에도 indices 보존 (backup & restore)
- `scripts/concept_compiler.py`
  - `_get_source_content()` 완전 재작성 — 관련 청크 선택 모드
    - 문서 ≤ 55% 예산: 전체 반환 (기존과 동일)
    - 문서 초과: `source_chunk_indices` → 인접 청크 추가 → 키워드 매칭 순 선택
  - `compile_file()` 통합 함수 추가 — P5 두 단계(extract+compile) 래핑
    - `compile_document()` 대체 함수, 하위 호환 필드(`concept`, `wiki_path`, `strategy`) 포함
- `scripts/perf.py` — `_compile_one_with_retry()`: `compile_document` → `compile_file` 교체
- `scripts/cli.py` — `_compile_single()`: P5 2단계 파이프라인 연결
  - `_print_compile_result_p5()` 추가: 신규/보완/중복/충돌 건수 표시

**결정사항:**
- `incremental.py`는 `compile_batch()` → `_compile_one_with_retry()` 체인을 통해 자동으로 P5 적용 (별도 수정 불필요)
- `source_chunk_indices` 없는 개념(단일 패스 추출 또는 구형 JSON)은 키워드 매칭 fallback으로 처리
- `compile.py`는 삭제하지 않음 — 하위 호환 및 직접 테스트용으로 유지

**주의:**
- 기존 `.kb_concepts/*.concepts.json` 파일(구형)에는 `source_chunk_indices` 없음 → 키워드 fallback 경로 사용
- `_map_to_existing()` LLM 호출 후 concepts JSON이 재파싱되므로 indices 복원 로직 필요 (구현 완료)
- 청크 분할 결과는 settings.yaml의 chunking 파라미터에 따라 달라짐 — 모델 변경 시 청크 수/크기 변함

**다음:** 실제 문서로 테스트 후 결과 확인 권장

---

## HO-028 | 2026-04-16 | 중복 인제스트 감지

**완료:** 동일 문서 재요청 시 중복 감지 → 재작성/건너뜀 선택 기능
- `scripts/cli.py` — 중복 감지 헬퍼 추가
  - `_approx_slug()` — 인제스터 슬러그 로직 근사 구현
  - `_find_existing_raw(source, settings, is_url)` — 파일/URL 별 기존 raw 파일 탐색
    - 파일: `raw/{section}/{slug}*.md` glob 매칭
    - URL: `raw/articles/*.md` frontmatter `source_url` 스캔
  - `_cleanup_raw_files(md_files)` — `.md` + `.meta.yaml` + `.concepts.json` 일괄 삭제
  - `ingest` 커맨드에 `--force`, `--skip-existing` 플래그 추가
  - 플래그 없을 때 `typer.confirm()` 대화형 확인 프롬프트
- `web/app/api/upload/route.ts` — 웹 업로드 중복 처리
  - `findExistingRaw(filename)` — TS 측 기존 파일 탐색 (slugify 동일 로직)
  - `cleanupExistingRaw(mdPath)` — 기존 raw 파일 정리
  - `force` form field 처리: `rewrite` | `skip` | 미지정(→ `duplicate` 응답)
  - 임시 파일명을 원본 파일명 기반으로 변경 (Python 인제스터 출력 경로 정확도 향상)
  - 임시 디렉토리(`mkdtemp`) 방식으로 전환
- `web/app/(main)/upload/page.tsx` — 중복 확인 UI
  - `FileStatus`에 `"duplicate"`, `"skipped"` 상태 추가
  - `duplicate` 응답 시 인라인 확인 패널 표시 (기존 경로 + 재작성/건너뜀 버튼)
  - `rewriteOne()`, `skipOne()` 함수 추가

**결정사항:**
- **TS 측 slug 로직 중복**: Python `_approx_slug`와 동일 로직을 TS에 구현. 완벽히 동일하지 않을 수 있으나 `slug*.md` glob으로 보완 (hash suffix 변형도 탐지)
- **URL 중복 탐지**: 날짜가 포함된 파일명 대신 frontmatter `source_url` 스캔 방식 채택 — 정확하지만 O(n) 스캔 (개인 KB는 파일 수가 적으므로 허용)
- **기존 임시 파일 방식 변경**: `mkdtemp` + 원본 파일명 방식으로 변경 — Python 인제스터가 올바른 출력 파일명을 생성할 수 있도록

**주의:**
- URL 인제스터(YouTube, GitHub)는 중복 감지 미지원 (CLI에서 `_find_existing_raw` is_url=True이지만 YouTube/GitHub URL은 articles/ 스캔 방식과 다를 수 있음)
- TS slug와 Python slug가 미묘하게 다를 경우 TS 측에서 탐지 못할 수 있음 → 이 경우 CLI에서 `--force` 플래그로 처리 가능

**다음:** 없음 (요청된 기능 완료)

---

## HO-027 | 2026-04-10 | P4-02

**완료:** 파일 업로드 UI (드래그 앤 드롭)
- `web/app/api/upload/route.ts` — 신규 생성
  - POST: `multipart/form-data` 파일 수신 → OS 임시 디렉토리에 저장 → `python -m scripts.cli ingest <temppath>` 실행 → 결과 반환
  - 지원 확장자: .pdf, .xlsx, .xls, .xlsm, .pptx, .docx, .md, .txt
  - `KB_PROJECT_ROOT` 환경변수로 프로젝트 루트 오버라이드 가능 (기본값: `../`)
  - 임시 파일은 요청 완료 후 `finally`에서 자동 삭제
- `web/app/(main)/upload/page.tsx` — 신규 생성
  - 드래그 앤 드롭 존 (HTML5 native DnD)
  - 파일 목록: 파일명/크기/확장자 아이콘 표시
  - 개별 인제스트 버튼 + 전체 일괄 인제스트 버튼
  - 업로드 중: 스피너 애니메이션 ("처리 중")
  - 완료: 저장 경로 표시 + `/raw` 링크 안내
  - 오류: 오류 메시지 인라인 표시
- `web/app/(main)/layout.tsx` — 사이드바에 "업로드" 링크 추가

**결정사항:**
- **개별 + 전체 업로드 분리**: 파일별 개별 인제스트 버튼과 전체 일괄 버튼 공존 — 선택적 처리 가능
- **순차 처리**: `uploadAll()`은 파일을 순차적으로 하나씩 처리 — LLM 호출 부하 방지
- **임시 파일 경로**: `os.tmpdir()` 사용, 파일명은 `kb_upload_{timestamp}_{original}` 형식
- **CLI 경유**: Python 인제스터를 직접 import하지 않고 `python -m scripts.cli ingest`로 실행 — 모든 라우팅 로직 재사용
- **저장 경로 파싱**: cli.py의 rich Panel 출력에서 "저장: ..." 패턴 정규식 추출

**주의:**
- `python -m scripts.cli`는 `KB_PROJECT_ROOT`(기본 `../`)에서 실행 — 서버 환경에서 Python venv 활성화 필요할 수 있음
- `execAsync` timeout 120초 — 대용량 파일 인제스트 시 초과 가능성 있음 (LLM 호출 포함 시)
- Next.js는 `multipart/form-data` 파일을 `request.formData()`로 처리 — `bodyParser` 비활성화 불필요 (App Router 기본)
- 동시 다중 업로드 시 임시 파일명 충돌 방지: `Date.now()_Math.random()` prefix 사용

**다음:** 없음 (P4 전체 완료)

---

## HO-026 | 2026-04-09 | P5-04

**완료:** 개념명 정규화 (P5-04)
- `scripts/concept_normalizer.py` — 신규 생성
  - `load_all_concepts(wiki_root)` — wiki/concepts/ 전체 로드 (redirect_to 파일 제외)
  - `find_duplicate_groups(concepts, settings, prompts, cache)` — LLM으로 유사/중복 그룹 탐지, 배치 처리(>40개) + Union-Find 그룹 병합
  - `normalize_wiki(wiki_root, ...)` — 전체 파이프라인 진입점
    - 그룹 내 canonical 파일 결정 (이름 일치 우선, 없으면 내용 가장 많은 파일)
    - `_merge_concept_files()` — LLM으로 여러 파일 내용 통합
    - `_write_redirect_file()` — 비정규 파일을 redirect_to frontmatter + [[canonical]] 링크로 전환
    - `_update_all_backlinks()` — wiki/ 전체 [[old]] → [[canonical]] 교체 (frontmatter YAML 포함)
    - `_write_report()` — wiki/_normalization_report.md 보고서 저장
- `config/prompts.yaml` — 프롬프트 2개 추가
  - `normalize_concepts` — 개념 목록(이름+요약) → JSON [{canonical, members}] (유사 그룹 탐지)
  - `merge_concept_files` — 여러 파일 내용 → 통합 wiki 항목 생성
- `scripts/cli.py` — `kb wiki reorg` 명령어 추가
  - `wiki_app = typer.Typer(name="wiki", ...)` 서브앱 추가
  - `--dry-run`: 파일 변경 없이 탐지 결과만 출력
  - `--no-merge`: 리다이렉트만, 내용 병합 없이
  - `--no-backlinks`: 백링크 업데이트 생략
  - `--no-cache`: LLM 캐시 비활성화

**결정사항:**
- **false positive 방지 우선**: LLM 프롬프트에서 확신 없으면 포함 금지 명시 — 잘못된 병합이 정보 손실을 유발하므로
- **Union-Find 배치 병합**: 배치 경계에서 같은 개념이 다른 그룹에 배정될 경우 겹치는 멤버로 합산
- **리다이렉트 형식**: frontmatter에 `redirect_to` + `original_name` 필드 → 질의 엔진/인덱서가 식별 가능
- **백링크 정규식**: `\[\[old_name\]\]` 패턴 + frontmatter YAML `- old_name` 항목도 동시 교체
- **canonical 파일 결정 순서**: ① 이름이 정확히 canonical인 파일 ② 내용 가장 많은 멤버 파일
- **병합 없이 리다이렉트만**: `--no-merge`로 내용 병합 없이 리다이렉트 파일만 생성 가능 (빠른 실행)

**주의:**
- `find_duplicate_groups` LLM 출력의 members 검증 필수 — 존재하지 않는 이름은 자동 드롭됨
- 배치 처리(>40개) 시 배치 경계 개념 쌍은 별도 비교 안 됨 (첫 배치가 전체 목록 내에서만 비교)
- `_update_backlinks_in_file` 정규식이 YAML frontmatter의 `-` 목록 항목만 교체 (들여쓰기 없는 형식 가정)
- canonical 파일명이 현재 파일명과 다를 경우 파일 이동(shutil.move) 발생 — 기존 파일 경로 참조 주의
- 리다이렉트 파일은 `load_all_concepts()` 호출 시 자동 제외됨 (redirect_to 필드 감지)

**다음:** Phase 5 전체 완료. 다음 단계 기획 필요.

---

## HO-025 | 2026-04-09 | P5-03

**완료:** 개념 관계 맵 자동 생성 (P5-03)
- `scripts/concept_graph.py` — 신규 생성
  - `load_all_concepts(wiki_root)` — wiki/concepts/ 전체 로드 (slug, name, summary, path)
  - `infer_relations(concepts, settings, prompts, cache)` — LLM으로 관계 추론 (JSON 파싱 포함)
  - `update_concept_files(relations, concepts, dry_run)` — 개념 파일 frontmatter + ## 관련 개념 섹션 갱신
  - `update_index_graph(relations, concepts, wiki_root, dry_run)` — _index.md 관계 맵 섹션 갱신
  - `export_graph_json(relations, concepts, wiki_root, dry_run)` — wiki/_graph.json 저장
  - `build_concept_graph(wiki_root, ...)` — 전체 파이프라인 진입점
- `config/prompts.yaml` — `infer_concept_relations` 프롬프트 추가
  - 개념 목록(이름+요약) → JSON 배열 [{source, target, type}]
- `scripts/cli.py` — `kb graph` 명령어 추가
  - `--dry-run`: 파일 수정 없이 추론만
  - `--no-export`: _graph.json 내보내기 생략
- `web/lib/wiki.ts` — D3.js 연동 강화
  - `GraphEdge`에 `relationType?: "parent"|"child"|"related"|"conflict"` 추가
  - `buildGraphData()`에서 `_graph.json` 읽어 타입 정보 병합
  - 위키링크 엣지와 _graph.json 엣지 통합 (타입 있는 것 우선)
- `web/components/ConceptGraph.tsx` — 엣지 타입별 시각화
  - 상위(parent): 파란색, 하위(child): 초록색, 연관(related): 회색, 상충(conflict): 빨간색 점선
  - 범례 업데이트 (노드 타입 + 관계 유형)

**결정사항:**
- **관계 추론 단위**: 개념 전체 내용 대신 요약만 LLM에 전달 (토큰 절약)
  - `## 핵심 요약` 섹션 우선, 없으면 첫 단락 최대 300자
- **배치 처리**: 개념이 30개 초과 시 30개씩 나눠 처리 (컨텍스트 제한 대응)
- **역방향 자동 추가**: parent 추론 → child 역방향 자동 생성 (LLM 중복 입력 방지)
- **_graph.json 우선 전략**: 위키링크 엣지와 _graph.json 엣지 병합 시 타입 정보 있는 것 우선
- **D3.js 하위 호환**: _graph.json 없어도 기존 위키링크 방식으로 정상 동작 (graceful fallback)

**주의:**
- `update_concept_files`의 정규식으로 기존 `## 관련 개념` 섹션 교체 시 섹션 끝 패턴(`\n##|\Z`)에 의존
  - 마지막 섹션이면 `\Z`로 처리되므로 줄바꿈 없는 파일 말단 주의
- 배치 처리 시 배치 간 관계 추론 안 됨 (배치 경계에 걸린 개념 쌍)
  - 개념 수가 적으면(≤30) 문제 없음. 30개 초과 시 중요 관계 누락 가능
- `_graph.json`은 `kb graph` 실행 시마다 전체 재생성 (점진적 갱신 아님)

**다음:** P5-04 (개념명 정규화 — 유사 개념 중복 방지 + 리다이렉트)

---

## HO-024 | 2026-04-09 | P5-02

**완료:** 개념별 컴파일 (2단계 컴파일 파이프라인 Step 2) 구현
- `scripts/concept_compiler.py` — 신규 생성
  - `compile_concept(concept, source_path, ...)` — 단일 개념 처리 (신규/병합)
  - `compile_from_concepts_json(concepts_path, ...)` — JSON 파일 전체 처리
  - `compile_all_concepts_jsons(concepts_dir, ...)` — .kb_concepts/ 전체 일괄 처리
- `config/prompts.yaml` — 2개 프롬프트 추가
  - `compile_concept_new` — 신규 개념 wiki 항목 생성 (null/similar match용)
  - `compile_concept_merge` — 기존 개념 병합 (exact match용, complement/duplicate/conflict 판정)
- `scripts/cli.py` — `kb compile-concepts` 명령어 추가
  - `kb compile-concepts <파일>` — 특정 .concepts.json 처리
  - `kb compile-concepts --all` — .kb_concepts/ 전체 처리
  - `--no-index` 옵션으로 인덱스 갱신 생략 가능

**결정사항:**
- **match_type별 처리 분기:**
  - `null` / `similar` → `compile_concept_new` 프롬프트로 신규 wiki 생성
    - `similar`는 기존 유사 개념을 `related_concepts`에 백링크로 포함
  - `exact` → 기존 wiki 파일 읽어 `compile_concept_merge` 프롬프트 호출
    - `complement`: LLM이 통합 wiki 반환 → 파일 덮어쓰기
    - `duplicate`: source_files에 새 출처만 추가 (LLM 없이)
    - `conflict`: wiki/conflicts/ 에 충돌 보고서 저장 + 기존 wiki에 ⚠️ 알림 삽입
- **병합 응답 파싱 형식**: JSON 대신 `ACTION:` / `CONFLICT_SUMMARY:` / `---CONTENT---` 줄 구분자 방식 채택 — LLM 파싱 실패 위험 최소화
- **소스 토큰 초과 처리**: 소스 문서가 예산 60% 초과 시 개념 추출기 요약만 전달 (full doc 대신)
- **파일 탐색**: 기존 wiki 파일은 파일명(slug 변환) 우선, 없으면 H1 제목으로 순차 검색

**주의:**
- `compile_concept_merge` 에서 ACTION 줄이 없는 경우 complement 기본값으로 fallback
- exact match인데 기존 wiki 파일을 못 찾으면 신규 생성으로 자동 전환 (경고 로그)
- 여러 개념이 같은 기존 wiki 파일(exact match)을 가리킬 경우 순차 처리됨 (race condition 없음)
- 인덱스 갱신은 각 wiki 파일별로 순차 호출 (update_all) — 개념이 많으면 느릴 수 있음

**다음:** P5-03 (개념 관계 맵 자동 생성 — wiki/_index.md 관계 그래프 섹션)

---

## HO-023 | 2026-04-09 | P5-01

**완료:** 개념 추출 단계 (2단계 컴파일 파이프라인 Step 1) 구현
- `scripts/concept_extractor.py` — 신규 생성
  - `extract_concepts(source_path, ...)` — raw/ 문서 → 핵심 개념 5~15개 추출
  - 단일 패스 (≤80% 토큰) / 청크 분할 (초과) 자동 선택
  - 기존 `wiki/_index.md` 조회 → `existing_match` / `match_type` 매핑
  - 결과: `.kb_concepts/{slug}.concepts.json` 저장
  - `load_concepts(source_path)` — 저장된 JSON 로드 유틸
- `config/prompts.yaml` — 3개 프롬프트 추가
  - `extract_concepts` — 단일 패스용 (JSON 배열 출력)
  - `extract_concepts_chunk` — 청크별 추출용
  - `extract_concepts_map` — 청크 병합 후 기존 인덱스 매핑용
- `scripts/cli.py` — `kb extract-concepts <파일>` 명령어 추가
  - `--no-save`: JSON 저장 생략
  - `--json`: 결과를 JSON으로 출력

**결정사항:**
- **출력 포맷**: `{"name", "summary", "existing_match", "match_type"}` 4개 필드
  - `match_type`: "exact"(동일 개념) / "similar"(유사) / null(신규)
  - P5-02가 이 정보를 바탕으로 병합/신규 생성 결정
- **임시 파일 위치**: `.kb_concepts/` — raw/ 불변 원칙 준수, wiki/ 오염 방지
- **청크 병합 전략**: 청크별 추출 → 이름 기준 dedup → 긴 summary 우선 → 최대 20개 제한 → 인덱스 매핑 LLM 후처리
- **LLM 파싱 방어**: 코드 펜스 제거 + JSON 배열 정규식 추출 fallback

**주의:**
- `extract_concepts_map` 프롬프트 사용 시 LLM 파싱 실패 가능성 있음 → fallback으로 원본 concepts 반환
- `.kb_concepts/` 디렉토리는 `.gitignore` 추가 권장 (임시 파일)
- P5-02에서 이 JSON을 읽어 `wiki/concepts/{개념명}.md` 생성/병합 진행

**다음:** P5-02 (개념별 컴파일 — 기존 개념 파일 생성/병합)

---

## HO-022 | 2026-04-06 | P3-04

**완료:** 외부 연동 REST API 서버 구현
- `scripts/api_server.py` — FastAPI 기반 REST API 서버 신규 생성
  - GET /v1/health, /v1/status, /v1/index
  - GET /v1/concepts, /v1/concepts/{slug}, /v1/search
  - POST /v1/ingest (URL/텍스트), /v1/query
  - GET|POST|DELETE /v1/webhooks
  - OpenAPI 문서 자동 생성 (/docs, /redoc)
- `scripts/cli.py` — `kb api` 명령어 그룹 추가
  - `kb api serve` — 서버 시작
  - `kb api keygen/keys/revoke` — API 키 관리
  - `kb api webhooks/webhook-add/webhook-del` — Webhook 관리
- `pyproject.toml` — fastapi, uvicorn[standard], httpx 의존성 추가
- `start.sh` — `--api` 플래그 추가 (API 서버 선택적 시작)
- `Makefile` — `make api`, `make api-keygen` 타겟 추가
- `.env.example` — KB_API_HOST/PORT/KEYS_ENABLED/CORS_ORIGINS 항목 추가

**결정사항:**
- **FastAPI 채택** (Flask/aiohttp 대신): 자동 OpenAPI 문서, Pydantic 모델, 비동기 지원
- **인증 방식**: SHA-256 해시 저장 (`config/api_keys.yaml`) — 원본 키는 한 번만 표시, 이후 복구 불가
  - `X-API-Key` 헤더 또는 `Authorization: Bearer` 모두 지원
  - `KB_API_KEYS_ENABLED=false`로 로컬 환경에서 인증 비활성화 가능
- **Webhook**: `config/webhooks.yaml` 저장, HMAC-SHA256 서명 옵션 지원
  - 이벤트: concept.created, concept.updated, ingest.completed, query.completed
  - 비동기 httpx 전송, 실패 시 조용히 무시 (서비스 안정성 우선)
- **인제스트 API**: 기존 `kb ingest` CLI를 subprocess로 호출 — 비즈니스 로직 중복 없음
- **CORS**: 기본 전체 허용, `KB_API_CORS_ORIGINS`으로 실서비스 제한 가능
- **별도 포트**: 웹 UI(3000)와 API 서버(8000) 분리 — 충돌 없음

**주의:**
- `httpx` 패키지가 설치되어야 Webhook 전송 동작 — 이미 pyproject.toml에 추가됨
- `fastapi>=0.115` + `starlette>=1.0` 조합 — 기존 Next.js 의존성과 무관
- API 서버는 `uv run kb api serve` 또는 `make api`로 별도 실행 (start.sh --api로도 가능)
- `config/api_keys.yaml`: 해시만 저장, 원본 키 분실 시 새 키 발급 필요
- 인제스트 POST는 백그라운드가 아닌 동기 실행 (120초 타임아웃) — 대용량 파일은 오래 걸릴 수 있음

**다음:** Phase 3 전체 완료. 서비스 안정화 또는 신규 기능 기획.

---

## HO-021 | 2026-04-06 | P3-03

**완료:** 조직 단위 지식 관리 구현
- `scripts/org.py` — 신규 생성: 조직/팀/멤버 계층 관리 + RBAC + 활동 로그 + 조직 공유 위키 컴파일
- `scripts/cli.py` — `kb org` 명령어 그룹 추가 (init / team create|list / member add|role|remove|list / stats / log / wiki)
- `web/app/api/org/route.ts` — GET /api/org 엔드포인트 (stats / log / members 액션)
- `web/app/(main)/org/page.tsx` — 조직 대시보드 UI (통계 카드, 팀 카드, 활동 로그)
- `web/app/(main)/layout.tsx` — 사이드바에 "조직 관리" 링크 추가

**결정사항:**
- **P2-06 팀 위에 조직 계층 추가**: 기존 team.yaml은 그대로 유지, org.yaml이 독립적으로 조직 구조를 관리
  - 팀(P2-06)은 2인 이상 소규모 협업, 조직(P3-03)은 여러 팀을 아우르는 엔터프라이즈 단위
- **RBAC 3단계**: admin(모든 권한) / editor(인제스트+컴파일) / viewer(읽기 전용)
  - `ROLE_PERMISSIONS` dict로 권한 집합 관리 → 새 권한 추가 시 단일 위치 수정
- **활동 로그 JSONL**: `config/org_activity.jsonl` — 파일 append 방식, 재시작 없이 실시간 기록
  - 멤버/팀별 필터링 지원, 최신순 정렬
- **조직 공유 위키 컴파일 전략**:
  - 단일 소스 개념: `shutil.copy2` (LLM 비용 없음)
  - 다중 소스 동일 개념: 임시 파일에 버전별 내용 합친 뒤 `compile_document` 호출 → LLM 병합
- **웹 API**: js-yaml 미설치 → Python subprocess (`uv run python -c "..."`) 로 JSON 출력
  - 조직 설정 없을 때 404 + 명확한 안내 메시지

**주의:**
- `org.yaml`과 `team.yaml`은 독립 파일 — 팀 기능(P2-06)은 org 없이도 동작 (하위 호환)
- `compile_org_wiki()` 내부에서 `scripts.compile.compile_document` 임포트 → compile.py 시그니처 변경 시 영향
- 웹 API는 `uv run python -c` 로 subprocess 실행 — 응답 지연 있을 수 있음 (30초 타임아웃)
- `org_activity.jsonl` 없으면 활동 로그 엔드포인트는 빈 배열 반환 (에러 없음)

**다음:** P3-04 (외부 연동 API)

---

## HO-020 | 2026-04-06 | P3-02

**완료:** 모바일 클리퍼 PWA 구현 (iOS/Android)
- `web/public/manifest.json` — PWA 매니페스트 (share_target, 아이콘, shortcuts)
- `web/public/sw.js` — 서비스 워커 (오프라인 fallback + PWA 설치)
- `web/public/icons/icon.svg` — SVG 앱 아이콘
- `web/public/icons/icon-192.png`, `icon-512.png` — PNG 앱 아이콘 (Pillow 생성)
- `web/components/SwRegister.tsx` — 클라이언트 SW 등록 컴포넌트
- `web/app/layout.tsx` — PWA 메타 태그 추가 (manifest, theme-color, apple-web-app)
- `web/app/(clipper)/layout.tsx` — 사이드바 없는 모바일 전용 레이아웃
- `web/app/(clipper)/clip/page.tsx` — 클리퍼 UI (URL/텍스트 입력, Share Target 파라미터 자동 채우기)
- `web/app/api/clip/route.ts` — POST /api/clip 엔드포인트 (uv run kb ingest 호출)
- `.env.example` — KB_CLIP_KEY, KB_PROJECT_DIR 항목 추가
- `web/app/(main)/layout.tsx` — 사이드바에 "클리퍼" 링크 추가

**결정사항:**
- **PWA 방식 선택** (React Native 대신): 기존 Next.js 인프라 재사용, 앱스토어 불필요, iOS/Android 동시 지원
- **Web Share Target API**: manifest.json의 `share_target` → GET 방식으로 `/clip?url=...&title=...&text=...` 수신
  - Android Chrome: 공유 시트에서 "KB 클리퍼" 선택 → 자동 URL 채우기
  - iOS Safari: "홈 화면에 추가" 후 공유 시트에서 "KB에 추가" 선택 가능
- **API 인증**: `KB_CLIP_KEY` 환경변수 설정 시 `X-KB-Key` 헤더 또는 `Authorization: Bearer` 토큰 검증
  - 설정 안 하면 로컬 네트워크용 오픈 액세스
- **텍스트 인제스트**: 임시 .md 파일 생성 → `kb ingest <tmpfile>` → 정리 (finally 블록)
- **URL 인제스트**: `kb ingest <url>` 직접 호출 (spawn 배열 인자 → shell injection 없음)
- 빌드 확인 완료: `/clip` (Static), `/api/clip` (Dynamic) 정상 생성

**주의:**
- `uv` 명령이 PATH에 있어야 `/api/clip` 동작 — systemd 서비스의 `Environment` 또는 `ExecStartPre`에서 uv 경로 확인 필요
- `KB_PROJECT_DIR` 미설정 시 `web/` 의 부모 디렉토리로 자동 추정 (`path.resolve(process.cwd(), "..")`)
  - Next.js가 `web/` 에서 실행되면 올바르게 동작; 다른 경로면 명시 설정
- Web Share Target은 HTTPS 또는 localhost에서만 동작 (PWA 보안 정책)
- iOS 홈 화면 추가 후 Share Extension 연동은 Safari 전용 (Chrome iOS 미지원)

**다음:** P3-03 (조직 단위 지식 관리) 또는 P3-04 (외부 연동 API)

---

## HO-014 | 2026-04-06 | P2-06 + P2-08

**완료:** Phase 2 마지막 두 태스크 완료 — Phase 2 전체 완료
- `scripts/cache.py` — 신규 생성: LLM 응답 파일 캐시 (P2-08)
- `scripts/team.py` — 신규 생성: 팀 지식베이스 설정/경로 관리 (P2-06)
- `scripts/compile.py` — `_call_llm()` + 내부 함수 캐시 파라미터 추가
- `scripts/perf.py` — `compile_batch()` / `_compile_one_with_retry()` 캐시 주입
- `scripts/cli.py` — `kb cache` / `kb team init|add|status` 명령어 추가, 팀 경로 자동 적용
- `config/settings.yaml` — `cache:` 섹션 추가 (`enabled`, `ttl_days`)

**결정사항:**
- **P2-08 캐싱 전략:**
  - 캐시 키: SHA256(model + "|" + system_prompt + "|" + user_prompt) — 완전 결정적
  - 저장: `.kb_cache/{key[:2]}/{key}.json` — 디렉토리 샤딩으로 파일 수 분산
  - 배치 컴파일 시 모든 파일이 동일 `CacheStore` 공유 → 동일 청크 포함 문서 히트 가능
  - `enabled: true` / `ttl_days: 0` (영구) 기본값 — 운영 중 비활성화 시 settings.yaml 수정
- **P2-06 팀 설계:**
  - `config/team.yaml` 존재 시 자동 팀 모드 활성 — 별도 플래그 불필요
  - 모든 기존 명령어(`compile`, `status` 등)가 `_load_team_paths()`로 경로 자동 해석
  - `shared_raw`는 절대/상대 경로 모두 지원 (네트워크 드라이브, git 서브모듈 등)
  - 멤버별 wiki 경로는 team.yaml의 `members[].wiki` — 없으면 `wiki/{member_id}/` fallback

**주의:**
- `cache.py`의 `CacheStore`는 쓰레드 안전 (파일 기반, GIL 보호) — 병렬 배치에서 공유 가능
- `team.yaml` 없으면 팀 모드 자동 비활성 → 기존 단일 사용자 방식 그대로 동작 (하위 호환)
- `compile.py`에서 `cache` 파라미터 기본값 `None` → `None`이면 내부에서 `make_cache_from_settings()` 자동 생성
- `.kb_cache/`는 `.gitignore`에 추가 권장 (API 응답은 재현 가능, 버전 관리 불필요)

**다음:** Phase 3 — P3-01 (클라우드 호스팅) 또는 서비스 안정화

---

## HO-013 | 2026-04-06 | P2-07

**완료:** 대용량 성능 최적화 (1000건+) 구현
- `scripts/perf.py` — 신규 생성: 병렬 배치 컴파일 / 역방향 소스 인덱스 / 병렬 해시 / 체크포인트
- `scripts/incremental.py` — `find_changed_files()` 병렬 해시 + `compile_changed()` 배치 처리로 교체
- `scripts/cli.py` — `_compile_all()` → `compile_batch()` 사용 + `--resume` / `--clear-checkpoint` 옵션 추가

**결정사항:**
- 핵심 병목 4가지 해결:
  1. **순차 컴파일 → 병렬**: `compile_batch()` + `ThreadPoolExecutor(max_workers)` — 1000건 처리 시 ~4배 속도 향상
  2. **파일별 인덱스 갱신 → 배칭**: `update_index=False`로 각 파일 컴파일, 전체 완료 후 1회 갱신 — LLM 2000회 → 2회 절감
  3. **O(n²) 관련 개념 탐색 → O(1)**: `.kb_source_index.json` 역방향 인덱스 캐시, mtime 기반 자동 무효화
  4. **순차 해시 계산 → 병렬**: `hash_files_parallel()`, 1000파일 해시 ~8배 빠름
- `inner_workers=1` (외부 max_workers≥4 시): 쓰레드 폭발 방지 (outer×inner 제한)
- Rate limit 자동 재시도: exponential backoff (2→4→8→16→32초, 최대 5회)
- 체크포인트(`.kb_checkpoint.json`): `--resume` 시 완료 파일 건너뜀, 중단 후 재시작 가능
- 역방향 인덱스(`.kb_source_index.json`): 새 wiki 파일 생성 시 자동 무효화 → 다음 `compile_changed` 시 재빌드

**주의:**
- `compile_batch()`는 progress bar를 `stderr`에 출력 (stdout은 깨끗하게 유지)
- 체크포인트는 `--all` 전용 — `compile_changed()`는 체크포인트 미사용 (해시 스토어가 동일 역할)
- `find_changed_files()` 병렬 해시: 10파일 미만이면 순차 처리 (threadpool 오버헤드 방지)
- `_compile_all()`에서 images_dir 필터 추가 (이전 버전에서 누락되어 있었음)

**다음:** P2-06 (팀 지식베이스) 또는 P2-08 (API 비용 최적화)

---

## HO-001 | 2026-04-05 | 기획

**완료:** 프로젝트 기획 및 문서 체계 수립
- `LLM_지식베이스_기획서.md` — 전체 설계 원본 (기술스택, 4단계 파이프라인, 로드맵, 청킹전략 포함)
- `CLAUDE.md` — 작업 규약 및 설계 원칙
- `task.md` — Phase 1~3 전체 태스크 목록
- `handoff.md` — 이 파일

**결정사항:**
- RAG(벡터DB) 사용하지 않음 → 마크다운 인덱스 파일로 대체 (Karpathy 검증 방식)
- 모델 독립 설계: `settings.yaml`의 `context_limit` 하나로 전체 청킹/컨텍스트 로직 적응
- 청킹 3단계: 단일패스(≤80%) / Map-Reduce(80~300%) / 계층트리(300%+)
- `raw/` 절대 수정 금지 원칙 확정
- Office 파일(Excel/PPT/Word) MVP 범위에 포함 (W1-03~05)

**주의:**
- 기획서에 `3.2-B` 섹션이 청킹 전략 전체를 담고 있음 — 구현 시 반드시 참조
- `settings.yaml`의 `context_limit`은 모델의 입력+출력 합산 한도가 아닌 **입력 한도** 기준으로 설정할 것
- Excel 인제스터(W1-03)가 청킹 엔진(W1-06)에 의존 → W1-06 먼저 설계 후 W1-03~05 구현 권장

**다음:** INFRA-01 (프로젝트 디렉토리 구조 + pyproject.toml + settings.yaml 초안)

---

## HO-003 | 2026-04-05 | INFRA-02

**완료:** 토큰 카운터 유틸리티 구현
- `scripts/__init__.py` — 패키지 초기화
- `scripts/token_counter.py` — 토큰 추정/예산/청킹 전략 판단 전체 구현

**결정사항:**
- 토큰 추정: `len(text.encode("utf-8")) / 4` 방식 채택
  - 영문(~1바이트/글자)과 한글(~3바이트/글자) 모두 동일 공식 적용 가능
  - API 호출 없이 로컬 추정, ±15% 오차로 전략 결정에 충분
- 청킹 전략: settings.yaml의 `single_pass_threshold`(0.8) / `map_reduce_threshold`(3.0) 기준 자동 분기
- 핵심 함수: `estimate_tokens`, `get_available_tokens`, `get_chunking_strategy`, `token_budget_report`, `file_budget_report`

**주의:**
- `estimate_tokens()`는 바이트 기반 추정 — 실제 Claude 토큰 수와 ±15% 오차 가능
- `load_settings()`는 경로 미지정 시 `config/settings.yaml` 자동 참조 (프로젝트 루트 기준)
- W1-06 청킹 엔진에서 이 모듈의 `calculate_chunks_needed()`, `get_chunking_strategy()` 를 그대로 사용할 것

**다음:** W1-01 (웹 아티클 인제스터)

---

## HO-004 | 2026-04-05 | W1-01

**완료:** 웹 아티클 인제스터 구현
- `scripts/ingest_web.py` — URL → 마크다운 변환 + 이미지 다운로드 + raw/articles/ 저장

**결정사항:**
- trafilatura `fetch_url()` + `extract(with_metadata=False)` 조합 — 자체 frontmatter 중복 방지
- 이미지 다운로드: `requests` 사용, URL MD5 해시로 파일명 (중복 방지)
- 제목 중복 처리: trafilatura가 본문 첫 줄에 H1 포함 → 자동 제거 후처리 적용
- 파일명 충돌: 동일 슬러그 존재 시 URL 해시 6자리 접미사 추가
- `requests` 의존성을 `pyproject.toml`에 명시 추가

**주의:**
- `trafilatura.extract_metadata()` 는 일부 사이트에서 title 빈 값 반환 가능 → 빈 경우 URL 슬러그로 fallback
- settings.yaml `ingest.image_download: false` 시 이미지 다운로드 건너뜀
- 이미지 URL이 `/assets/...` 같은 상대경로면 마크다운에 그대로 남음 (외부 절대 URL만 처리)

**다음:** W1-02 (PDF 인제스터)

---

## HO-006 | 2026-04-05 | W1-06

**완료:** 청킹 엔진 구현
- `scripts/chunking.py` — 마크다운 문서 분할 전체 로직

**결정사항:**
- 헤딩 기반 1차 분할 → 섹션 단위 팩킹 → 단락/문장 단위 폴백 (3단계 분할 계층)
- 청크 헤더 형식: `[문서명 / 섹션명 / N개 중 K번째]`
- overlap: 이전 청크 마지막 부분을 `<!-- overlap --> ... <!-- /overlap -->` 블록으로 삽입
- hierarchical 전략: chunk_size_ratio=0.4 (40%)로 작게 쪼개고, L1 그룹 번호(group 필드) 부여
  - 컴파일러(W2-02)는 group 번호로 묶어 계층 요약 수행
- `save_chunks()` 호출 시 `.meta.yaml` 자동 생성 (각 청크의 섹션명, 토큰 수, level/group 기록)
- 퍼블릭 API 3종: `chunk_document()`, `save_chunks()`, `chunk_file()`

**주의:**
- `_overlap_tail()` 은 바이트 기준 역산이므로 정확히 overlap_tokens 토큰이 아닐 수 있음 (±10%)
- single_pass 전략도 청크 헤더 삽입됨 → 컴파일러가 일관된 형식으로 처리 가능
- `chunk_file(save=True)` 시 기본 출력 경로는 `wiki/chunks/{doc_name}/`

**다음:** W1-03 (Excel 인제스터)

---

## HO-005 | 2026-04-05 | W1-02

**완료:** PDF 인제스터 구현
- `scripts/ingest_pdf.py` — PDF → 마크다운 변환 + 이미지 추출 + raw/papers/ 저장

**결정사항:**
- 폰트 크기 분석: 문서 전체에서 가장 많이 등장하는 크기를 body로 삼고, ×1.1/×1.2/×1.5 배율로 H3/H2/H1 임계값 자동 계산
- 짧은 굵은 텍스트(< 120자) → `**bold**` 소제목 처리 (헤딩 레벨 미달 시)
- 이미지 중복 방지: 바이트 MD5 해시로 파일명 결정 (URL MD5와 동일 전략)
- 페이지 구분자: 각 페이지 사이 `---` 수평선 삽입
- Vision 캡션: `settings.yaml`의 `ingest.vision_caption` 플래그로 on/off

**주의:**
- `fitz.open()` 으로 PDF를 열 때 암호화된 파일은 실패함 → 별도 처리 없음 (error 반환)
- 스캔 PDF(이미지 기반) 는 텍스트 추출 불가 → 이미지 추출 + Vision 캡션만 가능
- `_get_font_stats()`는 전체 문서를 두 번 순회 (1번: 통계, 2번: 변환) — 대용량 PDF 주의

**다음:** W1-06 (청킹 엔진) 또는 W1-03 (Excel 인제스터)
- HO-001 주의: Excel/PPT/Word 인제스터(W1-03~05)는 W1-06에 의존 → W1-06 먼저 권장

---

## HO-008 | 2026-04-05 | W1-04

**완료:** PowerPoint 인제스터 구현
- `scripts/ingest_ppt.py` — .pptx → 마크다운 변환 + raw/office/ 저장

**결정사항:**
- 슬라이드 헤딩: `## Slide N: 제목` 형식
- 도형 처리 우선순위: 이미지 → 테이블 → 텍스트 순
- 폰트 크기 기반 헤딩 추정: ≥28pt → `###`, ≥20pt → `####`, 그 외 텍스트
- 발표자 노트: `> **Note:**\n> ...` 블록쿼트 형식
- 청킹: 10슬라이드 단위, 각 청크 앞에 전체 목차(TOC) 반복 삽입
- 청크 경계 마커: `<!-- chunk N/M: Slide X–Y -->` HTML 주석
- 이미지: MD5 해시로 파일명, `raw/images/` 저장
- Vision 캡션: `vision_caption` 플래그 on/off
- 제목: `prs.core_properties.title` 우선, 없으면 파일명

**주의:**
- `placeholder_format.idx 0,1`이 제목 플레이스홀더 — 일부 테마는 다른 idx 사용 가능
- `.ppt` (구형 바이너리) 포맷은 python-pptx 미지원 → 에러 반환
- 슬라이드 이미지 도형이 그룹화된 경우 개별 이미지 추출 안 됨 (MSO_SHAPE_TYPE.PICTURE만 처리)

**다음:** W1-05 (Word 인제스터)

---

## HO-009 | 2026-04-05 | W1-05

**완료:** Word 인제스터 구현
- `scripts/ingest_word.py` — .docx → 마크다운 변환 + raw/office/ 저장

**결정사항:**
- 헤딩: `para.style.name` 소문자 비교 → "heading 1~6" → #~######
- 표: `doc.element.body` XML 직접 순회로 단락/표 혼합 순서 보존 (`Document.paragraphs`는 표 건너뜀)
- 각주/미주: `doc.part.part_related_by(rel_type)` → XML iter로 텍스트 추출, 문서 하단에 `[^N]: ...` 형식 모음
- 런 기반 텍스트: bold/italic → **/***/*** 처리
- H2 단위 청킹: `_split_at_h2()` → `_build_chunks()`, `min_chunk_tokens` 미만 섹션은 다음 섹션과 병합
- overlap: `_overlap_tail(prev_body, overlap_tokens)` → `<!-- overlap --> ... <!-- /overlap -->` 블록으로 삽입
- 제목 우선순위: `core_properties.title` → 첫 번째 Heading 1 → 파일명

**주의:**
- `.doc` (구형 바이너리) 포맷은 python-docx 미지원 → 명확한 에러 메시지 반환
- `_get_notes()` 는 각주 id ≤ 0 (구분자 각주) 자동 건너뜀
- 리스트 스타일 감지: style name에 "list bullet" / "list number" / "list paragraph" 포함 여부 기반
  - 일부 템플릿은 다른 스타일명 사용 가능 → 오탐 시 일반 단락으로 fallback
- `_runs_to_text()` fallback: 런이 비어 있으면 `para.text` 사용

**다음:** W2-01 (단일 문서 컴파일러)

---

## HO-010 | 2026-04-05 | W2-01

**완료:** 단일 문서 컴파일러 구현
- `scripts/compile.py` — 마크다운 문서 → LLM → wiki/concepts/ 항목 생성

**결정사항:**
- `compile_document(path)` — 파일 기반 진입점, `compile_text(text)` — 텍스트 직접 처리
- `{{ variable }}` 형식 템플릿 치환: `re.sub` 기반 `_render()` 함수 (Jinja2 의존 없음)
- LLM 출력에서 마크다운 코드 펜스(`` ```markdown ... ``` ``) 자동 제거 후 저장
- 개념명: H1 제목 기준 자동 추출 → 파일명(`_concept_to_filename()`)으로 변환
- 파일명 충돌: 내용이 같으면 덮어쓰기(갱신), 다르면 `_2`, `_3` 접미사
- single_pass 전략만 담당 — map_reduce/hierarchical은 `ValueError` 발생시켜 W2-02 유도
- CLI: `python -m scripts.compile <파일>` → JSON 출력

**주의:**
- `ANTHROPIC_API_KEY` 환경변수 필수 (settings.yaml의 `api_key_env` 참조)
- `_render()` 는 `{{ key }}`만 치환, 미등록 키는 원문 유지
- `compile_document()` 는 `raw/` 원본을 수정하지 않음 (읽기만)
- wiki_root 기본값은 프로젝트 루트 / `settings.yaml paths.wiki`

**다음:** W2-02 (청크 Map-Reduce 컴파일러)

---

## HO-012 | 2026-04-05 | W2-03

**완료:** 인덱스 자동 갱신 구현
- `scripts/index_updater.py` — _index.md / _summaries.md LLM 갱신 + 백링크 삽입
- `scripts/compile.py` — `compile_document()` / `compile_text()`에 `update_index=True` 파라미터 추가, 완료 후 자동 호출

**결정사항:**
- `update_all()` 이 세 작업(index, summaries, backlinks)을 순차 실행 — 각각 skip 플래그로 선택적 비활성화 가능
- 백링크 삽입은 LLM 없이 `[[개념]]` 정규식 파싱으로 처리 (비용/속도 우위)
- 역방향 백링크: 새 항목 A가 [[B]]를 언급하면 B.md에 `- [[A]] — 역참조` 삽입
- `_find_concept_file()` 2단계 탐색: 파일명 직접 매칭 → H1 개념명 전체 스캔 (언더스코어 변환 불일치 대비)
- 컴파일 실패와 인덱스 갱신 실패를 분리 — 갱신 실패 시 `logger.warning`만 기록, 컴파일 결과는 반환
- `_update_frontmatter_date()`: LLM 출력의 `last_updated` 를 실제 오늘 날짜로 강제 수정

**주의:**
- `insert_backlinks()`는 `[[개념]]` 이 `wiki/concepts/` 에 실제 파일로 존재할 때만 삽입 — 없는 개념은 `skipped` 처리
- `'## 관련 개념'` 섹션이 없는 파일에는 파일 끝에 섹션 자체를 새로 생성
- 중복 백링크 방지: `_has_backlink()` 로 사전 확인 후 건너뜀

**다음:** W2-04 (증분 컴파일러)

---

## HO-013 | 2026-04-05 | W2-04

**완료:** 증분 컴파일러 구현
- `scripts/incremental.py` — 해시 감시 / 선택적 재컴파일 / 충돌 감지 / watchdog 전체 구현
- `config/prompts.yaml` — `detect_conflict` 프롬프트 추가
- `wiki/conflicts/` 디렉토리 생성

**결정사항:**
- 해시 스토어: SHA256, `.kb_hashes.json` (settings.yaml `paths.hash_store` 참조)
- 관련 개념 탐색: wiki/concepts/*.md frontmatter의 `source_files` 필드 스캔 (2단계 경로 정규화)
- 충돌 감지: 변경 파일(`modified`) 한정 실행 — 신규 파일은 비교 대상 기존 wiki 없음
  - LLM 응답이 "NONE"이면 충돌 없음으로 처리, 있으면 wiki/conflicts/ 저장
- watchdog debounce 1초 — 연속 이벤트를 하나로 묶어 compile_changed() 호출
- `compile_changed()` 실행 흐름: 해시 비교 → 백업 → compile_document() → 충돌 감지 → 해시 갱신
- anthropic 임포트를 `_call_llm()` 내부로 이동 — watchdog 미설치 환경에서도 hash/conflict-free 동작 가능하도록

**주의:**
- watchdog 미설치 시 `compile_changed()`는 정상 동작, `watch()`만 ImportError 발생
- `find_related_concepts()` 는 frontmatter `source_files` 가 없는 오래된 wiki 파일은 건너뜀
- `detect_conflict()` 는 LLM 비용 발생 — `check_conflicts=False` 로 비활성화 가능
- 충돌 파일명: `{날짜}_{wiki파일명}_vs_{소스파일명}.md` (각 30자 제한)

**다음:** W3-01 (기본 질의 처리)

---

## HO-014 | 2026-04-05 | W3-01

**완료:** 기본 질의 처리 엔진 구현
- `scripts/query.py` — 우선순위 기반 컨텍스트 조립 + LLM 질의 전체 구현

**결정사항:**
- 관련도 점수: 키워드 기반 (`_score_relevance`), 파일명 매칭 가중치 2.0 / 본문 1.0
  - RAG/벡터 없이 단순 단어 포함 여부로 처리 — Karpathy 방식 준수
  - 한글 포함 고려: `[\w가-힣]{2,}` 정규식으로 단어 추출
- P1(인덱스) → P2(concepts 관련도 순) → P3(explorations 관련도 순) 순으로 토큰 예산 채움
- 토큰 예산: `get_available_tokens(settings)` 직접 사용 (token_counter.py 재사용)
- 반환값: `{question, answer, used_files, token_budget, tokens_used, context_stats}`
  - `context_stats`에 P1/P2/P3/skipped 분류 통계 포함 — W3-02 fallback 로직에 활용 가능
- CLI: `python -m scripts.query "<질문>"` → 답변 + 메타 출력

**주의:**
- `build_context()`는 예산 초과 시 concept 파일을 단순 스킵 (트런케이션 없음)
  - 트런케이션/압축 fallback은 W3-02에서 구현 — 이 함수가 그 진입점
- P1 파일(_index.md, _summaries.md)이 없으면 조용히 건너뜀 (빈 wiki 환경 대응)
- `_score_relevance()` 점수 0인 파일은 컨텍스트 제외 — 완전 무관 파일 필터링

**다음:** W3-02 (컨텍스트 압축 fallback)

---

## HO-015 | 2026-04-05 | W3-02

**완료:** 컨텍스트 압축 fallback 구현
- `scripts/query.py` — `build_context_compressed()`, `_first_paragraph()`, `_query_decomposed()` 추가
- `query()` 함수에 3단계 자동 fallback 로직 통합

**결정사항:**
- Fallback 1 (`first_para`): concept 파일을 frontmatter 제외 첫 단락(H1+바로 아래 단락)으로 잘라 포함
  - `[압축: 첫 단락, 관련도: N.NN]` 레이블로 LLM에게 압축 사실 명시
- Fallback 2 (`summaries_only`): 개별 concept 파일 생략, P1(_index+_summaries)만 사용
  - 압축된 concept 파일은 `stats["compressed"]`에 기록 (추적 용)
- Fallback 3 (서브 질문 분해): `query_decompose` → 각 서브 질문에 build_context + fallback1 → `query_merge`
  - 재귀 방지: 서브 질문 처리는 fallback 1까지만 허용
- 반환값에 `fallback_level` 필드 추가 (0~3)
- CLI 출력에도 fallback 단계 표시

**주의:**
- `_query_decomposed()`는 서브 질문 수만큼 LLM 호출 발생 — 비용이 가장 비쌈
- `query_decompose` 프롬프트 응답이 JSON 배열이 아닐 경우 원래 질문으로 단일 처리 (graceful fallback)
- Fallback 3의 `tokens_used`는 각 서브 질문 tokens_used 합산 (근사값)
- `build_context_compressed(mode="summaries_only")`은 P1 파일도 예산 초과 시 스킵될 수 있음

**다음:** W3-03 (탐색 결과 저장)

---

## HO-016 | 2026-04-05 | W3-03

**완료:** 탐색 결과 저장 구현
- `scripts/exploration.py` — 신규 생성 (탐색 저장 전체 로직)
- `scripts/query.py` — `query()` 함수에 `save=True` 파라미터 추가 + CLI `--save` 플래그

**결정사항:**
- `save_exploration(result, ...)` 가 진입점 — `query()` 결과 dict를 그대로 받음
- LLM 호출 1회로 탐색 파일 내용 생성 (`save_exploration` 프롬프트 재사용)
- 새 개념: `_parse_list_section(text, '발견된 새 개념')` 로 파싱 → `_create_concept_stub()` 로 stub 생성
  - 파일명 매칭 + H1 본문 스캔 2단계 중복 확인
  - stub에 `status: stub` frontmatter 플래그 → `kb compile` 시 식별 가능
- 갭: `_parse_list_section(text, '추가 조사 필요')` → `_append_gaps()` — 중복 방지 포함
- `query(save=True)` 시 `result["exploration"]` 키에 저장 메타 반환, 저장 실패는 warning만 (질의 결과 불영향)
- `_parse_list_section()` : `^(?:[-*]\s+|\d+[.)]\s+)(.*)` 정규식으로 선두 기호 제거, `[[개념명]]` 추출, `(...)` placeholder 자동 제거

**주의:**
- `exploration.py`는 `scripts.query`를 임포트하지 않음 (순환 임포트 방지)
- `query.py`에서 `from scripts.exploration import save_exploration`를 함수 내부에서 지연 임포트
- 탐색 파일 파일명: `YYYY-MM-DD_{슬러그 최대40자}.md` — 충돌 시 `_2`, `_3` 접미사
- LLM이 `발견된 새 개념` 섹션에 개념을 쓰지 않으면 stub 미생성 (graceful)

**다음:** W4-01 (CLI 인터페이스)

---

## HO-019 | 2026-04-05 | W4-03

**완료:** MVP 통합 테스트 구현 + 전체 통과 (68/68)
- `tests/conftest.py` — fixture: `proj`(임시 프로젝트 구조), `mock_settings`, 샘플 생성 헬퍼
- `tests/test_token_counter.py` — 16개 단위 테스트
- `tests/test_chunking.py` — 11개 단위 테스트 (save_chunks/chunk_file 실제 API 반영)
- `tests/test_compile.py` — 11개 단위 테스트 (mock LLM)
- `tests/test_query.py` — 11개 단위 테스트 (mock LLM)
- `tests/test_exploration.py` — 12개 단위 테스트 (mock LLM) + 루프 1회 검증
- `tests/test_integration.py` — 12개 통합 테스트 (50건 인제스트 → 컴파일 → 복합질문 5개 → 탐색-재편입 루프)
- `scripts/test_mvp.py` — CLI 실행 가능 MVP 리포터 (mock/real LLM 선택)

**결정사항:**
- 모든 LLM 호출은 `unittest.mock.patch`로 모킹 — API 키 없이 실행 가능
- `proj` fixture는 `tmp_path` 기반 완전한 프로젝트 구조 생성 + 실제 prompts.yaml 복사
- `test_mvp.py` 기본값: 임시 디렉토리 사용 (프로젝트 wiki/raw 오염 방지), `--project` 플래그로 실제 디렉토리 사용
- `chunk_file()` / `save_chunks()` 반환값이 `list`가 아닌 `dict` — 초기 테스트에서 발견해 수정
- `compile_text()`의 파라미터가 `doc_name`이 아닌 `source_label` — 마찬가지로 발견해 수정
- `save_exploration()` 반환 키가 `exploration_path`가 아닌 `exploration_file` — 수정

**주의:**
- `tests/test_integration.py::TestExplorationLoop::test_full_loop_once` 는 50건 컴파일로 ~20초 소요
- `test_mvp.py --project` 실행 시 실제 raw/wiki 수정됨 — 주의해서 사용
- mock `_call_llm`은 user_prompt의 첫 `# ` 라인을 개념명으로 추출하므로, 실제 프롬프트 템플릿 구조에 따라 다른 개념명이 추출될 수 있음 (통합 테스트에서는 임시 디렉토리 사용이므로 문제없음)

**다음:** Phase 2 시작 — P2-01 (웹 UI) 또는 P2-03 (YouTube 자막 인제스터) 선택

---

## HO-018 | 2026-04-05 | W4-02

**완료:** Obsidian 연동 설정 + 검증
- `wiki/.obsidian/app.json` — 위키링크 모드, 첨부파일 경로 `../raw/images`
- `wiki/.obsidian/graph.json` — `chunks/` 필터, 폴더별 색상 구분
- `wiki/concepts/LLM_지식베이스_시스템.md` — 데모 개념 파일 (시스템 자체 설명)
- `wiki/OBSIDIAN_SETUP.md` — vault 열기 가이드
- `scripts/verify_obsidian.py` — 연동 호환성 자동 검증 도구

**결정사항:**
- vault 루트: `wiki/` 폴더 (프로젝트 루트가 아님)
- `chunks/` 는 graph.json `search: "-path:chunks/"` 필터로 그래프 뷰에서 제외 (중간 파일 노이즈 차단)
- 그래프 색상: concepts=파랑, explorations=초록, conflicts=빨강, 관리파일=노랑
- 이미지: `attachmentFolderPath: "../raw/images"` → vault 외부 raw/images/ 자동 연결
- `[[링크]]` 형식이 Obsidian wikilink와 완전 동일 → 백링크 패널·그래프 뷰 즉시 동작
- `verify_obsidian.py` 검증 결과: ERR 0건, WARN 0건, INFO 4건 (미래 stub 링크 — 정상)

**주의:**
- `_summaries.md` 주석의 `` `[[개념명]]` `` 가 verify_obsidian에서 INFO로 잡힘 (코드 스팬 내부 — 무시해도 됨)
- Obsidian 첫 실행 시 vault 신뢰 확인 팝업이 뜸 (`.obsidian/` 존재해도 보안 정책상 표시)
- `.obsidian/workspace.json` 은 Obsidian이 자동 생성 — 커밋에서 제외 권장 (`.gitignore`에 추가 가능)

**다음:** W4-03 (MVP 통합 테스트)

---

## HO-017 | 2026-04-05 | W4-01

**완료:** CLI 인터페이스 구현
- `scripts/cli.py` — typer + rich 기반 전체 CLI

**결정사항:**
- 4개 주 명령어: `ingest`, `compile`, `query`, `status`
- 보너스 명령어 `watch` 추가 (incremental.py의 watch() 래핑)
- `ingest`: URL → `ingest_web`, .pdf → `ingest_pdf`, .xlsx/.xls/.xlsm → `ingest_excel`, .pptx → `ingest_ppt`, .docx → `ingest_word`, .md/.txt → 직접 복사
- `compile` 옵션: `--all`(전체), `--changed`(기본), `--file`(개별), `--dry-run`, `--no-index`, `--workers`
  - `--all` 실행 시 인덱스 갱신은 마지막 파일에서만 1회 수행 (비용 절감)
- `query` 옵션: `--save`(explorations 저장), `--verbose`(컨텍스트 통계/파일 목록)
- `status`: raw 건수(하위 디렉토리별), wiki 개념/탐색/충돌 건수, stub 수, gaps 수, 마지막 컴파일 시각 (hash_store mtime 기반)
- 모든 LLM 호출 구간에 rich SpinnerColumn 표시 (transient — 완료 후 사라짐)
- `_load_settings_safe()`: settings.yaml 미존재 시 명확한 오류 메시지 + Exit(1)

**주의:**
- `kb compile` 옵션 없이 실행하면 `--changed` 와 동일 (가장 안전한 기본값)
- `kb watch` 는 watchdog 미설치 시 ImportError → 명확한 오류 메시지 출력 후 종료
- `_compile_all` 은 `.md` 파일만 대상 (인제스터가 이미 .md로 변환한 파일 처리)
- `kb status` 의 gaps 수는 `gaps.md`의 `- ` 시작 줄 카운트 기반 (근사값)

**다음:** W4-02 (Obsidian 연동 확인)

---

## HO-011 | 2026-04-05 | W2-02

**완료:** 청크 Map-Reduce 컴파일러 구현
- `scripts/compile.py` — `compile_document()` 전략 자동 라우팅 + Map-Reduce/Hierarchical 내부 함수 추가

**결정사항:**
- 별도 파일 대신 `compile.py`에 통합 — 공유 유틸(`_call_llm`, `_render`, `_strip_fence` 등) 재사용
- 병렬 처리: `ThreadPoolExecutor` (I/O bound LLM 호출에 적합), `max_workers=4` 기본값
- `compile_document()` / `compile_text()` 가 전략을 자동 선택 — 호출자는 전략을 신경 쓸 필요 없음
- Hierarchical: L2 청크 → 그룹별 병렬 요약(L1) → 최종 통합 (2단계 Map-Reduce)
  - L1 통합 시 `wiki_index` 대신 `"(그룹 N/M 중간 요약)"` 문자열 전달 — 불필요한 인덱스 컨텍스트 제거
- 반환값에 `chunk_count` 추가 (single_pass: 1, 나머지: 실제 청크 수)

**주의:**
- `compile_chunk_summary` 프롬프트의 `system` 필드는 비어 있음 — `_render(tmpl["system"], {})` 호출 시 빈 문자열 전달됨. Claude API는 빈 system 허용
- `chunk.section` 을 `chunk_range` 변수로 전달 — 섹션명이 청크 범위 역할
- `_compile_hierarchical_chunks()`의 그룹 번호는 0-based (`chunk.group`) — 로그 출력 시 `g_num + 1`로 1-based 변환

**다음:** W2-03 (인덱스 자동 갱신)

---

## HO-007 | 2026-04-05 | W1-03

**완료:** Excel 인제스터 구현
- `scripts/ingest_excel.py` — .xlsx/.xls/.xlsm → 마크다운 테이블 변환 + raw/office/ 저장

**결정사항:**
- openpyxl 두 번 로드: `data_only=False`(수식 문자열) + `data_only=True`(캐시 계산값) 조합
  - 수식셀: `계산값 [formula: =수식]` 형식으로 병기
  - `data_only=True` 값은 마지막 저장 시 캐시된 값이므로 실시간 계산이 아님 (openpyxl 한계)
- 청킹: 1000행 단위 시트 내 분할, 각 청크에 컬럼 헤더 반복 (`settings.yaml`의 `excel_rows_per_chunk` 참조)
- 차트: xlsx 차트는 렌더링 이미지 없음 → 차트 타입/제목/시리즈 메타데이터 텍스트 설명으로 대체
  - `read_only=False`로 별도 로드하여 `ws._charts` 접근 (read_only 모드에서는 _charts 미지원)
- `.meta.yaml`: 시트별 행수/열수/차트수 포함

**주의:**
- `read_only=True`로 열면 `ws._charts` 접근 불가 → 차트 추출 시 `read_only=False` 재로드 필요
- 대용량 파일에서 `read_only=False` 재로드는 메모리 이슈 가능 → 향후 최적화 대상
- `.xls` 포맷은 openpyxl이 지원하지 않을 수 있음 (xlrd 필요) → 현재는 에러 반환

**다음:** W1-04 (PowerPoint 인제스터)

---

## HO-002 | 2026-04-05 | INFRA-01

**완료:** 프로젝트 디렉토리 구조 + 설정 파일 초안 생성
- `raw/articles/`, `raw/papers/`, `raw/repos/`, `raw/office/`, `raw/images/`
- `wiki/concepts/`, `wiki/explorations/`, `wiki/chunks/`
- `wiki/_index.md`, `wiki/_summaries.md`, `wiki/gaps.md`
- `config/settings.yaml` — 모델 프로필 + 청킹 임계값 + 경로 설정
- `config/prompts.yaml` — 컴파일/쿼리/인제스트 프롬프트 템플릿 전체
- `pyproject.toml` — uv 기반 의존성 선언

**결정사항:**
- `context_limit`은 입력 한도 기준 (기획서 주의사항 반영)
- 청킹 임계값 80%/300%를 `single_pass_threshold`/`map_reduce_threshold`로 명명
- `prompts.yaml`에 compile, query, vision, exploration 전체 프롬프트 포함
- `wiki/chunks/` 추가 — Map-Reduce 청크 요약 저장용 (기획서 3.2-B)

**주의:**
- `scripts/cli.py` 아직 없음 → `kb` CLI 동작 안 함 (W4-01에서 구현)
- `prompts.yaml`의 `{{ variable }}`은 Python에서 str.format_map() 또는 jinja2로 치환
- `pyproject.toml` entry point: `scripts.cli:app`

**다음:** INFRA-02 (토큰 카운터 유틸리티)

---

---

## HO-008 | 2026-04-05 | P2-01

**완료:** 웹 UI 구현 — `web/` 디렉토리 (Next.js 16.2.2 + Tailwind CSS)
- `web/lib/wiki.ts` — wiki 파일 읽기 유틸 (concepts, explorations, root 파일)
- `web/app/page.tsx` — 홈 (_summaries.md + 개념 목록)
- `web/app/concepts/page.tsx` — 개념 목록 페이지
- `web/app/concepts/[slug]/page.tsx` — 개념 상세 (마크다운 렌더링, [[위키링크]] → Next.js Link 변환)
- `web/app/explorations/page.tsx` + `[slug]/page.tsx` — 탐색 기록
- `web/app/gaps/page.tsx` — 갭 목록
- `web/app/search/page.tsx` — 클라이언트 사이드 검색 UI
- `web/app/api/search/route.ts` — Fuse.js 기반 검색 API
- `web/components/MarkdownRenderer.tsx` — react-markdown + remark-gfm, Obsidian [[링크]] 자동 변환

**결정사항:**
- `react-markdown` 채택 (next-mdx-remote 대신) — wiki 파일이 .mdx가 아닌 순수 .md이므로 충분
- Fuse.js 퍼지 검색: title(가중치 2) + content(가중치 1), threshold 0.4
- `WIKI_DIR` 환경변수로 wiki 경로 오버라이드 가능 (기본값: `../wiki`)
- 빌드: `pnpm build` 성공, 정적 생성(SSG) + 검색 API는 서버사이드(Dynamic)
- 개발 서버: `WIKI_DIR=../wiki pnpm dev --port 3000`

**주의:**
- `web/` 안에서 `pnpm install` 필요 (루트 pyproject.toml과 별개 프로젝트)
- Turbopack 경고 있음 (lib/wiki.ts의 `path.join`/`fs` 사용) — 빌드는 정상 완료
- `rehype-highlight` 설치했으나 현재 코드 하이라이팅 미적용 — 필요 시 MarkdownRenderer에 추가

**다음:** P2-02 (개념 그래프 뷰, D3.js) 또는 P2-03 (YouTube 자막 인제스터)

---

## HO-009 | 2026-04-06 | P2-02

**완료:** 개념 그래프 뷰 구현 — D3.js 포스 다이렉트 그래프
- `web/lib/wiki.ts` — `buildGraphData()`, `GraphNode`, `GraphEdge`, `GraphData` 추가 (extractWikiLinks 포함)
- `web/app/api/graph/route.ts` — 그래프 데이터 JSON API (Dynamic)
- `web/components/ConceptGraph.tsx` — D3.js 클라이언트 컴포넌트 (포스 시뮬레이션, 줌/팬, 드래그, 클릭 내비게이션)
- `web/app/graph/page.tsx` — 그래프 페이지
- `web/app/layout.tsx` — 사이드바에 "그래프" 링크 추가

**결정사항:**
- D3 v7 (`d3` + `@types/d3`) — pnpm으로 설치
- `[[위키링크]]` 정규식 추출 → 개념/탐색 간 엣지 생성, 중복 엣지 제거 (방향 무관 dedup)
- 노드 반지름 = `6 + (inDegree / maxDegree) * 14` — 연결 많을수록 큼
- 색상: 개념 파랑(`#3b82f6`), 탐색 보라(`#8b5cf6`)
- 줌/팬: `d3.zoom` 스케일 0.2~4x
- 드래그로 노드 고정, 마우스 놓으면 fx/fy=null (재시뮬레이션)
- 클릭 → `router.push()` (concepts/ 또는 explorations/)
- 범례 + 호버 툴팁 (절대 포지션 오버레이)
- `pnpm build` 통과 — `/graph` 는 Static 페이지, `/api/graph` 는 Dynamic API

**주의:**
- `ConceptGraph.tsx` 는 `"use client"` — SSR 불가, D3 DOM 접근은 useEffect 내부에서만
- 개념 수가 많아지면 포스 시뮬레이션 성능 저하 가능 → P2-07(대용량 최적화)에서 처리
- `extractWikiLinks()`는 `[[링크|별칭]]`, `[[링크#섹션]]` 형식도 처리 (파이프/샵 이후 제거)
- `inDegree` 초기화를 allSlugs 기반으로 함 — 링크가 없는 노드도 0으로 정상 포함

**다음:** P2-03 (YouTube 자막 인제스터)

---

## HO-010 | 2026-04-06 | P2-03

**완료:** YouTube 자막 인제스터 구현
- `scripts/ingest_youtube.py` — YouTube URL → 자막 추출 → 마크다운 변환 전체 구현
- `scripts/cli.py` — `_is_youtube_url()` 추가 + ingest 라우팅에 YouTube 분기 추가
- `pyproject.toml` — `youtube-transcript-api>=0.6.0` 의존성 추가

**결정사항:**
- `youtube-transcript-api` 채택 — API 키 불필요, 수동/자동 생성 자막 모두 지원
- 메타데이터: YouTube oEmbed API (`youtube.com/oembed?url=...`) — API 키 없이 title/channel 조회
- 언어 우선순위: ko → en → ja → zh-Hans → zh-Hant (수동 자막 먼저, 없으면 자동 생성)
- 타임스탬프 섹션: 120초(2분) 단위로 `## [MM:SS](유튜브링크)` 헤더 생성
  - 섹션 내 세그먼트들은 공백으로 연결해 단락으로 합침
- 출력 경로: `raw/articles/{날짜}_yt_{슬러그}.md` (웹 아티클과 동일 디렉토리)
- `.meta.yaml`: video_id, channel, language, is_generated_transcript, segment_count 포함
- CLI: `youtube.com` 또는 `youtu.be` 포함 URL → ingest_youtube 자동 라우팅

**주의:**
- `TranscriptsDisabled` 예외: 자막 비활성 영상은 "error" status 반환 (크래시 없음)
- oEmbed API 실패 시 title은 `"YouTube Video {video_id}"` fallback
- `_fetch_transcript` 내부에서 `youtube_transcript_api` import — 설치 안 됐을 때 ImportError 명확
- 지원 URL: `watch?v=`, `youtu.be/`, `/shorts/`, `/embed/` 4가지 패턴

**다음:** P2-04 (GitHub 레포 인제스터)

---

## HO-012 | 2026-04-06 | P2-05

**완료:** 위키 공유 기능 (읽기 전용 링크) 구현
- `web/components/ShareButton.tsx` — 클립보드 복사 버튼 컴포넌트 ("use client")
- `web/app/(share)/share/[type]/[slug]/page.tsx` — 사이드바 없는 클린 공유 뷰
- `web/app/(share)/layout.tsx` — share 그룹 레이아웃 (pass-through)
- `web/app/(main)/layout.tsx` — 사이드바 레이아웃 (기존 layout.tsx에서 분리)
- `web/app/layout.tsx` — 루트 html/body only (사이드바 제거)
- `web/app/(main)/concepts/[slug]/page.tsx` — ShareButton 추가
- `web/app/(main)/explorations/[slug]/page.tsx` — ShareButton 추가
- `scripts/share.py` — 스탠드얼론 HTML 내보내기 (의존성 없음, 자체 MD→HTML 변환)
- `scripts/cli.py` — `kb share <개념명>` 명령어 추가

**결정사항:**
- Next.js route group 패턴 사용: `(main)` + `(share)` 분리 → 사이드바 없는 공유 뷰 구현
  - 기존 모든 페이지를 `app/(main)/`로 이동, 공유 페이지는 `app/(share)/`
  - URL은 변경 없음 (`/concepts/...` → `/concepts/...`, `/share/...` → `/share/...`)
- 공유 URL: `/share/concepts/{slug}` 또는 `/share/explorations/{slug}` (읽기 전용 라벨 포함)
- ShareButton: `navigator.clipboard.writeText()` + execCommand fallback (구형 브라우저 대응)
- HTML 내보내기(`scripts/share.py`): 외부 의존성 없이 자체 MD→HTML 변환 내장
  - 저장 위치: `exports/` (기본), `--output` 옵션으로 변경 가능
  - 개념 탐색 순서: concepts/ 직접 매칭 → H1 스캔 → explorations/ 슬러그 포함 매칭

**주의:**
- `web/app/(main)/api/search/route.ts` 는 실수로 복사됨 → 삭제됨, 실제 API는 `app/api/search/route.ts` 유지
- route group 이동 후 `app/layout.tsx` 는 html/body만 — 기존 metadata 선언은 `(main)/layout.tsx` 에 없음 (필요 시 추가)
- `ShareButton`은 `"use client"` — 서버 컴포넌트에서 import 시 직접 렌더링은 가능, `useState` 내부 사용

**다음:** P2-06 (팀 지식베이스) 또는 P2-07 (대용량 성능 최적화)

---

## HO-011 | 2026-04-06 | P2-04

**완료:** GitHub 레포 인제스터 구현
- `scripts/ingest_github.py` — GitHub URL → 파일 트리 탐색 → 마크다운 변환 전체 구현
- `scripts/cli.py` — `_is_github_url()` 추가 + ingest 라우팅에 GitHub 분기 추가

**결정사항:**
- GitHub API v3 (raw.githubusercontent.com 으로 파일 내용 조회) — 추가 의존성 없음
- 인증: `GITHUB_TOKEN` 환경변수 (선택적) — 없어도 공개 레포 60 req/hr 동작
- 파일 선택 전략:
  - README, pyproject.toml, package.json 등 `_PRIORITY_FILES` 최우선 수집 (priority 0)
  - 루트에 가까울수록 우선, docs/ 폴더 우대
  - 건너뛸 디렉토리: node_modules, __pycache__, .venv, dist, build, .next 등
  - 단일 파일 최대 100KB, 전체 최대 60K 토큰, 최대 40파일
- Markdown → 코드펜스 없이 삽입, 나머지는 언어 지정 코드펜스
- 출력: `raw/repos/{날짜}_gh_{owner}-{repo}.md` (`.meta.yaml` 포함)
- CLI: `github.com` 포함 URL → ingest_github 자동 라우팅

**주의:**
- `_fetch_tree()` API 응답이 `truncated: true` 이면 경고 로그 (10만 파일 초과 레포)
- 토큰 예산 초과 파일은 `skipped` 리스트로 마크다운 상단 목차에 명시
- `_parse_github_url()` 은 `/tree/branch` 포함 URL도 처리
- Private 레포는 `GITHUB_TOKEN` 필수 (없으면 404 error 반환)

**다음:** P2-05 (위키 공유 기능)
