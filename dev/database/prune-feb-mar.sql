\set ON_ERROR_STOP on

BEGIN;
SET LOCAL lock_timeout = '30s';
SET LOCAL statement_timeout = 0;

CREATE TEMP TABLE domeye_dev_context (
    release_id text NOT NULL,
    system_identifier text NOT NULL,
    checkpoint_key text NOT NULL,
    prune_sql_sha256 text NOT NULL,
    inventory_sha256 text NOT NULL,
    data_start timestamp without time zone NOT NULL,
    data_end_exclusive timestamp without time zone NOT NULL
) ON COMMIT DROP;

INSERT INTO domeye_dev_context(
    release_id,
    system_identifier,
    checkpoint_key,
    prune_sql_sha256,
    inventory_sha256,
    data_start,
    data_end_exclusive
) VALUES (
    :'release_id',
    :'system_identifier',
    :'checkpoint_key',
    :'prune_sql_sha256',
    :'inventory_sha256',
    TIMESTAMP :'data_start',
    TIMESTAMP :'data_end_exclusive'
);

DO $block$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'domeye_dev') THEN
        RAISE EXCEPTION 'domeye_dev schema 已存在，拒绝重复或跨版本裁剪';
    END IF;
END
$block$;

CREATE SCHEMA domeye_dev AUTHORIZATION CURRENT_USER;
REVOKE ALL ON SCHEMA domeye_dev FROM PUBLIC;

CREATE TABLE domeye_dev.prune_checkpoint (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    schema_version integer NOT NULL CHECK (schema_version = 1),
    release_id text NOT NULL,
    system_identifier text NOT NULL CHECK (system_identifier ~ '^[0-9]+$'),
    checkpoint_key text NOT NULL CHECK (checkpoint_key ~ '^[0-9a-f]{64}$'),
    prune_sql_sha256 text NOT NULL CHECK (prune_sql_sha256 ~ '^[0-9a-f]{64}$'),
    inventory_sha256 text NOT NULL CHECK (inventory_sha256 ~ '^[0-9a-f]{64}$'),
    data_start timestamp without time zone NOT NULL,
    data_end_exclusive timestamp without time zone NOT NULL,
    completed_at timestamp with time zone NOT NULL DEFAULT clock_timestamp(),
    CHECK (data_start = TIMESTAMP '2026-02-01 00:00:00'),
    CHECK (data_end_exclusive = TIMESTAMP '2026-04-01 00:00:00')
);
REVOKE ALL ON TABLE domeye_dev.prune_checkpoint FROM PUBLIC;

DO $block$
DECLARE
    candidate record;
    keep_table boolean;
BEGIN
    FOR candidate IN
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
        ORDER BY tablename
    LOOP
        keep_table := candidate.tablename = 'feature_country'
            OR candidate.tablename ~ '^(event_table|hijack|sub_hijack|leak_event|prefix_outage|as_outage|country_outage)_20260[23]$'
            OR candidate.tablename ~ '^feature_(other|us|br|cn|ru|in|gb|id|de|au|pl)_20260[23]$';

        IF NOT keep_table THEN
            EXECUTE format('DROP TABLE public.%I CASCADE', candidate.tablename);
        END IF;
    END LOOP;
END
$block$;

SELECT drop_chunks(
    relation => 'public.feature_country',
    older_than => TIMESTAMP '2026-02-01 00:00:00'
);
SELECT drop_chunks(
    relation => 'public.feature_country',
    newer_than => TIMESTAMP '2026-04-01 00:00:00'
);

-- 月表已由发布 inventory 证明时间范围与表后缀一致。这里只清理唯一跨月的
-- feature_country 边界 chunk，避免对 36 张大表执行重复 DELETE 全表扫描。
DELETE FROM public.feature_country
WHERE t < TIMESTAMP '2026-02-01 00:00:00'
   OR t >= TIMESTAMP '2026-04-01 00:00:00';

DO $block$
DECLARE
    candidate record;
BEGIN
    IF to_regclass('timescaledb_information.jobs') IS NULL THEN
        RETURN;
    END IF;

    FOR candidate IN
        SELECT hypertable_schema, hypertable_name
        FROM timescaledb_information.jobs
        WHERE proc_name = 'policy_retention'
          AND hypertable_schema = 'public'
    LOOP
        PERFORM remove_retention_policy(
            format('%I.%I', candidate.hypertable_schema, candidate.hypertable_name),
            if_exists => true
        );
    END LOOP;
END
$block$;

INSERT INTO domeye_dev.prune_checkpoint(
    singleton,
    schema_version,
    release_id,
    system_identifier,
    checkpoint_key,
    prune_sql_sha256,
    inventory_sha256,
    data_start,
    data_end_exclusive
)
SELECT
    true,
    1,
    release_id,
    system_identifier,
    checkpoint_key,
    prune_sql_sha256,
    inventory_sha256,
    data_start,
    data_end_exclusive
FROM domeye_dev_context;

COMMIT;

SELECT jsonb_build_object(
    'checkpoint_key', checkpoint_key,
    'completed_at', completed_at,
    'data_start', data_start,
    'data_end_exclusive', data_end_exclusive
)::text
FROM domeye_dev.prune_checkpoint
WHERE singleton;
