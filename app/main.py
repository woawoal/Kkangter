from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from .config import BASE_DIR, settings
from .db import Base, SessionLocal, engine
from .deps import LoginRequired, templates
from .routes import admin, auth, me, posts, public, vision
from .services.catalog import seed_catalog


def init_db():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_catalog(db, BASE_DIR / "seed" / "catalog.json")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY, max_age=60 * 60 * 24 * 60,
                   same_site="lax", https_only=settings.ENV == "prod")

app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
if settings.STORAGE_BACKEND == "local":
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")

for r in (public.router, auth.router, posts.router, me.router, admin.router, vision.router):
    app.include_router(r)


@app.exception_handler(LoginRequired)
async def _login_required(request: Request, exc: LoginRequired):
    return RedirectResponse(f"/login?next={quote(exc.next_url)}", 303)


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith("/api/") or request.url.path.startswith("/push/"):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    msg = exc.detail if isinstance(exc.detail, str) and exc.detail not in ("Not Found", "Forbidden") else \
        {404: "페이지를 찾을 수 없어요", 403: "권한이 없어요"}.get(exc.status_code, "문제가 생겼어요")
    return templates.TemplateResponse(request, "error.html", {"user": None, "unread": 0, "flash": None,
                                                              "code": exc.status_code, "msg": msg},
                                      status_code=exc.status_code)
