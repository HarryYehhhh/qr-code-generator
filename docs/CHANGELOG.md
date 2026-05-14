# CHANGELOG

Harness 執行記錄。**最新的在上面**（reverse chronological）。

每條由 planner / evaluator 在自己跑完後追加，主 Claude 不直接寫。
格式：
```
## YYYY-MM-DD — <sprint 或 feature> — <agent> — <一句話結果>
- 連結：spec / contract / qa-report / ADR
- 備註：循環次數、未解問題、後續工作（若有）
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
