# Kkangter (깡터) — 랜덤깡 인형 양도·교환 MVP

오픈톡방에서 묻히던 인형 양도·교환글을 **검색**하고, 원하는 인형이 올라오면 **알림**받는 모바일 웹 서비스.

- 기획서: [`docs/PLAN.md`](docs/PLAN.md) (화면·데이터 구조·스택·일정·법적 체크)
- 출시 메시지: [`docs/launch-messages.md`](docs/launch-messages.md)

## 기능
- 게시글: 사진(최대 5장, 자동 리사이즈·EXIF 제거) + 시리즈/캐릭터 다중 선택 + 양도/교환/구해요 + 인형별 가격
- 시리즈·캐릭터 검색 (별칭·공백 무시), 구분/시리즈 필터
- 🔔 관심 알림: 캐릭터 또는 시리즈 단위, 글 종류·최대가격 조건 → 앱 내 알림함 + 웹 푸시
- 🔁 맞교환 자동 매칭: 서로 원하는 인형을 가진 교환글끼리 연결
- 연락: 1:1 오픈채팅 링크 이동 / 카톡 ID 복사 (클릭 기록)
- 글 상태(거래중·예약중·거래완료), 무료 끌어올리기(24시간 1회 — 유료화 자리), 신고
- 베타 사전신청 페이지 (`PRELAUNCH=1` 이면 홈이 베타 페이지로 이동)
- 관리자: 지표 대시보드·퍼널·인기 검색어, 카탈로그 일괄 추가, 신고 처리, 베타 신청 CSV
- 모든 핵심 행동을 `events` 테이블에 기록 → 포트폴리오 수치

## 로컬 실행
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m app.cli demo                 # (선택) 데모 데이터
ADMIN_IDS=말랑이 uvicorn app.main:app --reload
# http://localhost:8000 → 로그인 화면의 '개발용 테스트 로그인' 사용
pytest -q                              # 테스트 11개
```

## 배포 (Cloud Run + Supabase + R2, 월 0원 목표)
1. **Supabase**: 새 프로젝트(리전 Seoul) → Database 연결 문자열(Transaction pooler) 복사. 드라이버는 `postgresql+psycopg://` 로 시작하게 바꾼다.
2. **Cloudflare R2**: 버킷 생성 → Public access(또는 커스텀 도메인) 켜기 → API 토큰 발급.
3. **카카오**: developers.kakao.com 앱 생성 → 카카오 로그인 활성화 → Redirect URI `https://도메인/auth/kakao/callback` 등록 → 동의항목 '닉네임'.
4. **VAPID 키**: `python -m app.cli vapid`
5. **배포**
   ```bash
   gcloud run deploy kkangter --source . --region asia-northeast3 --allow-unauthenticated \
     --min-instances 0 --max-instances 2 --memory 512Mi \
     --set-env-vars "ENV=prod,BASE_URL=https://도메인,PRELAUNCH=1,STORAGE_BACKEND=s3,..." \
     --set-secrets "SECRET_KEY=kkangter-secret:latest,DATABASE_URL=kkangter-db:latest,..."
   ```
   환경변수 전체 목록은 `.env.example`.
6. 도메인 연결 → 처음 로그인한 뒤 `ADMIN_IDS` 에 내 카카오 회원번호를 넣고 재배포.
7. 출시일에 `PRELAUNCH=0`.

## 구조
```
app/
  main.py            앱 생성, 라우터, 에러 처리
  models.py          데이터 모델 (docs/PLAN.md 3장)
  routes/            auth · posts · me(알림/알림함/푸시) · admin · public(베타/약관/PWA)
  services/          matching(알림 매칭) · metrics(지표) · catalog · storage · push
  templates/         Jinja2 (모바일 우선)
  static/            app.css, new-post.js(글쓰기), sw.js(푸시), manifest
seed/catalog.json    샘플 카탈로그 → 실제 인기 시리즈로 교체할 것
tests/               핵심 흐름 테스트
```

## 알려진 한계 / TODO
- 테이블은 `create_all` 로 생성 → 스키마가 바뀌기 시작하면 Alembic 도입
- CSRF는 SameSite=Lax 쿠키에 의존 (MVP 수준)
- 회원 탈퇴, 글 수정 기능 없음 → 베타 피드백을 보고 추가
- 유료 끌어올리기: `/p/{id}/bump` 에 결제 확인 붙이기 (토스페이먼츠 결제위젯)
