from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.services.url_validator import validate_safe_url


def _validate_url_field(v: str) -> str:
    if not v.startswith(("http://", "https://")):
        raise ValueError("URL must start with http:// or https://")
    if not v.isascii():
        raise ValueError("URL must contain only ASCII characters")
    validate_safe_url(v)
    return v


class CreateQRCodeRequest(BaseModel):
    url: str = Field(..., max_length=2048)

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        return _validate_url_field(v)


class CreateQRCodeResponse(BaseModel):
    qr_token: str


class GetQRCodeResponse(BaseModel):
    url: str
    click_count: int
    status: str
    created_at: datetime


class QRCodeListItem(BaseModel):
    qr_token: str
    url: str
    click_count: int
    status: str
    created_at: datetime


class UpdateQRCodeRequest(BaseModel):
    url: str = Field(..., max_length=2048)

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        return _validate_url_field(v)


class ImageSpec(BaseModel):
    dimension: int = Field(default=256, ge=32, le=2048)
    color: str = Field(default="#000000", pattern=r"^#[0-9a-fA-F]{6}$")
    border: int = Field(default=4, ge=0, le=20)
