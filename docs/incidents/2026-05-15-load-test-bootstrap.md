# Postmortem — 第一次本地壓測 bootstrap 連環失敗

- **日期**：2026-05-15
- **影響範圍**：本地開發環境（docker-compose），無 production 影響
- **失敗階段**：Sprint C 交付後，第一次嘗試實跑 k6 壓測填數字
- **總卡關時間**：~ 30 分鐘（含 debug + 修工具腳本 + 修我自己的 sed 烏龍）
- **嚴重程度**：P3（dev 體驗問題，可被測試 / 文件補救）
- **作者**：Harry + Claude

---

## TL;DR

照著 `docs/load-test-plan.md` SOP 走第一輪壓測，**連續踩了 4 個本可避免的坑**：

1. **Postgres schema 沒建** — API 連得上 DB 但 `qr_codes` table 不存在，所有 POST 寫入炸 5xx
2. **k6 v0.50+ 的 console.log 格式改變** — 寫好的 shell pipe 解析不到 token，`tokens.json` 是空 array
3. **修 (2) 時用 batch sed 寫崩 7 個檔案** — 替換字串含 `|` 跟 sed delimiter 互相吃，原始片段被疊加 4 次
4. **修 (2) 時 JSDoc 註解內的 sed pattern `.*/` 把 `*/` 當成 JSDoc 結束符** — node `--check` 紅燈，最諷刺：完美呼應「不實跑就抓不到」的主題

四個都不算難解，但**全部都是「Sprint C 完成 → 第一次實跑」之間的 gap 造成的**——腳本通過 lint，但沒人真的執行過。

---

## Timeline

| 時間 | 事件 |
|---|---|
| `~T0` | `docker compose up -d` 起 stack，7 個 service healthy，`/metrics` 正常吐 |
| `T0+5m` | 跑 seed.js，`jq 'length' tokens.json` 回傳 `0`（預期 ~1000） |
| `T0+8m` | 第一次 debug：把 `2>/dev/null` 拿掉看 stderr，發現 `Request Failed: connection reset by peer` 一片 |
| `T0+12m` | 看 `docker compose logs api` → `psycopg2.errors.UndefinedTable: relation "qr_codes" does not exist` → **Bug #1 確認** |
| `T0+13m` | `docker compose exec api alembic upgrade head` → 修好，重跑 seed |
| `T0+15m` | tokens.json **還是 0**。API log 卻看到 `201 Created` 一堆 → token 有建出來，pipe 攔不到 |
| `T0+18m` | 用 `docker compose ... 2>&1 | grep -i token` 看 raw 輸出，發現 `level=info msg="TOKEN:xxx" source=console` → **Bug #2 確認** |
| `T0+20m` | 寫好正確的 sed pipe，準備用 batch sed 把 8 個檔案的舊 pipe 改成新版 |
| `T0+22m` | sed 跑完，發現所有檔案的對應行被疊加成 4 倍長 → **Bug #3 自爆** |
| `T0+28m` | 用 Edit tool 一個檔一個檔修乾淨，grep 驗證沒重複 |
| `T0+30m` | 給使用者正確的指令，bootstrap unblock |

---

## Bug #1 — Postgres schema 沒建

### 觀察到的症狀
- API container `Up (healthy)`
- `/metrics` 正常回應
- 所有 `POST /v1/qr_code` → `connection reset by peer` 或 `EOF`
- API log：`psycopg2.errors.UndefinedTable: relation "qr_codes" does not exist`

### 根本原因

`app/main.py` 的 lifespan 邏輯：

```python
if database_url.startswith("sqlite"):
    Base.metadata.create_all(bind=engine)  # SQLite only
# Postgres: schema 必須靠外部 alembic 跑
```

這是**故意的設計**：production 環境 app 通常沒有 DDL 權限，schema 演進必須走 alembic migration（被 CI/CD 控制）。線上流程是：

