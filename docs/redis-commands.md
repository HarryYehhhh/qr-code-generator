# Redis 指令學習筆記（QR Code Generator 專用）

> 這份只列出**這個專案實際用到**的 Redis 指令，依用途分組。每個指令給三件事：
> 1. 語法（python-redis client API）
> 2. 在這個專案哪裡用、為什麼是它
> 3. 你該記得的陷阱

對應原始碼路徑：
- `app/services/qr_service.py` — URL / image cache
- `app/services/click_stream.py` — Streams producer / consumer
- `app/worker.py` — consumer 主迴圈
- `app/jobs/flush_clicks.py` — hourly hash → DB
- `app/main.py` — redirect handler 讀 cache

---

## 0. Redis 的資料結構速覽

| 結構 | 你可以把它想成 | 在這個專案用來做什麼 |
|---|---|---|
| **String** | key → value，最普通的 KV，value 可以是 bytes | URL cache、image PNG bytes、dedupe 標記 |
| **Hash** | key → `{field: value, ...}`（一張小表） | `qr:clicks:{hour}` 累加各 token 的點擊次數 |
| **Stream** | append-only log + consumer group（有點像迷你版 Kafka） | `clicks:stream` 點擊事件佇列 |

**為什麼挑這三個**：String 是基本款（cache 用），Hash 適合「同一個 namespace 下多個 counter」（hourly 點擊統計），Stream 提供 **at-least-once + consumer group**，比 Pub/Sub 強（Pub/Sub 訂閱者沒在的時候訊息會掉）。

---

## 1. String（簡單 KV）

### 1.1 `GET` — 讀單一 key

```python
cached = redis.get("qr:url:abc123")   # 回傳 bytes 或 None
```

**專案用法**
- `app/main.py:124` 在 `/r/{token}` redirect 路徑讀 URL cache
- `app/services/image_service.py:54` 讀圖片 bytes cache
- 都先 GET 看 cache，miss 才落 DB / regenerate

**為什麼選它**：O(1)、單一 round-trip、`redis-py` 不必序列化（bytes in/out）。

**陷阱**
- 回傳是 `bytes`，要顯示成字串得 `.decode()`
- Miss 回 `None`，不是 KeyError；別當 dict 用

---

### 1.2 `SETEX` — set with TTL

```python
redis.setex("qr:url:abc123", 86400, url)   # 86400 秒 = 1 天
```

**專案用法**
- `app/services/qr_service.py:36` 建立 QR 時預熱 URL cache
- `app/services/qr_service.py:116` redirect cache miss 後回填
- `app/services/qr_service.py:127` image bytes cache（TTL 7 天）

**為什麼選它**：cache 一定要有過期時間，否則 stale data 永遠不會自己消失。`SETEX` 是 `SET` + `EXPIRE` 合成的原子操作，省一次 round-trip 也避免「設好值但忘了設 TTL」的人為錯。

**陷阱**
- TTL 設太短 → 命中率掉、DB 壓力上升
- TTL 設太長 → URL 被改了但用戶還拿到舊網址（這也是 redirect 用 302 不用 301 的原因）

---

### 1.3 `SET … EX … NX` — 不存在才設

```python
result = redis.set(key, 1, ex=3600, nx=True)
# 回傳 True：成功設定（key 之前不存在）
# 回傳 None：key 已存在，沒動
```

**專案用法**
- `app/services/click_stream.py:137` `mark_processed()` 用這個做 idempotency。每個 stream entry ID 處理完前先嘗試 `SET dedupe:{entry_id} 1 EX 3600 NX`，**如果回 None 代表這顆已經處理過了，跳過就好**。

**為什麼選它**：worker 用 `XREADGROUP` + `XACK`，但 `XACK` 之前如果 crash，這顆訊息會被 `XCLAIM` 重新派發，**會被處理第二次**。靠 `SET NX` 擋這種 replay，是 at-least-once → exactly-once 的標準作法。

**陷阱**
- `nx=True` 才有意義，沒加就只是普通 `SET`
- TTL 要比訊息「最久可能被 replay」的時間長，但別永久存（會無限增長）

---

### 1.4 `DEL` — 刪 key

```python
redis.delete("qr:url:abc123")
```

