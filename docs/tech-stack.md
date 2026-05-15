# Tech Stack 計劃書 — 為什麼是這些工具

> 這份文件解釋專案每一項技術選型的**動機**：「為什麼用它、它解決什麼問題、面試被問到怎麼答」。
> 不討論 how-to（那是 README / CLAUDE.md 的職責），只討論 why。

## 0. 大原則：選型三條準則

我做每一項選型都會問三個問題：

1. **這個問題真的存在嗎？**（避免 over-engineering：例如沒幾個 user 就先上 Kafka）
2. **最輕量的解法是什麼？**（避免引入新基礎設施：能複用既有元件就不引入新的）
3. **如果規模 ×10，這個解法會在哪裡撞牆？**（避免 under-engineering：選了之後不能無法升級）

下面每個工具的「為什麼選它」都隱含這三條。

---

## 1. Application Framework

### FastAPI

| 維度 | 內容 |
|---|---|
| **是什麼** | Python 的 async web framework，基於 Starlette + Pydantic |
| **為何選它** | (1) Async-first 適合 I/O 密集的微服務（這個專案 redirect 路徑全是 I/O：Redis GET + Stream XADD）；(2) Pydantic schema 自動產生 OpenAPI 文件，省掉 contract test 大半的維護成本；(3) Dependency injection 系統讓測試很乾淨（`get_db`、`get_redis` 在 conftest 可以一行 override） |
| **替代品比較** | **Flask**：同步、需手寫 schema 驗證，async 是後加的。**Django**：太重，自帶 ORM/admin 不需要。**aiohttp**：低階、沒 schema 整合。**Litestar/BlackSheep**：更新但社群小、招聘端不熟 |
| **本專案用到的特性** | Lifespan（OTel/structlog/Instrumentator 初始化）、Dependency override（測試 Redis/DB mock）、BackgroundTasks（早期版本記錄 click，後被 stream 取代）、自動 OpenAPI（給前端 / 整合方對接） |
| **面試 FAQ** | **Q: 為什麼不用 Flask？** → 「redirect 是純 I/O，async 能讓單 process 同時處理多個 redirect 而不阻塞，預期 QPS 拉高時 Flask 同步模型會先撞 worker 上限。」<br>**Q: FastAPI async 怎麼跟 SQLAlchemy 整合？** → 「目前用同步 SQLAlchemy + `pool_size=1`，因為 Cloud SQL Connector 還沒完全支援 async。Async SQLAlchemy 是未來升級項。」 |

### Pydantic v2

| 維度 | 內容 |
|---|---|
| **是什麼** | Python 資料驗證庫，FastAPI 的 schema 引擎 |
| **為何選它** | (1) Type-driven validation：寫一次 `class CreateRequest(BaseModel)` 同時拿到「runtime 驗證 + IDE 提示 + OpenAPI schema」；(2) v2 底層用 Rust，比 v1 快 5–50 倍；(3) URL / Email 等內建 validator，省自己寫 regex |
| **替代品比較** | **marshmallow**：純 Python、慢、要另外維 schema；**dataclasses + 手動 validate**：不會送進 OpenAPI |
| **本專案用到的特性** | `HttpUrl` 自動驗證 URL 格式（防 SSRF 的第一道線）、`Field(min_length, max_length)` 卡住惡意輸入 |
| **面試 FAQ** | **Q: 怎麼防止使用者送惡意 URL？** → 「Pydantic 的 `HttpUrl` 先做格式 / scheme 白名單，再加自家 `validate_safe_url` 擋私網段／meta-data endpoint（SSRF 防護），最後 token 走 SHA-256 + Base62 不可預測。」 |

---

## 2. Data Layer

### PostgreSQL 15

