-- BidOS v0.3.1 事件解决状态（已读 vs 已解决）
--
-- 背景：002_schema.sql 的 events 表只有 handled（已读），没有业务解决字段。
-- 交接文档 §3.1 / §12.5 与 docs/EXPIRY_AND_BID_BOND_RULES.md 的铁律是：
--
--     **已读 != 已解决。**
--
-- 项目到期 / 保证金这类持续事件，用户点开看过（handled=true）之后仍然必须
-- 留在首页时间轴，直到业务上真正解决（匹配到新公告 / 确认续签 / 确认到账 /
-- 人工关闭）。
--
-- ── 重要：本迁移是「可选优化」，不是事件引擎的前置条件 ─────────────────
--
-- 解决状态的**权威载体是 `events.metadata`（JSONB）**，例如：
--
--     {"resolved_at": "2026-09-24T08:00:00+00:00",
--      "resolution": "renewed", "resolved_by": "u_sales",
--      "resolution_note": "已与甲方确认续签"}
--
-- `app/services/event_store.py` 的 SqlAlchemyEventStore 完全基于 metadata 读写
-- 与过滤（`metadata->>'resolved_at' IS NULL` 即"未解决"），因此**不执行本文件
-- 事件引擎也照常工作**。本文件只做两件事：
--
--   1. 补三列，便于财务 / 审计用纯 SQL 直接查"谁在什么时候解决了什么"；
--   2. 补索引，让首页时间轴查询与幂等扫描走索引而不是全表扫。
--
-- 之所以不写回 ORM（`repositories/models.py`）：该文件由 repo-agent 负责，
-- 且 JSONB 已足够。若将来 ORM 映射了这三列，`apply_event_to_row()` 会通过
-- `hasattr` 探测自动开始双写，无需修改任何代码。
--
-- 铁律（交接文档 §1.2 / §14）：
--   1. 只 ADD COLUMN / ADD INDEX，不改动 002 已有列，向后兼容；
--   2. 老数据 resolved_at 为 NULL，语义上等价于"未解决"，行为与升级前一致；
--   3. resolution 取值与 app/services/event_resolver.py 常量一致：
--        new_tender_matched / renewed / refund_confirmed / manual_closed。

-- ── 1. 可选的留痕列 ────────────────────────────────────────────────────
ALTER TABLE events ADD COLUMN IF NOT EXISTS resolved_at timestamptz;
ALTER TABLE events ADD COLUMN IF NOT EXISTS resolution text;
ALTER TABLE events ADD COLUMN IF NOT EXISTS resolved_by text;

COMMENT ON COLUMN events.handled IS
    '仅表示"人看过"。已读 != 已解决：handled=true 且未解决的事件仍须出现在首页时间轴。';
COMMENT ON COLUMN events.resolved_at IS
    '可选的解决时间留痕列。权威值在 events.metadata->>''resolved_at''；NULL = 未解决。';
COMMENT ON COLUMN events.resolution IS
    '可选留痕列。new_tender_matched / renewed / refund_confirmed / manual_closed。';
COMMENT ON COLUMN events.resolved_by IS
    '可选留痕列。解决操作人；到账凭证来源见 event_sources。';

-- ── 2. 索引：全部建立在 metadata JSONB 上，不依赖上面三列 ──────────────
-- 首页时间轴：未解决事件按 severity + due_at 取。
CREATE INDEX IF NOT EXISTS idx_events_open_severity_due
    ON events (severity, due_at)
    WHERE metadata->>'resolved_at' IS NULL;

-- 幂等扫描：按 resolution_key 查已有持续事件。
CREATE INDEX IF NOT EXISTS idx_events_resolution_key
    ON events (resolution_key)
    WHERE resolution_key IS NOT NULL;

-- 解决留痕查询（metadata 路径）。
CREATE INDEX IF NOT EXISTS idx_events_resolved_at
    ON events ((metadata->>'resolved_at'))
    WHERE metadata->>'resolved_at' IS NOT NULL;
