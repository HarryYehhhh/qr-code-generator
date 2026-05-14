# Contract: Sprint A — Click pipeline 拆分為 producer / consumer

對應 spec: docs/specs/sprint-a-click-stream.md
相關 ADR: docs/decisions/0001-redis-streams-for-click-pipeline.md

## Deliverables

### Backend — Producer 改造
- [ ] 在「應用程式碼路徑」`app/main.py` 修改 `_record_click(redis, token)`：
  - 移除 `redis.hincrby(...)`
  - 改為 `redis.xadd("clicks:stream", {"token": token, "ts": <ISO8601 UTC>}, maxlen=100000, approximate=True)`
  - 函式簽名與呼叫點不變；redirect handler 流程不變
- [ ] 新增「應用程式碼路徑」`app/services/click_stream.py`：
  - `STREAM_KEY = "clicks:stream"`
  - `GROUP_NAME = "click-aggregator"`
  - `publish_click(redis, token, ts) -> str`：包 `XADD`，回傳 entry id
  - `ensure_group(redis)`：`XGROUP CREATE ... MKSTREAM`，已存在則 swallow `BUSYGROUP`
  - `read_batch(redis, consumer_name, count, block_ms) -> list[(entry_id, fields)]`：包 `XREADGROUP`
  - `claim_stale(redis, consumer_name, min_idle_ms) -> list[(entry_id, fields)]`：包 `XPENDING` + `XCLAIM`
  - `ack(redis, entry_ids: list[str])`：包 `XACK`
  - `mark_processed(redis, entry_id) -> bool`：`SET qr:clicks:dedupe:{entry_id} 1 EX 3600 NX`；回傳是否首次處理

### Backend — Consumer / Worker
- [ ] 新增「應用程式碼路徑」`app/worker.py`：
  - `if __name__ == "__main__": main()` entrypoint
  - `main()` 流程：
    1. 讀 env：`CONSUMER_NAME` (預設 `worker-{hostname}-{pid}`)、`BATCH_SIZE` (預設 500)、`FLUSH_INTERVAL_SECONDS` (預設 5)、`CLAIM_MIN_IDLE_MS` (預設 60000)
    2. `ensure_group(redis)`
    3. 啟動時跑一次 `claim_stale` 把孤兒 entry 領回
    4. 進主迴圈：`read_batch(count=BATCH_SIZE, block_ms=1000)` → 對每筆 `mark_processed` 守門 → 累加到 `pending_counts: dict[str, int]` 與 `pending_ids: list[str]`
    5. 觸發條件（任一）：`len(pending_ids) >= BATCH_SIZE` 或 距離上次 flush 超過 `FLUSH_INTERVAL_SECONDS`
    6. Flush：對每個 token `redis.hincrby(f"qr:clicks:{hour}", token, count)`（用 entry 內 `ts` 決定 hour bucket，跨 hour 的 batch 分組）→ `ack(pending_ids)` → 清空 buffer
    7. 處理 `SIGTERM` / `SIGINT`：把 buffer flush + ack 再退出
  - 函式拆分以利測試：`run_once(redis, consumer_name, buffer)` 讓單元測試可驅動單一迭代

### Infra — 容器化
- [ ] 修改「Dockerfile」：
  - 維持單一 build stage、單一 image
  - `CMD` 預設為 API：`["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]`
  - 不在 Dockerfile 寫死 worker 啟動；worker 在 `docker-compose.yml` / 部署設定中以 `command: python -m app.worker` 覆寫
- [ ] 新增「`docker-compose.yml`」於專案根目錄：
  - `redis`：`redis:7-alpine`，expose 6379
  - `postgres`：`postgres:15`，env `POSTGRES_USER/PASSWORD/DB`，volume 持久化
  - `api`：build 本 repo，`command` 留預設 uvicorn，依賴 redis + postgres，env 帶 `DATABASE_URL`、`REDIS_URL`、`ENVIRONMENT=local-compose`
  - `worker`：build 本 repo，`command: python -m app.worker`，同樣 env，依賴 redis
  - api 對外 expose `8000:8080`（避免與 host 上其他服務衝撞）

### Docs
- [ ] 更新「`README.md`」：新增「Architecture (Sprint A)」小節，含 mermaid 圖描述 producer / stream / consumer group / worker / flush job 拓樸；更新本地啟動段落，加上 `docker-compose up` 路徑
- [ ] 更新「`CLAUDE.md`」的「Click Stats Pipeline」段：替換成 stream-based 描述（保留 flush job 段落不變），補一句指向 ADR-0001

