# BidOS 交接手册（2026-10-10）

> **给接手的人 / Agent**：先读 §0 四条铁律和 §1 三十秒上手，再按 §3 代码地图跳到你负责的那块。
>要动桌面 Excel 或排查「改了表系统没变」，直接跳 **§9bis 双向联动机制**。
> 本文所有路径、行号、端口、表名、数字都是 **2026-10-10 15:41 实测核对过的**，不是历史报告的转述。
> 历史报告（`docs/`、`deliverables/` 里的 md）里的数字**不能当作现状**，它们记录的是当时那一刻。

---

## §0 四条铁律（违反其中任何一条，做的都是白工）

| # | 铁律 | 违反后果 |
|---|---|---|
| **1** | **改完 `tender_details.py` 必须先重启常驻 API，再回填**：`launchctl kickstart -k "gui/$(id -u)/com.bidos.api"` | `com.bidos.api` 是 launchd 常驻进程，内存里是**旧解析器**。`BIDOS_SCAN_CRON=*/30` 一到点就用旧代码重写 `tender_detail`，把你刚回填的结果静默降级回旧版本。现象极隐蔽：写入时间戳逐条递增但 `version` 不变。 |
| **2** | **桌面 Excel 是唯一正本**，`data/display_snapshots/*.json.gz` 只是可重建的读取缓存 | 拿缓存当正本改，回填一次就没了；拿副本当正本写，会出现两个事实源 |
| **3** | **不把历史报告当现状；缺接口就说阻塞，不用模拟冒充成功** | 本项目已经因为 SPA 渠道正文抓不到而反复返工（见 §7.1） |
| **4** | **改桌面 Excel 之前先关掉 Excel / WPS** | 正本被占用时 `workbook_is_open()` 会让整轮同步返回 `busy`，你在这段时间改的内容系统读不到（不会丢，关闭后下轮自动补，但最长滞后 60 秒）。详见 §9bis |

> **「Excel ↔ 系统双向联动」的一句话结论（2026-10-10 15:41 实测）**：
> **双向都通、且完全自动** —— 改表 ≤60 秒进系统，系统改 ≤70 秒回写 Excel，**不需要手动点任何同步按钮**。
> 完整机制、延迟量级、三道闸、乐观锁与排障速查见 **§9bis**。

---

## §1 三十秒上手

```bash
# 项目根（一律从这里进，不要在别处跑脚本）
cd /Users/mini/workbuddy-ai/招投标开发/BidOS_Prototype_v0.3

# 1) 探活（两个都必须 200）
curl -s -o /dev/null -w 'api %{http_code}\n' http://127.0.0.1:8000/api/blind/workspace
curl -s -o /dev/null -w 'web %{http_code}\n' http://127.0.0.1:5173/blind

# 2) 打开页面
#    前端 http://127.0.0.1:5173/blind   （新信息 tab 深度链接：?tab=新信息&select=<project_id>）
#    接口 http://127.0.0.1:8000/api/automation/status  （自动化/调度真实状态）

# 3) 连库（OrbStack 容器 bidos-pg，宿主端口 55432）
psql "postgresql://bidos:bidos_dev_only@127.0.0.1:55432/bidos"
```

**当前运行态（2026-10-10 15:20 实测）**

| 项 | 值 |
|---|---|
| 主机 | macOS 26 / Apple M5 / arm64 |
| 后端 | FastAPI + uvicorn，`http://127.0.0.1:8000`，launchd `com.bidos.api` |
| 前端 | React 19 + Vite 7，**dev server** `http://127.0.0.1:5173`，launchd `com.bidos.web` |
| 改前端 | 直接改 `apps/web/src/**` 即热更新，**不需要 build**（`apps/web/dist/` 是旧产物，不被服务） |
| 数据库 | PostgreSQL，OrbStack/Docker 容器 `bidos-pg`，宿主 `127.0.0.1:55432`，库 `bidos` |
| Python | `.venv311` → Python 3.11.16（**不是**系统 python） |
| Node | `/opt/homebrew/bin/node` v26.8.1（vite 由 launchd 直接调 node 跑） |
| 调度 | `BIDOS_SCHEDULER_ENABLED=1`，TZ `Asia/Shanghai`，8 个 job（见 §6） |
| 版本控制 | **无 git 仓库**。改前自己备份，改坏只能靠 `deliverables/*/backups/` 回滚 |

---

## §2 目录地图：什么东西放哪

