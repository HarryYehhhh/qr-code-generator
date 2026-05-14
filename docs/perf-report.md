# Performance Report

> 本報告數字由使用者於本地實跑 k6 後填入。每個 `<TBD: ...>` 旁邊已標註「Run:」執行指令與「Source:」對應指標。
> 實作骨架版本：Sprint C（2026-05-14）。Baseline commit：6566794（pre-Sprint-A snapshot）。

---

## 1. Environment

| Field | Value | How to fill |
| --- | --- | --- |
| Machine | `<TBD: CPU model / RAM>` | Run: `sysctl -n machdep.cpu.brand_string && sysctl -n hw.memsize` (macOS) or `lscpu && free -h` (Linux) |
| Docker resources | `<TBD: CPUs / Memory>` | Run: `docker info \| grep -E 'CPUs\|Total Memory'` |
| Git SHA (current) | `<TBD: short SHA>` | Run: `git rev-parse --short HEAD` |
| Git SHA (baseline) | `6566794` | Pre-Sprint-A snapshot |
| k6 version | `<TBD: vX.Y.Z>` | Run: `docker run --rm grafana/k6 version` |
| Docker Compose version | `<TBD: vX.Y.Z>` | Run: `docker compose version` |
| OS | `<TBD: Darwin/Linux vX>` | Run: `uname -srm` |

---

## 2. Scenarios

### 2.1 redirect_hot

- **Purpose**: Measure cache-hot redirect throughput ceiling. All tokens pre-exist in Redis URL cache — no DB fallback occurs. This is the best-case latency for `GET /r/{token}`.
- **Run**: `docker-compose --profile loadtest run --rm k6 run /scripts/redirect_hot.js`
  - Source: `scripts/k6/redirect_hot.js` — stages: warm-up 30s → steady 2m → spike 30s → ramp-down 30s
- **Thresholds**: `http_req_failed < 1%`, `http_req_duration{p(99)} < 500ms` (see `scripts/k6/redirect_hot.js` for adjustment guidance)

| Metric | Value | Source |
| --- | --- | --- |
| RPS (steady state) | `<TBD: rps>` | Source: Prometheus query `sum(rate(http_requests_total{handler="/r/{qr_token}"}[1m]))` / Grafana panel: "Redirect QPS" |
| p50 latency (ms) | `<TBD: ms>` | Source: Grafana panel "Redirect duration p50/p95/p99" — query `histogram_quantile(0.50, sum(rate(http_request_duration_seconds_bucket{handler="/r/{qr_token}"}[1m])) by (le)) * 1000` |
| p95 latency (ms) | `<TBD: ms>` | Source: same Grafana panel, quantile=0.95 |
| p99 latency (ms) | `<TBD: ms>` | Source: Prometheus `histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{handler="/r/{qr_token}"}[1m])) by (le)) * 1000` |
| Error rate (%) | `<TBD: %>` | Source: Prometheus `rate(http_req_failed[1m])` — also available in k6 end-of-test summary |
| DB CPU (%) | `<TBD: %>` | Run: `docker stats db --no-stream --format "{{.CPUPerc}}"` during the test |
| Redis ops/s | `<TBD: ops>` | Run: `docker-compose exec redis redis-cli info stats \| grep instantaneous_ops_per_sec` |
| Cache hit rate | `<TBD: ~100%>` | Source: Prometheus `rate(qr_redirect_total{cache_result="hit"}[5m]) / rate(qr_redirect_total[5m])` / Grafana panel: "Redirect rate by cache_result" (expected ~100% for hot scenario) |

### 2.2 redirect_cold

- **Purpose**: Measure DB-fallback + SETEX path latency. Each VU iteration creates a new token (Postgres INSERT) and immediately redirects, exercising the full write path.
- **Run**: `docker-compose --profile loadtest run --rm k6 run /scripts/redirect_cold.js`
  - Source: `scripts/k6/redirect_cold.js` — constant-vus: 50 VUs for 3 minutes
- **Thresholds**: `http_req_failed < 1%`, `http_req_duration{p(99)} < 1500ms` (looser than hot path to account for DB write latency)

| Metric | Value | Source |
| --- | --- | --- |
| RPS (steady state) | `<TBD: rps>` | Source: Prometheus `sum(rate(http_requests_total{handler="/r/{qr_token}"}[1m]))` / Grafana panel: "Redirect QPS" |
| p50 latency (ms) | `<TBD: ms>` | Source: Grafana panel "Redirect duration p50/p95/p99" |
| p95 latency (ms) | `<TBD: ms>` | Source: same panel, quantile=0.95 |
| p99 latency (ms) | `<TBD: ms>` | Source: Prometheus histogram_quantile(0.99, ...) |
| Error rate (%) | `<TBD: %>` | Source: k6 end-of-test summary `http_req_failed` rate |
| DB CPU (%) | `<TBD: %>` | Run: `docker stats db --no-stream --format "{{.CPUPerc}}"` during test |
| Redis ops/s | `<TBD: ops>` | Run: `docker-compose exec redis redis-cli info stats \| grep instantaneous_ops_per_sec` |
| DB pool in use | `<TBD: 0-3>` | Source: Prometheus `qr_db_pool_in_use` / Grafana panel: "qr_db_pool_in_use" (max=3 on db-f1-micro sizing) |

