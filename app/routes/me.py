from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import settings
from ..db import get_db
from ..deps import flash, render, require_user
from ..models import Character, Notification, Post, PushSubscription, Series, User, Watch, now
from ..services import catalog, push
from ..services.metrics import track

router = APIRouter()


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    posts = db.scalars(select(Post).where(Post.user_id == user.id, Post.status != "hidden")
                       .options(selectinload(Post.photos), selectinload(Post.items))
                       .order_by(Post.created_at.desc())).all()
    return render(request, "me.html", db, user, posts=posts, push_key=settings.VAPID_PUBLIC_KEY)


# ---------- 관심 알림 ----------

@router.get("/watches")
def watches(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    items = db.scalars(select(Watch).where(Watch.user_id == user.id, Watch.is_active.is_(True))
                       .order_by(Watch.created_at.desc())).all()
    return render(request, "watches.html", db, user, watches=items, series_list=catalog.all_series(db),
                  push_key=settings.VAPID_PUBLIC_KEY)


@router.post("/watch")
def add_watch(request: Request, character_id: str = Form(""), series_id: str = Form(""),
              post_types: list[str] = Form(["sell", "trade"]), max_price: str = Form(""),
              next: str = Form("/watches"), db: Session = Depends(get_db), user: User = Depends(require_user)):
    character_id = int(character_id) if character_id.isdigit() else None
    series_id = int(series_id) if series_id.isdigit() else None
    if not character_id and not series_id:
        raise HTTPException(400)
    if character_id and not db.get(Character, character_id) or series_id and not db.get(Series, series_id):
        raise HTTPException(404)
    types = ",".join(t for t in post_types if t in ("sell", "trade", "want")) or "sell,trade"
    q = select(Watch).where(Watch.user_id == user.id, Watch.is_active.is_(True))
    q = q.where(Watch.character_id == character_id) if character_id else q.where(Watch.series_id == series_id,
                                                                                 Watch.character_id.is_(None))
    w = db.scalar(q)
    if not w:
        w = Watch(user_id=user.id, character_id=character_id, series_id=None if character_id else series_id)
        db.add(w)
        db.flush()
        track(db, "watch_created", user_id=user.id, watch_id=w.id, character_id=character_id, series_id=series_id)
    w.post_types = types
    mp = max_price.replace(",", "").strip()
    w.max_price = int(mp) if mp.isdigit() else None
    db.commit()
    flash(request, "알림을 신청했어요. 올라오면 바로 알려드릴게요 🔔")
    return RedirectResponse(next if next.startswith("/") else "/watches", 303)


@router.post("/watch/{watch_id}/delete")
def del_watch(watch_id: int, request: Request, next: str = Form("/watches"), db: Session = Depends(get_db),
              user: User = Depends(require_user)):
    w = db.get(Watch, watch_id)
    if not w or w.user_id != user.id:
        raise HTTPException(404)
    w.is_active = False
    db.commit()
    flash(request, "알림을 해제했어요")
    return RedirectResponse(next if next.startswith("/") else "/watches", 303)


# ---------- 알림함 ----------

@router.get("/notifications")
def notifications(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    items = db.scalars(select(Notification).where(Notification.user_id == user.id)
                       .options(selectinload(Notification.post).selectinload(Post.photos))
                       .order_by(Notification.created_at.desc()).limit(100)).all()
    return render(request, "notifications.html", db, user, items=items)


@router.post("/notifications/read-all")
def read_all(db: Session = Depends(get_db), user: User = Depends(require_user)):
    for n in db.scalars(select(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))):
        n.read_at = now()
    db.commit()
    return RedirectResponse("/notifications", 303)


@router.get("/n/{nid}")
def open_notification(nid: int, src: str = "app", db: Session = Depends(get_db), user: User = Depends(require_user)):
    n = db.get(Notification, nid)
    if not n or n.user_id != user.id:
        raise HTTPException(404)
    if not n.read_at:
        n.read_at = now()
        track(db, "notification_clicked", user_id=user.id, post_id=n.post_id, kind=n.kind, src=src)
        db.commit()
    return RedirectResponse(f"/p/{n.post_id}", 303)


# ---------- 웹 푸시 구독 ----------

@router.post("/push/subscribe")
async def push_subscribe(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    body = await request.json()
    try:
        endpoint, keys = body["endpoint"], body["keys"]
    except (KeyError, TypeError):
        raise HTTPException(400)
    sub = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
    if not sub:
        db.add(PushSubscription(user_id=user.id, endpoint=endpoint, p256dh=keys["p256dh"], auth=keys["auth"]))
        track(db, "push_subscribed", user_id=user.id)
    else:
        sub.user_id = user.id
    db.commit()
    return JSONResponse({"ok": True, "enabled": push.enabled()})