**專案用法**
- `app/services/qr_service.py:78` URL 被 PUT 更新時，刪 URL cache（讓下次 redirect 重讀 DB 拿新值）
- `app/services/qr_service.py:94` DELETE QR 時清 cache
- `app/jobs/flush_clicks.py:43` flush 結束後刪 `:flushing` 鍵

**為什麼**：cache invalidation 兩大流派——**TTL（被動失效）+ DEL（主動失效）**。Update / Delete 走 DEL 比較確定。

**陷阱**
- `del` 是 Python 保留字，所以 redis-py 用 `delete()`（不要寫成 `redis.del(...)`，會 SyntaxError）
- 高併發下「先更新 DB 再 DEL」與「先 DEL 再更新 DB」都有 race condition，這個專案的 update 用「先寫 DB 再 DEL cache」，是常見的折衷

---

### 1.5 `EXISTS` — 檢查 key 在不在

```python
if redis.exists("qr:clicks:2026-05-14-15:flushing"):
    ...
```

**專案用法**
- `app/jobs/flush_clicks.py:34-35` flush 之前檢查上一輪有沒有殘留 `:flushing` 鍵（crash recovery）

**陷阱**
- 回傳 `int`（0 或 1），不是 bool。`if redis.exists(k):` 還是會運作因為 `if 0:` 是 False，但寫 `redis.exists(k) == 1` 更明確

---

## 2. Hash（namespace 下多個 counter）

### 2.1 `HINCRBY` — hash field 累加

```python
redis.hincrby("qr:clicks:2026-05-14-15", "abc123", 5)
# 把 qr:clicks:2026-05-14-15 這個 hash 裡 abc123 這個 field +5
```

**專案用法**
- `app/worker.py:125` worker 從 stream 讀到一批點擊，**在記憶體聚合**後一次寫進對應小時的 hash

**為什麼選 hash 而不是 N 個 String key**
- 一個小時可能有上千個 token 被點擊，用 hash 把它們收在 **同一個 key 下**，省記憶體（Redis 對小 hash 有 ziplist 編碼）、好刪除（一次 `RENAME` 整個小時）
- `HINCRBY` 是原子的，多個 worker 同時打也安全

**陷阱**
- HINCRBY 本身**不是 idempotent**：同一筆訊息 replay 會多算。這個專案靠 `mark_processed()` 的 `SET NX` 擋；沒擋的話得在邏輯外另做去重
- Hash 累積太多 field（百萬級）會退化成 hashtable encoding，記憶體跳一個檔次

---

### 2.2 `HSCAN` — 邊掃邊讀 hash（不會卡 Redis）

```python
cursor = 0
while True:
    cursor, fields = redis.hscan(key, cursor=cursor, count=1000)
    for token, count in fields.items():
        ...
    if cursor == 0:
        break
```

**專案用法**
- `app/jobs/flush_clicks.py:52` hourly flush job 把 `qr:clicks:{hour}:flushing` 的所有 field 排空到 Postgres

**為什麼不用 `HGETALL`**
- `HGETALL` 是 **O(N) 一次拉回全部**，hash 很大時會 block Redis（單執行緒），其他 client 全部排隊
- `HSCAN` 是 **cursor-based pagination**，每次拉 ~N 個 field，Redis 可以在批次之間處理其他指令

**陷阱**
- 必須迴圈直到 cursor 回到 0
- 同一個 cursor 期間如果 hash 被改了，可能會漏 / 重複看到 field（這個專案先 `RENAME` 到 `:flushing` 讓 source hash 凍結，繞掉這問題）

---

## 3. 通用 Key 操作

### 3.1 `RENAME` — 原子改名

```python
redis.rename("qr:clicks:2026-05-14-14", "qr:clicks:2026-05-14-14:flushing")
```

**專案用法**
- `app/jobs/flush_clicks.py:39` flush job 開始時，把 `qr:clicks:{previous_hour}` 改名成 `:flushing` 後綴

**為什麼這樣設計**
- Flush 過程中 worker 可能還在累加上一輪的 hash（時鐘臨界點）。**改名是原子的**，改完後 worker 寫不到舊 key，新 key（沒後綴）會被自動建立。等於切了個快照給 flush job 慢慢吃，不會跟 worker 寫入打架
- 中途 crash 也不會掉資料：下一輪 flush 進來看到殘留 `:flushing`，直接接著吃

