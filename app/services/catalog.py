import json
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..models import Character, Series


def seed_catalog(db: Session, path: Path) -> int:
    """카탈로그가 비어 있을 때만 seed 파일을 넣는다."""
    if db.scalar(select(func.count(Series.id))):
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    n = 0
    for si, s in enumerate(data["series"]):
        series = Series(name=s["name"], brand=s.get("brand", ""), aliases=s.get("aliases", ""), sort=si)
        db.add(series)
        db.flush()
        for ci, c in enumerate(s["characters"]):
            db.add(Character(series_id=series.id, name=c["name"], aliases=c.get("aliases", ""),
                             is_secret=c.get("is_secret", False), sort=ci))
            n += 1
    db.commit()
    return n


def bulk_add(db: Session, text: str) -> int:
    """관리자용 일괄 추가. 한 줄에 `시리즈명 | 캐릭터명 | 별칭(선택) | 시크릿(선택: y)`"""
    added = 0
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        series = db.scalar(select(Series).where(Series.name == parts[0]))
        if not series:
            series = Series(name=parts[0], sort=999)
            db.add(series)
            db.flush()
        exists = db.scalar(select(Character.id).where(Character.series_id == series.id, Character.name == parts[1]))
        if exists:
            continue
        db.add(Character(series_id=series.id, name=parts[1], aliases=parts[2] if len(parts) > 2 else "",
                         is_secret=len(parts) > 3 and parts[3].lower() in ("y", "yes", "1", "시크릿")))
        added += 1
    db.commit()
    return added


def all_series(db: Session) -> list[Series]:
    return list(db.scalars(
        select(Series).where(Series.is_active.is_(True)).options(selectinload(Series.characters))
        .order_by(Series.sort, Series.id)
    ).all())


def search_characters(db: Session, q: str, limit: int = 30) -> list[Character]:
    """시리즈명·캐릭터명·별칭 부분일치. 공백 무시('하치 와레' → '하치와레')."""
    q = q.strip().lower().replace(" ", "")
    if not q:
        return []
    like = f"%{q}%"
    norm = lambda col: func.replace(func.lower(col), " ", "")  # noqa: E731
    stmt = (
        select(Character).join(Series)
        .where(Series.is_active.is_(True))
        .where(or_(norm(Character.name).like(like), norm(Character.aliases).like(like),
                   norm(Series.name).like(like), norm(Series.aliases).like(like)))
        .options(selectinload(Character.series))
        .order_by(Series.sort, Character.sort)
        .limit(limit)
    )
    return list(db.scalars(stmt).all())
