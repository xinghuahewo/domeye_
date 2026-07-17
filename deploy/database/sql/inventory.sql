\set ON_ERROR_STOP on

CREATE TEMP TABLE domeye_inventory (
    table_name text PRIMARY KEY,
    row_count bigint NOT NULL,
    min_time text,
    max_time text,
    schema_hash text NOT NULL
);

DO $block$
DECLARE
    candidate record;
    count_value bigint;
    min_value text;
    max_value text;
    hash_value text;
    time_column text;
BEGIN
    FOR candidate IN
        SELECT c.oid, c.relname AS table_name
        FROM pg_class AS c
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p')
        ORDER BY c.relname
    LOOP
        time_column := CASE WHEN candidate.table_name LIKE 'feature_%' THEN 't' ELSE 's_time' END;
        EXECUTE format(
            'SELECT count(*), min(%I)::text, max(%I)::text FROM public.%I',
            time_column,
            time_column,
            candidate.table_name
        ) INTO count_value, min_value, max_value;

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

        INSERT INTO domeye_inventory(table_name, row_count, min_time, max_time, schema_hash)
        VALUES (candidate.table_name, count_value, min_value, max_value, coalesce(hash_value, md5('')));
    END LOOP;
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
                        'name', table_name,
                        'row_count', row_count,
                        'min_time', min_time,
                        'max_time', max_time,
                        'schema_hash', schema_hash
                    )
                    ORDER BY table_name
                )
                FROM domeye_inventory
            ),
            '[]'::jsonb
        )
    )
);
