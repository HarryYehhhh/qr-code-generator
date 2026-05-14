# QR Code Generator

A QR code management API service. FastAPI backend with dynamic redirect links, click tracking, and customisable QR images. Redis-backed for fast URL cache, async click counters, and PNG byte caching. Local dev runs on SQLite; production runs on Cloud Run + Cloud SQL + Memorystore.

## Tech Stack

**Backend**
- **Framework**: FastAPI + Uvicorn (single process — no `--workers`)
- **ORM**: SQLAlchemy 2.0
- **Migrations**: Alembic
- **Validation**: Pydantic v2
- **QR Generation**: qrcode + Pillow
- **Database**: SQLite (local) / Cloud SQL PostgreSQL via Cloud SQL Python Connector (production)
- **Cache & async work**: Redis — URL cache, hourly click counter, PNG byte cache
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
    API -->|metadata| DB
    API -->|URL cache, clicks, PNG bytes| Redis
```

### Production (GCP)

```mermaid
graph LR
    Client[Client / Browser]
    CR[Cloud Run :8080]
    SQL[(Cloud SQL<br/>PostgreSQL)]
    Mem[(Memorystore<br/>Redis)]
    Sched[Cloud Scheduler]

    Client -->|API request| CR
    CR -->|Cloud SQL Connector<br/>pg8000| SQL
    CR -->|cache + clicks| Mem
    Sched -->|hourly flush| CR
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
| `GET` | `/r/{token}` | 302 redirect to target URL (also fires `HINCRBY` click counter) | 302 / 410 |
| `POST` | `/internal/flush_clicks` | Drain hourly click counter to DB (Cloud Scheduler trigger, `X-Internal-Token` header) | 200 |

`click_count` and `last_clicked_at` are eventually consistent — they lag up to 1 hour behind real traffic, populated by the hourly flush job.

### Image Query Parameters

| Param | Default | Range |
|-------|---------|-------|
| `dimension` | 256 | 32–2048 |
| `color` | `#000000` | 6-digit hex |
| `border` | 4 | 0–20 |

## Quick Start

```bash
# Redis (required for URL cache, click counters, image bytes)
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

# 7. Check click count (will be 0 — clicks are buffered in Redis)
curl -s http://localhost:8000/v1/qr_code/aBcDeFgHiJ | python3 -m json.tool
# → click_count: 0 (expected — see "Flush clicks" below)
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

### Flushing click counts (local)

Redirect clicks are buffered in Redis (`qr:clicks:{YYYY-MM-DD-HH}`) and only written to DB by the flush job. In production, Cloud Scheduler calls this hourly. Locally, trigger it manually:

```bash
# Set INTERNAL_TOKEN in .env first, e.g. INTERNAL_TOKEN=dev-token
curl -X POST http://localhost:8000/internal/flush_clicks \
  -H "X-Internal-Token: dev-token"

# Now click_count should reflect redirect visits
curl -s http://localhost:8000/v1/qr_code/aBcDeFgHiJ | python3 -m json.tool
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

# 4. Verify click flush (Cloud Scheduler runs at :05 every hour)
#    Or trigger manually:
curl -X POST https://<CLOUD_RUN_URL>/internal/flush_clicks \
  -H "X-Internal-Token: <INTERNAL_TOKEN>"

# 5. Confirm click_count updated
curl -s https://<CLOUD_RUN_URL>/v1/qr_code/<TOKEN> | python3 -m json.tool
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
  cloudscheduler.googleapis.com \
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
openssl rand -hex 32 | gcloud secrets create qr-internal-token --data-file=-
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
  --set-secrets="DB_PASS=qr-db-pass:latest,SERVER_SECRET=qr-server-secret:latest,INTERNAL_TOKEN=qr-internal-token:latest"
```

The runtime service account needs `roles/cloudsql.client` and `roles/redis.editor`.

> Don't pass `--add-cloudsql-instances` — the Cloud SQL Python Connector replaces the Unix-socket transport.

### 7. Schedule the hourly click flush

```bash
gcloud scheduler jobs create http qr-flush-clicks \
  --location=asia-east1 \
  --schedule="5 * * * *" \
  --uri="https://<CLOUD_RUN_URL>/internal/flush_clicks" \
  --http-method=POST \
  --headers="X-Internal-Token=<INTERNAL_TOKEN>"