```
/Users/mini/workbuddy-ai/招投标开发/BidOS_Prototype_v0.3/
├── HANDOVER.md                  ← 本文件（根目录，最易发现）
├── README.md                    架构说明（v0.3），不是运维入口
├── start_bidos.sh               一键启动（API + web + 库）
├── 启动BidOS.command            双击启动（给非技术同事）
├── docker-compose.yml           OrbStack 容器定义
│
├── apps/
│   ├── api/app/                 FastAPI 后端（services/ 39 个模块，约 18.4k 行）
│   │   ├── main.py              61 个 HTTP 端点全部在这里（不在 routes/ 里）
│   │   ├── routes/workspace_hub.py  只有 6 个端点的独立路由（主链路不走它）
│   │   ├── services/            业务逻辑（见 §3）
│   │   ├── connectors/          官网接入：official_http / json_api / search_api / excel / signing
│   │   ├── repositories/        models.py（SQLAlchemy）、postgres_repository.py
│   │   ├── ai/                  AI provider：registry / openai_compatible / mock
│   │   ├── ingestion/pipeline.py 抓取→规范化→索引→审计
│   │   └── core/config.py       settings（环境变量）
│   ├── web/src/                 React 前端（pages/ 13 个页面 + services/ 数据层）
│   └── worker/app/              Worker 原型，**本机当前不靠它调度**
│
├── config/
│   ├── blind_channel_strategies.json   109 个盲投渠道的策略与实测状态 ← 渠道问题看这里
│   ├── sources.yaml                   5 条官网来源（3 条 verified=true / 2 条 false），含实测结论
│   ├── workbook_sources.json          桌面正本 Excel 的文件选择规则
│   └── cn_workdays.json               中国工作日历（**每年例行更新**，调休）
│
├── data/                        运行态，不是正本
│   ├── display_snapshots/{blind,workspace,portfolio}.json.gz   每小时快照（可重建）
│   ├── blind_workspace_state.json   盲投标记与锁
│   └── backups/ · portfolio_excel_backups/
│
├── scripts/                     34 个运维脚本 + writeback_columns.yaml（见 §3.3）
├── tests/backend/               32 个测试文件
├── docs/                        19 篇项目文档（部分初始说明已过时，实时入口以本文件为准）
├── deliverables/                历史交付物与报告，按日期目录（见 §5）
└── outputs/system-upgrade-20261008/   整理台账副本（**不是正本**）
```

---

## §3 代码地图：按「我要改什么」跳

### 3.1 后端核心链路（盲投信息这一条线）

```
config/sources.yaml  ─┐
config/blind_channel_strategies.json ─┤
                                      ↓
       services/blind_collection.py    ← 采集：从 109 个渠道抓公告列表
       BlindCollection.run()  L424     （_public_listing L201 / _json_listing L306 / _sdic_listing L393）
                    ↓  projects.metadata['landing']
       services/announcement_landing.py ← 落地：去重、建 source_pointers
                    ↓
       services/tender_details.py      ← ⭐ 字段抽取（1790 行，本项目最核心文件，见 §3.2）
       TenderDetailsService.refresh_missing() L1570   ← 定时补取公告正文
                                      ↓  projects.metadata['tender_detail']
       services/blind_workspace.py    ← ⭐ 状态机与 Excel 读写（705 行）
       BlindWorkspace.workspace() L589 / sync() L211 / expire_candidates() L178
                                      ↓
       services/display_snapshots.py   ← 每小时打包成 *.json.gz
                    ↓
       GET /api/blind/workspace  (main.py L1524, ?refresh=true 强制重建)
                    ↓
       apps/web/src/pages/BlindBidPage.tsx   ← 页面
```

**其余 service 一览（按行数，前 12）**

| 文件 | 行 | 干什么 |
|---|---:|---|
| `evidence_aggregator.py` | 1857 | 证据聚合（原公告定位、页码、行号） |
| **`tender_details.py`** | **1790** | **公告字段抽取（见 §3.2）** |
| `result_announcement_backfill.py` | 1368 | 中标结果公告回填 |
| `portfolio_workspace.py` | 1279 | 投标台账视图 |
| `event_store.py` | 1064 | 事件持久化 |
| `search_sql.py` | 1004 | 站内检索 |
| `event_scanner.py` | 904 | 事件扫描（到期/开标/结果） |
| `workspace_hub.py` | 789 | 工作记录 |
| `announcement_landing.py` | 781 | 公告落地去重 |
| **`blind_workspace.py`** | **705** | **盲投状态机（见上）** |
| `scheduler.py` | 693 | APScheduler 宿主 |
| `similarity.py` | 563 | 相似度 |
| `blind_collection.py` | 511 | 盲投采集 |

### 3.2 ⭐ `tender_details.py` 内部导航（接手字段问题的第一站）

模块 docstring L1–146 是**完整的版本变更史**，每一条都写清了「现象 → 根因 → 修法 → 实测影响」。
**改这个文件前必须先读 docstring**，否则很容易把前几轮修好的东西改回去。

| 行 | 锚点 | 说明 |
|---:|---|---|
| **147** | `VERSION = 12` | 解析器版本号。**任何规则改动都要 +1**，否则 `_store` 的同版本继承逻辑会保留旧值 |
| 484 | `notice_text(html)` | HTML → 正文提纯（块级标签才换行） |
| 561 | `record_text(record)` | 接口 JSON 记录 → 「字段：值」可读文本 |
| 601 | `public_amount(text, mapped)` | 金额/限价抽取 |
| 672 | `blocked_text(text)` | 判断是否被验证墙挡住 |
| 690 | `_plausible_region(value)` | 区域可信度闸 |
| 702 | `_plausible_location(value)` | 地点可信度闸 |
| **733** | `_region_from_org(name)` | **按甲方名称推行政区划**（V12 新增，推断值） |
| 812 | `_clean_value(value)` | 通用清洗 + 站点残留闸 |
| **1041** | `_file_fee(text)` | **报名费用**（行首标签行 → 同行售价句式 → 免费句式） |
| **1074** | `_method_value(text)` | **采购方式**（「采用…方式」句式，带否定词不采信） |
| **1120** | `_section_body(text, cuts)` | **无冒号章节正文**（项目情况简述；四道闸） |
| **1271** | `extract_details(...)` | **主抽取函数**，所有字段在这里汇总 |
| 1517 | `promotion_fields(detail)` | promote 进正本时的字段拼接 |
| **1570** | `refresh_missing(catalog, limit, offline, force)` | **定时补取**。`offline=True` 只用于 A/B 对照，正式回写必须走联网 |
| 1668 | `_ai_enrich(...)` | 模型整理钩子，**默认关闭** |
| 1706 | `_mirror(new, old)` | 镜像列更新口径（防止残留值在页面复活） |
| **1718** | `_store(pid, detail, body)` | **落库 + 继承 + 镜像**，字段丢失的多半出在这里 |

