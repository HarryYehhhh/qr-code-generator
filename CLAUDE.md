# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

```bash
# Backend setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# Run backend dev server
uvicorn app.main:app --reload --port 8000

# Reset local database (SQLite auto-creates schema on startup)
rm -f qr_codes.db

# Run migrations (required for PostgreSQL / production)
alembic upgrade head

# Create a new migration after editing models
alembic revision --autogenerate -m "describe change"

# Mark an existing prod DB as already at baseline (one-off, before first deploy)
alembic stamp head

# Test endpoints
curl -X POST http://localhost:8000/v1/qr_code -H "Content-Type: application/json" -d '{"url": "https://example.com"}'
curl http://localhost:8000/v1/qr_code/{qr_token}
curl "http://localhost:8000/v1/qr_code_image/{qr_token}?dimension=256&color=%23000000&border=4"
curl -L http://localhost:8000/r/{qr_token}

# Run the click-stream worker (Sprint A)
python -m app.worker

# Start full local stack (api + worker + redis + postgres) via Docker Compose
docker-compose up

# Run tests
pytest tests/ -v

# Run a single test
pytest tests/test_qr.py::TestCreateQRCode::test_create_success -v

# Docker (production image)
docker build -t qr-code-generator .
docker run -p 8080:8080 --env-file .env.prod qr-code-generator

# Deploy to Cloud Run (via Artifact Registry)
gcloud builds submit --tag asia-east1-docker.pkg.dev/<PROJECT_ID>/qr-repo/qr-code-generator
gcloud run deploy qr-code-generator \
  --image asia-east1-docker.pkg.dev/<PROJECT_ID>/qr-repo/qr-code-generator \
  --region asia-east1
```

## Architecture

QR Code Generator using FastAPI backend (API-only, no frontend). Local dev uses SQLite; production uses Cloud SQL PostgreSQL on Cloud Run. Image bytes are cached in Redis (no GCS / CDN — see Image Cache section).

### ENV Switch Pattern
`ENVIRONMENT` in `app/config.py` documents which backends are active:
- **`"local"`**: SQLite DB
- **`"production"`**: Cloud SQL PostgreSQL via Cloud SQL Python Connector (`INSTANCE_CONNECTION_NAME` env var triggers the Connector branch in `app/database.py`)

### Request Flow
1. **Router** (`app/routers/qr.py`, prefix `/v1`) validates input via Pydantic schemas
2. **Service layer** (`app/services/qr_service.py`) orchestrates business logic
3. **Token service** generates Base62 tokens: `SHA-256(url + random_nonce + SERVER_SECRET)` → first 10 Base62 chars. Retries up to 5 times on UNIQUE constraint collision.
4. **Image service** (`app/services/image_service.py`) generates QR PNGs with customizable spec (dimension/color/border). Output bytes are cached in Redis directly — no disk / GCS.

### Soft Delete
DELETE endpoint sets `status='deleted'` + `deleted_at` timestamp. All queries filter `status == 'active'`. Rows are never physically removed.

### Redirect Route
`GET /r/{qr_token}` in `app/main.py` returns 302 (not 301) to allow URL updates to take effect. Reads URL from Redis (`qr:url:{token}`) — falls back to DB on cache miss and populates the cache. Click counting is async: fires `XADD clicks:stream … MAXLEN ~ 100000` (producer) — the worker process drains the stream and writes to `qr:clicks:{hour}`. `click_count` and `last_clicked_at` are populated by the hourly flush job (see Click Stats Pipeline). Registered **after** `/v1` router to avoid path conflicts.

### API Contracts
- `POST /v1/qr_code` → `{"qr_token": "..."}` (201)
- `GET /v1/qr_codes` → `[{qr_token, url, click_count, status, created_at}]` (200)
- `GET /v1/qr_code/{token}` → `{url, click_count, status, created_at}` (200) or 410 if deleted
- `PUT /v1/qr_code/{token}` → 204
- `DELETE /v1/qr_code/{token}` → 204 (soft delete)
- `GET /v1/qr_code_image/{token}?dimension=&color=&border=` → `image/png` bytes (200), `Cache-Control: public, max-age=300, must-revalidate`
- `GET /r/{token}` → 302 redirect

### Schema Migrations
Alembic is the source of truth for production schema. `alembic/env.py` reads `settings.DATABASE_URL` and imports `app.models` so `--autogenerate` sees all tables. The lifespan in `app/main.py` only runs `create_all` on SQLite — PostgreSQL must be migrated explicitly via `alembic upgrade head`. Tests still use `create_all` directly because they target a transient SQLite DB.

### Click Stats Pipeline (Sprint A — Redis Streams)

**Decision**: see [docs/decisions/0001-redis-streams-for-click-pipeline.md](docs/decisions/0001-redis-streams-for-click-pipeline.md)

**Producer** (`app/main.py` → `app/services/click_stream.py`):
- `_record_click()` calls `publish_click(redis, token, ts)` which does `XADD clicks:stream {"token": ..., "ts": <ISO8601 UTC>} MAXLEN ~ 100000`.
- Failures are swallowed so that a Redis hiccup never blocks the 302 redirect.

