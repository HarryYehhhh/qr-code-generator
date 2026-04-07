# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# Run dev server
uvicorn app.main:app --reload --port 8000

# Reset database (no migrations, just delete and restart)
rm -f qr_codes.db

# Test endpoints
curl -X POST http://localhost:8000/v1/qr_code -H "Content-Type: application/json" -d '{"url": "https://example.com"}'
curl http://localhost:8000/v1/qr_code/{qr_token}
curl "http://localhost:8000/v1/qr_code_image/{qr_token}?dimension=256&color=%23000000&border=4"

# Docker (production image)
docker build -t qr-code-generator .
docker run -p 8080:8080 --env-file .env.prod qr-code-generator

# Deploy to Cloud Run
gcloud builds submit --tag gcr.io/<PROJECT_ID>/qr-code-generator
gcloud run deploy qr-code-generator --image gcr.io/<PROJECT_ID>/qr-code-generator --region asia-east1
```

## Architecture

QR Code Generator using FastAPI, with dual-environment support: SQLite + local files (local dev) and Cloud SQL PostgreSQL + Cloud Storage + CDN (production on Cloud Run).

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
`GET /{qr_token}` in `app/main.py` returns 302 (not 301) to allow URL updates to take effect. Updates `last_clicked_at` on each redirect. Registered **after** `/v1` router to avoid path conflicts.

### API Contracts (from plan.md)
- `POST /v1/qr_code` → `{"qr_token": "..."}` (201)
- `GET /v1/qr_code/{token}` → `{"url": "..."}` (200)
- `PUT /v1/qr_code/{token}` → 204
- `DELETE /v1/qr_code/{token}` → 204 (soft delete)
- `GET /v1/qr_code_image/{token}?dimension=&color=&border=` → `{"image_location": "..."}` (200)
- `GET /{token}` → 302 redirect

### Production Infrastructure (GCP)
- **Cloud Run**: Stateless container, port 8080, env vars set via `--set-env-vars`
- **Cloud SQL**: PostgreSQL 15 via Unix socket (`/cloudsql/<connection_name>`)
- **Cloud Storage**: QR images uploaded to GCS bucket
- **CDN**: Serves images publicly from GCS via Cloud CDN
