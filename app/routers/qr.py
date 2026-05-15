from fastapi import APIRouter, Depends, HTTPException, Query, Response
from redis import Redis
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_redis
from app.schemas import (
    CreateQRCodeRequest,
    CreateQRCodeResponse,
    GetQRCodeResponse,
    QRCodeListItem,
    UpdateQRCodeRequest,
)
from app.services.qr_service import (
    create_qr_code,
    delete_qr_code,
    get_or_generate_image,
    get_qr_code,
    get_qr_code_any_status,
    list_qr_codes,
    update_qr_code,
)

router = APIRouter(prefix="/v1", tags=["qr"])


@router.get("/qr_codes", response_model=list[QRCodeListItem])
def list_all(db: Session = Depends(get_db)):
    qr_list = list_qr_codes(db)
    return [
        QRCodeListItem(
            qr_token=qr.qr_token,
            url=qr.url,
            status=qr.status,
            created_at=qr.created_at,
        )
        for qr in qr_list
    ]


@router.post("/qr_code", status_code=201, response_model=CreateQRCodeResponse)
def create(
    request: CreateQRCodeRequest,
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    token = create_qr_code(db, request.url, redis=redis)
    return CreateQRCodeResponse(qr_token=token)


@router.get("/qr_code_image/{qr_token}")
def get_image(
    qr_token: str,
    dimension: int = Query(default=256, ge=32, le=2048),
    color: str = Query(default="#000000", pattern=r"^#[0-9a-fA-F]{6}$"),
    border: int = Query(default=4, ge=0, le=20),
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    image_spec = {"dimension": dimension, "color": color, "border": border}
    image_bytes = get_or_generate_image(db, qr_token, image_spec, redis)
    if image_bytes is None:
        raise HTTPException(status_code=404, detail="QR code not found")
    return Response(
        content=image_bytes,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=300, must-revalidate"},
    )


@router.get("/qr_code/{qr_token}", response_model=GetQRCodeResponse)
def get_one(qr_token: str, db: Session = Depends(get_db)):
    qr = get_qr_code_any_status(db, qr_token)
    if not qr:
        raise HTTPException(status_code=404, detail="QR code not found")
    if qr.status == "deleted":
        raise HTTPException(status_code=410, detail="QR code has been deleted")
    return GetQRCodeResponse(
        url=qr.url,
        status=qr.status,
        created_at=qr.created_at,
    )


@router.put("/qr_code/{qr_token}", status_code=204)
def update(
    qr_token: str,
    request: UpdateQRCodeRequest,
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    if not update_qr_code(db, qr_token, request.url, redis=redis):
        raise HTTPException(status_code=404, detail="QR code not found")
    return Response(status_code=204)


@router.delete("/qr_code/{qr_token}", status_code=204)
def delete(
    qr_token: str,
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    if not delete_qr_code(db, qr_token, redis=redis):
        raise HTTPException(status_code=404, detail="QR code not found")
    return Response(status_code=204)