```
1. CI 跑 alembic upgrade head（via Cloud SQL Auth Proxy）
2. CI 部署 Cloud Run
```

**問題**：本地 docker-compose 跳過了「CI 在外面跑 alembic」這一層，但 lifespan 也沒有為本地開後門。第一次起 stack 的 Postgres 是空 DB，app 連得上，但任何寫操作就炸。

### 為什麼這麼容易踩

| 因素 | 影響 |
|---|---|
| **本機開發長期用 SQLite** | 從來都自動建表，沒人記得「Postgres 不一樣」 |
| **API 沒在 startup fail-fast** | 連 DB 成功但 table 不存在，要等第一個寫操作才爆，stack 看起來「健康」 |
| **CLAUDE.md 有寫但容易漏看** | 文件第 N 段的一句話，跨入「本地壓測」情境時注意力在別處 |
| **`docker-compose up -d --build` 沒有 hook 點** | 沒地方塞「先跑 migration」 |

### 解法

**短期（已套用）**：
```bash
docker compose exec api alembic upgrade head
```

**長期（後續優化）**：把 migration 加進 api service 的 startup command。Alembic 本身是 idempotent（檢查 `alembic_version` table，沒新 migration 就 noop），所以即使 production 不需要也能放著：

```yaml
# docker-compose.yml
api:
  command: >
    sh -c "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8080"
```

---

## Bug #2 — k6 v0.50+ console.log 格式變了

### 觀察到的症狀

- seed.js 真的有送 POST、API 真的回 201
- `tokens.json` 卻是 `[]`（空 array）
- 原本指令：
  ```bash
  k6 run /scripts/seed.js 2>/dev/null \
    | grep "^TOKEN:" | sed 's/^TOKEN://' | jq -s '.' \
    > tokens.json
  ```

### 根本原因（兩個合起來才壞）

#### A. k6 把 console.log 寫到 STDERR 不是 stdout

很多人直覺以為 `console.log` 像 Node.js 那樣寫 stdout。**k6 不是**——它把所有 log（含 `console.log`、warning、error）統一走 stderr，stdout 留給 summary report / `--out` 整合。

但指令前面有 `2>/dev/null` → **stderr 全被丟掉**。

#### B. k6 v0.50+ 把 console.log wrap 成 structured log

舊版 k6（v0.4x）`console.log("TOKEN:abc")` 印出來大概是 `TOKEN:abc` 純字串。**新版（v0.50+，我們用的 v0.54）** 印的是：

```
time="2026-05-15T..." level=info msg="TOKEN:abc" source=console
```

`TOKEN:` 不在行首了，被包進 `msg="..."` 裡面。`grep "^TOKEN:"` 的 `^` 是行首錨點 → 永遠匹配不到。

**只要 A 跟 B 任何一個成立，pipe 就壞掉。兩個同時成立，就完全沒救。**

### 解法

```bash
docker compose --profile loadtest run --rm k6 run /scripts/seed.js 2>&1 \
  | sed -nE 's/.*msg="TOKEN:([^"]+)".*/\1/p' \
  | jq -R . | jq -s . \
  > scripts/k6/tokens.json
```

逐項對應：

| 片段 | 作用 |
|---|---|
| `2>&1` | stderr 併到 stdout，不再被丟掉 |
| `sed -nE` | `-n` 只印 substitution 成功的行，`-E` 啟用 extended regex（不用跳脫括號） |
| `s/.*msg="TOKEN:([^"]+)".*/\1/p` | 從 `msg="TOKEN:xxx"` 抓 `xxx`，`[^"]+` 防止吃到後面引號 |
| `jq -R .` | 每行 raw 字串包成 JSON string |
| `jq -s .` | slurp 全部 string 成 JSON array |

### 為什麼當初寫錯

寫 `seed.js` 的 docstring 時，是按照「k6 console.log 是 raw stdout」的舊印象寫的——這個假設在 v0.4x 正確，現在不對。**沒有實跑就不會發現**。

