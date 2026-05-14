# QA Report: sprint-b-observability

- **Contract**: docs/contracts/sprint-b-observability.md
- **Spec**: docs/specs/sprint-b-observability.md
- **ADR**: docs/decisions/0002-otel-with-dual-exporter.md
- **Date**: 2026-05-14
- **Verdict**: ⚠️ Pass with issues（無 P0/P1 阻擋合併；Docs 章節未交付屬 P2，建議補上）

## Contract checklist

### Backend — `app/observability.py`
| Deliverable | Status | Note |
|---|---|---|
| `init_tracing(service_name)` dual exporter | ✅ | local/local-compose→OTLP、production→GCP、其他→noop |
| 讀 `OTEL_EXPORTER_OTLP_ENDPOINT`（預設 `http://localhost:4318`）| ✅ | line 54 |
| `Resource` 含 `service.name` + `service.version`（env `APP_VERSION`，預設 `dev`） | ✅ | line 41-46 |
| 自動 instrumentation（FastAPI / SQLAlchemy / Redis / requests） | ✅ | `_apply_auto_instrumentation`；個別 try/except 避免雙重 instrument 噴錯 |
| `init_tracing` idempotent | ✅ | `_initialized` flag；`test_init_tracing_idempotent` 通過 |
| `get_tracer` / `is_initialized` / `current_exporter_kind` | ✅ | 全暴露 |

### Backend — `app/logging.py`
| Deliverable | Status | Note |
|---|---|---|
| structlog processor chain（含 `add_log_level` / iso `TimeStamper(utc=True)` / `StackInfoRenderer` / `format_exc_info` / `_add_trace_context` / JSONRenderer） | ✅ | line 56-79 |
| stdlib logging 透過 `ProcessorFormatter` 走同一條 chain | ✅ | line 76-89 |
| Log level 預設 `INFO`，env `LOG_LEVEL` 覆寫 | ✅ | line 52 |
| `_add_trace_context` 在 INVALID_SPAN 時填空字串 | ✅ | `test_logging_outside_span_has_empty_trace_id` 通過 |
| `get_logger` thin wrapper | ✅ | line 98 |

### Backend — `app/metrics.py`
| Deliverable | Status | Note |
|---|---|---|
| 六個 metric（REDIRECT_TOTAL / IMAGE_CACHE_TOTAL / CLICK_STREAM_PUBLISHED / CLICK_STREAM_CONSUMED / CLICK_STREAM_LAG / DB_POOL_IN_USE） | ✅ | label 設定正確（`cache_result` / `result`） |
| Helper（observe_redirect / observe_image_cache / observe_publish / observe_consume / set_stream_lag / set_db_pool_in_use） | ✅ | |
| lifespan 呼 `configure_logging` + `init_tracing("qr-api")` | ✅ | `app/main.py:41-44` |
| `Instrumentator().instrument(app).expose(app, "/metrics")` | ⚠️ | 在 module load 時掛（line 66-67）而非 lifespan 內；功能等價，但與 contract 字面有出入 |
| 每 5 秒 background task 更新 `DB_POOL_IN_USE` + shutdown cancel | ✅ | `_pool_monitor_task` + lifespan finally cancel |

### Backend — 手動 spans / metric hooks
| Deliverable | Status | Note |
|---|---|---|
| `qr_service.{create,get,list,update,delete}` 包 span | ✅ | 涉及單一 token 處 `set_attribute("qr.token", ...)` |
| `image_service.cache_lookup` span + `qr.image.spec_hash` + `qr.cache_result` + metric | ✅ | |
| `image_service.generate` span + `qr.image.spec_hash` | ✅ | |
| `click_stream.publish` span + `stream.entry_id` + metric | ✅ | |
| `click_stream.consume_batch` wrapper | ⚠️ | Contract 註明「或於 worker 處」；以 `worker.run_once` span（含 `batch.size`）取代。Spec acceptance 仍列 `click_stream.consume_batch` 為手動 span（line 22）。視為合理替代但語意略漂移 |
| `ensure_group` 改 `redis.exceptions.ResponseError` | ✅ | `click_stream.py:56-59` |
| `_PENDING_FETCH_LIMIT` 常數 + `claim_stale(count=...)` 參數 | ✅ | line 20、line 90-104 |
| redirect handler 設 `qr.cache_result` / `qr.token` + `observe_redirect` | ✅ | `app/main.py:127-142` |
| `_record_click` swallow 加 `logger.warning("publish_click failed", error=...)` | ✅ | line 155-157 |
| `worker.run_once(..., batch_count, block_ms)` + span `batch.size` | ✅ | `worker.py:82-116` |
| `main()` 用 `run_once(...)` 取代 inline 邏輯 | ✅ | `worker.py:184` |
| `_ts_to_hour` fallback warning | ✅ | line 77 |
| 主迴圈 `set_stream_lag` + flush 後 `observe_consume` | ✅ | line 187-206 |
| `flush_clicks.py` stdlib → structlog | ✅ | line 3、line 8 |