配套正则常量在文件头部（L110–L260）：`MONEY_RE` / `LABELS` / `LABEL_RE` / `SECTION_RE` /
`HEADING_RE` / `_MID_HEADING_RE` / `_FIELD_MAX` / `_ORG_NAME_RE` / `_METHOD_OK_RE` 等。

### 3.3 脚本（`scripts/`）—— 按用途挑

| 脚本 | 用途 |
|---|---|
| `desktop_api.py` | **launchd 的 API 入口**（等 OrbStack 就绪后拉起 API 与调度） |
| `backfill_tender_details_v5.py` | **公告字段回填**（常用）。`--force` 跳重试窗口，`--offline` 仅对照 |
| `compare_parser_versions.py` | **同正文 A/B**：`--measure v11\|v12` 把某版新增规则关掉当基线 |
| `compare_tender_details.py` | 库内现值 vs 离线重算（**有设计缺陷**，别当结论用，见 §7.3） |
| `audit_candidate_fields.py` / `qa_audit.py` / `qa_snapshot.py` | 体检类 |
| `excel_writeback.py` / `import_excel.py` | 正本 Excel 读写 |
| `run_scheduler.py` / `verify_scheduler.py` | 调度手动触发与核验 |
| `parse_award_notices.py` / `rebuild_own_results.py` / `result_announcement_backfill` 相关 | 中标结果链路 |
| `build_data_dictionary.py` / `check_architecture.py` | 文档生成 / 架构校验 |
| `nas_sync.py` | launchd `com.bidos.nassync` 的入口（StartInterval 60s） |

### 3.4 前端页面 ↔ 后端

| 路由 | 页面 | 主要数据接口 |
|---|---|---|
| `/` | `HomePage.tsx` | `/api/dashboard` |
| **`/blind`** | **`BlindBidPage.tsx`（+ `.css`）** | **`GET /api/blind/workspace`**、`POST /api/candidates/{id}/promote|dismiss`、`POST /api/blind/{id}/save` |
| `/results` | `ResultsPage.tsx` | `/api/results/own` |
| `/bid-bonds` | `BidBondsPage.tsx` | `/api/bid-bonds` |
| `/registration` `/evaluation` `/document` | 三页 | `/api/registrations|evaluations|documents` |
| `/competitors` | `CompetitorsPage.tsx` | `/api/competitors` |
| `/search` | `SearchPage.tsx` | `/api/search` |
| `/alerts` | `AlertsPage.tsx` | `/api/alerts`、`/api/events` |
| `/ops` | `OpsPage.tsx` | `/api/automation/status`、`/api/sources/health` |

前端数据层在 `apps/web/src/services/`：`apiProvider.ts`（请求）、`workspace.ts`（工作台数据）、
`detailFields.ts`（**通用显示层**：`displayValue` / `detailSections` / 折扣率格式化 / 「其他原表字段」兜底段）。
**盲投抽屉自己的字段分组、折叠阈值、`另 N 项未填写` 逻辑在 `BlindBidPage.tsx` L470/L477**
（`components/PortfolioUI.tsx` L81/L83 有一份镜像实现，改一处记得同步另一处）。

深链：`/blind?tab=新信息&select=<project_id>` 可直接打开某候选的详情抽屉（截图取证用这个）。

---

## §4 资料库在哪（要找历史结论/原始数据/知识沉淀，往这看）

| 资料 | 位置 | 说明 |
|---|---|---|
| **业务正本 Excel** | `/Users/mini/Desktop/AI小程序收集汇总资料/` | 5 份：`盲投项目信息 收集汇总表2026(3).xlsx`（含月表和 `2026年未参与` 历史子表）、`投标项目汇总9.17.xlsx`、`投标项目计划9.13.xlsx`、`（汇总）主要竞争对手 投标项目统计.xlsx`、`盲投渠道表及重点关注企业8.14.xlsx`（**账号密码在这份里**） |
| 正本选择规则 | `config/workbook_sources.json` | 改这个文件决定系统认哪几份表 |
| **共享记忆（SSOT）** | `/Users/mini/记忆系统/system/data/` | `memory.db`、`projects.json`、`knowledge.json`、`checkpoints/`、`backups/`。**当前事实只认这里**，通过 CLI `node /Users/mini/记忆系统/system/bin/mem.js context --task "<任务>" --json` 读 |
| Obsidian 知识库 | `/Users/mini/05-知识库-Obsidian`、`/Users/mini/Documents/Obsidian Vault` | 知识原文与索引 |
| 本项目工作日志 | `/Users/mini/WorkBuddy/2026-10-09-17-45-00/.workbuddy/memory/2026-10-10.md` | 逐轮工作记录（10 轮），搜这个最快知道「之前试过什么、为什么撤」 |
| 历史交付物 | 项目内 `deliverables/<日期>-<主题>/` | 每轮的报告、截图证据、A/B 明细。**看日期最新的** |
| 上一版交接包 | `deliverables/handoff-20261009/` | 含 `database.dump`（pg_dump custom）、`backups/`、`接手包索引.json` |
| 本轮（V12）交付 | `deliverables/blind-fields-fillrate-20261010/` | 字段填充 + 废话小字移除报告与截图 |
| 渠道覆盖报告 | `deliverables/blind-channel-coverage-20261009/` | 109 渠道逐个的实测结论，`build_strategies.py` 是生成脚本 |
| 项目文档 | 项目内 `docs/`（19 篇） | 架构、数据模型、API 契约、失败模式。**部分初始说明已过时**，冲突时以本文件为准 |
| 整理台账（副本） | `outputs/system-upgrade-20261008/招投标统一台账.xlsx` | **副本，不要往这里写** |