### 2.3 image_mixed

- **Purpose**: Measure QR image serving under 50/50 cache hit/miss load. Cache-miss path triggers `qrcode` lib CPU (~10-20ms per PNG). Monitors `GET /v1/qr_code_image/{token}`.
- **Run**: `docker-compose --profile loadtest run --rm k6 run /scripts/image_mixed.js`
  - Source: `scripts/k6/image_mixed.js` — ramping-vus: warm-up 30s → 100 VUs for 2m → ramp-down 30s
- **Thresholds**: `http_req_failed < 1%`, `http_req_duration{p(95)} < 800ms`

| Metric | Value | Source |
| --- | --- | --- |
| RPS (steady state) | `<TBD: rps>` | Source: Prometheus `sum(rate(http_requests_total{handler="/v1/qr_code_image/{qr_token}"}[1m]))` |
| p50 latency (ms) | `<TBD: ms>` | Source: Grafana panel "Redirect duration p50/p95/p99" (or create an image-specific panel) |
| p95 latency (ms) | `<TBD: ms>` | Source: same panel, quantile=0.95 |
| p99 latency (ms) | `<TBD: ms>` | Source: Prometheus histogram_quantile(0.99, ...) for image handler |
| Error rate (%) | `<TBD: %>` | Source: k6 end-of-test summary `http_req_failed` rate |
| API container CPU (%) | `<TBD: %>` | Run: `docker stats api --no-stream --format "{{.CPUPerc}}"` during test |
| Image cache hit rate (%) | `<TBD: ~50%>` | Source: Prometheus `rate(qr_image_cache_total{result="hit"}[5m]) / rate(qr_image_cache_total[5m])` / Grafana panel: "Image cache hit rate" (expected ~50% with random spec) |
| Redis ops/s | `<TBD: ops>` | Run: `docker-compose exec redis redis-cli info stats \| grep instantaneous_ops_per_sec` |
| QR generate CPU time (ms) | `<TBD: ms>` | Source: Jaeger trace — look for `image_service.generate` span duration on cache-miss requests |

---

## 3. Baseline vs Current

- **Baseline checkout**: `git checkout 6566794 && docker-compose up -d --build`
  - Run: Apply baseline with `alembic upgrade head` if using Postgres, then seed tokens.
- **Current checkout**: `git checkout main && docker-compose up -d --build`
- **Process**: Run each scenario twice (once per checkout), fill columns below.

| Scenario | Metric | Baseline (6566794) | Current (main) | Delta |
| --- | --- | --- | --- | --- |
| redirect_hot | RPS | `<TBD>` | `<TBD>` | `<TBD>` |
| redirect_hot | p99 (ms) | `<TBD>` | `<TBD>` | `<TBD>` |
| redirect_hot | error rate (%) | `<TBD>` | `<TBD>` | `<TBD>` |
| redirect_cold | RPS | `<TBD>` | `<TBD>` | `<TBD>` |
| redirect_cold | p99 (ms) | `<TBD>` | `<TBD>` | `<TBD>` |
| redirect_cold | error rate (%) | `<TBD>` | `<TBD>` | `<TBD>` |
| image_mixed | RPS | `<TBD>` | `<TBD>` | `<TBD>` |
| image_mixed | p99 (ms) | `<TBD>` | `<TBD>` | `<TBD>` |
| image_mixed | cache hit rate (%) | `<TBD>` | `<TBD>` | `<TBD>` |

**Note on baseline**: commit `6566794` predates Sprint A (no Redis Streams, no worker process). The baseline docker-compose may not include the worker service. Redirect click counting will use the old `HINCRBY` path directly.

---

## 4. Bottleneck Analysis

### redirect_hot

- **Expected bottleneck**: Redis URL cache lookup network round-trip (~0.5-2ms) + FastAPI middleware overhead (OTEL span creation, Prometheus counter increment).
- **How to verify**: Open Jaeger at `http://localhost:16686`, filter by service `qr-api`, operation `GET /r/{qr_token}`. Inspect the `app.handler` span — the Redis GET child span should be <5ms. If FastAPI middleware spans are >10ms, suspect asyncio event-loop saturation.
- **Expected p99 range**: 50-200ms on a modern laptop (8-core, 16GB RAM) with Docker local networking.
- **Fix direction if threshold breached**:
  1. Pipeline `GET qr:url:{token}` + `XADD clicks:stream` into a single Redis pipeline (reduces round-trips from 2 to 1).
  2. Increase Redis connection pool beyond current default (check `redis.py` `max_connections`).
  3. If event-loop is the bottleneck, consider `uvicorn --workers 2` (must first resolve Cloud SQL Connector fork issue — see CLAUDE.md).

