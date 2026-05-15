# ADR-0003: 從 MVP 移除點擊計數，聚焦 redirect

- **Status**: Accepted
- **Date**: 2026-05-15
- **Supersedes**: [ADR-0001 — Redis Streams for click pipeline](0001-redis-streams-for-click-pipeline.md)
- **Related**: [ADR-0002 — OpenTelemetry with dual exporter](0002-otel-with-dual-exporter.md)（保留）

---

## Context

Sprint A 把點擊計數從同步 HINCRBY 拆成 Redis Streams + 獨立 worker pipeline（見 ADR-0001）。Sprint B 又補上 OTel + Prometheus 全套觀測性。執行至 Sprint C 後做了 baseline vs current 壓測，得出三個結論：

1. **Worker 拆分本身對 redirect 路徑 throughput 影響極小**（XADD vs HINCRBY 差 0.2 ms）。
2. **真正的瓶頸在 redirect 路徑本身**——event loop、DB pool、CPU-bound 圖片生成。
3. **點擊計數的「分析儲存」不該跟 redirect 共用 Postgres**——應該完全獨立的 analytics tier（BigQuery / ClickHouse），這個專案範圍內不會做。

既然 analytics tier 不實作，**保留 click_count 欄位 + qr_click_stats 表 + worker 程序就只是 dead weight**：增加維運表面、混淆架構敘事、讓「這個專案在做什麼」變模糊。

## Decision

**從程式碼移除整套點擊計數 pipeline，把 MVP focus 收斂在「URL shortener + QR image」這條 redirect 路徑。**

具體刪除：

| 元件 | 動作 |
| --- | --- |
| `app/services/click_stream.py` | 整檔刪 |
| `app/worker.py` | 整檔刪 |
| `app/jobs/flush_clicks.py` + `app/jobs/__init__.py` | 整目錄刪 |
| `app/routers/internal.py`（只有 flush trigger）| 整檔刪 |
| `app/main.py:_record_click()` 與其呼叫點 | 移除 |
| `app/models.py:QRClickStat` 類別、`QRCode.click_count`、`QRCode.last_clicked_at` | 移除 |
| `app/schemas.py:click_count` 欄位 | 移除 |
| `app/metrics.py` 的 `qr_click_stream_*` metrics | 移除 |
| `docker-compose.yml` 的 worker service | 移除 |
| `tests/test_click_stream.py` | 整檔刪 |
| 其他 test 內 click 相關斷言 | 移除 |

新增：

- Alembic migration `0003_remove_click_counting.py` — drop `qr_codes.click_count`、`qr_codes.last_clicked_at`、`qr_click_stats` table。

保留：

- ADR-0001、Sprint A spec / contract / QA report — **歷史紀錄不刪**，但本 ADR 標記為 supersedes
- Sprint B observability（OTel + Prometheus + structlog）— 仍然對 redirect 路徑有價值
- 整個 Sprint C 壓測腳本 + perf report — redirect 數字仍然有效

## Consequences

### 正向

- **Codebase 縮小** ~800 LOC，認知負擔降低。
- **架構敘事清楚**：「URL shortener / QR image generator with cache + observability」一句話講完。
- **Migration 簡化**：production schema 變單表（`qr_codes`），CRUD lifecycle 直觀。
- **Redirect 路徑變更輕**：少一個 `XADD`，少一個 metric increment。
- **Operational surface 縮小**：不用維運 worker container、不用 cron 設定、不用監控 stream lag。
- **Click counting 真正要做時，可以走更現代的設計**：Cloud CDN edge logs → Pub/Sub → BigQuery，而不是繼承這個半成品實作。

### 負向

- **失去 Sprint A 的 demo material**：「我拆過 microservice」這個面試敘事點要改用 Sprint B observability + Sprint C performance comparison 撐。
- **API contract breaking change**：`GET /v1/qr_code/{token}` 跟 `GET /v1/qr_codes` 不再回傳 `click_count`。是 MVP 階段所以可接受。
- **歷史 Sprint A 文件變成 stale**：保留作為決策過程的紀錄，但讀者要看到本 ADR 才知道實作已撤回。

### 中性

- 觀測性（Sprint B）跟壓測（Sprint C）仍然 100 % 適用 redirect 路徑——這兩個 sprint 的價值不依賴 click counting。
- 三架構壓測報告（[`docs/perf-report.md`](../perf-report.md)）量到的 RPS / p99 都是 redirect path 的數字，移除 click counting 不影響其有效性。

## Alternatives considered

### A. 保留欄位 + 表，只停 worker
- **拒絕原因**：dead column + dead table 比沒有更糟，會誤導讀程式碼的人以為功能還在。

### B. 把 click_count 移到 Redis（簡化版）
- **拒絕原因**：仍然要解 hourly persist 問題，沒解決根本耦合。如果要做就應該做完整的 analytics tier。

### C. 整個搬到 BigQuery / ClickHouse
- **拒絕原因**：**不在這個 portfolio project 的 scope**。要做的話成本（GCP 帳單 + 時間）跟複雜度都不划算；對面試展示的邊際價值不高。

### D. 不動，讓它躺著
- **拒絕原因**：架構敘事不清，code review 時讀者會困惑「為什麼有 worker 但 click_count 沒人用」。
