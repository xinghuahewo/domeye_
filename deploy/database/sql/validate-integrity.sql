\set ON_ERROR_STOP on

BEGIN;
SET LOCAL enable_nestloop = off;
SET LOCAL enable_mergejoin = off;
SET LOCAL domeye.data_start TO :'data_start';
SET LOCAL domeye.snapshot_time TO :'snapshot_time';
SET LOCAL domeye.snapshot_month TO :'snapshot_month';

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

CREATE TEMP TABLE domeye_expected_relation (
    schema_name text NOT NULL,
    table_name text NOT NULL,
    relation_role text NOT NULL,
    time_column text,
    PRIMARY KEY (schema_name, table_name)
) ON COMMIT DROP;

INSERT INTO domeye_expected_relation(
    schema_name, table_name, relation_role, time_column
)
SELECT
    'public',
    table_name,
    CASE
        WHEN table_name = 'feature_country' OR table_name LIKE 'feature_%'
            THEN 'feature'
        ELSE 'event_or_fact'
    END,
    CASE
        WHEN table_name = 'feature_country' OR table_name LIKE 'feature_%'
            THEN 't'
        ELSE 's_time'
    END
FROM domeye_expected_table;

INSERT INTO domeye_expected_relation(
    schema_name, table_name, relation_role, time_column
)
VALUES
    ('info', 'schema_metadata', 'static_info_meta', NULL),
    ('info', 'dataset_release', 'static_info_meta', NULL),
    ('info', 'source_file', 'static_info_meta', NULL),
    ('info', 'import_run', 'static_info_restricted', NULL),
    ('info', 'quality_result', 'static_info_meta', NULL),
    ('info', 'quarantine', 'static_info_restricted', NULL),
    ('info', 'active_release', 'static_info_meta', NULL),
    ('info', 'country', 'static_info_business', NULL),
    ('info', 'country_alias', 'static_info_business', NULL),
    ('info', 'autonomous_system', 'static_info_business', NULL),
    ('info', 'as_contact', 'static_info_restricted', NULL),
    ('info', 'as_policy_member', 'static_info_business', NULL),
    ('info', 'as_relation', 'static_info_business', NULL),
    ('info', 'important_as', 'static_info_business', NULL),
    ('info', 'prefix', 'static_info_partitioned', NULL),
    ('info', 'prefix_origin', 'static_info_partitioned', NULL),
    ('info', 'prefix_domain', 'static_info_partitioned', NULL);

DO $block$
BEGIN
    IF to_regclass('info.schema_metadata') IS NOT NULL
       AND EXISTS (
           SELECT 1
           FROM info.schema_metadata
           WHERE singleton
             AND schema_version = 1
             AND implementation_scope = 'all_24_files'
       ) THEN
        INSERT INTO domeye_expected_relation(
            schema_name, table_name, relation_role, time_column
        )
        VALUES
            ('info', 'source_record', 'static_info_restricted', NULL),
            ('info', 'mapping_record', 'static_info_business', NULL),
            ('info', 'domain_record', 'static_info_partitioned', NULL),
            ('info', 'domain_address', 'static_info_partitioned', NULL),
            ('info', 'as_prefix_history', 'static_info_partitioned', NULL),
            ('info', 'important_prefix', 'static_info_business', NULL),
            ('info', 'important_domain', 'static_info_business', NULL),
            ('info', 'private_as_location', 'static_info_business', NULL),
            ('info', 'route_triplet_baseline', 'static_info_partitioned', NULL),
            ('info', 'dns_observation', 'static_info_partitioned', NULL),
            ('info', 'as_rank', 'static_info_business', NULL),
            ('info', 'organization', 'static_info_business', NULL),
            ('info', 'organization_as', 'static_info_business', NULL),
            ('info', 'organization_prefix', 'static_info_business', NULL),
            ('info', 'legacy_record', 'static_info_restricted', NULL);
    END IF;
END
$block$;

