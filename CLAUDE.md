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

# Reset database (no migrations, just delete and restart)
rm -f qr_codes.db

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

### Testing
Tests use FastAPI `TestClient` with a separate SQLite DB (`test_qr_codes.db`). `tests/conftest.py` overrides the `get_db` dependency so tests never touch the dev database. Each test gets a fresh schema via `create_all` / `drop_all`.

### Production Infrastructure (GCP)
- **Cloud Run**: Stateless container, port 8080, env vars set via `--update-env-vars`
- **Cloud SQL**: PostgreSQL 15 via Unix socket (`/cloudsql/<connection_name>`)
- **Cloud Storage**: QR images uploaded to GCS bucket
- **CDN**: Serves images publicly from GCS via Cloud CDN
- **Artifact Registry**: Docker images stored in `asia-east1-docker.pkg.dev/<PROJECT_ID>/qr-repo/`

### Deployment Gotcha (zsh)
When setting `DATABASE_URL` via `gcloud run --set-env-vars`, use `^||^` custom delimiter or single quotes to prevent zsh from interpreting `?` in the connection string. See README.md for the full deploy command.