**查历史结论的建议顺序**：本文件 §7（踩坑）→ `docs/FAILURE_MODES.md` → 对应日期的 `deliverables/*/` → 工作日志。

---

## §5 数据模型与库表

`projects.metadata`（jsonb）是核心：**同一行里挂着 `landing`（落地信息）+ `tender_detail`（字段抽取结果）**。

| 表 | 行数 | 说明 |
|---|---:|---|
| `projects` | 2440 | 主表。`workflow_stage` 区分 `公告候选` / 后续阶段；`client_name` / `region` / `project_type` 是**镜像列**（由 `tender_details._mirror` 同步） |
| `source_objects` | 4826 | 抓取到的原始对象，含 `normalized_text`（**离线对照时的正文来源**） |
| `source_pointers` | 6422 | 证据定位：`source_url` + `original_text`（**回填时优先匹配的缓存正文**） |
| `project_results` | 1602 | 中标结果 |
| `entities` / `project_entities` / `entity_aliases` / `organizations` | 2064 / 3344 / 7 / 2 | 实体与别名 |
| `events` / `event_sources` | 680 / 182 | 到期、开标、结果提醒事件 |
| `bid_bond_ledger` | 269 | 保证金台账 |
| `audit_logs` | 3482 | 审计 |
| `competitor_stats` / `source_health` / `human_decisions` / `users` | 14 / 4 / 2 / 1 | |
| `bid_documents` / `document_chunks` / `feedback` / `project_evaluations` / `registrations` | 0 | 空表（功能已建，无数据） |

另有 **10 张 `_bak_` 备份表**（`projects_bak_20260928` 等），排查历史问题时有用。

---

## §6 定时任务（`GET /api/automation/status` 可查实时状态）

| job_id | cron | 干什么 |
|---|---|---|
| **`blind_daily_collection`** | `16:30` | **盲投日采集**（每天 16:30 收过去 24h 的新公告） |
| `display_hourly_snapshot` | 每小时整点 | 重建三份显示快照 |
| `blind_candidate_details` | 每 10 分钟 | 给候选补取公告正文 + 抽字段 |
| `blind_candidate_expiry` | 每 1 分钟 | 清理超 24h 未进待评估的新信息 |
| `result_backfill` | `*/10` | 中标结果回填 |
| `event_scan` | `*/30` | 到期/开标/结果事件扫描 |
| `key_project_monitor` | `*/30` | 重点关注项目监控 |
| `focus_competitor_public_results` | 每 10 分钟 | 竞对公开结果 |

**非工作日不采集**（认调休）：逻辑在 `blind_collection.skip_reason_for()` L91 +
`config/cn_workdays.json`。周一会补采「上周五 16:30 之后 → 本周一 16:30」整个窗口
（`resolve_window_start` L123 / `window_floor_start` L109）。
⚠️ **`cn_workdays.json` 每年例行更新**，调休安排变了必须先改它，否则采集窗口会算错。

### 6.1 盲投渠道现状（`config/blind_channel_strategies.json`，共 109 条）

| status | 条数 | 含义 |
|---|---:|---|
| `verified` | 48 | 实测抓到带日期公告且最新一条 ≤7 天 |
| `list_ok` | 23 | 能抓到列表，字段/日期口径未完全核验 |
| `manual` | 16 | 需人工处理（多为登录墙/验证码，**不绕过**） |
| `no_listing` | 11 | 公开页面提取不到带日期的公告列表 |
| `blocked` | 6 | 被反爬/验证拦截 |
| `render_only` | 4 | 只能渲染、拿不到数据（**当前是空跑**） |
| `unreachable` | 1 | 网络不可达 |

最近一次采集实测（`GET /api/automation/status` 的 `collection`）：
`catalogCount=109`、`total=111`（**有重复计数，见待办 #5**）、`usable=53`、`failed=58`、`newCount=59`。

### 6.2 前端拿到的数据结构

`GET /api/blind/workspace` 顶层 5 个键：
`records`（台账正本行）、`candidates`（新信息候选）、`automation`（调度状态）、
`rules`（口径规则）、`snapshot`（快照元信息）。
`?refresh=true` 会强制重建快照（约 16 秒）。候选对象里页面真正读的是
`client` / `agency` / `region` / `type` / `fields` / `detailStatus` / `detailMessage` / `aiAssist`。

---

## §7 已知故障模式与踩坑（最有价值的部分）

