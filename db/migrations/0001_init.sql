-- decision-trace: 初期スキーマ（設計書 §3、v3）。
--
-- すべての表で RLS を有効にし、ポリシーは1つも作らない
-- （＝ Supabase の匿名キー・ログインユーザーのキーからは何も読めない。spec S3）。
-- worker は専用ロール app_worker（BYPASSRLS）で接続する（§3.3）。
--
-- auth.users（Supabase Auth）への外部キーはここでは張らない。auth スキーマは
-- Supabase 側が管理しており、このマイグレーションは素の Postgres（CI・ローカルの
-- テスト用 DB を含む）にもそのまま適用できる必要があるため（S10：Supabase 固有の
-- 機能への依存はログインとファイル置き場の2箇所に閉じ込める）。

-- ============================================================
-- projects
-- ============================================================
CREATE TABLE projects (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    goal_description text NOT NULL,
    readme_markdown text,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed')),
    no_progress_threshold_hours integer NOT NULL DEFAULT 24,
    exclude_weekends boolean NOT NULL DEFAULT false,
    next_source_no integer NOT NULL DEFAULT 1,
    next_event_no integer NOT NULL DEFAULT 1,
    next_version_no integer NOT NULL DEFAULT 1,
    needs_rebuild boolean NOT NULL DEFAULT false,
    visibility_epoch integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE projects ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- project_members
-- ============================================================
CREATE TABLE project_members (
    project_id uuid NOT NULL REFERENCES projects (id),
    user_id uuid NOT NULL,
    email text NOT NULL,
    role text NOT NULL CHECK (role IN ('member', 'manager')),
    notify_on_progress boolean NOT NULL DEFAULT true,
    notify_on_no_progress boolean NOT NULL DEFAULT true,
    PRIMARY KEY (project_id, user_id)
);

ALTER TABLE project_members ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- project_sources
-- ============================================================
CREATE TABLE project_sources (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects (id),
    source_no integer NOT NULL,
    type text NOT NULL CHECK (type IN ('file', 'conversation', 'answer')),
    filename text,
    uploaded_by uuid NOT NULL,
    recorded_at timestamptz NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('all', 'managers_only')),
    is_excluded boolean NOT NULL DEFAULT false,
    exclude_reason text CHECK (exclude_reason IN ('private', 'confidential', 'irrelevant', 'other')),
    current_version_id uuid,
    next_seq integer NOT NULL DEFAULT 1,
    updated_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, source_no)
);

-- 「同じファイル名」は同じ登録者のものだけを指す【C-3】。
CREATE UNIQUE INDEX project_sources_unique_file_per_uploader
    ON project_sources (project_id, uploaded_by, filename)
    WHERE type = 'file';

ALTER TABLE project_sources ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- source_versions（追記専用）
-- ============================================================
CREATE TABLE source_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id uuid NOT NULL REFERENCES project_sources (id),
    version_no integer NOT NULL,
    content_hash text NOT NULL,
    extracted_text text NOT NULL,
    storage_path text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, version_no)
);

ALTER TABLE source_versions ENABLE ROW LEVEL SECURITY;

-- project_sources.current_version_id は source_versions が存在してから張る
-- （取り込みの1トランザクションの中で、版を作った後に更新する）。
ALTER TABLE project_sources
    ADD CONSTRAINT project_sources_current_version_id_fkey
    FOREIGN KEY (current_version_id) REFERENCES source_versions (id);

-- ============================================================
-- source_segments（consumed・carry_count 以外は変更禁止）
-- ============================================================
CREATE TABLE source_segments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects (id),
    source_version_id uuid NOT NULL REFERENCES source_versions (id),
    label text NOT NULL,
    seq integer NOT NULL,
    speaker text CHECK (speaker IN ('user', 'ai', 'unknown')),
    text text NOT NULL,
    locator text,
    is_new boolean NOT NULL,
    consumed boolean NOT NULL DEFAULT false,
    carry_count integer NOT NULL DEFAULT 0,
    UNIQUE (project_id, label)
);

ALTER TABLE source_segments ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- events（追記専用）
-- ============================================================
CREATE TABLE runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects (id),
    trigger text NOT NULL CHECK (trigger IN ('manual', 'schedule')),
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
    step text CHECK (
        step IN ('extracting', 'investigating', 'assembling', 'checking', 'notifying', 'done')
    ),
    outcome text CHECK (
        outcome IN ('report_created', 'no_new_events', 'locked', 'cost_limited', 'failed')
    ),
    error_message text,
    started_at timestamptz,
    finished_at timestamptz,
    created_by uuid
);

ALTER TABLE runs ENABLE ROW LEVEL SECURITY;