## API 不變動聲明（contract test 必驗）

下列 endpoints 在 Sprint A 前後必須 byte-identical（status code、response body schema、headers）：

| Method | Path | 不變動要點 |
| --- | --- | --- |
| POST | `/v1/qr_code` | 201, `{"qr_token": "..."}` |
| GET | `/v1/qr_codes` | 200, list of `{qr_token, url, click_count, status, created_at}` |
| GET | `/v1/qr_code/{token}` | 200 / 410 / 404 |
| PUT | `/v1/qr_code/{token}` | 204 |
| DELETE | `/v1/qr_code/{token}` | 204 |
| GET | `/v1/qr_code_image/{token}` | 200, `image/png`, `Cache-Control: public, max-age=300, must-revalidate` |
| GET | `/r/{token}` | 302, `Location` 指向原 URL |
| POST | `/internal/flush_clicks` | 200 `{"rows_flushed": N}` / 403 |

## Out of scope
- OTel / Prometheus / structlog（Sprint B）
- k6 壓測（Sprint C）
- Cloud Run worker 部署
- Dead-letter stream
- `qr_click_stats` schema 變動
- Rate limiting / circuit breaker / retry

## Tests required

### 新增 `tests/test_click_stream.py`
- [ ] `test_xadd_on_redirect_cache_hit`：cache hit 路徑下 `/r/{token}` 後，`XLEN clicks:stream == 1`，entry fields 含正確 token
- [ ] `test_xadd_on_redirect_cache_miss`：cache miss 路徑下 `/r/{token}` 後同樣 `XADD` 一筆，且 URL 被 `SETEX` 回 cache
- [ ] `test_no_hincrby_on_redirect`：redirect 後 `qr:clicks:{hour}` 不應存在（worker 還沒跑）
- [ ] `test_ensure_group_idempotent`：連跑兩次不 raise
- [ ] `test_worker_run_once_aggregates_and_acks`：
  - 預先 `XADD` 3 筆同 token、2 筆不同 token
  - 跑 `run_once`
  - 驗證 `qr:clicks:{hour}` hash 正確 (`tokenA=3, tokenB=2`)
  - `XLEN` 沒減（XACK 不刪 entry，但 pending list 清空）→ `XPENDING clicks:stream click-aggregator` summary count == 0
- [ ] `test_worker_dedupe_skips_replay`：手動把同一 entry 餵兩次（用 `XCLAIM` 模擬重派）→ hash 只增加一次
- [ ] `test_worker_claims_stale_pending_on_startup`：模擬「consumer X 讀了沒 ack 就死掉」→ 用 `time.sleep` 或 fakeredis 的 idle 模擬 stale → 新 consumer 啟動跑 `claim_stale` → 領回後處理 → 沒 lost click
- [ ] `test_worker_handles_cross_hour_batch`：buffer 內含跨小時的 entry（ts 在 hour boundary 兩側）→ flush 分成兩個 hash key
- [ ] `test_worker_flush_then_existing_flush_job`：worker flush 完之後直接呼叫 `flush_previous_hour`（mock 時鐘讓 hash 屬於上一小時），驗證 DB 結果正確 → 確認既有 flush job 與新 worker 串接無縫
- [ ] `test_thousand_redirects_one_token`：對同一 token 跑 1000 次 `/r/{token}`，worker `run_once` 直到 stream 清空，最終 hash 值 == 1000

### 既有測試
- [ ] 既有 `tests/` 全部測試保持綠燈（contract test 不變）
- [ ] 若有 test 依賴 `qr:clicks:{hour}` 在 redirect 後立刻有值，改成「呼叫 worker.run_once 後再斷言」

### 手動 / e2e 驗證（記錄在 QA report）
- [ ] `docker-compose up` 後 `curl localhost:8000/r/{token}` 觀察 worker log
- [ ] `redis-cli HGETALL qr:clicks:{hour}` 確認計數
- [ ] `docker kill <worker-container>` 中途，再 `docker compose up -d worker` → 計數最終一致

## Definition of done
- 全部 deliverable 打勾
- `pytest tests/ -v` 全綠
- API 不變動聲明逐項通過 contract test
- `docker-compose up` 起得來、worker log 看得到事件、hash 計數正確
- Kill-restart 場景無 lost click、無 double count
- evaluator 的 QA report 無 P0/P1