CREATE TEMP TABLE domeye_info_validation (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    present boolean NOT NULL,
    implementation_scope text,
    expected_base_table_count integer NOT NULL,
    actual_table_count integer NOT NULL,
    missing_base_table_count integer NOT NULL,
    extra_table_count integer NOT NULL,
    release_count integer NOT NULL,
    active_release_count integer NOT NULL,
    source_contract_failure_count integer NOT NULL,
    reconciliation_failure_count integer NOT NULL,
    lifecycle_failure_count integer NOT NULL,
    missing_tables jsonb NOT NULL,
    extra_tables jsonb NOT NULL,
    ok boolean NOT NULL
) ON COMMIT DROP;

INSERT INTO domeye_info_validation(
    singleton,
    present,
    implementation_scope,
    expected_base_table_count,
    actual_table_count,
    missing_base_table_count,
    extra_table_count,
    release_count,
    active_release_count,
    source_contract_failure_count,
    reconciliation_failure_count,
    lifecycle_failure_count,
    missing_tables,
    extra_tables,
    ok
) VALUES (
    true, false, NULL,
    (SELECT count(*) FROM domeye_expected_relation WHERE schema_name = 'info'),
    0, 0, 0, 0, 0, 0, 0, 0,
    '[]'::jsonb, '[]'::jsonb, true
);

