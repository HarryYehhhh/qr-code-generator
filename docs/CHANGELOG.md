# CHANGELOG

Harness 執行記錄。**最新的在上面**（reverse chronological）。

每條由 planner / evaluator 在自己跑完後追加，主 Claude 不直接寫。
格式：
```
## YYYY-MM-DD — <sprint 或 feature> — <agent> — <一句話結果>
- 連結：spec / contract / qa-report / ADR
- 備註：循環次數、未解問題、後續工作（若有）
```

## 2026-05-15 — simplify-observability — manual refactor — 移除 OTel + Prometheus stack，保留 structlog

- 連結：[ADR-0004](decisions/0004-simplify-observability-keep-structlog.md)（部分 supersedes ADR-0002）
- 動作：刪 `app/observability.py`、`app/metrics.py`、`tests/test_observability.py`、`tests/test_metrics.py`、`tests/test_span_attributes.py`、`tests/test_docs_observability.py`、`docker/prometheus.yml`、整個 `docker/grafana/` 目錄；改 `app/main.py`（移除 init_tracing / Instrumentator / span 建立 / metric 呼叫；redirect 改用 `logger.info` 帶 cache_result）、`app/services/qr_service.py`（移除 manual span）、`app/services/image_service.py`（移除 span + metric，改用 logger）、`app/logging.py`（移除 `_add_trace_context` processor）、`tests/conftest.py`（移除 tracer / exporter fixtures）、`tests/test_compose_loadtest_profile.py`（移除 prometheus 斷言）、`tests/test_requirements_pinned.py`（改檢查核心依賴）、`docker-compose.yml`（移除 jaeger/prometheus/grafana service + OTEL env）、`requirements.txt`（移除 9 個套件，保留 structlog）、`CLAUDE.md` + `README.md` + `docs/architecture.md` Observability 段
- API 變動：`/metrics` endpoint 不再存在
- Stack 變動：docker compose 從 7 個 service（api/postgres/redis/jaeger/prometheus/grafana/k6 loadtest）縮成 4 個（api/postgres/redis/k6 loadtest）
- 驗證：`pytest tests/ -q` → **44 passed**（從 61 降到 44，刪掉 17 個 obs/metrics/span 相關 test）；docker compose 重建 + smoke：POST/GET/redirect/image 全綠、`/metrics → 404`、structured JSON log 正常輸出（含 cache_result 欄位）
- LOC 影響：~ -600 LOC + 3 個 docker container + 9 個 dependency
- 後續：可重跑 k6 baseline vs current 驗證 OTel overhead 移除後 current 應追上 baseline

## 2026-05-15 — remove-click-counting-mvp — manual refactor — Sprint A click pipeline 整套移除，focus 收斂在 redirect

