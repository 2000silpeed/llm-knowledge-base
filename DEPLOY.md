# 배포 가이드

---

## 1. 로컬 실행

```bash
# 1. 환경변수 설정
cp .env.example .env
# .env 파일에 ANTHROPIC_API_KEY 등 값 입력

# 2. 한 번에 시작 (웹 UI + CLI 환경)
./start.sh

# 웹 UI만 시작
./start.sh --web

# 종료
./stop.sh
```

또는 Makefile 사용:

```bash
make install   # 최초 1회 의존성 설치
make start     # 시작
make stop      # 종료
make status    # 현황
make compile   # 변경 파일 컴파일
```

웹 UI: http://localhost:3000

---

## 2. 리눅스 서버 상주 (systemd)

웹 UI와 파일 감시를 서버 부팅 시 자동 시작하도록 등록합니다.

### 사전 준비

```bash
# 의존성 설치
uv sync
cd web && pnpm install && pnpm build && cd ..
```

### 웹 UI 서비스 등록

```bash
# 1. 유닛 파일 편집 (User, WorkingDirectory 경로 수정)
nano systemd/kb-web.service

# 2. 설치
sudo cp systemd/kb-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable kb-web
sudo systemctl start kb-web

# 상태 확인
sudo systemctl status kb-web
journalctl -u kb-web -f
```

### 자동 컴파일 서비스 등록 (선택)

raw/ 에 파일이 추가되면 자동으로 wiki/ 를 업데이트합니다.

```bash
nano systemd/kb-watch.service   # 경로 수정

sudo cp systemd/kb-watch.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable kb-watch
sudo systemctl start kb-watch
```

---

## 3. Vercel (웹 UI 배포) — 가이드

> Python CLI는 Vercel에서 실행되지 않습니다. 웹 UI 정적 서빙 전용입니다.
> wiki/ 디렉토리를 git에 포함하거나 별도 스토리지에서 마운트해야 합니다.

### 배포 방법

1. [vercel.com](https://vercel.com) 가입 후 GitHub 레포 연결
2. 설정:
   - **Framework Preset**: Next.js (자동 감지)
   - **Root Directory**: `web`
   - **Build Command**: `pnpm build`
   - **Output Directory**: `.next`
3. 환경변수 추가 (Vercel 대시보드 → Settings → Environment Variables):
   ```
   KB_WIKI_DIR = /vercel/path/to/wiki   # 또는 별도 스토리지 경로
   ```
4. Deploy 클릭

### 주의사항

- Vercel은 서버리스(함수 실행 시간 제한)이므로 LLM 컴파일 같은 장시간 작업은 불가
- wiki/ 파일은 별도 서버에서 컴파일 후 git push → Vercel 자동 재배포 방식 권장
- 읽기 전용 위키 뷰어 용도로 적합

---

## 4. Railway (풀스택 배포) — 가이드

Python CLI + 웹 UI를 하나의 서버에서 실행합니다. Docker 없이 git push만으로 배포됩니다.

### 배포 방법

1. [railway.app](https://railway.app) 가입 후 **New Project → Deploy from GitHub repo** 선택
2. 레포 선택 → Railway가 Python + Node.js 자동 감지 (Nixpacks)
3. 환경변수 추가 (Railway 대시보드 → Variables):
   ```
   ANTHROPIC_API_KEY = sk-ant-...
   PORT = 3000
   ```
4. 서비스 2개 설정:
   - **web**: Root Directory = `web`, Start Command = `pnpm start`
   - **worker** (선택): Start Command = `kb watch`
5. Volume 마운트: `/app/wiki`, `/app/raw` → 퍼시스턴트 스토리지 연결

### railway.toml (선택)

프로젝트 루트에 `railway.toml` 추가 시 배포 설정 자동 적용:

```toml
[build]
builder = "NIXPACKS"

[deploy]
startCommand = "cd web && pnpm start"
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 3
```

### 주의사항

- 무료 티어: 월 500시간 (24시간 상시 운영 시 유료 필요)
- raw/, wiki/ 는 Railway Volume에 마운트해야 재배포 시 데이터 유지
- LLM API 키는 반드시 환경변수로 관리 (코드에 하드코딩 금지)

---

## 환경변수 목록

| 변수명 | 설명 | 기본값 |
|--------|------|--------|
| `ANTHROPIC_API_KEY` | Anthropic API 키 | 필수 |
| `Z_AI_API_KEY` | Z.ai API 키 | 선택 |
| `KB_WIKI_DIR` | wiki 디렉토리 절대 경로 | `./wiki` |
| `PORT` | 웹 UI 포트 | `3000` |
