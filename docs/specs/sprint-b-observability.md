# Spec: Sprint B — Observability 三本柱（OTel traces + Prometheus metrics + structlog JSON logs）

## Goal
讓 QR Code Generator 在 30 秒內可以定位「請求慢 / 失敗 / 服務拆分後行為改變」的根因——同時鋪好 **distributed tracing**、**metrics**、**structured logs** 三本柱，logs 帶 `trace_id` 能跨工具互查；並於 Sprint A 拆出的 worker 同步啟用同一套 observability stack。

## User stories
- As a **service operator**，redirect 變慢時我能在 Jaeger / Cloud Trace 一張 trace 內看到 FastAPI handler → Redis lookup → click_stream publish 的每段耗時，並從 span attribute 直接判斷是 `cache_result=hit` 還是 `miss`。
- As a **service operator**，我可以從 `/metrics` 看到 redirect QPS（依 cache hit/miss 拆）、image cache hit rate、click stream publish/consume 速率與 lag、DB pool 使用率，用 Grafana 預設 dashboard 一頁看完。
- As a **service operator**，所有 log 都是 JSON 一行一筆且含 `trace_id` / `span_id`，故障時用 `trace_id` 同時撈 Jaeger 與 log 不需手工拼接。
- As a **developer**，本地 `docker-compose up` 起完整觀測 stack（Jaeger + Prometheus + Grafana），不需要額外 cloud 帳號即可開發 / demo。
- As a **production operator**，部署到 Cloud Run 時 traces 直接寫進 Cloud Trace、Cloud Logging 自動關聯，不需另跑 OTel collector。
- As an **interviewer audience**，commit / README 可看到「OTel SDK + dual exporter + 自動 + 手動 instrumentation + structlog 注 trace context」這條敘事對應到實際程式碼。

## Acceptance criteria

### OpenTelemetry tracing
- [ ] 新增 `app/observability.py`，於 `app/main.py` lifespan 啟動時呼叫 `init_tracing()`；worker (`app/worker.py`) 啟動時亦呼叫同一函式。
- [ ] 自動 instrumentation 套用以下 library：`fastapi`、`sqlalchemy`、`redis`、`requests`。
- [ ] 手動 spans（皆使用 `tracer.start_as_current_span`）：
  - `qr_service.create`、`qr_service.get`、`qr_service.list`、`qr_service.update`、`qr_service.delete`
  - `image_service.generate`、`image_service.cache_lookup`
  - `click_stream.publish`、`click_stream.consume_batch`
  - `worker.run_once`
- [ ] 必備 span attributes：
  - `qr.token`（凡涉及單一 token 的 span）
  - `qr.cache_result` ∈ `{hit, miss}`（image_service cache_lookup、redirect handler 的 URL cache lookup）
  - `qr.image.spec_hash`（image_service.generate / cache_lookup）
  - `stream.entry_id`（click_stream.publish 回傳值、consume_batch 內每筆）
  - `batch.size`（worker.run_once、click_stream.consume_batch）
- [ ] Dual exporter，由 `ENVIRONMENT` 決定：
  - `ENVIRONMENT=local`（或 `local-compose`）→ 只啟用 OTLP/HTTP exporter，target `http://jaeger:4318/v1/traces`（compose 內 hostname）
  - `ENVIRONMENT=production` → 只啟用 `opentelemetry-exporter-gcp-trace`（Cloud Trace）
  - 其他值 → 不掛 exporter（no-op tracer），測試不噴 network call
- [ ] Resource 必含 `service.name`（API: `qr-api`、worker: `qr-worker`）、`service.version`（從 env `APP_VERSION`，預設 `dev`）。

### Prometheus metrics
- [ ] `prometheus-fastapi-instrumentator` 掛 `/metrics` endpoint（API 服務）。
- [ ] 新增 `app/metrics.py` 定義以下 metric：
  - `qr_redirect_total{cache_result}` Counter（labels: `hit` / `miss`）
  - `qr_image_cache_total{result}` Counter（labels: `hit` / `miss`）
  - `qr_click_stream_published_total` Counter
  - `qr_click_stream_consumed_total` Counter
  - `qr_click_stream_lag` Gauge（worker 定期 sample，值 = `XLEN clicks:stream` − consumer group `delivered`）
  - `qr_db_pool_in_use` Gauge（從 SQLAlchemy engine pool 抽 `checkedout()`）
- [ ] Metric 增加點：
  - 每次 redirect handler 終結時 inc 對應 `qr_redirect_total` label
  - `image_service.cache_lookup` 結果 inc `qr_image_cache_total`
  - `click_stream.publish_click` 成功後 inc `qr_click_stream_published_total`
  - `worker.run_once` flush 後 inc `qr_click_stream_consumed_total` 對應數量
  - Worker 主迴圈每次 idle tick 更新 `qr_click_stream_lag`
  - API lifespan 啟動一個 lightweight background task 每 5 秒更新 `qr_db_pool_in_use`