- 連結：[ADR-0003](decisions/0003-remove-click-counting-mvp.md)（supersedes ADR-0001）；[`docs/architecture.md`](architecture.md)（新增的目標架構文件）
- 動作：刪 `app/services/click_stream.py`、`app/worker.py`、`app/jobs/`、`app/routers/internal.py`、`tests/test_click_stream.py`；改 `app/main.py` / `app/models.py` / `app/schemas.py` / `app/metrics.py` / `app/routers/qr.py` / `tests/test_metrics.py` / `tests/test_span_attributes.py`；新增 alembic migration `0003_remove_click_counting`；改 `docker-compose.yml` 拿掉 worker service；大幅 update `CLAUDE.md` / `README.md`
- API 變動：`GET /v1/qr_codes` 與 `GET /v1/qr_code/{token}` 不再回傳 `click_count`；`/internal/flush_clicks` 整 endpoint 移除
- 驗證：`pytest tests/ -q` → 61 passed（舊 81 pass，刪 20 click 相關 test）；`docker compose up` + `alembic upgrade head` + 手動 POST/GET/redirect/image smoke test 全綠；`/metrics` 已不含 `qr_click_stream_*`
```

---

<!-- 新條目從這裡開始 -->

## 2026-05-14 — sprint-c-load-test — evaluator — ⚠️ Pass with issues
- 連結：docs/qa-reports/sprint-c-load-test.md
- 備註：pytest tests/ -v 80/80 passed（既有 60 + 新增 20）；🔴 0、🟡 2、🟢 3；循環次數 1；未解 P0/P1 = 0。Contract 全條 deliverable 打勾，API 不變動，Sprint B 三條 QA warnings 實質清除。唯一 issue：README.md:476 與 CLAUDE.md:161 ADR 連結指向 `0002-observability-otel-prometheus.md` 但實際檔名為 `0002-otel-with-dual-exporter.md`（dead link，generator 自述已知）。test_docs_observability 僅查字串不查 link target，所以 test 綠但問題仍在。可合併，建議合併前順手改連結。

## 2026-05-14 — sprint-c-load-test — planner — spec + contract 完成
- 連結：docs/specs/sprint-c-load-test.md, docs/contracts/sprint-c-load-test.md
- 備註：k6 三情境（redirect_hot / redirect_cold / image_mixed）+ seed + lib/common 共 5 個 `.js` 檔；docker-compose 新增 `k6` service 走 `loadtest` profile + prometheus remote-write；`docs/perf-report.md` 骨架完備，數字以 `<TBD:>` placeholder + 每處標 Run/Source 由使用者本地實跑後填。一併收 Sprint B 三條 QA warnings：README 補 Observability + Performance section、CLAUDE.md 補 Observability 段、`requirements.txt` 10 個 observability 套件 pin 版本。Scope 明列「不在 sandbox 內實跑 k6」，避免 evaluator 拿沒有真實數字當 Fail。無 ADR。Open questions：k6 image tag 是否 pin（暫 latest）；threshold 數值（500/1500/800 ms）由使用者依機器調整不鎖死。

## 2026-05-14 — sprint-b-observability — evaluator — ⚠️ Pass with issues
- 連結：docs/qa-reports/sprint-b-observability.md
- 備註：pytest tests/ -v 60/60 passed（既有 38 + 新增 22）；🔴 0、🟡 5、🟢 6；循環次數 1；未解 P0/P1 = 0。Sprint A 五條 QA warnings 經對應測試（`test_record_click_failure_emits_warning_log` / `test_run_once_respects_batch_count_param` / `test_main_loop_calls_run_once` / `test_worker_dedupe_skips_replay_real_xclaim` / `test_ensure_group_catches_response_error_only` / `test_claim_stale_count_param`）真實清除驗證通過。API 不變動聲明 38 case 全綠。主要 warning：README/CLAUDE.md 未補 Observability 章節（Docs deliverable 三條未交）、requirements.txt observability 套件未鎖版本（ADR-0002 明文要求）、BatchSpanProcessor 測試 teardown stderr 污染、`click_stream.consume_batch` wrapper 未實作（contract 允許「或於 worker 處」以 `worker.run_once` 替代）、Instrumentator 在 module load 而非 lifespan。可合併。

## 2026-05-14 — sprint-b-observability — planner — spec + contract + ADR 完成
- 連結：docs/specs/sprint-b-observability.md, docs/contracts/sprint-b-observability.md, docs/decisions/0002-otel-with-dual-exporter.md
- 備註：三本柱（OTel traces + Prometheus metrics + structlog JSON logs）導入，dual exporter（local→Jaeger / prod→Cloud Trace）由 ENVIRONMENT 切換。docker-compose 補 jaeger/prometheus/grafana 三個 service，含預設 dashboard。一併把 Sprint A 的 5 條 QA warnings 收進本 sprint scope（_record_click warning log、run_once 參數化、dedupe replay test 改走真 XCLAIM、ensure_group ResponseError 判斷、xpending_range count 抽常數）。Open questions：Cloud Trace exporter 在無 GCP credentials 環境的 fallback 由 google-auth 處理，不在本 sprint 額外驗。

## 2026-05-14 — sprint-a-click-stream — evaluator — ⚠️ Pass with issues
- 連結：docs/qa-reports/sprint-a-click-stream.md
- 備註：pytest tests/ -v 38/38 passed；🔴 0、🟡 5、🟢 5；循環次數 1；未解 P0/P1 = 0。所有 contract deliverable 打勾、API 不變動聲明驗證通過、crash recovery 與 idempotency 測試覆蓋到位。主要 warning 是 `test_worker_dedupe_skips_replay` 未真正走 XCLAIM 重派路徑、`run_once` 與 `main()` 有 DRY 重複，以及 `_record_click` swallow 沒 log。可合併。

## 2026-05-14 — sprint-a-click-stream — planner — spec + contract + ADR 完成
- 連結：docs/specs/sprint-a-click-stream.md, docs/contracts/sprint-a-click-stream.md, docs/decisions/0001-redis-streams-for-click-pipeline.md
- 備註：把 click pipeline 改成 Redis Streams producer/consumer。Spec 鎖定公開 API 不變、worker 走 XREADGROUP + XPENDING/XCLAIM、idempotency 用 dedupe key、既有 flush job 不動。ADR-0001 記錄選 Streams 而非 Pub/Sub / Kafka / Cloud Pub/Sub 的理由。Open question：worker 在 Cloud Run 的部署形態留作後續 sprint。