| 維度 | 內容 |
|---|---|
| **是什麼** | Relational DB，OSS 界事實標準 |
| **為何選它** | (1) 寫入需要 ACID（避免 click_count 計算錯）；(2) `ON CONFLICT ... DO UPDATE`（UPSERT）讓 `flush_previous_hour` 的 idempotency 寫得很乾淨——沒這個語法就要 SELECT-then-INSERT 兩段；(3) GCP Cloud SQL 直接支援，部署成本低 |
| **替代品比較** | **MySQL**：UPSERT 用 `ON DUPLICATE KEY UPDATE`，語法較弱、JSON 支援差；**SQLite**：單機、寫鎖全表，撐不住高併發；**DynamoDB / NoSQL**：丟掉 schema 與 JOIN，token + click_stats 的關聯查詢會變麻煩 |
| **本專案用到的特性** | `ON CONFLICT (qr_token, hour_bucket) DO UPDATE` 做 hourly flush idempotency、UNIQUE constraint 擋 token 碰撞、partial index（status=active）加速查詢 |
| **面試 FAQ** | **Q: 為什麼不用 Redis 直接當資料庫？** → 「Redis 是 cache + 暫存，不持久（AOF 也有 fsync window）。Token 是不可丟資料（連結背後是真實 user content），必須走 RDBMS。」<br>**Q: 預期 QPS 多高？db-f1-micro 撐得住嗎？** → 「f1-micro max_connections 25，我設 `pool_size=1, max_overflow=2`，Cloud Run max_instances=6 → 18 connection 預算，剩 7 給管理連線。撞牆點是 `redirect_cold`（每次 INSERT），壓測量到的 RPS（待填）若不夠就要 token pre-generation pool。」 |

### Redis 7

| 維度 | 內容 |
|---|---|
| **是什麼** | In-memory data store，這個專案同時當 cache + queue + streaming |
| **為何選它（vs Memcached / Hazelcast）** | (1) **資料結構豐富**：除了 KV 還有 Hash / Stream / Set / SortedSet，這個專案一個 Redis 同時做三件事——URL cache（KV）、click counter（Hash）、click event stream（Stream），不用引入第二個系統；(2) Memorystore 在 GCP 是 managed service，省維運；(3) Memcached 沒 stream，沒 atomic counter group 操作 |
| **本專案用 Redis 做的三件事** | <br>**1. URL cache**（`qr:url:{token}` → string，TTL 1h）— redirect 99% 流量打這裡，不碰 DB<br>**2. Image cache**（`qr:img:{hash}` → PNG bytes，TTL 7d）— QR 圖每張 1–3KB，content-addressed<br>**3. Click stream**（`clicks:stream` → Stream + consumer group）— 把同步 HINCRBY 改成 producer/consumer |
| **面試 FAQ** | **Q: Redis 掛掉會怎樣？** → 「Redirect handler 設計成 fallback to DB（讀 URL）+ swallow XADD 失敗（不阻 302）。但 click 計數會丟失到 Redis 恢復為止。Resilience pattern（circuit breaker）是後續 sprint 的範圍。」<br>**Q: maxmemory policy 用什麼？** → 「`allkeys-lru`，image cache 是最大宗（150MB / 50k entries 估算），URL cache 變動少。」 |

### Redis Streams（重點：為什麼不選 Kafka / Pub/Sub）

