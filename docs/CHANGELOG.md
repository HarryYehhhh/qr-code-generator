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

## 2026-05-14 — sprint-a-click-stream — evaluator — ⚠️ Pass with issues
- 連結：docs/qa-reports/sprint-a-click-stream.md
- 備註：pytest tests/ -v 38/38 passed；🔴 0、🟡 5、🟢 5；循環次數 1；未解 P0/P1 = 0。所有 contract deliverable 打勾、API 不變動聲明驗證通過、crash recovery 與 idempotency 測試覆蓋到位。主要 warning 是 `test_worker_dedupe_skips_replay` 未真正走 XCLAIM 重派路徑、`run_once` 與 `main()` 有 DRY 重複，以及 `_record_click` swallow 沒 log。可合併。

## 2026-05-14 — sprint-a-click-stream — planner — spec + contract + ADR 完成
- 連結：docs/specs/sprint-a-click-stream.md, docs/contracts/sprint-a-click-stream.md, docs/decisions/0001-redis-streams-for-click-pipeline.md
- 備註：把 click pipeline 改成 Redis Streams producer/consumer。Spec 鎖定公開 API 不變、worker 走 XREADGROUP + XPENDING/XCLAIM、idempotency 用 dedupe key、既有 flush job 不動。ADR-0001 記錄選 Streams 而非 Pub/Sub / Kafka / Cloud Pub/Sub 的理由。Open question：worker 在 Cloud Run 的部署形態留作後續 sprint。
