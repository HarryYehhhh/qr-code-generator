from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import Base, engine, get_db
from app.routers import qr
from app.services.qr_service import get_qr_code_any_status


@asynccontextmanager
async def lifespan(app: FastAPI):
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
def redirect(qr_token: str, db: Session = Depends(get_db)):
    qr = get_qr_code_any_status(db, qr_token)
    if not qr:
        raise HTTPException(status_code=404, detail="QR code not found")
    if qr.status == "deleted":
        raise HTTPException(status_code=410, detail="QR code has been deleted")
    from app.services.qr_service import redirect_qr_code

    redirect_qr_code(db, qr_token)
    return RedirectResponse(url=qr.url, status_code=302)


if settings.ENVIRONMENT == "local":
    import os

    os.makedirs(settings.STORAGE_PATH, exist_ok=True)
    app.mount("/static", StaticFiles(directory=settings.STORAGE_PATH), name="static")
