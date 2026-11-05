-- BidOS v0.3.1 工作流承载表
--
-- 背景：002_schema.sql 只定义了「真相源索引 + 实体 + 事件 + 证据」，
-- 没有承载报名/评估/标书/结果这些**阶段明细**的表。真实 Excel 落库后
-- 前端各页面需要有地方读，因此这里补齐承载表。
--
-- 铁律（交接文档 §1.2 / §14）：
--   1. 不改动 002 已有表结构，只新增，向后兼容；
--   2. 项目结束日期的权威字段仍然是 projects.project_end_at，
--      本文件**不重复存** project_end_at，避免双写冲突；
--   3. 所有明细都保留 metadata，原始来源一律走 source_pointers。

CREATE TABLE IF NOT EXISTS project_results (
    id text PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    result text NOT NULL,                       -- 中标 / 未中标 / 放弃
    winner text,
    winning_price numeric(18,2),
    our_price numeric(18,2),
    reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
    lessons jsonb NOT NULL DEFAULT '[]'::jsonb,
    reviewed boolean NOT NULL DEFAULT false,
    result_date date,
    expiry_resolved boolean NOT NULL DEFAULT false,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS registrations (
    project_id text PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    progress integer NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    deadline_label text,
    status text,
    materials jsonb NOT NULL DEFAULT '[]'::jsonb,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bid_documents (
    project_id text PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    progress integer NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    due_label text,
    sections jsonb NOT NULL DEFAULT '[]'::jsonb,
    issues jsonb NOT NULL DEFAULT '[]'::jsonb,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS project_evaluations (
    project_id text PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    score integer,
    ai_view text,
    human_decision text,
    human_note text,
    evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    similar_projects jsonb NOT NULL DEFAULT '[]'::jsonb,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS competitor_stats (
    entity_id text PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,
    regions jsonb NOT NULL DEFAULT '[]'::jsonb,
    project_count integer NOT NULL DEFAULT 0,
    won_count integer NOT NULL DEFAULT 0,
    avg_quote_ratio numeric(8,4),
    activity text,
    specialties jsonb NOT NULL DEFAULT '[]'::jsonb,
    expiring_projects integer NOT NULL DEFAULT 0,
    recent_trend jsonb NOT NULL DEFAULT '[]'::jsonb,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- 每个项目的来源证据视图：避免每次在应用层 join 三张表
CREATE OR REPLACE VIEW v_project_sources AS
SELECT
    sp.id,
    sp.project_id,
    sp.source_type,
    sp.source_name,
    sp.trust_level,
    sp.source_url,
    sp.deep_link,
    sp.file_name,
    sp.sheet_name,
    sp.row_id,
    sp.cell_range,
    sp.page_number,
    sp.section,
    sp.record_id,
    sp.original_text,
    sp.created_at
FROM source_pointers sp
WHERE sp.project_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_project_results_project ON project_results(project_id);
CREATE INDEX IF NOT EXISTS idx_project_results_date ON project_results(result_date DESC);
CREATE INDEX IF NOT EXISTS idx_registrations_status ON registrations(status);
CREATE INDEX IF NOT EXISTS idx_bid_documents_progress ON bid_documents(progress);
CREATE INDEX IF NOT EXISTS idx_evaluations_decision ON project_evaluations(human_decision);
