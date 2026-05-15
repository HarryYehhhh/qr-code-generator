# ADR-0004: 簡化觀測性——移除 OTel + Prometheus，保留 structlog

- **Status**: Accepted
- **Date**: 2026-05-15
- **Supersedes (partially)**: [ADR-0002 — OpenTelemetry with dual exporter](0002-otel-with-dual-exporter.md)
- **Related**: [ADR-0003 — 移除 click counting](0003-remove-click-counting-mvp.md)（同一波 scope 收斂）

---

## Context

Sprint B 加入了三本柱觀測性：OpenTelemetry tracing（雙 exporter：local Jaeger / prod Cloud Trace）+ Prometheus metrics（`/metrics` endpoint + 自訂 counter/gauge）+ structlog JSON logging（含 trace_id correlation）。Sprint C 完成 baseline vs current 壓測後、ADR-0003 移除 click counting 後，重新檢視這三件套的 ROI：

| 工具 | 解決的問題 | 在這個 single-instance + 0 microservice 的專案內 |
|---|---|---|
| OTel tracing | 跨 service 排查 / 看請求在 5 跳裡哪一跳慢 | 全部請求在 1 process 內處理完，幾乎沒有跨 service hop |
| Prometheus + Grafana | 自製 dashboard、設 alert rule、看歷史趨勢 | Cloud Run console 已經免費提供 latency / error rate / RPS / log search |
| structlog JSON | log 跨工具 grep / 結構化 query / Cloud Logging 自動解析欄位 | **真實有用**——production 上 stdout JSON → Cloud Logging 直接吃，本地 dev 也比 stdlib 友善 |

實測壓測（[`docs/perf-baseline-comparison.md`](../perf-baseline-comparison.md)）顯示 OTel SDK 在 hot 路徑造成 ~5–10 % throughput regression（baseline 2060 → current 1901 RPS、p50 從 66 → 71 ms）。也就是「我們花了 cost 但對單機 single-process 應用沒拿到對等價值」。

## Decision

**砍掉 OTel + Prometheus stack，保留 structlog。**

對齊 ADR-0003「focus on redirect」的精神——把不對 MVP 創造價值的 stack 拆出去，但**保留有實質效益的 structured logging**。

### 具體刪除

| 元件 | 動作 |
| --- | --- |
| `app/observability.py` | 整檔刪 |
| `app/metrics.py` | 整檔刪 |
| `app/main.py` 中的 `init_tracing` / `Instrumentator` / 手寫 span / `observe_*` 呼叫 | 移除 |
| `app/services/qr_service.py` 與 `image_service.py` 中的手寫 span 與 `observe_image_cache` | 移除 |
| `app/logging.py` 內的 `_add_trace_context` processor（OTel trace_id / span_id 注入） | 移除（structlog 本身保留） |
| `docker-compose.yml` 的 `jaeger` / `prometheus` / `grafana` service + 對應 `OTEL_EXPORTER_OTLP_ENDPOINT` env | 移除 |
| `docker/prometheus.yml` 與 `docker/grafana/` 整目錄 | 移除 |
| `tests/test_observability.py` / `test_metrics.py` / `test_span_attributes.py` | 整檔刪 |
| `tests/conftest.py` 的 `set_test_environment` 與 `global_exporter` fixture | 移除 |
| `requirements.txt` | 移除 9 個套件：`opentelemetry-sdk`、`opentelemetry-exporter-otlp-proto-http`、`opentelemetry-exporter-gcp-trace`、`opentelemetry-instrumentation-fastapi`、`opentelemetry-instrumentation-sqlalchemy`、`opentelemetry-instrumentation-redis`、`opentelemetry-instrumentation-requests`、`opentelemetry-api`、`prometheus-fastapi-instrumentator` |

### 保留

- `app/logging.py` 簡化版（`structlog` JSON formatter，無 trace 注入）
- `structlog` 與 `prometheus-client`（後者保留作為將來真要 metric 時的依賴；目前無用但很輕，1 個檔 import 沒成本）—— **改：連 `prometheus-client` 也一起刪**，真要再加回來
- ADR-0002 文件保留作為當時決策過程的紀錄，不刪
- Sprint B spec / contract / QA report 保留作為「我做過完整觀測性」的歷史

### Production observability 改用什麼

- **Logs**：FastAPI / structlog → stdout JSON → **Cloud Logging 自動接**，可以做 log-based query / metrics / alert，不用任何額外 SDK
- **Metrics**：**Cloud Run console 自動提供** request latency p50/p95/p99 + error rate + container CPU / memory + instance count，零 config
- **Traces**：**不再實作**。將來真的需要再加 OTel SDK + Cloud Trace exporter（單一 exporter，比 Sprint B 的 dual exporter 簡單）

## Consequences

### 正向

- **Codebase 縮小** ~600 LOC + 9 個 dependency + 3 個 docker container
- **架構敘事乾淨**：「FastAPI + Redis cache + Postgres + structured JSON logs」一句話講完
- **k6 壓測重新衡量**：預期 current 的 redirect_hot 跟 baseline 持平（OTel SDK overhead 拿掉後）
- **Operational 心智負擔降低**：不用維運 prometheus.yml / grafana provisioning / dashboard JSON 三套配置
- **GCP 帳單潛在節省**：Memorystore 壓力降低（少了 OTel 的 redis instrumentation 在每個 op 上加 span）

### 負向

- **失去 demo material**：「我裝過 Jaeger / Grafana」這條面試敘事點要改用「我裝過、評估過、決定砍掉」這種更高一層的敘事撐
- **失去自製 metric 的能力**：將來想看「cache hit rate per minute」這類 query 要靠 log-based metric（`jq` count + GCP Logs Explorer）
- **Sprint B 的工作大部分變成歷史**：spec / contract 保留；code 大幅刪除

### 中性

- **structlog 留著**：log 行為基本不變，只是不再帶 `trace_id` 欄位（沒有 trace 可關聯）
- **k6 壓測腳本不動**：k6 自己量 RPS / p99，從來不依賴 Prometheus
- **Sprint C perf-report**：數字仍然有效（量的是 redirect 路徑），但要加註「OTel overhead 已移除」

## Alternatives considered

### A. 全砍光，連 structlog 也回 stdlib `logging`
- **拒絕**：structlog 跟 stdlib 配置難度差不多，但 production grep / Cloud Logging 解析的價值是真的。多寫 30 行設定 enable JSON 格式是好交易。

### B. 保留 OTel + Cloud Trace exporter（砍 Jaeger / Prometheus / Grafana）
- **拒絕**：手寫 span 散在 5 個檔案、SDK 啟動成本、4 個 instrumentor 套件。為了「將來可能要 trace」這個 hypothetical 太貴。真要時再加。

### C. 保留 Prometheus（砍 OTel）
- **拒絕**：自製 metric 在這個應用其實沒看過——Cloud Run console 已經 cover 95 %。剩下 5 % 用 log-based metric 能做。

### D. 不動，留著
- **拒絕**：跟 ADR-0003 不對齊。如果說那次砍掉是因為 click counting 對 MVP 沒價值，這次留著就是雙重標準。

## 對未來的人的提示

如果哪天真的要再加觀測性，**從最小開始**：

1. 先看 stdout logs 跟 Cloud Run console 不夠用什麼
2. 不夠的話**只加單一工具**（要追跨 service trace 就只裝 OTel + 單一 exporter；要做自製 dashboard 就只裝 Prometheus）
3. **不要** 一次裝三件套
4. 加之前先寫 ADR 說「為什麼這次跟 ADR-0004 不同」