| 維度 | 內容 |
|---|---|
| **是什麼** | Redis 5.0 引入的 append-only log，支援 consumer group / ack / pending |
| **為何選它（vs Kafka / Pub/Sub）** | 詳見 `docs/decisions/0001-redis-streams-for-click-pipeline.md`。簡短：(1) **沿用既有 Redis**，不增加新基礎設施；(2) **流量量級用不到 Kafka**（每秒幾千事件 vs Kafka 設計給每秒百萬）；(3) **GCP Pub/Sub** 多一個 service、本地測試要 emulator，門檻高；(4) Streams 的 `XPENDING + XCLAIM` 直接給 crash recovery，不用自己做 |
| **本專案用到的特性** | Consumer group（多 worker 共享 stream）、`XACK`（at-least-once 邊界）、`XPENDING + XCLAIM`（stale entry 接手）、`MAXLEN ~`（自動修剪 stream 長度） |
| **面試 FAQ** | **Q: Kafka 跟 Redis Streams 差在哪？** → 「Kafka 是 disk-first，partition 多、保留時間長（天級），單條 message 大、生態系廣（Connect、Streams、KSQL）；Redis Streams 是 in-memory，重啟靠 AOF/RDB，partition 是邏輯的（不同 stream key），適合 ms 級延遲、訊息量中等、能容忍 Redis 重啟丟一點點資料的情境。這個專案符合後者。」<br>**Q: 流量再 ×100 你會換 Kafka 嗎？** → 「會。觀察指標是 `qr_click_stream_lag`：當 worker 怎麼擴都追不上、或 Redis maxmemory 撐不住 stream 長度時，就到了 Kafka 的甜蜜點。換 Kafka 後 producer 介面用 `confluent-kafka` 包裝、保持 publish_click 的呼叫 signature 不變，consumer worker 也是同一個 pattern——只是換 client。」 |

---

## 3. Observability（你最想搞懂的這段）

> **核心理念**：observability 不是「裝幾個工具就好」，而是要對應**三本柱**——metrics（量）/ traces（鏈路）/ logs（事件）。每個工具有清楚的定位，**不重疊也不互相取代**。

### OpenTelemetry（OTel）

| 維度 | 內容 |
|---|---|
| **是什麼** | CNCF graduated 專案，分散式追蹤的**事實標準 API + SDK**，跨語言（Python / Java / Go / JS 都有實作） |
| **為何選它（vs vendor-specific SDK）** | (1) **Vendor-neutral**：同一份 instrumentation code 可以同時送 Jaeger（本地）+ Cloud Trace（雲端），未來換 Datadog / New Relic 不用改 application code，只改 exporter；(2) **Auto-instrumentation**：`opentelemetry-instrumentation-fastapi` 一行 import 就把所有 endpoint 包成 span，省掉 80% 樣板碼；(3) **Spec 統一**：trace_id 格式、context propagation header（`traceparent`）已標準化，跨服務串聯不會出問題 |
| **替代品比較** | **Cloud Trace SDK（GCP）**：vendor lock-in；**Jaeger client（OpenTracing 時代）**：已 deprecated，被 OTel 取代；**Zipkin client**：API 簡單但功能不足，metrics/logs 沒整合 |
| **本專案用到的特性** | Auto instrumentation（FastAPI / SQLAlchemy / Redis）、Manual span（`qr_service.*` / `image_service.*` / `worker.run_once`）、Span attributes（`qr.cache_result=hit\|miss` 等業務語意）、Dual exporter by `ENVIRONMENT`（local→Jaeger、prod→Cloud Trace、test→noop） |
| **面試 FAQ** | **Q: OTel 跟 Prometheus 差在哪？** → 「OTel tracing 看**單一請求**的完整旅程（一個 trace = N 個 span，跨服務）；Prometheus 看**聚合的時間序列**（QPS、p99）。前者回答『這個請求為什麼慢』，後者回答『系統現在多忙』。**互補不互斥**。」<br>**Q: 為什麼雙 exporter？** → 「本地 Jaeger 免費、即時、有 UI 適合 debug；prod 進 Cloud Trace 是因為它跟 Cloud Logging 自動關聯（log JSON 帶 trace_id 就能 cross-link），不用自己搭 backend。」<br>**Q: Sampling 怎麼設？** → 「目前都是 100% sampling 因為流量還小。production 真上量後會用 `parent_based(traceidratio)` 設 1–10%，error span 永遠 keep（tail-based 也是選項但複雜）。」 |

### Prometheus

