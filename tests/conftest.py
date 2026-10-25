import os
import tempfile

import pytest

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["UPLOAD_DIR"] = f"{_tmp}/uploads"
os.environ["ADMIN_IDS"] = "admin"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app, init_db  # noqa: E402


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    init_db()
    with TestClient(app) as c:
        yield c


def login(c, nickname, onboard=True):
    c.post("/auth/dev", data={"nickname": nickname})
    if onboard:
        c.post("/onboarding", data={"nickname": nickname, "openchat_url": f"https://open.kakao.com/o/{nickname}"})
    return c
