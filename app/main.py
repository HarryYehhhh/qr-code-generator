from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Base, engine, get_db
from app.routers import qr
from app.services.qr_service import redirect_qr_code


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="QR Code Generator", lifespan=lifespan)

app.include_router(qr.router)


@app.get("/{qr_token}")
def redirect(qr_token: str, db: Session = Depends(get_db)):
    url = redirect_qr_code(db, qr_token)
    if not url:
        raise HTTPException(status_code=404, detail="QR code not found")
    return RedirectResponse(url=url, status_code=302)


if settings.ENVIRONMENT == "local":
    import os

    os.makedirs(settings.STORAGE_PATH, exist_ok=True)
    app.mount("/static", StaticFiles(directory=settings.STORAGE_PATH), name="static")