| 維度 | 內容 |
|---|---|
| **是什麼** | CNCF graduated 的時序資料庫 + scraping-based metric collector |
| **為何選它（vs StatsD / InfluxDB / Datadog）** | (1) **Pull model**（Prometheus 主動去抓 `/metrics`）而非 push，**target discovery + 失聯偵測**內建——Push model 服務掛掉時就停止上報，看起來反而「沒事」；(2) **PromQL** 對運維夠用（rate / histogram_quantile / aggregation），不需要 SQL；(3) **本地 docker 跑得起來**，不像 Datadog 一定要 SaaS；(4) k6 也支援 `experimental-prometheus-rw` 把壓測指標寫進來，**測試與線上用同一個工具**，學習曲線只走一次 |
| **本專案這個專案具體適合 Prometheus 的點** | (1) **Cardinality 可控**：我的 metric label 都是有界的（`cache_result=hit\|miss`、`result=hit\|miss`），不會像 InfluxDB 適合無界 tag；(2) **`/metrics` endpoint = 一個 HTTP scrape**：FastAPI 本來就 expose HTTP，加一條 route 的成本是零；(3) **k6 整合**：壓測時可以同時看 application metrics（cache hit rate）跟 load metrics（VUs、RPS），同一個 dashboard |
| **本專案的 6 個自訂 metric** | `qr_redirect_total{cache_result}`、`qr_image_cache_total{result}`、`qr_click_stream_published_total`、`qr_click_stream_consumed_total`、`qr_click_stream_lag`、`qr_db_pool_in_use`（前 4 個是 Counter，後 2 個是 Gauge） |
| **面試 FAQ** | **Q: 為什麼是 pull 不是 push？** → 「Pull 讓 Prometheus 主動發現 target 死亡（scrape 失敗 → up=0），push model（StatsD）服務掛了就完全沒訊號，反而像沒事。例外是 short-lived batch job——那種要用 Pushgateway。」<br>**Q: 你的 metric cardinality 怎麼控？** → 「不放 token / user_id 進 label（無界）；只放有限枚舉的 dimension。Histogram 的 bucket 我用 instrumentator 預設值，必要時再調。」<br>**Q: 為什麼不用 Cloud Monitoring？** → 「上線後**會同時用**：OTel exporter 一份送 GCP Managed Prometheus（或自架 Prometheus federation），保留 PromQL 與本地 Grafana 相容性；vendor lock-in 風險最低。」 |

### Grafana

| 維度 | 內容 |
|---|---|
| **是什麼** | 開源 dashboard / visualization 工具，data source 不限 Prometheus（也吃 Loki、Tempo、Cloud Monitoring 等） |
| **為何選它（vs Prometheus UI / Datadog / Kibana）** | (1) **Multi-source 同一張 dashboard**：左邊放 Prometheus 的 p99，右邊放 Loki 的 error log，中間放 Tempo 的 trace——unified view；(2) **Provisioning as code**：dashboard JSON 可以塞進 git（這個專案 `docker/grafana/dashboards/qr.json` 就是），重建環境只要起 container；(3) **不綁 vendor**：Prometheus、Loki、Tempo 都是同一家（Grafana Labs），但 datasource API 是 open 的，未來換 backend 不會掉資料 |
| **本專案 dashboard 包含的 panel** | QPS（全域 + by handler）、redirect p50/p95/p99、image cache hit rate、stream publish/consume rate、stream lag、DB pool utilization——**7 個 panel 對應 7 個關鍵問題** |
| **面試 FAQ** | **Q: 為什麼不直接看 Prometheus 自己的 web UI？** → 「Prometheus UI 是給 ad-hoc PromQL 查詢用的，沒辦法存『常用視圖』。Grafana dashboard 是給 oncall 一打開就能掃完系統健康度——時間從『敲查詢』降到『看一眼』。」<br>**Q: Dashboard 你會放幾個 panel？** → 「**Golden signals 四件套：latency / traffic / errors / saturation**（Google SRE Book）。然後針對業務加 2–3 個（cache hit rate、stream lag）。再多就沒人看了。」 |

