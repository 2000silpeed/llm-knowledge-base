# LLM 기반 개인 지식 베이스 시스템

> **LLM이 위키를 쓰고 유지한다. 사람은 자료 투입과 질문만.**
>
> Inspired by [Andrej Karpathy's tweet](https://x.com/karpathy/status/2039805659525644595) on LLM-maintained wikis.

---

## 개요

웹 아티클, PDF, Office 파일, YouTube, GitHub 레포를 **자동으로 인제스트**하고, Claude LLM이 **구조화된 위키**로 컴파일·유지합니다. RAG(벡터 DB/임베딩) 없이 **마크다운 인덱스 파일**만으로 동작합니다.

```
raw/         ← 원본 자료 (불변)
  articles/  ← 웹 아티클
  papers/    ← PDF 논문
  office/    ← Excel, PPT, Word
  repos/     ← GitHub 레포
  images/    ← 추출된 이미지

wiki/        ← LLM이 생성·유지 (사람 직접 편집 최소화)
  concepts/  ← 개념 항목 (.md)
  explorations/ ← 탐색·질의 기록
  conflicts/ ← 충돌 감지 기록
  _index.md  ← 전체 개념 인덱스
  _summaries.md ← 요약 색인
  gaps.md    ← 추가 조사 필요 항목
```

---

## 주요 기능

| 기능 | 설명 |
|------|------|
| **다중 소스 인제스트** | 웹, PDF, Excel, PPT, Word, YouTube 자막, GitHub 레포 |
| **자동 청킹** | 문서 크기에 따라 단일 패스 / Map-Reduce / 계층 트리 자동 선택 |
| **증분 컴파일** | 변경된 파일만 선택적 재컴파일 (SHA-256 해시 감시) |
| **대용량 최적화** | 병렬 처리 + 체크포인트로 1,000건+ 처리 |
| **API 응답 캐싱** | 동일 입력 재요청 시 LLM API 미호출 → 비용 절감 |
| **웹 UI** | Next.js 기반 위키 브라우저 + 개념 그래프 + 검색 |
| **공유 기능** | 스탠드얼론 HTML 내보내기 (읽기 전용 공유) |
| **팀 지식베이스** | 공유 raw/ + 개인 wiki/ 분리 구조 |
| **Obsidian 연동** | wiki/ 폴더를 Obsidian Vault로 직접 사용 |

---

## 빠른 시작

### 요구사항

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) 패키지 관리자
- Anthropic API 키

### 설치

```bash
git clone https://github.com/2000silpeed/llm-knowledge-base.git
cd llm-knowledge-base

# 의존성 설치
uv sync

# API 키 설정
export ANTHROPIC_API_KEY="sk-ant-..."

# CLI 확인
uv run kb --help
```

### 첫 번째 자료 인제스트

```bash
# 웹 아티클
uv run kb ingest https://example.com/article

# PDF
uv run kb ingest paper.pdf

# YouTube
uv run kb ingest https://youtu.be/VIDEO_ID

# GitHub 레포
uv run kb ingest https://github.com/owner/repo
```

### 위키 컴파일 & 질의

```bash
# 변경된 파일만 컴파일 (기본)
uv run kb compile

# 전체 재컴파일 (대용량 + 병렬)
uv run kb compile --all --workers 8

# 질의
uv run kb query "딥러닝 트랜스포머 구조가 뭐야?"

# 현황 요약
uv run kb status
```

---

## CLI 명령어 레퍼런스

### `kb ingest`

원본 자료를 `raw/`에 수집합니다.

```bash
kb ingest <파일/URL>
```

지원 형식: `.pdf` `.xlsx` `.xls` `.pptx` `.docx` `.md` `.txt` · HTTP URL · YouTube URL · GitHub URL

---

### `kb compile`

`raw/` 파일을 LLM으로 컴파일해 `wiki/concepts/`에 항목을 생성합니다.

```bash
kb compile                        # 변경 파일만 (기본)
kb compile --all                  # 전체 재컴파일
kb compile --all --workers 8      # 병렬 8쓰레드
kb compile --all --resume         # 체크포인트에서 재시작
kb compile --file raw/papers/foo.md  # 특정 파일만
kb compile --dry-run              # 변경 감지만 (컴파일 안 함)
```

**청킹 전략** (자동 선택):

| 전략 | 조건 | 설명 |
|------|------|------|
| `single_pass` | ≤ 80% 컨텍스트 | 전체를 한 번에 LLM 전달 |
| `map_reduce` | ≤ 300% | 청크별 병렬 요약 → 통합 |
| `hierarchical` | > 300% | 2단계 계층 요약 트리 |

---

### `kb query`

wiki를 컨텍스트로 삼아 LLM에 질문합니다.

```bash
kb query "질문 내용"
kb query "질문" --save       # 답변을 wiki/explorations/에 저장
kb query "질문" --verbose    # 컨텍스트 통계 출력
```

**컨텍스트 우선순위**: `_index.md` + `_summaries.md` → 관련 개념 파일 → explorations 항목

---

### `kb status`

```bash
kb status
```

raw 인제스트 건수 / wiki 개념 항목 수 / 갭 항목 수 / 마지막 컴파일 시각을 출력합니다.

