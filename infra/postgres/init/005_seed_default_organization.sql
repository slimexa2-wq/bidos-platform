-- BidOS v0.3.1 默认租户 seed
--
-- 为什么必须有这个文件：
--   002_schema.sql 里有 7 张表的 organization_id 是
--   `NOT NULL REFERENCES organizations(id)`：
--     users / projects / entities / source_objects / events / feedback / audit_logs
--   而 001~004 **没有任何一行 organizations 的 INSERT**。
--   结果是：事件引擎一旦切到 postgres 模式，每次扫描写 events 都会
--   **外键违例失败**，而 mock 模式完全不受影响 —— 典型的
--   "本地全绿、真库运行时挂"，直接打掉交接文档 §17 验收项 6 和 7
--   （90 天到期事件真实触发 / 保证金逾期事件真实触发）。
--
-- 注意：organizations.name 也是 NOT NULL，只插 id 照样失败，必须两个都给。

-- 真实数据导入用的是 org_xiangneng（import_excel.py 的 DEFAULT_ORG_ID）。
-- 只 seed org_default 的话，按本文件注释配置 BIDOS_ORGANIZATION_ID=org_default
-- 接真库会**整站静默空白**（opportunities/alerts 全 0 行且不报错）。
-- 两个租户 id 都保留，与导入侧实际写入的对齐。
INSERT INTO organizations (id, name)
VALUES
    ('org_default', '四川祥能人力资本服务有限公司'),
    ('org_xiangneng', '四川祥能人力资本服务有限公司')
ON CONFLICT (id) DO NOTHING;

-- 系统账号：供 users 外键场景兜底（projects.owner_user_id、
-- bid_bond_ledger.owner_user_id、human_decisions.user_id 等都 REFERENCES users）。
-- 真实部署应改为公司实际账号体系，并同步 BIDOS_ORGANIZATION_ID。
INSERT INTO users (id, organization_id, display_name, role, is_active)
VALUES
    ('u_system', 'org_default', '系统', 'system', true)
ON CONFLICT (id) DO NOTHING;
