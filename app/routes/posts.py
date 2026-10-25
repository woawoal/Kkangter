from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..config import settings
from ..db import get_db
from ..deps import current_user, flash, render, require_user
from ..models import (
    POST_STATUS, POST_TYPES, Character, Post, PostItem, PostPhoto, Report, Series, User, Watch, now,
)
from ..services import catalog, matching, storage
from ..services.metrics import track

router = APIRouter()
PAGE = 20


def _feed_query(type_: str = "", series_id: int | None = None, char_id: int | None = None,
                char_ids: list[int] | None = None, include_done: bool = False):
    stmt = select(Post).options(
        selectinload(Post.photos), selectinload(Post.items).selectinload(PostItem.character).selectinload(Character.series)
    )
    stmt = stmt.where(Post.status.in_(["open", "reserved", "done"] if include_done else ["open", "reserved"]))
    if type_ in POST_TYPES:
        stmt = stmt.where(Post.type == type_)
    if char_id:
        char_ids = [char_id]
    if char_ids is not None:
        stmt = stmt.where(Post.id.in_(select(PostItem.post_id).where(PostItem.character_id.in_(char_ids or [-1]))))
    elif series_id:
        sub = select(PostItem.post_id).join(Character).where(Character.series_id == series_id)
        stmt = stmt.where(Post.id.in_(sub))
    return stmt.order_by(Post.bumped_at.desc(), Post.id.desc())


@router.get("/")
def home(request: Request, type: str = "", series: int | None = None, page: int = 1,
         src: str = "", db: Session = Depends(get_db), user: User | None = Depends(current_user)):
    if src:
        request.session["src"] = src[:50]  # 유입 경로(오픈톡방 공지 링크 등) 기록
    if settings.PRELAUNCH and not (user and user.is_admin):
        return RedirectResponse("/beta" + (f"?src={src}" if src else ""), 303)
    page = max(page, 1)
    posts = db.scalars(_feed_query(type, series).offset((page - 1) * PAGE).limit(PAGE + 1)).all()
    return render(request, "home.html", db, user, posts=posts[:PAGE], has_more=len(posts) > PAGE, page=page,
                  type=type, series_id=series, series_list=catalog.all_series(db), types=POST_TYPES)


@router.get("/search")
def search(request: Request, q: str = "", type: str = "", db: Session = Depends(get_db),
           user: User | None = Depends(current_user)):
    chars = catalog.search_characters(db, q) if q else []
    posts = []
    if q:
        posts = db.scalars(_feed_query(type, char_ids=[c.id for c in chars]).limit(60)).all()
        track(db, "search", user_id=user.id if user else None, q=q.strip()[:50], results=len(posts))
        db.commit()
    return render(request, "search.html", db, user, q=q, chars=chars, posts=posts, type=type, types=POST_TYPES,
                  series_list=catalog.all_series(db))


@router.get("/c/{char_id}")
def character_page(char_id: int, request: Request, type: str = "", db: Session = Depends(get_db),
                   user: User | None = Depends(current_user)):
    ch = db.get(Character, char_id)
    if not ch:
        raise HTTPException(404)
    posts = db.scalars(_feed_query(type, char_id=char_id).limit(60)).all()
    counts = dict(db.execute(
        select(Post.type, func.count(Post.id.distinct())).join(PostItem)
        .where(PostItem.character_id == char_id, Post.status == "open").group_by(Post.type)
    ).all())
    # 최근 거래가 (시세 기능의 씨앗): 거래완료된 양도글의 개별가
    recent_prices = db.execute(
        select(PostItem.price, Post.done_at).join(Post)
        .where(PostItem.character_id == char_id, PostItem.role == "have", Post.type == "sell",
               Post.status == "done", PostItem.price.isnot(None))
        .order_by(Post.done_at.desc()).limit(5)
    ).all()
    watch = None
    if user:
        watch = db.scalar(select(Watch).where(Watch.user_id == user.id, Watch.character_id == char_id,
                                              Watch.is_active.is_(True)))
    return render(request, "character.html", db, user, ch=ch, posts=posts, counts=counts, type=type,
                  types=POST_TYPES, watch=watch, recent_prices=recent_prices)


