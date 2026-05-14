# Contract: Sprint C — k6 壓測腳本與容量報告框架

對應 spec: docs/specs/sprint-c-load-test.md

## Deliverables

### Backend / Scripts — k6 腳本（新增）
- [ ] 新增 `scripts/k6/lib/common.js`：
  - `export function baseUrl()`：讀 `__ENV.BASE_URL`，預設 `'http://api:8080'`
  - `export const defaultOptions`：含 `summaryTrendStats: ['min','med','avg','p(50)','p(95)','p(99)','max']`、`discardResponseBodies: true`
  - `export function buildThresholds({ failedRate = 0.01, p99Ms = 500 } = {})`：回 `{ 'http_req_failed': [...], 'http_req_duration': [...] }`
  - `export function pickToken(tokens)`：隨機從陣列挑一個
  - `export function loadTokens()`：用 `open('./tokens.json')` 載入並 `JSON.parse`
- [ ] 新增 `scripts/k6/seed.js`：
  - 頂端 block comment：用途 + 執行指令 `k6 run --env BASE_URL=http://localhost:8000 --env SEED_COUNT=1000 scripts/k6/seed.js`
  - `export const options = { vus: 10, iterations: <SEED_COUNT> }`，`SEED_COUNT` 從 `__ENV.SEED_COUNT` 讀（預設 1000）
  - VU function：`POST {baseUrl}/v1/qr_code` body `{"url":"https://example.com/{iter}"}`，將 `qr_token` 收集到 `SharedArray` 或 `handleSummary` 寫檔
  - `export function handleSummary(data)`：將收集到的 tokens 寫到 `scripts/k6/tokens.json`（用 `open()` 不可寫，改用 stdout JSON 並由 wrapper script / docs 指示用 `k6 run ... | jq` 重定向；以 comment 寫明做法）
  - 接受替代實作：用 `console.log` 每行印 `TOKEN:xxx` 並在 docs 寫 grep redirect 方式
- [ ] 新增 `scripts/k6/redirect_hot.js`：
  - 頂端 block comment：用途（量 cache hit 上限 / Redis URL cache 路徑）、執行指令、預期 bottleneck（Redis network round-trip / FastAPI overhead）、可調 thresholds
  - import `lib/common.js`
  - `export const options = { scenarios: { hot: { executor: 'ramping-vus', stages: [{duration:'30s',target:50},{duration:'2m',target:200},{duration:'30s',target:500},{duration:'30s',target:0}] } }, thresholds: buildThresholds({ p99Ms: 500 }) }`
  - `default` function：load tokens via `SharedArray`、隨機 `http.get(`${baseUrl()}/r/${pickToken(tokens)}`, { redirects: 0 })`、`check(res, { 'status 302': r => r.status === 302 })`
- [ ] 新增 `scripts/k6/redirect_cold.js`：
  - 頂端 block comment：用途（每 iter 新 token 走 DB fallback + SETEX）、預期 bottleneck（Postgres INSERT、SERVER_SECRET hashing）、執行指令
  - `export const options = { scenarios: { cold: { executor: 'constant-vus', vus: 50, duration: '3m' } }, thresholds: buildThresholds({ p99Ms: 1500 }) }`
  - VU function：先 POST 建 token、解析 `qr_token`、立即 `GET /r/{token}`
- [ ] 新增 `scripts/k6/image_mixed.js`：
  - 頂端 block comment：用途、預期 bottleneck（cache miss 時 QR generate CPU + Redis setex）、執行指令
  - `export const options = { scenarios: { image: { executor: 'ramping-vus', stages: [{duration:'30s',target:30},{duration:'2m',target:100},{duration:'30s',target:0}] } }, thresholds: buildThresholds({ p99Ms: 800 }) }`
  - VU function：`Math.random() < 0.5` 走固定 `dimension=256&color=%23000000&border=4`（cache hit）、否則隨機 `dimension`（200~400）/ `color` / `border`（cache miss）

### Infra — docker-compose（修改）
- [ ] 修改 `docker-compose.yml`：
  - 新增 service `k6`：
    - `image: grafana/k6:latest`
    - `profiles: ["loadtest"]`
    - `volumes: ["./scripts/k6:/scripts:ro"]`
    - `command: ["run", "--out", "experimental-prometheus-rw", "/scripts/redirect_hot.js"]`
    - `environment: { K6_PROMETHEUS_RW_SERVER_URL: "http://prometheus:9090/api/v1/write", K6_PROMETHEUS_RW_TREND_STATS: "p(50),p(95),p(99),min,max", BASE_URL: "http://api:8080" }`
    - `depends_on: [api, prometheus]`
  - 修改 `prometheus` service command（或新增 `--web.enable-remote-write-receiver` flag）以接收 k6 remote write；保留既有 scrape config 不變