Sprint C 故意把「腳本完備 + 數字後補」切開（spec 寫明 subagent 無法在 sandbox 內可靠跑 docker），導致**沒有任何一個環節驗證過 shell pipe 真的能解析 k6 輸出**。

`tests/test_k6_scripts.py` 是 syntax-only（用 node `--check` + grep export pattern），抓不到 pipe 邏輯錯誤。

---

## Bug #4 — 修 Bug #2 時把 JSDoc 的 sed pattern 寫成 `.*/`，node `--check` 失敗

寫 postmortem + guardrail test 時順手跑了一次 `pytest tests/test_k6_scripts.py`，發現 `test_node_syntax_check_when_available` 紅燈：

```
seed.js:13
 *     | sed -nE 's/.*msg="TOKEN:([^"]+)".*/\1/p' \
                                            ^
SyntaxError: Invalid or unexpected token
```

### 根本原因
sed pattern `s/.*msg="TOKEN:([^"]+)".*/\1/p` 裡的 **`.*/`** 在 JSDoc block comment（`/** ... */`）裡會被當成**註解結束符 `*/`**。Node 把後面所有東西當 JS 解析 → SyntaxError。

```js
/**
 * sed -nE 's/.*msg="TOKEN:([^"]+)".*/\1/p'
                                     ^^
                                     這個 */ 提前關了註解！
 */
```

### 解法
sed 用 `|` 當 delimiter（合法且常見的替代），完全避開 `/`：

```js
/**
 * sed -nE 's|.*msg="TOKEN:([^"]+)".*|\1|p'  ← 沒有任何 /，JSDoc 安全
 */
```

### 諷刺的地方
這條 bug 完美 mirror 主題：**寫腳本沒實跑 → lint pass → 真的執行才爆**。

`tests/test_k6_scripts.py:test_node_syntax_check_when_available` 早就在跑 node `--check`，可是 generator 寫 docstring 時沒跑這個 test，evaluator 也沒查到。**規則就算寫進文件，沒有 enforcement 點還是會漏**——這也是為什麼 §Action Items 第 5 條要把 smoke test 變成 generator 工作流的強制步驟。

---

## Bug #3 — 批次 sed 寫崩 7 個檔案（我自己造的）

### 觀察到的症狀

修 Bug #2 時想用一行 sed 把 8 個檔案的舊 pipe 全換掉，結果跑完發現每個檔案的對應行都變成這樣：

```
2>&1 | sed -nE 's/.*msg="TOKEN:([^"]+)".*/\1/p' | jq -R . | jq -s .|2>&1 | sed -nE 's/.*msg="TOKEN:([^"]+)".*/\1/p' | jq -R . | jq -s .|2>&1 | sed -nE 's/.*msg="TOKEN:([^"]+)".*/\1/p' | jq -R . | jq -s .|2>&1 | sed -nE 's/.*msg="TOKEN:([^"]+)".*/\1/p' | jq -R . | jq -s .
```

**原 pattern 被重複貼 4 次**。

### 根本原因

我下的指令大致長這樣：

```bash
sed -i.bak -E 's|2>/dev/null \| grep "\^TOKEN:" \| sed ...|2>&1 \| sed -nE ... \| jq -s .|g' file.md
```

- 用 `|` 當 sed s/// 的 **delimiter**
- 但 **replacement 字串裡也含 `|`**（shell pipe）
- 結果 sed parser 把 replacement 中間第一個 `|` 當成 delimiter 結束，後面 `s .` 被當成新 flag 解析，再加上 `g` flag 對結果反覆做事——導致 replacement 被重複插入

### 解法

退回 Edit tool 的 `replace_all=true`，**一個檔一個檔處理**：

```python
Edit(file_path, old_string="<corrupted long string>",
                new_string="<correct single one-liner>",
                replace_all=True)
```

