\set ON_ERROR_STOP on

\if :source_preflight
BEGIN;
SET LOCAL statement_timeout = 0;
SET LOCAL lock_timeout = '30s';

CREATE TEMP TABLE domeye_source_expected_table (
    table_name text PRIMARY KEY
) ON COMMIT DROP;

INSERT INTO domeye_source_expected_table(table_name)
VALUES ('feature_country');

INSERT INTO domeye_source_expected_table(table_name)
SELECT family.name || '_' || month_suffix.value
FROM (
    VALUES
        ('202602'), ('202603'), ('202604'),
        ('202605'), ('202606'), ('202607')
) AS month_suffix(value)
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

DO $block$
DECLARE
    candidate record;
    time_column text;
    has_outside_row boolean;
    feature_min timestamp without time zone;
    feature_max timestamp without time zone;
BEGIN
    IF (SELECT count(*) FROM pg_tables WHERE schemaname = 'public')
       <> (SELECT count(*) FROM domeye_source_expected_table) THEN
        RAISE EXCEPTION '源候选 public 表数量与固定 release inventory 前提不一致';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM domeye_source_expected_table AS expected
        WHERE to_regclass(format('public.%I', expected.table_name)) IS NULL
    ) OR EXISTS (
        SELECT 1
        FROM pg_tables AS actual
        WHERE actual.schemaname = 'public'
          AND NOT EXISTS (
              SELECT 1 FROM domeye_source_expected_table AS expected
              WHERE expected.table_name = actual.tablename
          )
    ) THEN
        RAISE EXCEPTION '源候选 public 表集合与固定 release inventory 前提不一致';
    END IF;

    FOR candidate IN
        SELECT table_name
        FROM domeye_source_expected_table
        WHERE table_name ~ '_20260[23]$'
        ORDER BY table_name
    LOOP
        time_column := CASE WHEN candidate.table_name LIKE 'feature_%' THEN 't' ELSE 's_time' END;
        EXECUTE format(
            'SELECT EXISTS ('
            'SELECT 1 FROM public.%I WHERE %I < $1 OR %I >= $2 LIMIT 1)',
            candidate.table_name,
            time_column,
            time_column
        ) INTO has_outside_row
          USING TIMESTAMP '2026-02-01 00:00:00', TIMESTAMP '2026-04-01 00:00:00';
        IF has_outside_row THEN
            RAISE EXCEPTION '源候选保留月表含有窗口外数据：%', candidate.table_name;
        END IF;
    END LOOP;

    SELECT min(t), max(t)
    INTO feature_min, feature_max
    FROM public.feature_country;
    IF feature_min IS NULL OR feature_max IS NULL
       OR feature_min >= TIMESTAMP '2026-04-01 00:00:00'
       OR feature_max < TIMESTAMP '2026-02-01 00:00:00' THEN
        RAISE EXCEPTION '源候选 feature_country 与 2、3 月窗口没有有效交集';
    END IF;
END
$block$;

SELECT jsonb_build_object(
    'ok', true,
    'mode', 'source-preflight',
    'public_table_count', (
        SELECT count(*) FROM pg_tables WHERE schemaname = 'public'
    ),
    'expected_table_count', (
        SELECT count(*) FROM domeye_source_expected_table
    ),
    'feature_country_min', (SELECT min(t) FROM public.feature_country),
    'feature_country_max', (SELECT max(t) FROM public.feature_country)
)::text;

COMMIT;
\quit
\endif

BEGIN;
SET LOCAL statement_timeout = 0;
SET LOCAL lock_timeout = '30s';

CREATE TEMP TABLE domeye_dev_context (
    release_id text NOT NULL,
    system_identifier text NOT NULL,
    checkpoint_key text NOT NULL,
    prune_sql_sha256 text NOT NULL,
    inventory_sha256 text NOT NULL,
    reader_role text NOT NULL,
    data_start timestamp without time zone NOT NULL,
    data_end_exclusive timestamp without time zone NOT NULL
) ON COMMIT DROP;

INSERT INTO domeye_dev_context(
    release_id,
    system_identifier,
    checkpoint_key,
    prune_sql_sha256,
    inventory_sha256,
    reader_role,
    data_start,
    data_end_exclusive
) VALUES (
    :'release_id',
    :'system_identifier',
    :'checkpoint_key',
    :'prune_sql_sha256',
    :'inventory_sha256',
    :'reader_role',
    TIMESTAMP :'data_start',
    TIMESTAMP :'data_end_exclusive'
);

CREATE TEMP TABLE domeye_expected_table (
    table_name text PRIMARY KEY
) ON COMMIT DROP;

INSERT INTO domeye_expected_table(table_name)
VALUES ('feature_country');

INSERT INTO domeye_expected_table(table_name)
SELECT family.name || '_' || month_suffix.value
FROM (
    VALUES ('202602'), ('202603')
) AS month_suffix(value)
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

