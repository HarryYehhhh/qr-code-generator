# Architecture

> 本文件描述當前架構與目標架構。為什麼長這樣的決策過程見 `docs/decisions/`。

---

## TL;DR

**這個專案 focus 在 QR Code generator 的 redirect 路徑：把短 token 換回 URL、發 302、產生 QR PNG。** 點擊計數 / analytics 已從 MVP 移除（見 [ADR-0003](decisions/0003-remove-click-counting-mvp.md)），但架構文件仍然保留 analytics tier 的「目標形狀」，描述未來如果要加應該怎麼接。

---

## Current Architecture（已實作）

```
                            ┌─────────────┐
                            │   Client    │
                            └──────┬──────┘
                                   │ HTTPS
                                   ▼
                            ┌─────────────┐
                            │ Cloud Run   │     ◄── auto-scaling instances
                            │ (FastAPI)   │     ◄── stdout JSON → Cloud Logging
                            │             │     ◄── built-in metrics (Cloud Run console)
                            └──┬───────┬──┘
                               │       │
                  cache hit ◄──┘       └──► cache miss
                               │       │
                               ▼       ▼
                       ┌──────────┐  ┌────────────┐
                       │  Redis   │  │ PostgreSQL │
                       │ (cache + │  │ (qr_codes) │
                       │  PNG)    │  │            │
                       └──────────┘  └────────────┘
                            ▲
                            │ image bytes
                            │ (TTL 7d)
                            └────── /v1/qr_code_image
```

### 元件職責

| 元件 | 職責 | 位置 |
| --- | --- | --- |
| FastAPI app | redirect handler、QR CRUD、image generation | `app/main.py`、`app/routers/qr.py` |
| Redis URL cache | `qr:url:{token}` → URL，cache hit 時直接回 302（99 % 的請求） | `app/services/qr_service.py` |
| Redis PNG cache | `qr:img:{spec_hash}:{url_hash16}` → PNG bytes，TTL 7d | `app/services/image_service.py` |
| PostgreSQL | `qr_codes` 表（URL + status + lifecycle）。Cache miss 才查 | `app/models.py`、`app/database.py` |
| Observability | structlog JSON 寫 stdout（Cloud Logging 自動接） | `app/logging.py` |

### Request flow（hot path：redirect）

```
GET /r/{token}
    │
    ▼
[Redis] GET qr:url:{token}
    ├─── HIT (99 %)  → 302 redirect → 結束
    └─── MISS (1 %)  → [Postgres] SELECT url FROM qr_codes WHERE qr_token = ?
                          ├─── found    → [Redis] SETEX qr:url:{token} url 3600 → 302
                          └─── not found → 404
```

實測效能（Sprint C，commit `1fcb25b`，Apple M4 Pro）：

| Scenario | Steady RPS | p50 | p99 | Bottleneck |
| --- | --- | --- | --- | --- |
| redirect_hot (cache hit) | ~1,900 | 56 ms | 461 ms | uvicorn event loop |
| redirect_cold (POST + GET) | ~580 (each) | 39 ms | 78 ms | DB pool（pinned 在 3） |
| image_mixed (50 % cache miss) | ~500 | 223 ms | 494 ms | `qrcode` lib CPU |

完整數據：[docs/perf-report.md](perf-report.md)、[docs/perf-baseline-comparison.md](perf-baseline-comparison.md)。

---

## Target Architecture（含未來擴展）

如果要把這個服務推到 production-grade 高流量，會長這樣：

```
                  ┌─────────────┐
                  │   Client    │
                  └──────┬──────┘
                         │
                         ▼
                  ┌─────────────┐
                  │  Cloud CDN  │  ◄── 快取 redirect (95 %+ offload)
                  └──────┬──────┘
                         │
                         ▼
                  ┌─────────────┐
              ┌───┤ API Server  │───────────────────────┐
              │   │ (Cloud Run) │                       │
              │   └──────┬──────┘                       │
              │          │                              │
              ▼          ▼                              ▼
        ┌──────────┐ ┌────────────┐            ┌──────────────┐
        │  Redis   │ │ PostgreSQL │            │ Message Queue│
        │ (cache)  │ │ (qr_codes) │            │ (Pub/Sub)    │
        └──────────┘ └────────────┘            └──────┬───────┘
                                                       │
                                                       ▼
                                               ┌──────────────┐
                                               │  Analytics   │
                                               │  (BigQuery)  │
                                               └──────────────┘
        ┌─────────────────── REDIRECT TIER ────────────────┐ ┌── ANALYTICS TIER (out of MVP scope) ──┐
```

### 兩個 tier 的設計理由