**陷阱**
- Source key 不存在會 raise `ResponseError: no such key`，所以才要先 `EXISTS`
- 如果 destination 已存在，`RENAME` 會覆寫；用 `RENAMENX` 才會擋

---

## 4. Stream（核心：點擊事件 producer / consumer）

> Stream 是 Redis 5.0 加入的資料結構，行為類似 Kafka 但是嵌入在 Redis 裡。這個專案 Sprint A 拆 worker 就是靠它。

### 4.1 `XADD` — 追加事件到 stream

```python
entry_id = redis.xadd(
    "clicks:stream",
    {"token": "abc123", "ts": "2026-05-14T15:30:00Z"},
    maxlen=100000,            # 約略上限，避免無限長
)
# entry_id 形如 b"1715692200000-0"（毫秒-序號）
```

**專案用法**
- `app/services/click_stream.py:28` redirect handler 把每次點擊推進 stream（取代原本的同步 HINCRBY）

**為什麼用 XADD 而不直接 HINCRBY**
- 解耦：redirect 只負責「事件已記下」，不負責「累加到哪一小時的哪個 token」。後者交給 worker
- 可重放：worker 重啟可以從上次 XACK 的點繼續，不會掉
- 可擴展：未來想加第二個 consumer group（例如「即時通知」）只要訂閱同一條 stream

**陷阱**
- `maxlen=100000` 前面要不要加 `~`？**redis-py 預設就是 `~`（approximate trimming）**，效能好但實際長度可能略多。要精確 trim 用 `approximate=False`，但會慢
- 不加 maxlen 而 stream 無限長 → Redis 記憶體會被吃光

---

### 4.2 `XGROUP CREATE` — 建立 consumer group

```python
redis.xgroup_create("clicks:stream", "click-aggregator", id="0", mkstream=True)
```

**專案用法**
- `app/services/click_stream.py:55` worker 啟動時呼叫；已存在會 raise `ResponseError("BUSYGROUP ...")`，要 try/except 吞掉

**參數**
- `id="0"` 從 stream 開頭讀起；`id="$"` 從現在以後新的訊息開始
- `mkstream=True` 如果 stream 還不存在就先建（worker 比 producer 早起時很有用）

**陷阱**
- group 名字寫錯後 redis 不會幫你 raise，consumer 會「合法」開始一個新 group，看似運作但其實另一條獨立的消費路徑

---

### 4.3 `XREADGROUP` — consumer 拉取訊息

```python
result = redis.xreadgroup(
    "click-aggregator",            # group name
    "worker-1",                    # consumer name（同 group 內每個 worker 一個）
    {"clicks:stream": ">"},        # ">" = 還沒派給 consumer 的新訊息
    count=500,                     # 一次最多拉幾筆
    block=1000,                    # block 1000ms 直到有新訊息（避免空轉 busy loop）
)
```

**專案用法**
- `app/services/click_stream.py:73` worker 主迴圈每次 tick 都呼叫

**`>` 是什麼**
- `>` 意思是「**這個 consumer 還沒讀過的新訊息**」
- 給數字 ID（例如 `"0"`）會讀 PEL（pending entries list，已派發但沒 ACK 的）

**陷阱**
- consumer name 一定要**唯一**，重複名稱會搶同一條 PEL（這個專案用 hostname / pid 自動產生）
- `block=0` 是無限等；測試 / shutdown 要小心，給有限 timeout 才能 graceful 關機

---

### 4.4 `XACK` — 確認處理完成

```python
redis.xack("clicks:stream", "click-aggregator", "1715692200000-0", "1715692200000-1")
```

**專案用法**
- `app/services/click_stream.py:128` worker 把這一批訊息累加進 hash 並寫成功後一次性 ACK

**為什麼一定要 ACK**
- 沒 ACK 的訊息會卡在 PEL（pending entries list），下次 `XCLAIM` 會被別的 consumer 撿走重新處理
- 這就是 **at-least-once** 的關鍵：crash 前沒 ACK → 重做；ACK 之後 crash → 不重做

**陷阱**
- ACK 太早（還沒寫進 hash 就 ACK）→ crash 後資料掉
- ACK 太晚（等很久才一次 ACK）→ PEL 變很長、佔記憶體、其他 consumer 看到很多 stale entry

