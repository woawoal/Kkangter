"""이벤트 기록과 지표 집계.

기록하는 이벤트 (이름은 바꾸지 말 것 — 지표 연속성)
  signup, login, onboarded, post_created, post_status_changed, post_done,
  post_viewed, contact_clicked, watch_created, watch_matched,
  notification_clicked, push_subscribed, search, beta_signup, report_created
"""
from datetime import datetime, timedelta

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from ..models import BetaSignup, Event, Notification, Post, User, Watch, now


def track(db: Session, name: str, user_id: int | None = None, post_id: int | None = None, **props):
    db.add(Event(name=name, user_id=user_id, post_id=post_id, props=props or {}))


def _count(db, stmt) -> int:
    return db.scalar(stmt) or 0


def dashboard(db: Session, days: int = 14) -> dict:
    since_7 = now() - timedelta(days=7)
    kpi = {
        "가입자": _count(db, select(func.count(User.id))),
        "베타 신청": _count(db, select(func.count(BetaSignup.id))),
        "게시글": _count(db, select(func.count(Post.id))),
        "거래완료": _count(db, select(func.count(Post.id)).where(Post.status == "done")),
        "활성 알림 신청": _count(db, select(func.count(Watch.id)).where(Watch.is_active.is_(True))),
        "알림 매칭": _count(db, select(func.count(Notification.id))),
        "연락하기 클릭": _count(db, select(func.count(Event.id)).where(Event.name == "contact_clicked")),
        "WAU(7일 활성)": _count(
            db, select(func.count(distinct(Event.user_id))).where(Event.created_at >= since_7, Event.user_id.isnot(None))
        ),
    }
    # 일별 추이
    start = (now() - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    names = ["signup", "post_created", "post_done", "watch_matched", "contact_clicked"]
    rows = db.execute(
        select(func.date(Event.created_at), Event.name, func.count(Event.id))
        .where(Event.created_at >= start, Event.name.in_(names))
        .group_by(func.date(Event.created_at), Event.name)
    ).all()
    daily: dict[str, dict[str, int]] = {}
    for i in range(days):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        daily[d] = {n: 0 for n in names}
    for d, n, c in rows:
        key = d if isinstance(d, str) else d.strftime("%Y-%m-%d")
        if key in daily:
            daily[key][n] = c
    # 퍼널: 가입 → 온보딩 → 글/알림 작성 → 연락 클릭
    users_total = kpi["가입자"]
    onboarded = _count(db, select(func.count(User.id)).where(User.nickname != "", (User.openchat_url != "") | (User.kakao_talk_id != "")))
    created_something = _count(
        db, select(func.count(distinct(Event.user_id))).where(Event.name.in_(["post_created", "watch_created"]))
    )
    contacted = _count(db, select(func.count(distinct(Event.user_id))).where(Event.name == "contact_clicked"))
    funnel = [("가입", users_total), ("프로필 완료", onboarded), ("글·알림 작성", created_something), ("연락하기", contacted)]
    # 알림 → 클릭 전환
    notif_clicked = _count(db, select(func.count(Event.id)).where(Event.name == "notification_clicked"))
    kpi_rates = {
        "알림 클릭률": f"{(notif_clicked / kpi['알림 매칭'] * 100):.0f}%" if kpi["알림 매칭"] else "-",
        "거래완료율": f"{(kpi['거래완료'] / kpi['게시글'] * 100):.0f}%" if kpi["게시글"] else "-",
    }
    top_searches = db.execute(
        select(Event.props["q"].as_string(), func.count(Event.id))
        .where(Event.name == "search")
        .group_by(Event.props["q"].as_string())
        .order_by(func.count(Event.id).desc())
        .limit(10)
    ).all()
    return {"kpi": kpi, "rates": kpi_rates, "daily": daily, "names": names, "funnel": funnel,
            "top_searches": [(q, c) for q, c in top_searches if q]}