```

### 8. Verify

```bash
curl -X POST https://<CLOUD_RUN_URL>/v1/qr_code \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'
```

### Cleanup

```bash
gcloud scheduler jobs delete qr-flush-clicks --location=asia-east1 --quiet
gcloud run services delete qr-code-generator --region=asia-east1 --quiet
gcloud redis instances delete qr-redis --region=asia-east1 --quiet
gcloud compute networks vpc-access connectors delete qr-connector --region=asia-east1 --quiet
gcloud sql instances delete qr-db --quiet
gcloud artifacts repositories delete qr-repo --location=asia-east1 --quiet
```

## Architecture (Sprint A)

Sprint A splits click counting into an async producer / consumer pipeline backed by Redis Streams.

### Topology

```mermaid
graph LR
    Client[Client / Browser]
    API[API :8080\nFastAPI producer]
    Stream[(clicks:stream\nRedis Stream)]
    Worker[Worker\nconsumer group click-aggregator]
    Hash[(qr:clicks:YYYY-MM-DD-HH\nRedis Hash)]
    FlushJob[Hourly flush job\n/internal/flush_clicks]
    DB[(PostgreSQL\nqr_click_stats)]

    Client -->|GET /r/{token}| API
    API -->|XADD token ts| Stream
    Worker -->|XREADGROUP| Stream
    Worker -->|HINCRBY| Hash
    FlushJob -->|HSCAN + RENAME| Hash
    FlushJob -->|INSERT ON CONFLICT| DB
```

**Key design points:**
- `_record_click()` in `app/main.py` does `XADD clicks:stream … MAXLEN ~ 100000` — the 302 redirect never touches a hash or DB.
- Worker (`app/worker.py`) runs as a separate process, reads via `XREADGROUP`, accumulates an in-memory dict, and flushes to `qr:clicks:{hour}` hashes every `BATCH_SIZE` entries or `FLUSH_INTERVAL_SECONDS` seconds (whichever comes first).
- Crash recovery: on startup, worker calls `XPENDING` + `XCLAIM` to reclaim entries left by a dead consumer.
- Idempotency: `SET qr:clicks:dedupe:{entry_id} 1 EX 3600 NX` prevents double-counting when an entry is replayed via `XCLAIM`.
- The existing hourly flush job (`/internal/flush_clicks`) and `qr_click_stats` schema are unchanged — they drain the same `qr:clicks:{hour}` hashes the worker fills.

Decision rationale: [docs/decisions/0001-redis-streams-for-click-pipeline.md](docs/decisions/0001-redis-streams-for-click-pipeline.md)

### Local quick-start (Docker Compose)

Starts all four services — `api`, `worker`, `redis`, `postgres` — with one command:

```bash
docker-compose up
```

The api is exposed at `http://localhost:8000`. The worker runs as a separate container sharing the same image, with `command: python -m app.worker` overriding the default Dockerfile CMD.

```bash
# Create a QR code and test redirect
curl -s -X POST http://localhost:8000/v1/qr_code \
  -H "Content-Type: application/json" -d '{"url": "https://example.com"}'
# → {"qr_token": "aBcDeFgHiJ"}

curl -o /dev/null -w "%{http_code}" http://localhost:8000/r/aBcDeFgHiJ
# → 302 (worker will aggregate the click within ~5 s)

# Inspect click hash in Redis
docker-compose exec redis redis-cli HGETALL qr:clicks:$(date -u +%Y-%m-%d-%H)
```

**Resilience test:**
```bash
# Kill the worker mid-flight
docker-compose kill worker

# Restart — XCLAIM reclaims orphaned entries
docker-compose up -d worker

# Click counts are eventually consistent; no lost or double-counted clicks
```

## Key Design Decisions

- **Soft delete** — records are never physically removed; all queries filter `status == 'active'`
- **302 redirect** — allows URL updates to take effect immediately
- **Async click counting (Sprint A)** — redirect path publishes `XADD clicks:stream … MAXLEN ~ 100000` (producer). A separate worker process (`app/worker.py`) reads via `XREADGROUP`, aggregates in memory, and flushes to `qr:clicks:{hour}` hashes. A Cloud Scheduler hourly cron drains the hashes into `qr_click_stats` via `/internal/flush_clicks`. Idempotent: rename-then-delete + `ON CONFLICT DO UPDATE` with additive merge. Crash recovery via `XCLAIM`; double-count protection via per-entry dedupe key.
- **URL cache pre-warm** — `POST /v1/qr_code` writes `qr:url:{token}` to Redis (TTL 24h), so the first redirect after creation is already a cache hit. Cache is invalidated on update / delete.
- **Image cache** — PNG bytes live in Redis under content-addressed key `qr:img:{spec_hash}:{url_hash16}`, TTL 7 days. Cache miss regenerates in process (~10–20 ms CPU). No GCS, no CDN, no disk.
- **Connection pool sized for db-f1-micro** — `pool_size=1, max_overflow=2, pool_recycle=300, pool_pre_ping=True`. Combined with Cloud Run `--max-instances=6`, app stays under the ~25 connection ceiling with headroom for admin / migrations / flush job.
- **Cloud SQL Python Connector** — production transport (pg8000 driver). Activated when `INSTANCE_CONNECTION_NAME` is set. Migrations stay on `psycopg2-binary` via Cloud SQL Auth Proxy from a dev box / CI.
- **Token generation** — `SHA-256(url + random_nonce + SERVER_SECRET)` → first 10 Base62 chars. Retries up to 5 times on UNIQUE collision.

For deeper architecture and rationale, see [CLAUDE.md](CLAUDE.md).
