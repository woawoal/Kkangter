from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import BASE_DIR, settings
from .db import get_db
from .models import Notification, User, now

templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


def _won(v):
    return "" if v is None else f"{v:,}원"


def _ago(dt):
    if not dt:
        return ""
    s = (now() - dt).total_seconds()
    if s < 60:
        return "방금"
    if s < 3600:
        return f"{int(s // 60)}분 전"
    if s < 86400:
        return f"{int(s // 3600)}시간 전"
    if s < 86400 * 7:
        return f"{int(s // 86400)}일 전"
    return (dt + timedelta(hours=9)).strftime("%m.%d")


templates.env.filters["won"] = _won
templates.env.filters["ago"] = _ago
templates.env.globals["settings"] = settings


class LoginRequired(Exception):
    def __init__(self, next_url: str = "/"):
        self.next_url = next_url


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    uid = request.session.get("uid")
    if not uid:
        return None
    user = db.get(User, uid)
    if not user or user.is_banned:
        request.session.clear()
        return None
    if (now() - user.last_seen_at).total_seconds() > 600:
        user.last_seen_at = now()
        db.commit()
    return user


def require_user(request: Request, user: User | None = Depends(current_user)) -> User:
    if not user:
        raise LoginRequired(str(request.url.path))
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "관리자만 접근할 수 있어요")
    return user


def render(request: Request, name: str, db: Session, user: User | None, status_code: int = 200, **ctx):
    unread = 0
    if user:
        unread = db.scalar(
            select(func.count(Notification.id)).where(Notification.user_id == user.id, Notification.read_at.is_(None))
        ) or 0
    flash = request.session.pop("flash", None)
    return templates.TemplateResponse(
        request, name, {"user": user, "unread": unread, "flash": flash, **ctx},
        status_code=status_code,
    )


def flash(request: Request, msg: str):
    request.session["flash"] = msg