### 7.1 SPA 渠道抓不到正文 —— 未解决

`blind_collection._public_listing` 对 SPA 渠道（4 条 `sichuan_gov_procurement`、
3 条长城汽车 `notice_text`=0）的正文来源**至今没查清**。
`sources.yaml` 开头已写明：四川政府采购网首页是 Vue SPA，`http` 策略结构上抓不到，
真实数据源是**带签名的门户 JSON 接口**（`connectors/signing.py`）。
**把 SPA 站点配成 http 策略只会稳定产出 `parse_no_items` —— 那是策略选错，不是页面改版。**

### 7.2 同一 URL 两次抓取排版不同（踩了两次，已从根上缓解）

* 现象 A：四川站第二次抓取正文被**折成一行**（3556 字仅 2 个换行）→ 所有 `(?:^|\n)` 行首规则失效 → `服务期限` 87→181 字。
* 现象 B：四川政采把费用写成**三行**（`…获取招标文件` / `方式：在线获取` / `售价：0元`），联网正文保留换行而正则不跨行 → `报名费用` 静默读空；缓存正文折成一行反而能读。
* 已做：`_MID_HEADING_RE` / `_MID_SUBHEADING_RE` 兜中缝标题；`_FEE_LABEL_RE` 认行首标签行；
  **`bodyPick`**——两份正文都在手上时取**抽出字段更多**的那份（记录在 `detail.bodyPick`）。
* **仍然要做的**：改解析器后一定按铁律 1 重启 API，否则常驻进程会用旧代码覆盖。

### 7.3 `compare_tender_details.py` 的结果不能当结论

它拿「库内现值」当改前对照，但**库值来自联网正文、重算用缓存正文**，
差值里混着抓取抖动而不是解析器改动。正确做法是 `compare_parser_versions.py`——
**固定同一份正文、只切规则开关**。

### 7.4 正则回写的连带伤害

往 `_CONTACT_TAIL_RE` 加 `公\s*司\s*地\s*址` 会让 `有限公司 地址：` 整段匹配，
机构名被砍成 `…有限`（实测 6 条受害）。**代码里已留禁止注释**，改那里前先看注释。

### 7.5 验证码/登录墙不绕过（合规红线）

`blind_channel_strategies.json` 的 `rules.noBypass`：验证码/登录墙一律不绕过，交人工。
中国招标投标公共服务平台（网易易盾）`verified: false` 就是这个原因，不要试图绕过。

---

## §8 验证方法（别只看文档，测了才算）

```bash
cd /Users/mini/workbuddy-ai/招投标开发/BidOS_Prototype_v0.3

# 1) 测试套件（当前基线：14 failed / 2 errors / 564 passed —— 既有失败项，非新引入）
./.venv311/bin/python -m pytest tests -q

# 2) 同正文 A/B：量某一版规则的净效果（变长数 > 0 就说明有越界）
./.venv311/bin/python scripts/compare_parser_versions.py --measure v12

# 3) 联网强制回填
./.venv311/bin/python scripts/backfill_tender_details_v5.py --limit 200 --force

# 4) 重建显示快照（约 16 秒）
curl -s -o /tmp/ws.json "http://127.0.0.1:8000/api/blind/workspace?refresh=true"

# 5) 页面实证截图（headless Chrome）
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CHROME" --headless=new --disable-gpu --no-sandbox --hide-scrollbars \
  --window-size=1680,1500 --virtual-time-budget=28000 \
  --screenshot=/tmp/shot.png \
  "http://127.0.0.1:5173/blind?tab=%E6%96%B0%E4%BF%A1%E6%81%AF&select=<project_id>"
```

**判定标准（沿用这几条，别自创）**：变长 0 处（无越界）· 站点残留 0 处 ·
所有候选版本号一致 · 丢失项逐条人工核对确认是垃圾 · 测试台数字与基线一致。

---

## §9 业务口径（不可违反）

1. **桌面 Excel 是正本**；快照是缓存；`outputs/` 下的台账是副本。
2. **财年 = 上年 12 月 ~ 本年 11 月**（2026 财年 = 2025-12-01 ~ 2026-11-30）。
3. **跨源合并**：一个项目在多份表里的信息合并保留；**同名但不同年份/轮次不能随意合并**。
4. **AI 只整理，不替人决定**。是否参与、是否进待评估由人点。
5. 盲投五态：新信息 → 待评估 → 未报名 / 报名未参与 / 报名参与。
   **未进待评估的，首次收集 24h 后自动删除，不能归入未报名。**
6. 金额不混算（单位、总额/单价、服务年限）；只有可比金额才算中标价÷限价，折扣率保留 2 位小数。
7. 中标状态按项目汇总表的标记，不能只看公司名称（兄弟公司可能是资质主体）。
8. `config/sources.yaml` 里 `verified: false` 的来源**不得对外承诺已接入**
   （当前 5 条来源：3 条 verified=true，2 条 false）。
9. **细节字段基于原表实际表头**，不添加整库都没有的字段。

---

## §9bis ⭐ Excel ↔ 系统 双向联动机制（2026-10-10 15:41 全链路实测）

**结论：双向都通，且完全自动，不需要手动点任何同步按钮。**
唯一硬性前置条件：**改表前先关闭 Excel / WPS**（见下方闸门 G0）。

### 9bis.1 一个入口，两个方向