- [ ] 不動 api / worker / redis / db / jaeger / grafana 既有設定
- [ ] 驗證指令：`docker-compose --profile loadtest config`（應通過）、`docker-compose config | grep -c '^  k6:'`（應為 0，因 k6 在 loadtest profile 內，預設不出現於 `docker-compose config` 輸出——實作時依 Compose 版本行為調整 assertion）

### Docs — `docs/perf-report.md`（新增）
- [ ] 新增 `docs/perf-report.md`，骨架：
  ```markdown
  # Performance Report

  > 本報告數字由使用者於本地實跑 k6 後填入。每個 `<TBD: ...>` 旁邊已標註「Run:」執行指令與「Source:」對應指標。

  ## 1. Environment
  | Field | Value | How to fill |
  | --- | --- | --- |
  | Machine | <TBD: CPU / RAM> | Run: `sysctl -n machdep.cpu.brand_string && sysctl -n hw.memsize` |
  | Docker resources | <TBD: cpus / memory> | Run: `docker info \| grep -E 'CPUs\|Total Memory'` |
  | Git SHA (current) | <TBD: sha> | Run: `git rev-parse --short HEAD` |
  | Git SHA (baseline) | 6566794 | Pre-Sprint-A snapshot |
  | k6 version | <TBD: vX.Y.Z> | Run: `docker run --rm grafana/k6 version` |

  ## 2. Scenarios

  ### 2.1 redirect_hot
  - Purpose：cache-hot redirect throughput ceiling
  - Run：`docker-compose --profile loadtest run --rm -e SCRIPT=/scripts/redirect_hot.js k6`
  - Thresholds：見 `scripts/k6/redirect_hot.js`

  | Metric | Value | Source |
  | --- | --- | --- |
  | RPS (steady) | <TBD: rps> | Prometheus: `sum(rate(http_reqs[1m]))` / Grafana panel: "Redirect QPS" |
  | p50 | <TBD: ms> | Grafana panel: "Redirect duration p50/p95/p99" |
  | p95 | <TBD: ms> | 同上 |
  | p99 | <TBD: ms> | 同上 |
  | Error rate | <TBD: %> | Prometheus: `rate(http_req_failed[1m])` |
  | DB CPU | <TBD: %> | `docker stats db --no-stream` |
  | Redis ops/s | <TBD: ops> | `redis-cli info stats \| grep instantaneous_ops_per_sec` |
  | Cache hit rate | <TBD: %> | Grafana panel: "Image cache hit rate"（此情境應 ~100% URL cache hit） |

  ### 2.2 redirect_cold
  - Purpose：DB fallback + SETEX 路徑
  - Run：`docker-compose --profile loadtest run --rm k6 run /scripts/redirect_cold.js`
  - 表格同 2.1 結構

  ### 2.3 image_mixed
  - Purpose：50/50 image cache hit/miss
  - Run：`docker-compose --profile loadtest run --rm k6 run /scripts/image_mixed.js`
  - 表格同 2.1 結構，額外列「QR generate CPU time」row

  ## 3. Baseline vs Current
  - Baseline checkout：`git checkout 6566794 && docker-compose up -d --build`
  - Current：`git checkout main && docker-compose up -d --build`
  - 對每個情境跑兩次，填入下表

  | Scenario | Metric | Baseline | Current | Δ |
  | --- | --- | --- | --- | --- |
  | redirect_hot | RPS | <TBD> | <TBD> | <TBD> |
  | redirect_hot | p99 ms | <TBD> | <TBD> | <TBD> |
  | redirect_cold | RPS | <TBD> | <TBD> | <TBD> |
  | redirect_cold | p99 ms | <TBD> | <TBD> | <TBD> |
  | image_mixed | RPS | <TBD> | <TBD> | <TBD> |
  | image_mixed | p99 ms | <TBD> | <TBD> | <TBD> |

  ## 4. Bottleneck analysis
  ### redirect_hot
  - 預期 bottleneck：Redis URL cache lookup 的 round-trip + FastAPI middleware overhead
  - 驗證方法：對照 Jaeger trace，看 `app.handler` span 內 Redis call 佔比；若 >70% 即 confirmed
  - 修正方向：（1）pipeline GET+HINCRBY（已在 Sprint A 改成 XADD）；（2）connection pool 加大；（3）uvicorn workers > 1（須先解 Cloud SQL Connector fork 問題）

  ### redirect_cold
  - 預期 bottleneck：Postgres INSERT（單 row, autocommit）+ SHA-256 hashing
  - 驗證：DB CPU > 70% 且 Grafana `qr_db_pool_in_use` 飽和
  - 修正方向：batch insert / async write-behind / token 預生成池

  ### image_mixed
  - 預期 bottleneck：cache miss 時 `qrcode` lib CPU（~10-20ms per image）
  - 驗證：api container CPU > 80% 且 Grafana image cache hit rate ~50%
  - 修正方向：worker pool / 預先生成常用 spec / 改用 Rust QR lib

  ## 5. Conclusion & next steps
  - <TBD：以實測結論寫 2-3 句>
  - Next：rate limiting / circuit breaker（plan item 4-5，未來 sprint）

  ## How to fill this report
  1. `git checkout 6566794 && docker-compose up -d --build`
  2. `k6 run scripts/k6/seed.js` 預建 token
  3. 依序跑 redirect_hot / redirect_cold / image_mixed，填 Baseline 欄
  4. `git checkout main && docker-compose up -d --build`
  5. 重複步驟 2-3，填 Current 欄
  6. 補 Bottleneck analysis 與 Conclusion
  ```