CREATE TABLE events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects (id),
    run_id uuid NOT NULL REFERENCES runs (id),
    event_no integer NOT NULL,
    kind text NOT NULL CHECK (kind IN ('decision', 'rejected_option', 'open_issue', 'finding', 'status')),
    summary text NOT NULL,
    reason text,
    occurred_at timestamptz NOT NULL,
    segment_ids text[] NOT NULL DEFAULT '{}',
    origin text CHECK (origin IN ('human_originated', 'ai_verified', 'ai_unverified', 'document')),
    visibility_at_creation text NOT NULL CHECK (visibility_at_creation IN ('all', 'managers_only')),
    support text CHECK (support IN ('supported', 'partial', 'unsupported')),
    supersedes_event_id uuid REFERENCES events (id),
    partition text NOT NULL CHECK (partition IN ('all', 'managers')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, event_no)
);

ALTER TABLE events ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- reports（追記専用）
-- ============================================================
CREATE TABLE reports (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects (id),
    run_id uuid NOT NULL REFERENCES runs (id),
    version_no integer NOT NULL,
    audience text NOT NULL CHECK (audience IN ('all', 'managers')),
    kind text NOT NULL CHECK (kind IN ('diff', 'baseline')),
    judge_status text NOT NULL CHECK (judge_status IN ('pass', 'flagged')),
    body_markdown text NOT NULL,
    mermaid_dsl text,
    evidence_catalog jsonb NOT NULL DEFAULT '{}'::jsonb,
    event_ids integer[] NOT NULL DEFAULT '{}',
    cited_segment_ids text[] NOT NULL DEFAULT '{}',
    input_segment_ids text[] NOT NULL DEFAULT '{}',
    flags jsonb NOT NULL DEFAULT '{}'::jsonb,
    summary_for_mail text NOT NULL,
    generated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, version_no, audience)
);

ALTER TABLE reports ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- agent_questions（status・answer_source_id 以外は変更禁止）
-- ============================================================
CREATE TABLE agent_questions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects (id),
    run_id uuid NOT NULL REFERENCES runs (id),
    asked_to uuid NOT NULL,
    question text NOT NULL,
    related_event_id uuid NOT NULL REFERENCES events (id),
    partition text NOT NULL CHECK (partition IN ('all', 'managers')),
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'answered', 'expired')),
    answer_source_id uuid REFERENCES project_sources (id),
    created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE agent_questions ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- notifications
-- ============================================================
CREATE TABLE notifications (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    project_id uuid NOT NULL REFERENCES projects (id),
    kind text NOT NULL CHECK (kind IN ('question', 'report', 'no_progress')),
    title text NOT NULL,
    question_id uuid REFERENCES agent_questions (id),
    created_at timestamptz NOT NULL DEFAULT now(),
    read_at timestamptz
);

ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- notifications_log（追記専用）
-- ============================================================
CREATE TABLE notifications_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects (id),
    report_id uuid REFERENCES reports (id),
    kind text NOT NULL CHECK (kind IN ('question', 'report', 'no_progress')),
    recipients text[] NOT NULL,
    sent_at timestamptz NOT NULL DEFAULT now(),
    provider_message_id text
);

ALTER TABLE notifications_log ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- source_audit_log（追記専用）
-- ============================================================
CREATE TABLE source_audit_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id uuid NOT NULL REFERENCES project_sources (id),
    changed_by uuid NOT NULL,
    changed_at timestamptz NOT NULL DEFAULT now(),
    field text NOT NULL CHECK (field IN ('visibility', 'is_excluded')),
    old_value text,
    new_value text,
    reason text NOT NULL CHECK (reason IN ('private', 'confidential', 'irrelevant', 'other'))
);

ALTER TABLE source_audit_log ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- model_calls_log（追記専用）
-- ============================================================
CREATE TABLE model_calls_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects (id),
    run_id uuid NOT NULL REFERENCES runs (id),
    stage text NOT NULL CHECK (stage IN ('EXTRACT', 'SUPPORT', 'AGENT', 'ASSEMBLE', 'JUDGE')),
    model text NOT NULL,
    input_tokens integer NOT NULL,
    output_tokens integer NOT NULL,
    estimated_cost_usd numeric NOT NULL,
    latency_ms integer NOT NULL,
    ok boolean NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE model_calls_log ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- daily_cost_alerts / daily_costs
-- ============================================================
CREATE TABLE daily_cost_alerts (
    alert_date date PRIMARY KEY,
    triggered_at timestamptz NOT NULL DEFAULT now(),
    notified boolean NOT NULL DEFAULT false
);

ALTER TABLE daily_cost_alerts ENABLE ROW LEVEL SECURITY;

CREATE TABLE daily_costs (
    cost_date date PRIMARY KEY,
    reserved_usd numeric NOT NULL DEFAULT 0,
    spent_usd numeric NOT NULL DEFAULT 0
);