### Jaeger（local trace UI）

| 維度 | 內容 |
|---|---|
| **是什麼** | CNCF graduated 的分散式 trace storage + UI |
| **為何選它（vs Tempo / Zipkin）** | (1) **`all-in-one` image 一行起來**——docker-compose 不用配 collector / storage，本地 demo 友好；(2) **OTLP 原生支援**（不用裝 Jaeger client）；(3) **UI 比 Zipkin 強**（service map、operation latency histogram）。**Production 我選 Cloud Trace** 因為跟 Cloud Logging 自動關聯，這只是本地 dev 工具 |
| **本專案用法** | `docker compose up` 後 `localhost:16686` 看 trace，filter operation = `GET /r/{qr_token}` 看 redirect 完整鏈路（FastAPI → service → Redis → response） |
| **面試 FAQ** | **Q: Jaeger 跟 Tempo 差在哪？** → 「Jaeger 是 trace-only 後端，自有 storage（Cassandra/Elasticsearch）；Tempo 是 Grafana Labs 的，**只存 trace_id → trace blob 對應**（用 object store），靠 metrics（exemplars）/ logs 找入口 trace_id，便宜很多。Production 大規模選 Tempo；本地 dev 選 Jaeger。」 |

### structlog

| 維度 | 內容 |
|---|---|
| **是什麼** | Python structured logging library，輸出 JSON / key-value 格式 |
| **為何選它（vs stdlib logging / loguru）** | (1) **Structured first**：每行 log 是 dict，不是字串，可以直接被 Cloud Logging / Loki / ELK 解析；(2) **Processor chain**：trace_id / span_id / request_id 一次 inject 到所有 log，不用每個呼叫點手動寫；(3) **跟 stdlib logging 相容**：第三方庫的 log 也會走同一條管線；(4) **loguru 雖然好用但 API 不是 stdlib 兼容**，移植成本高 |
| **本專案 processor chain 做了什麼** | `add_log_level` → `TimeStamper(iso)` → `add_trace_context`（從 OTel current span 抓 trace_id/span_id 注入）→ JSONRenderer。產出範例：`{"event":"click event published","level":"info","timestamp":"2026-05-14T10:00:00Z","trace_id":"abc...","span_id":"def...","token":"xyz"}` |
| **為什麼 trace_id 注入到 log 是 game changer** | 在 Cloud Logging / Loki 裡 query `trace_id="abc..."` 就能撈出**這個請求的所有 log**，跨服務、跨機器都行。沒有的話只能猜時間區間 + grep。 |
| **面試 FAQ** | **Q: 為什麼不用 print？** → 「(1) print 不分 level，沒法 filter；(2) print 是字串，沒辦法被機器解析；(3) print 在多 process 下會交錯壞掉。Structured logging 解決全部。」<br>**Q: 為什麼 JSON 不是純文字？** → 「Cloud Logging / Loki / Datadog 都 prefer JSON——把 log 變成可查的 event，不是要靠 regex 解析的字串。Production 一定要 structured。」 |

---

## 4. Load Testing

### k6 — 重點交代

| 維度 | 內容 |
|---|---|
| **是什麼** | Grafana Labs 維運的 OSS load testing tool，**用 Go 寫的執行器 + JavaScript 寫的測試腳本** |
| **為何選它（vs JMeter / Locust / wrk / Gatling）** | 詳見下面四個比較 |

**和 JMeter 比**：
- JMeter 是 Java GUI + XML config，**寫測試靠拖拉**，version control 不友善（XML diff 看不懂）；**k6 是 JS 腳本**，git diff 跟 review 直接看得懂
- JMeter 一個 thread = 一個 OS thread，幾千 user 就吃 GB 級 RAM；k6 用 Go goroutine，**單台機器跑 10k+ VU 不吃力**
- JMeter 學習曲線陡（GUI + plugin ecosystem），k6 拿 JS 就能寫，**前端工程師也能參與壓測**

