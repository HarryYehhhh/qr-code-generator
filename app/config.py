from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ENVIRONMENT: str = "local"
    DATABASE_URL: str = "sqlite:///./qr_codes.db"
    SERVER_SECRET: str = "local-dev-secret-change-me"
    REDIS_URL: str = "redis://localhost:6379/0"
    INTERNAL_TOKEN: str = ""
    INSTANCE_CONNECTION_NAME: str = ""
    DB_USER: str = ""
    DB_PASS: str = ""
    DB_NAME: str = ""
    CLOUD_SQL_IP_TYPE: str = "PUBLIC"

    class Config:
        env_file = "env/.env"


settings = Settings()