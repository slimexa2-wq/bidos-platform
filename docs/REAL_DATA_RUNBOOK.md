# BidOS 真实数据运行手册（REAL_DATA_RUNBOOK）

> 目标：让任何人**照着这份手册从零把真实 PostgreSQL 链路跑通**，而不是只在
> SQLite / 内存里自测。文中每条命令都在本机真实执行过，执行结果见
> [§7 实测记录](#7-实测记录)。
>
> 红线：**禁止把未验证的说成已验证。** 本手册区分「已实测通过」与「未完成/阻塞」，
> 不确定的地方一律写明。

---

## 0. 为什么需要这份手册

`BIDOS_DATA_MODE=postgres` 时，仓储层读的是真库；但真库链路有三类东西是
SQLite 内存库**永远测不出来**的：

1. PostgreSQL 专属类型与索引：`jsonb`、`vector(1024)`、`hnsw`、`gin_trgm_ops`；
2. 真实 Excel 的脏数据（合并单元格、裸日期序列号、金额文本、缺失表头）；
3. 应用装配层的类型错配（例如把 `sessionmaker` 当 `Engine` 传）。

本手册的价值就在于把这些一次跑出来。

---

## 1. 环境前提

| 项 | 要求 | 本机实测 |
|---|---|---|
| Python | 3.11+ | `/Users/mini/.workbuddy-ai/binaries/python/envs/bidos/bin/python`（3.13.12） |
| Python 依赖 | sqlalchemy / psycopg[binary] / openpyxl / pyyaml / fastapi / httpx | 已装 |
| Docker | Docker CLI + 可用 daemon | `/usr/local/bin/docker` 29.4.0，daemon = OrbStack |
| PG 镜像 | `pgvector/pgvector:pg16`（**必须带 pgvector**） | 已拉取 |
| 真实 Excel | 只读 | `/Users/mini/Desktop/市场共享文件/AI小程序收集汇总资料/` |

> **为什么必须用 pgvector 镜像**：`002_schema.sql` 里 `document_chunks.embedding`
> 是 `vector(1024)` 且有 `hnsw` 索引，`001_extensions.sql` 要 `CREATE EXTENSION vector`。
> Homebrew 的 `postgresql@16` **默认不带 pgvector**（本机实测只有 `pg_trgm`），
> 直接用它会在 001 就失败。

---

## 2. 一条命令跑通（推荐）

```bash
cd /Users/mini/workbuddy-ai/招投标开发/BidOS_Prototype_v0.3
PY=/Users/mini/.workbuddy-ai/binaries/python/envs/bidos/bin/python

$PY scripts/seed_and_verify.py --report /tmp/bidos_e2e_report.json
```

`seed_and_verify.py` 会自动完成：探测/启动 Docker → 确保 pgvector 容器 →
执行 001~004 迁移（`ON_ERROR_STOP=1`，逐条记录成败）→ 核对 schema 清单 →
dry-run + 真实入库 → 用 `BIDOS_DATA_MODE=postgres` 跑端到端校验 → 重复导入验幂等。

常用变体：

```bash
# 只建库 + 迁移，不灌数
$PY scripts/seed_and_verify.py --skip-import

# 重建容器（清空数据重来）
$PY scripts/seed_and_verify.py --recreate

# 不管理容器，直接对已有 PG 操作（迁移需你自行保证已执行）
$PY scripts/seed_and_verify.py --no-docker \
    --database-url postgresql+psycopg://bidos:bidos_dev_only@127.0.0.1:55432/bidos
```

退出码：`0` 全通过 / `1` 有 FAIL / `2` 环境不满足。

---

## 3. 分步命令清单（想手工做时用）

### 3.1 启动 Docker daemon

```bash
docker info >/dev/null 2>&1 || open -a OrbStack   # 或 open -a Docker
# 等待 daemon 就绪
until docker info >/dev/null 2>&1; do sleep 2; done
```

### 3.2 拉镜像并起容器

> **踩坑：宿主端口不要用 5432。** 本机 5432 已被另一个项目的容器
> `xiangneng-hrms-local-postgres-1`（`postgres:17-alpine`）占用，直接用 5432 会
> `bind: address already in use`。BidOS 统一用 **55432**。

```bash
docker pull pgvector/pgvector:pg16

docker run -d --name bidos-pg \
  -e POSTGRES_DB=bidos \
  -e POSTGRES_USER=bidos \
  -e POSTGRES_PASSWORD=bidos_dev_only \
  -p 55432:5432 \
  pgvector/pgvector:pg16

# 等待就绪
until docker exec bidos-pg pg_isready -U bidos -d bidos >/dev/null 2>&1; do sleep 1; done
docker exec bidos-pg psql -U bidos -d bidos -c "SELECT version();"
# 期望：PostgreSQL 16.x ... aarch64-unknown-linux-gnu
```

### 3.3 执行迁移（顺序不可变）

`002` 依赖 `001` 建出的 `pg_trgm` / `vector` 扩展，**必须按序执行**。

```bash
cd /Users/mini/workbuddy-ai/招投标开发/BidOS_Prototype_v0.3

for f in 001_extensions 002_schema 003_workflow_tables 004_event_resolution; do
  echo "===== $f ====="
  docker exec -i bidos-pg psql -v ON_ERROR_STOP=1 -U bidos -d bidos \
    < infra/postgres/init/$f.sql || echo "!!! $f 失败 !!!"
done
```

> `ON_ERROR_STOP=1` 是硬要求：任何一条 DDL 失败都必须中断并暴露，
> 不允许"跳过错误继续建"。

### 3.4 核对 schema 清单

```bash
docker exec bidos-pg psql -U bidos -d bidos -tAc \
  "SELECT extname FROM pg_extension ORDER BY 1;"
# 期望：pg_trgm / pgcrypto / plpgsql / vector

docker exec bidos-pg psql -U bidos -d bidos -tAc \
  "SELECT count(*) FROM pg_tables WHERE schemaname='public';"
# 期望：21

docker exec bidos-pg psql -U bidos -d bidos -tAc \
  "SELECT count(*) FROM pg_indexes WHERE schemaname='public';"
# 期望：45

docker exec bidos-pg psql -U bidos -d bidos -tAc \
  "SELECT column_name FROM information_schema.columns
   WHERE table_name='events'
     AND column_name IN ('resolved_at','resolution','resolved_by') ORDER BY 1;"
# 期望：resolution / resolved_at / resolved_by（004 迁移生效）
```

### 3.5 真实 Excel 入库（先 dry-run）

> **真实数据源只读，绝对禁止写入/修改。** `ExcelConnector` 用
> `openpyxl read_only=True`，本步骤只读取。

```bash
export DATABASE_URL="postgresql+psycopg://bidos:bidos_dev_only@127.0.0.1:55432/bidos"
PY=/Users/mini/.workbuddy-ai/binaries/python/envs/bidos/bin/python
SRC="/Users/mini/Desktop/市场共享文件/AI小程序收集汇总资料"

# ① 先看要写什么，不连库
$PY scripts/import_excel.py --input "$SRC" --dry-run --show 10

# ② 确认无误后真实入库（幂等，可重跑）
$PY scripts/import_excel.py --input "$SRC"
```

### 3.6 切到真实数据模式

`BIDOS_DATA_MODE` 是**进程启动时**读取的（`app/core/config.py` 的 `settings` 是导入期单例），
所以必须在**启动 API 之前**设置：

```bash
export BIDOS_DATA_MODE=postgres
export DATABASE_URL="postgresql+psycopg://bidos:bidos_dev_only@127.0.0.1:55432/bidos"

# 关键：租户 ID 必须与 import_excel 写入的 organization_id 一致！
# 不设的话 container 会回落到 "org_default"，而库里数据是 "org_xiangneng"，
# 结果是 /api/alerts（首页提醒中心）静默返回 0。见 §8 缺陷 4。
export BIDOS_ORGANIZATION_ID=org_xiangneng

cd apps/api
$PY -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

验证（`data_mode` 必须真的是 `postgres`，不是降级 mock）：

```bash
curl -s localhost:8000/health | $PY -m json.tool | head -20
curl -s localhost:8000/api/config | $PY -m json.tool
curl -s "localhost:8000/api/opportunities" | $PY -c "import sys,json;print(len(json.load(sys.stdin)))"
curl -s "localhost:8000/api/alerts" | $PY -c "import sys,json;print(len(json.load(sys.stdin)))"
curl -s "localhost:8000/api/search?q=劳务" | $PY -m json.tool | head -30
```

---

## 4. 数据源清单（只读）

目录：`/Users/mini/Desktop/市场共享文件/AI小程序收集汇总资料/`

| 文件 | 映射 source_id | record_type |
|---|---|---|
| `盲投项目信息 收集汇总表2026(3).xlsx` | `blind_bid_2026` | project |
| `投标项目计划9.13.xlsx` | `bid_plan_2026` | project |
| `（汇总）主要竞争对手 投标项目统计.xlsx` | `competitor_stats` | project |
| `盲投渠道表及重点关注企业8.14.xlsx` | `blind_bid_channel` | entity |
| `投标项目汇总9.17.xlsx` | 已纳入映射 | project（含保证金/结果） |

> 映射定义在 `apps/api/app/config/field_mapping.yaml`（excel-agent 维护）。
> 该文件仍在迭代中 —— 实测中它从 4 个源扩到 5 个源后，`bid_bond_ledger` 才从 0 变 191 行。
> **复现时请连同 mapping 一起固定版本**，否则行数会随映射变化。

**数据覆盖度（真库实测，2026-09-24）**

> 链路通 ≠ 数据齐。下面这些缺口**不是链路故障**，但会让人误判成"功能没做"，
> 因此必须显式记录。`seed_and_verify.py` 会把同一组数字打进报告的「过程说明」。

| 观测 | 实测值 | 影响 |
|---|---|---|
| `projects` 总数 | 2274 | — |
| `projects.workflow_stage` / `status` | **2274/2274 全为 NULL** | 首页 `dashboard.stages` 五张卡片计数几乎恒为 0（实测明细 `[0,0,0,0,1]`） |
| `projects.project_end_at` | **仅 13 行非空** | 到期关注窗口只覆盖 13 个项目，是事件扫描 `created=0` 的主因之一 |
| `projects.deadline` | 979 行非空 | 日期已正确还原 |
| `events` 类型分布 | `HUMAN_REVIEW_REQUIRED`=1473、`DATA_CONFLICT`=55 | **没有** `DEADLINE_APPROACHING` / `PROJECT_EXPIRING` / `DEPOSIT_OVERDUE` |
| `organizations` | 仅 `org_xiangneng` | **005 seed 插的是 `org_default`** —— 与导入数据不一致，建议 005 补一行 `org_xiangneng` |
| `users` | **0 行** | `projects.owner_user_id` 全 NULL → 保证金台账「负责人」列全空（outerjoin 不报错，非 bug） |
| `registrations` / `bid_documents` / `project_evaluations` / `competitor_stats` / `event_sources` | 全表 0 行 | 导入源未覆盖这些阶段明细 |
| `project_results` | 1 行，`result_date=NULL`、`reviewed=false` | 与缺陷 1e 吻合：值写不进去所以是 NULL |
| `source_pointers` | 3840 行，全为 `sheet` 类型且带 location 字段 | 证据链完整 |

> `workflow_stage` / `status` 为 NULL 时，**不要**在仓储层"兜底推导"阶段 ——
> 那等于编造业务状态。正确做法是导入侧把这两列落进去。

**已知覆盖缺口（如实记录）**：

- `registrations` / `bid_documents` / `project_evaluations` / `competitor_stats`
  四张承载表仍为 0 行 —— 导入源未覆盖这些阶段明细（它们主要靠人工/后续流程写入），
  不是链路故障。
- `project_results` 仅 1 行：真实 Excel 里明确写了「投标结果」的行很少。

---

## 5. 端到端校验清单（`seed_and_verify.py` 自动执行）

| # | 校验项 | 期望 |
|---|---|---|
| 1 | 扩展 | `pg_trgm` / `pgcrypto` / `vector` 均存在 |
| 2 | 表 | 21 张业务表齐全 |
| 3 | 视图 | `v_project_sources` 存在 |
| 4 | 索引 | ≥ 40（实测 45） |
| 5 | 004 迁移 | `events.resolved_at/resolution/resolved_by` 存在 |
| 6 | factory | `describe_repository()["mode"] == "postgres"`（未降级） |
| 7 | `/health` | `data_mode == "postgres"` |
| 8 | `/health` | `subsystems.degraded` 为空 |
| 9 | `/api/config` | `effective_data_mode == "postgres"` |
| 10 | `/api/opportunities` | 条数 > 0 |
| 11 | `/api/alerts` | 条数 > 0 |
| 12 | `/api/dashboard` | `stages` 非空 |
| 13 | `/api/events/scan` | 返回 `created`（不是 503） |
| 14 | 检索 | 中文项目名能命中 |
| 15 | Source Pointer | 全部带 `sheet_name` / `row_id` / `cell_range` |
| 16 | Source Object | 带 `content_hash` |
| 17 | 幂等 | 重复导入后 `projects` 行数不增长 |
| 18 | 租户一致性 | `BIDOS_ORGANIZATION_ID` 与库中 `events.organization_id` 一致（否则 `/api/alerts` 静默为空） |
| 19 | ORM↔DDL 类型 | 无新增类型漂移（否则真实写库报 `DatatypeMismatch`） |

> 「表本来就空」的端点会记 **SKIP** 而不是 FAIL —— 链路没坏，只是导入源没覆盖该表。
> 这是刻意的区分，避免把"没数据"误报成"链路失败"，也避免反过来掩盖真实故障。

---

## 6. 故障排查

| 症状 | 原因 | 处理 |
|---|---|---|
| `bind: address already in use` (5432) | 5432 被别的容器占用 | 改用 `-p 55432:5432` |
| `failed to connect to the docker API ... .orbstack/run/docker.sock` | Docker daemon 没起 | `open -a OrbStack`，等 `docker info` 成功 |
| `CREATE EXTENSION vector` 失败 / 无 `vector` 类型 | 用了不带 pgvector 的 PG | 必须用 `pgvector/pgvector:pg16` |
| `cannot cast type integer to timestamp with time zone` | Excel 裸日期序列号（如 46031）未归一化 | 见 §8 已知缺陷 1 |
| `'sessionmaker' object has no attribute 'connect'` | 检索服务被传了 sessionmaker 而非 Engine | 见 §8 已知缺陷 2 |
| `scan_all 没有任何可用数据源` | `EventScanner` 未注入 project_source/bond_source | 见 §8 已修复 |
| `/api/alerts` 返回 0，但 events 表有数据 | 读写租户 ID 不一致（`org_default` vs `org_xiangneng`） | 设 `BIDOS_ORGANIZATION_ID=org_xiangneng`，见 §8 缺陷 3 |
| `/health` 显示 `data_mode=mock` | `DATABASE_URL` 为空或连不上，已降级 | 查 `docker ps` 与 `DATABASE_URL`；降级会打 WARNING |
| 灌数后端点仍为 0 | 导入源未覆盖该表 | 看 `seed_and_verify` 的 counts 与 SKIP 说明 |

---

## 7. 实测记录

环境：macOS / Darwin 27.0.0 / OrbStack Docker 29.4.0 / PostgreSQL 16.15 + pgvector。

| 步骤 | 结果 |
|---|---|
| Docker daemon | 初始未运行（OrbStack 未启动）→ `open -a OrbStack` 后 3s 就绪 |
| 镜像 `pgvector/pgvector:pg16` | 拉取成功（约 5 分钟） |
| 容器 | `bidos-pg` 起在 **55432**（5432 被 HRMS 容器占用） |
| 001~004 迁移 | **4/4 全部成功**，`ON_ERROR_STOP=1` 下无一条 DDL 报错 |
| schema | 21 表 / 1 视图 / 45 索引 / 扩展 `pg_trgm,pgcrypto,plpgsql,vector` |
| `vector(1024)` + HNSW 索引 | 创建成功 |
| 真实入库（**原版 import_excel.py**） | **失败**，依次暴露 5 处缺陷，见 §8 缺陷 1 |
| 真实入库（`/tmp` 诊断补丁副本，仅验证链路） | **成功**：`created=13697 updated=5575` |
| 入库后真实条数 | `projects=2274` `entities=2023` `source_objects=3840` `source_pointers=3840` `events=1528` `project_results=1` `bid_bond_ledger=191` |
| **幂等验证** | 连续导入 3 次：第 2、3 次 `created=0`、`skipped(幂等)=3840`，**各表行数完全不变** |
| Source Pointer 定位信息 | 3840/3840 带 `sheet_name` + `row_id` + `cell_range`；3840 条带 `content_hash` |
| `seed_and_verify.py` 端到端 | **PASS=21 / FAIL=1 / SKIP=4** |
| `tests/backend/test_real_postgres_e2e.py` | **18 项通过（1 项 skip）** |
| `/health` `data_mode` | `postgres`（未降级 mock） |
| `/api/opportunities` | 2274 条真实项目 |
| `/api/alerts` | 1528 条真实事件（修正租户 ID 后） |
| `/api/results` / `/api/bid-bonds` | 1 条 / 191 条真实数据 |
| `/api/dashboard` | 5 张阶段卡片结构完整、`keyTimeline`=6；但**阶段计数明细为 `[0,0,0,0,1]`** —— 因 `workflow_stage`/`status` 全为 NULL（数据覆盖缺口，见 §4），不是链路故障 |
| `/api/search?q=万达中学食堂` | 命中 2 条真实中文项目 |
| 唯一 FAIL | `hybrid_search` 子系统降级，见 §8 缺陷 2 |

> **诚实声明**：上面"真实入库成功"那行是用 `/tmp/import_excel_patched.py`
> （仅含 §8 缺陷 1 的诊断修复）跑出来的，目的是**证明下游链路本身是通的**。
> 仓库里的 `scripts/import_excel.py` **当前仍未修复**，直接跑仍会失败。
> 修复前不要把本手册的入库结果当作"原版脚本可用"。
>
> 另外 `field_mapping.yaml` 在我实测期间被持续扩源，因此行数会随映射版本变化 ——
> 上表数字对应的是 `mapping` 已含 `投标项目汇总9.17.xlsx` 的版本。

---

## 8. 已知缺陷（真实链路暴露）

> 这些**只在真实 PostgreSQL 上才会暴露**，SQLite 内存自测测不出来。
> 状态以本节为准。

### 缺陷 1（阻塞入库）—— `import_excel.py` 五处缺陷，逐层暴露
负责人：excel-agent。**修一处才暴露下一处**，以下是完整清单（全部实测）：

**(1a) 日期序列号未使用归一化值**
- 报错：`CannotCoerce: cannot cast type integer to timestamp with time zone`，`projects.deadline = 46031`。
- 根因：连接器 `record.item.fields["报名截止时间"]` **已正确**还原为
  `datetime(2026-01-09, tz=Asia/Shanghai)`；但 `record.cells` 存的是原始单元格
  （`excel_connector.py:891`），而 `extract_row()` 读 `record.cells` 并直接取
  `cell.value`，绕过了归一化。

**(1b) 所有 event payload 缺少 `id`（最严重）**
- 报错：`NotNullViolation: null value in column "id" of relation "events"`。
- 根因：`PlannedWrite(kind="event", id=..., payload={...})` 的 **payload 里没有 `"id"`**，
  而 `apply_plan` 是用 `model(**write.payload)` 建对象的。
- 影响：**每一个**事件写入都失败 —— 包括 `DATA_CONFLICT`、`HUMAN_REVIEW_REQUIRED`、
  `SOURCE_FAILED`，即"失败可见"机制本身写不进去。

**(1c) 无法解析的日期文本被硬塞进 timestamptz**
- 报错：`InvalidDatetimeFormat: invalid input syntax for type timestamp with time zone: "8/170"`（另有 `"/"`）。
- 根因：真实表日期列有脏值。连接器把它们判为 `text`（`"/"` 判为 null token）保留是**对的**，
  但 `extract_row` 不做类型校验就写进 `projects.deadline`。
- 期望行为：无法解析的日期**不得入库**，原文转 `metadata` 并生成
  `HUMAN_REVIEW_REQUIRED`，绝不硬塞也绝不静默丢弃。

**(1d) 金额描述文本被硬塞进 numeric 列**
- 报错：`InvalidTextRepresentation: invalid input syntax for type numeric:
  "1208.289898万元/年(不含税价)，其中不可竞争费用为1198.407898万元/年(不含税)…"`。
- 根因：与 1c 同类。连接器对无法可靠解析的金额文本**有意**保留原文（并加了
  "已保留原文待人工确认" 备注），但 import 仍把它写进 `projects.budget`（`numeric(18,2)`）。

**(1e) ORM 与 DDL 类型漂移导致 `project_results` 写不进去**
- 报错：`DatatypeMismatch: column "result_date" is of type date but expression is of type character varying`。
- 根因：`003_workflow_tables.sql` 是 `result_date date`，而 `models.py` 声明
  `result_date: Mapped[str | None] = mapped_column(String)`，SQLAlchemy 绑定 `$10::VARCHAR`。
  **注意即使值为 NULL 也会失败** —— PG 在计划期就拒绝 VARCHAR→date。
- 归属：`models.py` 属 repo-agent（见缺陷 4）。

**诊断验证的修法**（1a~1d 已实测可完整入库；1e 需在 ORM/DDL 侧修）：
```python
# extract_row 中
if not target:
    continue
# 完全以连接器归一化结果为准；归一化里没有该列 = 该列该为空，不得回退原始值
if header not in record.item.fields:
    continue
value = record.item.fields[header]
# 类型守卫：日期/金额目标列只接受对应类型，其余转 metadata 待人工
_leaf = target.rsplit(".", 1)[-1].lower()
_raw_key = target.split(".", 1)[1] + "_raw"
if _leaf in DATE_LEAVES and not isinstance(value, datetime):
    extracted.meta_fields[_raw_key] = value; continue
if _leaf in MONEY_LEAVES and not isinstance(value, (int, float)):
    extracted.meta_fields[_raw_key] = value; continue
```
```python
# apply_plan 中
_payload = dict(write.payload); _payload.setdefault("id", write.id)
session.add(model(**{payload_key_map.get(k, k): v for k, v in _payload.items()}))
```

> ⚠️ 反面教材：第一版修法写成 `record.item.fields.get(header, cell.value)`（回退原始值），
> 结果把 `"/"` 又带了回来。**归一化结果里没有该列就说明它该是空的**，不能回退。

### 缺陷 2（检索失效）—— `container.py` 把 sessionmaker 当 Engine
负责人：**team-lead**（`container.py` 不在 repo-agent 写权限内）。
状态：**部分修复** —— repo-agent 已在 `factory.py` 加好 `get_engine()`（惰性、与
`_get_session_factory()` 共用同一 Engine 实例），只差 `container.py` 把
`SqlSearchService(_get_session_factory())` 改成 `SqlSearchService(get_engine())` 这一行。
- 现象：`子系统 hybrid_search 未挂载，已降级：'sessionmaker' object has no attribute 'connect'`。
- 根因：`SqlSearchService.__init__(engine: Engine, ...)`（`search_sql.py:314`）需要 Engine，
  但 `container.py` 传的是 `_get_session_factory()`（sessionmaker）。
- 影响：`sql_search is None` → `/api/search` 会 fallback 到内存索引乃至 **mock.json**，
  即 `data_mode=postgres` 时检索可能返回原型假数据且不告警。
- repo-agent 真库实测（`factory.get_engine()` 直接构造）：`dialect=postgresql`、
  `capability=(structured=True, trigram=True)`、检索命中 5 条真实项目、`runtime_degradations={}`。

### 缺陷 3（首页时间轴静默为空）—— 读写租户 ID 不一致
负责人：repo-agent / 需 team-lead 定口径。状态：**已有可用规避（设环境变量）**。
- 现象：库里 1771 条未解决事件，`repository.alerts()` 返回 1771，
  但 `GET /api/alerts` 返回 **0**（且 HTTP 200，不报错）。
- 根因：`/api/alerts` 优先走 `event_store.list_open()`，而
  `SqlAlchemyEventStore` 按 `organization_id` 过滤：
  - `container.py:24` `DEFAULT_ORGANIZATION_ID = settings.organization_id or "org_default"`
  - `settings.organization_id` 默认空 → 回落到 **`org_default`**
  - 但 `import_excel.py:62` 写入的是 **`org_xiangneng`**
  - 同时 `PostgresRepository` 在 org 为空时**不过滤**（team-lead 裁定的 B1 读侧口径），
    于是同一个进程里"项目读得到、事件读不到"，语义不一致。
- 规避（已实测）：启动时设 `BIDOS_ORGANIZATION_ID=org_xiangneng` → `/api/alerts` 由 0 变 **1771**。
- 建议根治：事件仓储的读侧与 `PostgresRepository` 采用同一口径（org 未配置则不按租户过滤），
  或统一 `DEFAULT_ORGANIZATION_ID` 与导入脚本的 `DEFAULT_ORG_ID`。
- 已固化为回归测试：`test_event_store_tenant_matches_imported_rows` 与
  `test_health_reports_postgres_in_subprocess` 中的 `/api/alerts` 非空断言。

### 缺陷 4（ORM ↔ DDL 类型漂移，系统性扫描发现 3 处）
负责人：**team-lead**（`models.py` 由其维护；repo-agent 已确认该文件不在其写权限内，并已转交）。
状态：**未修复**，已固化为回归测试 `test_no_new_orm_ddl_type_drift`
（允许清单内的已知漂移不报错，新漂移立即失败）。

| 表.列 | ORM 声明 | 真实 DDL | 后果 |
|---|---|---|---|
| `project_results.result_date` | `String` | `date` | 写 `project_results` 必失败（缺陷 1e） |
| `audit_logs.source_pointer_ids` | `JSONB` | `text[]` | 写审计日志会类型不匹配 |
| `feedback.evidence_pointer_ids` | `JSONB` | `text[]` | 写反馈会类型不匹配 |

> **读侧影响评估**（repo-agent 实测，已验证）：`result_date` 漂移对**读**无害 ——
> SQLAlchemy 声明 `String` 不改变 psycopg 的返回值，PG `date` 列照样返回
> `datetime.date`，`PostgresRepository._as_dt()` 对 `date` 有专门分支。
> 真库实测 `results()` 返回 1 行、不抛异常。所以**范围是写侧**：
> `apply_plan` 写 `project_results` / `audit_logs` / `feedback` 时才会炸。
>
> 这类问题 **SQLite 内存自测永远测不出**（SQLite 不做类型校验，字符串照收）。
> 扫描方式：遍历 `Base.metadata` 的列类型，与 `information_schema.columns.data_type` 逐一比对。

### 已修复
- **`EventScanner` 未注入数据源**（原 `POST /api/events/scan` 恒 503）：
  repo-agent 已改为注入数据源，现返回
  `{'created': 0, 'updated': 0, 'skipped_resolved': 0, 'events': 0}`（200）。
  当前为 0 是因为 `project_results` / `bid_bond_ledger` 为空（数据覆盖缺口，见 §4），
  不是链路故障。

---

## 9. 相关文件

- `scripts/seed_and_verify.py` —— 本手册的自动化实现（一键建库+灌数+校验）
- `scripts/import_excel.py` —— Excel 规范化幂等入库（excel-agent）
- `tests/backend/test_real_postgres_e2e.py` —— 真实 PG 端到端测试（无 PG 自动 skip）
- `infra/postgres/init/001~004*.sql` —— 迁移脚本
- `docs/FAILURE_MODES.md` / `docs/REAL_DATA_INTEGRATION.md` —— 失败可见与真实数据接入约定