**和 Locust 比**：
- Locust 是 Python，腳本可重用 application code（appealing）；但 **Python GIL + gevent 在高 QPS 下不穩**，CPU 會先變成 load generator 自己的瓶頸而不是受測系統
- k6 是 Go：**load generator 不會搶 CPU**，量到的 latency 是 server side 真實 latency

**和 wrk 比**：
- wrk 是 C，輕量極快，**但只能跑單一 URL**，沒辦法寫「先 POST 拿 token 再 GET redirect」這種 scenario；本專案的 `redirect_cold` 場景沒 wrk 能用
- wrk 沒 thresholds（pass/fail criteria）、沒 metric output 整合，CI/CD 不友善

**和 Gatling 比**：
- Gatling 是 Scala DSL，效能強，但**團隊要會 Scala**；k6 JS 是平民語言

| **本專案這個專案特別適合 k6 的點** | (1) **三個情境寫成三個檔**，每個檔頂端 block comment 寫用途 / 預期瓶頸——code 就是文件；(2) **Thresholds**（`http_req_failed < 1%`、`p99 < 500ms`）直接寫進腳本——CI 整合時 exit code 表 pass/fail，未來能擋 release；(3) **Prometheus output**（`--out experimental-prometheus-rw`）讓壓測 metric 跟 application metric 在同一個 Grafana 看，**「load generator 飆 RPS」和「application p99 漲」一張圖看完**；(4) **`SharedArray` 載 tokens**——10k VU 共享一份 1MB token list 不會占 10GB RAM |

| **本專案三個 k6 scenario 設計理由** | <br>**redirect_hot**（ramping-vus 50 → 200 → 500）：模擬高峰流量上來的爬坡，看 spike 對 p99 的影響——**面試重點：你會不會設計 warm-up**<br>**redirect_cold**（constant 50 VU × 3min）：穩態量 DB 寫入路徑——**面試重點：怎麼隔離只測單一路徑**<br>**image_mixed**（50% hit / 50% miss）：模擬真實混合流量——**面試重點：cache 效益要對比，不能只測 hit** |

| **面試 FAQ** | **Q: 為什麼不用 Locust（Python 你比較熟）？** → 「Locust 的 Python load generator 在 1k QPS 以上會被 GIL 拖累，量到的 latency 失真。k6 用 Go，load generator 自己不會變成瓶頸，量到的 p99 就是 server side 真實的 p99。」<br>**Q: VU（virtual user）怎麼換算成 QPS？** → 「不能直接換算。QPS = VU × (1 / 單請求耗時)。VU 是並發數、QPS 是吞吐量。我會看 k6 summary 的 `http_reqs / duration` 才是真實 RPS。」<br>**Q: 壓測腳本要不要寫測試？** → 「寫了 `tests/test_k6_scripts.py`——用 node `--check` 驗 syntax + grep `options/scenarios/thresholds` 存在，保證 PR 不會偷偷把腳本改壞。實際 load behaviour 要本地跑才能驗證。」<br>**Q: 怎麼避免 thundering herd？** → 「腳本的 `stages: [{duration: 30s, target: 50}]` 是 ramping，不是一下灌進去；spike 階段刻意只有 30s 就拉回 0。**ramping 才能量到飽和曲線**，瞬間打滿只能量到崩潰點。」 |

---

## 5. Infrastructure & Deployment

### Docker / docker-compose

| 維度 | 內容 |
|---|---|
| **是什麼** | 容器化執行環境 + 本地多服務編排 |
| **為何選它** | (1) **本地 dev 環境 = 一行 `docker compose up`**，新人 onboarding 從「裝 Postgres、裝 Redis、配 env」變成「裝 Docker」一步；(2) **prod 用同一個 image**——Cloud Run 跑的是同一個 Dockerfile 產出物，「本地能跑、雲端壞掉」的問題大幅降低；(3) **Profile 機制**（`--profile loadtest`）讓 k6 / Jaeger / Grafana 預設不啟動，平常 dev 不浪費資源 |
| **本專案用法** | 同一個 image 雙 CMD：api 走 `uvicorn`、worker 走 `python -m app.worker`（在 docker-compose `command` 覆寫）；profile=loadtest 包 k6 |

