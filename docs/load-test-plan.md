# 壓力測試計劃表（Load Test Plan）

> 目的：用 docker-compose 起整套服務（含 Postgres 15），跑 k6 壓測量化「微服務化 + observability」兩個 sprint 的效益，產出可供面試展示的數字。
> 配套文件：執行細節 → [scripts/k6/README.md](../scripts/k6/README.md)、結果歸檔 → [docs/perf-report.md](perf-report.md)。

---

## 0. 為什麼做這份計劃

| 想驗證的問題 | 對應情境 | 關鍵指標 |
|---|---|---|
| 拆 worker 後 redirect p99 有沒有變好？ | redirect_hot baseline vs current | p99、error rate |
| DB 寫入路徑會在多少 QPS 飽和？ | redirect_cold | DB CPU、`qr_db_pool_in_use` |
| Image cache 真的擋住了 CPU 嗎？ | image_mixed | API CPU、cache hit rate |
| 觀測性正常運作？ | 全部情境 | Jaeger 能看到 trace、Prometheus `/metrics` 有資料 |

---

## 1. 執行前準備（一次性）

### 1.1 環境檢查

```bash
docker --version            # 25.x+
docker compose version      # v2.x+
docker info | grep -E 'CPUs|Total Memory'   # 建議 ≥ 4 CPU / 8 GB
```

⚠️ Docker Desktop 預設常常只給 2 CPU / 2 GB，跑出來的數字會被資源拖累。先去 Settings → Resources 調到 4 CPU / 8 GB 以上，**所有測試用同一份配置**才能比對。

### 1.2 起 stack

```bash
cd /Users/yehhungwei/qr-code-generator
docker compose up -d --build
# 等 api healthy：
curl -s http://localhost:8000/healthz   # 應回 {"status":"ok"} 之類
```

服務 endpoint：

| Service | URL | 用途 |
|---|---|---|
| API | http://localhost:8000 | 壓測對象 |
| Prometheus | http://localhost:9090 | 查 raw metric / PromQL |
| Grafana | http://localhost:3000 | 看 dashboard（預設 admin/admin） |
| Jaeger | http://localhost:16686 | 看單一 trace |
| Redis | `docker compose exec redis redis-cli` | 看 stream / cache |
| Postgres | `docker compose exec db psql -U postgres -d qrdb` | 看 click stats |

### 1.3 Seed tokens

`redirect_hot` 需要預先存在的 token list：

```bash
# k6 v0.50+ writes console.log to STDERR wrapped as `level=info msg="TOKEN:xxx" source=console`,
# so we capture stderr (2>&1) and extract the token from the msg field.
docker compose --profile loadtest run --rm k6 run /scripts/seed.js 2>&1 \
  | sed -nE 's|^time=.*level=info msg="TOKEN:([^"]+)".*|\1|p' \
  | jq -R . | jq -s . \
  > scripts/k6/tokens.json

jq 'length' scripts/k6/tokens.json   # 應該回傳 ~1000
```

⚠️ 第一次跑前要先建 Postgres schema（lifespan 只在 SQLite 自動 create_all）：

```bash
docker compose exec api alembic upgrade head
```

---

## 2. 三個測試情境

### 2.1 redirect_hot — Redis cache 極限

| 項目 | 內容 |
|---|---|
| **目的** | 量「全部命中 Redis URL cache」時的 redirect p99 與 throughput 上限 |
| **執行指令** | `docker compose --profile loadtest run --rm k6 run /scripts/redirect_hot.js` |
| **負載曲線** | warm-up 30s @ 50 VU → steady 2m @ 200 VU → spike 30s @ 500 VU → ramp-down 30s |
| **預期 bottleneck** | Redis 網路 RTT + FastAPI middleware overhead；事件迴圈飽和 |
| **驗收（threshold）** | `http_req_failed < 1%`、`p99 < 500ms` |
| **預期 p99 範圍** | 50–200ms（M 系列 MacBook 本地） |

**怎麼確認 bottleneck**：
1. 打開 Jaeger，filter service=`qr-api` operation=`GET /r/{qr_token}`，看 Redis GET child span 是否 < 5ms
2. `docker stats api` → CPU 若 100% 但 Redis GET 仍 < 5ms，就是 event loop 飽和（單 worker）

### 2.2 redirect_cold — DB 寫入路徑

| 項目 | 內容 |
|---|---|
| **目的** | 量每次都建新 token（INSERT + Redis SETEX）的 cold-path 上限 |
| **執行指令** | `docker compose --profile loadtest run --rm k6 run /scripts/redirect_cold.js` |
| **負載曲線** | constant 50 VU × 3 分鐘 |
| **預期 bottleneck** | Postgres INSERT + SHA-256 token hashing + SQLAlchemy pool（`pool_size=1, max_overflow=2`，上限 3） |
| **驗收（threshold）** | `http_req_failed < 1%`、`p99 < 1500ms` |
| **預期 p99 範圍** | 200–800ms |

