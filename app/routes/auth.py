"""카카오 로그인 + 로컬 개발용 테스트 로그인."""
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import current_user, flash, render, require_user
from ..models import User
from ..services.metrics import track

router = APIRouter()

KAKAO_AUTH = "https://kauth.kakao.com/oauth/authorize"
KAKAO_TOKEN = "https://kauth.kakao.com/oauth/token"
KAKAO_ME = "https://kapi.kakao.com/v2/user/me"


def _safe_next(url: str | None) -> str:
    return url if url and url.startswith("/") and not url.startswith("//") else "/"


def login_user(request: Request, db: Session, provider: str, provider_id: str, nickname: str) -> User:
    user = db.scalar(select(User).where(User.provider == provider, User.provider_id == provider_id))
    is_new = user is None
    if is_new:
        user = User(provider=provider, provider_id=provider_id, nickname=nickname[:30])
        db.add(user)
        db.flush()
        track(db, "signup", user_id=user.id, provider=provider, source=request.session.get("src", ""))
    if provider_id in settings.ADMIN_IDS or f"{provider}:{provider_id}" in settings.ADMIN_IDS:
        user.is_admin = True
    track(db, "login", user_id=user.id)
    db.commit()
    request.session["uid"] = user.id
    return user


@router.get("/login")
def login_page(request: Request, next: str = "/", db: Session = Depends(get_db),
               user: User | None = Depends(current_user)):
    if user:
        return RedirectResponse(_safe_next(next), 303)
    request.session["next"] = _safe_next(next)
    return render(request, "login.html", db, None, kakao=bool(settings.KAKAO_CLIENT_ID),
                  dev=settings.dev_login_enabled)


@router.get("/auth/kakao")
def kakao_start(request: Request):
    if not settings.KAKAO_CLIENT_ID:
        raise HTTPException(500, "KAKAO_CLIENT_ID 가 설정되지 않았어요")
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    q = urlencode({"client_id": settings.KAKAO_CLIENT_ID, "response_type": "code", "state": state,
                   "redirect_uri": f"{settings.BASE_URL}/auth/kakao/callback"})
    return RedirectResponse(f"{KAKAO_AUTH}?{q}", 303)


@router.get("/auth/kakao/callback")
def kakao_callback(request: Request, code: str = "", state: str = "", db: Session = Depends(get_db)):
    if not code or state != request.session.pop("oauth_state", None):
        flash(request, "로그인에 실패했어요. 다시 시도해 주세요.")
        return RedirectResponse("/login", 303)
    data = {"grant_type": "authorization_code", "client_id": settings.KAKAO_CLIENT_ID, "code": code,
            "redirect_uri": f"{settings.BASE_URL}/auth/kakao/callback"}
    if settings.KAKAO_CLIENT_SECRET:
        data["client_secret"] = settings.KAKAO_CLIENT_SECRET
    with httpx.Client(timeout=10) as c:
        tok = c.post(KAKAO_TOKEN, data=data)
        if tok.status_code != 200:
            flash(request, "카카오 로그인에 실패했어요.")
            return RedirectResponse("/login", 303)
        me = c.get(KAKAO_ME, headers={"Authorization": f"Bearer {tok.json()['access_token']}"}).json()
    nickname = (me.get("kakao_account", {}).get("profile", {}) or {}).get("nickname", "") \
        or me.get("properties", {}).get("nickname", "")
    user = login_user(request, db, "kakao", str(me["id"]), nickname)
    nxt = request.session.pop("next", "/")
    return RedirectResponse("/onboarding" if not user.onboarded else nxt, 303)


@router.post("/auth/dev")
def dev_login(request: Request, nickname: str = Form(...), db: Session = Depends(get_db)):
    if not settings.dev_login_enabled:
        raise HTTPException(404)
    user = login_user(request, db, "dev", nickname.strip(), nickname.strip())
    nxt = request.session.pop("next", "/")
    return RedirectResponse("/onboarding" if not user.onboarded else nxt, 303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", 303)


@router.get("/onboarding")
def onboarding(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    return render(request, "onboarding.html", db, user)


@router.post("/onboarding")
def onboarding_save(request: Request, nickname: str = Form(...), openchat_url: str = Form(""),
                    kakao_talk_id: str = Form(""), db: Session = Depends(get_db),
                    user: User = Depends(require_user)):
    openchat_url = openchat_url.strip()
    err = None
    if not nickname.strip():
        err = "닉네임을 입력해 주세요"
    elif openchat_url and not openchat_url.startswith("https://open.kakao.com/"):
        err = "오픈채팅 링크는 https://open.kakao.com/ 으로 시작해야 해요"
    elif not openchat_url and not kakao_talk_id.strip():
        err = "오픈채팅 링크나 카카오톡 ID 중 하나는 꼭 입력해 주세요"
    if err:
        return render(request, "onboarding.html", db, user, error=err, status_code=400)
    was = user.onboarded
    user.nickname = nickname.strip()[:30]
    user.openchat_url = openchat_url[:300]
    user.kakao_talk_id = kakao_talk_id.strip()[:50]
    if not was:
        track(db, "onboarded", user_id=user.id)
    db.commit()
    flash(request, "프로필을 저장했어요")
    return RedirectResponse(request.session.pop("next", "/") if not was else "/me", 303)
