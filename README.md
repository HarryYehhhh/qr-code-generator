# QR Code Generator

A focused URL-shortener / QR-image service. FastAPI backend with dynamic redirects and customisable QR images, Redis-backed for sub-ms cache lookups. Local dev runs on SQLite; production runs on Cloud Run + Cloud SQL + Memorystore. Full architecture diagram and design rationale: [`docs/architecture.md`](docs/architecture.md).

> **Scope** ([ADR-0003](docs/decisions/0003-remove-click-counting-mvp.md)): the MVP focuses on the **redirect path**. Click counting / analytics was implemented in Sprint A but removed to keep the architecture story crisp. Sprint A docs are kept as historical record of the decision.

## Tech Stack

**Backend**
- **Framework**: FastAPI + Uvicorn (single process — no `--workers`)
- **ORM**: SQLAlchemy 2.0
- **Migrations**: Alembic
- **Validation**: Pydantic v2
- **QR Generation**: qrcode + Pillow
- **Database**: SQLite (local) / Cloud SQL PostgreSQL via Cloud SQL Python Connector (production)
- **Cache**: Redis — URL cache + PNG byte cache
- **Deployment**: Docker + Cloud Run

## Architecture

### Local Development

```mermaid
graph LR
    Client[Client]
    API[FastAPI :8000]
    DB[(SQLite)]
    Redis[(Redis :6379)]

    Client -->|API request| API
    API -->|metadata (cache miss)| DB
    API -->|URL cache + PNG bytes| Redis
```

### Production (GCP)

```mermaid
graph LR
    Client[Client / Browser]
    CR[Cloud Run :8080]
    SQL[(Cloud SQL<br/>PostgreSQL)]
    Mem[(Memorystore<br/>Redis)]

    Client -->|API request| CR
    CR -->|Cloud SQL Connector<br/>pg8000| SQL
    CR -->|URL cache + PNG bytes| Mem
```

`ENVIRONMENT` and `INSTANCE_CONNECTION_NAME` together select the DB transport. Single `app/database.py` factory branches on these — see [CLAUDE.md](CLAUDE.md) for connection-pool sizing rationale.

## API Endpoints

| Method | Path | Description | Status |
|--------|------|-------------|--------|
| `POST` | `/v1/qr_code` | Create a new QR code (also pre-warms URL cache) | 201 |
| `GET` | `/v1/qr_codes` | List all QR codes | 200 |
| `GET` | `/v1/qr_code/{token}` | Get QR code metadata | 200 / 410 |
| `PUT` | `/v1/qr_code/{token}` | Update target URL (invalidates cache) | 204 |
| `DELETE` | `/v1/qr_code/{token}` | Soft delete (invalidates cache) | 204 |
| `GET` | `/v1/qr_code_image/{token}` | Returns PNG bytes (`image/png`, `Cache-Control: max-age=300`) | 200 / 404 |
| `GET` | `/r/{token}` | 302 redirect to target URL | 302 / 404 / 410 |

### Image Query Parameters

| Param | Default | Range |
|-------|---------|-------|
| `dimension` | 256 | 32–2048 |
| `color` | `#000000` | 6-digit hex |
| `border` | 4 | 0–20 |

## Quick Start

```bash
# Redis (required for URL cache + image PNG bytes)
docker run -d -p 6379:6379 --name qr-redis redis:7-alpine

# Backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

SQLite auto-creates the schema on first boot. For PostgreSQL, run `alembic upgrade head` first (see [Schema Migrations](#schema-migrations-alembic)).

For API smoke tests and full E2E flows, see [Testing](#testing).

## Schema Migrations (Alembic)

Alembic is the **source of truth** for database schema in PostgreSQL environments. Migration files live in `alembic/versions/`.

### Why Alembic matters

| Environment | Schema management | Notes |
|---|---|---|
| **SQLite (default local)** | `create_all` on startup | Auto-creates tables; no migration needed |
| **PostgreSQL (local Docker / production)** | `alembic upgrade head` | Must run manually before first request |
| **pytest** | `create_all` / `drop_all` per test | Uses transient SQLite; no migration needed |

If you use PostgreSQL locally (via Docker Compose), you **must** run migrations — the app will not auto-create tables.

### Common commands

```bash
# Apply all pending migrations
alembic upgrade head

