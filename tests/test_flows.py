import io

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

from app.db import SessionLocal
from app.main import app
from app.models import Character, Event, Notification, Post

from .conftest import login


def png():
    buf = io.BytesIO()
    Image.new("RGB", (2000, 1500), "pink").save(buf, "PNG")
    return ("p.png", buf.getvalue(), "image/png")


def char_ids(n=3):
    with SessionLocal() as db:
        return [c.id for c in db.scalars(select(Character).order_by(Character.id).limit(n))]


def user(name):
    c = TestClient(app)
    c.__enter__()
    return login(c, name)


def create(c, type_, have=(), want=(), price="", **extra):
    data = {"type": type_, "price": price, "note": "상태 좋아요",
            "have": [str(h) for h in have], "want": [str(w) for w in want]}
    data.update({k: str(v) for k, v in extra.items()})
    files = [("photos", png())] if type_ != "want" else []
    return c.post("/new", data=data, files=files, follow_redirects=False)


def count(model, *where):
    with SessionLocal() as db:
        return db.scalar(select(func.count(model.id)).where(*where))


def test_pages_render(client):
    for path in ["/", "/search", "/search?q=하치", "/beta", "/login", "/terms", "/privacy", "/healthz"]:
        assert client.get(path).status_code == 200, path
    assert client.get("/new", follow_redirects=False).headers["location"].startswith("/login")


def test_onboarding_required_before_posting(client):
    login(client, "noob", onboard=False)
    r = client.get("/new", follow_redirects=False)
    assert r.headers["location"] == "/onboarding"
    r = client.post("/onboarding", data={"nickname": "noob", "openchat_url": "http://evil.com"})
    assert r.status_code == 400


def test_create_sell_post_and_resize(client):
    a, *_ = char_ids()
    login(client, "seller")
    r = create(client, "sell", have=[a], price="15000", **{f"price_{a}": "15,000"})
    assert r.status_code == 303
    pid = int(r.headers["location"].split("/")[-1])
    with SessionLocal() as db:
        p = db.get(Post, pid)
        assert p.price == 15000 and p.have_items[0].price == 15000
        assert p.photos[0].url.endswith(".webp")
    assert "seller" in client.get(f"/p/{pid}").text


def test_validation(client):
    a, b, _ = char_ids()
    login(client, "v")
    assert create(client, "trade", have=[a]).status_code == 400  # 원하는 인형 없음
    assert client.post("/new", data={"type": "sell", "have": str(a)}).status_code == 400  # 사진 없음


def test_watch_notification_and_click(client):
    a, b, _ = char_ids()
    buyer = user("buyer")
    buyer.post("/watch", data={"character_id": a, "post_types": ["sell", "trade"]})
    seller = user("seller2")
    create(seller, "sell", have=[a], price="9000")
    create(seller, "sell", have=[b], price="9000")  # 관심 없는 인형 → 알림 X
    assert count(Notification) == 1
    page = buyer.get("/notifications").text
    assert "관심 인형" in page
    with SessionLocal() as db:
        nid = db.scalar(select(Notification.id))
    r = buyer.get(f"/n/{nid}", follow_redirects=False)
    assert r.headers["location"].startswith("/p/")
    assert count(Event, Event.name == "notification_clicked") == 1


def test_watch_max_price_and_series(client):
    a, b, _ = char_ids()
    with SessionLocal() as db:
        sid = db.get(Character, a).series_id
    w = user("w")
    w.post("/watch", data={"series_id": sid, "character_id": "", "max_price": "10000"})
    s = user("s")
    create(s, "sell", have=[b], price="20000")  # 가격 초과
    assert count(Notification) == 0
    create(s, "sell", have=[b], price="8000")
    assert count(Notification) == 1


def test_mutual_trade_match(client):
    a, b, _ = char_ids()
    u1, u2 = user("u1"), user("u2")
    create(u1, "trade", have=[a], want=[b])
    r = create(u2, "trade", have=[b], want=[a])
    assert count(Notification, Notification.kind == "mutual_trade") == 2
    pid = int(r.headers["location"].split("/")[-1])
    assert "맞교환 상대" in u2.get(f"/p/{pid}").text


def test_contact_and_done_metrics(client):
    a, *_ = char_ids()
    s = user("s3")
    pid = int(create(s, "sell", have=[a]).headers["location"].split("/")[-1])
    b = user("b3")
    r = b.get(f"/p/{pid}/contact", follow_redirects=False)
    assert r.headers["location"].startswith("https://open.kakao.com/")
    assert b.post(f"/p/{pid}/status", data={"status": "done"}, follow_redirects=False).status_code == 403
    s.post(f"/p/{pid}/status", data={"status": "done"})
    assert count(Event, Event.name == "post_done") == 1
    assert count(Event, Event.name == "contact_clicked") == 1


def test_search(client):
    a, *_ = char_ids()
    s = user("s4")
    create(s, "sell", have=[a])
    r = client.get("/api/characters", params={"q": "하치 와레"})
    assert r.json() and r.json()[0]["name"] == "하치와레"
    assert "치이카와" in client.get("/search", params={"q": "chiikawa"}).text


def test_beta_signup_and_admin(client):
    r = client.post("/beta", data={"nickname": "베타", "contact": "kid", "interests": ["치이카와 랜덤 마스코트"],
                                   "roles": ["trade"], "agree": "1"}, follow_redirects=False)
    assert r.headers["location"] == "/beta?done=1"
    assert client.post("/beta", data={"nickname": "x", "contact": "y"}).status_code == 400  # 동의 없음
    assert client.get("/admin", follow_redirects=False).status_code in (303, 307)
    login(client, "admin")
    page = client.get("/admin").text
    assert "베타 신청" in page
    csv = client.get("/admin/beta.csv").text
    assert "베타" in csv
    client.post("/admin/catalog", data={"text": "새 시리즈 | 새캐릭 | newone | y"})
    assert client.get("/api/characters", params={"q": "newone"}).json()[0]["secret"] is True


def test_bump_limit(client):
    a, *_ = char_ids()
    s = user("s5")
    pid = int(create(s, "sell", have=[a]).headers["location"].split("/")[-1])
    s.post(f"/p/{pid}/bump")
    assert count(Event, Event.name == "post_bumped") == 0  # 24시간 이내라 거절
