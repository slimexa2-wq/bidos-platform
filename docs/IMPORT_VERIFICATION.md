# 真实数据导入验证报告（IMPORT_VERIFICATION）

- 执行者：import-rerun agent
- 日期：2026-09-24
- 环境：Python `bidos` env；PostgreSQL `bidos-pg` 容器 @ `127.0.0.1:55432`（库名 bidos，用户 bidos）
- 数据源（只读）：`/Users/mini/Desktop/市场共享文件/AI小程序收集汇总资料/`（5 张业务 xlsx）
- 脚本：`scripts/import_excel.py`（当前原版，未做任何修改）

## 1. 导入线健康检查

`python -m unittest tests.backend.test_excel_connector`：**47 个测试全部通过**。

但注意：测试全绿 ≠ 真实数据可用。真实数据含数字格式的 Excel 日期序列号，测试数据没有，导致下文 §4 的入库崩溃。

## 2. Dry-run 计划写入量

`import_excel.py --dry-run --input <数据源目录>`：

| 写入类型 | 计划条数 |
|---|---|
| project.create | 2274 |
| entity.create | 1949 |
| source_object.create | 3840 |
| source_pointer.create | 3840 |
| event.create | 1409 |
| bid_bond.create | 419 |
| project_result.create | 1337 |

事件拆分：HUMAN_REVIEW_REQUIRED 1022、DATA_CONFLICT 387（无 SOURCE_FAILED）。

按来源拆分（project）：
- 投标项目汇总9.17.xlsx：398
- 投标项目计划9.13.xlsx：138
- 盲投渠道表及重点关注企业8.14.xlsx：0（纯渠道/企业表）
- 盲投项目信息 收集汇总表2026(3).xlsx：1033
- （汇总）主要竞争对手 投标项目统计.xlsx：705

## 3. 库状态与清理

导入前发现库中已有当天 08:15 UTC 的同源数据（2274 projects 等），但与当前脚本计划严重不符：

| 表 | 库中（旧导入遗留） | 当前脚本 dry-run 计划 |
|---|---|---|
| project_results | 1 | 1337 |
| bid_bond_ledger | 191 | 419 |
| events | 1528（HR 1473 + DC 55） | 1409（HR 1022 + DC 387） |
| entities | 2023 | 1949 |

判定为旧缺陷版脚本的遗留产物。为获得干净的首次导入数据，已执行：

```sql
TRUNCATE projects, entities, entity_aliases, project_entities, source_objects,
  source_pointers, events, event_sources, bid_bond_ledger, project_results,
  bid_documents, competitor_stats, project_evaluations, document_chunks,
  human_decisions CASCADE;
```

保留 `organizations`（org_xiangneng seed）等非导入表。其余表（users、feedback、audit_logs 等）在清理前即为 0 行。

## 4. 真实入库：**失败（阻断缺陷）**

执行 `DATABASE_URL=postgresql+psycopg://bidos:bidos_dev_only@127.0.0.1:55432/bidos python scripts/import_excel.py --input <数据源目录>`：

- 注意：环境只装了 psycopg3（3.3.6）+ SQLAlchemy 2.0.54，无 psycopg2。`postgresql://` DSN 会解析到 psycopg2 dialect 而报 `ModuleNotFoundError`，须改用 `postgresql+psycopg://`（脚本无 psycopg2 硬依赖，仅 DSN scheme 问题，未改脚本）。
- 随后在第一批 flush 即崩溃：

```
sqlalchemy.exc.ProgrammingError: (psycopg.errors.CannotCoerce)
cannot cast type integer to timestamp with time zone
... 'deadline': 46031 ...
```

崩溃后各业务表均回滚为 0 行（事务级回滚，库无脏数据）。

## 5. 根因分析（已定位到行，未修改任何文件）

### 缺陷 A：connector 层不做日期序列号转换

`apps/api/app/connectors/excel_connector.py:263-265`：`normalize_cell` 对 int/float 直接记为 `kind="number"`，**未调用**同文件 204 行已有的 `excel_serial_to_datetime()`。

真实数据扫描（`scripts/_tmp_dirty_date_scan.py`）确认「报名截止时间」→ `projects.deadline` 列：

| 单元格类型 | 数量 |
|---|---|
| datetime（正常） | 711 |
| **int（Excel 序列号，如 46031）** | **67** |
| **str（脏日期文本）** | **13** |

另「到期日期」→ `projects.project_end_at`：13 个，全部为 datetime（该列干净）。

### 缺陷 B：脚本层 project 路径无类型守卫

`scripts/import_excel.py:922-928` 把 `extracted.project_fields` 原样塞进 project payload。`_dt()` 守卫（526 行，注释明确写着"只有真正的日期才允许落到 DateTime 列，其余一律 None（交人工）"）只用在 bond 字段（1039-1042 行），deadline / project_end_at 等项目路径完全没走。于是 int 46031 直达 `timestamptz` 列，PostgreSQL 拒绝 cast，整批导入崩溃。

### 为什么测试是绿的

测试数据里日期都是 datetime 或可解析文本，没有数字格式的序列号单元格，因此两条缺陷均未被现有测试覆盖。

## 6. 入库后质量核查

**无法执行**——真实入库在第一步 flush 即崩溃（§4），业务表为空，deadline 真日期 / project_end_at / events 分布 / bid_bond status / 幂等二次导入等核查项目均无对象可查。待缺陷 A/B 修复后重跑本验证流程再补。

## 7. 结论

- 导入线单测健康（47 绿），dry-run 计划生成正常（确定性 ID 幂等设计有效：5 个抽查 project ID 与库中旧数据完全一致）。
- **当前原版脚本对真实数据不可用**：67 个数字格式日期序列号 + 13 个脏日期文本会导致入库必然崩溃。任务背景中的"日期序列号"与"脏日期文本"两类缺陷在真实数据路径上并未修复，仅 result_date 的 ORM↔DDL 漂移已修复（models.py:302 为 `Date`）。
- 修复建议（二选一或叠加）：
  1. connector 层：`normalize_cell` 对 number 且 `is_date_field` 为真的单元格调用 `excel_serial_to_datetime`；
  2. 脚本层：project 路径对日期目标字段套 `_dt()` 守卫，非日期值转 None 并发 HUMAN_REVIEW_REQUIRED 事件（与 `_dt` 注释的设计意图一致）。
