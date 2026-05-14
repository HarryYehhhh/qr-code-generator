# Spec: Sprint C — k6 壓測腳本與容量報告框架

## Goal
建立一套**可立即執行**的 k6 壓測腳本與報告框架，並把 Sprint B 留下的 docs / requirements pin 等 QA warnings 一併清掉，讓使用者在本地實跑 docker-compose + k6 後可直接把結果填入 `docs/perf-report.md`，產出面試可亮的 Performance 段落。

## 重要 scope 限制（影響驗收）
- 本 sprint **不包含實際跑壓測收集真實數字**——subagent 無法在 sandbox 內可靠啟動 docker，亦無法量到代表性 RPS / p99。
- Deliverable 是「腳本 + compose 整合 + 報告骨架 + 對照指南」，數字欄位以明確 placeholder（`<TBD: RPS>` / `<TBD: p99_ms>` ...）填入，**每個 placeholder 旁邊必須註明**：
  1. 觸發此數字的精確 k6 指令
  2. 對應的 Prometheus query 或 Grafana panel 名稱
  3. 預期合理區間（用於判讀對錯）
- evaluator 不得以「沒有真實數字」判 Fail；應驗證骨架完整、placeholder 註解齊全、腳本 syntax 正確、compose config 合法。

## User stories
- As an **interviewee**，我跑完 `docker-compose up && k6 run scripts/k6/redirect_hot.js` 之後可以照 `docs/perf-report.md` 的指引把 RPS / p50 / p95 / p99 / error rate 填入表格，無須額外想「該量哪個指標」。
- As a **reviewer**，我可以 `cat scripts/k6/*.js` 看到三個情境（hot / cold / image_mixed）+ seed，每個檔頂端 comment 寫明「測什麼路徑、預期 bottleneck、thresholds」。
- As a **developer**，`docker-compose --profile loadtest up k6` 可一鍵把 k6 容器接到既有 stack，輸出走 Prometheus remote write，Grafana 預設 dashboard 即可看 k6 指標。
- As a **maintainer**，`pytest tests/ -v` 仍 60+/60+ 全綠（含新增的 k6 腳本 lint test），公開 API 與 Sprint A/B 行為完全不動。
- As a **reader**，README 頂部有 Performance section 連到 `docs/perf-report.md`、Observability section 連到 Jaeger / Prometheus / Grafana 入口（補 Sprint B 漏掉的章節）。

## Acceptance criteria

### k6 腳本（`scripts/k6/`）
- [ ] `seed.js`：POST `/v1/qr_code` 預建 `N` 個 token（`N` 由 env `SEED_COUNT` 控制，預設 1000），輸出 token list 到 `scripts/k6/tokens.json`。可獨立 `k6 run --env BASE_URL=... scripts/k6/seed.js` 執行。
- [ ] `redirect_hot.js`：載入 `tokens.json`，隨機挑 token 打 `GET /r/{token}`；三段 stages（warm-up 30s → steady 2m → spike 30s），thresholds：`http_req_failed < 1%`、`http_req_duration{p(99)} < 500ms`（值由 reviewer 視機器調整，於 comment 註明可調）。
- [ ] `redirect_cold.js`：每個 VU iteration 先 POST 建新 token 再立即 redirect，量 DB fallback / Redis SETEX 路徑；thresholds 同上但 p99 放寬到 `< 1500ms`，並在 comment 註明預期 bottleneck 為 Postgres write。
- [ ] `image_mixed.js`：50/50 cache hit（重複打同一個 token+spec）與 miss（隨機 spec hash via querystring `dimension` / `color` / `border`）打 `GET /v1/qr_code_image`；thresholds：`http_req_failed < 1%`、`p(95) < 800ms`。
- [ ] `lib/common.js`：匯出共用 `defaultOptions`、`buildThresholds(scenario)`、`pickToken(tokens)`、`baseUrl()`（讀 env `BASE_URL`，預設 `http://api:8080`）。
- [ ] 每支腳本檔案頂端 block comment 註明：用途、執行指令、預期 bottleneck、可調參數。
- [ ] 全部腳本可被 `k6 inspect <file>` 接受（或被 Node syntax check 過），不依賴雲端 cloud token。

### docker-compose 整合
- [ ] `docker-compose.yml` 新增 `k6` service：
  - image `grafana/k6:latest`
  - profile `loadtest`（預設 `docker-compose up` 不會啟動）
  - mount `./scripts/k6:/scripts:ro`
  - command 預設 `run --out experimental-prometheus-rw /scripts/redirect_hot.js`（可被覆蓋）
  - env：`K6_PROMETHEUS_RW_SERVER_URL=http://prometheus:9090/api/v1/write`、`BASE_URL=http://api:8080`、`K6_PROMETHEUS_RW_TREND_STATS=p(50),p(95),p(99),min,max`
  - depends_on `api`、`prometheus`
- [ ] `docker/prometheus.yml` 開啟 `--web.enable-remote-write-receiver`（透過 prometheus service 的 command flag 加上）並保留既有 scrape job。
- [ ] `docker-compose --profile loadtest config` 不報錯。
- [ ] 預設 `docker-compose up` 行為與 Sprint B 完全相同（k6 不啟動）。

