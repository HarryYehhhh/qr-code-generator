# ADR-0002: 用 OpenTelemetry SDK + 雙 exporter（OTLP→Jaeger / Cloud Trace）取代單一 vendor SDK

- **Date**: 2026-05-14
- **Status**: Accepted

## Context

Sprint B 要為 QR Code Generator（FastAPI API + 獨立 worker）導入 distributed tracing。目標：
- 本地 `docker-compose` 開發 / demo 時，能在自架 UI 看到完整 trace。
- 部署到 Cloud Run 時，trace 直接寫進 Cloud Trace，且能與 Cloud Logging 透過 `trace_id` 自動關聯。
- 既有自動 instrumentation（FastAPI、SQLAlchemy、Redis、requests）能直接套用，不需重寫攔截邏輯。
- 不為了一個面試展示專案多養一個 OTel collector sidecar / DaemonSet。

候選方案：

1. **OpenTelemetry SDK + 雙 exporter**
   - 同一份 instrumentation code，跑 `init_tracing()` 時依 `ENVIRONMENT` 選 `OTLPSpanExporter`（本地→Jaeger 的 `4318/v1/traces`）或 `CloudTraceSpanExporter`（prod）。
2. **只用 Cloud Trace SDK（`google-cloud-trace`）**
   - 直接呼叫 GCP client，本地需要 GCP credentials + 連 cloud；或 mock。
3. **OTel SDK + 單一 OTLP exporter，本地直送 Jaeger、prod 透過 OTel collector 轉送 Cloud Trace**
   - 標準 cloud-native 做法，但需多跑一個 collector。
4. **完全不導 tracing，只靠 logs / metrics**
   - 成本最低，但 sprint goal 明確要 traces。

## Decision

採用方案 1：**OpenTelemetry SDK + 雙 exporter，由 `ENVIRONMENT` env 切換**。

- 本地 / `local-compose` → `OTLPSpanExporter` 直送 `http://jaeger:4318`。
- `production` → `CloudTraceSpanExporter`（`opentelemetry-exporter-gcp-trace` 套件）直送 Cloud Trace。
- 其他環境（測試）→ no-op，不掛 exporter。
- 自動 instrumentation 與手動 spans 一致；資源屬性 `service.name` 區分 `qr-api` / `qr-worker`。

## Consequences

### 正面
- 程式碼一份，本地與 prod 行為差異只在 exporter 與 endpoint，方便 debug 「為什麼某段 span 在 prod 看不到」這類問題。
- 換 vendor（將來想接 Honeycomb / Tempo / Datadog）只需多一個 exporter 分支，不動 instrumentation。
- OTel 的自動 instrumentation 生態完整，FastAPI / SQLAlchemy / Redis / requests 都有官方 package，省自寫 middleware 的力。
- Cloud Trace 與 Cloud Logging 自動依 `trace_id` 互相關聯，配合 structlog 注入 trace context 後不需額外設定。
- 不引入 collector → 部署拓樸維持「Cloud Run + Cloud SQL + Memorystore」三件，沒新增 ops burden。

### 負面 / Trade-off
- 多兩個依賴：`opentelemetry-exporter-otlp-proto-http`、`opentelemetry-exporter-gcp-trace`，image 變大幾 MB。
- 直送 Cloud Trace 表示每個 Cloud Run instance 各自打 API；若 QPS 很高，collector 的 batch / retry 會比直送好（目前 QPS 規模可忽略）。
- 「`ENVIRONMENT` 分支」是 application-level 邏輯而非 ops-level 配置，新人讀 code 時需理解切換點；以一個小 `app/observability.py` 收斂可接受。
- `opentelemetry-exporter-gcp-trace` 是 Google 維護的 contrib package，更新節奏與 OTel SDK 不完全同步——需在 `requirements.txt` 鎖版本，升級時一起測。

## Alternatives considered

- **方案 2（只用 Cloud Trace SDK）**：本地開發要嘛連 cloud 要嘛 mock，demo 流程脫離面試敘事的「微服務在自己機器上跑得起來」；換 vendor 全部要重寫。否決。
- **方案 3（OTel + collector）**：標準做法，但本專案規模不需要 batch / sampling / fan-out，多一個 service 拖累 docker-compose 啟動時間與部署複雜度。否決。
- **方案 4（不導 tracing）**：違背 sprint goal「三本柱」與面試敘事「服務拆分後我看得見它在做什麼」。否決。

未來若 QPS 規模上升或要接多個 backend，可從方案 1 升級到方案 3：把 exporter 改成統一 OTLP，再以 collector 做 fan-out，instrumentation code 完全不動。
