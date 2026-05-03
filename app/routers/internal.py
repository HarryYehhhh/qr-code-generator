from fastapi import APIRouter, Depends, Header, HTTPException, status
from redis import Redis
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import get_redis
from app.jobs.flush_clicks import flush_previous_hour

router = APIRouter(prefix="/internal", tags=["internal"])


@router.post("/flush_clicks")
def trigger_flush(
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis),
    x_internal_token: str = Header(default=""),
):
    if not settings.INTERNAL_TOKEN or x_internal_token != settings.INTERNAL_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    rows = flush_previous_hour(redis, db)
    return {"rows_flushed": rows}
