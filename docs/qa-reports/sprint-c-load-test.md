# QA Report: sprint-c-load-test

- **Contract**: docs/contracts/sprint-c-load-test.md
- **Spec**: docs/specs/sprint-c-load-test.md
- **Date**: 2026-05-14
- **Verdict**: ⚠️ Pass with issues

## Contract checklist

| Deliverable | Status | Note |
|---|---|---|
| `scripts/k6/lib/common.js`（baseUrl / defaultOptions / buildThresholds / pickToken / loadTokens） | ✅ | 五個 export 齊全；`discardResponseBodies` 與 `summaryTrendStats` 符合 |
| `scripts/k6/seed.js`（block comment / SEED_COUNT / handleSummary） | ✅ | 採 contract 接受的 `console.log TOKEN:` 替代實作 |
| `scripts/k6/redirect_hot.js`（ramping-vus / thresholds / lib import） | ✅ | stages 與 thresholds 與 contract 對齊 |
| `scripts/k6/redirect_cold.js`（constant-vus 50 / 3m / p99=1500） | ✅ | 並含 `if (!created) return` 防 404 噪音 |
| `scripts/k6/image_mixed.js`（50/50 hit/miss / ramping-vus） | ✅ | Cache 切分採 `Math.random() < 0.5` |
| `docker-compose.yml` 新增 k6 service（profile loadtest） | ✅ | image / volume / command / env / depends_on 齊全 |
| `prometheus` service 加 `--web.enable-remote-write-receiver` | ✅ | 在 command list 末尾 |
| 預設 `docker-compose up` 不啟動 k6 | ✅ | k6 在 `loadtest` profile 內 |
| `docs/perf-report.md` 骨架（5 章節 + How to fill） | ✅ | 結構完整 |
| 每個 `<TBD:` 旁有 Run/Source 註解 | ✅ | `test_perf_report_placeholders_have_source` 通過 |
| `README.md` Observability section | ⚠️ | section 完整，但 ADR 連結指向不存在檔案（見 Findings #1） |
| `README.md` Performance section | ✅ | 連到 `docs/perf-report.md` |
| `CLAUDE.md` Observability section（三檔 + ADR-0002） | ⚠️ | section 完整，但 ADR 連結指向不存在檔案（見 Findings #1） |
| `CLAUDE.md` Performance / Load testing 段 | ✅ | 列三情境 + scripts/k6/ 與 perf-report 連結 |
| `requirements.txt` 11 個 observability 套件 pin | ✅ | 全部 `==X.Y.Z` 格式 |
| 新增 `tests/test_k6_scripts.py`（10） | ✅ | 全綠 |
| 新增 `tests/test_docs_perf_report.py`（3） | ✅ | 全綠 |
| 新增 `tests/test_compose_loadtest_profile.py`（3） | ✅ | 全綠 |
| 新增 `tests/test_requirements_pinned.py`（1） | ✅ | 全綠 |
| 新增 `tests/test_docs_observability.py`（3） | ✅ | 全綠（但見 Findings #1，test 僅查字串不查 link target） |
| Sprint A/B 既有 60 case 全綠 | ✅ | 80 passed 含新增 20 |
| API 不變動 | ✅ | 既有 API contract test 全綠 |

## Test results

- 測試指令：`pytest tests/ -v`
- 結果：**80 passed / 0 failed / 0 skipped**
- 既有 suite regression：✅ 無
- 跑時 stderr 出現 BatchSpanProcessor 連 jaeger 失敗的 `ValueError: I/O operation on closed file` 雜訊——這是 Sprint B QA 報告中已記錄的 cosmetic teardown issue，與 Sprint C 無關，且不影響 test 結果。

## Findings

### 🔴 Critical（Must Fix）

無。

### 🟡 Warnings（Should Fix）

