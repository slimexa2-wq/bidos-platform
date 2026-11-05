# 事件引擎持久化设计（P1）

> 落地范围：交接文档 §3 提醒/事件引擎、§12.5 持续事件、§11 P1 任务队列，
> 规则权威定义见 `docs/EXPIRY_AND_BID_BOND_RULES.md`。
>
> 代码：`apps/api/app/services/event_store.py`、`event_scanner.py`、`event_resolver.py`
> 测试：`tests/backend/test_event_store.py`（46 例，不依赖 PostgreSQL）
> 迁移：`infra/postgres/init/004_event_resolution.sql`（可选优化）

## 1. 铁律：已读 != 已解决

这是整个模块最重要的一条，也是与旧行为最容易混淆的地方。

| 动作 | 写什么 | 事件还在首页吗 |
|---|---|---|
| 用户点开看过 | `handled = true` | **在**（未解决） |
| 匹配到新公告 / 确认续签 / 确认到账 / 人工关闭 | `resolved_at` 被写入 | 不在 |

因此：

* `mark_handled(event_id)` 的 docstring 明确写了"只标记已读，不影响业务解决"；
* `list_open()` 过滤条件是 **`resolved_at IS NULL`**，**不是** `handled = false`。
  只有显式传 `unread_only=True` 时才会按 `handled` 过滤；
* `home_timeline()` 直接复用 `list_open()`，保证已解决的持续事件不再出现在首页时间轴。

> ⚠️ 已知冲突：任务描述里出现过"`list_open` 只返回 `handled=false AND
> resolved_at IS NULL`"。这与铁律和验收标准 §12.5 矛盾，已按**铁律**实现
> （`handled` 不参与 `list_open` 过滤）。

## 2. 解决状态的存放位置

解决状态（`resolved_at` / `resolution` / `resolved_by` / `resolution_note`）
存放在 **`Event.metadata`（JSONB）**，它是唯一权威载体。

理由：

1. `app/domain.py` 与 `app/repositories/models.py` 由其他 Agent 负责，本模块不越界改文件；
2. `events.metadata` 列现有 ORM 已声明，因此**不改任何别人的文件**即可正确落库；
3. metadata 是 JSON 安全的，内存实现与 SQL 实现行为完全一致；
4. `infra/postgres/init/004_event_resolution.sql` 因此降级为**可选优化**：
   补 `resolved_at / resolution / resolved_by` 三列 + 索引，便于纯 SQL 审计查询。
   **不执行该迁移，事件引擎也照常工作**；
5. 若将来 ORM 映射了这三列，`apply_event_to_row()` 会通过 `hasattr` 探测自动开始双写，
   无需再改代码。

`resolution` 取值与 `event_resolver.py` 常量一致：
`new_tender_matched` / `renewed` / `refund_confirmed` / `manual_closed`。

### 跨模块契约（与仓储层）

以下三个符号被 `PostgresRepository`（`alerts()` / `keyTimeline`）直接依赖，
改动前必须通知 repo-agent，否则口径漂移：

| 符号 | 契约 |
|---|---|
| `RESOLVED_AT`（`"resolved_at"`） | 键存在于 `metadata` 即表示"已解决"，与 `handled` 无关 |
| `SEVERITY_ORDER` | severity → 排序权重，数字越小越紧急 |
| `sort_key(event)` | severity 升序 → `due_at` 升序（NULL 最后）→ `occurred_at` |

仓储层用 `not is_resolved(event)` 过滤，与本模块 SQL 侧
`jsonb_extract_path_text(metadata, 'resolved_at') IS NULL` 语义等价
（后者可用 004 的部分索引）。

### 读路径：脏数据可见但不 500

`row_to_event()` 是**严格**映射：未知 `event_type`、severity 越界、缺 `occurred_at`
都会抛 `ValidationError` / `ValueError`。它适合测试与写回路径，但**不适合读路径** ——
一行脏数据不该让整个提醒中心 500。

读路径改用宽松解码：

```python
from app.services.event_store import decode_event_rows

