"""환경변수 기반 설정. 로컬은 .env 없이도 바로 실행되도록 기본값을 둔다."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


class Settings:
    APP_NAME = _env("APP_NAME", "깡터")
    ENV = _env("ENV", "dev")  # dev | prod
    SECRET_KEY = _env("SECRET_KEY", "dev-secret-change-me")
    BASE_URL = _env("BASE_URL", "http://localhost:8000")

    # DB: 로컬은 SQLite, 운영은 Postgres(Supabase 등) URL
    DATABASE_URL = _env("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'app.db'}")

    # 이미지 저장소: local | s3 (Cloudflare R2 / Supabase Storage 모두 S3 호환)
    STORAGE_BACKEND = _env("STORAGE_BACKEND", "local")
    UPLOAD_DIR = Path(_env("UPLOAD_DIR", str(BASE_DIR / "data" / "uploads")))
    S3_ENDPOINT = _env("S3_ENDPOINT")
    S3_BUCKET = _env("S3_BUCKET")
    S3_ACCESS_KEY = _env("S3_ACCESS_KEY")
    S3_SECRET_KEY = _env("S3_SECRET_KEY")
    S3_PUBLIC_BASE = _env("S3_PUBLIC_BASE")  # 공개 이미지 URL prefix

    # 카카오 로그인 (developers.kakao.com > REST API 키)
    KAKAO_CLIENT_ID = _env("KAKAO_CLIENT_ID")
    KAKAO_CLIENT_SECRET = _env("KAKAO_CLIENT_SECRET")

    # 웹 푸시 (VAPID). `python -m app.cli vapid` 로 생성
    VAPID_PUBLIC_KEY = _env("VAPID_PUBLIC_KEY")
    VAPID_PRIVATE_KEY = _env("VAPID_PRIVATE_KEY")
    VAPID_SUBJECT = _env("VAPID_SUBJECT", "mailto:admin@example.com")

    # 관리자: 카카오 user id 또는 dev 닉네임, 콤마 구분
    ADMIN_IDS = {x.strip() for x in _env("ADMIN_IDS", "").split(",") if x.strip()}

    # 출시 전에는 홈(/)을 베타 사전신청 페이지로 보낸다
    PRELAUNCH = _env("PRELAUNCH", "0") == "1"

    MAX_PHOTOS = 5
    MAX_UPLOAD_BYTES = 8 * 1024 * 1024

    # 사진 자동 인식 (비전 AI). 키가 없으면 버튼이 숨겨진다.
    VISION_PROVIDER = _env("VISION_PROVIDER", "anthropic")  # anthropic | mock
    ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
    # 작은 중국어 이름표를 읽어야 해서 기본은 Sonnet. 비용을 줄이려면 claude-haiku-5-5 로 비교해 볼 것
    VISION_MODEL = _env("VISION_MODEL", "claude-sonnet-5-5")
    VISION_DAILY_LIMIT = int(_env("VISION_DAILY_LIMIT", "30"))  # 사용자당 하루 호출 수

    @property
    def vision_enabled(self) -> bool:
        return self.VISION_PROVIDER == "mock" or bool(self.ANTHROPIC_API_KEY)

    @property
    def dev_login_enabled(self) -> bool:
        # 카카오 키가 없는 로컬 개발 환경에서만 테스트 로그인 허용
        return self.ENV != "prod"


settings = Settings()
