# Contract: Sprint B — Observability 三本柱

對應 spec: docs/specs/sprint-b-observability.md
相關 ADR: docs/decisions/0002-otel-with-dual-exporter.md

## Deliverables

### Backend — OTel tracing 初始化（新增）
- [ ] 新增「應用程式碼路徑」`app/observability.py`：
  - `init_tracing(service_name: str) -> None`
    - 讀 `settings.ENVIRONMENT`：
      - `local` / `local-compose` → 掛 `OTLPSpanExporter`（OTLP/HTTP），endpoint 取 env `OTEL_EXPORTER_OTLP_ENDPOINT`（預設 `http://localhost:4318`）
      - `production` → 掛 `CloudTraceSpanExporter`（`opentelemetry-exporter-gcp-trace`）
      - 其他 → no-op（不設 exporter，仍註冊 TracerProvider 以便 manual span 有效）
    - 設定 `Resource` 帶 `service.name=service_name`、`service.version=os.getenv("APP_VERSION", "dev")`
    - 套用自動 instrumentation：`FastAPIInstrumentor`、`SQLAlchemyInstrumentor`、`RedisInstrumentor`、`RequestsInstrumentor`
    - 函式 idempotent：重複呼叫只初始化一次（用模組級旗標）
  - `get_tracer(name: str)`：薄包 `trace.get_tracer(name)`，方便測試 mock
  - 暴露 `is_initialized() -> bool`、`current_exporter_kind() -> Literal["otlp", "gcp", "noop"]`，供測試斷言

### Backend — structlog 設定（新增）
- [ ] 新增「應用程式碼路徑」`app/logging.py`：
  - `configure_logging() -> None`：
    - 設定 structlog processor chain：`add_log_level`、`TimeStamper(fmt="iso", utc=True)`、`StackInfoRenderer`、`format_exc_info`、`_add_trace_context`（自寫，讀 `trace.get_current_span().get_span_context()` 注 `trace_id` / `span_id`）、`JSONRenderer`
    - 透過 `structlog.stdlib.ProcessorFormatter` 把 stdlib logging（uvicorn / sqlalchemy / redis）統一導向 structlog 輸出
    - Log level 預設 `INFO`，可由 env `LOG_LEVEL` 覆寫
  - 暴露 `get_logger(name: str)`：薄包 `structlog.get_logger(name)`
  - `_add_trace_context(logger, method_name, event_dict)`：當 span 為 `INVALID_SPAN` 時欄位空字串而非 missing

### Backend — Prometheus metrics（新增）
- [ ] 新增「應用程式碼路徑」`app/metrics.py`：
  - 模組級宣告 metric（使用 `prometheus_client`）：
    - `REDIRECT_TOTAL = Counter("qr_redirect_total", "...", ["cache_result"])`
    - `IMAGE_CACHE_TOTAL = Counter("qr_image_cache_total", "...", ["result"])`
    - `CLICK_STREAM_PUBLISHED = Counter("qr_click_stream_published_total", "...")`
    - `CLICK_STREAM_CONSUMED = Counter("qr_click_stream_consumed_total", "...")`
    - `CLICK_STREAM_LAG = Gauge("qr_click_stream_lag", "...")`
    - `DB_POOL_IN_USE = Gauge("qr_db_pool_in_use", "...")`
  - 暴露 helper：`observe_redirect(cache_result: str)`、`observe_image_cache(result: str)`、`observe_publish()`、`observe_consume(n: int)`、`set_stream_lag(v: int)`、`set_db_pool_in_use(v: int)`
- [ ] 在 `app/main.py` lifespan：
  - 呼叫 `configure_logging()`、`init_tracing("qr-api")`
  - 掛 `prometheus_fastapi_instrumentator.Instrumentator().instrument(app).expose(app, endpoint="/metrics")`
  - 啟動 `asyncio.create_task` 每 5 秒抽 `engine.pool.checkedout()` 寫 `DB_POOL_IN_USE`；lifespan shutdown 時 cancel

### Backend — 手動 spans + metric hook（修改既有檔）
- [ ] 修改 `app/services/qr_service.py`：在 `create` / `get` / `list` / `update` / `delete` 各包一個 `tracer.start_as_current_span("qr_service.<op>")`，在處理單一 token 的 span 設 `span.set_attribute("qr.token", token)`
- [ ] 修改 `app/services/image_service.py`：
  - `cache_lookup`：span `image_service.cache_lookup`，attributes `qr.image.spec_hash`、`qr.cache_result`；hit/miss 各 inc `IMAGE_CACHE_TOTAL`
  - `generate`：span `image_service.generate`，attribute `qr.image.spec_hash`