| Tier | Workload 特性 | 儲存選擇 |
| --- | --- | --- |
| **Redirect tier** | 高頻讀（被 Redis cache 擋下 99 %）、低頻寫（建 QR 才寫一次）、強一致性需求低 | Postgres（單表 `qr_codes`，欄位簡單）+ Redis（cache + PNG bytes） |
| **Analytics tier** | 高頻寫（每次 redirect 一筆事件）、批量讀（dashboard / report 才查）、可容忍短暫 lag | Pub/Sub（缓衝） + BigQuery（columnar、分析友善）|

把兩個 tier 放在同一個 Postgres 是常見的 anti-pattern——讀寫模式衝突、index 互相干擾、scaling 受限於最慢那個。

### 為什麼 MVP 不做 analytics tier

見 [ADR-0003](decisions/0003-remove-click-counting-mvp.md)。簡述：

1. 真正做 analytics tier 需要 Pub/Sub + BigQuery 兩個 GCP 服務（成本 + 複雜度）
2. 半成品（worker 寫回同 Postgres）混淆架構敘事
3. 對「面試展示一個有觀測性 / 壓測過的 URL shortener」的目標邊際價值低

歷史上這個 pipeline 在 Sprint A 實作過（Redis Streams + worker），ADR-0001 記錄了當時的設計，**Sprint A 的 spec/contract/QA report 全保留**作為「我做過 microservice 拆分」的決策紀錄。

### 真要 50,000 RPS 的話

詳見對話 thread。摘要：

| 階段 | 動作 |
| --- | --- |
| 0–2k RPS | 現況單 instance |
| 2k–10k RPS | Cloud Run `--max-instances=20` + `uvicorn --workers 4`（要先解 Cloud SQL Connector fork issue） |
| 10k–50k RPS | Memorystore 升 Standard / Cluster；Postgres `db-n1-standard-2` |
| 50k+ RPS | Cloud CDN offload 95 % cache hit；origin server 只處理 cache miss |

**重點**：50k RPS 是 CDN 跟水平擴展問題，不是單機調參能解決。

---

## Sprint 與架構演進對應

| Sprint | 架構意圖 | 目前狀態 |
| --- | --- | --- |
| Sprint A | 把 click counting 拆出 redirect 路徑（producer / consumer 模式） | **已 revert**（[ADR-0003](decisions/0003-remove-click-counting-mvp.md)）。歷史 spec/contract 保留 |
| Sprint B | 三本柱觀測性（OTel + Prometheus + structlog） | **部分 revert**（[ADR-0004](decisions/0004-simplify-observability-keep-structlog.md)）。OTel + Prometheus 移除，**structlog 保留**。歷史 spec/contract 保留 |
| Sprint C | k6 壓測 + 容量數據 | **active**，腳本仍然有效，量到的數字仍然正確 |
| Sprint D（未來） | Rate limiting / API gateway | 候選 |
| Sprint E（未來） | Resilience patterns（circuit breaker / retry） | 候選 |

---

## 程式碼導覽

```
app/
├── main.py              # FastAPI app, lifespan, redirect handler
├── routers/qr.py        # /v1/qr_code CRUD + /v1/qr_code_image
├── services/
│   ├── qr_service.py    # business logic (create / get / list / update / delete)
│   ├── image_service.py # QR PNG 生成 + Redis cache lookup
│   └── token_service.py # Base62 token 生成（SHA-256 + nonce + secret）
├── models.py            # SQLAlchemy: QRCode (single table)
├── schemas.py           # Pydantic request/response models
├── logging.py           # structlog JSON config (sole observability module post-ADR-0004)
├── database.py          # SQLAlchemy engine, Cloud SQL Connector switch
├── config.py            # Pydantic Settings, ENVIRONMENT switch
└── dependencies.py      # FastAPI Depends helpers (get_db, get_redis)

tests/                   # FastAPI TestClient + fakeredis + SQLite
docs/
├── architecture.md      # 本文件
├── decisions/           # ADRs（所有設計決策的不可變紀錄）
├── specs/               # Sprint specs（產品意圖）
├── contracts/           # Sprint contracts（工作契約）
├── qa-reports/          # Evaluator 產出的驗收報告
├── perf-report.md       # Sprint C 壓測結果
├── perf-baseline-comparison.md  # baseline vs current 對比
├── load-test-plan.md    # 壓測 SOP
├── incidents/           # Postmortem
└── CHANGELOG.md         # 各 sprint 時序紀錄

scripts/k6/              # k6 load test scripts (seed, hot, cold, image, stress)
docker-compose.yml       # local dev stack: api + postgres + redis (+ k6 in loadtest profile)
alembic/                 # DB migrations
```
