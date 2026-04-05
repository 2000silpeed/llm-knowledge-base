# Obsidian Vault 설정 가이드

> 이 `wiki/` 폴더를 Obsidian vault로 열면 됩니다.
> `.obsidian/` 설정이 미리 포함되어 있어 별도 설정 없이 바로 사용 가능합니다.

## 열기 방법

1. Obsidian 실행 → **Open folder as vault**
2. 이 프로젝트의 `wiki/` 폴더 선택
3. 완료 — 백링크와 그래프 뷰가 즉시 동작합니다.

## 사전 설정 내용 (`.obsidian/`)

| 파일 | 설정 |
|---|---|
| `app.json` | 위키링크 모드 (`[[]]`), 첨부파일 경로 `../raw/images` |
| `graph.json` | `chunks/` 제외 필터, 폴더별 색상 구분 |

### 그래프 뷰 색상

- **파란색** — `concepts/` 개념 항목
- **초록색** — `explorations/` 탐색 기록
- **빨간색** — `conflicts/` 충돌 감지 기록
- **노란색** — `_index.md`, `_summaries.md`, `gaps.md` (관리 파일)

## 백링크 작동 방식

개념 파일은 `[[개념명]]` 형식의 위키링크를 사용합니다.

```markdown
## 관련 개념
- [[딥러닝]] — 상위 개념
- [[트랜스포머]] — 유사 개념
```

Obsidian의 **백링크 패널** (우측 사이드바)에서 해당 개념을 참조하는 모든 파일을 확인할 수 있습니다.

## 이미지 경로 주의

인제스트된 이미지는 `raw/images/`에 저장됩니다 (vault 외부).
개념 파일에서 이미지를 참조할 경우 상대경로 `../raw/images/파일명.png` 를 사용하세요.
Obsidian `app.json`의 `attachmentFolderPath`가 `../raw/images`로 설정되어 있어 자동 연결됩니다.

## 폴더 구조

```
wiki/                    ← Obsidian vault 루트
  .obsidian/             ← Obsidian 설정 (자동 관리)
  _index.md              ← 전체 개념 목록 + 관계 맵 (LLM 유지)
  _summaries.md          ← 개념별 한 줄 요약 (LLM 유지)
  gaps.md                ← 추가 조사가 필요한 항목 (LLM 누적)
  concepts/              ← 컴파일된 개념 파일 (LLM 생성)
  explorations/          ← 탐색 결과 기록 (kb query --save)
  conflicts/             ← 충돌 감지 기록 (증분 컴파일러)
  chunks/                ← 청크 중간 파일 (그래프 뷰 제외됨)
```

## 직접 편집 가이드라인

- `concepts/`, `explorations/` — **LLM 소유**. 직접 편집 최소화.
- `gaps.md` — 직접 메모 추가 가능. LLM도 누적 기록함.
- `_index.md`, `_summaries.md` — **편집 금지**. `kb compile` 실행 시 자동 재생성.