ALTER TABLE daily_costs ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- no_progress_alerts
-- ============================================================
CREATE TABLE no_progress_alerts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects (id),
    audience text NOT NULL CHECK (audience IN ('all', 'managers')),
    since timestamptz NOT NULL,
    sent_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, audience, since)
);

ALTER TABLE no_progress_alerts ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- 追記専用の強制（I3、設計書 §3.2）
-- ============================================================
CREATE FUNCTION forbid_update_delete() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_events_append_only
    BEFORE UPDATE OR DELETE ON events
    FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();

CREATE TRIGGER trg_source_audit_log_append_only
    BEFORE UPDATE OR DELETE ON source_audit_log
    FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();

CREATE TRIGGER trg_reports_append_only
    BEFORE UPDATE OR DELETE ON reports
    FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();

CREATE TRIGGER trg_source_versions_append_only
    BEFORE UPDATE OR DELETE ON source_versions
    FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();

CREATE TRIGGER trg_notifications_log_append_only
    BEFORE UPDATE OR DELETE ON notifications_log
    FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();

CREATE TRIGGER trg_model_calls_log_append_only
    BEFORE UPDATE OR DELETE ON model_calls_log
    FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();

-- source_segments：consumed・carry_count 以外の変更禁止、削除も禁止。
CREATE FUNCTION guard_source_segments() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'source_segments rows cannot be deleted';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
        OR NEW.project_id IS DISTINCT FROM OLD.project_id
        OR NEW.source_version_id IS DISTINCT FROM OLD.source_version_id
        OR NEW.label IS DISTINCT FROM OLD.label
        OR NEW.seq IS DISTINCT FROM OLD.seq
        OR NEW.speaker IS DISTINCT FROM OLD.speaker
        OR NEW.text IS DISTINCT FROM OLD.text
        OR NEW.locator IS DISTINCT FROM OLD.locator
        OR NEW.is_new IS DISTINCT FROM OLD.is_new
    THEN
        RAISE EXCEPTION 'source_segments only consumed and carry_count may change';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_source_segments_guard
    BEFORE UPDATE OR DELETE ON source_segments
    FOR EACH ROW EXECUTE FUNCTION guard_source_segments();

-- agent_questions：status・answer_source_id 以外の変更禁止、削除も禁止。
CREATE FUNCTION guard_agent_questions() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'agent_questions rows cannot be deleted';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
        OR NEW.project_id IS DISTINCT FROM OLD.project_id
        OR NEW.run_id IS DISTINCT FROM OLD.run_id
        OR NEW.asked_to IS DISTINCT FROM OLD.asked_to
        OR NEW.question IS DISTINCT FROM OLD.question
        OR NEW.related_event_id IS DISTINCT FROM OLD.related_event_id
        OR NEW.partition IS DISTINCT FROM OLD.partition
        OR NEW.created_at IS DISTINCT FROM OLD.created_at
    THEN
        RAISE EXCEPTION 'agent_questions only status and answer_source_id may change';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_agent_questions_guard
    BEFORE UPDATE OR DELETE ON agent_questions
    FOR EACH ROW EXECUTE FUNCTION guard_agent_questions();

-- ============================================================
-- app_worker ロール（設計書 §3、§3.2）
-- ============================================================
-- NOLOGIN で作成する。ログイン用パスワードは運用環境で
--   ALTER ROLE app_worker WITH LOGIN PASSWORD '...';
-- のように別途設定する（このマイグレーションには含めない）。
-- BYPASSRLS を付け、RLS が有効な表にも worker からは通常どおりアクセスできるようにする
-- （RLS が防ぐのは Supabase の匿名キー・ログインユーザーのキー経由のアクセス）。
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_worker') THEN
        CREATE ROLE app_worker NOLOGIN BYPASSRLS;
    END IF;
END
$$;

COMMENT ON ROLE app_worker IS
    'worker 専用ロール。NOLOGIN で作成。ログイン用パスワードは運用環境で ALTER ROLE ... WITH LOGIN PASSWORD ... により別途設定する。';

GRANT USAGE ON SCHEMA public TO app_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_worker;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_worker;

-- 追記専用の表からは UPDATE・DELETE・TRUNCATE を REVOKE する（トリガーとの二重防御）。
REVOKE UPDATE, DELETE, TRUNCATE ON
    events,
    source_audit_log,
    reports,
    source_versions,
    notifications_log,
    model_calls_log
FROM app_worker;

-- 決めた列以外の変更を禁止する表からは、DELETE・TRUNCATE を REVOKE する
-- （UPDATE 自体はトリガーが列単位で制限する）。
REVOKE DELETE, TRUNCATE ON source_segments, agent_questions FROM app_worker;