- [ ] 修改 `app/services/click_stream.py`：
  - `publish_click`：span `click_stream.publish`，attribute `stream.entry_id`；成功後 `observe_publish()`
  - 新增 `consume_batch` wrapper（或於 worker 處）span `click_stream.consume_batch`，attribute `batch.size`
  - `ensure_group`：改 `except redis.exceptions.ResponseError as exc:` 再判 `BUSYGROUP`
  - `xpending_range` 的 `count` 抽成 `_PENDING_FETCH_LIMIT` 模組常數（預設 500），`claim_stale` 接 `count: int | None = None` 參數覆寫
- [ ] 修改 `app/main.py`：
  - redirect handler 在 cache lookup 後依 hit/miss 呼叫 `observe_redirect("hit" | "miss")`，並在當前 span 設 `qr.cache_result`、`qr.token`
  - `_record_click` swallow 區塊加 `logger.warning("publish_click failed", error=str(exc))`，並維持 swallow（不影響 302）
- [ ] 修改 `app/worker.py`：
  - 啟動時呼叫 `configure_logging()`、`init_tracing("qr-worker")`
  - `run_once(redis, consumer_name, buffer, *, batch_count: int = 500, block_ms: int = 0)`：包 span `worker.run_once`，attribute `batch.size`
  - `main()` 改用 `run_once(..., batch_count=BATCH_SIZE, block_ms=1000)`，消除重複的 14 行 batch-processing 邏輯
  - `_ts_to_hour` fallback 路徑 `logger.warning("unparseable ts", ts=ts)`
  - 主迴圈 idle tick 量 `XLEN` − group last-delivered，呼叫 `set_stream_lag(...)`；flush 後呼叫 `observe_consume(len(ack_ids))`
- [ ] 修改 `app/jobs/flush_clicks.py`：把 `logging.getLogger(__name__)` 換成 `structlog.get_logger(__name__)`，原有 log 行語意保留

### Infra — docker-compose stack
- [ ] 修改「`docker-compose.yml`」：
  - 新增 service `jaeger`（image `jaegertracing/all-in-one:latest`，ports `16686:16686`、`4318:4318`，env `COLLECTOR_OTLP_ENABLED=true`）
  - 新增 service `prometheus`（image `prom/prometheus:latest`，掛 `./docker/prometheus.yml:/etc/prometheus/prometheus.yml`，ports `9090:9090`）
  - 新增 service `grafana`（image `grafana/grafana:latest`，掛 `./docker/grafana/provisioning:/etc/grafana/provisioning`、`./docker/grafana/dashboards:/var/lib/grafana/dashboards`，ports `3000:3000`，env 預設 admin/admin）
  - `api` / `worker` env 補：`OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318`、確保 `ENVIRONMENT=local-compose`
  - `api` depends_on 增加 `jaeger`
- [ ] 新增「`docker/prometheus.yml`」：
  - `global.scrape_interval: 5s`
  - `scrape_configs` 一個 job `qr-api`，target `api:8080`
- [ ] 新增「`docker/grafana/provisioning/datasources/prometheus.yml`」：宣告 prometheus datasource (`http://prometheus:9090`) 為 default
- [ ] 新增「`docker/grafana/provisioning/dashboards/qr.yml`」：file provider 指向 `/var/lib/grafana/dashboards`
- [ ] 新增「`docker/grafana/dashboards/qr.json`」：至少含以下 panel：
  - Stat: 5m redirect QPS
  - Time series: redirect rate by `cache_result`
  - Time series: redirect duration p50 / p95 / p99（從 `http_request_duration_seconds_bucket{handler="/r/{qr_token}"}`）
  - Stat: image cache hit rate `rate(qr_image_cache_total{result="hit"}[5m]) / rate(qr_image_cache_total[5m])`
  - Time series: published vs consumed rate
  - Time series: `qr_click_stream_lag`
  - Time series: `qr_db_pool_in_use`

