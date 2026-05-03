from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from redis import Redis
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Base, engine, get_db
from app.dependencies import get_redis
from app.routers import internal as internal_router
from app.routers import qr
from app.services.qr_service import get_qr_code_any_status


@asynccontextmanager
async def lifespan(app: FastAPI):
    # SQLite (local dev) auto-creates tables for fast reset; PostgreSQL relies
    # on `alembic upgrade head` so production schema stays version-controlled
    if settings.DATABASE_URL.startswith("sqlite"):
        Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="QR Code Generator", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(qr.router)
app.include_router(internal_router.router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    for error in exc.errors():
        if error.get("type") == "json_invalid":
            return JSONResponse(
                status_code=422,
                content={
                    "detail": (
                        "Invalid JSON body. If your URL contains special characters "
                        "like ?, =, &, make sure they are not backslash-escaped "
                        "in the JSON string (e.g. use ? not \\?)."
                    )
                },
            )
    # Fall back to FastAPI's default serialization for other validation errors
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(exc.errors())},
    )


@app.get("/r/{qr_token}")
def redirect(
    qr_token: str,
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    cache_key = f"qr:url:{qr_token}"
    cached_url = redis.get(cache_key)
    if cached_url:
        _record_click(redis, qr_token)
        return RedirectResponse(url=cached_url.decode(), status_code=302)

    qr = get_qr_code_any_status(db, qr_token)
    if not qr:
        raise HTTPException(status_code=404, detail="QR code not found")
    if qr.status == "deleted":
        raise HTTPException(status_code=410, detail="QR code has been deleted")

    redis.setex(cache_key, 86400, qr.url)
    _record_click(redis, qr_token)
    return RedirectResponse(url=qr.url, status_code=302)


def _record_click(redis: Redis, token: str) -> None:
    hour = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")
    redis.hincrby(f"qr:clicks:{hour}", token, 1)


if settings.ENVIRONMENT == "local":
    import os

    os.makedirs(settings.STORAGE_PATH, exist_ok=True)
    app.mount("/static", StaticFiles(directory=settings.STORAGE_PATH), name="static")