# Create a new migration after editing app/models.py
alembic revision --autogenerate -m "describe change"

# Mark an existing DB as already at a specific revision (skip running DDL)
alembic stamp <revision>

# View current migration status
alembic current

# View migration history
alembic history
```

### First-time setup for an existing PostgreSQL DB

If the DB already has `qr_codes` table (e.g. created by an older version), Alembic will fail with `DuplicateTable`. Fix:

```bash
# Tell Alembic the baseline migration is already applied
alembic stamp 0001_baseline

# Then apply remaining migrations
alembic upgrade head
```

## Testing

### Unit tests (pytest)

```bash
pytest tests/ -v
```

Uses FastAPI `TestClient` + isolated SQLite test DB + `fakeredis`. No GCP credentials required.

### Local E2E testing (SQLite)

Simplest path — no Docker needed (except Redis):

```bash
# 1. Start Redis
docker run -d -p 6379:6379 --name qr-redis redis:7-alpine

# 2. Use default .env (SQLite + Redis on 6379)
cp .env.example .env

# 3. Start server (SQLite auto-creates tables)
uvicorn app.main:app --reload --port 8000

# 4. Create a QR code
curl -s -X POST http://localhost:8000/v1/qr_code \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'
# → {"qr_token": "aBcDeFgHiJ"}

# 5. Get QR image
curl -s -o qr.png "http://localhost:8000/v1/qr_code_image/aBcDeFgHiJ?dimension=256"
# → PNG saved to qr.png (image bytes from Redis cache or generated on the fly)

# 6. Test redirect (should 302 → https://example.com)
curl -s -o /dev/null -w "%{http_code} → %{redirect_url}" http://localhost:8000/r/aBcDeFgHiJ
# → 302 → https://example.com

# 7. Inspect QR metadata
curl -s http://localhost:8000/v1/qr_code/aBcDeFgHiJ | python3 -m json.tool
# → {"url": "...", "status": "active", "created_at": "..."}
```

### Local E2E testing (PostgreSQL via Docker Compose)

Use this when you want to match production behaviour:

```bash
# 1. Start PostgreSQL + Redis (adjust port mapping in docker-compose as needed)
#    Ensure env/.env points to the correct PostgreSQL and Redis ports

# 2. Run Alembic migrations
DATABASE_URL="postgresql+psycopg2://<user>:<pass>@localhost:<port>/<db>" \
  alembic upgrade head

# 3. Start server
uvicorn app.main:app --reload --port 8000

# 4. Same curl commands as above
```

### Production E2E testing

```bash
# 1. Apply migrations via Cloud SQL Auth Proxy
cloud-sql-proxy <PROJECT_ID>:asia-east1:qr-db &
DATABASE_URL="postgresql+psycopg2://qrapp:<DB_PASSWORD>@127.0.0.1:5432/qrdb" \
  alembic upgrade head

# 2. Create a QR code
curl -s -X POST https://<CLOUD_RUN_URL>/v1/qr_code \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'

# 3. Test redirect
curl -s -o /dev/null -w "%{http_code} → %{redirect_url}" \
  https://<CLOUD_RUN_URL>/r/<TOKEN>
```

## Production Deployment (GCP)

Prerequisites: [gcloud CLI](https://cloud.google.com/sdk/docs/install), GCP project with billing enabled.

### 1. Enable APIs

```bash
gcloud projects create <PROJECT_ID>
gcloud config set project <PROJECT_ID>
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  redis.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  vpcaccess.googleapis.com
```

### 2. Create Cloud SQL (PostgreSQL)

```bash
gcloud sql instances create qr-db \
  --database-version=POSTGRES_15 \
  --tier=db-f1-micro \
  --region=asia-east1