### 依賴
- [ ] 修改「`requirements.txt`」新增：
  - `opentelemetry-sdk`
  - `opentelemetry-exporter-otlp-proto-http`
  - `opentelemetry-exporter-gcp-trace`
  - `opentelemetry-instrumentation-fastapi`
  - `opentelemetry-instrumentation-sqlalchemy`
  - `opentelemetry-instrumentation-redis`
  - `opentelemetry-instrumentation-requests`
  - `prometheus-fastapi-instrumentator`
  - `prometheus-client`（若尚未間接帶入則明列）
  - `structlog`

### Docs
- [ ] 更新「`README.md`」：
  - 新增「Observability (Sprint B)」章節：說明三本柱、Jaeger / Prometheus / Grafana 進入點 URL、如何用 `trace_id` 跨工具查
  - 在「Architecture」加一句 OTel dual exporter 由 `ENVIRONMENT` 決定
  - 列 Grafana dashboard 預設密碼與 import 位置
- [ ] 更新「`CLAUDE.md`」：
  - 新增「Observability」段，描述 `app/observability.py` / `app/logging.py` / `app/metrics.py` 三檔職責
  - 在 prod 部署段補一句 service account 需要 `roles/cloudtrace.agent`
  - 連到 ADR-0002

## API 不變動聲明（contract test 必驗）

下列 endpoints 在 Sprint B 前後 response body schema / status code / headers byte-identical（新增 `/metrics` 不影響既有 endpoints）：

| Method | Path | 不變動要點 |
| --- | --- | --- |
| POST | `/v1/qr_code` | 201, `{"qr_token": "..."}` |
| GET | `/v1/qr_codes` | 200 |
| GET | `/v1/qr_code/{token}` | 200 / 410 / 404 |
| PUT | `/v1/qr_code/{token}` | 204 |
| DELETE | `/v1/qr_code/{token}` | 204 |
| GET | `/v1/qr_code_image/{token}` | 200, `image/png`, `Cache-Control: public, max-age=300, must-revalidate` |
| GET | `/r/{token}` | 302, `Location` 正確 |
| POST | `/internal/flush_clicks` | 200 / 403 |

## Out of scope
- Alerting / SLO / error budget
- Log aggregation backend（Loki / Cloud Logging sink）
- OTel collector sidecar
- Cross-service propagator 客製
- Cloud Run 部署參數變更（VPC / SA scope）
- k6 壓測（Sprint C）

## Tests required

### 新增 `tests/test_observability.py`
- [ ] `test_init_tracing_local_uses_otlp`：set `ENVIRONMENT=local`，呼叫 `init_tracing` → `current_exporter_kind() == "otlp"`
- [ ] `test_init_tracing_production_uses_gcp`：set `ENVIRONMENT=production` + monkeypatch `CloudTraceSpanExporter` 避免實際 GCP 呼叫 → `current_exporter_kind() == "gcp"`
- [ ] `test_init_tracing_other_env_noop`：set `ENVIRONMENT=test` → `current_exporter_kind() == "noop"`，但 `get_tracer().start_as_current_span(...)` 不噴錯
- [ ] `test_init_tracing_idempotent`：連呼兩次只初始化一次（用 patched exporter 觀察 call count）
- [ ] `test_logging_emits_json_with_trace_context`：configure_logging 後在一個 active span 內 `logger.info("hello", foo=1)` → capture stdout → 解析為 JSON dict → 含 `event=hello`, `foo=1`, `trace_id` 非空, `span_id` 非空, `level=info`, ISO `timestamp`
- [ ] `test_logging_outside_span_has_empty_trace_id`：無 active span 時 log 行的 `trace_id` 為空字串

### 新增 `tests/test_metrics.py`
- [ ] `test_metrics_endpoint_exposes_custom_counters`：起 TestClient → 觸發一次 redirect → `GET /metrics` body 含 `qr_redirect_total`、`qr_image_cache_total`、`qr_click_stream_published_total`、`qr_click_stream_consumed_total`、`qr_click_stream_lag`、`qr_db_pool_in_use` 全部六個 metric 名稱
- [ ] `test_redirect_increments_cache_hit_counter`：兩次 redirect（先 miss 後 hit）→ scrape `/metrics` → `qr_redirect_total{cache_result="hit"}` 與 `{cache_result="miss"}` 都 ≥ 1
- [ ] `test_image_cache_counter_hit_and_miss`：對同一 token 兩次 `GET /v1/qr_code_image` → `qr_image_cache_total{result="miss"} == 1` 且 `{result="hit"} == 1`
- [ ] `test_publish_counter_increments_on_redirect`：redirect 一次 → `qr_click_stream_published_total` 增加 1
- [ ] `test_worker_run_once_increments_consumed`：預先 XADD 3 筆 → 呼叫 `run_once` → `qr_click_stream_consumed_total` 增加 3

