# ADR-0001: 用 Redis Streams 作為 click pipeline 的訊息層

- **Date**: 2026-05-14
- **Status**: Accepted

## Context
Sprint A 要把 redirect-time 的同步 `HINCRBY` 拆成 producer / consumer 模型，需要選一個訊息傳遞機制。候選方案：
1. Redis Streams (`XADD` / `XREADGROUP`)
2. Redis Pub/Sub
3. Kafka / Redpanda
4. Google Cloud Pub/Sub
5. RabbitMQ

選型限制：
- 既有 infra 已經有 Memorystore for Redis（redirect URL cache / click counter / image cache 都跑在上面）
- 部署目標是 Cloud Run + 單一 Memorystore + Cloud SQL，新元件越少越好
- 面試敘事重點是「展示 producer/consumer/consumer group/pending entries 的理解」，不是「能跑 Kafka」
- click 流量量級小（單 instance 預期 < 100 QPS redirect），不需要 Kafka 等級 throughput
- 需要 at-least-once delivery + 可被重派的能力（worker crash 時不能掉 click）

## Decision
採用 **Redis Streams** 作為 click event 的訊息層，搭配 consumer group `click-aggregator`。

關鍵設計：
- Stream key: `clicks:stream`，`MAXLEN ~ 100000` 防無限長
- Consumer group: `click-aggregator`，每個 worker process = 一個 consumer
- Worker 用 `XREADGROUP` 拉、`XACK` 確認；crash recovery 用 `XPENDING` + `XCLAIM`
- Idempotency 用 `SET qr:clicks:dedupe:{entry_id} 1 EX 3600 NX` 守門（防 XCLAIM 重派造成重複累加）
- Worker 仍把累加結果寫進現有的 `qr:clicks:{hour}` hash，既有 hourly flush job 完全不動

## Consequences

### Positive
- 零新 infra：沿用 Memorystore，沒有額外 broker 要部署 / 維護 / 付費
- 原生支援 consumer group、pending entries、XCLAIM，符合面試要展現的 at-least-once + crash recovery 敘事
- 跟既有 `qr:clicks:{hour}` hash + flush job 對接乾淨，schema 不動
- Worker 可以單獨水平擴展（同 group 多 consumer 自動 sharding）

### Negative / Trade-off
- Memorystore 變成更核心的 SPOF。若 Redis 掛了，redirect 不只 cache miss、連 click event 也會掉（mitigation：redirect handler 要把 XADD 失敗 swallow 成 warning，不影響 302 回應 — 列為 Sprint A 實作注意事項）
- Redis Streams 沒有 Kafka 等級的 partition / replay 工具；如果未來 click 流量上到 Kafka 等級，這層會是瓶頸並需要替換（風險可接受，當前量級遠未到）
- Idempotency 用獨立 dedupe key 而非 Redis 內建去重，多一次 round-trip（可接受）

### 中性
- 同一 Memorystore 既當 cache 又當 queue，需要設定合適的 `maxmemory-policy`。Stream / hash 是 critical state，不能跟 cache 一起被 evict。實作時：
  - 維持 `allkeys-lru`（cache 多）但保留 `MAXLEN` 控制 stream 大小
  - 或長期改成 `volatile-lru` 並只對 cache key 設 TTL — 列入後續評估

## Alternatives considered

### Redis Pub/Sub
- 拒因：fire-and-forget，沒有 ack、沒有 consumer group、沒有 replay。worker 重啟時的訊息就掉了，不符合「至少一次」需求。

### Kafka / Redpanda
- 拒因：新增一整套 broker（broker / zookeeper or kraft / schema），運維與成本顯著上升。當前流量量級用不到。面試展示「我能拆 producer/consumer」用 Streams 已經夠用，必要時可在 ADR / README 註明「規模上到 X 時應遷移 Kafka」。

### Google Cloud Pub/Sub
- 拒因：unordered by default、at-least-once，能力符合，但要新建 topic / subscription / 跨服務 IAM，且本地 docker-compose dev loop 變複雜（需 emulator）。對 sprint 範圍而言 overkill。
- 可保留為未來 production-grade 升級選項。

### RabbitMQ
- 拒因：跟 Kafka 一樣多一個 broker 要養，且既有 stack 沒有它。沒有理由為了 click pipeline 引入。
