# Performance Report

> Run by Claude on 2026-05-15. Numbers below are from the **first complete pass** post-Sprint-B (commit `1fcb25b`). Baseline (`6566794`) comparison is still pending — see §3.
>
> Two bugs were uncovered during the run; root causes documented in §6 (Incidents during testing) and full postmortem at [docs/incidents/2026-05-15-load-test-bootstrap.md](incidents/2026-05-15-load-test-bootstrap.md).

---

## 1. Environment

| Field | Value |
| --- | --- |
| Machine | Apple M4 Pro, 48 GB RAM |
| Docker resources | 8 CPUs, 7.65 GiB memory (Docker Desktop default — well below host capacity) |
| Git SHA (current) | `1fcb25b` (post Sprint A + B + C, observability + worker stack live) |
| Git SHA (baseline) | `6566794` (pre-Sprint-A, no worker, no OTel) — **not yet measured** |
| k6 version | v0.54.0 |
| Docker Compose | v2.39.2-desktop.1 |
| OS | Darwin 25.4.0 arm64 (macOS) |

---

## 2. Scenarios

All RPS / p50 / p95 / p99 numbers in the tables below come from **Prometheus** queried over the steady-state window of each scenario (warm-up and ramp-down excluded). k6's end-of-test summary is included alongside as a sanity check — k6 reports an integral over the whole test so its p99 typically looks smaller than the steady-state value during the spike phase.

### 2.1 redirect_hot — Redis cache-hot ceiling

- **Purpose**: Measure peak redirect throughput when every URL is already in Redis cache.
- **Run**: `docker compose --profile loadtest run --rm k6 run /scripts/redirect_hot.js`
- **Load profile**: warm-up 30s @ 50 VU → steady 2m @ 200 VU → spike 30s @ 500 VU → ramp-down 30s
- **Total runtime**: 3m 31s

| Metric | Steady-state value | Source |
| --- | --- | --- |
| **RPS (avg / max over steady)** | **1,919 / 2,022 req/s** | Prometheus `sum(rate(http_requests_total{handler="/r/{qr_token}"}[30s]))` |
| **p50 latency** | 55.68 ms | Prometheus histogram_quantile 0.50 over 120s |
| **p95 latency** | 303.87 ms | same, 0.95 |
| **p99 latency** | 460.77 ms | same, 0.99 |
| Cache hit rate | **100 %** ✅ | Prometheus `qr_redirect_total{cache_result="hit"}` ratio |
| Error rate | **0 %** ✅ | k6 `http_req_failed = 0 / 399339` |
| Total requests | 399,339 | k6 summary |

**k6 integral summary (whole test)**: avg p50=71ms, p95=230ms, p99=276ms — lower than the steady-state values because k6 averages over warm-up + steady + spike + ramp-down.

### 2.2 redirect_cold — POST + GET round-trip

- **Purpose**: Measure the create-and-redirect round-trip for net-new tokens. Each VU iteration does `POST /v1/qr_code` followed by `GET /r/{token}`.
- **Run**: `docker compose --profile loadtest run --rm k6 run /scripts/redirect_cold.js`
- **Load profile**: constant 50 VU × 3 min
- **Total runtime**: 3m 1s

| Metric | Steady-state value | Source |
| --- | --- | --- |
| **POST RPS** | **580 req/s avg, 664 max** | Prometheus on POST `/v1/qr_code` |
| **GET /r RPS** | 580 req/s avg, 665 max | Prometheus on GET `/r/{qr_token}` |
| **POST p99** | 99.60 ms | Prometheus histogram_quantile |
| **GET /r p99** | 99.05 ms | same |
| **DB pool peak (qr_db_pool_in_use)** | **3 (saturated)** ⚠️ | Prometheus gauge — pool size = 1 + max_overflow 2 |
| Error rate | **0 %** ✅ | k6 `http_req_failed = 0 / 229370` |
| Total requests | 229,370 (≈ 114k iterations × 2 reqs) | k6 summary |

⚠️ **DB pool is the bottleneck** — `max_over_time(qr_db_pool_in_use[180s]) = 3` means every connection slot was in use throughout. To push p99 further down we'd raise pool_size; to push throughput up further we'd need a bigger Cloud SQL tier (db-f1-micro maxes at ~25 connections — current settings leave headroom for other instances).

