# Spec: Sprint A — Click pipeline 拆分為 producer / consumer 微服務

## Goal
把同步的 redirect-time click counting 改造成 **API (producer) → Redis Streams → Worker (consumer)** 的非同步 pipeline，讓 click 計數可獨立水平擴展，並建立面試展示微服務 / 高併發理解的第一段敘事。

## User stories
- As a **service operator**, I want redirect 請求在 Redis 暫時不可寫入時仍能完成 302，so that 短暫的 click pipeline 故障不影響核心 redirect SLA。
- As a **service operator**, I want click 計數由獨立 worker process 處理，so that 我可以單獨擴展或重啟 worker 而不影響 API。
- As a **developer**, I want `docker-compose up` 就能在本地起完整拓樸 (api + worker + redis + postgres)，so that 開發、demo、e2e 測試流程一致。
- As an **interviewer audience**，我能在 commit / README 看到「producer / consumer / consumer group / pending entries / idempotency」這些微服務術語對應到實際程式碼。

## Acceptance criteria
- [ ] `app/main.py` 的 `_record_click()` 改為對 `clicks:stream` 做 `XADD * token <token> ts <iso>`，並帶 `MAXLEN ~ 100000`；不再呼叫 `HINCRBY`。
- [ ] 新增 `app/worker.py`，可用 `python -m app.worker` 啟動。Worker 連 Redis、ensure consumer group `click-aggregator` 存在 (`XGROUP CREATE ... MKSTREAM`)，迴圈 `XREADGROUP`。
- [ ] Worker 在記憶體 dict 累加，達到 `BATCH_SIZE` (預設 500) 或 `FLUSH_INTERVAL_SECONDS` (預設 5) 後 flush 到 `qr:clicks:{YYYY-MM-DD-HH}` hash (沿用既有 schema)，再 `XACK` 對應 entry IDs。
- [ ] Crash recovery：worker 啟動時對 group 跑 `XPENDING` + `XCLAIM`（min-idle-time 60s）把孤兒 entry 領回再處理。
- [ ] Idempotency：每個 stream entry ID 在處理前用 `SET qr:clicks:dedupe:{entry_id} 1 EX 3600 NX` 守門，已存在則直接 `XACK` 跳過。
- [ ] `app/jobs/flush_clicks.py` 不變動；`/internal/flush_clicks` 行為不變。
- [ ] Dockerfile 維持單一 image，兩種啟動方式：
  - API: `uvicorn app.main:app --host 0.0.0.0 --port 8080`
  - Worker: `python -m app.worker`
- [ ] 新增 `docker-compose.yml`，本地一鍵起 `api`、`worker`、`redis`、`postgres` 四個 service。
- [ ] **公開 API 完全沒變動**：`POST /v1/qr_code`、`GET /v1/qr_codes`、`GET /v1/qr_code/{token}`、`PUT/DELETE /v1/qr_code/{token}`、`GET /v1/qr_code_image/{token}`、`GET /r/{token}` 的 request / response schema、status code、headers 全部與 sprint 前 byte-for-byte 一致。
- [ ] `pytest tests/ -v` 全綠（含新增的 `tests/test_click_stream.py`）。
- [ ] `docker-compose up` 起來後：
  - 對 `/r/{token}` 發請求 → worker stdout log 顯示收到事件
  - `redis-cli HGETALL qr:clicks:{current_hour}` 顯示對應 token 的計數遞增
- [ ] **Resilience 驗證**：worker 處理途中被 `SIGKILL`，再啟動同一個 worker，pending entry 被 `XCLAIM` 回來重跑，最終 `qr:clicks:{hour}` 的累積計數 == 實際 redirect 次數，**沒有 lost click、沒有 double count**（idempotency 守住）。
- [ ] **Throughput 驗證**：同一個 token 發 1000 次 `/r/{token}`，等 worker flush + 觸發 `/internal/flush_clicks` 後，DB `qr_click_stats` 對應 row 的 `click_count == 1000`。

## Non-goals
- Sprint A 不導入 OpenTelemetry / Prometheus / structlog（留 Sprint B）。
- 不做 k6 壓測與 perf report（留 Sprint C）。
- 不導入 Kafka / RabbitMQ；只用既有 Redis（決策見 ADR-0001）。
- 不改 `qr_click_stats` schema、不改 `/internal/flush_clicks` 行為。
- 不導入 rate limiting、circuit breaker、retry policy。
- 不部署 worker 到 Cloud Run / GKE；本 sprint 只交付 local docker-compose 拓樸與 image。Production 部署留作後續 sprint。

## Open questions
- Worker 在 Cloud Run 的部署形態（Cloud Run Job？另一個 Cloud Run service？GKE？）— 不在 Sprint A scope，但 ADR 留 placeholder。
- 是否需要 dead-letter stream — 目前以 dedupe key + XCLAIM 守住，暫不引入。
