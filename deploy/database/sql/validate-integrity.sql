\set ON_ERROR_STOP on

BEGIN;
SET LOCAL enable_nestloop = off;
SET LOCAL enable_mergejoin = off;
SELECT set_config('domeye.data_start', :'data_start', true);
SELECT set_config('domeye.snapshot_time', :'snapshot_time', true);
SELECT set_config('domeye.snapshot_month', :'snapshot_month', true);

CREATE TEMP TABLE domeye_expected_table (
    table_name text PRIMARY KEY
) ON COMMIT DROP;

INSERT INTO domeye_expected_table(table_name)
VALUES ('feature_country');

INSERT INTO domeye_expected_table(table_name)
SELECT family.name || '_' || to_char(month_value, 'YYYYMM')
FROM generate_series(
    date_trunc('month', current_setting('domeye.data_start')::timestamp),
    to_date(current_setting('domeye.snapshot_month'), 'YYYYMM'),
    interval '1 month'
) AS month_value
CROSS JOIN (
    VALUES
        ('event_table'),
        ('hijack'),
        ('sub_hijack'),
        ('leak_event'),
        ('prefix_outage'),
        ('as_outage'),
        ('country_outage'),
        ('feature_other'),
        ('feature_us'),
        ('feature_br'),
        ('feature_cn'),
        ('feature_ru'),
        ('feature_in'),
        ('feature_gb'),
        ('feature_id'),
        ('feature_de'),
        ('feature_au'),
        ('feature_pl')
) AS family(name);

CREATE TEMP TABLE domeye_detail_month_result (
    month text PRIMARY KEY,
    event_rows bigint NOT NULL,
    reference_rows bigint NOT NULL,
    malformed_count bigint NOT NULL,
    orphan_count bigint NOT NULL
) ON COMMIT DROP;

CREATE TEMP TABLE domeye_detail_orphan_sample (
    month text NOT NULL,
    event_type text NOT NULL,
    problem text NOT NULL,
    event_id text NOT NULL,
    source text NOT NULL
) ON COMMIT DROP;

DO $block$
DECLARE
    month_suffix text;
    event_table_name text;
    family text;
    fact_table_name text;
    event_rows_value bigint;
    reference_rows_value bigint;
    malformed_value bigint;
    orphan_value bigint;
