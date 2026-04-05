# LLM 기반 개인 지식 베이스 시스템 기획서

> **참조:** Andrej Karpathy 트윗 (2039805659525644595) — "There is room here for an incredible new product instead of a hacky collection of scripts."
> **작성일:** 2026-04-05
> **채널:** @소담 AI 스튜디오 / @sodam_ai

---

## 1. 프로젝트 개요

### 1.1 배경 및 목적

Karpathy가 직접 사용 중인 개인 지식 베이스 워크플로를 제품화한다.
핵심 인사이트: 복잡한 RAG 인프라 없이도, LLM이 마크다운 위키를 직접 유지·관리하도록 하면 지식이 **복리로 축적**된다.

기존 도구들(Notion, Obsidian, Roam)은 사람이 직접 정리해야 한다. 이 시스템은 **LLM이 위키를 쓰고 유지하며**, 사람은 원본 자료를 넣고 질문만 한다.

### 1.2 핵심 가치 명제

- 자료를 넣으면 → LLM이 위키를 자동 생성·갱신
- 위키가 커질수록 → 질문의 깊이와 정확도가 향상
- 탐색 결과가 → 다시 위키로 피드백되어 지식 복리 효과

---

## 2. 시스템 아키텍처

### 2.1 전체 파이프라인

```
[원본 자료 수집]
      │
      ▼
  raw/ 디렉토리
(articles, papers, repos, images)
      │
      ▼
[LLM 컴파일러]
      │
      ▼
  wiki/ 디렉토리
(마크다운 파일, 백링크, 인덱스, 요약)
      │
      ▼
[질의 & 탐색 엔진]
      │
      ▼
[탐색 결과] ──────────────────────────────┐
(markdown / slides / visualization)       │
                                          ▼
                                 wiki/ 에 재편입 (복리 효과)
```

### 2.2 디렉토리 구조

```
knowledge-base/
├── raw/                    # 원본 자료 (읽기 전용으로 취급)
│   ├── articles/
│   ├── papers/
│   ├── repos/
│   ├── office/             # Excel, PPT, Word 원본 파일
│   └── images/
├── wiki/                   # LLM이 유지·관리하는 위키
│   ├── _index.md           # 전체 목차 + 개념 간 관계 맵
│   ├── _summaries.md       # 각 문서 요약 인덱스
│   ├── concepts/           # 개념별 정리 파일
│   └── explorations/       # 질의 탐색 결과 누적
├── config/
│   ├── prompts.yaml        # 컴파일·쿼리 프롬프트 관리
│   └── settings.yaml       # LLM 모델, 청크 크기 등
└── scripts/                # CLI 도구
    ├── ingest.py
    ├── compile.py
    └── query.py
```

---

## 3. 4단계 핵심 기능

### 3.1 Stage 1 — 데이터 인제스트 (Ingestion)

**목표:** 다양한 형태의 원본 자료를 `raw/`에 마크다운으로 수집

**지원 입력 소스:**

| 소스 유형 | 처리 방식 |
|---|---|
| 웹 아티클 | URL → HTML → 마크다운 변환 (Obsidian Web Clipper 방식) |
| PDF 논문 | PDF 파싱 → 마크다운 + 이미지 추출 |
| **Excel (.xlsx/.xls/.csv)** | 시트별 표 구조 → 마크다운 테이블 + 수식/주석 보존, 차트 이미지 추출 |
| **PowerPoint (.pptx/.ppt)** | 슬라이드별 텍스트 + 이미지 추출, 발표자 노트 포함, 슬라이드 순서 구조 유지 |
| **Word (.docx/.doc)** | 제목/본문/표/이미지 구조 보존 → 마크다운 변환, 트랙변경 내역 선택 처리 |
| GitHub 레포 | README + 핵심 코드 + 이슈 요약 |
| YouTube | 자막 다운로드 → 마크다운 트랜스크립트 |
| 이미지 | 로컬 저장 + 캡션 자동 생성 |
| 직접 입력 | 텍스트 페이스트 |