**怎麼確認 bottleneck**：
1. Grafana `qr_db_pool_in_use` panel — 若一直黏在 3 就是 pool 飽和
2. `docker stats db` — CPU > 70% 就是 DB write 是真瓶頸
3. PromQL：`rate(pg_stat_statements_calls{query=~".*INSERT.*qr_codes.*"}[1m])`（如有裝 postgres exporter）

### 2.3 image_mixed — QR 影像生成

| 項目 | 內容 |
|---|---|
| **目的** | 量 50% cache hit / 50% miss 下，QR PNG 生成是否成為 CPU 瓶頸 |
| **執行指令** | `docker compose --profile loadtest run --rm k6 run /scripts/image_mixed.js` |
| **負載曲線** | warm-up 30s → 100 VU × 2m → ramp-down 30s |
| **預期 bottleneck** | Miss 路徑的 `qrcode` PNG 生成（~10–20ms CPU/req） |
| **驗收（threshold）** | `http_req_failed < 1%`、`p95 < 800ms` |
| **預期 p99 範圍** | 100–500ms（hit < 50ms / miss 50–200ms） |

**怎麼確認 bottleneck**：
1. `docker stats api` — CPU 接近 100% 就是 CPU bound
2. Grafana「Image cache hit rate」應 ~50%
3. Jaeger 找 `image_service.generate` span，看 miss 樣本的 duration

---

## 3. 重點指標表（每次測試都要記）

| 類別 | 指標 | 怎麼取得 | 合格範圍 |
|---|---|---|---|
| **吞吐** | RPS（steady state） | k6 summary `http_reqs / duration`；Prometheus `sum(rate(http_requests_total[1m]))` | hot ≥ 1000、cold ≥ 100、image ≥ 200 |
| **延遲** | p50 / p95 / p99（ms） | k6 summary；PromQL `histogram_quantile(...)` | 見各情境 threshold |
| **錯誤** | error rate (%) | k6 summary `http_req_failed`；`rate(http_req_failed[1m])` | < 1% |
| **DB** | CPU (%) | `docker stats db --no-stream --format "{{.CPUPerc}}"` | < 80% 才有 headroom |
| **DB** | `qr_db_pool_in_use` | Prometheus gauge | 不要長期黏在 3 |
| **Redis** | ops/s | `docker compose exec redis redis-cli info stats \| grep instantaneous_ops_per_sec` | 1k–20k 都正常 |
| **Cache** | redirect cache hit % | PromQL `rate(qr_redirect_total{cache_result="hit"}[5m]) / rate(qr_redirect_total[5m])` | hot 接近 100%、cold 接近 0% |
| **Cache** | image cache hit % | PromQL `rate(qr_image_cache_total{result="hit"}[5m]) / rate(qr_image_cache_total[5m])` | image_mixed 接近 50% |
| **App** | API CPU (%) | `docker stats api --no-stream` | hot/cold 可 < 100%；image_mixed 可衝高 |
| **Stream** | publish/consume rate | `qr_click_stream_published_total` / `..._consumed_total` | 兩者收斂（lag → 0） |
| **Stream** | lag | `qr_click_stream_lag` gauge | 測試結束時應趨近 0 |

---

## 4. 標準執行流程（每次完整測一輪）

```bash
# 1. 確認 stack 起來
docker compose ps

# 2. Seed（每次重建 DB 都要重做）
docker compose --profile loadtest run --rm k6 run /scripts/seed.js \
  2>&1 | sed -nE 's|^time=.*level=info msg="TOKEN:([^"]+)".*|\1|p' | jq -R . | jq -s . \
  > scripts/k6/tokens.json

# 3. 跑三情境（中間建議間隔 60s 讓 stream 排空）
docker compose --profile loadtest run --rm k6 run /scripts/redirect_hot.js   | tee results/hot.txt
sleep 60
docker compose --profile loadtest run --rm k6 run /scripts/redirect_cold.js  | tee results/cold.txt
sleep 60
docker compose --profile loadtest run --rm k6 run /scripts/image_mixed.js    | tee results/image.txt

# 4. 把 k6 summary + 重點指標填回 docs/perf-report.md
# 5. 截圖 Grafana dashboard（QPS、p99、cache hit rate 三張），存到 docs/perf/
```

> ⚠️ **避免兩個情境互污染**：跑 cold 之後 Redis URL cache 已被 SETEX 預熱，馬上跑 hot 會偏快。建議每輪測完 `docker compose exec redis redis-cli FLUSHDB`，重 seed 再下一輪。

---

## 5. Baseline vs Current 對比（核心面試素材）

