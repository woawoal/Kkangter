"""운영 보조 명령.
  python -m app.cli vapid          # 웹 푸시 VAPID 키 생성
  python -m app.cli admin <닉네임|kakao_id>   # 관리자 지정
  python -m app.cli stats          # 지표 요약 출력 (포트폴리오 수치 확인용)
  python -m app.cli demo           # 로컬 데모 데이터 생성
"""
import sys

from sqlalchemy import or_, select


def vapid():
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    priv = key.private_numbers().private_value.to_bytes(32, "big")
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731
    print(f"VAPID_PUBLIC_KEY={b64(pub)}\nVAPID_PRIVATE_KEY={b64(priv)}")


def make_admin(ident: str):
    from .db import SessionLocal
    from .main import init_db
    from .models import User

    init_db()
    with SessionLocal() as db:
        u = db.scalar(select(User).where(or_(User.provider_id == ident, User.nickname == ident)))
        if not u:
            print("사용자를 찾지 못했어요 (먼저 한 번 로그인하세요)")
            return
        u.is_admin = True
        db.commit()
        print(f"{u.nickname}({u.id}) → 관리자")


def stats():
    from .db import SessionLocal
    from .main import init_db
    from .services.metrics import dashboard

    init_db()
    with SessionLocal() as db:
        d = dashboard(db)
    for k, v in {**d["kpi"], **d["rates"]}.items():
        print(f"{k:>14}: {v}")


def demo():
    """사진 없이도 화면을 확인할 수 있게 컬러 placeholder 이미지와 글을 만든다."""
    import io
    import random

    from PIL import Image, ImageDraw

    from .db import SessionLocal
    from .main import init_db
    from .models import Character, Post, PostItem, PostPhoto, User, Watch
    from .services import matching, storage
    from .services.metrics import track

    init_db()
    random.seed(7)
    palette = ["#FFD6E0", "#FFEFB5", "#C9F2E3", "#D7E3FF", "#EADCFF", "#FFE0C7"]
    with SessionLocal() as db:
        chars = db.scalars(select(Character)).all()
        users = []
        for i, nick in enumerate(["말랑이", "하치덕후", "쿠로미맘", "랜덤깡장인", "모몽가최고"]):
            u = db.scalar(select(User).where(User.provider == "dev", User.provider_id == nick))
            if not u:
                u = User(provider="dev", provider_id=nick, nickname=nick,
                         openchat_url=f"https://open.kakao.com/o/demo{i}")
                db.add(u)
                db.flush()
                track(db, "signup", user_id=u.id)
            users.append(u)

        def img(text):
            im = Image.new("RGB", (800, 800), random.choice(palette))
            d = ImageDraw.Draw(im)
            d.ellipse((220, 200, 580, 560), fill="white")
            d.ellipse((330, 330, 360, 360), fill="#333")
            d.ellipse((440, 330, 470, 360), fill="#333")
            buf = io.BytesIO()
            im.save(buf, "PNG")
            return storage.save_image(buf.getvalue())

        w = Watch(user_id=users[0].id, character_id=chars[1].id)
        db.add(w)
        for k in range(14):
            u = users[k % len(users)]
            t = ["sell", "trade", "want"][k % 3]
            have = random.sample(chars, random.choice([1, 1, 2]))
            want = random.sample(chars, 1)
            p = Post(user_id=u.id, type=t, price=random.choice([8000, 12000, 15000, 22000]) if t != "trade" else None,
                     note="상태 좋아요! 반값택배 가능", ship_method="반값택배")
            if t != "want":
                for c in have:
                    p.items.append(PostItem(character_id=c.id, role="have",
                                            price=random.choice([7000, 9000, 13000]) if t == "sell" else None))
                p.photos.append(PostPhoto(url=img(have[0].name)))
            if t != "sell":
                for c in want:
                    p.items.append(PostItem(character_id=c.id, role="want"))
            db.add(p)
            db.flush()
            track(db, "post_created", user_id=u.id, post_id=p.id, type=t)
            matching.match_new_post(db, p)
        db.commit()
        print("데모 데이터 생성 완료")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"vapid": vapid, "stats": stats, "demo": demo}.get(cmd, lambda: None)() if cmd != "admin" else make_admin(sys.argv[2])
    if cmd not in ("vapid", "stats", "demo", "admin"):
        print(__doc__)
