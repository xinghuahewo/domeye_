\set ON_ERROR_STOP on

CREATE TEMP TABLE domeye_inventory (
    schema_name text NOT NULL,
    table_name text NOT NULL,
    relation_role text NOT NULL,
    time_column text,
    row_count bigint NOT NULL,
    min_time text,
    max_time text,
    schema_hash text NOT NULL,
    PRIMARY KEY(schema_name, table_name)
);

DO $block$
DECLARE
    candidate record;
    count_value bigint;
    min_value text;
    max_value text;
    hash_value text;
    time_column_value text;
    role_value text;
BEGIN
    FOR candidate IN
        SELECT c.oid, n.nspname AS schema_name, c.relname AS table_name
        FROM pg_class AS c
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname IN ('public', 'info')
          AND c.relkind IN ('r', 'p')
          AND NOT c.relispartition
        ORDER BY n.nspname, c.relname
    LOOP
        IF candidate.schema_name = 'info' THEN
            time_column_value := NULL;
            role_value := CASE
                WHEN candidate.table_name IN (
                    'as_contact', 'quarantine', 'import_run',
                    'source_record', 'legacy_record'
                )
                    OR candidate.table_name ~
                        '^(source_record|legacy_record)_r[0-9]+$'
                    THEN 'static_info_restricted'
                WHEN candidate.table_name IN (
                    'schema_metadata', 'dataset_release', 'source_file',
                    'quality_result', 'active_release'
                ) THEN 'static_info_meta'
                WHEN candidate.table_name IN ('prefix', 'prefix_origin', 'prefix_domain')
                     OR candidate.table_name ~ '^prefix(_origin|_domain)?_r[0-9]+$'
                    THEN 'static_info_partitioned'
                ELSE 'static_info_business'
            END;
            EXECUTE format(
                'SELECT count(*) FROM %I.%I',
                candidate.schema_name,
                candidate.table_name
            ) INTO count_value;
            min_value := NULL;
            max_value := NULL;
        ELSE
            time_column_value := CASE
                WHEN candidate.table_name LIKE 'feature_%' THEN 't'
                ELSE 's_time'
            END;
            role_value := CASE
                WHEN candidate.table_name LIKE 'feature_%' THEN 'feature'
                ELSE 'event_or_fact'
            END;
            EXECUTE format(
                'SELECT count(*), min(%I)::text, max(%I)::text FROM %I.%I',
                time_column_value,
                time_column_value,
                candidate.schema_name,
                candidate.table_name
            ) INTO count_value, min_value, max_value;
        END IF;

        SELECT md5(string_agg(definition, E'\n' ORDER BY category, position))
        INTO hash_value
        FROM (
            SELECT
                'column'::text AS category,
                a.attnum::bigint AS position,
                format(
                    '%s|%s|%s|%s|%s',
                    a.attname,
                    format_type(a.atttypid, a.atttypmod),
                    a.attnotnull,
                    a.attidentity,
                    coalesce(pg_get_expr(ad.adbin, ad.adrelid), '')
                ) AS definition
            FROM pg_attribute AS a
            LEFT JOIN pg_attrdef AS ad
              ON ad.adrelid = a.attrelid
             AND ad.adnum = a.attnum
            WHERE a.attrelid = candidate.oid
              AND a.attnum > 0
              AND NOT a.attisdropped

            UNION ALL

            SELECT
                'constraint',
                100000 + row_number() OVER (ORDER BY conname),
                conname || '|' || pg_get_constraintdef(oid, true)
            FROM pg_constraint
            WHERE conrelid = candidate.oid

            UNION ALL

            SELECT
                'index',
                200000 + row_number() OVER (ORDER BY pg_get_indexdef(indexrelid)),
                pg_get_indexdef(indexrelid)
            FROM pg_index
            WHERE indrelid = candidate.oid
        ) AS definitions;

        INSERT INTO domeye_inventory(
            schema_name,
            table_name,
            relation_role,
            time_column,
            row_count,
            min_time,
            max_time,
            schema_hash
        ) VALUES (
            candidate.schema_name,
            candidate.table_name,
            role_value,
            time_column_value,
            count_value,
            min_value,
            max_value,
            coalesce(hash_value, md5(''))
        );
    END LOOP;
END
$block$;