---

### `kb share`

wiki 항목을 스탠드얼론 HTML로 내보냅니다.

```bash
kb share "개념명"
kb share "개념명" --output ./exports
```

---

### `kb watch`

`raw/` 디렉토리를 감시하며 변경 시 자동 컴파일합니다.

```bash
kb watch
kb watch --workers 4
```

---

### `kb cache` (P2-08)

LLM 응답 캐시를 관리합니다. 동일 입력 재요청 시 API 미호출로 비용을 절감합니다.

```bash
kb cache              # 캐시 통계 출력
kb cache --clear      # 캐시 전체 삭제
kb cache --evict      # 만료된 항목만 삭제
```

---

### `kb team` (P2-06)

팀 지식베이스를 관리합니다. 공유 `raw/` + 개인 `wiki/` 구조를 지원합니다.

```bash
# 팀 설정 초기화 (공유 raw 경로, 내 멤버 ID)
kb team init ../shared/raw alice

# 팀원 추가
kb team add bob --wiki /path/to/bob/wiki

# 팀 현황
kb team status
```

`config/team.yaml`이 존재하면 모든 명령어가 자동으로 팀 경로를 사용합니다.

---

## 설정

### `config/settings.yaml`

```yaml
llm:
  model: claude-sonnet-4-6       # 모델 변경 시 이 줄만 수정
  context_limit: 200000
  output_reserved: 8000
  temperature: 0.3

cache:
  enabled: true                  # LLM 응답 캐싱
  ttl_days: 0                    # 0 = 영구 보존

chunking:
  single_pass_threshold: 0.80
  map_reduce_threshold: 3.00
  overlap_tokens: 200

paths:
  raw: raw
  wiki: wiki
```

모델 변경은 `llm.model` 한 줄만 바꾸면 전체 파이프라인이 자동 적응합니다.

---

## 웹 UI

Next.js 기반 위키 브라우저입니다. `wiki/` 디렉토리의 마크다운 파일을 실시간으로 렌더링합니다.

```bash
cd web
pnpm install
pnpm dev        # http://localhost:3000
```

| 페이지 | 경로 | 설명 |
|--------|------|------|
| 홈 | `/` | 개념 목록 + 최근 탐색 |
| 개념 목록 | `/concepts` | 전체 개념 카드 + 검색 |
| 개념 상세 | `/concepts/[slug]` | 마크다운 렌더링 + 백링크 |
| 탐색 기록 | `/explorations` | 질의 답변 아카이브 |
| 개념 그래프 | `/graph` | D3.js 인터랙티브 그래프 |
| 갭 목록 | `/gaps` | 추가 조사 필요 항목 |
| 공유 페이지 | `/share/[type]/[slug]` | 읽기 전용 공유 URL |

---

## 팀 지식베이스

```
shared_raw/          ← 팀 공유 (네트워크 드라이브, git 서브모듈 등)
  articles/
  papers/
  ...

alice/wiki/          ← Alice의 개인 wiki
bob/wiki/            ← Bob의 개인 wiki
```

```yaml
# config/team.yaml
shared_raw: ../shared/raw
member: alice
members:
  - id: alice
    wiki: wiki/alice
  - id: bob
    wiki: /absolute/path/bob/wiki
```

팀원마다 동일한 소스를 다른 관점으로 컴파일할 수 있습니다.

---

## 설계 원칙

1. **raw/ 불변** — 원본 자료는 항상 원형 보존, 절대 수정하지 않음
2. **wiki/ LLM 소유** — 위키 파일은 LLM이 쓰고 유지, 사람 직접 편집 최소화
3. **모델 독립** — `settings.yaml` 한 줄 변경으로 모든 Claude 모델 지원
4. **복리 우선** — 모든 탐색 결과는 wiki/로 환원되어 다음 질문의 컨텍스트가 됨
5. **RAG 없음** — 벡터 DB/임베딩 사용 금지, 인덱스 파일로 해결

---

## 아키텍처

```
┌─────────────────────────────────────────────────────────┐
│  소스 자료                                               │
│  웹 · PDF · Excel · PPT · Word · YouTube · GitHub        │
└────────────────────────┬────────────────────────────────┘
                         │ kb ingest
                         ▼
┌─────────────────────────────────────────────────────────┐
│  raw/  (불변 원본 마크다운)                              │
└────────────────────────┬────────────────────────────────┘
                         │ kb compile
                         │ [단일패스 / Map-Reduce / 계층트리]
                         │ [증분 · 병렬 · 캐싱]
                         ▼
┌─────────────────────────────────────────────────────────┐
│  wiki/  (LLM 생성·유지)                                 │
│  concepts/ · explorations/ · _index.md · _summaries.md  │
└──────────┬────────────────────────┬────────────────────┘
           │ kb query               │ Next.js Web UI
           ▼                        ▼
      LLM 답변               브라우저 / Obsidian
```

---

## 라이선스

MIT

---

## 참고

- Karpathy tweet: [2039805659525644595](https://x.com/karpathy/status/2039805659525644595)
- Claude API: [anthropic.com](https://www.anthropic.com)
