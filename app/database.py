from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# Sized for db-f1-micro (~25 max_connections). Per-instance budget is
# pool_size + max_overflow = 3 conn. Combined with Cloud Run --max-instances=6
# this caps the app at ~18 conn, leaving ~7 for admin / flush job / psql.
_PG_POOL_KWARGS = dict(
    pool_size=1,
    max_overflow=2,
    pool_timeout=10,
    pool_recycle=300,
    pool_pre_ping=True,
)

# Lazy module-level singleton; only instantiated inside the Connector branch
# so importing this module without GCP creds (tests, local dev) stays safe.
_connector = None


def _build_engine():
    if settings.INSTANCE_CONNECTION_NAME:
        from google.cloud.sql.connector import Connector, IPTypes

        global _connector
        if _connector is None:
            _connector = Connector()
        ip_type = (
            IPTypes.PRIVATE
            if settings.CLOUD_SQL_IP_TYPE == "PRIVATE"
            else IPTypes.PUBLIC
        )

        def getconn():
            return _connector.connect(
                settings.INSTANCE_CONNECTION_NAME,
                "pg8000",
                user=settings.DB_USER,
                password=settings.DB_PASS,
                db=settings.DB_NAME,
                ip_type=ip_type,
            )

        return create_engine(
            "postgresql+pg8000://", creator=getconn, **_PG_POOL_KWARGS
        )

    if settings.DATABASE_URL.startswith("sqlite"):
        return create_engine(
            settings.DATABASE_URL, connect_args={"check_same_thread": False}
        )

    return create_engine(settings.DATABASE_URL, **_PG_POOL_KWARGS)


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def close_connector() -> None:
    """Release the Cloud SQL Connector's background thread on shutdown."""
    global _connector
    if _connector is not None:
        _connector.close()
        _connector = None