`BlindWorkspace.sync()`（`apps/api/app/services/blind_workspace.py:211`）是**唯一**的同步入口，一个方法里串起两个方向：

```
sync()
 ├─ 0) flock(/tmp/bidos_nas_sync.lock)      并发闸：抢不到锁直接返回 busy
 ├─ 1) workbook_path('*盲投项目信息*汇总表*.xlsx')   正本定位（config/workbook_sources.json 优先）
 ├─ 2) workbook_is_open(source)             ★闸门 G0：lsof -t 命中 → 返回 busy，本轮整个跳过
 ├─ 3) w.collect_db_pending(cfg, dsn)       从库里捞 status=pending 的写回请求   ← 方向 A 的准备
 ├─ 4) w.run_pass(...)                      ★写进 Excel（系统 → 源表）
 ├─ 5) self._read_master(source, w, cfg)   ★读 Excel 回灌数据库（源表 → 系统）    ← 方向 B
 ├─ 6) _read_own_ledger / _apply_summary_results / expire_candidates
 └─ 7) write_state()写 data/blind_workspace_state.json（含 rows / expired / lastSync）
```

**谁在调它**：launchd 常驻 `com.bidos.nassync`（`scripts/nas_sync.py`，`StartInterval 60`）**每 60 秒自动跑一轮**；
`POST /api/blind/sync` 与 `save()` 内部也会调。所以「改表 → 系统跟着变」不需要人工干预。

### 9bis.2 方向 A：系统改 → 源表对应位置改（writeback）

| 环节 | 位置 | 说明 |
|---|---|---|
| 写请求入库 | `save()` L580 | 追加到 `projects.metadata.system_edits[]`，`excel:{sheet,row,column,status:'pending'}`，带 `base_value` 快照 |
| 捞队列 | `excel_writeback.collect_db_pending:263` | `WHERE metadata ? 'system_edits'`，只取 `status='pending'` |
| 定位格位 | `SheetLayout.bidos_col / find_col` | BidOS 预留列按 `bidos_columns` 配置定位；普通业务列**按表头名精确匹配**（找不到就 failed，**绝不猜列**） |
| 落盘 | `run_pass` → 临时文件 + `preserve_formula_caches` + `os.replace` | 保住公式缓存值，避免公式列变空白 |
| 回执 | 写回后置 `status='applied'` + `written_at` | 这两个字段是判定「到底写没写成」的**唯一可靠凭据** |

**实测（15:37:42）**：项目 `bidos_proj_0ac8e75b6eb818b3`（成都分公司及海盐办公场地保洁服务，4月!行30），
调 `POST /api/blind/{id}/save` 改**业务列**`备注`（不是 BidOS 预留列）→ `written_at=15:38:40`，
Excel 单元格变成 `【系统写回测试-B】`，库里 `blind_fields.备注` 同步。
**结论：业务列和 BidOS 列都能写回，不只预留列。**

可改字段 = `blind_fields` 里除 `BidOS项目ID`、`BidOS回写时间` 外的任意字段（`save()` L570）。
`报名参与情况` 另有白名单校验（L572），只认 `待评估/未报名/报名未参与/报名参与`。

### 9bis.3 方向 B：源表改 → 系统跟着变（read）

`_read_master()`（L247）每次把 9 张月表逐行重读一遍，**共 1003 行**（2026-10-10 实测）：

1. `find_header_row` 按锚点定位表头（4月实测 `header_row=2`）、`last_data_row_of` 定末行；
2. 逐行建`fields` 字典（表头名 → 单元格值）；
3. 定位项目：**优先 `BidOS项目ID` 精确匹配**；没有就按（项目名 + 甲方名）唯一匹配；再退到「同名唯一」兜底；
4. 回写库：`blind_present=True`、`blind_fields=fields`、`blind_pointer={file,sheet,row}`；
5. **业务镜像列同步**（L336–340）：
   ```python
   p.name         = fields.get('项目名称') or p.name
   p.client_name  = fields.get('甲方名称') or None
   p.region       = fields.get('项目区域') or None
   p.project_type = fields.get('项目类型') or None
   p.workflow_stage = '盲投信息'
   ```
   → 所以**改 Excel 的项目区域/项目类型/甲方名称，库里的结构化列会跟着变**，不只是快照字典变。
6. L312–316：所有带 `blind_pointer` 的先置 `blind_present=False`，再按本轮读到的置回True
   —— 这样**表里删掉的行会自然变成「不在表内」**，不会留脏指针。

**实测（15:35:11 → 15:36:17，全程零人工干预，只改了 Excel 单元格）**：
`4月!B30 项目区域` 由 `【反向联动测试】` 改成 `【自动联动验证-B】` → 66 秒后 nassync 自动同步 →
库内 `projects.region` 与 `blind_fields.项目区域` 双双变成 `【自动联动验证-B】`。

### 9bis.4⚠️ 实测延迟（我本人踩过两次「以为没生效」的坑）

| 场景 | 延迟 | 排查要点 |
|---|---|---|
| 改 Excel → 系统更新 | **≤ 60 秒**（nassync 轮询间隔），实测 66 秒 | 看 `/tmp/bidos_nas_sync.out` 的 `lastSync` 是否晚于你的写入时间 |
| 系统改 → 写回 Excel | **约 70 秒**（`save()` 内两次 sync + nassync 兜底），实测 `at=15:37:28` → `written_at=15:38:40` | 看 `system_edits[].excel.status` 是否变`applied`、`written_at` 有值 |