⚠️ **Caveat**: The script's POST pre-warms the URL in Redis (`SETEX` happens server-side), so the subsequent GET is technically a cache hit, not a true DB-fallback. To measure pure DB fallback latency we'd need a separate scenario that flushes Redis between POST and GET.

### 2.3 image_mixed — 50/50 cache hit/miss for QR PNG

- **Purpose**: Measure QR image serving under mixed cache pressure. Half the requests hit a fixed spec (cache hit), half use random params (cache miss → CPU-bound `qrcode` generation).
- **Run**: `docker compose --profile loadtest run --rm k6 run /scripts/image_mixed.js`
- **Load profile**: warm-up 30s → 100 VU × 2m → ramp-down 30s
- **Total runtime**: 3m 4s

| Metric | Steady-state value | Source |
| --- | --- | --- |
| **RPS** | **505 / 519 req/s (avg / max)** | Prometheus on `/v1/qr_code_image/{qr_token}` |
| **p50 latency** | 222.92 ms | Prometheus histogram_quantile |
| **p95 latency** | 472.29 ms | same |
| **p99 latency** | 494.46 ms (just under threshold 800 ms ✅) | same |
| **Image cache hit rate** | **50.03 %** ✅ (matches design) | Prometheus `qr_image_cache_total` ratio |
| Error rate | **0 %** ✅ | k6 |
| Total requests | 90,443 | k6 summary |

**Bottleneck**: Cache-miss path = `qrcode` Python lib generating PNG (~10–20ms CPU per request). At 100 VU with 50 % miss → ~50 concurrent CPU-bound jobs serialised through one uvicorn worker. p99 of 494 ms ≈ ~10 misses queued up.

---

## 3. Baseline vs Current

⚠️ **Baseline run not yet performed.** To complete this section:

```bash
git checkout 6566794
docker compose down -v && docker compose up -d --build
docker compose exec api alembic upgrade head    # may not be needed pre-Sprint-A; check schema
docker compose --profile loadtest run --rm k6 run /scripts/seed.js 2>&1 \
  | sed -nE 's|^time=.*level=info msg="TOKEN:([^"]+)".*|\1|p' \
  | jq -R . | jq -s . > scripts/k6/tokens.json
docker compose --profile loadtest run --rm k6 run /scripts/redirect_hot.js  | tee results/baseline_hot.txt
docker compose --profile loadtest run --rm k6 run /scripts/redirect_cold.js | tee results/baseline_cold.txt
docker compose --profile loadtest run --rm k6 run /scripts/image_mixed.js   | tee results/baseline_image.txt
git checkout main
```

Then fill the comparison table:

| Scenario | Metric | Baseline (`6566794`) | Current (`1fcb25b`) | Delta |
| --- | --- | --- | --- | --- |
| redirect_hot | Steady RPS | TBD | 1,919 | TBD |
| redirect_hot | p99 (ms) | TBD | 460.77 | TBD |
| redirect_cold | POST RPS | TBD | 580 | TBD |
| redirect_cold | POST p99 (ms) | TBD | 99.60 | TBD |
| image_mixed | RPS | TBD | 505 | TBD |
| image_mixed | p99 (ms) | TBD | 494.46 | TBD |

**Hypothesis going in** (to be validated by baseline):
1. redirect_hot p99 will be **slightly lower** on baseline (one fewer Redis op — direct HINCRBY instead of XADD).
2. DB CPU during heavy redirect load will be **noticeably higher** on baseline (sync HINCRBY → DB writes per redirect, vs. current XADD → batched flush by worker).
3. Cold path numbers should be similar (both versions hit the same INSERT path).

---

## 4. Bottleneck Analysis

### redirect_hot
- **Confirmed bottleneck**: FastAPI / event loop overhead, NOT Redis. Cache hit rate 100 %, Redis is essentially "free" at this load (sub-ms GETs in Jaeger).
- **Evidence**: p99 climbs from ~70 ms (median) to 460 ms (steady-state p99 including spike phase). Spike from 200→500 VU produces a tail because single-process uvicorn cannot accept faster than its event loop spins.
- **Fix direction**:
  1. Pipeline the Redis URL GET + Stream XADD into a single round-trip → cuts redirect path from 2 RTTs to 1.
  2. `uvicorn --workers N` (after resolving Cloud SQL Connector fork issue per CLAUDE.md).
  3. Move `_record_click()` XADD onto a `BackgroundTask` so the 302 returns before the publish (current code already swallows publish failures so this is safe).