### redirect_cold

- **Expected bottleneck**: Postgres INSERT (single row, autocommit) + SHA-256 hashing + Base62 encoding.
- **How to verify**: `docker stats db --no-stream` — if DB CPU >70% during the test, Postgres write path is confirmed as bottleneck. Also check Grafana panel `qr_db_pool_in_use` — if it saturates at 3 (pool_size=1 + max_overflow=2), requests queue in SQLAlchemy.
- **Expected p99 range**: 200-800ms depending on Postgres I/O and Docker overlay network.
- **Fix direction if threshold breached**:
  1. Token pre-generation pool: generate tokens in batch during idle periods, store in Redis list.
  2. Async write-behind: accept the request immediately, write to DB via worker (eventual consistency trade-off).
  3. Use `pg8000` async driver + `asyncpg` if switching to async SQLAlchemy.

### image_mixed

- **Expected bottleneck**: On cache-miss requests, the `qrcode` Python library generates a PNG image in ~10-20ms CPU time per request. At 100 VUs with 50% miss rate this is ~50 concurrent CPU-bound operations.
- **How to verify**: `docker stats api --no-stream` — api container CPU should approach 80-100% during steady state. Grafana "Image cache hit rate" panel should read ~50%.
- **Expected p99 range**: 100-500ms (hit path <50ms, miss path 50-200ms).
- **Fix direction if threshold breached**:
  1. Pre-generate common specs (e.g. the top-10 dimension/color/border combinations) at startup or via a cron job.
  2. Offload PNG generation to a worker process or thread pool (`asyncio.run_in_executor`).
  3. Replace `qrcode` with a Rust-based QR lib (e.g. via a Python FFI binding) for ~10x CPU improvement.

---

## 5. Conclusion & Next Steps

- `<TBD: 2-3 sentence conclusion based on actual measurements>` — Source: k6 end-of-test summary (RPS, p99); Grafana panels for cache hit rate; Docker stats for CPU
  - Example: "redirect_hot achieves X RPS at p99=Yms on a MacBook M-series with 8GB Docker allocation, confirming Redis is not the bottleneck. redirect_cold is bounded by Postgres INSERT throughput at Z RPS. image_mixed cache hit rate held at ~50% as designed."
- **Next steps** (future sprints):
  - Rate limiting (`slowapi` or nginx rate limit) to protect against burst traffic beyond capacity.
  - Circuit breaker pattern for Redis / Postgres failure modes.
  - Benchmark with `uvicorn --workers 2` after resolving Cloud SQL Connector fork issue.
  - Consider CDN or nginx caching for `/v1/qr_code_image` to eliminate miss-path CPU entirely.

---

## How to Fill This Report

Follow these 6 steps to populate all placeholders with real numbers:

1. **Baseline setup**:
   ```bash
   git checkout 6566794
   docker-compose up -d --build
   # Wait for api to be healthy, then seed tokens:
   k6 run --env BASE_URL=http://localhost:8000 scripts/k6/seed.js \
     2>/dev/null | grep "^TOKEN:" | sed 's/^TOKEN://' | jq -s '.' > scripts/k6/tokens.json
   ```

2. **Baseline — run redirect_hot**:
   ```bash
   k6 run --env BASE_URL=http://localhost:8000 scripts/k6/redirect_hot.js
   # → Fill Section 3 Baseline columns for redirect_hot (RPS, p99, error rate)
   ```

3. **Baseline — run redirect_cold and image_mixed**:
   ```bash
   k6 run --env BASE_URL=http://localhost:8000 scripts/k6/redirect_cold.js
   k6 run --env BASE_URL=http://localhost:8000 scripts/k6/image_mixed.js
   # → Fill Section 3 Baseline columns for redirect_cold and image_mixed
   ```

4. **Current setup**:
   ```bash
   git checkout main
   docker-compose up -d --build
   # Re-seed tokens (new DB state):
   k6 run --env BASE_URL=http://localhost:8000 scripts/k6/seed.js \
     2>/dev/null | grep "^TOKEN:" | sed 's/^TOKEN://' | jq -s '.' > scripts/k6/tokens.json
   ```

5. **Current — run all three scenarios**:
   ```bash
   k6 run --env BASE_URL=http://localhost:8000 scripts/k6/redirect_hot.js
   k6 run --env BASE_URL=http://localhost:8000 scripts/k6/redirect_cold.js
   k6 run --env BASE_URL=http://localhost:8000 scripts/k6/image_mixed.js
   # → Fill Section 2 tables and Section 3 Current columns
   ```

6. **Fill Environment, Bottleneck analysis, and Conclusion**:
   - Run the "How to fill" commands in Section 1 (Environment table) for machine specs.
   - Cross-check Jaeger traces and Grafana panels during tests for bottleneck analysis.
   - Write 2-3 sentences in Section 5 Conclusion based on Delta column in Section 3.
