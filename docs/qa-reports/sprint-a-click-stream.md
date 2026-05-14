# QA Report: sprint-a-click-stream

- **Contract**: docs/contracts/sprint-a-click-stream.md
- **Spec**: docs/specs/sprint-a-click-stream.md
- **ADR**: docs/decisions/0001-redis-streams-for-click-pipeline.md
- **Date**: 2026-05-14
- **Verdict**: ⚠️ Pass with issues（無 P0/P1，但有需修補的 warnings）

## Contract checklist

| Deliverable | Status | Note |
|---|---|---|
| `app/main.py` `_record_click` 改為 XADD、簽名不變 | ✅ | `_record_click(redis, token)` 簽名保留；改為 `publish_click(redis, token, ts)` |
| 失敗 swallow（不影響 302） | ✅ | `try/except Exception: pass` |
| `app/services/click_stream.py` STREAM_KEY/GROUP_NAME 常數 | ✅ | 與 contract 字面一致 |
| `publish_click` 帶 maxlen=100000 approximate | ✅ | `maxlen=100000, approximate=True` |
| `ensure_group` 對 BUSYGROUP swallow | ✅ | 透過字串比對例外訊息 |
| `read_batch` 包 XREADGROUP | ✅ | 回傳 `[(entry_id, fields)]` 已 decode |
| `claim_stale` 包 XPENDING + XCLAIM | ✅ | 用 `xpending_range` + `time_since_delivered` 過濾 |
| `ack` 包 XACK | ✅ | 空 list 自動 noop |
| `mark_processed` 用 dedupe key NX EX 3600 | ✅ | TTL = `_DEDUPE_TTL = 3600` |
| `app/worker.py` `__main__` entrypoint | ✅ | |
| Env 讀取 CONSUMER_NAME / BATCH_SIZE / FLUSH_INTERVAL_SECONDS / CLAIM_MIN_IDLE_MS | ✅ | 預設值符合 contract |
| `ensure_group` on startup | ✅ | |
| 啟動 `claim_stale` 領回孤兒 | ✅ | |
| 主迴圈 `read_batch(count=BATCH_SIZE, block_ms=1000)` | ✅ | |
| `mark_processed` 守門後累加到 buffer | ✅ | `FlushBuffer.add` |
| Flush 觸發條件（size or interval） | ✅ | `len(buffer.ids) >= batch_size or elapsed >= flush_interval` |
| Flush 用 entry ts 決定 hour bucket、跨小時分組 | ✅ | `FlushBuffer.counts: {hour: {token: count}}` |
| 先 hincrby 再 ack | ✅ | `flush()` 內順序正確 |
| SIGTERM / SIGINT 處理：drain 後退出 | ✅ | `_handle_signal` 設旗標，主迴圈外再呼叫一次 `flush` |
| `run_once(redis, consumer_name, buffer)` 拆分 | ✅ | 簽名與 contract 一致 |
| Dockerfile 維持單 image，CMD 為 uvicorn | ✅ | 沒動 |
| docker-compose.yml redis/postgres/api/worker | ✅ | 四 service 齊全；api 對外 `8000:8080` |
| 更新 README.md「Architecture (Sprint A)」+ mermaid | ✅ | line 356 起、含拓樸圖與 docker-compose 段 |
| 更新 CLAUDE.md「Click Stats Pipeline」+ 指向 ADR-0001 | ✅ | line 90 起、附 ADR 連結 |
| API 不變動聲明（所有公開 endpoint byte-identical） | ✅ | `tests/test_qr.py` 全綠（28 case） |
| 新增 `tests/test_click_stream.py` 全部 10 case | ✅ | 全綠 |

## Test results

- 測試指令：`pytest tests/ -v`
- 結果：**38 passed / 0 failed / 0 skipped**
- 失敗測試：無
- 既有 suite regression：✅ 既有 `tests/test_qr.py` 全綠

對照 contract 的 10 個必測 case：
- ✅ `test_xadd_on_redirect_cache_hit`
- ✅ `test_xadd_on_redirect_cache_miss`
- ✅ `test_no_hincrby_on_redirect`
- ✅ `test_ensure_group_idempotent`
- ✅ `test_worker_run_once_aggregates_and_acks`
- ⚠️ `test_worker_dedupe_skips_replay`（見 Findings — 測試方式偏弱）
- ✅ `test_worker_claims_stale_pending_on_startup`
- ✅ `test_worker_handles_cross_hour_batch`
- ✅ `test_worker_flush_then_existing_flush_job`
- ✅ `test_thousand_redirects_one_token`

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）

- **app/worker.py:81-99 `run_once` count 寫死 500**
  函式忽略外部 `BATCH_SIZE`，永遠 `read_batch(..., count=500, block_ms=0)`。`main()` 自己另外維護一份相同邏輯（line 158-165）導致 batch 大小決策有兩處實作；只要 contract 之後改 default，會漏改一處。
  影響：低（功能上正確），但違反 DRY、不利後續調整。
  建議：讓 `run_once` 接受 `count` / `block_ms` 參數（或包成 config dataclass），`main()` 直接呼叫 `run_once` 取代行內複製的 14 行邏輯。

