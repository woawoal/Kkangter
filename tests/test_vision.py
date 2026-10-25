"""사진 인식: 모델 호출은 mock(사람이 읽은 결과)으로 대체하고, 카탈로그 매칭·흐름·정확도 기록을 검증."""
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Character, Event, Post, VisionParse
from app.services import vision

from .conftest import login

IMG = Path(__file__).parent / "fixtures" / "lullaby_trade.png"


def ids(*names):
    with SessionLocal() as db:
        return {c.name: c.id for c in db.scalars(select(Character).where(Character.name.in_(names)))}


def test_match_lullaby_mock(client):
    with SessionLocal() as db:
        res = vision.match(db, vision.MOCK_RESPONSE)
    assert res["series"]["name"] == "Lullaby Fluffy Baby"
    assert res["post_type"] == "trade"
    top = {(d["mark"], d["candidates"][0]["name"]) for d in res["detections"] if d["candidates"]}
    # 이름표가 가려진 양·돼지는 동물로, 깨진 글자(详迁愿)는 별칭 퍼지 매칭으로 찾아야 한다
    assert {("have", "양"), ("have", "돼지"), ("want", "강아지"), ("want", "쥐")} <= top
    secret = next(d for d in res["detections"] if d["is_secret"])
    assert secret["candidates"][0]["name"] == "시크릿"  # 같은 '토끼' 모양이어도 이름표로 구분


def test_unknown_series_gives_catalog_suggestion(client):
    raw = {"image_type": "lineup_sheet", "series_text": "Moonlight Bunny Party",
           "characters": [{"label_text": "月月", "animal": "고래", "mark": "have"}]}
    with SessionLocal() as db:
        res = vision.match(db, raw)
    assert res["series"] is None
    assert "Moonlight Bunny Party | 고래 | 月月" in res["catalog_suggestion"]


def test_parse_endpoint_and_acceptance(client, monkeypatch):
    monkeypatch.setattr(settings, "VISION_PROVIDER", "mock")
    login(client, "visionuser")
    assert "사진으로 자동 채우기" in client.get("/new").text
    r = client.post("/api/vision/parse", files={"image": ("t.png", IMG.read_bytes(), "image/png")})
    assert r.status_code == 200, r.text
    body = r.json()
    # 같은 이미지는 캐시 → 모델 재호출 없음
    r2 = client.post("/api/vision/parse", files={"image": ("t.png", IMG.read_bytes(), "image/png")})
    assert r2.status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(Event).where(Event.name == "vision_parsed").order_by(Event.id.desc())).props["cached"]

    c = ids("양", "돼지", "강아지", "쥐", "토끼")
    # 사용자가 돼지를 지우고 토끼로 바꿨다고 가정 → top1 3/4
    data = {"type": "trade", "have": [str(c["양"]), str(c["토끼"])], "want": [str(c["강아지"]), str(c["쥐"])],
            "vision_parse_id": str(body["parse_id"]), "price": "", "note": ""}
    r = client.post("/new", data=data, files=[("photos", ("t.png", IMG.read_bytes(), "image/png"))],
                    follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        ev = db.scalar(select(Event).where(Event.name == "vision_accepted"))
        assert ev.props["n_marked"] == 4 and ev.props["top1"] == 3
        vp = db.get(VisionParse, body["parse_id"])
        assert vp.post_id and vp.accepted["have"]
    login(client, "admin")
    page = client.get("/admin/vision").text
    assert "75%" in page


def test_vision_disabled_without_key(client):
    login(client, "nokey")
    assert "사진으로 자동 채우기" not in client.get("/new").text
    assert client.post("/api/vision/parse", files={"image": ("t.png", IMG.read_bytes(), "image/png")}).status_code == 503