gcloud sql databases create qrdb --instance=qr-db
gcloud sql users create qrapp --instance=qr-db --password=<DB_PASSWORD>
```

### 3. Create Memorystore for Redis

```bash
gcloud redis instances create qr-redis \
  --size=1 --region=asia-east1 \
  --redis-version=redis_7_0
# Note the resulting host IP for REDIS_URL below.
```

Memorystore lives inside a VPC, so Cloud Run needs a Serverless VPC Access connector to reach it:

```bash
gcloud compute networks vpc-access connectors create qr-connector \
  --region=asia-east1 --network=default --range=10.8.0.0/28
```

### 4. Apply migrations via Cloud SQL Auth Proxy

```bash
# Run from a dev box / CI — not from Cloud Run
cloud-sql-proxy <PROJECT_ID>:asia-east1:qr-db &
DATABASE_URL="postgresql+psycopg2://qrapp:<DB_PASSWORD>@127.0.0.1:5432/qrdb" \
  alembic upgrade head
```

### 5. Store secrets

```bash
echo -n "<DB_PASSWORD>" | gcloud secrets create qr-db-pass --data-file=-
openssl rand -hex 32 | gcloud secrets create qr-server-secret --data-file=-
```

### 6. Build & deploy

```bash
gcloud artifacts repositories create qr-repo \
  --repository-format=docker --location=asia-east1

gcloud builds submit \
  --tag asia-east1-docker.pkg.dev/<PROJECT_ID>/qr-repo/qr-code-generator

gcloud run deploy qr-code-generator \
  --image asia-east1-docker.pkg.dev/<PROJECT_ID>/qr-repo/qr-code-generator \
  --region asia-east1 \
  --allow-unauthenticated \
  --max-instances=6 --min-instances=0 \
  --concurrency=80 --cpu=1 --memory=512Mi \
  --vpc-connector=qr-connector \
  --service-account=qr-runtime@<PROJECT_ID>.iam.gserviceaccount.com \
  --set-env-vars="ENVIRONMENT=production,INSTANCE_CONNECTION_NAME=<PROJECT_ID>:asia-east1:qr-db,DB_USER=qrapp,DB_NAME=qrdb,CLOUD_SQL_IP_TYPE=PUBLIC,REDIS_URL=redis://<MEMORYSTORE_IP>:6379/0" \
  --set-secrets="DB_PASS=qr-db-pass:latest,SERVER_SECRET=qr-server-secret:latest"
```

The runtime service account needs `roles/cloudsql.client` and `roles/redis.editor`.

> Don't pass `--add-cloudsql-instances` — the Cloud SQL Python Connector replaces the Unix-socket transport.

### 7. Verify

```bash
curl -X POST https://<CLOUD_RUN_URL>/v1/qr_code \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'
```

### Cleanup

```bash
gcloud run services delete qr-code-generator --region=asia-east1 --quiet
gcloud redis instances delete qr-redis --region=asia-east1 --quiet
gcloud compute networks vpc-access connectors delete qr-connector --region=asia-east1 --quiet
gcloud sql instances delete qr-db --quiet
gcloud artifacts repositories delete qr-repo --location=asia-east1 --quiet
```

## Local quick-start (Docker Compose)

```bash
docker compose up -d
docker compose exec api alembic upgrade head    # one-time, creates Postgres schema

# Test redirect
curl -s -X POST http://localhost:8000/v1/qr_code \
  -H "Content-Type: application/json" -d '{"url": "https://example.com"}'
# → {"qr_token": "aBcDeFgHiJ"}