---

### 4.5 `XPENDING` / `XPENDING_RANGE` — 看誰還沒 ACK

```python
# 簡略版本：只回 summary
summary = redis.xpending("clicks:stream", "click-aggregator")
# {"pending": 12, "min": "...-0", "max": "...-7", "consumers": [...]}

# 詳細版本：列出每一筆
pending = redis.xpending_range("clicks:stream", "click-aggregator",
                                min="-", max="+", count=500)
# [{"message_id": "...", "consumer": "worker-1", "time_since_delivered": 70000, "times_delivered": 1}, ...]
```

**專案用法**
- `app/services/click_stream.py:105` worker 啟動時掃 PEL，看有沒有閒置太久（前一個 worker 死掉留下的）需要接手
- `app/worker.py:190` observability，用來算 `qr_click_stream_lag` metric

**陷阱**
- `xpending_range` 的 `count` 太小，大量 pending 時要分頁
- `time_since_delivered` 單位是毫秒

---

### 4.6 `XCLAIM` — 把訊息搶過來

```python
claimed = redis.xclaim(
    "clicks:stream", "click-aggregator", "worker-1",
    min_idle_time=60000,           # 至少閒置 60 秒才搶
    message_ids=["1715692200000-0", "1715692200000-1"],
)
```

**專案用法**
- `app/services/click_stream.py:119` worker 啟動時把「閒置超過 60 秒的 pending entries」接到自己名下繼續處理

**`min_idle_time` 為什麼重要**
- 同一個 group 內，consumer A 正在處理某筆訊息但還沒 ACK，是「合法的 in-flight」
- 如果 consumer B 馬上搶走，就會 double-process
- 用 `min_idle_time=60000` 等於說「你 60 秒還沒 ACK，我認定你死了」

**陷阱**
- `min_idle_time` 太短 → race，慢的 consumer 被誤判
- 太長 → consumer 真的死掉後恢復太慢

---

### 4.7 `XLEN` — stream 有幾筆

```python
total = redis.xlen("clicks:stream")
```

**專案用法**
- `app/worker.py:189` 給 `qr_click_stream_lag` metric 用

**陷阱**
- `XLEN` 是「目前 stream 裡所有訊息」**包含已 ACK 的**（直到 MAXLEN trim 掉）。要算 lag 通常還要扣 group 已 deliver 的，看你怎麼定義 lag

---

## 5. 維運 / Debug 指令（CLI 用）

跑壓測時你會在 `docker compose exec redis redis-cli` 裡敲這些：

| 指令 | 用途 | 範例 |
|---|---|---|
| `PING` | 健康檢查 | `PING` → `PONG`（compose healthcheck 在用） |
| `INFO stats` | 看 ops/sec、connection、command stats | `INFO stats \| grep instantaneous_ops_per_sec` |
| `MONITOR` | 即時看每個指令（debug 用，**production 不要開**） | `MONITOR`（會把整個 redis ops dump 到螢幕） |
| `KEYS qr:url:*` | 列符合 pattern 的 key（**production 不要用**，O(N) 掃全庫） | 改用 `SCAN` |
| `SCAN 0 MATCH qr:url:*` | cursor-based 版本，可在 production 用 | 必須迴圈到 cursor=0 |
| `TYPE <key>` | 看 key 是哪種資料結構 | `TYPE clicks:stream` → `stream` |
| `TTL <key>` | 看 key 還剩幾秒過期；-1 永久、-2 不存在 | `TTL qr:url:abc123` |
| `HGETALL qr:clicks:2026-05-14-15` | 看某小時的點擊累積（小 hash 才用） | 大 hash 用 `HSCAN` |
| `XINFO STREAM clicks:stream` | 看 stream metadata、groups | 壓測除錯首選 |
| `XINFO GROUPS clicks:stream` | 看所有 consumer group 狀態、pending 數 | 壓測除錯首選 |
| `FLUSHDB` | 清空當前 db（**只在 dev / 測試用**） | 壓測情境切換時用 |

---

## 6. 設計模式整理（這個專案怎麼把指令組起來）

### 模式 A：Cache-aside（URL / image bytes）