- [ ] 文件須 lint：每個 `<TBD:` 後 200 字內出現 `Run:` 或 `Source:`（由 `tests/test_docs_perf_report.py` 驗）

### Docs — README / CLAUDE.md
- [ ] 修改 `README.md`：
  - 在頂部「Quickstart」或「Overview」後新增「Performance」short section：一行說明「本專案在 cache-hot redirect 可達 <see perf-report> RPS」+ 連到 `docs/perf-report.md`
  - 新增「Observability」section（補 Sprint B QA #1）：列 Jaeger / Prometheus / Grafana URL + admin 預設密碼 + 一句 `trace_id` 跨工具查說明 + 連 ADR-0002
- [ ] 修改 `CLAUDE.md`：
  - 新增「Observability」短段（補 Sprint B QA #3）：`app/observability.py` / `app/logging.py` / `app/metrics.py` 三檔職責一句話 + ADR-0002 連結 + prod SA 需 `roles/cloudtrace.agent`
  - 新增「Performance / Load testing」短段：指向 `scripts/k6/` 與 `docs/perf-report.md`，列三個情境名稱

### 依賴 — `requirements.txt` 鎖版本
- [ ] 將 Sprint B 引入的 10 個 observability 套件 pin 到具體版本（`==X.Y.Z`），版本以 generator 解析 PyPI 當下穩定版為準：
  - `opentelemetry-sdk`
  - `opentelemetry-api`（若顯式列）
  - `opentelemetry-exporter-otlp-proto-http`
  - `opentelemetry-exporter-gcp-trace`
  - `opentelemetry-instrumentation-fastapi`
  - `opentelemetry-instrumentation-sqlalchemy`
  - `opentelemetry-instrumentation-redis`
  - `opentelemetry-instrumentation-requests`
  - `prometheus-fastapi-instrumentator`
  - `prometheus-client`
  - `structlog`
- [ ] 不動其他既有 dependency

## Prometheus query 對照表（給 perf-report.md 引用）

| 指標 | Prometheus query | Grafana panel | 備註 |
| --- | --- | --- | --- |
| Redirect RPS | `sum(rate(http_requests_total{handler="/r/{qr_token}"}[1m]))` | "Redirect QPS" | 由 `prometheus-fastapi-instrumentator` 提供 |
| Redirect RPS by cache | `sum(rate(qr_redirect_total[1m])) by (cache_result)` | "Redirect rate by cache_result" | Sprint B custom counter |
| Redirect p99 | `histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{handler="/r/{qr_token}"}[1m])) by (le))` | "Redirect duration p50/p95/p99" | |
| Image cache hit rate | `rate(qr_image_cache_total{result="hit"}[5m]) / rate(qr_image_cache_total[5m])` | "Image cache hit rate" | |
| Click stream lag | `qr_click_stream_lag` | "qr_click_stream_lag" | worker 每 idle tick 更新 |
| DB pool in use | `qr_db_pool_in_use` | "qr_db_pool_in_use" | API lifespan 每 5s sample |
| Published vs consumed | `rate(qr_click_stream_published_total[1m])` vs `rate(qr_click_stream_consumed_total[1m])` | "Published vs consumed rate" | |
| k6 RPS | `sum(rate(k6_http_reqs[1m]))` | k6 自身輸出 | 需 prometheus 開 `--web.enable-remote-write-receiver` |
| k6 p99 | `k6_http_req_duration{quantile="0.99"}` | k6 自身輸出 | |

## API 不變動聲明（contract test 必驗）