events, rejected = decode_event_rows(rows)
# events   -> list[Event]，可直接喂 sort_key / is_resolved
# rejected -> [{"event_id": ..., "reason": "ValueError: 'UNKNOWN' is not a valid EventType"}]
```

调用方必须把 `rejected` 计数并告警（写日志 / 暴露到 `/health`）。
**这不是静默吞错** —— 失败可见，但不拖垮页面。repo-agent 的
`_load_open_events()` 建议改用此函数。

## 3. 幂等策略（持续事件不刷屏）

`upsert()` 以 **`resolution_key`（缺省时 `event.id`）** 为幂等键：

| 情况 | 行为 |
|---|---|
| 键不存在 | 插入（`created`） |
| 键已存在且**未解决** | 原地更新 severity / detail / due_at / metadata（`updated`）；**保留** `id`、`occurred_at`、`handled` |
| 键已存在且**已解决** | **不复活、不覆盖**（`skipped_resolved`） |

关键点：

* 保留 `occurred_at` 保证"首次出现时间"不因每天重扫而变新；
* 保留 `handled` 保证重扫不会把已读打回未读；
* 已解决事件不被重扫复活 —— 否则用户确认过的续签 / 到账会被每天刷回首页，
  这是最容易踩的坑，有专门测试 `test_resolved_event_is_not_resurrected_by_rescan` 守住；
* severity 升级是**原地更新**而不是新建事件（`info → warning → critical`），
  所以首页不会出现同一项目的多条到期提醒。

## 4. 扫描器（`event_scanner.py`）

复用 `EventEngine` 的纯函数规则，**不引入任何 LLM 判断**：

* `scan_project_expiry(projects)` → `EventEngine.expiry_watch_event()`
  * 结束前 90 天进入提醒（`info`）；≤30 天升为 `warning`；已过期未匹配新公告为 `critical`；
  * 无 `project_end_at` 的项目记入 `skipped_projects`，不猜日期、不产出；
  * `resolved=True` 的项目不产出事件。
* `scan_bid_bonds(records)` → `EventEngine.deposit_refund_event()`
  * 应退日前 3 天起提醒；应退日 `warning`；逾期 `DEPOSIT_OVERDUE` + `critical`；
  * `已退回 / 无需退回 / 有 actual_refund_at` → 不产出；
  * `未缴纳` → 不产出（没有可退的钱）；
  * **`expected_refund_at is None` → 产出 `HUMAN_REVIEW_REQUIRED`，且不产出任何
    DEPOSIT 提醒**（交接文档 §3.2：禁止 AI 猜日期后自动产生"精准提醒"）；
  * 状态字符串无法识别 → 记入 `skipped_bonds`，不猜。
* `scan_all(...)` 汇总并写库，返回 `ScanResult`（含 `created/updated/skipped_resolved`
  与全部跳过原因），因此"数据缺口"是可见的。

### 输入适配

三种输入都支持（鸭子类型，`_pick` + `_as_datetime`）：

1. pydantic：`ProjectExpiryWatch` / `BidBondLedgerRecord`；
2. ORM 行：`Project` / `BidBondLedger`（属性名 `metadata_`）；
3. Repository dict：camelCase，日期为 `"2026-09-24"` 字符串
   （`PostgresRepository` 返回的形状，见 `repositories/base.py` 契约）。
   纯日期按 UTC 零点处理，避免与 aware `now` 比较时抛 TypeError。

### 失败可见（§12.6）

* 数据源传 `None` → 抛 `ScanSourceUnavailable`；
* 取数函数抛错 → 包装成 `ScanSourceUnavailable` 抛出；
* 空列表 `[]` 才是合法的"确实没有数据"；
* `scan_all()` 不传任何数据源也抛错。
  **绝不用空结果冒充"没有到期项目"。**

## 5. 解决器（`event_resolver.py`）

| 方法 | 关闭对象 | resolution |
|---|---|---|
| `resolve_by_new_tender(project_id, sp_id, confidence=...)` | 该项目所有 `PROJECT_EXPIRING` | `new_tender_matched` |
| `resolve_by_renewal(project_id, actor_id)` | 该项目所有 `PROJECT_EXPIRING` | `renewed` |
| `resolve_by_refund_confirmed(ledger_id, actor_id, sp_id)` | 该笔保证金的所有事件（含人工复核事件） | `refund_confirmed` |
| `resolve_manual(event_id, actor_id, note)` | 单条事件 | `manual_closed` |

* **低置信度不自动关闭**：`confidence < confidence_threshold`（默认 0.7）时
  改为产出 `HUMAN_REVIEW_REQUIRED`（带候选 Source Pointer 与置信度），
  到期事件保持 open，返回 `status="review_required"`。
* `resolve_by_refund_confirmed` 强制要求 `refund_source_pointer_id`（到账凭证来源），
  否则抛 `ValueError`；操作人、时间、凭证来源三者都进审计。
* `resolve_manual` 强制要求 `note`（关闭原因必须留痕）。

### 红线：保证金不得以「人工关闭」结案（§3.2）

若允许 `manual_closed` 结案保证金事件，UI 上一个通用"关闭"按钮就能抹掉凭证校验，
审计链形同虚设（等于"口头说退了就算退了"）。

因此校验放在 **`EventStore.resolve()` 这个唯一收口点**，而非只放在
`EventResolver.resolve_manual()`：`main.py` 的 `POST /api/alerts/{id}/resolve`
是**直接调用 `store.resolve()`** 的，只在 resolver 层加校验会留下一个绕过口子。

判定规则（`is_deposit_event`）：事件类型为 `DEPOSIT_REFUND_DUE` / `DEPOSIT_OVERDUE`，
或 `resolution_key` 以 `deposit_refund` 开头（覆盖 `deposit_refund:{ledger}` 与
`deposit_refund_review:{ledger}` 这类"应退日期待补录"的复核事件）。

命中且 `resolution == "manual_closed"` → 抛 `RefundConfirmationRequired`
（`ValueError` 子类，兼容既有 `except ValueError`），并指明改走
`resolve_by_refund_confirmed` + 凭证。事件**保持 open**，不会被关掉。

**该拒绝是无条件的**（team-lead 裁定「直接拒绝」）：没有"note 非空即放行"这类例外。
否则一个通用"关闭"按钮加一个随便填的原因就能绕过凭证，红线就形同虚设。

#### 已知产品缺口：部分保证金场景当前无法关闭

红线保留的同时，以下**合法业务场景没有合规的结案路径**（提醒会永久留在首页）：

* **中标后保证金转为履约保证金** —— 钱不是"退回"而是改变用途，`refund_confirmed` 不成立；
* **保证金被没收**（违约等）—— 永远不会到账。

后果：首页会出现永不消失的 `critical` 提醒，且用户无合规操作可消除，长期会引发
"狼来了"效应、让人无视提醒。

**建议的解法（待 team-lead 决策，尚未实现）**：新增**独立且带凭证**的关闭路径，
例如 `converted_to_performance_bond` / `written_off`，各自强制要求
凭证 Source Pointer（转换协议 / 没收通知或财务核销单）+ 操作人 + 原因。

> ⚠️ 不能退化成"`note` 非空即可关闭" —— 那等于把刚堵上的口子重新打开。
> 新增路径必须**有据可依（带凭证）**。


### 审计

`EventStore.resolve()` 内部调用 `app/services/audit.py` 的 `AuditService.write()`，
`action="event_resolved"`，记录 `before`（原 handled/severity/resolved_at）、
`after`（resolution/note/resolved_at）、`actor_id`、`source_pointer_ids`。
构造 store 时若未注入 `audit`，则不写审计 —— **生产环境必须注入 container 里的
`audit` 单例**。

## 6. 排序与首页

`list_open(severity_order=True)` 排序：`severity`（critical > warning > info >
success > neutral）→ `due_at` 升序（NULL 最后）→ `occurred_at`。
SQL 实现用 `CASE` + `NULLS LAST`，未解决过滤走
`jsonb_extract_path_text(metadata, 'resolved_at') IS NULL`，配合 004 里的部分索引。

## 7. 与 Repository 层的集成注意

`PostgresRepository.alerts()`（`postgres_repository.py:239`）当前是：

```python
select(Event).where(Event.handled.is_(False))
```

这会**把"已读"当成"已解决"**：用户点开看过之后，未解决的到期提醒就消失了，
直接违反 §3.1 / §12.5 与验收标准。建议 repo-agent 改为：

```python
select(Event).where(func.jsonb_extract_path_text(Event.metadata_, "resolved_at").is_(None))
```

（该文件不在本次改动范围内，故仅记录问题，未修改。）

## 8. 已挂载的 API 端点（以 `main.py` 实际实现为准）

以下路径是 **team-lead 在 `main.py` 里已实现的最终契约**（web-agent 的
`apiProvider.ts` 已对齐）。⚠️ 早先本文档/消息里给过的
`/api/projects/{id}/resolve-expiry`、`/api/bid-bonds/{id}/confirm-refund`
两个写法**作废**，请以本表为准：

```python
GET  /api/alerts                                  # 未解决事件（handled 不参与过滤）
POST /api/alerts/{event_id}/handle                # 只标记已读，事件仍在
POST /api/alerts/{event_id}/resolve?resolution=   # 业务解决单条事件
POST /api/events/scan                             # 重算持续事件（幂等）
POST /api/projects/{project_id}/expiry/resolve    # resolution=new_tender_matched|renewed
                                                  # new_tender_matched 必须带 source_pointer_id
                                                  # confidence 低于阈值 → 不关闭，转人工
