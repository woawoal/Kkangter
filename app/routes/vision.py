from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import render, require_admin, require_user
from ..models import Event, User, VisionParse, now
from ..services import storage, vision
from ..services.metrics import track

router = APIRouter()


@router.post("/api/vision/parse")
async def parse(request: Request, image: UploadFile, db: Session = Depends(get_db),
                user: User = Depends(require_user)):
    if not settings.vision_enabled:
        raise HTTPException(503, "사진 인식 기능이 꺼져 있어요")
    raw_bytes = await image.read()
    if len(raw_bytes) > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "사진은 8MB 이하만 올릴 수 있어요")
    used = db.scalar(select(func.count(Event.id)).where(
        Event.name == "vision_parsed", Event.user_id == user.id,
        Event.created_at >= now() - timedelta(days=1))) or 0
    if used >= settings.VISION_DAILY_LIMIT and not user.is_admin:
        raise HTTPException(429, "오늘 사진 인식 횟수를 다 썼어요. 직접 선택해 주세요")
    try:
        jpeg, sha = vision.prepare(raw_bytes)
    except Exception:
        raise HTTPException(400, "이미지 파일이 아니에요")

    cached = db.scalar(select(VisionParse).where(VisionParse.image_sha == sha).order_by(VisionParse.id.desc()))
    try:
        if cached:  # 같은 이미지는 모델을 다시 부르지 않고, 최신 카탈로그로 매칭만 다시 한다
            raw, ms = cached.raw, 0
        else:
            raw_t0 = vision.time.monotonic()
            raw = vision.call_model(jpeg)
            ms = int((vision.time.monotonic() - raw_t0) * 1000)
    except vision.VisionError as e:
        track(db, "vision_failed", user_id=user.id, error=str(e)[:200])
        db.commit()
        raise HTTPException(502, "사진을 읽지 못했어요. 직접 선택해 주세요")
    result = vision.match(db, raw)
    vp = VisionParse(user_id=user.id, image_sha=sha, model=settings.VISION_MODEL if not cached else cached.model,
                     raw=raw, result=result, series_id=(result["series"] or {}).get("id"), latency_ms=ms,
                     image_url=cached.image_url if cached else storage.save_image(raw_bytes))
    db.add(vp)
    db.flush()
    track(db, "vision_parsed", user_id=user.id, parse_id=vp.id, cached=bool(cached),
          image_type=result["image_type"], series_matched=bool(result["series"]),
          n_det=len(result["detections"]), n_auto=sum(d["auto"] for d in result["detections"]), ms=ms)
    db.commit()
    return JSONResponse({"parse_id": vp.id, **result})


def record_acceptance(db: Session, parse_id: int | None, user_id: int, post_id: int,
                      have: list[int], want: list[int]):
    """글 작성 시 호출. AI 1순위 후보가 최종 선택에 남았는지 = top-1 정확도."""
    if not parse_id:
        return
    vp = db.get(VisionParse, parse_id)
    if not vp or vp.user_id != user_id or vp.post_id:
        return
    vp.post_id, vp.accepted = post_id, {"have": have, "want": want}
    final = {"have": set(have), "want": set(want)}
    marked = [d for d in vp.result.get("detections", []) if d["mark"] in ("have", "want")]
    top1 = sum(1 for d in marked if d["candidates"] and d["candidates"][0]["id"] in final[d["mark"]])
    top3 = sum(1 for d in marked if any(c["id"] in final[d["mark"]] for c in d["candidates"]))
    track(db, "vision_accepted", user_id=user_id, post_id=post_id, parse_id=vp.id,
          n_marked=len(marked), top1=top1, top3=top3)


@router.get("/admin/vision")
def admin_vision(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    items = db.scalars(select(VisionParse).order_by(VisionParse.id.desc()).limit(50)).all()
    acc = db.scalars(select(Event).where(Event.name == "vision_accepted")).all()
    n = sum(e.props.get("n_marked", 0) for e in acc)
    stats = {
        "인식 요청": db.scalar(select(func.count(Event.id)).where(Event.name == "vision_parsed")) or 0,
        "실패": db.scalar(select(func.count(Event.id)).where(Event.name == "vision_failed")) or 0,
        "글로 이어짐": len(acc),
        "Top-1 정확도": f"{sum(e.props.get('top1', 0) for e in acc) / n * 100:.0f}%" if n else "-",
        "Top-3 정확도": f"{sum(e.props.get('top3', 0) for e in acc) / n * 100:.0f}%" if n else "-",
    }
    return render(request, "admin/vision.html", db, user, items=items, stats=stats)