要兩個版本各跑一次同樣的 k6：

| 階段 | git 操作 | 預期看到的差異 |
|---|---|---|
| **Baseline**（pre-Sprint-A） | `git checkout 6566794` → 重做 §1.2 §1.3 | redirect 是同步 HINCRBY，沒 worker。理論上 hot 路徑 p99 略低（少一次 XADD），但點擊計數的擴展性差 |
| **Current**（post-Sprint-B） | `git checkout main` → 重做 §1.2 §1.3 | XADD 取代 HINCRBY；多一支 worker process；有完整 OTel/Prom/structlog |

對比表填到 [perf-report.md §3](perf-report.md#3-baseline-vs-current)。**面試重點不是「拆完一定變快」**（單機本地網路下，多一次 XADD 反而會稍慢一點），而是：

1. **拆出 worker 後 redirect handler 邏輯變單純**（少了 hash 累加），p99 標準差 / 抖動會降低
2. **DB 寫入頻率從每次 redirect 一發降為 hourly batch**，DB CPU 在高 QPS 下會明顯較平
3. **點擊計數可水平擴展**（多開幾個 worker 就好），原本綁死在 API process

這三點要在 §6 結論的敘事裡寫清楚。

---

## 6. 測試結果（執行後填）

實際數字填到 [`docs/perf-report.md`](perf-report.md)，這份計劃表只記**摘要**：

| 日期 | Commit | 情境 | RPS | p99 (ms) | error % | 主瓶頸 | 備註 |
|---|---|---|---|---|---|---|---|
| `<TBD>` | `6566794` | redirect_hot | | | | | baseline |
| `<TBD>` | `6566794` | redirect_cold | | | | | baseline |
| `<TBD>` | `6566794` | image_mixed | | | | | baseline |
| `<TBD>` | `main` | redirect_hot | | | | | current |
| `<TBD>` | `main` | redirect_cold | | | | | current |
| `<TBD>` | `main` | image_mixed | | | | | current |

---

## 7. 常見問題排查

> 💡 第一次 onboarding 常踩的坑見下表前 3 列（紀錄於 2026-05-15 首次本地壓測）。

| 現象 | 可能原因 | 修法 |
|---|---|---|
| **POST /v1/qr_code 全部 connection reset / EOF；API log 顯示 `psycopg2.errors.UndefinedTable: relation "qr_codes" does not exist`** | **Postgres schema 沒建。`app/main.py` 的 lifespan 只在 SQLite 自動 `create_all`，Postgres 必須手動跑 alembic migration** | `docker compose exec api alembic upgrade head` 後重跑。可考慮把 migration 寫進 api service 的 startup command（見 §8） |
| **`jq 'length' scripts/k6/tokens.json` 回傳 0（seed 看起來成功但 tokens.json 是 `[]`）** | **k6 v0.50+ 把 `console.log` 寫到 STDERR，且包成 `time=... level=info msg="TOKEN:xxx" source=console`。舊版的 pipe `2>/dev/null \| grep "^TOKEN:"` (1) 把 stderr 丟掉、(2) 用行首錨點而 TOKEN: 不在行首** | 改用 `2>&1 \| sed -nE 's/.*msg="TOKEN:([^"]+)".*/\\1/p' \| jq -R . \| jq -s .`：先 merge stderr 到 stdout、再從 `msg="..."` 用 capture group 抓 token |
| k6 一開始就一堆 5xx | API 還沒 ready | `curl localhost:8000/healthz` 確認、加 `sleep 10` |
| `tokens.json: No such file` | 沒跑 seed | 回 §1.3 |
| Cold 跑出來 RPS 比 hot 還高 | URL 已被 cache（跑過 cold 後沒 FLUSHDB） | `docker compose exec redis redis-cli FLUSHDB` 重來 |
| Grafana panel 全空 | Prometheus scrape 失敗 | `curl localhost:9090/api/v1/targets` 看 `/metrics` 是否 up |
| Trace 沒出現在 Jaeger | OTel exporter 沒指對 endpoint | `docker compose logs api \| grep -i otlp`、確認 `OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318` |
| API CPU 一直 100% 但 p99 沒漲 | 量得不夠（VU 太低）或 docker 限制太緊 | 提高 VU、放寬 Docker resources |
| Worker log 沒收到 click | XADD 失敗被 swallow | `docker compose logs api \| grep "_record_click"`；看 Redis 連線 |

---

## 8. 範圍外（之後再做）

- CI 自動跑 k6 + 把 p99 設為 release gate（需要穩定的 benchmark runner）
- 用 Cloud SQL `db-f1-micro` 模擬真實雲端延遲（cost 分析另見對話）
- Rate limit / circuit breaker 加上去後重跑一次（Sprint D/E 候選）