7 個檔案、7 次 Edit call，全部乾淨修復。

### 教訓

**替換字串如果含有 regex metacharacter（`|`、`(`、`)`、`/`、`$` 等）或 shell metacharacter，永遠不要走 batch sed。** 用 Edit tool 的 `replace_all` 或寫 Python script 用 `str.replace()` 才安全。

---

## 三個 Bug 的共同根因

**「Sprint 完成」≠「實際能跑」**。Sprint C 的 deliverable 定義是「腳本 + compose 整合 + report schema」，acceptance criteria 沒有「實際把 stack 起來跑一遍 seed」。在 sandbox 限制下這是合理的範圍切割，但**沒有後續任何環節補回這個驗證**：

| 環節 | 應該抓到的 bug | 實際結果 |
|---|---|---|
| generator | Bug #1（schema）、Bug #2（k6 pipe） | 沒抓到——只 lint 沒實跑 |
| evaluator | 同上 | 沒抓到——只看 contract 條目打勾 |
| `tests/test_k6_scripts.py` | Bug #2（k6 pipe） | 抓不到——是 syntax-only test |
| CI（github actions） | Bug #1 | 抓不到——CI 用 SQLite |
| 第一次本地實跑 | 三個都會撞 | ✅ **全撞了** |

整套 harness 的 quality gate 全部在「靜態檢查 / 受控環境測試」，缺一個「real environment smoke test」的步驟。

---

## Action Items（已開單）

| # | Action | 對應 Bug | 狀態 |
|---|---|---|---|
| 1 | 把 `alembic upgrade head` 加進 api service startup command | #1 | TODO |
| 2 | 修正 seed.js + 所有 k6 doc 的 pipe 命令 | #2 | ✅ 已完成 |
| 3 | 把 k6 console.log 格式說明寫進 `docs/load-test-plan.md §7` troubleshooting | #2 | ✅ 已完成 |
| 4 | 寫這份 postmortem 並 link 到 CHANGELOG | all | ✅ 本文 |
| 5 | 更新 `~/.claude/agents/generator.md` 規則：「任何 shell pipeline 必須 smoke run 一次才能算完成」 | #2 #4 | ✅ 已完成 |
| 6 | 更新 `.claude/agents/infra.md` 規則：「container image 必須 pin 版本，禁止 `:latest`」 | previous incident | ⏸ 等使用者授權 agent 設定編輯 |
| 7 | 加 `tests/test_no_latest_images.py` 自動化擋未 pin 的 image | previous incident | ✅ 已完成 |
| 8 | 修 JSDoc 內 sed pattern：用 `\|` delimiter 取代 `/` 避免 `*/` 提前關閉註解 | #4 | ✅ 已完成 |
| 9 | 修 `test_compose_loadtest_profile.py` 的 k6 image 斷言（原本寫死期待 `:latest`，違反新規則） | #4 副作用 | ✅ 已完成 |
| 10 | 加 `tests/test_db_schema_bootstrap.py` 起個 fresh container 確認 alembic 能跑成功 | #1 | 候選 |

---

## 給未來自己的提醒

1. **「lint 通過」不等於「能執行」**——shell pipeline 特別容易死在 quoting、escaping、tool 版本差異。Smoke test 是必要的，哪怕只是「執行成功，輸出存在」這種最弱驗證。
2. **`:latest` 是定時炸彈**——上次（前一個工作 session）`jaegertracing/all-in-one:latest` 被 Jaeger v2 切版淘汰、這次再次提醒了同樣的事。所有 image 都要 pin 明確版本。
3. **批次 sed 是危險工具**——替換字串只要含 regex/shell metacharacter 就有可能炸。Edit tool 雖然慢但安全。
4. **第一次跑壓測前，先 smoke 一遍**——`curl localhost:8000/v1/qr_code -X POST ...` 確認 API 真的能 INSERT，再跑 k6。30 秒省 20 分鐘。