> **判定纪律**：**不要用「刚写完立刻查 Excel」判断联动是否生效** —— writeback 一轮要十几秒，
> 我因为查得太早，连续两次误判成「没联动」。**先看 `excel.status` 和 `written_at`，再去看单元格。**

### 9bis.5 三道闸（任一命中就整轮跳过，数据不丢，下轮自动补）

| 闸 | 位置 | 触发条件 | 现象 |
|---|---|---|---|
| **G0 进程占用** | `workbook_is_open()` L76 | `lsof -t <正本>` 非空（Excel/WPS 打开着） | `sync()` 返回 `{"status":"busy","message":"盲投 Excel 正本正在打开，关闭后自动继续同步"}` |
| **G1 锁文件** | `gate_lockfile()` L314 | 同目录存在 `~$盲投项目信息 收集汇总表2026(3).xlsx` | 本轮跳过写回，请求保持 `pending` |
| **G2 备份** | `gate_backup()` | 每次落盘前留一份，保留 20 份 | 出问题从备份恢复 |

**实测 G0**：用 `tail -f` 占住正本 → `workbook_is_open` 从 `False` 变 `True`，
`POST /api/blind/sync` 立刻返回 `{"status":"busy","message":"盲投 Excel 正本正在打开，关闭后自动继续同步"}`；
释放后恢复 `False`。

> 📌 **给使用者的硬规则：改表前先关掉 Excel/WPS，保存完再打开。**
> 开着Excel 改表的这段时间，所有同步都是暂停的（不会丢，关闭后自动继续，但数据会有最长 60 秒的滞后）。

### 9bis.6 并发保护：乐观锁

`save()` L574：`baseValue`（前端带来的原值）与库内最新 `blind_fields[field]` 不一致 →
直接拒绝：**「「{field}」已在Excel更新，请刷新后再修改」**。
即**别人（或你在 Excel 里）改过同一格后，系统里的旧界面不能再覆盖写回**，必须先刷新。
这是防止「系统把表里的人工修改冲掉」的关键闸门，改写回逻辑时**绝对不能绕过**。

### 9bis.7 排障速查

| 现象 | 先查 | 常见原因 |
|---|---|---|
| 改了 Excel，系统没变 | `cat /tmp/bidos_nas_sync.out \| tail -6` | ① Excel 开着（G0）② 最后一轮 `status: error` ③ nassync 进程挂了：`launchctl print "gui/$(id -u)/com.bidos.nassync"` |
| 系统改了，Excel 没变 | 查 `system_edits[].excel.status` | `pending` = 没写出去（多半是 G0/G1）；`applied` 但单元格没变 = 列定位失败，看 `failed_items` |
| 页面报「已在Excel更新，请刷新后再修改」 | 不是故障 | 乐观锁正常工作，你手里的 `baseValue` 过期了 |
| 想立刻同步不等 60 秒 | `curl -X POST http://127.0.0.1:8000/api/blind/sync` | 手动触发，仍受三闸约束 |

---

## §10 待办（交接时仍未完成，别当成已做）

| # | 事项 | 状态 |
|---|---|---|
| 1 | `_public_listing` 对 SPA 渠道正文来源 | **未查清**（§7.1） |
| 2 | 16:30 准点执行核验 | 未做（只验证过 cron 配置） |
| 3 | promote 端到端验证 | 未做 |
| 4 | `blind_workspace_state.json` 里 19:38 旧连接失败残留 | 待清理 |
| 5 | 重复计数：`中国招标投标公共服务平台` 在 total 计两次（111 应为 110） | 未修 |
| 6 | 单渠道上限 60/240 是否合理 | 待拍板 |
| 7 | 23 个「接口只给 id」的渠道补 `apiCall.urlTemplate` | 未做 |
| 8 | 4 个 `render_only` 渠道引入 Playwright 常驻 worker | 未做（当前只渲染不出数据） |
| 9 | 「待发布」是否按政府采购意向公开口径、范围仅四川省级还是扩地市 | 待确认 |
| 10 | 折叠阈值是否改比例、`采购要求` 是否移除、`其他原表字段` 兜底段是否默认不展示 | 待拍板 |
| 11 | `config/cn_workdays.json` | **每年例行更新** |
| 12 | 字段真实缺失（非没抽到）：`所属行业` 3/52、`基本要求` 2/52、`投标保证金（元）`/`中标服务费（元）` 0/52 | 原文确实没写，留空是诚实的 |

---

## §11 快速排障

| 症状 | 先查 |
|---|---|
| 页面字段值不对 / 是站点残留文案 | `tender_details._looks_like_residue` 与 `_clean_value`；镜像列看 `_mirror`（L1706） |
| 回填完几分钟又变回去 | **铁律 1**，重启 `com.bidos.api` |
| 页面不更新 | 快照是每小时重建的；手动 `?refresh=true`；确认 API 已重启 |
| 某候选一直空白 | `GET /api/blind/workspace` 看该条 `detailStatus`；`需人工查看` = 被验证墙挡住 |
| 采集不到公告 | `config/blind_channel_strategies.json` 查该渠道 `status`；`GET /api/automation/status` 看 `collection.channels[].message` |
| 调度没跑 | `GET /api/automation/status` 看 `scheduler.running` 与 `jobs`；日志 `/tmp/bidos_api.log` |
| 库连不上 | OrbStack 是否在跑；`lsof -nP -iTCP:8000 -sTCP:LISTEN` 看 API 是否监听 |
| 环境变量 / 凭证问题 | `~/Library/LaunchAgents/com.bidos.*.plist`；**不要把凭证写进日志或报告** |
| **改了 Excel 系统没变 / 系统改了 Excel 没变** | **见 §9bis 双向联动机制**：先看 `/tmp/bidos_nas_sync.out`，再确认 Excel 是否开着（G0 闸），实测延迟 ≤60 秒 |