@router.get("/s/{series_id}")
def series_page(series_id: int, request: Request, db: Session = Depends(get_db),
                user: User | None = Depends(current_user)):
    return RedirectResponse(f"/?series={series_id}", 303)


@router.get("/p/{post_id}")
def post_detail(post_id: int, request: Request, db: Session = Depends(get_db),
                user: User | None = Depends(current_user)):
    post = db.get(Post, post_id)
    if not post or (post.status == "hidden" and not (user and (user.is_admin or user.id == post.user_id))):
        raise HTTPException(404, "삭제되었거나 없는 글이에요")
    is_owner = bool(user and user.id == post.user_id)
    if not is_owner:
        post.view_count += 1
        track(db, "post_viewed", user_id=user.id if user else None, post_id=post.id)
        db.commit()
    mutual = matching.find_mutual_trades(db, post) if post.type == "trade" and post.status == "open" else []
    return render(request, "post.html", db, user, post=post, is_owner=is_owner, mutual=mutual,
                  statuses=POST_STATUS)


@router.get("/p/{post_id}/contact")
def contact(post_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """연락하기: 클릭을 기록한 뒤 오픈채팅으로 보내거나 카톡 ID를 보여준다. (거래 의향의 핵심 지표)"""
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(404)
    if user.id != post.user_id:
        track(db, "contact_clicked", user_id=user.id, post_id=post.id, to=post.user_id)
        db.commit()
    if post.user.openchat_url:
        return RedirectResponse(post.user.openchat_url, 303)
    return render(request, "contact.html", db, user, post=post)


# ---------- 글쓰기 ----------

@router.get("/new")
def new_post(request: Request, type: str = "sell", char: int | None = None, db: Session = Depends(get_db),
             user: User = Depends(require_user)):
    if not user.onboarded:
        request.session["next"] = "/new"
        return RedirectResponse("/onboarding", 303)
    preset = db.get(Character, char) if char else None
    return render(request, "new.html", db, user, type=type if type in POST_TYPES else "sell", types=POST_TYPES,
                  catalog_json=_catalog_json(db), preset=preset, max_photos=settings.MAX_PHOTOS)


def _catalog_json(db: Session):
    return [{"id": s.id, "name": s.name, "chars": [{"id": c.id, "name": c.name, "secret": c.is_secret,
                                                   "aliases": c.aliases} for c in s.characters]}
            for s in catalog.all_series(db)]


def _int_or_none(v: str | None):
    v = (v or "").replace(",", "").replace("원", "").strip()
    return int(v) if v.isdigit() else None


@router.post("/new")
async def create_post(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    form = await request.form()
    type_ = form.get("type", "")
    have = [int(x) for x in form.getlist("have") if str(x).isdigit()]
    want = [int(x) for x in form.getlist("want") if str(x).isdigit()]
    photos: list[UploadFile] = [f for f in form.getlist("photos") if getattr(f, "filename", "")]

    errors = []
    if type_ not in POST_TYPES:
        errors.append("구분을 선택해 주세요")
    if type_ in ("sell", "trade") and not have:
        errors.append("가지고 있는 인형을 1개 이상 선택해 주세요")
    if type_ in ("trade", "want") and not want:
        errors.append("원하는 인형을 1개 이상 선택해 주세요")
    if type_ in ("sell", "trade") and not photos:
        errors.append("실물 사진을 1장 이상 올려 주세요")
    if len(photos) > settings.MAX_PHOTOS:
        errors.append(f"사진은 최대 {settings.MAX_PHOTOS}장까지 올릴 수 있어요")
    valid_ids = set(db.scalars(select(Character.id).where(Character.id.in_(have + want))).all())
    if set(have + want) - valid_ids:
        errors.append("없는 캐릭터가 선택됐어요")

    urls = []
    if not errors:
        for f in photos:
            try:
                urls.append(storage.save_image(await f.read()))
            except storage.InvalidImage as e:
                errors.append(str(e))
                break
    if errors:
        return render(request, "new.html", db, user, type=type_ or "sell", types=POST_TYPES, errors=errors,
                      catalog_json=_catalog_json(db), preset=None, max_photos=settings.MAX_PHOTOS,
                      status_code=400)

    post = Post(user_id=user.id, type=type_, price=_int_or_none(form.get("price")),
                note=str(form.get("note", ""))[:1000], ship_method=str(form.get("ship_method", ""))[:30])
    if type_ != "want":
        for cid in dict.fromkeys(have):
            post.items.append(PostItem(character_id=cid, role="have",
                                       price=_int_or_none(form.get(f"price_{cid}")) if type_ == "sell" else None))
    if type_ != "sell":
        for cid in dict.fromkeys(want):
            post.items.append(PostItem(character_id=cid, role="want"))
    for i, u in enumerate(urls):
        post.photos.append(PostPhoto(url=u, sort=i))
    db.add(post)
    db.flush()
    track(db, "post_created", user_id=user.id, post_id=post.id, type=type_, n_items=len(post.items),
          n_photos=len(urls))
    notes = matching.match_new_post(db, post)
    db.commit()
    flash(request, "글을 올렸어요!" + (f" 관심 있는 {len(notes)}명에게 알림을 보냈어요." if notes else ""))
    return RedirectResponse(f"/p/{post.id}", 303)


@router.post("/p/{post_id}/status")
def change_status(post_id: int, request: Request, status: str = Form(...), db: Session = Depends(get_db),
                  user: User = Depends(require_user)):
    post = db.get(Post, post_id)
    if not post or (post.user_id != user.id and not user.is_admin):
        raise HTTPException(403)
    if status not in POST_STATUS:
        raise HTTPException(400)
    prev = post.status
    post.status = status
    if status == "done" and prev != "done":
        post.done_at = now()
        track(db, "post_done", user_id=user.id, post_id=post.id, type=post.type)
    track(db, "post_status_changed", user_id=user.id, post_id=post.id, frm=prev, to=status)
    db.commit()
    flash(request, {"done": "거래완료로 바꿨어요. 축하해요! 🎉", "hidden": "글을 숨겼어요"}.get(status, "상태를 바꿨어요"))
    return RedirectResponse(f"/p/{post.id}" if status != "hidden" else "/me", 303)


@router.post("/p/{post_id}/bump")
def bump(post_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """무료 끌어올리기(24시간 1회). 유료화 시 이 엔드포인트에 결제 확인만 붙이면 된다."""
    post = db.get(Post, post_id)
    if not post or post.user_id != user.id:
        raise HTTPException(403)
    if (now() - post.bumped_at).total_seconds() < 86400:
        flash(request, "끌어올리기는 24시간에 한 번 할 수 있어요")
    else:
        post.bumped_at = now()
        track(db, "post_bumped", user_id=user.id, post_id=post.id, paid=False)
        db.commit()
        flash(request, "글을 맨 위로 끌어올렸어요")
    return RedirectResponse(f"/p/{post.id}", 303)


@router.post("/p/{post_id}/report")
def report(post_id: int, request: Request, reason: str = Form(...), db: Session = Depends(get_db),
           user: User = Depends(require_user)):
    if not db.get(Post, post_id):
        raise HTTPException(404)
    db.add(Report(post_id=post_id, user_id=user.id, reason=reason[:300]))
    track(db, "report_created", user_id=user.id, post_id=post_id)
    db.commit()
    flash(request, "신고가 접수됐어요. 확인 후 조치할게요.")
    return RedirectResponse(f"/p/{post_id}", 303)


# ---------- JSON API (캐릭터 선택기 자동완성) ----------

@router.get("/api/characters")
def api_characters(q: str = "", db: Session = Depends(get_db)):
    return JSONResponse([{"id": c.id, "name": c.name, "series": c.series.name, "secret": c.is_secret}
                         for c in catalog.search_characters(db, q, limit=20)])


@router.post("/api/catalog-request")
def catalog_request(request: Request, text: str = Form(...), db: Session = Depends(get_db),
                    user: User = Depends(require_user)):
    track(db, "catalog_request", user_id=user.id, text=text[:200])
    db.commit()
    return JSONResponse({"ok": True})