CREATE TEMP TABLE domeye_static_info_inventory (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    present boolean NOT NULL,
    activation_state text NOT NULL,
    content_id text,
    manifest_sha256 text,
    source_release_label text,
    release_status text,
    implementation_scope text,
    file_count integer NOT NULL,
    loaded_file_count integer NOT NULL,
    active_source_record_count bigint NOT NULL,
    legacy_source_record_count bigint NOT NULL,
    quality_gate_version text,
    quality_status text
);

INSERT INTO domeye_static_info_inventory(
    singleton,
    present,
    activation_state,
    content_id,
    manifest_sha256,
    source_release_label,
    release_status,
    implementation_scope,
    file_count,
    loaded_file_count,
    active_source_record_count,
    legacy_source_record_count,
    quality_gate_version,
    quality_status
) VALUES (
    true, false, 'absent', NULL, NULL, NULL, NULL, NULL,
    0, 0, 0, 0, NULL, NULL
);

DO $block$
BEGIN
    IF to_regclass('info.dataset_release') IS NULL
       OR to_regclass('info.source_file') IS NULL THEN
        RETURN;
    END IF;

    UPDATE domeye_static_info_inventory AS inventory
    SET present = true,
        activation_state = selected.activation_state,
        content_id = selected.content_id,
        manifest_sha256 = selected.manifest_sha256,
        source_release_label = selected.source_release_label,
        release_status = selected.status,
        implementation_scope = selected.implementation_scope,
        file_count = selected.file_count,
        loaded_file_count = selected.loaded_file_count,
        active_source_record_count = selected.active_source_record_count,
        legacy_source_record_count = selected.legacy_source_record_count,
        quality_gate_version = selected.quality_gate_version,
        quality_status = selected.quality_status
    FROM (
        SELECT
            release.release_sk,
            CASE
                WHEN active.release_sk IS NOT NULL THEN 'active'
                ELSE 'shadow'
            END AS activation_state,
            release.content_id,
            release.manifest_sha256::text,
            release.source_release_label,
            release.status,
            metadata.implementation_scope,
            count(source.source_file_sk)::integer AS file_count,
            count(*) FILTER (WHERE source.load_status = 'loaded')::integer
                AS loaded_file_count,
            coalesce(sum(source.logical_record_count)
                FILTER (WHERE source.role = 'active'), 0)
                AS active_source_record_count,
            coalesce(sum(source.logical_record_count)
                FILTER (WHERE source.role <> 'active'), 0)
                AS legacy_source_record_count,
            release.quality_summary->>'quality_gate_version'
                AS quality_gate_version,
            release.quality_summary->>'status' AS quality_status
        FROM info.dataset_release AS release
        LEFT JOIN info.active_release AS active
          ON active.profile_name = 'core'
         AND active.release_sk = release.release_sk
        LEFT JOIN info.source_file AS source
          ON source.release_sk = release.release_sk
        CROSS JOIN info.schema_metadata AS metadata
        WHERE metadata.singleton
        GROUP BY release.release_sk, active.release_sk, metadata.implementation_scope
        ORDER BY (active.release_sk IS NOT NULL) DESC, release.release_sk DESC
        LIMIT 1
    ) AS selected
    WHERE inventory.singleton;
END
$block$;

SELECT jsonb_pretty(
    jsonb_build_object(
        'schema_version', 1,
        'data_start', :'data_start',
        'snapshot_time', :'snapshot_time',
        'tables', coalesce(
            (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'name', CASE
                            WHEN schema_name = 'public' THEN table_name
                            ELSE schema_name || '.' || table_name
                        END,
                        'schema_name', schema_name,
                        'table_name', table_name,
                        'role', relation_role,
                        'time_column', time_column,
                        'row_count', row_count,
                        'min_time', min_time,
                        'max_time', max_time,
                        'schema_hash', schema_hash
                    )
                    ORDER BY schema_name, table_name
                )
                FROM domeye_inventory
            ),
            '[]'::jsonb
        ),
        'static_info', (
            SELECT CASE
                WHEN NOT present THEN NULL
                ELSE jsonb_build_object(
                    'activation_state', activation_state,
                    'content_id', content_id,
                    'manifest_sha256', manifest_sha256,
                    'source_release_label', source_release_label,
                    'release_status', release_status,
                    'implementation_scope', implementation_scope,
                    'file_count', file_count,
                    'loaded_file_count', loaded_file_count,
                    'active_source_record_count', active_source_record_count,
                    'legacy_source_record_count', legacy_source_record_count,
                    'quality_gate_version', quality_gate_version,
                    'quality_status', quality_status
                )
            END
            FROM domeye_static_info_inventory
            WHERE singleton
        )
    )
);