### Cloud Run + Cloud SQL Python Connector

| 維度 | 內容 |
|---|---|
| **是什麼** | GCP 的 serverless container platform + 連 Cloud SQL 的 native library |
| **為何選它（vs GKE / Cloud Functions）** | (1) **Scale-to-zero**：個人專案沒流量時 0 instance、0 費用；(2) **無需管 cluster**（GKE 太重）；(3) Cloud SQL Python Connector **取代 Unix socket / proxy sidecar**——免裝 cloud-sql-proxy、走 IAM auth、TLS 內建 |
| **本專案用到的特性** | `INSTANCE_CONNECTION_NAME` env var 觸發 Connector branch（見 `app/database.py`）、`pool_size=1, max_overflow=2` 配合 `db-f1-micro` 25 max_connections |
| **面試 FAQ** | **Q: 為什麼不直接 VM + nginx？** → 「QR 服務流量是 bursty（行銷活動會突然暴增），Cloud Run scale-to-zero 平常 0 成本、burst 自動展開。VM 要 24/7 付錢還要自己處理 ASG。」<br>**Q: Connector 跟 Cloud SQL Proxy 差在哪？** → 「Proxy 是 sidecar 進程，跟你的 app 並排跑；Connector 是 library，直接在 app process 裡建 mTLS 連線。Cloud Run **不能跑 sidecar**（單一 container），所以 Connector 是唯一選項。」 |

### Alembic

| 維度 | 內容 |
|---|---|
| **是什麼** | SQLAlchemy 官方的 schema migration tool |
| **為何選它（vs Django migrations / Liquibase / Flyway）** | (1) 跟 SQLAlchemy 模型同源，**`--autogenerate`** 直接讀 model 差異產 migration；(2) Python script，可以塞 data migration 邏輯；(3) Liquibase/Flyway 是 SQL-first，跟 ORM 改動 sync 起來累 |
| **本專案部署 gotcha** | 寫進 `alembic/env.py`：如果 `INSTANCE_CONNECTION_NAME` 被 set 但 `DATABASE_URL` 是 sqlite，**直接報錯**——擋住「跑 alembic 卻改到 SQLite 然後以為改到 prod」這種人為事故 |

---

## 6. Testing

### pytest + FastAPI TestClient

| 維度 | 內容 |
|---|---|
| **為何選它** | (1) FastAPI 官方推薦；(2) TestClient 是 Starlette 包裝的 httpx，**測試走真實 ASGI app**——不是 mock，連 middleware / dependency injection 都會跑到；(3) `conftest.py` 的 fixture 機制讓「測試獨立 DB」這種 pattern 一行 override |

### fakeredis

| 維度 | 內容 |
|---|---|
| **為何選它（vs miniredis / docker test container / mock）** | (1) **Pure Python in-process**——不用啟動 Redis 進程，pytest 跑 38 個 test 在 1.5s 內結束；(2) 支援 Stream / consumer group（這是關鍵——很多 mock 不支援）；(3) test 跟 prod 同一份 application code 不用切 branch |
| **本專案用到的細節** | `tests/conftest.py` 把 `get_redis` dependency 換成 `FakeRedis()` instance；Sprint A 的 dedupe test 真的跑 XCLAIM 流程（在 fakeredis 上），不是 mock |
| **限制** | fakeredis 的 `time_since_delivered` 不會隨 `sleep` 增加——所以 dedupe test 用「預寫 dedupe key」模擬重派，QA report 有記 |

---

## 7. 整套 stack 的「故事線」