BEGIN
    FOR month_suffix IN
        SELECT to_char(month_value, 'YYYYMM')
        FROM generate_series(
            date_trunc('month', current_setting('domeye.data_start')::timestamp),
            to_date(current_setting('domeye.snapshot_month'), 'YYYYMM'),
            interval '1 month'
        ) AS month_value
        ORDER BY month_value
    LOOP
        event_table_name := 'event_table_' || month_suffix;

        CREATE TEMP TABLE domeye_month_reference (
            event_type text NOT NULL,
            problem text NOT NULL,
            event_id text NOT NULL,
            source text NOT NULL
        ) ON COMMIT DROP;
        CREATE TEMP TABLE domeye_month_fact (
            event_type text NOT NULL,
            problem text NOT NULL,
            event_id text NOT NULL,
            source text NOT NULL,
            PRIMARY KEY(event_type, problem, event_id, source)
        ) ON COMMIT DROP;

        event_rows_value := 0;
        reference_rows_value := 0;
        malformed_value := 0;
        orphan_value := 0;

        IF to_regclass(format('public.%I', event_table_name)) IS NOT NULL THEN
            EXECUTE format('SELECT count(*) FROM public.%I', event_table_name)
            INTO event_rows_value;

            EXECUTE format($query$
                INSERT INTO domeye_month_reference(event_type, problem, event_id, source)
                SELECT
                    split_part(detail_url, '/', 1),
                    split_part(detail_url, '/', 3),
                    split_part(detail_url, '/', 4),
                    split_part(detail_url, '/', 5)
                FROM public.%I
                WHERE cardinality(string_to_array(detail_url, '/')) = 5
                  AND split_part(detail_url, '/', 1) IN (
                      'hijack', 'sub_hijack', 'leak',
                      'prefix_outage', 'as_outage', 'country_outage'
                  )
                  AND split_part(detail_url, '/', 3) <> ''
                  AND split_part(detail_url, '/', 4) ~ '^[0-9]+$'
                  AND split_part(detail_url, '/', 5) <> ''
                  AND split_part(detail_url, '/', 2) ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$'
                  AND replace(substr(split_part(detail_url, '/', 2), 1, 7), '-', '') = %L
            $query$, event_table_name, month_suffix);

            SELECT count(*) INTO reference_rows_value FROM domeye_month_reference;
            malformed_value := event_rows_value - reference_rows_value;
        END IF;

        FOREACH family IN ARRAY ARRAY[
            'hijack', 'sub_hijack', 'leak_event',
            'prefix_outage', 'as_outage', 'country_outage'
        ]
        LOOP
            fact_table_name := family || '_' || month_suffix;
            IF to_regclass(format('public.%I', fact_table_name)) IS NULL THEN
                CONTINUE;
            END IF;

            IF family = 'hijack' THEN
                EXECUTE format($query$
                    INSERT INTO domeye_month_fact(event_type, problem, event_id, source)
                    SELECT DISTINCT 'hijack', replace(prefix, '/', '-'), hijack_eventid::text, source
                    FROM public.%I
                    ON CONFLICT DO NOTHING
                $query$, fact_table_name);
            ELSIF family = 'sub_hijack' THEN
                EXECUTE format($query$
                    INSERT INTO domeye_month_fact(event_type, problem, event_id, source)
                    SELECT DISTINCT 'sub_hijack', replace(prefix, '/', '-'), sub_hijack_eventid::text, source
                    FROM public.%I
                    ON CONFLICT DO NOTHING
                $query$, fact_table_name);
            ELSIF family = 'leak_event' THEN
                EXECUTE format($query$
                    INSERT INTO domeye_month_fact(event_type, problem, event_id, source)
                    SELECT DISTINCT 'leak', replace(prefix, '/', '-'), leak_event_id::text, source
                    FROM public.%I
                    ON CONFLICT DO NOTHING
                $query$, fact_table_name);
            ELSIF family = 'prefix_outage' THEN
                EXECUTE format($query$
                    INSERT INTO domeye_month_fact(event_type, problem, event_id, source)
                    SELECT DISTINCT 'prefix_outage', replace(prefix, '/', '-'), outage_id::text, source
                    FROM public.%I
                    ON CONFLICT DO NOTHING
                $query$, fact_table_name);
            ELSIF family = 'as_outage' THEN
                EXECUTE format($query$
                    INSERT INTO domeye_month_fact(event_type, problem, event_id, source)
                    SELECT DISTINCT 'as_outage', asn, outage_id::text, source
                    FROM public.%I
                    ON CONFLICT DO NOTHING
                $query$, fact_table_name);
            ELSE
                EXECUTE format($query$
                    INSERT INTO domeye_month_fact(event_type, problem, event_id, source)
                    SELECT DISTINCT 'country_outage', country, outage_id::text, source
                    FROM public.%I
                    ON CONFLICT DO NOTHING
                $query$, fact_table_name);
            END IF;
        END LOOP;

        ANALYZE domeye_month_reference;
        ANALYZE domeye_month_fact;

        SELECT count(*)
        INTO orphan_value
        FROM domeye_month_reference AS reference
        LEFT JOIN domeye_month_fact AS fact
          ON fact.event_type = reference.event_type
         AND fact.problem = reference.problem
         AND fact.event_id = reference.event_id
         AND fact.source = reference.source
        WHERE fact.event_type IS NULL;

        INSERT INTO domeye_detail_orphan_sample(month, event_type, problem, event_id, source)
        SELECT month_suffix, reference.event_type, reference.problem, reference.event_id, reference.source
        FROM domeye_month_reference AS reference
        LEFT JOIN domeye_month_fact AS fact
          ON fact.event_type = reference.event_type
         AND fact.problem = reference.problem
         AND fact.event_id = reference.event_id
         AND fact.source = reference.source
        WHERE fact.event_type IS NULL
        ORDER BY reference.event_type, reference.problem, reference.event_id, reference.source
        LIMIT 20;

        INSERT INTO domeye_detail_month_result(
            month, event_rows, reference_rows, malformed_count, orphan_count
        ) VALUES (
            month_suffix, event_rows_value, reference_rows_value, malformed_value, orphan_value
        );

        DROP TABLE domeye_month_reference;
        DROP TABLE domeye_month_fact;
    END LOOP;
END
$block$;

