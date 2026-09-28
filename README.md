# KRX 일별 시세 수집 파이프라인

KRX Open API로 코스피·코스닥 전 종목(우선주·스팩 제외, 리츠 포함)의 일별 시세를 받아
Turso DB에 **최근 200거래일**만 유지하며 쌓습니다.

| 파일 | 역할 |
|---|---|
| `init_load.py` | 최초 1회. 어제부터 거꾸로 200거래일 적재 (끊기면 다시 실행 → 이어받기) |
| `daily_update.py` | 매 평일 08:05. 빠진 거래일 채우기 + 200거래일 초과분 삭제 + 미갱신 시 08:35까지 재시도 |
| `status/latest.json` | 마지막 실행 결과 (워크플로가 자동 커밋) |

저장 항목: 기준일자, 종목코드, 종목명, 시장, 소속부, 시가, 고가, 저가, 종가, 전일대비, 등락률, 거래량, 거래대금, 시가총액, 상장주식수

---

## 설치 순서

### 1. Turso DB 만들기
turso.tech 대시보드에서 DB 생성 후 두 값을 복사합니다.
- **Database URL**: `libsql://이름-조직.turso.io`
- **Auth Token**: DB 화면 > Create Token (Read & Write, 만료 없음 권장)

테이블은 첫 실행 때 자동 생성됩니다.

### 2. GitHub 저장소 만들기
새 공개(Public) 저장소를 만들고 이 폴더의 파일을 모두 올립니다.
(`.github/workflows` 폴더가 꼭 포함돼야 합니다)

### 3. Secrets 등록
저장소 **Settings > Secrets and variables > Actions > New repository secret**

| 이름 | 값 |
|---|---|
| `KRX_AUTH_KEY` | KRX Open API 인증키 |
| `TURSO_DATABASE_URL` | 1번의 Database URL |
| `TURSO_AUTH_TOKEN` | 1번의 Auth Token |

> 공개 저장소여도 Secrets 값은 외부에 보이지 않고 실행 로그에도 `***`로 가려집니다.
> 인증키를 코드나 `.env` 파일로 커밋하지 마세요.

### 4. 초기 적재 실행
**Actions 탭 > init-load > Run workflow**
- 기본값(200거래일, 호출 600회)이면 한 번에 끝납니다. 약 400회 호출, 10~20분 소요.
- 나눠 받고 싶으면 `max_calls`를 150 정도로 두고 며칠에 걸쳐 여러 번 실행하세요.
- 결과는 Actions 로그 마지막 줄과 `status/latest.json`에서 확인. `partial`이면 다시 실행.

### 5. cron-job.org 설정 (매일 08:05 실행)

**5-1. GitHub 토큰 발급**
GitHub 우측 상단 프로필 > Settings > Developer settings > Personal access tokens >
**Fine-grained tokens > Generate new token**
- Repository access: **Only select repositories** → 이 저장소만
- Permissions > Repository permissions > **Actions: Read and write**
- 만료일 설정 후 생성, 토큰 복사 (만료되면 재발급 후 cron-job.org 헤더만 교체)

**5-2. cron-job.org 작업 생성** (Create cronjob)
- URL: `https://api.github.com/repos/<GitHub아이디>/<저장소명>/actions/workflows/daily_update.yml/dispatches`
- Schedule: Custom → 월~금, 08:05 / **Time zone: Asia/Seoul**
- Advanced 탭
  - Request method: **POST**
  - Headers:
    - `Authorization`: `Bearer <5-1 토큰>`
    - `Accept`: `application/vnd.github+json`
    - `X-GitHub-Api-Version`: `2022-11-28`
    - `Content-Type`: `application/json`
  - Request body: `{"ref":"main"}`  (기본 브랜치가 master면 master)
- 저장 후 **Test run** → 응답 코드 `204`면 성공 (Actions 탭에 실행이 생김)

**백업 실행**: `daily_update.yml`에 GitHub 자체 스케줄(KST 08:40)도 걸려 있습니다.
cron-job.org가 실패해도 이것이 대신 실행하고, 이미 최신이면 몇 초 만에 종료됩니다.

---

## 실행 결과 확인 방법

**① status 파일 (가장 간단)**
`https://raw.githubusercontent.com/<아이디>/<저장소명>/main/status/latest.json`

| status | 의미 |
|---|---|
| `success` | 정상 (또는 휴장일이라 받을 게 없음 / 이미 최신) |
| `no_data` | 08:35까지 KRX 데이터가 안 올라옴 (미갱신 또는 달력에 없는 임시휴장) |
| `failed` | 오류 (message 확인. 인증 오류면 키/서비스 승인 확인) |
| `partial` | 초기 적재 진행 중 (다시 실행하면 이어받음) |

판단 기준: `latest_date`가 **직전 거래일**이고 `stored_days`가 200이면 정상입니다.

**② GitHub 알림**: 실행이 실패(빨간 X)하면 GitHub가 저장소 주인에게 메일을 보냅니다.

**③ DB에서 직접**: `SELECT * FROM v_data_status;`

---

## 자주 쓸 쿼리 (Turso 대시보드 SQL 콘솔)

```sql
-- 최신 거래일 시가총액 상위 20
SELECT isu_cd, isu_nm, market, close_prc, fluc_rt, mktcap_rank
FROM v_latest ORDER BY mktcap_rank LIMIT 20;

-- 특정 종목 200일 시세
SELECT bas_dd, open_prc, high_prc, low_prc, close_prc, volume
FROM daily_price WHERE isu_cd = '005930' ORDER BY bas_dd;

-- 상장주식수 변동 (ratio 1.5 이상이면 액면분할 등으로 가격이 끊겼을 가능성)
SELECT * FROM v_share_changes WHERE ratio >= 1.5 OR ratio <= 0.67 ORDER BY bas_dd DESC;

-- 최근 실행 이력
SELECT * FROM pipeline_runs ORDER BY id DESC LIMIT 10;
```

## DB 구조
- `daily_price` : 일별 시세 (기본키: 기준일자+종목코드)
- `fetch_log` : 날짜·시장별 수집 기록 (ok / empty / error)
- `pipeline_runs` : 실행 이력
- 뷰 `v_latest`, `v_share_changes`, `v_data_status`

## 참고
- 가격은 **수정주가가 아닌 원 주가**입니다. 분할 등은 `v_share_changes`로 확인하세요.
- 우선주는 종목코드 끝자리(0이 아니면 우선주)로, 스팩은 종목명·소속부로 걸러냅니다.
- 휴장일 판단은 KRX 응답(데이터 없음)이 최종 기준이고, 공휴일 달력은 재시도 여부 판단에만 씁니다.
