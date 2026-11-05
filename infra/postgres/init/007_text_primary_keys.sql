-- BidOS v0.3.1 修复：三张表的主键类型与全库「确定性 hash」口径不一致
--
-- 问题（2026-09-28 实测）：
--   002_schema.sql 里这三张表把主键声明成
--       id uuid PRIMARY KEY DEFAULT gen_random_uuid()
--   而同一份 DDL 里 projects / entities / events / source_pointers / feedback /
--   audit_logs 等表的主键都是 text，ORM（models.py）也一律声明 String。
--
--   更关键的是这违反项目自己的主键纪律 ——
--   scripts/import_excel.py:13「所有主键都是**确定性 hash**（不是随机 UUID）」。
--   业务侧生成的 id 形如 `bidos_ent_<sha1>` / `bidos_alias_<sha1>` / `hd_<hex>`（text），
--   往 uuid 列写会直接报
--       DataError: invalid input syntax for type uuid: "bidos_alias_5cedaddb..."
--
--   实际后果（都是「表恒为 0 行」的真因，不是没人调用）：
--     * entity_aliases   → 别名归并通道完全写不进去，错别字变体无法归并；
--     * human_decisions  → 人工结论无法落库（P4「摘要评估/人工结论」缺口）；
--     * document_chunks  → 文档分块无法落库（P4「招标文件解析」缺口）。
--
-- 处置：把三张表的主键改为 text，并去掉随机 UUID 默认值。
--   三张表当前均为 0 行，转换无数据风险；本文件幂等，可重复执行。
--
-- 说明：按项目既有约定「迁移只增不改」，本文件不改 002，而是追加修正，
--   保证已存在的库与新建的库最终 schema 一致。

DO $$
DECLARE
    target text;
BEGIN
    FOREACH target IN ARRAY ARRAY['entity_aliases', 'human_decisions', 'document_chunks']
    LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = target
              AND column_name = 'id'
              AND data_type = 'uuid'
        ) THEN
            EXECUTE format('ALTER TABLE %I ALTER COLUMN id DROP DEFAULT', target);
            EXECUTE format('ALTER TABLE %I ALTER COLUMN id TYPE text USING id::text', target);
        END IF;
    END LOOP;
END
$$;