**Consumer / Worker** (`app/worker.py`):
- Runs as a separate process: `python -m app.worker`.
- On startup: `XGROUP CREATE clicks:stream click-aggregator $ MKSTREAM` (idempotent), then `XPENDING` + `XCLAIM` to reclaim orphaned entries from dead consumers.
- Main loop: `XREADGROUP click-aggregator <consumer_name> clicks:stream > COUNT <BATCH_SIZE> BLOCK 1000ms`.
- Accumulates counts in `FlushBuffer` (in-memory `{hour_bucket: {token: count}}`).
- Flush trigger: `len(pending_ids) >= BATCH_SIZE` OR `elapsed >= FLUSH_INTERVAL_SECONDS` (default 5 s).
- Flush: `HINCRBY qr:clicks:{hour} token count` for each bucket/token pair → `XACK` all entry IDs.
- Idempotency: `SET qr:clicks:dedupe:{entry_id} 1 EX 3600 NX` before counting — replayed entries (from XCLAIM) are acked and skipped.
- Handles `SIGTERM` / `SIGINT` by flushing the buffer before exit.

**Hourly flush job** (`app/jobs/flush_clicks.py`) is **unchanged**:
- Drains `qr:clicks:{YYYY-MM-DD-HH}` hash into `qr_click_stats` via rename-then-delete + `ON CONFLICT DO UPDATE`.
- Auth: `X-Internal-Token` header against `settings.INTERNAL_TOKEN`.
- Production: triggered by Cloud Scheduler every hour at :05.

### Testing
Tests use FastAPI `TestClient` with a separate SQLite DB (`test_qr_codes.db`). `tests/conftest.py` overrides the `get_db` dependency so tests never touch the dev database. Each test gets a fresh schema via `create_all` / `drop_all`.

### Production Infrastructure (GCP)
- **Cloud Run**: Stateless container, port 8080, single uvicorn process (no `--workers`)
- **Cloud SQL**: PostgreSQL 15 via Cloud SQL Python Connector (`pg8000` driver), public IP. Selected when `INSTANCE_CONNECTION_NAME` env var is set; otherwise `DATABASE_URL` is used directly (Docker compose / Auth Proxy).
- **Memorystore for Redis**: holds redirect URL cache, click counters, and image PNG bytes. Lives in a VPC, so Cloud Run reaches it via a Serverless VPC Access connector (`--vpc-connector`).
- **Artifact Registry**: Docker images stored in `asia-east1-docker.pkg.dev/<PROJECT_ID>/qr-repo/`

### Image Cache
QR PNG bytes are cached directly in Redis — no GCS, no disk, no CDN.
- Key: `qr:img:{spec_hash}:{url_hash16}` (content-addressed; same `(url, spec)` always maps to byte-identical PNG)
- TTL: 7 days
- On cache miss: regenerate via `generate_qr_image(url, image_spec)` and `setex`. QR generation is ~10-20 ms CPU.
- API response sets `Cache-Control: public, max-age=300, must-revalidate` — short TTL because the response URL is token-based, not content-addressed, so URL updates would otherwise serve stale browser cache.
- Recommend `maxmemory-policy=allkeys-lru` on Memorystore. Average PNG ~1-3 KB, so 50 K cached entries ≈ 150 MB.

### Connection Pool
Sized for `db-f1-micro` (~25 max_connections):
- `pool_size=1, max_overflow=2, pool_timeout=10, pool_recycle=300, pool_pre_ping=True`
- Per-instance budget: 3 connections
- **Cloud Run `--max-instances=6`** → max 18 conn for app, leaves ~7 for admin / hourly flush job / psql
- The Connector is a lazy module-level singleton in `app/database.py`. **Never add `--workers > 1`** to uvicorn without moving Connector init into FastAPI lifespan — the Connector's background thread does not survive `fork()`.

### Recommended Cloud Run deploy flags
```bash
gcloud run deploy qr-code-generator \
  --image asia-east1-docker.pkg.dev/<PROJECT>/qr-repo/qr-code-generator \
  --region asia-east1 \
  --max-instances=6 --min-instances=0 \
  --concurrency=80 --cpu=1 --memory=512Mi \
  --vpc-connector=qr-connector \
  --service-account=qr-runtime@<PROJECT>.iam.gserviceaccount.com \
  --set-env-vars=ENVIRONMENT=production,INSTANCE_CONNECTION_NAME=<PROJECT>:asia-east1:<INSTANCE>,DB_USER=qrapp,DB_NAME=qrdb,CLOUD_SQL_IP_TYPE=PUBLIC,REDIS_URL=redis://<MEMORYSTORE_IP>:6379/0 \
  --set-secrets=DB_PASS=qr-db-pass:latest,SERVER_SECRET=qr-server-secret:latest,INTERNAL_TOKEN=qr-internal-token:latest
```
- The Connector replaces Unix socket — **do not** pass `--add-cloudsql-instances`.
- Service account needs `roles/cloudsql.client` and `roles/redis.editor`.
- DB password and `INTERNAL_TOKEN` live in Secret Manager.
- See [README.md](README.md) for the full step-by-step deploy including Memorystore + VPC connector + Cloud Scheduler setup.

### Running migrations against production
Use the [Cloud SQL Auth Proxy](https://cloud.google.com/sql/docs/postgres/sql-proxy) from a dev box / CI rather than the runtime Connector:
```bash
cloud-sql-proxy <PROJECT>:asia-east1:<INSTANCE> &
DATABASE_URL='postgresql+psycopg2://qrapp:***@127.0.0.1:5432/qrdb' alembic upgrade head
```
This keeps `psycopg2-binary` in `requirements.txt` as the migration driver. Alembic's `env.py` errors out if `INSTANCE_CONNECTION_NAME` is set with a SQLite `DATABASE_URL` to catch operator mistakes.