### Infra — docker-compose / Prometheus / Grafana
| Deliverable | Status | Note |
|---|---|---|
| jaeger service（16686 / 4318, COLLECTOR_OTLP_ENABLED=true） | ✅ | |
| prometheus service（9090, 掛 `docker/prometheus.yml`） | ✅ | |
| grafana service（3000, 掛 provisioning + dashboards） | ✅ | admin password 設 admin |
| api/worker env：`OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318`、`ENVIRONMENT=local-compose` | ✅ | |
| api depends_on 增 jaeger | ✅ | |
| `docker/prometheus.yml` scrape api:8080 每 5s | ✅ | |
| `docker/grafana/provisioning/datasources/prometheus.yml`（default） | ✅ | |
| `docker/grafana/provisioning/dashboards/qr.yml` file provider | ✅ | |
| `docker/grafana/dashboards/qr.json` 7 panels | ✅ | 全部齊（QPS / by cache / p50p95p99 / image hit / pub vs con / lag / db_pool） |

### 依賴
| Deliverable | Status | Note |
|---|---|---|
| 10 個新 observability 套件加入 requirements.txt | ✅ | 但未鎖版本（ADR-0002 提到應鎖 `opentelemetry-exporter-gcp-trace`，目前全部 unpinned） |

### Docs
| Deliverable | Status | Note |
|---|---|---|
| README「Observability (Sprint B)」章節 | ❌ | 未新增（grep 不到 observability / jaeger / prometheus / grafana） |
| README「Architecture」加 OTel dual exporter 一句 | ❌ | 未新增 |
| CLAUDE.md「Observability」段（三檔職責 + ADR-0002 + `roles/cloudtrace.agent`） | ❌ | 未新增（grep 不到任何相關字眼） |

## API 不變動聲明
- 既有 38 個 case（`tests/test_qr.py` 28 + `tests/test_click_stream.py` 10）全綠 → byte-identical 行為驗證通過。

## Sprint A 5 條 QA warnings 清除驗證
| # | QA Item | 對應測試 | 真實驗證 |
|---|---------|---------|---------|
| 1 | `_record_click` swallow 加 warning log | `test_record_click_failure_emits_warning_log` | ✅ patch `app.main.publish_click` 噴 RuntimeError、捕到 `publish_click failed` event；redirect 仍 302 |
| 2 | `run_once` 參數化 + `main()` 改用 `run_once` | `test_run_once_respects_batch_count_param` + `test_main_loop_calls_run_once` | ✅ batch_count=2 時實際只 accept 2 筆；`worker.py:184` 主迴圈確實呼叫 `run_once(...)` |
| 3 | dedupe replay 改走真實 XCLAIM | `test_worker_dedupe_skips_replay_real_xclaim` | ✅ 完整跑 consumer-A 不 ack → consumer-B `claim_stale(min_idle_ms=0)` 領回 → 第一輪 mark_processed+flush（count=1）→ 第二輪 inject 已存在 dedupe key → mark_processed False、走 ack 分支（count 仍為 1） |
| 4 | `ensure_group` 改 ResponseError 判斷 | `test_ensure_group_catches_response_error_only` | ✅ ValueError propagate；ResponseError("BUSYGROUP…") swallow；ResponseError("WRONGTYPE…") propagate；三種情境都驗到 |
| 5 | `xpending_range count` 抽常數 + `claim_stale(count=)` | `test_claim_stale_count_param` | ✅ `_PENDING_FETCH_LIMIT = 500`；mock 驗 `xpending_range` 被傳 count=10 |