SELECT jsonb_pretty(
    jsonb_build_object(
        'schema_version', 1,
        'data_start', current_setting('domeye.data_start'),
        'snapshot_time', current_setting('domeye.snapshot_time'),
        'table_whitelist', jsonb_build_object(
            'expected_count', (SELECT count(*) FROM domeye_expected_table),
            'actual_public_count', (
                SELECT count(*) FROM pg_tables WHERE schemaname = 'public'
            ),
            'missing_count', (
                SELECT count(*)
                FROM domeye_expected_table AS expected
                WHERE NOT EXISTS (
                    SELECT 1 FROM pg_tables AS actual
                    WHERE actual.schemaname = 'public'
                      AND actual.tablename = expected.table_name
                )
            ),
            'extra_public_count', (
                SELECT count(*)
                FROM pg_tables AS actual
                WHERE actual.schemaname = 'public'
                  AND NOT EXISTS (
                      SELECT 1 FROM domeye_expected_table AS expected
                      WHERE expected.table_name = actual.tablename
                  )
            ),
            'extra_user_schema_table_count', (
                SELECT count(*)
                FROM pg_class AS relation
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE relation.relkind IN ('r', 'p')
                  AND namespace.nspname <> 'public'
                  AND namespace.nspname NOT LIKE 'pg_%'
                  AND namespace.nspname <> 'information_schema'
                  AND namespace.nspname NOT IN (
                      '_timescaledb_catalog', '_timescaledb_config',
                      '_timescaledb_internal', '_timescaledb_cache',
                      'timescaledb_information', 'timescaledb_experimental'
                  )
            ),
            'missing_tables', coalesce((
                SELECT jsonb_agg(expected.table_name ORDER BY expected.table_name)
                FROM domeye_expected_table AS expected
                WHERE NOT EXISTS (
                    SELECT 1 FROM pg_tables AS actual
                    WHERE actual.schemaname = 'public'
                      AND actual.tablename = expected.table_name
                )
            ), '[]'::jsonb),
            'extra_public_tables', coalesce((
                SELECT jsonb_agg(actual.tablename ORDER BY actual.tablename)
                FROM pg_tables AS actual
                WHERE actual.schemaname = 'public'
                  AND NOT EXISTS (
                      SELECT 1 FROM domeye_expected_table AS expected
                      WHERE expected.table_name = actual.tablename
                  )
            ), '[]'::jsonb),
            'extra_user_schema_tables', coalesce((
                SELECT jsonb_agg(
                    namespace.nspname || '.' || relation.relname
                    ORDER BY namespace.nspname, relation.relname
                )
                FROM pg_class AS relation
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE relation.relkind IN ('r', 'p')
                  AND namespace.nspname <> 'public'
                  AND namespace.nspname NOT LIKE 'pg_%'
                  AND namespace.nspname <> 'information_schema'
                  AND namespace.nspname NOT IN (
                      '_timescaledb_catalog', '_timescaledb_config',
                      '_timescaledb_internal', '_timescaledb_cache',
                      'timescaledb_information', 'timescaledb_experimental'
                  )
            ), '[]'::jsonb),
            'ok', (
                NOT EXISTS (
                    SELECT 1 FROM domeye_expected_table AS expected
                    WHERE NOT EXISTS (
                        SELECT 1 FROM pg_tables AS actual
                        WHERE actual.schemaname = 'public'
                          AND actual.tablename = expected.table_name
                    )
                )
                AND NOT EXISTS (
                    SELECT 1 FROM pg_tables AS actual
                    WHERE actual.schemaname = 'public'
                      AND NOT EXISTS (
                          SELECT 1 FROM domeye_expected_table AS expected
                          WHERE expected.table_name = actual.tablename
                      )
                )
                AND NOT EXISTS (
                    SELECT 1
                    FROM pg_class AS relation
                    JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                    WHERE relation.relkind IN ('r', 'p')
                      AND namespace.nspname <> 'public'
                      AND namespace.nspname NOT LIKE 'pg_%'
                      AND namespace.nspname <> 'information_schema'
                      AND namespace.nspname NOT IN (
                          '_timescaledb_catalog', '_timescaledb_config',
                          '_timescaledb_internal', '_timescaledb_cache',
                          'timescaledb_information', 'timescaledb_experimental'
                      )
                )
            )
        ),
        'detail_references', jsonb_build_object(
            'event_rows', (SELECT coalesce(sum(event_rows), 0) FROM domeye_detail_month_result),
            'reference_rows', (SELECT coalesce(sum(reference_rows), 0) FROM domeye_detail_month_result),
            'malformed_count', (SELECT coalesce(sum(malformed_count), 0) FROM domeye_detail_month_result),
            'orphan_count', (SELECT coalesce(sum(orphan_count), 0) FROM domeye_detail_month_result),
            'by_month', coalesce((
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'month', month,
                        'event_rows', event_rows,
                        'reference_rows', reference_rows,
                        'malformed_count', malformed_count,
                        'orphan_count', orphan_count
                    ) ORDER BY month
                )
                FROM domeye_detail_month_result
            ), '[]'::jsonb),
            'orphan_samples', coalesce((
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'month', month,
                        'event_type', event_type,
                        'problem', problem,
                        'event_id', event_id,
                        'source', source
                    ) ORDER BY month, event_type, problem, event_id, source
                )
                FROM domeye_detail_orphan_sample
            ), '[]'::jsonb),
            'ok', (
                (SELECT coalesce(sum(malformed_count), 0) FROM domeye_detail_month_result) = 0
                AND (SELECT coalesce(sum(orphan_count), 0) FROM domeye_detail_month_result) = 0
            )
        )
    )
);

COMMIT;
