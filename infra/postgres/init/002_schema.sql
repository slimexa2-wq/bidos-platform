-- BidOS core schema v0.3
-- Business tables remain intentionally lean; original Excel/Feishu sources can stay authoritative.

CREATE TABLE IF NOT EXISTS organizations (
    id text PRIMARY KEY,
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
    id text PRIMARY KEY,
    organization_id text NOT NULL REFERENCES organizations(id),
    display_name text NOT NULL,
    role text NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS projects (
    id text PRIMARY KEY,
    organization_id text NOT NULL REFERENCES organizations(id),
    name text NOT NULL,
    client_name text,
    region text,
    project_type text,
    workflow_stage text,
    status text,
    budget numeric(18,2),
    deadline timestamptz,
    expires_at timestamptz,
    project_end_at timestamptz,
    project_end_date_kind text CHECK (project_end_date_kind IN ('exact','estimated','manual')),
    expiry_watch_resolved boolean NOT NULL DEFAULT false,
    owner_user_id text REFERENCES users(id),
    source_of_truth text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS entities (
    id text PRIMARY KEY,
    organization_id text NOT NULL REFERENCES organizations(id),
    entity_type text NOT NULL CHECK (entity_type IN ('client','competitor','agency','company')),
    canonical_name text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS entity_aliases (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id text NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    alias text NOT NULL,
    source_id text,
    UNIQUE(entity_id, alias)
);

CREATE TABLE IF NOT EXISTS project_entities (
    project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    entity_id text NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    relation_type text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY(project_id, entity_id, relation_type)
);

CREATE TABLE IF NOT EXISTS source_objects (
    id text PRIMARY KEY,
    organization_id text NOT NULL REFERENCES organizations(id),
    source_id text NOT NULL,
    external_id text,
    source_type text NOT NULL,
    title text,
    source_url text,
    content_hash text,
    published_at timestamptz,
    captured_at timestamptz NOT NULL DEFAULT now(),
    normalized_text text,
    raw_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE(source_id, external_id)
);

CREATE TABLE IF NOT EXISTS source_pointers (
    id text PRIMARY KEY,
    source_object_id text REFERENCES source_objects(id) ON DELETE CASCADE,
    project_id text REFERENCES projects(id) ON DELETE SET NULL,
    entity_id text REFERENCES entities(id) ON DELETE SET NULL,
    source_name text NOT NULL,
    source_type text NOT NULL,
    trust_level text NOT NULL,
    source_url text,
    deep_link text,
    file_id text,
    file_name text,
    sheet_name text,
    row_id text,
    cell_range text,
    page_number integer,
    section text,
    block_id text,
    record_id text,
    original_text text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS document_chunks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_pointer_id text NOT NULL REFERENCES source_pointers(id) ON DELETE CASCADE,
    project_id text REFERENCES projects(id) ON DELETE SET NULL,
    chunk_index integer NOT NULL,
    content text NOT NULL,
    page_number integer,
    section text,
    embedding vector(1024),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(source_pointer_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS events (
    id text PRIMARY KEY,
    organization_id text NOT NULL REFERENCES organizations(id),
    event_type text NOT NULL,
    severity text NOT NULL,
    title text NOT NULL,
    detail text,
    project_id text REFERENCES projects(id) ON DELETE SET NULL,
    entity_id text REFERENCES entities(id) ON DELETE SET NULL,
    occurred_at timestamptz NOT NULL,
    due_at timestamptz,
    handled boolean NOT NULL DEFAULT false,
    persistent boolean NOT NULL DEFAULT false,
    resolution_key text,
    resolution_hint text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS event_sources (
    event_id text NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    source_pointer_id text NOT NULL REFERENCES source_pointers(id) ON DELETE CASCADE,
    PRIMARY KEY(event_id, source_pointer_id)
);

CREATE TABLE IF NOT EXISTS source_health (
    source_id text PRIMARY KEY,
    source_name text NOT NULL,
    strategy text NOT NULL,
    last_success_at timestamptz,
    last_checked_at timestamptz,
    consecutive_failures integer NOT NULL DEFAULT 0,
    items_found integer NOT NULL DEFAULT 0,
    requires_login boolean NOT NULL DEFAULT false,
    human_action_required boolean NOT NULL DEFAULT false,
    last_error text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);


CREATE TABLE IF NOT EXISTS bid_bond_ledger (
    id text PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    bid_section text,
    amount numeric(18,2) NOT NULL CHECK (amount >= 0),
    paid_at timestamptz,
    result_published_at timestamptz,
    expected_refund_at timestamptz,
    actual_refund_at timestamptz,
    status text NOT NULL CHECK (status IN ('未缴纳','已缴纳','待退回','逾期未退','已退回','无需退回')),
    owner_user_id text REFERENCES users(id),
    payment_source_pointer_id text REFERENCES source_pointers(id) ON DELETE SET NULL,
    refund_rule_source_pointer_id text REFERENCES source_pointers(id) ON DELETE SET NULL,
    refund_source_pointer_id text REFERENCES source_pointers(id) ON DELETE SET NULL,
    note text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS human_decisions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    decision_type text NOT NULL,
    decision text NOT NULL,
    note text,
    user_id text REFERENCES users(id),
    evidence_pointer_ids text[] NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS feedback (
    id text PRIMARY KEY,
    organization_id text NOT NULL REFERENCES organizations(id),
    object_type text NOT NULL,
    object_id text NOT NULL,
    ai_output text NOT NULL,
    corrected_output text NOT NULL,
    reason text NOT NULL,
    evidence_pointer_ids text[] NOT NULL DEFAULT '{}',
    user_id text REFERENCES users(id),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id text PRIMARY KEY,
    organization_id text NOT NULL REFERENCES organizations(id),
    action text NOT NULL,
    actor_id text,
    object_type text NOT NULL,
    object_id text NOT NULL,
    before_state jsonb,
    after_state jsonb,
    source_pointer_ids text[] NOT NULL DEFAULT '{}',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_projects_deadline ON projects(deadline);
CREATE INDEX IF NOT EXISTS idx_projects_expires_at ON projects(expires_at);
CREATE INDEX IF NOT EXISTS idx_projects_project_end_at ON projects(project_end_at);
CREATE INDEX IF NOT EXISTS idx_projects_client_trgm ON projects USING gin (client_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_projects_name_trgm ON projects USING gin (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_entities_name_trgm ON entities USING gin (canonical_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_source_text_trgm ON source_objects USING gin (normalized_text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_events_project_time ON events(project_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_open ON events(handled, severity, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_chunks_project ON document_chunks(project_id);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw ON document_chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_bid_bond_status_due ON bid_bond_ledger(status, expected_refund_at);
CREATE INDEX IF NOT EXISTS idx_bid_bond_project ON bid_bond_ledger(project_id);