- **README.md:476** 與 **CLAUDE.md:161**: ADR 連結 `[docs/decisions/0002-observability-otel-prometheus.md](docs/decisions/0002-observability-otel-prometheus.md)` 指向不存在檔案——實際 ADR 檔名為 `docs/decisions/0002-otel-with-dual-exporter.md`（見 `ls docs/decisions/`）。
  - 為何是問題：dead link 對 reviewer / 面試官會明顯露出文件未對齊；Sprint B QA 是要求補 Observability section 並連回 ADR-0002，這個連結 broken 等同於沒補完。
  - `tests/test_docs_observability.py::test_claude_md_has_observability_section` 只檢查 `ADR-0002` 字串存在，不檢查 link target 是否實際存在，所以 test pass 但問題仍在。
  - 建議修法：兩處的 `0002-observability-otel-prometheus.md` 改為 `0002-otel-with-dual-exporter.md`。可順手在 test 中加一條斷言：`assert (Path("docs") / "decisions" / "0002-otel-with-dual-exporter.md").exists()` 防回歸。

- **docker-compose.yml:100**: k6 service 的 `command` 寫死 `/scripts/redirect_hot.js`，要跑 cold / image_mixed 必須在 CLI 用 `docker-compose --profile loadtest run --rm k6 run /scripts/<script>.js` 覆蓋整個 command。
  - 為何是問題：spec acceptance criteria 寫「command 預設 ... 可被覆蓋」——目前確實可覆蓋但 docs/perf-report.md 與 scripts/k6/README.md 已說明使用 `run --rm` 形式，所以可接受。屬資訊性提醒。
  - 建議修法：在 `scripts/k6/README.md` 明確列出每個情境覆蓋 command 的範例（若尚未列出）。

### 🟢 Suggestions

- **scripts/k6/redirect_cold.js:71-72**: comment 已自承「POST /v1/qr_code 會 pre-warm Redis URL cache，所以第一次 redirect 其實是 cache hit；要強制 DB fallback 需 flush Redis」。誠實標註很好，但若想真正量到 DB fallback 路徑，可考慮在 cold 情境 VU 中插入 `DEL qr:url:{token}`（透過額外端點或直接打 Redis）。目前實作仍能呈現 INSERT + SETEX 的整體寫入成本，屬合理 trade-off。
- **scripts/k6/seed.js**: token 收集是 `console.log` 印 `TOKEN:` prefix，由 shell `grep | sed | jq` 拼成 `tokens.json`。contract 接受此替代實作。若想更穩健可改用 [xk6-output-statsd](https://github.com/grafana/xk6-output-statsd) 或自訂 binary，但這是後續優化。
- **docker-compose.yml** 沒有 pin k6 image（用 `grafana/k6:latest`）——spec Open question 已標註，先用 latest，可日後 pin。

### ✅ What looks good

- 五個 k6 `.js` 檔頂端 block comment 都齊備（用途 / 執行指令 / 預期 bottleneck / 可調 thresholds），閱讀體驗很好。
- `docs/perf-report.md` 把 Prometheus query 與 Grafana panel 都標出來，符合「不用實跑也能讀得懂該量什麼」的目標。
- `requirements.txt` observability 套件 pin 版本準確（11 套件全部 `==X.Y.Z`，含 contract 列出的 10 個 + 額外的 `opentelemetry-api`）。
- `tests/test_k6_scripts.py` 在有 `node` 時跑 `node --check`、沒 node 時 skip，可移植性好。
- Sprint B QA warnings #1（README Observability）、#3（CLAUDE.md 三檔職責）、requirements pin 都實質清掉（dead link 議題另計）。
- 測試覆蓋面切得很細：腳本 syntax、compose profile、文件結構、placeholder source 註解、requirements pin、observability section — 對應 contract 每條 deliverable。

## 結論

- 是否可合併：**是**（單一 dead link 在合併前修一行就好，不阻斷）
- 最該先處理的 1–3 件事：
  1. 修 README.md:476 與 CLAUDE.md:161 兩處 ADR 連結指向實際存在的 `0002-otel-with-dual-exporter.md`。
  2.（可選）`tests/test_docs_observability.py` 加一條 `assert ADR file exists` 防回歸。
  3.（可選）`scripts/k6/README.md` 補上 cold / image 情境的 docker-compose run override 範例。