| Method | Path | 不變動要點 |
| --- | --- | --- |
| POST | `/v1/qr_code` | 201, `{"qr_token": "..."}` |
| GET | `/v1/qr_codes` | 200 |
| GET | `/v1/qr_code/{token}` | 200 / 410 / 404 |
| PUT | `/v1/qr_code/{token}` | 204 |
| DELETE | `/v1/qr_code/{token}` | 204 |
| GET | `/v1/qr_code_image/{token}` | 200, `image/png`, `Cache-Control: public, max-age=300, must-revalidate` |
| GET | `/r/{token}` | 302 |
| GET | `/metrics` | 200 |
| POST | `/internal/flush_clicks` | 200 / 403 |

## Out of scope
- 實際執行 k6 收集真實數字（留給使用者）
- CI 整合 / 自動回歸閾值
- k6 cloud / SaaS 接線
- Cloud Run / Cloud SQL / Memorystore 規格調整
- Sprint B Instrumentator 掛載位置 cosmetic 修正
- 新增觀測指標
- 公開 API 變更

## Tests required

### 新增 `tests/test_k6_scripts.py`
- [ ] `test_seed_script_exists_and_has_default_export`：檔案存在；含 `export default` 或 `export function handleSummary`
- [ ] `test_redirect_hot_has_options_and_thresholds`：含 `export const options`、`thresholds`、`scenarios`、引用 `lib/common`
- [ ] `test_redirect_cold_has_options_and_thresholds`：同上
- [ ] `test_image_mixed_has_options_and_thresholds`：同上
- [ ] `test_common_lib_exports`：`lib/common.js` 含 `export function baseUrl`、`export const defaultOptions`、`export function buildThresholds`、`export function pickToken`
- [ ] `test_node_syntax_check_when_available`：若 `shutil.which("node")` 存在，對 5 個 `.js` 檔跑 `node --check`，全部 returncode 0；否則 `pytest.skip`

### 新增 `tests/test_docs_perf_report.py`
- [ ] `test_perf_report_has_required_sections`：含 `# Performance Report`、`## 1. Environment`、`## 2. Scenarios`、`## 3. Baseline vs Current`、`## 4. Bottleneck analysis`、`## 5. Conclusion`
- [ ] `test_perf_report_placeholders_have_source`：用 regex 找 `<TBD:` 每個 occurrence，往後 200 字內必出現 `Run:` 或 `Source:`
- [ ] `test_perf_report_has_scenario_subsections`：含 `### 2.1 redirect_hot`、`### 2.2 redirect_cold`、`### 2.3 image_mixed`

### 新增 `tests/test_compose_loadtest_profile.py`
- [ ] `test_compose_loadtest_config_parses`：若 `shutil.which("docker")` 存在，subprocess 跑 `docker compose --profile loadtest config`，returncode 0；否則 skip
- [ ] `test_compose_yaml_has_k6_service_in_loadtest_profile`：直接 yaml.safe_load `docker-compose.yml`，斷言 `services.k6.profiles == ["loadtest"]`、`image == "grafana/k6:latest"`、`depends_on` 含 `api` 與 `prometheus`
- [ ] `test_prometheus_remote_write_enabled`：讀 `docker-compose.yml`，斷言 prometheus service command 含 `--web.enable-remote-write-receiver`

### 新增 `tests/test_requirements_pinned.py`
- [ ] `test_observability_packages_pinned`：讀 `requirements.txt`，斷言 10 個 observability 套件每個都符合 `<name>==<version>` 形式（regex `==\d`）

### 新增 `tests/test_docs_observability.py`
- [ ] `test_readme_has_observability_section`：`README.md` 含 `## Observability`（或類似 heading）且提到 `jaeger`、`prometheus`、`grafana`、`16686`、`9090`、`3000`
- [ ] `test_readme_has_performance_section`：含 `Performance` heading 且連到 `docs/perf-report.md`
- [ ] `test_claude_md_has_observability_section`：`CLAUDE.md` 含 `app/observability.py`、`app/logging.py`、`app/metrics.py` 三個檔名與 `ADR-0002` 字串

### 既有測試
- [ ] 既有 60 case（Sprint A 38 + Sprint B 22）保持綠燈
- [ ] API 不變動聲明 8 條 endpoint 行為 byte-identical

## Definition of done
- 全部 deliverable 打勾
- `pytest tests/ -v` 全綠（既有 60 + 新增 ~13 case）
- `docker-compose config` 不含 k6；`docker-compose --profile loadtest config` 含 k6 且不報錯
- 5 個 k6 `.js` 檔語法合法（有 node 時 `node --check` 全 pass）
- `docs/perf-report.md` 骨架完整，所有 `<TBD:` 均有 `Run:` 或 `Source:` 註解
- README 有 Observability + Performance section
- CLAUDE.md 有 Observability 段
- `requirements.txt` observability 10 套件全 pin
- 公開 API byte-identical
- evaluator 的 QA report 無 P0/P1（「沒有真實數字」不得列為 issue，spec 已明列）