```
GET → 命中 → 用
       ↓ miss
       讀 DB / 生成
       ↓
       SETEX 回填 cache
       ↓
       回應
```

對應檔案：`app/services/qr_service.py`、`app/services/image_service.py`

### 模式 B：Producer / Consumer with at-least-once（click pipeline）

```
[producer] XADD clicks:stream            ← redirect handler
                ↓
[stream]   clicks:stream (MAXLEN ~ 100k)
                ↓
[consumer] XREADGROUP > COUNT 500
                ↓
           SET dedupe:{id} NX EX 3600   ← idempotency
                ↓
           HINCRBY qr:clicks:{hour} ... ← in-memory batch then flush
                ↓
           XACK                          ← 確認，不會被 XCLAIM 搶
                ↓
[recovery] worker 啟動時 XPENDING_RANGE → XCLAIM idle>60s 接手
```

對應檔案：`app/services/click_stream.py`、`app/worker.py`

### 模式 C：Atomic snapshot via RENAME（hourly flush）

```
EXISTS qr:clicks:{prev_hour}:flushing
  YES → resume (crash 前一輪沒做完)
  NO  → EXISTS qr:clicks:{prev_hour}
         YES → RENAME 到 :flushing
         NO  → 無事可做
         ↓
HSCAN :flushing 分批 → INSERT...ON CONFLICT 進 PG
         ↓
DEL :flushing
```

對應檔案：`app/jobs/flush_clicks.py`

---

## 7. 面試常見問題 cheat sheet

| 問題 | 你該講 |
|---|---|
| 為什麼用 Redis Streams 而不是 Pub/Sub？ | Pub/Sub 是 fire-and-forget，訂閱者離線時訊息會掉。Streams 持久化 + consumer group + ACK，**at-least-once**。 |
| 為什麼用 Streams 不用 Kafka？ | 量級沒到（每秒幾千），現成的 Redis 已經在用；Kafka 加一個 broker 等於多一個 SPOF + 維運成本。如果未來要分區 partition 或 retention 是天，再考慮換 Kafka。 |
| at-least-once 怎麼變 exactly-once？ | Stream 本身保證 at-least-once（XCLAIM 會重派）。靠 consumer 端做 idempotency——這個專案用 `SET dedupe:{entry_id} NX EX` 擋重做。 |
| 為什麼 cache invalidate 用 DEL 而不是 SETEX 新值？ | DEL 之後下次讀會強制從 DB 重撈，**保證讀到的是最新版**。SETEX 把舊值直接覆寫的話，race condition 下可能寫到 stale 值（兩個 client 同時 update）。 |
| Hash vs 多個 String key？ | 同 namespace 下多個 counter 用 hash 省記憶體（小 hash 有 ziplist 壓縮），刪除整個 namespace 也只要一個 DEL；分散在 String key 上每個都吃 overhead。 |
| 為什麼 flush 要先 RENAME？ | 把 source 切成快照，flush job 慢慢吃不會跟正在寫入的 worker 打架。RENAME 是原子操作。 |
| Redis 單執行緒怎麼撐高 QPS？ | 1) 單執行緒 = 命令間無 lock contention；2) 大部分指令是 O(1)/O(log N)；3) IO 多工（epoll）。瓶頸通常在網路而不是 CPU。 |
| 用了 Redis 之後怎麼保證資料不掉？ | 我們不靠 Redis 做 source of truth——它是 cache + queue buffer。真正的資料還是在 Postgres。Redis 掛了流量會劣化但不會掉資料（hash 還沒 flush 的會掉，是 trade-off）。 |

---

## 8. 延伸閱讀（按重要性）

1. [Redis Streams Tutorial（官方）](https://redis.io/docs/latest/develop/data-types/streams-tutorial/) — Stream 一定要看完，consumer group / PEL / XCLAIM 的圖很清楚
2. [Redis cluster spec](https://redis.io/docs/latest/operate/oss_and_stack/reference/cluster-spec/) — 如果未來真要 scale 出單機
3. ADR-0001 [docs/decisions/0001-redis-streams-for-click-pipeline.md](decisions/0001-redis-streams-for-click-pipeline.md) — 這個專案為什麼選 Streams
4. [redis-py docs](https://redis.readthedocs.io/) — Python client 的方法簽名速查