### `docs/perf-report.md` 報告骨架
- [ ] 章節結構：
  1. Environment（machine / docker resources / git SHA / image tag / k6 version）— 每欄填寫 placeholder + 取得指令
  2. Scenarios（三個 sub-section：redirect_hot、redirect_cold、image_mixed）
     - 每個含：執行指令、Threshold 設定、結果表格（RPS / p50 / p95 / p99 / error rate / DB CPU / Redis ops/s / cache hit rate）
     - 每個 cell 用 placeholder `<TBD: 指標名>` 並在欄旁標註「Source：Grafana panel `<name>` / Prometheus query `<expr>`」
  3. Baseline vs Current 對照
     - Baseline checkout 指令：`git checkout 6566794`（Sprint A 前的最後一個 commit）
     - Current：main HEAD（Sprint B 完成後）
     - 對照表 metric 同上
  4. Bottleneck analysis（每個情境 1 段）：預先寫好「預期 bottleneck + 如何驗證 + 修正方向」框架，數字部分留 placeholder
  5. Conclusion & next steps
- [ ] 每個 placeholder 必含「執行指令 + Prometheus query / Grafana panel」對照。
- [ ] 文件結尾有 "How to fill this report" checklist，列出 baseline / current 兩輪共 6 次 k6 run 的順序。

### Sprint B QA warnings 收尾（順手）
- [ ] `README.md` 新增「Observability」section（QA #1）：說明 Jaeger (`http://localhost:16686`) / Prometheus (`http://localhost:9090`) / Grafana (`http://localhost:3000`, admin/admin) 入口、如何用 `trace_id` 跨工具查、`ENVIRONMENT` 切換 dual exporter 行為。
- [ ] `README.md` 新增「Performance」section：一句話總結 + 連到 `docs/perf-report.md`。
- [ ] `CLAUDE.md` 新增 Observability 短段（三檔職責 + ADR-0002 連結 + prod SA 需 `roles/cloudtrace.agent`）——若被視為與 Sprint B 重疊，可改放 Performance 段並連到 ADR-0002，避免重複工作。本 sprint 至少要確保 ADR-0002 / observability 三檔在 CLAUDE.md 有出現。
- [ ] `requirements.txt`：10 個 observability 套件全部 pin 版本（`==X.Y.Z` 形式，版本號以 generator 解析時的最新穩定版為準）。
- [ ] Sprint B 「Instrumentator 在 module load 而非 lifespan」屬 cosmetic，不在本 sprint 範圍，於 spec / contract Out of scope 明列。

### 測試
- [ ] 新增 `tests/test_k6_scripts.py`：
  - 至少 4 個 case，每個對應一個腳本（seed / redirect_hot / redirect_cold / image_mixed）
  - 驗證：(a) 檔案存在；(b) `export const options` 或 `export const scenarios` 出現；(c) `export default function` 出現；(d) 沒有 Python-style syntax（基本 regex 排除）
  - 若環境有 `node` binary 則額外跑 `node --check <file>`；沒有 node 時 skip 該 assertion（用 `pytest.importorskip` 概念，但這裡用 `shutil.which("node")`）
- [ ] 新增 `tests/test_docs_perf_report.py`：驗證 `docs/perf-report.md` 含必備章節標題（Environment / Scenarios / Baseline / Bottleneck / Conclusion），且每個 `<TBD:` placeholder 後 200 字內出現 `Source:` 或 `Run:` 註解。
- [ ] 既有 60 個 test 全綠（Sprint A 38 + Sprint B 22）。

### 驗證
- [ ] `pytest tests/ -v` 全部通過（含新增 k6 lint test 與 perf-report 結構 test）
- [ ] `docker-compose --profile loadtest config` 不報錯
- [ ] `docker-compose config` （無 profile）不含 k6 service
- [ ] 三個 k6 腳本 + seed + lib/common 共 5 個 `.js` 檔語法合法（node `--check` 或 k6 inspect 通過；CI 有 node 時自動跑）
- [ ] `docs/perf-report.md` 全部 placeholder 都有對應指令 / query
- [ ] README 兩個新 section 存在且連結正確
- [ ] `requirements.txt` observability 10 個套件全 pin
- [ ] 公開 API byte-identical：Sprint A/B 既有 60 case 全綠

## Non-goals
- 不實際執行 k6 壓測收集真實數字（留給使用者本地跑後填）
- 不做 CI 整合（GitHub Actions 跑 k6 / 自動回歸閾值）
- 不做 k6 cloud / load test as a service 接線
- 不調 Cloud Run / Memorystore / Cloud SQL 規格
- 不重做 Sprint B Instrumentator 掛載位置（cosmetic）
- 不重寫 `click_stream.consume_batch` wrapper（Sprint B QA 已用 `worker.run_once` 替代並接受）
- 不引入新的觀測指標（沿用 Sprint B 六個 metric）
- 不調公開 API schema / status code / headers

## Open questions
- k6 image 版本是否 pin 到具體 tag（`grafana/k6:0.50.0` vs `latest`）——預設 `latest`，若使用者需要 reproducibility 後續再 pin。
- Threshold p99 數值僅是占位（500ms / 1500ms / 800ms），使用者本地實跑後可在腳本 comment 內依機器調整；不在本 sprint 鎖死。
