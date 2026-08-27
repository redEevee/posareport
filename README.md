# 커머스 광고 리포트 MVP

카페24 주문 데이터를 기준 매출로 삼고, 광고 매체의 지출 데이터를 합쳐 ROAS를 보여주는 초기 버전입니다.

## 지금 포함된 기능

- 일자·매체별 광고비, 주문 수, 매출, ROAS API
- 브라우저 이벤트 수집 엔드포인트 (`POST /api/events`)
- 카페24 API 및 광고 API를 붙일 수 있는 커넥터 구조
- 실제 연결 전 확인할 수 있는 샘플 데이터와 대시보드

## 실행

```bash
python3 app/server.py
```

브라우저에서 `http://localhost:8787`을 엽니다.

## 실제 연동 순서

1. 카페24 개발자센터에서 `ntonmaster` 쇼핑몰용 앱을 만들고 주문 조회 권한을 부여합니다.
2. 앱 기본정보의 **Redirect URI**에 배포 도메인의 `https://<내-도메인>/auth/cafe24/callback`을 등록합니다. HTTPS와 도메인명이 필수이며, 코드의 `CAFE24_REDIRECT_URI`와 한 글자까지 같아야 합니다.
3. `.env.example`을 `.env`로 복사한 후 카페24 앱 정보와 광고 매체 자격 증명을 입력합니다.
4. 배포 후 `https://<내-도메인>/auth/cafe24/connect`로 접속해 카페24 인증을 완료합니다.
5. 광고 매체별 API 커넥터를 구현한 뒤 일일 동기화 작업을 배포합니다.

`CAFE24_ACCESS_TOKEN`, 광고 API 비밀키는 브라우저 스크립트나 Git 저장소에 넣으면 안 됩니다.

## 도메인 없이 배포하기

Render에서 이 프로젝트를 Web Service로 배포하면 `https://<서비스명>.onrender.com` 형태의 HTTPS 주소가 자동으로 생깁니다. 배포 주소가 확정되면 Render 환경변수 `CAFE24_REDIRECT_URI`와 카페24 개발자센터의 Redirect URI를 아래처럼 같은 값으로 설정합니다.

```
https://<서비스명>.onrender.com/auth/cafe24/callback
```

Render에는 `CAFE24_CLIENT_ID`와 `CAFE24_CLIENT_SECRET`을 환경변수로 입력합니다. `.env` 파일은 올리지 않습니다.

## posareport.store 연결값

리포트 플랫폼은 `report.posareport.store`를 사용하도록 준비되어 있습니다.

- 카페24 앱의 Redirect URI: `https://report.posareport.store/auth/cafe24/callback`
- 카페24 쇼핑몰 테마에 넣을 이벤트 수집 스크립트: `<script src="https://report.posareport.store/tracker.js" defer></script>`

Render 배포가 만든 도메인에 `report` 서브도메인을 연결한 뒤에만 위 주소가 동작합니다. Render가 안내하는 CNAME 값을 카페24 DNS 관리에서 `report` 레코드로 등록하세요.