curl -o /dev/null -w "%{http_code} → %{redirect_url}\n" http://localhost:8000/r/aBcDeFgHiJ
# → 302 → https://example.com
```

## Local quick-start (Docker Compose) — note

The compose stack now boots only `api + postgres + redis` ([ADR-0004](docs/decisions/0004-simplify-observability-keep-structlog.md) removed the Jaeger / Prometheus / Grafana sidecars). Application logs are JSON on stdout — `docker compose logs api` to view them.

## Performance

Cache-hot redirects (`GET /r/{token}`) serve from Redis with minimal DB involvement — see [`docs/perf-report.md`](docs/perf-report.md) for the full load-test results (RPS / p50 / p95 / p99 across three k6 scenarios: redirect_hot, redirect_cold, image_mixed).

To run a load test locally:

```bash
# Seed tokens, then run the hot-path scenario
k6 run --env BASE_URL=http://localhost:8000 scripts/k6/seed.js \
  2>/dev/null | grep "^TOKEN:" | sed 's/^TOKEN://' | jq -s '.' > scripts/k6/tokens.json
k6 run --env BASE_URL=http://localhost:8000 scripts/k6/redirect_hot.js
```

See [`scripts/k6/README.md`](scripts/k6/README.md) for full instructions.

## Observability

Sprint B originally shipped a full three-pillar stack — OpenTelemetry tracing (Jaeger local / Cloud Trace prod), Prometheus + Grafana metrics, structlog JSON logs. **[ADR-0004](docs/decisions/0004-simplify-observability-keep-structlog.md) removed the OTel + Prometheus halves** because for this single-process service they were ~5–10 % overhead with marginal value: traces only matter across services (we have one), and Cloud Run's built-in console already covers latency / RPS / error rate without any code.

What remains:

- **`structlog` JSON logs to stdout** (`app/logging.py`). On Cloud Run, Cloud Logging parses every line natively — searchable, alertable, and the source for log-based metrics if needed later.
- **Cloud Run console** for latency p50/p95/p99 + error rate + concurrency + container CPU/memory + instance count, no SDK required.

```bash
# Inspect structured logs locally:
docker compose logs api | jq .
```

If multi-service tracing is ever needed, re-introduce OTel SDK with a single Cloud Trace exporter — see ADR-0004 for the recommended approach.

## Key Design Decisions

- **Soft delete** — records are never physically removed; all queries filter `status == 'active'`
- **302 redirect** — allows URL updates to take effect immediately
- **Click counting removed (ADR-0003)** — Sprint A originally implemented an async click pipeline (Redis Streams + worker). It was removed to keep the MVP focused on the redirect path. See [ADR-0003](docs/decisions/0003-remove-click-counting-mvp.md) for rationale and the recommended modern shape (Cloud CDN logs → Pub/Sub → BigQuery) when this gets re-added.
- **URL cache pre-warm** — `POST /v1/qr_code` writes `qr:url:{token}` to Redis (TTL 24h), so the first redirect after creation is already a cache hit. Cache is invalidated on update / delete.
- **Image cache** — PNG bytes live in Redis under content-addressed key `qr:img:{spec_hash}:{url_hash16}`, TTL 7 days. Cache miss regenerates in process (~10–20 ms CPU). No GCS, no CDN, no disk.
- **Connection pool sized for db-f1-micro** — `pool_size=1, max_overflow=2, pool_recycle=300, pool_pre_ping=True`. Combined with Cloud Run `--max-instances=6`, app stays under the ~25 connection ceiling.
- **Cloud SQL Python Connector** — production transport (pg8000 driver). Activated when `INSTANCE_CONNECTION_NAME` is set. Migrations stay on `psycopg2-binary` via Cloud SQL Auth Proxy from a dev box / CI.
- **Token generation** — `SHA-256(url + random_nonce + SERVER_SECRET)` → first 10 Base62 chars. Retries up to 5 times on UNIQUE collision.

For deeper architecture and rationale, see [CLAUDE.md](CLAUDE.md).
