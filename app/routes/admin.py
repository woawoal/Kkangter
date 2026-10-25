import csv
import io

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import flash, render, require_admin
from ..models import BetaSignup, Event, Report, User
from ..services import catalog
from ..services.metrics import dashboard

router = APIRouter(prefix="/admin")


@router.get("")
def admin_home(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return render(request, "admin/dashboard.html", db, user, d=dashboard(db))


@router.get("/catalog")
def admin_catalog(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    reqs = db.scalars(select(Event).where(Event.name == "catalog_request").order_by(Event.id.desc()).limit(50)).all()
    return render(request, "admin/catalog.html", db, user, series_list=catalog.all_series(db), reqs=reqs)


@router.post("/catalog")
def admin_catalog_add(request: Request, text: str = Form(...), db: Session = Depends(get_db),
                      user: User = Depends(require_admin)):
    n = catalog.bulk_add(db, text)
    flash(request, f"캐릭터 {n}개를 추가했어요")
    return RedirectResponse("/admin/catalog", 303)


@router.get("/reports")
def admin_reports(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    items = db.scalars(select(Report).order_by(Report.resolved, Report.id.desc()).limit(100)).all()
    return render(request, "admin/reports.html", db, user, items=items)


@router.post("/reports/{rid}/resolve")
def admin_resolve(rid: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    r = db.get(Report, rid)
    if r:
        r.resolved = True
        db.commit()
    return RedirectResponse("/admin/reports", 303)


@router.get("/beta")
def admin_beta(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    items = db.scalars(select(BetaSignup).order_by(BetaSignup.id.desc())).all()
    return render(request, "admin/beta.html", db, user, items=items)


@router.get("/beta.csv")
def admin_beta_csv(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    buf = io.StringIO()
    buf.write("﻿")  # 엑셀 한글 깨짐 방지
    w = csv.writer(buf)
    w.writerow(["id", "닉네임", "연락처", "관심 시리즈", "용도", "유입", "신청일"])
    for b in db.scalars(select(BetaSignup).order_by(BetaSignup.id)):
        w.writerow([b.id, b.nickname, b.contact, b.interests, b.roles, b.source, b.created_at])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=beta_signups.csv"})
