"""웹 푸시 (PWA). 키가 없으면 조용히 건너뛰고 앱 내 알림함만 사용한다.
iOS는 16.4+ 에서 '홈 화면에 추가'한 경우에만 푸시를 받을 수 있다 → 앱 내 알림 배지가 기본."""
import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import PushSubscription

log = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY)


def send_to_user(db: Session, user_id: int, title: str, body: str, url: str) -> bool:
    if not enabled():
        return False
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:  # pragma: no cover
        return False
    subs = db.scalars(select(PushSubscription).where(PushSubscription.user_id == user_id)).all()
    ok = False
    for s in subs:
        try:
            webpush(
                subscription_info={"endpoint": s.endpoint, "keys": {"p256dh": s.p256dh, "auth": s.auth}},
                data=json.dumps({"title": title, "body": body, "url": url}),
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims={"sub": settings.VAPID_SUBJECT},
                ttl=60 * 60 * 24,
            )
            ok = True
        except WebPushException as e:  # 만료된 구독은 정리
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                db.delete(s)
            log.warning("push failed: %s", e)
    return ok