---

## §12 部署与交接（2026-10-10 新增）

### 12.1 交付物清单

| 文件 | 用途 |
|---|---|
| `install.sh` / `run_bidos.sh` | macOS 部署与进程管理 |
| `install.ps1` / `run_bidos.cmd` | Windows 部署与进程管理 |
| `启动BidOS.command` | macOS 双击启动器 |
| `同事部署指南.md` | macOS 同事看 |
| `Windows部署指南.md` | Windows 同事看 |
| `交接提示词.md` | 粘给同事的 WorkBuddy/AI 执行 |
| `data/bidos.dump` | 历史数据（pg_dump custom 格式，6.7MB） |
| `requirements.lock.txt` | macOS 依赖（79 条） |
| `requirements.windows.txt` | Windows 依赖（60 条，已滤掉 macOS 专有包） |

**一键部署**：解压后跑一次 `install.sh`（macOS）或 `install.ps1`（Windows）。
脚本做：查环境 → 建venv 装依赖 → 起 PostgreSQL → 恢复数据 → 注册开机自启 →
配防火墙 → 逐项验证。**幂等，重复执行不丢数据。**

### 12.2 ⚠️ 跨平台改造（Windows 同事必须知道）

原代码有 5 处 macOS 专属写法，Windows 上会直接失败，已全部改造：

| 位置 | 原写法（macOS 专属） | 现写法 |
|---|---|---|
| `app/services/file_lock.py`（新增） | `import fcntl` 散落在 6 个文件 | 统一封装：POSIX 用 fcntl，Windows 用 msvcrt |
| `services/blind_workspace.py:90` | `lsof -t` 判Excel 是否打开 | Windows 用 `CreateFileW` 试开判断 |
| `services/blind_workspace.py:23` | 正本目录写死 `/Volumes/...` | 按平台顺序探测多个候选路径 |
| `scripts/desktop_api.py:72` | `.venv311/bin/python` | Windows 用 `.venv311\Scripts\python.exe` |
| `scripts/desktop_api.py:20` | OrbStack + `/usr/bin/open` | Windows 用 Docker Desktop |
| `main.py:1661` | `subprocess.run(['open', ...])` | Windows 用 `os.startfile()` |

**验证结果**：macOS 侧 `pytest` 566 passed / 14 failed（与改造前基线**完全一致**，
零回归），服务重启后 api/web/局域网全 200、同步 1000+ 行正常。

**未验证部分**：Windows 脚本（`install.ps1` / `run_bidos.cmd`）经静态审查
（修掉 12 个会导致部署直接失败的问题，含中文 Windows 下 netstat 状态列是
「侦听」而非 `LISTENING`、`.cmd` 必须 CRLF+无 BOM 而 `.ps1` 必须 CRLF+BOM 等），
**但未在真实 Windows 机器上运行过**。

### 12.3 已知部署陷阱（macOS 实测踩过）

1. **launchd 僵死**：`launchctl bootstrap` 持续报 `error 5`，且 `bootout`
   也失败（domain 里有 `enabled` 残留记录但无服务实体）。此时 launchd 完全
   不可用。**对策**：`install.sh` 会自动降级为「后台守护进程」模式
   （`run_bidos.sh`），功能完全一样，只是关终端后需重新 start。
   开机自启另有登录项兜底（`autostart.sh`）。

2. **后台进程随脚本退出被回收**：直接 `cmd &` 启动的进程继承脚本进程组，
   调用方结束时内核向整个进程组发 SIGTERM（表现为脚本 exit 137、页面突然 502）。
   **对策**：`run_bidos.sh` 用 Python `os.setsid()` + 双 fork 脱离会话。

3. **孙进程 pid 对不上**：`spawn_detached` 返回的是中间 fork 进程 pid，
   真正服务是它的孙进程。用它做 `kill -0` 判断会误报「启动失败」。
   **对策**：启动成功后按**端口实际监听者**回读真实 pid。

4. **`set -u` 下未定义变量中止脚本**：macOS bash 下 `$BIDOS_WORKBOOK_DIR`
   未设置会直接退出。Windows 的 PowerShell 有同样问题。
   **对策**：所有可能未定义的环境变量都写 `${VAR:-}`。

---

*编制：2026-10-10 · 核对基准：`/api/automation/status`、display_snapshots、information_schema、实际文件遍历*
*§9bis 双向联动机制为 2026-10-10 15:41 实测补充：改 Excel / 改系统各做一次真实写入 + 等 nassync 自动同步验证，含三道闸与乐观锁复现*
*§12 部署与交接为 2026-10-10 17:00 补充：macOS 全流程实测跑通，Windows 完成代码层跨平台适配与脚本静态审查*