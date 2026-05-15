# Performance Report

URL-shortener redirect path 在高 QPS 下的承載量測。三個 click-counting 架構在**相同硬體、相同 k6 腳本、相同 Postgres / Redis、相同 logging（structlog-only）**下對比——只有 `api` container 換成不同架構的 build。

## Environment

| Field | Value |
| --- | --- |
| Machine | Apple M4 Pro, 48 GB RAM |
| Docker | Docker Desktop, 8 vCPU / 7.65 GiB |
| Load tool | k6 v0.54.0（在被測服務外部產生流量並量測） |
| App | Single uvicorn process, no `--workers` |
| DB | PostgreSQL 15, pool_size=1 + max_overflow=2 |
| Cache | Redis 7（URL cache + PNG byte cache） |
| Logging | structlog JSON to stdout（三版本一致） |

## Architectures compared

| 版本 | redirect handler | click 計數 |
| --- | --- | --- |
| **A. 同步點擊** | sync `def`（threadpool）| redirect 內直接 `HINCRBY` Redis |
| **B. 異步 worker** | `async def`（event loop）| redirect 只 `XADD`，獨立 worker process 消費 |
| **C. 無 click** | `async def`（event loop）| 完全移除（[ADR-0003](decisions/0003-remove-click-counting-mvp.md)）|

> 方法學註記：A 是 sync `def`、B/C 是 `async def`，所以 A→B 的差距包含「sync→async endpoint」+「同步 HINCRBY → worker offload」兩個因素。這是兩個架構**實際存在時的真實對比**，不是單一變因隔離——量測時刻意保持其餘條件（硬體 / 腳本 / DB / logging）一致。

## Scenarios

- **redirect_hot** — 1,000 個預建 token 隨機打 `/r/{token}`，全部 Redis cache hit。量 redirect 吞吐天花板。負載：30s warm-up → 2m @ 200 VU → spike 30s @ 500 VU → 30s ramp-down。
- **redirect_cold** — 每個 iteration 先 `POST` 建新 token 再 redirect。量 DB 寫入路徑。constant 50 VU × 3 min。
- **image_mixed** — 50% cache hit / 50% miss 打 `/v1/qr_code_image`。量 CPU-bound PNG 生成。100 VU × 2 min。

## Results（k6 整輪平均）

| Scenario | Metric | A. 同步點擊 | B. 異步 worker | C. 無 click | B vs A | C vs A |
| --- | --- | --- | --- | --- | --- | --- |
| **redirect_hot** | RPS | 1,620 | 2,227 | **2,603** | **+37 %** | +61 % |
| | p50 (ms) | 77.8 | 58.4 | **50.9** | −25 % | −35 % |
| | p95 (ms) | 279.8 | 204.9 | **167.1** | −27 % | −40 % |
| | p99 (ms) | 329.0 | 233.5 | **189.8** | **−29 %** | −42 % |
| **redirect_cold** | RPS | 1,121 | 1,301 | **1,416** | **+16 %** | +26 % |
| | p50 (ms) | 17.9 | 35.9 | 32.7 | +101 % | +83 % |
| | p95 (ms) | 137.7 | 57.0 | **61.0** | −59 % | −56 % |
| | p99 (ms) | 202.4 | 152.3 | **77.4** | −25 % | **−62 %** |
| **image_mixed** | RPS | 458 | 486 | 458 | +6 % | 0 % |
| | p50 (ms) | 119.4 | 111.3 | 115.4 | −7 % | −3 % |
| | p95 (ms) | 223.2 | 214.4 | 230.6 | −4 % | +3 % |
| | p99 (ms) | 250.9 | 240.2 | 263.1 | −4 % | +5 % |
| 全部 | error rate | 0 % | 0 % | 0 % | — | — |

## Bottleneck analysis

| 情境 | 瓶頸 | 證據 | 突破方向 |
| --- | --- | --- | --- |
| redirect_hot | 單 process uvicorn event loop | spike 500 VU 時 throughput 不升反降（past-peak）| 多 uvicorn worker / 多 instance |
| redirect_cold | SQLAlchemy connection pool（飽和在 pool_size+overflow=3）| pool 全程滿、Postgres CPU 閒置（queue 在 app 層）| 調大 pool / 升 DB tier |
| image_mixed | CPU-bound `qrcode` PNG 生成（~10–20 ms/req）| api container CPU ≈100%，三架構幾乎持平 | 預生成常見 spec / Rust QR lib / `run_in_executor` offload |

## Conclusion

- **異步 worker 拆分讓 hot path 承載 +37 %（1,620 → 2,227 RPS）、p99 −29 %（329 → 234 ms）**。redirect handler 從同步等 `HINCRBY` 變成 fire-and-forget `XADD`，click 計數丟給獨立可水平擴展的 worker process。
- **完全移除 click 計數再 +17 %**（少一個 Redis op，hot RPS 2,227 → 2,603）。
- **image_mixed 三架構持平**——瓶頸是 CPU-bound PNG 生成，與 click 架構無關。
- 所有情境 0 application error。

**容量規劃**：單 process 天花板 ~2,000–2,600 RPS。要到數萬 RPS 是架構問題不是 tuning——95%+ 的 redirect 是 cache-hot 固定 URL，應放 CDN 讓 origin 只處理 cache miss，再水平擴展（多 API instance + load balancer）。詳見 [`docs/architecture.md`](architecture.md)。

## How to reproduce

```bash
docker compose up -d
docker compose exec api alembic upgrade head

# 每輪測試前重置，避免跨情境污染
docker compose exec redis redis-cli FLUSHDB
docker compose exec postgres psql -U qrapp -d qrdb \
  -c "TRUNCATE qr_codes RESTART IDENTITY CASCADE"

# seed 1,000 token（k6 v0.50+ 把 console.log 寫 stderr 並包成 level=info msg="..."）
docker compose --profile loadtest run --rm k6 run /scripts/seed.js 2>&1 \
  | sed -nE 's|^time=.*level=info msg="TOKEN:([^"]+)".*|\1|p' \
  | jq -R . | jq -s . > scripts/k6/tokens.json
jq 'length' scripts/k6/tokens.json     # expect 1000

for s in redirect_hot redirect_cold image_mixed; do
  docker compose --profile loadtest run --rm k6 run /scripts/${s}.js
  docker compose exec redis redis-cli FLUSHDB
done
```

完整指令與情境說明：[`scripts/k6/README.md`](../scripts/k6/README.md)。
