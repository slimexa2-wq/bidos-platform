-- 006_bid_bond_refund_audit.sql
-- 交接文档 §2.8 台账凭证链：保证金到账确认必须记录 操作人 / 时间 / 凭证来源。
-- 向后兼容：只 ADD COLUMN，绝不修改 002 已有 DDL；可重复执行（幂等）。
-- 凭证来源复用已有的 refund_source_pointer_id 列（002 已建），此处只补审计两列。

ALTER TABLE bid_bond_ledger ADD COLUMN IF NOT EXISTS refund_confirmed_by text;
ALTER TABLE bid_bond_ledger ADD COLUMN IF NOT EXISTS refund_confirmed_at timestamptz;