- **tests/test_click_stream.py:179-214 `test_worker_dedupe_skips_replay` 並非真正測 replay**
  Generator 自己在 known limit 中標明：因 fakeredis 的 `time_since_delivered` 不隨 sleep 增加，改用「預寫 dedupe key + xadd 新 entry」模擬。問題是這只驗證了「dedupe key 存在 → run_once 不會累加」，沒有覆蓋真正的 XCLAIM 重派路徑。
  影響：dedupe 邏輯本身的單元行為有蓋到（mark_processed 回 False 走 ack 分支），但 contract 原意「XCLAIM 重派也只算一次」這條敘事弱了一截。
  建議：補一個整合式 case — consumer-A 讀後不 ack；consumer-B 用 `min_idle_ms=0` `claim_stale` 拿回來；run consumer-B 的 run_once 看到該 entry 第一次處理；接著手動再 claim 一次（或人工 inject 已存在 dedupe key），run_once 再跑一遍 → hash 仍為 1。這樣才真正測「重派但只算一次」。

- **app/services/click_stream.py:38-41 `ensure_group` 用字串比對例外**
  `except Exception` + `if "BUSYGROUP" not in str(exc): raise`。對 redis-py 是 `ResponseError`，未來換 client 或例外 message 文案變動會破。
  影響：低。
  建議：改 `except redis.exceptions.ResponseError as exc:` 並仍以 `BUSYGROUP` 子字串確認；其他例外讓它原生 propagate。

- **app/worker.py:71-78 `_ts_to_hour` 對非預期 ts 格式 silently fallback 到 now()**
  Contract 規定 producer 永遠送 `%Y-%m-%dT%H:%M:%SZ`，理論上 fallback 不會觸發；但 silently 改 hour bucket 會掩蓋上游 bug。
  影響：低。Spec 已說 sprint A 不做 observability，但這條 fallback 是「沉默吞錯」。
  建議：至少 `logger.warning("unparseable ts=%r, falling back to now()", ts)`，Sprint B 上 structlog 時自然會被替換。

- **app/services/click_stream.py:77 `xpending_range count=500` 寫死**
  與 worker BATCH_SIZE 解耦。若一次 crash 累積 > 500 個 pending，只有前 500 個會被 reclaim，剩下的要靠下一次 worker 重啟才會撈到。
  影響：低（按 contract 100 QPS 等級下，60 秒 idle 上限內累積到 500 + 機率很小），但留個地雷。
  建議：把 count 提到模組常數或讓 `claim_stale` 接 `count` 參數，至少在 docstring 標明上限。

### 🟢 Suggestions

- **app/worker.py:131-138 `_shutdown = [False]` 用 list 包 mutable**
  Python 慣用法但用 `threading.Event` 更直白且未來改多 thread 不會踩雷。

- **app/main.py:91-101 `_record_click` swallow 不分類**
  `except Exception: pass` 太寬。Redis connection error / timeout 算 expected、其他例外應該 re-raise 或至少 log。Spec 已說 Sprint B 才上 observability，但目前連 `logger.warning` 都沒有，故障時除錯成本高。建議至少 `logger.warning("publish_click failed: %s", exc)`。

- **app/worker.py:158-165 主迴圈與 `run_once` 重複邏輯**
  與上述 🟡 第一條同源；若採納把 main 改成呼叫 `run_once`，這條自然消除。

- **docker-compose.yml** 沒設 `restart: unless-stopped`。worker crash 後不會自動拉起，需手動 `docker compose up -d worker`，跟手動驗證腳本（contract 列的「kill-restart」場景）對得上但對日常 demo 不友善。

- **app/services/click_stream.py:127** `_now_iso` 定義了但沒被使用 — `_record_click` 自行算 ts。可以 export 出去當公用 helper、或刪掉。

### ✅ What looks good

- Producer / consumer 切分清楚，`click_stream` 模組是純包裝層，`worker` 不直接呼 redis-py 的 stream API，方便日後換 broker。
- `FlushBuffer` 是乾淨的小 dataclass，跨小時的 nested dict 設計剛好對到 contract 「跨 hour 的 batch 分組」需求，沒有過度設計。
- `flush()` 順序正確：先 hincrby（推 hash） → 後 ack（標記消費）。中間 crash 會留 pending → XCLAIM 領回；不會 lost click。配合 dedupe key 也守住 double-count。
- SIGTERM/SIGINT 用旗標 + 主迴圈外再 flush 一次的 pattern 標準，graceful drain 邏輯清楚。
- API 不變動聲明驗到位：`tests/test_qr.py` 28 case 全綠，redirect/cache/image/CRUD endpoint 行為 byte-identical。
- `test_thousand_redirects_one_token` 真的跑 1000 次 `/r/{token}`，xlen + drain loop + 最終 hash == 1000，覆蓋到 contract 的 throughput 驗證。
- `test_worker_handles_cross_hour_batch` 用 hour boundary 兩側的 ts 驗 nested dict 分組，邏輯漂亮。
- docker-compose.yml 的 healthcheck + depends_on condition 設置完整，符合「一鍵起拓樸」的 spec。
- Dockerfile 沒動 — 守住 contract「不在 Dockerfile 寫死 worker 啟動」的要求。

## 結論

- **是否可合併**：是。所有 P0/P1 級 deliverable 都完成、38 個測試全綠、API 不變動聲明驗到位、crash recovery + idempotency 路徑都有測試覆蓋。標記為「Pass with issues」是因有 5 條 🟡 warning，但都不阻擋合併。
- **最該先處理的 3 件事**：
  1. `test_worker_dedupe_skips_replay` 改成真正走 XCLAIM 重派路徑（最弱的測試覆蓋）。
  2. `run_once` 收參數，消除 `main()` 內重複的 14 行 batch-processing 邏輯（DRY + 後續調整單點）。
  3. `_record_click` swallow 至少加 `logger.warning` — 為 Sprint B observability 鋪路、也讓 Memorystore 故障時可追蹤。