**結論**：5 條全部「真實清掉」，不是貼標籤。

## Test results

- 測試指令：`pytest tests/ -v`
- 結果：**60 passed / 0 failed / 0 skipped**（既有 38 + 新增 22）
- 失敗測試：無
- 既有 suite regression：✅ 無

### Stderr 噪音
`OTLPSpanExporter` 的 BatchSpanProcessor 背景 thread 在某些 test 結束後試圖連 `localhost:4318`、印出 `ConnectionError + ValueError: I/O operation on closed file`。**不影響 pytest 判定**（exit code 0、60 passed），但 dev/CI log 會被污染。Generator 在 known limit 中已自述。

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）

- **`README.md` / `CLAUDE.md` 未新增 Observability 章節**
  Contract Docs 章節三條 deliverable 全未交付：README 沒「Observability (Sprint B)」段、沒在 Architecture 補 OTel dual exporter 一句、CLAUDE.md 沒新增 Observability 段（含三檔職責 / ADR-0002 連結 / prod 需 `roles/cloudtrace.agent` 提醒）。
  影響：使用者 / 新人沒入口理解三本柱與切換邏輯；prod 部署時可能漏 IAM role。
  建議：依 contract line 100-108 補齊三段文字；CLAUDE.md 連到 docs/decisions/0002-otel-with-dual-exporter.md。

- **`requirements.txt` observability 依賴未鎖版本**
  ADR-0002「負面 / Trade-off」明白寫「需在 `requirements.txt` 鎖版本」，目前 10 個套件全部 unpinned（line 15-24）。
  影響：未來 OTel SDK 與 contrib package（特別是 `opentelemetry-exporter-gcp-trace`）升級節奏不同步可能造成 prod build 紅燈。
  建議：用 `pip freeze | grep -i opentelemetry\|prometheus\|structlog` 把當前版本固定下來。

- **BatchSpanProcessor stderr 污染**
  測試結束時 OTel exporter background thread 試圖 export 已 captured spans，連 `localhost:4318` 失敗噴 stack trace；且 stdout 已關閉造成二次 `I/O operation on closed file` log error。
  影響：dev/CI log 噪音；CI 若 grep stderr 判斷會誤報。
  建議：`conftest.py` 在 session teardown 顯式 `trace.get_tracer_provider().shutdown()` 或避免 `init_tracing()` 在 test session 中真的掛上 BatchSpanProcessor（目前 conftest 已強制 `ENVIRONMENT=test`，但 `test_init_tracing_local_uses_otlp` / `test_init_tracing_idempotent` 在 test 內 monkeypatch 成 local 並 mock OTLP class——但 fixture teardown 後 module level state 可能殘留）。

- **`app/services/click_stream.consume_batch` wrapper 未實作**
  Spec acceptance criteria（line 22）列 `click_stream.consume_batch` 為手動 span 之一；contract line 52 給的選項是「或於 worker 處」，generator 選擇後者，以 `worker.run_once` 取代。
  影響：低（功能等價、`batch.size` attribute 與 `qr_click_stream_consumed_total` 都在）；但若日後新增非 worker caller（例如同步 drain helper），會缺一層共用 span。
  建議：把 `worker.run_once` 內部 `read_batch + mark_processed + buffer.add` 那段抽成 `click_stream.consume_batch(redis, consumer_name, count, block_ms) -> list[(entry_id, fields)]`，run_once 包外層 span 同名；或在 README 明白標示 spec 對應決策。

- **`app/main.py:66-67` Instrumentator 在 module load 而非 lifespan 內**
  Contract line 41-43 寫「在 `app/main.py` lifespan：掛 Instrumentator」。目前在 `FastAPI(...)` 建構完直接 `.instrument(app).expose(app, ...)`。
  影響：技術上仍能跑（FastAPI app 物件已存在）；但與 contract 字面有差距，未來若引入 conditional disable 邏輯會麻煩。
  建議：移到 lifespan 啟動段；或更新 contract 接受 module-level 掛載。

### 🟢 Suggestions

- **`app/main.py:22` 在 module import 時 `configure_logging()` 又在 lifespan 再呼叫一次**
  Idempotent 所以無害，但顯得冗餘。可只在 lifespan 呼叫；或 module-level 呼叫後 lifespan 不重複。