DO $block$
DECLARE
    missing_count integer;
    extra_count integer;
    source_failure_count integer;
    reconciliation_failure_value integer;
    lifecycle_failure_value integer;
    scope_value text;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'info') THEN
        RETURN;
    END IF;

    SELECT count(*)
    INTO missing_count
    FROM domeye_expected_relation AS expected
    WHERE expected.schema_name = 'info'
      AND to_regclass(format('%I.%I', expected.schema_name, expected.table_name))
          IS NULL;

    SELECT count(*)
    INTO extra_count
    FROM pg_tables AS actual
    WHERE actual.schemaname = 'info'
      AND NOT EXISTS (
          SELECT 1
          FROM domeye_expected_relation AS expected
          WHERE expected.schema_name = actual.schemaname
            AND expected.table_name = actual.tablename
      )
      AND actual.tablename !~
          '^(prefix|prefix_origin|prefix_domain|source_record|domain_record|domain_address|as_prefix_history|route_triplet_baseline|dns_observation|legacy_record)_r[0-9]+$';

    UPDATE domeye_info_validation
    SET present = true,
        actual_table_count = (
            SELECT count(*) FROM pg_tables WHERE schemaname = 'info'
        ),
        missing_base_table_count = missing_count,
        extra_table_count = extra_count,
        missing_tables = coalesce((
            SELECT jsonb_agg(expected.table_name ORDER BY expected.table_name)
            FROM domeye_expected_relation AS expected
            WHERE expected.schema_name = 'info'
              AND to_regclass(
                  format('%I.%I', expected.schema_name, expected.table_name)
              ) IS NULL
        ), '[]'::jsonb),
        extra_tables = coalesce((
            SELECT jsonb_agg(actual.tablename ORDER BY actual.tablename)
            FROM pg_tables AS actual
            WHERE actual.schemaname = 'info'
              AND NOT EXISTS (
                  SELECT 1
                  FROM domeye_expected_relation AS expected
                  WHERE expected.schema_name = actual.schemaname
                    AND expected.table_name = actual.tablename
              )
              AND actual.tablename !~
                  '^(prefix|prefix_origin|prefix_domain|source_record|domain_record|domain_address|as_prefix_history|route_triplet_baseline|dns_observation|legacy_record)_r[0-9]+$'
        ), '[]'::jsonb)
    WHERE singleton;

    IF missing_count <> 0 OR extra_count <> 0 THEN
        UPDATE domeye_info_validation SET ok = false WHERE singleton;
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_class AS child
        JOIN pg_namespace AS namespace ON namespace.oid = child.relnamespace
        LEFT JOIN pg_inherits AS inheritance ON inheritance.inhrelid = child.oid
        LEFT JOIN pg_class AS parent ON parent.oid = inheritance.inhparent
        WHERE namespace.nspname = 'info'
          AND child.relname ~
              '^(prefix|prefix_origin|prefix_domain|source_record|domain_record|domain_address|as_prefix_history|route_triplet_baseline|dns_observation|legacy_record)_r[0-9]+$'
          AND (
              NOT child.relispartition
              OR parent.relname NOT IN (
                  'prefix', 'prefix_origin', 'prefix_domain',
                  'source_record', 'domain_record', 'domain_address',
                  'as_prefix_history', 'route_triplet_baseline',
                  'dns_observation', 'legacy_record'
              )
              OR child.relname !~
                  ('^' || parent.relname || '_r[0-9]+$')
          )
    ) THEN
        UPDATE domeye_info_validation
        SET extra_table_count = extra_table_count + 1,
            extra_tables = extra_tables || '["invalid_partition"]'::jsonb,
            ok = false
        WHERE singleton;
        RETURN;
    END IF;

    SELECT implementation_scope
    INTO scope_value
    FROM info.schema_metadata
    WHERE singleton
      AND schema_version = 1;

    SELECT count(*)
    INTO source_failure_count
    FROM info.dataset_release AS release
    WHERE (
        SELECT count(*)
        FROM info.source_file AS source
        WHERE source.release_sk = release.release_sk
    ) <> 24;

    SELECT count(*)
    INTO reconciliation_failure_value
    FROM info.source_file
    WHERE (
        load_status = 'loaded'
        AND loaded_record_count + quarantined_record_count
            <> logical_record_count
    )
    OR (
        scope_value = 'all_24_files'
        AND load_status <> 'loaded'
    );

    IF scope_value = 'all_24_files' THEN
        SELECT reconciliation_failure_value + count(*)
        INTO reconciliation_failure_value
        FROM info.source_file AS source
        WHERE source.release_sk IN (
            SELECT release_sk FROM info.dataset_release
        )
          AND (
              (
                  SELECT count(*)
                  FROM info.source_record AS record
                  WHERE record.release_sk = source.release_sk
                    AND record.source_file_sk = source.source_file_sk
              ) <> source.logical_record_count
              OR (
                  SELECT count(*)
                  FROM info.source_record AS record
                  WHERE record.release_sk = source.release_sk
                    AND record.source_file_sk = source.source_file_sk
                    AND record.disposition = 'accepted'
              ) <> source.loaded_record_count
              OR (
                  SELECT count(*)
                  FROM info.source_record AS record
                  WHERE record.release_sk = source.release_sk
                    AND record.source_file_sk = source.source_file_sk
                    AND record.disposition = 'quarantined'
              ) <> source.quarantined_record_count
              OR EXISTS (
                  SELECT 1
                  FROM info.source_record AS record
                  WHERE record.release_sk = source.release_sk
                    AND record.source_file_sk = source.source_file_sk
                    AND record.disposition = 'quarantined'
                    AND (
                        record.reason_code IS NULL
                        OR btrim(record.reason_code) = ''
                    )
              )
          );

        SELECT reconciliation_failure_value + count(*)
        INTO reconciliation_failure_value
        FROM info.source_record AS record
        FULL OUTER JOIN info.quarantine AS quarantine
          ON quarantine.release_sk = record.release_sk
         AND quarantine.source_file_sk = record.source_file_sk
         AND quarantine.source_row_no = record.source_row_no
        WHERE (
            record.disposition = 'quarantined'
            OR quarantine.quarantine_sk IS NOT NULL
        )
          AND (
            record.source_row_no IS NULL
            OR quarantine.quarantine_sk IS NULL
            OR record.disposition <> 'quarantined'
            OR record.reason_code IS DISTINCT FROM quarantine.reason_code
            OR record.source_record_sha256 IS DISTINCT FROM
               quarantine.raw_record_sha256
          );

        SELECT reconciliation_failure_value + count(*)
        INTO reconciliation_failure_value
        FROM info.source_record AS record
        CROSS JOIN LATERAL jsonb_array_elements(record.quality_flags) AS flag
        WHERE coalesce((flag->>'blocking')::boolean, false);

        WITH accepted_by_kind AS (
            SELECT record_kind, count(*) AS record_count
            FROM info.source_record
            WHERE disposition = 'accepted'
            GROUP BY record_kind
        ),
        visible_by_kind AS (
            SELECT 'autonomous_system'::text AS record_kind,
                   count(*) AS record_count
            FROM info.autonomous_system
            UNION ALL
            SELECT 'important_as', count(*) FROM info.important_as
            UNION ALL
            SELECT 'prefix', count(*) FROM info.prefix
            UNION ALL
            SELECT 'country', count(*) FROM info.country
            UNION ALL
            SELECT 'domain', count(*) FROM info.domain_record
            UNION ALL
            SELECT mapping_kind, count(*)
            FROM info.mapping_record
            GROUP BY mapping_kind
            UNION ALL
            SELECT 'important_domain', count(*) FROM info.important_domain
            UNION ALL
            SELECT 'route_triplet', count(*)
            FROM info.route_triplet_baseline
            UNION ALL
            SELECT 'important_prefix', count(*) FROM info.important_prefix
            UNION ALL
            SELECT 'dns_observation', count(*) FROM info.dns_observation
            UNION ALL
            SELECT 'as_rank', count(*) FROM info.as_rank
            UNION ALL
            SELECT 'organization', count(*) FROM info.organization
            UNION ALL
            SELECT 'legacy', count(*) FROM info.legacy_record
        )
        SELECT reconciliation_failure_value + coalesce(
            sum(
                abs(
                    coalesce(accepted.record_count, 0)
                    - coalesce(visible.record_count, 0)
                )
            ),
            0
        )
        INTO reconciliation_failure_value
        FROM accepted_by_kind AS accepted
        FULL OUTER JOIN visible_by_kind AS visible USING (record_kind);

        SELECT reconciliation_failure_value + count(*)
        INTO reconciliation_failure_value
        FROM (
            SELECT business.release_sk, business.source_file_sk,
                   business.source_row_no, 'domain_record' AS table_name
            FROM info.domain_record AS business
            JOIN info.source_file AS source
              ON source.release_sk = business.release_sk
             AND source.source_file_sk = business.source_file_sk
            WHERE business.source_active
              AND source.role <> 'active'
            UNION ALL
            SELECT business.release_sk, business.source_file_sk,
                   business.source_row_no, 'mapping_record'
            FROM info.mapping_record AS business
            JOIN info.source_file AS source
              ON source.release_sk = business.release_sk
             AND source.source_file_sk = business.source_file_sk
            WHERE business.source_active
              AND source.role <> 'active'
            UNION ALL
            SELECT business.release_sk, business.source_file_sk,
                   business.source_row_no, 'as_prefix_history'
            FROM info.as_prefix_history AS business
            JOIN info.source_file AS source
              ON source.release_sk = business.release_sk
             AND source.source_file_sk = business.source_file_sk
            WHERE business.source_active
              AND source.role <> 'active'
            UNION ALL
            SELECT business.release_sk, business.source_file_sk,
                   business.source_row_no, 'as_relation'
            FROM info.as_relation AS business
            JOIN info.source_file AS source
              ON source.release_sk = business.release_sk
             AND source.source_file_sk = business.source_file_sk
            WHERE business.source_active
              AND source.role <> 'active'
            UNION ALL
            SELECT business.release_sk, business.source_file_sk,
                   business.source_row_no, 'important_domain'
            FROM info.important_domain AS business
            JOIN info.source_file AS source
              ON source.release_sk = business.release_sk
             AND source.source_file_sk = business.source_file_sk
            WHERE business.source_active
              AND source.role <> 'active'
            UNION ALL
            SELECT business.release_sk, business.source_file_sk,
                   business.source_row_no, 'private_as_location'
            FROM info.private_as_location AS business
            JOIN info.source_file AS source
              ON source.release_sk = business.release_sk
             AND source.source_file_sk = business.source_file_sk
            WHERE business.source_active
              AND source.role <> 'active'
            UNION ALL
            SELECT business.release_sk, business.source_file_sk,
                   business.source_row_no, 'route_triplet_baseline'
            FROM info.route_triplet_baseline AS business
            JOIN info.source_file AS source
              ON source.release_sk = business.release_sk
             AND source.source_file_sk = business.source_file_sk
            WHERE business.source_active
              AND source.role <> 'active'
            UNION ALL
            SELECT business.release_sk, business.source_file_sk,
                   business.source_row_no, 'important_prefix'
            FROM info.important_prefix AS business
            JOIN info.source_file AS source
              ON source.release_sk = business.release_sk
             AND source.source_file_sk = business.source_file_sk
            WHERE business.source_active
              AND source.role <> 'active'
            UNION ALL
            SELECT business.release_sk, business.source_file_sk,
                   business.source_row_no, 'legacy_record'
            FROM info.legacy_record AS business
            JOIN info.source_file AS source
              ON source.release_sk = business.release_sk
             AND source.source_file_sk = business.source_file_sk
            WHERE business.source_active
              AND source.role <> 'active'
        ) AS invalid_source_activation;
    END IF;

    SELECT count(*)
    INTO lifecycle_failure_value
    FROM info.dataset_release AS release
    WHERE release.status IN ('loading', 'failed')
       OR (
           release.status IN ('ready', 'active', 'retired')
           AND (
               SELECT count(*)
               FROM info.source_file AS source
               WHERE source.release_sk = release.release_sk
                 AND source.load_status = 'loaded'
           ) <> 24
       )
       OR (
           release.status = 'active'
           AND NOT EXISTS (
               SELECT 1
               FROM info.active_release AS active
               WHERE active.release_sk = release.release_sk
           )
       )
       OR (
           release.status = 'validating'
           AND release.loaded_scope @> ARRAY['all_24_files']
           AND (
               SELECT count(*)
               FROM info.source_file AS source
               WHERE source.release_sk = release.release_sk
                 AND source.load_status = 'loaded'
           ) <> 24
       )
       OR (
           release.status = 'validating'
           AND release.loaded_scope @> ARRAY['core_four_files']
           AND (
               SELECT count(*)
               FROM info.source_file AS source
               WHERE source.release_sk = release.release_sk
                 AND source.name IN (
                     'as_entity.csv', 'important_as.csv',
                     'ip_bgp_entity.csv', 'country.xlsx'
                 )
                 AND source.load_status = 'loaded'
           ) <> 4
       );

    UPDATE domeye_info_validation
    SET implementation_scope = scope_value,
        release_count = (SELECT count(*) FROM info.dataset_release),
        active_release_count = (SELECT count(*) FROM info.active_release),
        source_contract_failure_count = source_failure_count,
        reconciliation_failure_count = reconciliation_failure_value,
        lifecycle_failure_count = lifecycle_failure_value,
        ok = (
            scope_value IN ('core_four_files', 'all_24_files')
            AND (SELECT count(*) FROM info.dataset_release) > 0
            AND source_failure_count = 0
            AND reconciliation_failure_value = 0
            AND lifecycle_failure_value = 0
            AND NOT EXISTS (
                SELECT 1
                FROM info.active_release AS active
                JOIN info.dataset_release AS release
                  ON release.release_sk = active.release_sk
                WHERE release.status <> 'active'
                   OR EXISTS (
                       SELECT 1
                       FROM info.quality_result AS quality
                       WHERE quality.release_sk = active.release_sk
                         AND quality.blocking
                         AND quality.status <> 'pass'
                   )
            )
        )
    WHERE singleton;