如果面試官問「介紹一下這個專案的技術 stack」，**順序這樣講**：

1. **問題定位**：QR 服務的核心是 redirect 高 QPS（讀多寫少）+ 點擊統計（寫多讀少）兩條截然不同的路徑。
2. **資料層分工**：Redis 擔讀（cache + stream）、Postgres 擔最終一致性（持久化 + 統計表）。
3. **拆 worker**：用 Redis Streams 把點擊計數從 API process 拆出去，能獨立水平擴展。**為什麼是 Streams 不是 Kafka**——量級配不上 Kafka，沿用既有 Redis 是最低成本解。
4. **觀測性三本柱**：OTel traces 看單請求、Prometheus metrics 看系統健康、structlog 帶 trace_id 把 logs 串回 traces。本地用 Jaeger + Grafana，雲端走 Cloud Trace + Cloud Monitoring，**同一份 instrumentation 雙出口**靠 OTel 的 vendor-neutral 特性。
5. **壓測驗收**：k6 寫三個情境（hot / cold / mixed）做 baseline vs current 對比，**用同一個 Grafana 看 load generator 和 application 兩端 metric**，直接看出瓶頸落在哪。
6. **部署**：Cloud Run scale-to-zero + Cloud SQL Connector + Memorystore + Artifact Registry。**沒有 GKE、沒有 sidecar、沒有自管 redis**——能用 managed service 就不自己搭。

**敘事重點**：每個工具都解決一個具體問題，**沒有為了用而用**。如果面試官追問「為什麼不選 X」，回答的核心是「X 的價值要在更大規模才划算，這個專案還沒到那個 inflection point」——這比硬講 X 不好更安全。

---

## 8. 面試常見「陷阱題」答題方向

| 問題 | 回答策略 |
|---|---|
| 「你為什麼用這麼多工具？是不是 over-engineering？」 | **每個工具都對應一個我量化過或可量化的問題**。OTel 對應「請求慢在哪一跳」，Prometheus 對應「整體 p99 多少」，k6 對應「能撐多少 QPS」。如果只是個 weekend project 我不會引入。我承認 individual project 的規模上這套是 over，但這套**完整鋪好之後再加新 feature 的邊際成本很低**——這是專業環境的 baseline。 |
| 「沒有測試過真實流量怎麼證明這套有效？」 | k6 壓測是 controlled load，跟真實流量不等價——這是公平的批評。下一步是上 Cloud Run + 引一點真實流量（如 Cloud Scheduler 模擬 + 用 Cloud Trace exemplar 對應到 Prometheus histogram）。但 k6 至少證明**架構的飽和點在哪、observability 能不能定位瓶頸**——這兩件事不上線也驗得到。 |
| 「為什麼沒有 message queue（如 RabbitMQ）？」 | Redis Streams 已經是 queue + log 的合體。RabbitMQ 強在 routing（topic exchange、header exchange），這專案 routing 邏輯是零（所有 click 都進同一個 stream）。RabbitMQ 多帶 routing 的價值沒對應到 cost。 |
| 「如果預算砍半，你會拿掉哪個工具？」 | 拿掉 Grafana + Jaeger（dev only），保留 Prometheus + OTel + Cloud Trace。Production observability 的最低 viable 組合是「能量 + 能查 trace_id」。Dashboard 是 nice-to-have，trace 收集是 must-have——丟 trace 等於 incident response 變盲飛。 |
| 「你的選型有沒有什麼後悔的？」 | 有兩個：(1) 開始就上 `pool_size=1` 是為了 db-f1-micro，但本地 dev 也吃這個 setting，壓測時容易誤判 pool 限制是專案本身的問題；應該分 env config。(2) Sprint A 的 dedupe 用 SET NX 而不是 Redis 的 `XADD ... NOMKSTREAM` + ID-based dedupe——後者更原生但當時還不熟 stream 細節。**承認後悔很重要**，比硬講「我所有決策都對」可信。 |
