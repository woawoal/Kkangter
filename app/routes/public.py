"""베타 사전신청, 약관, PWA 파일."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import BASE_DIR
from ..db import get_db
from ..deps import current_user, render
from ..models import BetaSignup, User
from ..services import catalog
from ..services.metrics import track

router = APIRouter()
STATIC = BASE_DIR / "app" / "static"


@router.get("/beta")
def beta(request: Request, src: str = "", done: int = 0, db: Session = Depends(get_db),
         user: User | None = Depends(current_user)):
    if src:
        request.session["src"] = src[:50]
    count = db.scalar(select(func.count(BetaSignup.id))) or 0
    return render(request, "beta.html", db, user, count=count, done=done, series_list=catalog.all_series(db))


@router.post("/beta")
def beta_submit(request: Request, nickname: str = Form(...), contact: str = Form(...),
                interests: list[str] = Form([]), interests_etc: str = Form(""), roles: list[str] = Form([]),
                agree: str = Form(""), db: Session = Depends(get_db)):
    if not nickname.strip() or not contact.strip() or not agree:
        count = db.scalar(select(func.count(BetaSignup.id))) or 0
        return render(request, "beta.html", db, None, count=count, done=0, series_list=catalog.all_series(db),
                      error="닉네임, 연락처, 개인정보 수집 동의는 필수예요", status_code=400)
    ints = [i for i in interests if i] + ([interests_etc.strip()] if interests_etc.strip() else [])
    b = BetaSignup(nickname=nickname.strip()[:30], contact=contact.strip()[:120], interests=", ".join(ints)[:300],
                   roles=",".join(roles)[:50], source=request.session.get("src", "")[:50])
    db.add(b)
    db.flush()
    track(db, "beta_signup", props_id=b.id, source=b.source)
    db.commit()
    return RedirectResponse("/beta?done=1", 303)


@router.get("/terms")
def terms(request: Request, db: Session = Depends(get_db), user: User | None = Depends(current_user)):
    return render(request, "terms.html", db, user)


@router.get("/privacy")
def privacy(request: Request, db: Session = Depends(get_db), user: User | None = Depends(current_user)):
    return render(request, "privacy.html", db, user)


@router.get("/sw.js")
def sw():
    # 서비스워커는 루트 스코프에서 제공해야 전체 페이지 푸시를 받는다
    return FileResponse(STATIC / "sw.js", media_type="application/javascript")


@router.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@router.get("/healthz")
def healthz():
    return {"ok": True}


@router.get("/favicon.ico")
def favicon():
    return RedirectResponse("/static/icons/icon-192.png")