### 新增 `tests/test_span_attributes.py`
- [ ] 使用 `InMemorySpanExporter`（OTel SDK 提供）作 test exporter
- [ ] `test_redirect_span_has_cache_result_attribute`：redirect 一次 → finished spans 內找到 redirect handler span → attributes 含 `qr.cache_result` ∈ `{hit, miss}`、`qr.token`
- [ ] `test_image_cache_span_has_spec_hash`：image endpoint 一次 → 找到 `image_service.cache_lookup` span → 含 `qr.image.spec_hash`、`qr.cache_result`
- [ ] `test_publish_span_has_entry_id`：redirect 一次 → 找到 `click_stream.publish` span → `stream.entry_id` 非空
- [ ] `test_worker_run_once_span_has_batch_size`：worker run_once 處理 N 筆 → 找到 `worker.run_once` span → `batch.size == N`

### Sprint A QA warnings 對應測試（顯式驗證）
- [ ] `test_record_click_failure_emits_warning_log`：mock `publish_click` 噴 `RuntimeError` → 對 `/r/{token}` 發請求 → 仍回 302、log 中可見一行 `event=publish_click failed`、`error=...`
- [ ] `test_run_once_respects_batch_count_param`：呼叫 `run_once(..., batch_count=2)` 在 stream 有 5 筆時，單次只處理 2 筆
- [ ] `test_main_loop_calls_run_once`：用 mock `run_once` 跑一輪主迴圈 → 確認 `run_once` 被呼叫（取代行內重複邏輯）
- [ ] `test_worker_dedupe_skips_replay_real_xclaim`：改寫既有 case——consumer-A 讀後不 ack；consumer-B `claim_stale(min_idle_ms=0)` 領回；run_once 處理（hash 為 1）；再 inject 已存在 dedupe key 後 claim 同筆並 run_once → hash 仍為 1
- [ ] `test_ensure_group_catches_response_error_only`：mock redis client 噴 `ValueError`（非 ResponseError）→ `ensure_group` 應 raise；噴 `ResponseError("BUSYGROUP ...")` → swallow
- [ ] `test_claim_stale_count_param`：呼叫 `claim_stale(..., count=10)` 時驗證 `xpending_range` 用 count=10

### 既有測試
- [ ] 既有 `tests/test_qr.py`、`tests/test_click_stream.py` 全部 38 case 保持綠燈
- [ ] 若有 test 因 instrumentation 而需要 fixture（如 reset OTel state）統一在 `tests/conftest.py` 處理；不得改動任何斷言

### 手動 / e2e 驗證（記錄在 QA report）
- [ ] `docker-compose up` 後對 `/r/{token}` 發數十次請求
- [ ] 開 `http://localhost:16686` 選 service `qr-api`，看到 redirect trace 含 child spans + `qr.cache_result` attribute
- [ ] `curl http://localhost:8000/metrics | grep qr_` 顯示六個自定義 metric
- [ ] 開 `http://localhost:3000`（admin/admin）載入預設 dashboard，看到 QPS / p99 / cache hit rate panel 有資料
- [ ] 隨機抓 `docker compose logs api | head -n 1` → 確認是 JSON 且含非空 `trace_id`
- [ ] 用該 `trace_id` 在 Jaeger UI 「Lookup by Trace ID」可查到對應 trace

## Definition of done
- 全部 deliverable 打勾
- 專案測試指令 `pytest tests/ -v` 全綠
- API 不變動聲明逐項通過 contract test（既有 38 case）
- `docker-compose up` 可一鍵起完整 stack（api + worker + redis + postgres + jaeger + prometheus + grafana）
- Jaeger UI 看得到帶 `qr.cache_result` 的 trace
- `/metrics` 暴露六個自定義 metric
- Grafana dashboard 顯示 QPS / p99 / cache hit rate
- 任一 log 行是 JSON 且含 `trace_id` 欄位
- `ENVIRONMENT=production` 時 Cloud Trace exporter 啟用、`local` 時 OTLP 啟用，由測試斷言
- Sprint A 5 條 QA warnings 在 QA report 內顯式逐條確認已清除
- evaluator 的 QA report 無 P0/P1