- **`app/main.py:102-110` `_get_tracer()` 用 module-level `_tracer` cache**
  其他模組用 `_tracer = trace.get_tracer(__name__)`（module-level eager），main.py 用 lazy；風格不一致。Span 名稱用 `"redirect"` 而非 `"main.redirect"` / `"qr_api.redirect"`，與其他 `qr_service.create` / `image_service.cache_lookup` 命名規範不齊。

- **`app/worker.py:135-211` `main()` 仍有「30 行 boilerplate」沒進一步抽**
  可考慮把 `consumer_name / batch_size / flush_interval / claim_min_idle_ms` 收成 dataclass `WorkerConfig.from_env()`，main 函式更短。Sprint B 不強求。

- **`app/main.py:34` `_pool_monitor_task` 的 `except Exception: pass`**
  與 Sprint A 留下的 swallow 同類問題，建議至少 `logger.debug(...)`。

- **`docker-compose.yml` 沒設 `restart: unless-stopped`**
  Sprint A QA 也提過；新加的 jaeger/prometheus/grafana 若 crash 不會自動拉起。

- **Grafana dashboard `http_request_duration_seconds_bucket{handler=~"/r/.*"}` regex**
  `prometheus-fastapi-instrumentator` 預設 handler label 是 route template（例 `/r/{qr_token}`），用 `=~"/r/.*"` 可 match 也可 match unrelated path；建議 `handler="/r/{qr_token}"`。

### ✅ What looks good

- 雙 exporter 切換邏輯乾淨：`local/local-compose → OTLP`、`production → GCP`、其他 → noop，符合 ADR-0002。
- `init_tracing` 真的 idempotent，flag + 第二次呼叫 OTLPSpanExporter 建構次數 == 1 有測到。
- structlog `_add_trace_context` 處理 INVALID_SPAN 時填空字串，避免 log 行欄位忽有忽無——schema 穩定，下游 log query 好寫。
- six 個自定義 metric 名稱與 label 設計合理（`cache_result` / `result` 拆 hit/miss 給 Grafana 直接 group by）。
- Sprint A 五條 QA warnings 對應測試全部 **真實覆蓋對應行為路徑**（特別是 `test_worker_dedupe_skips_replay_real_xclaim` 真的走 XCLAIM 雙輪流程，而非貼標籤）。
- conftest 用 session-scoped `InMemorySpanExporter` 收所有 span、每個 test 用 `global_exporter` fixture clear，OTel state 隔離乾淨，避免互相污染。
- 7-panel Grafana dashboard 完整對應 contract line 78-85 列表，包含 5m QPS / cache 拆分 / p50p95p99 / image hit rate / pub vs con / lag / db_pool。
- docker-compose 完整一鍵起 stack（redis + postgres + api + worker + jaeger + prometheus + grafana），api depends_on jaeger 設好。
- API 不變動聲明驗到位：既有 38 case 全綠、redirect/cache/image/CRUD response schema byte-identical。

## 手動 / e2e 驗證
未執行（generator known limit 提到「docker-compose e2e 驗證需實際執行」，環境上不易在 sandbox 內 docker-compose up + curl Jaeger）。Contract line 169-175 列的 6 條手動驗證需在實機補測：
1. `docker-compose up` 後對 `/r/{token}` 發數十次請求
2. Jaeger UI 看到帶 `qr.cache_result` 的 trace
3. `curl /metrics | grep qr_` 看到六個 metric
4. Grafana dashboard 有資料
5. log 是 JSON 含非空 trace_id
6. 用該 trace_id 在 Jaeger 查到對應 trace

建議補一次手動 e2e 紀錄（screenshot 或 curl output）夾在本 QA report 末段或 PR description。

## 結論
- **是否可合併**：是（無 P0/P1）。
- **最該先處理的 3 件事**：
  1. 補 README.md / CLAUDE.md 的 Observability 章節（contract Docs 三條 deliverable 未交付）。
  2. `requirements.txt` 鎖版本（ADR-0002 明文要求）。
  3. 修 BatchSpanProcessor stderr 污染（conftest teardown 加 `provider.shutdown()` 或避免 test 中觸發實際 OTLP exporter）。
