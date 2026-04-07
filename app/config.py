from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ENVIRONMENT: str = "local"
    DATABASE_URL: str = "sqlite:///./qr_codes.db"
    STORAGE_PATH: str = "./qr_codes"
    BASE_URL: str = "http://localhost:8000"
    SERVER_SECRET: str = "local-dev-secret-change-me"
    GCS_BUCKET: str = ""
    CDN_BASE_URL: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