END
$block$;

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
                  AND namespace.nspname <> 'info'
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
                  AND namespace.nspname <> 'info'
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
                      AND namespace.nspname <> 'info'
                      AND namespace.nspname NOT LIKE 'pg_%'
                      AND namespace.nspname <> 'information_schema'
                      AND namespace.nspname NOT IN (
                          '_timescaledb_catalog', '_timescaledb_config',
                          '_timescaledb_internal', '_timescaledb_cache',
                          'timescaledb_information', 'timescaledb_experimental'
                      )
                )
                AND (SELECT ok FROM domeye_info_validation WHERE singleton)
            )
        ),
        'static_info', (
            SELECT jsonb_build_object(
                'present', present,
                'implementation_scope', implementation_scope,
                'expected_base_table_count', expected_base_table_count,
                'actual_table_count', actual_table_count,
                'missing_base_table_count', missing_base_table_count,
                'extra_table_count', extra_table_count,
                'release_count', release_count,
                'active_release_count', active_release_count,
                'source_contract_failure_count', source_contract_failure_count,
                'reconciliation_failure_count', reconciliation_failure_count,
                'lifecycle_failure_count', lifecycle_failure_count,
                'missing_tables', missing_tables,
                'extra_tables', extra_tables,
                'ok', ok
            )
            FROM domeye_info_validation
            WHERE singleton
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