### structlog
- [ ] 新增 `app/logging.py` 設定 structlog：JSON formatter、ISO timestamp、log level、logger name；processor chain 包含 trace context injector（讀 current span 的 `trace_id` / `span_id` 寫進 event dict）。
- [ ] `app/main.py` lifespan、`app/worker.py` `main()` 都呼叫 `configure_logging()`。
- [ ] 取代 `app/jobs/flush_clicks.py` 內的 stdlib `logging.getLogger` 為 `structlog.get_logger`。
- [ ] 在 Sprint A QA report 列的 swallow / silent fallback 點補 `logger.warning`：
  - `app/main.py:_record_click` swallow 加 `logger.warning("publish_click failed", error=str(exc))`
  - `app/worker.py:_ts_to_hour` fallback 加 `logger.warning("unparseable ts", ts=ts)`
- [ ] Stdlib `logging` 透過 structlog `ProcessorFormatter` 統一輸出（uvicorn / sqlalchemy / redis 的 log 亦走 JSON）。

### docker-compose stack
- [ ] `docker-compose.yml` 增加三個 service：
  - `jaeger`：`jaegertracing/all-in-one:latest`，expose `16686`（UI）、`4318`（OTLP/HTTP）
  - `prometheus`：`prom/prometheus:latest`，掛 `docker/prometheus.yml`，expose `9090`
  - `grafana`：`grafana/grafana:latest`，掛 provisioning 把 prometheus 設為 datasource、auto-import `docker/grafana/dashboards/qr.json`，expose `3000`
- [ ] `api` / `worker` service env 增加 `OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318`、`ENVIRONMENT=local-compose`。
- [ ] 新增 `docker/prometheus.yml`：scrape `api:8080/metrics` 每 5 秒。
- [ ] 新增 `docker/grafana/dashboards/qr.json`：至少含 panels：
  - Redirect QPS（總計與 cache hit/miss 拆分）
  - Redirect p50 / p95 / p99（從 instrumentator 的 histogram）
  - Image cache hit rate（5m rate）
  - Click stream published vs consumed rate
  - `qr_click_stream_lag`
  - `qr_db_pool_in_use`
- [ ] 新增 `docker/grafana/provisioning/datasources/prometheus.yml` 與 `dashboards/qr.yml` 自動載入。

### Sprint A QA warnings clean-up（一併納入本 sprint）
- [ ] `app/main.py:_record_click` swallow 加 `logger.warning`（同上）
- [ ] `app/worker.py:run_once` 接受參數 `batch_count`、`block_ms`（或 idle_ms），`main()` 改成呼叫 `run_once(...)` 取代重複的 14 行 batch-processing 邏輯
- [ ] `tests/test_click_stream.py:test_worker_dedupe_skips_replay` 改寫成真正走 XCLAIM 重派路徑（consumer-A 讀後不 ack → consumer-B `claim_stale(min_idle_ms=0)` 領回 → run_once 處理一次 → 再 claim 一次 / inject dedupe key → run_once 跑第二輪 → hash 仍為 1）
- [ ] `app/services/click_stream.py:ensure_group` 改 `except redis.exceptions.ResponseError as exc:` 再以 `BUSYGROUP` 子字串判斷
- [ ] `app/worker.py` / `click_stream.py` 的 `xpending_range count=500` 改抽常數（或讓 `claim_stale` 接 `count` 參數），在 docstring 標明上限
- [ ] QA report 在 checklist 內顯式逐項勾掉這 5 條

### 驗證
- [ ] `pytest tests/ -v` 全綠（含 Sprint A 既有 38 case + 新 observability tests）
- [ ] `docker-compose up` 後對 `/r/{token}` 發請求，瀏覽 `http://localhost:16686`（Jaeger UI）能看到一條 trace 完整覆蓋 FastAPI handler → Redis lookup → `click_stream.publish`，並含 `qr.cache_result` attribute
- [ ] `curl http://localhost:8000/metrics` 看到所有自定義 metric（名稱完整出現）
- [ ] Grafana `http://localhost:3000` 預設 dashboard 顯示 QPS / p99 / cache hit rate panel 有資料
- [ ] 隨機抓一行 API log，是合法 JSON 且含 `trace_id` 欄位（非空字串，且能在 Jaeger 用該 ID 查到對應 trace）
- [ ] `ENVIRONMENT=production` 時 Cloud Trace exporter 啟用、OTLP exporter 不啟用；`ENVIRONMENT=local` 時相反——以單元測試或 init function return value 斷言
- [ ] 公開 API 行為不變：既有 38 個 test 全部維持綠燈，response schema / status code / headers byte-identical

## Non-goals
- 不做 alerting / on-call routing / PagerDuty 接線
- 不定義 SLO / SLI / error budget
- 不導入 log aggregation 後端（Loki / Cloud Logging 自動關聯靠 trace_id 即可，不在 sprint 內手動配置 sink）
- 不做 distributed tracing 跨服務 propagation 的進階情境（W3C context 用 OTel SDK 預設即可，不另寫 custom propagator）
- 不做 Cloud Run 部署設定變更（VPC connector / service account scope 不調整，Cloud Trace 寫入靠 default credentials）
- 不調整 Sprint A 的功能邏輯（producer / consumer / dedupe / claim 設計不動，僅補 instrumentation）
- 不做 k6 壓測（Sprint C）
- 不引入 OpenTelemetry collector sidecar——本地直送 Jaeger、prod 直送 Cloud Trace

## Open questions
- Cloud Trace exporter 在沒有 GCP credentials 時的 fallback 行為——預期由 `google-auth` 自動偵測，本地 `ENVIRONMENT=local` 不會觸發；若日後 CI 需要 prod-like config 再處理。
