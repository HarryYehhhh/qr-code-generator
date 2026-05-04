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

# Frontend setup & dev server (port 5173)
cd frontend && npm install && npm run dev

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

QR Code Generator using FastAPI backend + Vue 3 / TypeScript frontend, with dual-environment support: SQLite + local files (local dev) and Cloud SQL PostgreSQL + Cloud Storage + CDN (production on Cloud Run).

### ENV Switch Pattern
`ENVIRONMENT` in `app/config.py` controls all behavior switching:
- **`"local"`**: SQLite DB, `LocalStorage` writes to disk, FastAPI StaticFiles mount at `/static`, image URLs use `BASE_URL/static/...`
- **`"production"`**: Cloud SQL PostgreSQL via Unix socket, `GCSStorage` uploads to GCS bucket, image URLs use `CDN_BASE_URL/...`

Storage backend is swapped via factory pattern in `app/storage/factory.py`. GCS import is **lazy** (inside the production branch) so local dev doesn't require `google-cloud-storage` to be installed. Database `connect_args` is also conditional — `check_same_thread=False` only applies to SQLite.

### Request Flow
1. **Router** (`app/routers/qr.py`, prefix `/v1`) validates input via Pydantic schemas
2. **Service layer** (`app/services/qr_service.py`) orchestrates business logic
3. **Token service** generates Base62 tokens: `SHA-256(url + random_nonce + SERVER_SECRET)` → first 10 Base62 chars. Retries up to 5 times on UNIQUE constraint collision.
4. **Image service** generates QR PNGs with customizable spec (dimension/color/border). Uses `spec_hash` as cache key — stored at `qr/{qr_token}/{spec_hash}.png`.
5. **Storage layer** (`app/storage/`) abstracts file I/O behind `StorageBackend` interface with path-based methods (`save`, `get`, `delete`, `exists`).

### Soft Delete
DELETE endpoint sets `status='deleted'` + `deleted_at` timestamp. All queries filter `status == 'active'`. Rows are never physically removed.

### Redirect Route
`GET /r/{qr_token}` in `app/main.py` returns 302 (not 301) to allow URL updates to take effect. Atomically increments `click_count` and updates `last_clicked_at` on each redirect. Registered **after** `/v1` router to avoid path conflicts.

### API Contracts
- `POST /v1/qr_code` → `{"qr_token": "..."}` (201)
- `GET /v1/qr_codes` → `[{qr_token, url, click_count, status, created_at}]` (200)
- `GET /v1/qr_code/{token}` → `{url, click_count, status, created_at}` (200) or 410 if deleted
- `PUT /v1/qr_code/{token}` → 204
- `DELETE /v1/qr_code/{token}` → 204 (soft delete)
- `GET /v1/qr_code_image/{token}?dimension=&color=&border=` → `{"image_location": "..."}` (200)
- `GET /r/{token}` → 302 redirect

### Frontend (Vue 3 + TypeScript)
Lives in `frontend/`. Vite dev server (port 5173) proxies `/v1`, `/static`, `/r` to the backend at port 8000 — this proxy config must stay in sync with actual backend routes. **Restart Vite after changing `vite.config.ts`** (proxy changes are not hot-reloaded). `shortUrl` in `QRCodeDisplay.vue` is hardcoded to `localhost:8000` — update this for production.

### Schema Migrations
Alembic is the source of truth for production schema. `alembic/env.py` reads `settings.DATABASE_URL` and imports `app.models` so `--autogenerate` sees all tables. The lifespan in `app/main.py` only runs `create_all` on SQLite — PostgreSQL must be migrated explicitly via `alembic upgrade head`. Tests still use `create_all` directly because they target a transient SQLite DB.

### Click Stats Pipeline
Per-redirect counter writes go to Redis (`qr:clicks:{YYYY-MM-DD-HH}` hash, field=token, value=count) instead of UPDATEing `qr_codes`. A scheduled `POST /internal/flush_clicks` (Cloud Scheduler, hourly) drains the previous-hour key into the `qr_click_stats` table via `flush_previous_hour` in `app/jobs/flush_clicks.py`. Flush is idempotent: rename-then-delete + `ON CONFLICT (qr_token, hour_bucket) DO UPDATE SET click_count = ... + EXCLUDED.click_count`. Auth on the endpoint uses an `X-Internal-Token` header against `settings.INTERNAL_TOKEN`.

### Testing
Tests use FastAPI `TestClient` with a separate SQLite DB (`test_qr_codes.db`). `tests/conftest.py` overrides the `get_db` dependency so tests never touch the dev database. Each test gets a fresh schema via `create_all` / `drop_all`.

### Production Infrastructure (GCP)
- **Cloud Run**: Stateless container, port 8080, single uvicorn process (no `--workers`)
- **Cloud SQL**: PostgreSQL 15 via Cloud SQL Python Connector (`pg8000` driver), public IP. Selected when `INSTANCE_CONNECTION_NAME` env var is set; otherwise `DATABASE_URL` is used directly (Docker compose / Auth Proxy).
- **Cloud Storage**: QR images uploaded to GCS bucket
- **CDN**: Serves images publicly from GCS via Cloud CDN
- **Artifact Registry**: Docker images stored in `asia-east1-docker.pkg.dev/<PROJECT_ID>/qr-repo/`

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
  --service-account=qr-runtime@<PROJECT>.iam.gserviceaccount.com \
  --set-env-vars=ENVIRONMENT=production,INSTANCE_CONNECTION_NAME=<PROJECT>:asia-east1:<INSTANCE>,DB_USER=qrapp,DB_NAME=qrcodes,CLOUD_SQL_IP_TYPE=PUBLIC \
  --set-secrets=DB_PASS=qr-db-pass:latest,SERVER_SECRET=qr-server-secret:latest,INTERNAL_TOKEN=qr-internal-token:latest
```
- The Connector replaces Unix socket — **do not** pass `--add-cloudsql-instances`.
- Service account needs IAM role `roles/cloudsql.client`.
- DB password lives in Secret Manager.

### Running migrations against production
Use the [Cloud SQL Auth Proxy](https://cloud.google.com/sql/docs/postgres/sql-proxy) from a dev box / CI rather than the runtime Connector:
```bash
cloud-sql-proxy <PROJECT>:asia-east1:<INSTANCE> &
DATABASE_URL='postgresql+psycopg2://qrapp:***@127.0.0.1:5432/qrcodes' alembic upgrade head
```
This keeps `psycopg2-binary` in `requirements.txt` as the migration driver. Alembic's `env.py` errors out if `INSTANCE_CONNECTION_NAME` is set with a SQLite `DATABASE_URL` to catch operator mistakes.