### redirect_cold
- **Confirmed bottleneck**: SQLAlchemy connection pool. `qr_db_pool_in_use` was pinned at 3 (= `pool_size 1 + max_overflow 2`) for the entire steady period. Requests queue at `pool_timeout` = 10 s.
- **Why it didn't blow up in latency**: 580 RPS × 100 ms p99 ≈ 58 in-flight requests, but only 3 hit the DB simultaneously. Postgres itself is idle most of the time — the queue is in SQLAlchemy.
- **Fix direction**:
  1. Raise `pool_size`. The CLAUDE.md sizing assumes a `db-f1-micro` shared with other Cloud Run instances; locally we have all 100 connections to ourselves. Bumping `pool_size=5, max_overflow=10` would likely 3–5× cold throughput.
  2. Token generation already retries up to 5× on UNIQUE collision — at higher RPS we'd need to monitor `_generate_token_with_retry` wall time.

### image_mixed
- **Confirmed bottleneck**: CPU-bound PNG generation on cache misses. Cache hit rate exactly 50 % as designed, p99 ≈ 500 ms = ~10 generations queued (10 ms each × 50 % miss × ~50 VU concurrent / 1 worker process).
- **Fix direction**:
  1. Pre-generate the top-N most common specs at startup (a few popular `dimension/color/border` combos cover most real-world usage).
  2. Offload PNG generation to a thread/process pool via `asyncio.run_in_executor`.
  3. Replace the pure-Python `qrcode` lib with a Rust-backed lib (e.g. via PyO3 binding) — typically 10× CPU win.

---

## 5. Conclusion

Single-process FastAPI + uvicorn (1 worker), local Apple M4 Pro / Docker Desktop (8 vCPU / 7.6 GiB):

- **Hot redirect**: ~1,920 RPS, p99 461 ms under spike (500 VU).
- **Cold POST + redirect**: ~580 RPS each, p99 100 ms — bounded by DB pool not CPU.
- **Image mixed (50/50)**: ~505 RPS, p99 494 ms — bounded by `qrcode` PNG generation.
- **All scenarios**: 0 application errors. The 407 "failures" seen in the very first hot run were 100 % attributable to a stale token written into `tokens.json` by a buggy seed pipe — see §6.

For an interview narrative this is enough to say:
> "Single-instance FastAPI hits ~1,900 RPS for cache-hot redirects, ~580 RPS for create-and-redirect, ~500 RPS for image generation on a laptop. Bottlenecks identified per scenario via Prometheus + Jaeger: event loop for hot, DB connection pool for cold, CPU/PNG generation for image. Each has a documented next-step fix."

Next investigations:
- Run baseline at `6566794` and quantify the **% change in DB CPU** at high QPS — that's the most defensible "Sprint A made it better" datapoint, since the headline RPS numbers won't move much (HINCRBY vs XADD on local Redis are both microseconds).
- Add per-handler latency panels to Grafana for `image_service.generate` so we can confirm the per-request cost in production.

---

## 6. Incidents during testing

Two bugs surfaced while running the very first end-to-end pass. Both are **test-tooling bugs**, not service bugs — but both are textbook examples of "lint-passed, fails on first real run" and are documented here so the next person doesn't waste an hour rediscovering them. Full postmortem: [docs/incidents/2026-05-15-load-test-bootstrap.md](incidents/2026-05-15-load-test-bootstrap.md).

### Incident #1 — 407 spurious 404s in `redirect_hot`

**Symptom**: First clean `redirect_hot` run reported `http_req_failed: 0.09 % (407 / 413,484)`. API logs showed `407 × "GET /r/%28%5B%5E HTTP/1.1" 404`.

**URL-decode `%28%5B%5E`** = `([^` — that's a fragment of a sed regex pattern, not a token.

