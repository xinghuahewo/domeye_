\set ON_ERROR_STOP on

CREATE TEMP TABLE domeye_discarded_malformed_event (
    month text NOT NULL,
    event_type text NOT NULL,
    row_count bigint NOT NULL,
    PRIMARY KEY(month, event_type)
);

SET domeye.data_start TO :'data_start';
SET domeye.snapshot_local TO :'snapshot_local';
SET domeye.snapshot_month TO :'snapshot_month';

DO $block$
DECLARE
    candidate record;
    keep_table boolean;
    month_suffix text;
    start_month text := to_char(current_setting('domeye.data_start')::timestamp, 'YYYYMM');
    end_month text := current_setting('domeye.snapshot_month');
BEGIN
    FOR candidate IN
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
        ORDER BY tablename
    LOOP
        keep_table := candidate.tablename = 'feature_country';

        IF candidate.tablename ~ '^(event_table|hijack|sub_hijack|leak_event|prefix_outage|as_outage|country_outage)_[0-9]{6}$' THEN
            month_suffix := right(candidate.tablename, 6);
            keep_table := month_suffix BETWEEN start_month AND end_month;
        ELSIF candidate.tablename ~ '^feature_(other|us|br|cn|ru|in|gb|id|de|au|pl)_[0-9]{6}$' THEN
            month_suffix := right(candidate.tablename, 6);
            keep_table := month_suffix BETWEEN start_month AND end_month;
        END IF;

        IF NOT keep_table THEN
            EXECUTE format('DROP TABLE public.%I CASCADE', candidate.tablename);
        END IF;
    END LOOP;
END
$block$;

DO $block$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE relation.relkind IN ('r', 'p', 'v', 'm')
          AND namespace.nspname NOT IN ('public', 'info')
          AND namespace.nspname NOT LIKE 'pg_%'
          AND namespace.nspname <> 'information_schema'
          AND namespace.nspname NOT IN (
              '_timescaledb_catalog', '_timescaledb_config',
              '_timescaledb_internal', '_timescaledb_cache',
              'timescaledb_information', 'timescaledb_experimental'
          )
    ) THEN
        RAISE EXCEPTION '候选库存在 public/info 之外的未授权用户 schema';
    END IF;
END
$block$;

DO $block$
DECLARE
    family text;
    current_table text;
    template_table text;
    end_month text := current_setting('domeye.snapshot_month');
BEGIN
    FOREACH family IN ARRAY ARRAY[
        'event_table',
        'hijack',
        'sub_hijack',
        'leak_event',
        'prefix_outage',
        'as_outage',
        'country_outage'
    ]
    LOOP
        current_table := family || '_' || end_month;
        IF to_regclass(format('public.%I', current_table)) IS NULL THEN
            SELECT tablename
            INTO template_table
            FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename ~ ('^' || family || '_[0-9]{6}$')
              AND right(tablename, 6) < end_month
            ORDER BY right(tablename, 6) DESC
            LIMIT 1;

            IF template_table IS NULL THEN
                RAISE EXCEPTION '无法为 % 创建当月空表：缺少历史模板', family;
            END IF;
            EXECUTE format(
                'CREATE TABLE public.%I (LIKE public.%I INCLUDING ALL)',
                current_table,
                template_table
            );
        END IF;
    END LOOP;
END
$block$;

DO $block$
DECLARE
    candidate record;
BEGIN
    FOR candidate IN
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename ~ '^event_table_[0-9]{6}$'
        ORDER BY tablename
    LOOP
        EXECUTE format($query$
            INSERT INTO domeye_discarded_malformed_event(month, event_type, row_count)
            SELECT %L, split_part(detail_url, '/', 1), count(*)
            FROM public.%I
            WHERE split_part(detail_url, '/', 1) IN (
                    'hijack', 'sub_hijack', 'leak',
                    'prefix_outage', 'as_outage', 'country_outage'
                  )
              AND s_time >= current_setting('domeye.data_start')::timestamp
              AND s_time <= current_setting('domeye.snapshot_local')::timestamp
              AND (
                    cardinality(string_to_array(detail_url, '/')) <> 5
                    OR split_part(detail_url, '/', 3) = ''
                    OR split_part(detail_url, '/', 4) !~ '^[0-9]+$'
                    OR split_part(detail_url, '/', 5) = ''
                  )
            GROUP BY split_part(detail_url, '/', 1)
        $query$, right(candidate.tablename, 6), candidate.tablename);

        EXECUTE format($query$
            DELETE FROM public.%I
            WHERE split_part(detail_url, '/', 1) IN (
                    'hijack', 'sub_hijack', 'leak',
                    'prefix_outage', 'as_outage', 'country_outage'
                  )
              AND s_time >= current_setting('domeye.data_start')::timestamp
              AND s_time <= current_setting('domeye.snapshot_local')::timestamp
              AND (
                    cardinality(string_to_array(detail_url, '/')) <> 5
                    OR split_part(detail_url, '/', 3) = ''
                    OR split_part(detail_url, '/', 4) !~ '^[0-9]+$'
                    OR split_part(detail_url, '/', 5) = ''
                  )
        $query$, candidate.tablename);
    END LOOP;
END
$block$;

DO $block$
DECLARE
    candidate record;
    time_column text;
    data_start timestamp := current_setting('domeye.data_start')::timestamp;
    snapshot_local timestamp := current_setting('domeye.snapshot_local')::timestamp;
BEGIN
    IF to_regclass('public.feature_country') IS NULL THEN
        RAISE EXCEPTION '源快照缺少必要表 public.feature_country';
    END IF;

    FOR candidate IN
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
        ORDER BY tablename
    LOOP
        time_column := CASE WHEN candidate.tablename LIKE 'feature_%' THEN 't' ELSE 's_time' END;
        EXECUTE format(
            'DELETE FROM public.%I WHERE %I < $1 OR %I > $2',
            candidate.tablename,
            time_column,
            time_column
        ) USING data_start, snapshot_local;
    END LOOP;
END
$block$;

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

VACUUM ANALYZE;

SELECT jsonb_build_object(
    'total', coalesce(sum(row_count), 0),
    'by_month_type', coalesce(
        jsonb_agg(
            jsonb_build_object(
                'month', month,
                'event_type', event_type,
                'row_count', row_count
            )
            ORDER BY month, event_type
        ),
        '[]'::jsonb
    )
)::text
FROM domeye_discarded_malformed_event;