CREATE TEMP TABLE domeye_detail_reference_sample (
    event_type text PRIMARY KEY,
    month_suffix text NOT NULL
) ON COMMIT DROP;

CREATE TEMP TABLE domeye_info_summary (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    payload jsonb
) ON COMMIT DROP;
INSERT INTO domeye_info_summary(singleton, payload) VALUES (true, NULL);

DO $block$
DECLARE
    context domeye_dev_context%ROWTYPE;
    public_count integer;
    feature_country_min timestamp without time zone;
    feature_country_max timestamp without time zone;
    as_family text;
    month_suffix text;
    family text;
    expected_event_type text;
    problem_column text;
    event_id_column text;
    event_table_name text;
    fact_table_name text;
    has_rows boolean;
    has_reference boolean;
    reader_record record;
    candidate record;
BEGIN
    SELECT * INTO STRICT context FROM domeye_dev_context;

    IF to_regclass('domeye_dev.prune_checkpoint') IS NULL THEN
        RAISE EXCEPTION '缺少持久裁剪检查点';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM domeye_dev.prune_checkpoint AS checkpoint
        WHERE checkpoint.singleton
          AND checkpoint.schema_version = 1
          AND checkpoint.release_id = context.release_id
          AND checkpoint.system_identifier = context.system_identifier
          AND checkpoint.checkpoint_key = context.checkpoint_key
          AND checkpoint.prune_sql_sha256 = context.prune_sql_sha256
          AND checkpoint.inventory_sha256 = context.inventory_sha256
          AND checkpoint.data_start = context.data_start
          AND checkpoint.data_end_exclusive = context.data_end_exclusive
    ) THEN
        RAISE EXCEPTION '持久裁剪检查点与当前状态不一致';
    END IF;

    SELECT count(*) INTO public_count
    FROM pg_tables
    WHERE schemaname = 'public';

    IF public_count <> 37 THEN
        RAISE EXCEPTION '开发库 public 表数量错误：%，预期 37', public_count;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM domeye_expected_table AS expected
        WHERE to_regclass(format('public.%I', expected.table_name)) IS NULL
    ) OR EXISTS (
        SELECT 1
        FROM pg_tables AS actual
        WHERE actual.schemaname = 'public'
          AND NOT EXISTS (
              SELECT 1
              FROM domeye_expected_table AS expected
              WHERE expected.table_name = actual.tablename
          )
    ) THEN
        RAISE EXCEPTION '开发库 public 表不符合 2、3 月精确白名单';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE relation.relkind IN ('r', 'p')
          AND namespace.nspname <> 'public'
          AND namespace.nspname NOT LIKE 'pg_%'
          AND namespace.nspname <> 'information_schema'
          AND namespace.nspname NOT IN (
              'domeye_dev', 'info',
              '_timescaledb_catalog', '_timescaledb_config',
              '_timescaledb_internal', '_timescaledb_cache',
              'timescaledb_information', 'timescaledb_experimental'
          )
    ) THEN
        RAISE EXCEPTION '开发库存在未授权的用户 schema 表';
    END IF;

    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'info') THEN
        IF to_regclass('info.dataset_release') IS NULL
           OR to_regclass('info.source_file') IS NULL THEN
            RAISE EXCEPTION 'info schema 不完整';
        END IF;
        IF NOT EXISTS (
            SELECT 1
            FROM info.dataset_release AS release
            WHERE release.source_release_label = context.release_id
              AND release.status NOT IN ('loading', 'failed')
              AND (
                  SELECT count(*)
                  FROM info.source_file AS source
                  WHERE source.release_sk = release.release_sk
              ) = 24
        ) THEN
            RAISE EXCEPTION 'info release 未与开发数据库 release-id/24 文件合同绑定';
        END IF;
        IF NOT has_schema_privilege(context.reader_role, 'info', 'USAGE')
           OR NOT has_table_privilege(
               context.reader_role,
               'info.autonomous_system',
               'SELECT'
           )
           OR has_table_privilege(
               context.reader_role,
               'info.as_contact',
               'SELECT'
           )
           OR (
               to_regclass('info.source_record') IS NOT NULL
               AND has_table_privilege(
                   context.reader_role,
                   'info.source_record',
                   'SELECT'
               )
           )
           OR (
               to_regclass('info.legacy_record') IS NOT NULL
               AND has_table_privilege(
                   context.reader_role,
                   'info.legacy_record',
                   'SELECT'
               )
           ) THEN
            RAISE EXCEPTION '开发库 info 只读权限不符合最小授权';
        END IF;
        IF EXISTS (
            SELECT 1
            FROM info.schema_metadata
            WHERE singleton
              AND implementation_scope = 'all_24_files'
        ) AND EXISTS (
            SELECT 1
            FROM info.source_file AS source
            WHERE source.release_sk = (
                SELECT release_sk
                FROM info.dataset_release
                WHERE source_release_label = context.release_id
                ORDER BY release_sk DESC
                LIMIT 1
            )
              AND (
                  source.load_status <> 'loaded'
                  OR source.loaded_record_count
                     + source.quarantined_record_count
                     <> source.logical_record_count
                  OR (
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
              )
        ) THEN
            RAISE EXCEPTION '开发库 INFO 全 24 文件或来源账本未闭合';
        END IF;
        IF EXISTS (
            SELECT 1
            FROM info.source_record AS record
            FULL OUTER JOIN info.quarantine AS quarantine
              ON quarantine.release_sk = record.release_sk
             AND quarantine.source_file_sk = record.source_file_sk
             AND quarantine.source_row_no = record.source_row_no
            WHERE coalesce(record.release_sk, quarantine.release_sk) = (
                SELECT release_sk
                FROM info.dataset_release
                WHERE source_release_label = context.release_id
                ORDER BY release_sk DESC
                LIMIT 1
            )
              AND (
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
              )
        ) THEN
            RAISE EXCEPTION '开发库 INFO 隔离记录与来源账本不一致';
        END IF;
        IF EXISTS (
            SELECT 1
            FROM info.source_record AS record
            CROSS JOIN LATERAL
                jsonb_array_elements(record.quality_flags) AS flag
            WHERE record.release_sk = (
                SELECT release_sk
                FROM info.dataset_release
                WHERE source_release_label = context.release_id
                ORDER BY release_sk DESC
                LIMIT 1
            )
              AND coalesce((flag->>'blocking')::boolean, false)
        ) THEN
            RAISE EXCEPTION '开发库 INFO 存在未批准的阻断级质量标记';
        END IF;
        UPDATE domeye_info_summary
        SET payload = (
            SELECT jsonb_build_object(
                'content_id', release.content_id,
                'manifest_sha256', release.manifest_sha256,
                'status', release.status,
                'source_release_label', release.source_release_label,
                'implementation_scope', metadata.implementation_scope,
                'loaded_file_count', (
                    SELECT count(*)
                    FROM info.source_file AS source
                    WHERE source.release_sk = release.release_sk
                      AND source.load_status = 'loaded'
                )
            )
            FROM info.dataset_release AS release
            CROSS JOIN info.schema_metadata AS metadata
            WHERE release.source_release_label = context.release_id
              AND metadata.singleton
            ORDER BY release.release_sk DESC
            LIMIT 1
        )
        WHERE singleton;
    END IF;

    SELECT * INTO reader_record
    FROM pg_roles
    WHERE rolname = context.reader_role;
    IF NOT FOUND THEN
        RAISE EXCEPTION '只读角色不存在：%', context.reader_role;
    END IF;
    IF reader_record.rolsuper
       OR reader_record.rolcreaterole
       OR reader_record.rolcreatedb
       OR reader_record.rolreplication
       OR reader_record.rolbypassrls THEN
        RAISE EXCEPTION '只读角色具有高权限：%', context.reader_role;
    END IF;
    IF NOT coalesce(
        reader_record.rolconfig @> ARRAY['default_transaction_read_only=on']::text[],
        false
    ) THEN
        RAISE EXCEPTION '只读角色未启用 default_transaction_read_only：%', context.reader_role;
    END IF;
    IF NOT has_database_privilege(context.reader_role, current_database(), 'CONNECT')
       OR NOT has_schema_privilege(context.reader_role, 'public', 'USAGE') THEN
        RAISE EXCEPTION '只读角色缺少数据库连接或 public schema 使用权限';
    END IF;

    FOR candidate IN SELECT table_name FROM domeye_expected_table ORDER BY table_name LOOP
        IF NOT has_table_privilege(
            context.reader_role,
            format('public.%I', candidate.table_name),
            'SELECT'
        ) THEN
            RAISE EXCEPTION '只读角色缺少 SELECT 权限：%', candidate.table_name;
        END IF;
        IF has_table_privilege(
            context.reader_role,
            format('public.%I', candidate.table_name),
            'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
        ) THEN
            RAISE EXCEPTION '只读角色仍有写权限：%', candidate.table_name;
        END IF;
    END LOOP;

    IF EXISTS (
        SELECT 1
        FROM public.feature_country
        WHERE t < context.data_start OR t >= context.data_end_exclusive
        LIMIT 1
    ) THEN
        RAISE EXCEPTION 'feature_country 含有开发窗口之外的数据';
    END IF;
    SELECT min(t), max(t)
    INTO feature_country_min, feature_country_max
    FROM public.feature_country;
    IF feature_country_min IS NULL OR feature_country_max IS NULL THEN
        RAISE EXCEPTION 'feature_country 在开发窗口内为空';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM public.event_table_202602
        UNION ALL
        SELECT 1 FROM public.event_table_202603
        LIMIT 1
    ) THEN
        RAISE EXCEPTION '2、3 月事件总表均为空';
    END IF;

    FOREACH as_family IN ARRAY ARRAY[
        'other', 'us', 'br', 'cn', 'ru', 'in',
        'gb', 'id', 'de', 'au', 'pl'
    ] LOOP
        EXECUTE format(
            'SELECT EXISTS (SELECT 1 FROM public.%I LIMIT 1)'
            ' OR EXISTS (SELECT 1 FROM public.%I LIMIT 1)',
            'feature_' || as_family || '_202602',
            'feature_' || as_family || '_202603'
        ) INTO has_rows;
        IF NOT has_rows THEN
            RAISE EXCEPTION 'AS 特征族在 2、3 月均为空：feature_%', as_family;
        END IF;
    END LOOP;

    FOREACH month_suffix IN ARRAY ARRAY['202602', '202603'] LOOP
        event_table_name := 'event_table_' || month_suffix;
        FOREACH family IN ARRAY ARRAY[
            'hijack', 'sub_hijack', 'leak_event',
            'prefix_outage', 'as_outage', 'country_outage'
        ] LOOP
            expected_event_type := CASE family WHEN 'leak_event' THEN 'leak' ELSE family END;
            IF EXISTS (
                SELECT 1 FROM domeye_detail_reference_sample AS sample
                WHERE sample.event_type = expected_event_type
            ) THEN
                CONTINUE;
            END IF;

            problem_column := CASE
                WHEN family = 'as_outage' THEN 'asn'
                WHEN family = 'country_outage' THEN 'country'
                ELSE 'prefix'
            END;
            event_id_column := CASE family
                WHEN 'hijack' THEN 'hijack_eventid'
                WHEN 'sub_hijack' THEN 'sub_hijack_eventid'
                WHEN 'leak_event' THEN 'leak_event_id'
                ELSE 'outage_id'
            END;
            fact_table_name := family || '_' || month_suffix;

            EXECUTE format($query$
                SELECT EXISTS (
                    SELECT 1
                    FROM public.%I AS event
                    JOIN public.%I AS fact
                      ON replace(fact.%I::text, '/', '-') = split_part(event.detail_url, '/', 3)
                     AND fact.%I::text = split_part(event.detail_url, '/', 4)
                     AND fact.source = split_part(event.detail_url, '/', 5)
                    WHERE cardinality(string_to_array(event.detail_url, '/')) = 5
                      AND split_part(event.detail_url, '/', 1) = %L
                      AND split_part(event.detail_url, '/', 3) <> ''
                      AND split_part(event.detail_url, '/', 4) ~ '^[0-9]+$'
                      AND split_part(event.detail_url, '/', 5) <> ''
                    LIMIT 1
                )
            $query$,
                event_table_name,
                fact_table_name,
                problem_column,
                event_id_column,
                expected_event_type
            ) INTO has_reference;

            IF has_reference THEN
                INSERT INTO domeye_detail_reference_sample(event_type, month_suffix)
                VALUES (expected_event_type, month_suffix);
            END IF;
        END LOOP;
    END LOOP;

    IF (SELECT count(*) FROM domeye_detail_reference_sample) <> 6 THEN
        RAISE EXCEPTION '六类事件详情引用不完整，缺少：%', (
            SELECT string_agg(expected.event_type, ',' ORDER BY expected.event_type)
            FROM (
                VALUES
                    ('hijack'), ('sub_hijack'), ('leak'),
                    ('prefix_outage'), ('as_outage'), ('country_outage')
            ) AS expected(event_type)
            WHERE NOT EXISTS (
                SELECT 1 FROM domeye_detail_reference_sample AS actual
                WHERE actual.event_type = expected.event_type
            )
        );
    END IF;
END
$block$;

SELECT jsonb_build_object(
    'ok', true,
    'public_table_count', (
        SELECT count(*) FROM pg_tables WHERE schemaname = 'public'
    ),
    'reader_table_count', (
        SELECT count(*) FROM domeye_expected_table
    ),
    'static_info', (
        SELECT payload FROM domeye_info_summary WHERE singleton
    ),
    'detail_reference_type_count', (
        SELECT count(*) FROM domeye_detail_reference_sample
    ),
    'detail_reference_types', (
        SELECT jsonb_agg(event_type ORDER BY event_type)
        FROM domeye_detail_reference_sample
    ),
    'data_start', :'data_start',
    'data_end_exclusive', :'data_end_exclusive',
    'feature_country_min', (SELECT min(t) FROM public.feature_country),
    'feature_country_max', (SELECT max(t) FROM public.feature_country),
    'checkpoint_key', :'checkpoint_key'
)::text;

COMMIT;
