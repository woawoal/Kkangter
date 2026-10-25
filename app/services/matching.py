"""알림 매칭 — 오픈톡방으로는 불가능한 핵심 차별점.

1) 관심 알림: 새 글의 인형 ∈ 사용자의 Watch(캐릭터/시리즈) → Notification
2) 맞교환 매칭: A가 'X 줄게 Y 원해', B가 'Y 줄게 X 원해' → 서로에게 알림
"""
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import Character, Notification, Post, PostItem, Watch
from . import push
from .metrics import track


def _offered_ids(post: Post) -> set[int]:
    # 양도·교환 글은 '가진 것'이 매칭 대상, 구해요 글은 '원하는 것'이 매칭 대상
    items = post.want_items if post.type == "want" else post.have_items
    return {i.character_id for i in items}


def match_new_post(db: Session, post: Post) -> list[Notification]:
    char_ids = _offered_ids(post)
    if not char_ids:
        return []
    series_ids = {
        sid for (sid,) in db.execute(select(Character.series_id).where(Character.id.in_(char_ids))).all()
    }
    watches = db.scalars(
        select(Watch).where(
            Watch.is_active.is_(True),
            Watch.user_id != post.user_id,
            or_(Watch.character_id.in_(char_ids), Watch.series_id.in_(series_ids)),
        )
    ).all()

    created: list[Notification] = []
    seen_users: set[int] = set()
    first_name = post.title
    for w in watches:
        if post.type not in w.post_types.split(","):
            continue
        if w.max_price and post.price and post.price > w.max_price:
            continue
        if w.user_id in seen_users:
            continue
        seen_users.add(w.user_id)
        msg = f"관심 인형 [{first_name}] {post.type_label} 글이 올라왔어요"
        n = Notification(user_id=w.user_id, post_id=post.id, watch_id=w.id, kind="watch", message=msg)
        db.add(n)
        created.append(n)
        track(db, "watch_matched", user_id=w.user_id, post_id=post.id, watch_id=w.id)

    created += _mutual_trades(db, post)
    db.flush()
    for n in created:
        n.pushed = push.send_to_user(db, n.user_id, title="새 매칭 알림", body=n.message, url=f"/n/{n.id}")
    return created


def find_mutual_trades(db: Session, post: Post, limit: int = 10) -> list[Post]:
    """이 교환글과 딱 맞는 상대 교환글 목록 (상대의 have ∩ 내 want, 상대의 want ∩ 내 have)."""
    if post.type != "trade":
        return []
    my_have = {i.character_id for i in post.have_items}
    my_want = {i.character_id for i in post.want_items}
    if not my_have or not my_want:
        return []
    they_have = select(PostItem.post_id).where(PostItem.role == "have", PostItem.character_id.in_(my_want))
    they_want = select(PostItem.post_id).where(PostItem.role == "want", PostItem.character_id.in_(my_have))
    return list(
        db.scalars(
            select(Post)
            .where(Post.id.in_(they_have), Post.id.in_(they_want), Post.type == "trade",
                   Post.status == "open", Post.user_id != post.user_id)
            .order_by(Post.bumped_at.desc())
            .limit(limit)
        ).all()
    )


def _mutual_trades(db: Session, post: Post) -> list[Notification]:
    out = []
    for other in find_mutual_trades(db, post):
        for uid, target in ((other.user_id, post), (post.user_id, other)):
            exists = db.scalar(select(Notification.id).where(
                Notification.user_id == uid, Notification.post_id == target.id, Notification.kind == "mutual_trade"))
            if exists:
                continue
            n = Notification(user_id=uid, post_id=target.id, kind="mutual_trade",
                             message=f"맞교환 상대를 찾았어요! [{target.title}]")
            db.add(n)
            out.append(n)
            track(db, "watch_matched", user_id=uid, post_id=target.id, kind="mutual_trade")
    return out