**Office 파일 처리 세부 규칙:**

- **Excel:** 데이터 시트는 마크다운 테이블로 변환. 수식은 계산 결과값 + `[formula: =SUM(...)]` 주석으로 병기. 피벗테이블/차트는 이미지로 추출 후 캡션 자동 생성. 시트가 여러 개면 각각 섹션으로 분리.
- **PPT:** 각 슬라이드를 `## Slide N: 제목` 형식으로 변환. 슬라이드 내 이미지는 로컬 저장 + Vision API로 내용 설명 생성. 발표자 노트는 `> Note:` 블록쿼트로 포함. 애니메이션/트랜지션 정보는 무시.
- **Word:** 스타일(제목1/2/3 → #/##/###) 계층 구조 보존. 표는 마크다운 테이블로. 각주/미주는 문서 하단에 모아서 변환. 트랙변경 내역은 기본 무시 (옵션으로 포함 가능).

**Office 파일 파싱 라이브러리:**
```
Excel:      openpyxl + xlrd (레거시 .xls)
PowerPoint: python-pptx
Word:       python-docx
공통 폴백:  LibreOffice headless (변환 불가 포맷 처리)
```

**핵심 규칙:**
- 이미지는 외부 URL이 아닌 **로컬 경로로 저장** (장기 보존)
- 원본 출처 URL과 수집 날짜 메타데이터 필수 기록
- `raw/`는 수정 금지 — 항상 원본 그대로 보존

---

### 3.2 Stage 2 — LLM 위키 컴파일 (Compilation)

**목표:** `raw/` 변경사항을 감지하여 `wiki/`를 증분(incremental)으로 갱신

**컴파일러 동작 방식:**

```
1. raw/ 변경 감지 (파일 해시 비교)
2. 신규/수정 파일을 LLM에 전달
3. LLM이 수행하는 작업:
   - 해당 문서의 개념 요약 생성/갱신
   - 기존 wiki/_index.md에 새 항목 추가
   - 관련 개념 파일에 백링크 삽입
   - 상충되는 내용이 있으면 충돌 노트 생성
4. wiki/ 파일 업데이트
5. _summaries.md 자동 재생성
```

**컴파일 프롬프트 핵심 지침:**
- 기존 wiki 구조와 일관된 스타일 유지
- 새 개념은 기존 개념과의 관계(유사/대립/상위/하위)를 명시
- 모든 파일 상단에 `last_updated`, `source_files` 메타데이터 유지

**증분 컴파일 조건:**
- 자료 100건 / 40만 단어 이상 시 전체 재컴파일 대신 증분 처리
- 변경된 파일과 연관된 개념 파일만 선택적 갱신

---

### 3.2-B 청킹 전략 (Chunking — 모델 컨텍스트 한도 대응)

**문제:** 사용 모델에 따라 입출력 토큰 한도가 크게 다름

```
모델 등급 분류:
  소형 (≤ 4만 토큰):  GPT-4o mini, Claude Haiku, Gemini Flash 등
  중형 (≤ 20만 토큰): GPT-4o, Claude Sonnet
  대형 (≤ 100만+):   Claude Opus, Gemini 1.5 Pro
```

**토큰 예산 자동 계산:**

```python
# settings.yaml에서 모델별 한도 설정
model_context_limit: 40000   # 사용 모델의 실제 한도 입력
reserved_for_output: 4000    # 출력 예약 토큰
reserved_for_prompt: 2000    # 시스템 프롬프트 예약
available_for_content: (limit - reserved_for_output - reserved_for_prompt)
# → 4만 모델 기준: 34,000 토큰이 실제 문서에 사용 가능
```

**파일 크기별 처리 전략:**

```
[파일 토큰 측정]
       │
       ├── ≤ 가용 토큰의 80%  → 단일 패스 처리 (그대로 전달)
       │
       ├── 80% ~ 300%        → 청크 분할 후 순차 처리
       │                        (Map-Reduce 방식)
       │
       └── 300% 초과         → 계층적 요약 트리 생성
                                (대용량 문서 특별 처리)
```

**청크 분할 규칙 (문서 유형별):**

- **Word / 긴 아티클:**
  - 제목 계층(H1/H2/H3)을 기준으로 분할 — 의미 단위 보존 우선
  - 제목 없으면 단락 경계 기준, 문장 중간 절대 자르지 않음
  - 각 청크에 `[문서명 / 섹션명 / 전체 N개 중 K번째]` 헤더 삽입
  - 앞 청크의 마지막 200 토큰을 다음 청크 앞에 overlap으로 포함 (문맥 연속성)

- **Excel (대용량 시트):**
  - 시트 단위로 1차 분할
  - 시트 내 행이 많으면 → 1000행 단위로 분할
  - 각 청크에 컬럼 헤더 항상 포함 (반복 삽입)
  - 분할 전 전체 시트 통계(행수, 컬럼명, 데이터 범위) 별도 요약 생성

- **PPT (슬라이드 많을 때):**
  - 10슬라이드 단위로 분할
  - 각 청크에 전체 목차(슬라이드 제목 리스트) 항상 포함
  - 섹션 구분자가 있으면 섹션 단위 우선 분할

**Map-Reduce 컴파일 방식 (청크 처리 시):**

```
청크 1 → LLM → 부분 요약 1 ─┐
청크 2 → LLM → 부분 요약 2 ─┤
청크 3 → LLM → 부분 요약 3 ─┤→ LLM → 최종 통합 wiki 항목
...                          │
청크 N → LLM → 부분 요약 N ─┘

각 부분 요약에 포함되는 것:
  - 핵심 개념 및 키워드
  - 이 청크에서 언급된 고유명사/수치
  - 다른 개념과의 관계 힌트
  - 다음 청크와의 연결 맥락
```

**계층적 요약 트리 (초대형 문서):**

```
원본 (예: 300페이지 보고서)
   │
   ├── 챕터 1 → 청크들 → 챕터 요약 1
   ├── 챕터 2 → 청크들 → 챕터 요약 2
   ├── ...
   └── 챕터 N → 청크들 → 챕터 요약 N
                              │
                              └→ 전체 문서 요약 (Executive Summary)
                                        │
                                        └→ wiki/_index.md 에 등록
```

**청킹 관련 메타데이터 저장:**

```yaml
# raw/office/보고서.xlsx.meta.yaml
source_file: 보고서.xlsx
total_tokens: 180000
chunk_count: 6
chunks:
  - id: chunk_001
    range: "시트1, 행 1-1000"
    tokens: 28000
    summary_file: wiki/chunks/보고서_chunk001_summary.md
  - ...
processed_at: 2026-04-05T10:00:00
model_used: claude-sonnet-4-6
context_limit_used: 40000
```

---

### 3.3 Stage 3 — 질의 & 탐색 (Query & Exploration)

**목표:** 위키를 기반으로 복잡한 질문에 심층 답변 생성

**질의 유형:**

```
단순 검색:   "X란 무엇인가?"
비교 분석:   "A와 B의 차이점은?"
종합 리포트: "이 분야의 최신 트렌드를 정리해줘"
갭 분석:     "내가 아직 수집하지 못한 중요한 내용은?"
슬라이드:    "이 주제로 발표 자료 초안 만들어줘"
```

**컨텍스트 주입 전략 (모델 한도 적응형):**

```
Step 1: 토큰 예산 계산
  available = model_limit - output_reserved - prompt_reserved

Step 2: 우선순위 기반 컨텍스트 채우기
  Priority 1 (항상 포함): _index.md + _summaries.md  (~2,000 토큰)
  Priority 2 (질문 관련): concept 파일들  (관련도 순으로 추가)
  Priority 3 (보조): exploration 결과 중 관련 항목

Step 3: 예산 초과 시 압축 단계
  → concept 파일을 첫 단락만 포함 (트런케이션)
  → 그래도 초과 시 summaries 버전으로 교체
  → 그래도 초과 시 multi-turn 분할 질의로 전환
```

**소형 모델(≤4만 토큰) 질의 특별 처리:**
- 질의를 서브 질문으로 자동 분해 → 각각 처리 → 결과 통합
- 예: "2023~2025 트렌드 비교" → 연도별 3회 쿼리 → 마지막 통합 쿼리

---

### 3.4 Stage 4 — 탐색 결과 재편입 (Compounding)

**목표:** 탐색 결과가 자동으로 위키에 피드백되어 지식이 누적

**재편입 규칙:**
- 모든 탐색 결과는 `wiki/explorations/YYYY-MM-DD_질문요약.md`로 저장
- 탐색 중 발견한 새 개념은 `wiki/concepts/`에 자동 추가
- 질문 빈도가 높은 주제는 상위 인덱스로 자동 승격
- "다음에 더 조사할 것" 항목은 `wiki/gaps.md`에 누적

---

## 4. 기술 스택

### 4.1 백엔드 (MVP)

```yaml
언어: Python 3.11+
LLM: Claude Sonnet 4.6 (claude-sonnet-4-6)  # 긴 컨텍스트 + 마크다운 품질
파일 감시: watchdog (raw/ 변경 감지)
마크다운 처리: python-markdown, mistune
PDF 파싱: pymupdf (fitz)
Office 파싱: openpyxl (Excel), python-pptx (PPT), python-docx (Word), xlrd (레거시 .xls)
Office 폴백: LibreOffice headless (변환 불가 포맷)
웹 클리핑: trafilatura + html2text
이미지 처리: Pillow + Claude Vision API
CLI: typer + rich
```

### 4.2 프론트엔드 (Phase 2)

```yaml
로컬 UI: Obsidian 연동 (wiki/ 폴더 직접 열기) — 즉시 사용 가능
웹 UI: Next.js + MDX 렌더링
그래프 뷰: D3.js (개념 간 관계 시각화)
```

### 4.2-B 모델 프로필 설정 (settings.yaml 예시)

```yaml
# 사용 모델에 맞게 한 번만 설정하면 전체 파이프라인이 자동 적응
llm:
  model: claude-sonnet-4-6
  context_limit: 200000     # 실제 모델 한도 (토큰)
  output_reserved: 8000     # 출력용 예약
  prompt_reserved: 3000     # 시스템 프롬프트용 예약
  # → 가용 컨텐츠 토큰: 189,000

chunking:
  overlap_tokens: 200          # 청크 간 overlap
  min_chunk_tokens: 500        # 최소 청크 크기 (너무 잘게 쪼개기 방지)
  excel_rows_per_chunk: 1000
  ppt_slides_per_chunk: 10

# 소형 모델 예시 (교체만 하면 동작)
# llm:
#   model: gpt-4o-mini
#   context_limit: 16000
#   output_reserved: 2000
#   prompt_reserved: 1000
#   # → 가용 컨텐츠 토큰: 13,000  (자동으로 더 잘게 청킹)
```

### 4.3 데이터 저장

```yaml
Phase 1 (MVP): 로컬 파일시스템 (마크다운)
Phase 2: SQLite (메타데이터, 검색 인덱스)
Phase 3: 선택적 클라우드 동기화 (S3 / iCloud)
```

---

## 5. 개발 로드맵

### Phase 1 — MVP (4주)

**목표:** 동작하는 로컬 CLI 도구

```
Week 1: 인제스트 파이프라인
  - URL → 마크다운 변환
  - PDF 파싱
  - Excel / PPT / Word 파싱 및 마크다운 변환
  - raw/ 디렉토리 구조 확립

Week 2: LLM 컴파일러 v1
  - 단일 문서 → wiki 항목 생성
  - _index.md, _summaries.md 자동 생성
  - 백링크 삽입 로직

Week 3: 질의 엔진
  - 컨텍스트 주입 전략 구현
  - 기본 질의 → 마크다운 답변
  - explorations/ 자동 저장

Week 4: 증분 컴파일 + 통합 테스트
  - 파일 해시 기반 변경 감지
  - CLI 인터페이스 완성
  - Obsidian 연동 확인
```

**MVP 성공 기준:**
- 자료 50건 인제스트 후 위키 자동 생성
- 임의의 복합 질문에 위키 기반 답변 생성
- 탐색 결과가 위키에 재편입되는 루프 1회 완성

---

### Phase 2 — 제품화 (8주)

```
Week 5-6: 웹 UI
  - 마크다운 렌더링 + 검색
  - 개념 그래프 뷰 (D3.js)

Week 7-8: 멀티 소스 인제스트 확장
  - YouTube 자막
  - GitHub 레포
  - 이미지 Vision 처리

Week 9-10: 협업 기능
  - 위키 공유 (읽기 전용 링크)
  - 팀 지식베이스 (공유 raw/ + 개인 wiki/)

Week 11-12: 성능 최적화
  - 대용량(1000건+) 증분 컴파일
  - 컨텍스트 압축 전략 고도화
  - 비용 최적화 (캐싱, 청크 전략)
```

---

### Phase 3 — SaaS (추후)

- 클라우드 호스팅 버전
- 모바일 클리퍼 앱 (iOS/Android)
- 팀/조직 단위 지식 관리
- API 제공 (외부 도구 연동)

---

## 6. 차별화 포인트

### vs. Notion AI / Obsidian Copilot
- 기존 도구: **사람이 정리하면** AI가 보조
- 이 시스템: **AI가 정리하며** 사람은 자료 투입과 질문만

### vs. 일반 RAG 시스템
- RAG: 벡터 DB + 임베딩 복잡도
- 이 시스템: 마크다운 인덱스 파일로 RAG 없이 동일 효과 (Karpathy 검증)

### vs. NotebookLM
- NotebookLM: 프로젝트별 분리, 지식 축적 없음
- 이 시스템: 모든 탐색이 위키로 환원되어 **복리 축적**

---

## 7. 비용 추정 (Claude API 기준)

```
인제스트 1건당:    ~$0.005  (문서 1개 → 요약 + 인덱스 업데이트)
증분 컴파일:       ~$0.01–0.05  (변경 규모에 따라)
질의 1회:         ~$0.02–0.10  (컨텍스트 크기에 따라)

월간 활발한 사용자 기준:
  - 자료 100건/월 + 쿼리 50회/월
  - 예상 API 비용: $3–8/월
```

---

## 8. 리스크 및 대응

| 리스크 | 대응 방안 |
|---|---|
| 위키 파일 LLM이 잘못 덮어씀 | raw/ 불변 원칙 + wiki/ git 버전 관리 |
| 컨텍스트 한도 초과 | 계층적 요약 (summaries of summaries) |
| LLM 비용 폭증 | 증분 컴파일 + 응답 캐싱 |
| 개념 파편화 (파일 난립) | 주기적 LLM 기반 위키 재구조화 태스크 |
| 민감 자료 유출 | 로컬 우선 아키텍처, 클라우드 옵션 선택 |

---

## 9. 다음 액션 아이템

1. **[즉시]** Karpathy의 실제 스크립트 구조 역설계 (xcancel 스레드 추가 분석)
2. **[Week 1]** `ingest.py` 프로토타입 — URL 클리핑 + raw/ 저장
3. **[Week 1]** `compile.py` 프로토타입 — 단일 문서 → wiki 항목 생성 테스트
4. **[Week 2]** 프롬프트 최적화 — 위키 일관성 유지 프롬프트 반복 실험
5. **[Week 2]** Obsidian vault로 wiki/ 폴더 열어서 UX 확인

---

## 10. 참고 자료

- Karpathy 원문 트윗: https://xcancel.com/karpathy/status/2039805659525644595
- Obsidian Web Clipper: https://obsidian.md/clipper
- trafilatura (웹 클리핑): https://trafilatura.readthedocs.io
- Claude API 문서: https://docs.anthropic.com

---

*"The explorations always add up." — Karpathy*
*지식 베이스가 커질수록, 다음 탐색은 더 깊어진다.*