POST /api/bid-bonds/{ledger_id}/refund/confirm    # 必须带 refund_source_pointer_id，否则 400
```

### store / scanner / resolver 的装配（`container.py`）

* store：postgres 模式用 `SqlAlchemyEventStore(session_factory, organization_id=...)`，
  否则 `InMemoryEventStore`；两者都注入 `audit` 单例（不注入则不写审计）；
* resolver：显式传 `confidence_threshold=settings.review_confidence_threshold`；
* **scanner 必须配置数据源**，否则 `scan_all()` 无参调用会抛
  `ScanSourceUnavailable`（这是刻意的：绝不静默返回空结果假装没有到期项目）。
  推荐装配：

```python
EventScanner(
    store,
    project_source=repository.results,        # 含 projectId / result / projectEndDate
    bond_source=repository.bid_bonds,         # 含 expectedRefundAt / status / projectName
)
```

`repository.results()` 是项目到期扫描的天然数据源（单一真相源：结束日期只认
`projects.project_end_at`）。它**不含** `expiryWatchResolved`，但这不影响正确性：
「已解决不再出现」由事件自身的 `resolved_at` 保证（store 的 upsert 不会复活已解决事件）。

### 错误码约定

* `ScanSourceUnavailable`（数据源不可用 / 未配置）→ **503**，不得返回空列表 + 200；
* `RefundConfirmationRequired`（保证金试图人工关闭）→ **400/409**，
  消息须指明改走 `/api/bid-bonds/{ledger_id}/refund/confirm` 并附凭证；
* `resolve_manual` 的 `note` 为空 → **400**。


## 9. 测试覆盖（`tests/backend/test_event_store.py`，79 例）

| 分组 | 关键断言 |
|---|---|
| 项目到期 | 91 天不产；90 天产 `info`；30 天 `warning`；过期 5 天仍 `critical`；resolved 不产；缺结束日期记入 skipped |
| 保证金 | 前 5 天不产；前 3 天产；逾期 `critical`；**缺应退日期 → `HUMAN_REVIEW_REQUIRED` 且无 DEPOSIT 事件**；已退回/未缴纳不产；状态乱码记入 skipped |
| 已读 vs 已解决 | `mark_handled` 后仍在 `list_open`；`resolve` 后消失；审计含操作人与凭证 |
| 幂等 | 重扫两次事件数不变；重扫不重置 `occurred_at`/`handled`；**已解决不被复活**；severity 原地升级；critical 排最前 |
| 解决渠道 | 高置信度关闭；低置信度不关闭且转人工；阈值边界 0.7 关闭；续签/到账/人工关闭；到账必须给凭证 |
| 失败可见 | `None` 源抛错；取数函数抛错；空列表合法；summary 暴露跳过数 |
| 行映射 | row↔event 往返；已映射三列时双写、未映射时不留幻影属性 |
| 脏数据解码 | 未知 `event_type` 严格抛错 / 宽松返回 reason；正常行直通；三类脏数据分类；一条脏数据不隐藏其余 |
| 输入适配 | ORM 行、Repository camelCase dict（含纯日期字符串）均可被扫描器消费 |
| **SQL 端到端** | sqlite 真实引擎：扫描落库 + 重扫幂等；`list_open` 过滤/排序/limit/project 过滤；`mark_handled` 后仍在 open；`resolve` 持久化且重读仍已解决；已解决不被复活；重扫保留 `occurred_at`/`handled`；`event_sources` 写入去重与**证据链回填**；低/高置信度解决全流程 |
| **保证金红线** | `manual_closed` 结案 `DEPOSIT_OVERDUE`/`DEPOSIT_REFUND_DUE`/`deposit_refund_review:*` 一律抛 `RefundConfirmationRequired` 且事件保持 open；`EventResolver.resolve_manual` 同样被拦；到账确认+凭证仍可关闭；项目到期的人工关闭不受影响 |
| **数据源装配** | 构造时配置 `project_source`/`bond_source` 后 `scan_all()` 无参可用；调用时传参可覆盖；显式传 `None` 仍抛错（不被配置兜住）；未配置且未传参 → 显式报错；`project_names_source` 生效 |
| **租户口径 B1** | org 可选、未传=读不过滤、写回落 `org_default`、空串视为未配置；SQL 断言未配置时 WHERE 无 `organization_id` 条件、配置后有 |
| **双实现一致性** | 同一套断言分别跑 `InMemoryEventStore` 与 `SqlAlchemyEventStore`：到账凭证必须落库；二次解决只许**追加**凭证、不许冲掉先前凭证（`sp_first` + `sp_second` 并集） |

## 10. 已知边界

* `SqlAlchemyEventStore` 已用 **sqlite 内存库 + 同一套 ORM 跑通端到端**
  （9 例，见测试分组「SQL 端到端」），覆盖 upsert 幂等、解决状态持久化、
  `mark_handled`/`list_open`/`resolve` 行为、`event_sources` 关联表读写。
  仍**未**在真实 PostgreSQL 上跑过（本机无 DB），PG 专属行为（部分索引、
  `NULLS LAST`、`jsonb_extract_path_text` 的执行计划）靠 PG 方言编译校验，
  上线前建议在真实 PG 上补一次冒烟；
* 测试用两个 sqlite 兼容 shim（`JSONB→JSON` DDL、注册 `jsonb_extract_path_text`
  函数），**只作用于测试进程，不改生产代码**；第二个 shim 是为了让风险最高的
  `list_open()` 能被真实执行而不是只靠"编译通过"担保；
* `event_sources` 关联表用 `text()` 原生 SQL 读写（`models.py` 未定义该 ORM 类）；
* **证据链回填**：`source_pointer_ids` 存在 `event_sources` 关联表而非 `events` 列，
  因此 `SqlAlchemyEventStore` 的 `get / list_open / list_all / mark_handled /
  upsert / resolve` 都会回填该字段。缺了这步，UI 会显示"这条提醒没有任何来源"，
  违反 §12.1。repo-agent 的 `alerts()` 另有一套 `_load_event_sources`，两者并存但结果一致；
* **上游证据链当前无数据流入（跨模块缺口，2026-09-24 实测）**：扫描器**如实透传**
  证据（`event_scanner.py:219` 读 `source_pointer_ids`/`sourcePointerIds`，
  `:393-397` 读 `payment_source_pointer_id`/`refund_rule_source_pointer_id`），
  且已有测试证明只要上游给出该字段就会被带进事件（`test_event_store.py:307`
  断言 `sp_pay` 出现在 `events[0].source_pointer_ids`）。
  **但两条数据入口都不返回任何 pointer id**：`PostgresRepository.results()`
  （`:407-424`）与 `bid_bonds()`（`:480-496`）均无该字段，`mock.json` 的
  `results`/`bidBonds` 同样没有 —— 两模式同构缺失，属契约级问题。
  实测 `POST /api/events/scan` 后 5 条事件**全部** `sourceIds=[]`。
  结论：§12.1/§12.3「提醒必须带原始证据 + 原表位置」**当前结构性不可达**；
  本文档描述的证据链落库/回填/并集逻辑**下游全部正确**，只是上游没有数据。
  目前全系统唯一真正流动的凭证是"到账确认"时人工传入的 `refund_source_pointer_id`。
  另注：`mock.json` 的 `opportunities[].sources` **确有**真实 id
  （`src-official-1` 等），但**没有任何事件引用**，是一条未接线的平行数据；
* **`sourcesResolved` 空集真空为真（`main.py:191`）**：其实现为
  `len(sources) == len(ids)`，`ids=[]` 时 `0 == 0` → `True`。于是"零证据"
  被渲染成"证据已解析"，与 §12.1 相反。且 `_mock_sources()`（`main.py:151-168`）
  只从 `mock.json` 构建，真实库模式下必然恒 `False`。该字段在 `main.py`（非本文档
  所属文件），已报 team-lead；消费方应改用三态判断（`sourceIds` 为空 / 部分未解析 /
  全部解析），不要依赖单一布尔；
* **双实现偏差（已修，2026-09-24）**：`apply_resolution()` 是两种实现共用的收口点，
  凭证并集语义已下沉到该函数；此前并集只写在 `SqlAlchemyEventStore._resolve`，
  导致 `InMemoryEventStore._resolve` 把 `source_pointer_ids` 整个丢掉 —— 而无库
  演示环境用的正是 InMemory，等于 §3.2 要求的到账凭证**静默消失**（事件关掉了、
  凭证查不到）。同时 `SqlAlchemyEventStore._resolve` 读 `current` 时未回填证据链，
  使"二次解决并集"的左半边为空。两处均已修，并由测试分组「双实现一致性」锁死。
  教训：**同一语义写在两个实现里，必然漂移**；原证据链用例只覆盖了真库版，所以没拦住；
* `resolve()` 目前允许对**已解决**事件再次解决，会覆盖 `resolution`/`resolved_by`/
  `resolution_note`（`resolved_at` 同步更新），审计日志仍保留 `before` 快照可追溯。
  是否改为"已解决即拒绝"属产品口径，待 team-lead 裁定；当前不做静默改变；
* `events.organization_id` 非空，真实模式必须先执行
  `infra/postgres/init/005_seed_default_organization.sql`，否则写入外键违例；
* **租户口径（team-lead 裁定 B1，已实现）**：
  * 读：`SqlAlchemyEventStore(organization_id=None)` → **不加租户过滤**（单租户原型
    行为），与 `InMemoryEventStore`（无 org 概念）行为一致；传了则按该 org 过滤；
  * 写：`events.organization_id` 是 `NOT NULL`，未传时回落到
    `DEFAULT_ORGANIZATION_ID`（`org_default`），由 005 seed 保证该行存在；
  * ⚠️ `postgres_repository.py` 读侧**始终不加** org 过滤（与「未配置=不过滤」一致），
    因此当前两层行为一致；将来真要上多租户，需两侧同时改为「必传 org」。
* `settings.review_confidence_threshold`（0.78）已由 container 显式传给
  `EventResolver`；模块内默认 0.7 仅作兜底。
* `repository.results()` **不含** `expiryWatchResolved`；「已解决不再出现」由事件自身
  `resolved_at` 保证（store 不会复活已解决事件），不依赖该字段。