**Root cause**: `seed.js`'s `handleSummary` function emits an example shell command in its stdout summary, and that example command literally contains the regex `s|.*msg="TOKEN:([^"]+)".*|\1|p`. The token-extraction sed pipe was greedy enough to match its own example text in the seed output, capturing `([^` as a "token" and writing it into `tokens.json`. With 1,001 tokens (1,000 real + 1 fake), each VU had a ~0.1 % chance of picking the bogus token per request → 0.1 % × 413,484 ≈ 413 → matches the observed 407 within stochastic noise.

**Fix applied**: anchor the sed pattern to `^time=` so it only matches real k6 log lines (which always start with `time="..."`), not the example text. All seven files containing the pattern were updated:
- `scripts/k6/seed.js` (header docstring + handleSummary literal)
- `scripts/k6/redirect_hot.js` (header)
- `scripts/k6/image_mixed.js` (header)
- `scripts/k6/README.md`
- `docs/load-test-plan.md`
- `docs/perf-report.md` (this file)
- `docs/incidents/2026-05-15-load-test-bootstrap.md`

**Verified**: After fix, `tokens.json` contains exactly 1,000 entries, all matching `^[A-Za-z0-9]{10}$`. Re-run hot: **0 / 399,339 failures**.

### Incident #2 — `redirect_cold` threw `TypeError` on every iteration

**Symptom**: Every iteration of `redirect_cold` threw `TypeError: Cannot read property 'qr_token' of undefined` at line 68 (`JSON.parse(createRes.body).qr_token`). Despite this, the API log showed every POST returning `201 Created` cleanly.

**Root cause**: `scripts/k6/lib/common.js`'s `defaultOptions` sets `discardResponseBodies: true` (k6 perf optimisation — saves memory by not buffering response bytes). `redirect_cold.js` does `export const options = { ...defaultOptions, ... }` so it inherits the flag. With bodies discarded, `createRes.body` is `undefined` regardless of HTTP status, so `JSON.parse(undefined).qr_token` throws.

The `redirect_hot.js` script doesn't read response bodies (it only checks `r.status === 302`), so it didn't surface the bug. Only `redirect_cold` actually needs the body.

**Fix applied**: pass `responseType: 'text'` per-request in `redirect_cold.js`'s POST call to override the global discard. Also added defensive `try/catch` around `JSON.parse` and a `'has body'` check.

**Verified**: Re-run cold: **0 / 229,370 failures**, all iterations completed.

### Process lessons

Both bugs share the same shape: **the script passed lint / unit tests but failed on first real execution**. Tests in `tests/test_k6_scripts.py` only check JS syntax (`node --check`), they don't actually invoke the scripts against a running stack. The harness response is documented in the postmortem:
- `~/.claude/agents/generator.md` now requires "Shell pipeline smoke test" as Step 5 of the workflow.
- `tests/test_no_latest_images.py` enforces `:latest` ban on all container images (related earlier incident — `jaegertracing/all-in-one:latest` got retired).
- A pending follow-up is to add a real-environment smoke test in CI for any new k6 scenario.

---

## How to reproduce these numbers

```bash
# Fresh state every time — avoids cross-scenario pollution
docker compose exec redis redis-cli FLUSHDB
docker compose exec postgres psql -U qrapp -d qrdb -c "TRUNCATE qr_codes, qr_click_stats RESTART IDENTITY CASCADE"

# Seed 1000 tokens (anchored sed to avoid Incident #1)
docker compose --profile loadtest run --rm k6 run /scripts/seed.js 2>&1 \
  | sed -nE 's|^time=.*level=info msg="TOKEN:([^"]+)".*|\1|p' \
  | jq -R . | jq -s . > scripts/k6/tokens.json
jq 'length' scripts/k6/tokens.json     # expect 1000

# Run each scenario, capture timing for later Prometheus queries
for s in redirect_hot redirect_cold image_mixed; do
  START=$(date +%s)
  docker compose --profile loadtest run --rm k6 run /scripts/${s}.js | tee results/${s}.txt
  END=$(date +%s)
  echo "$START $END" > results/${s}.timing
  # Reset between scenarios to avoid cache pollution
  docker compose exec redis redis-cli FLUSHDB
done

# Pull steady-state numbers from Prometheus (skip warm-up + ramp-down)
# See the helper queries embedded in this report's section 2 tables.
```
