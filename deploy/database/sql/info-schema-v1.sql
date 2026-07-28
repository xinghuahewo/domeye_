\set ON_ERROR_STOP on

BEGIN;

CREATE SCHEMA IF NOT EXISTS info;
REVOKE ALL ON SCHEMA info FROM PUBLIC;

CREATE TABLE IF NOT EXISTS info.schema_metadata (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    schema_version integer NOT NULL CHECK (schema_version = 1),
    implementation_scope text NOT NULL
        CHECK (implementation_scope IN ('core_four_files', 'all_24_files')),
    installed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

INSERT INTO info.schema_metadata(singleton, schema_version, implementation_scope)
VALUES (true, 1, 'core_four_files')
ON CONFLICT (singleton) DO NOTHING;

DO $block$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM info.schema_metadata
        WHERE singleton
          AND schema_version = 1
          AND implementation_scope IN ('core_four_files', 'all_24_files')
    ) THEN
        RAISE EXCEPTION '既有 info schema_metadata 与 v1 static INFO 不兼容';
    END IF;
END
$block$;

CREATE TABLE IF NOT EXISTS info.dataset_release (
    release_sk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    content_id text NOT NULL UNIQUE
        CHECK (content_id ~ '^info_v1_[0-9a-f]{32}$'),
    manifest_sha256 char(64) NOT NULL UNIQUE
        CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    source_release_label text NOT NULL,
    status text NOT NULL DEFAULT 'loading'
        CHECK (status IN (
            'loading', 'validating', 'ready', 'active',
            'retired', 'failed'
        )),
    parser_version text NOT NULL,
    importer_config_sha256 char(64) NOT NULL
        CHECK (importer_config_sha256 ~ '^[0-9a-f]{64}$'),
    code_commit text,
    loaded_scope text[] NOT NULL DEFAULT ARRAY[]::text[],
    quality_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    activated_at timestamptz,
    CHECK (
        (status = 'active' AND activated_at IS NOT NULL)
        OR status <> 'active'
    )
);

CREATE TABLE IF NOT EXISTS info.source_file (
    source_file_sk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    release_sk bigint NOT NULL
        REFERENCES info.dataset_release(release_sk) ON DELETE RESTRICT,
    name text NOT NULL,
    dataset_kind text NOT NULL,
    file_format text NOT NULL
        CHECK (file_format IN ('csv', 'json', 'line_text', 'xls', 'xlsx')),
    role text NOT NULL
        CHECK (role IN ('active', 'loaded_not_consumed', 'config_only', 'legacy')),
    parser text NOT NULL,
    source_priority integer CHECK (source_priority IS NULL OR source_priority >= 0),
    size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
    sha256 char(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    header jsonb NOT NULL,
    header_sha256 char(64) NOT NULL CHECK (header_sha256 ~ '^[0-9a-f]{64}$'),
    physical_line_count bigint NOT NULL CHECK (physical_line_count >= 0),
    logical_record_count bigint NOT NULL CHECK (logical_record_count >= 0),
    count_method text NOT NULL,
    load_status text NOT NULL DEFAULT 'pending'
        CHECK (load_status IN ('pending', 'loading', 'loaded', 'failed')),
    loaded_record_count bigint NOT NULL DEFAULT 0 CHECK (loaded_record_count >= 0),
    quarantined_record_count bigint NOT NULL DEFAULT 0
        CHECK (quarantined_record_count >= 0),
    loaded_at timestamptz,
    UNIQUE (release_sk, name),
    CHECK (
        load_status <> 'loaded'
        OR loaded_record_count + quarantined_record_count = logical_record_count
    )
);

CREATE TABLE IF NOT EXISTS info.import_run (
    import_run_sk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    release_sk bigint NOT NULL
        REFERENCES info.dataset_release(release_sk) ON DELETE RESTRICT,
    idempotency_key char(64) NOT NULL UNIQUE
        CHECK (idempotency_key ~ '^[0-9a-f]{64}$'),
    attempt_no integer NOT NULL DEFAULT 1 CHECK (attempt_no > 0),
    parser_version text NOT NULL,
    importer_config_sha256 char(64) NOT NULL
        CHECK (importer_config_sha256 ~ '^[0-9a-f]{64}$'),
    scope text NOT NULL,
    status text NOT NULL
        CHECK (status IN ('created', 'loading', 'validating', 'completed', 'failed')),
    checkpoint jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_summary text,
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    UNIQUE (release_sk, attempt_no)
);

CREATE TABLE IF NOT EXISTS info.quality_result (
    quality_result_sk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    release_sk bigint NOT NULL
        REFERENCES info.dataset_release(release_sk) ON DELETE RESTRICT,
    import_run_sk bigint
        REFERENCES info.import_run(import_run_sk) ON DELETE RESTRICT,
    rule_id text NOT NULL,
    rule_version integer NOT NULL CHECK (rule_version > 0),
    blocking boolean NOT NULL,
    status text NOT NULL CHECK (status IN ('pass', 'fail')),
    observed jsonb NOT NULL,
    expected jsonb NOT NULL,
    evidence_ref jsonb NOT NULL DEFAULT '{}'::jsonb,
    checked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (release_sk, rule_id, rule_version)
);

CREATE TABLE IF NOT EXISTS info.quarantine (
    quarantine_sk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    release_sk bigint NOT NULL
        REFERENCES info.dataset_release(release_sk) ON DELETE RESTRICT,
    source_file_sk bigint NOT NULL
        REFERENCES info.source_file(source_file_sk) ON DELETE RESTRICT,
    source_row_no bigint NOT NULL CHECK (source_row_no > 0),
    natural_key text,
    reason_code text NOT NULL,
    raw_record_sha256 char(64) NOT NULL
        CHECK (raw_record_sha256 ~ '^[0-9a-f]{64}$'),
    restricted_payload jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (release_sk, source_file_sk, source_row_no, reason_code)
);
REVOKE ALL ON info.quarantine FROM PUBLIC;

CREATE TABLE IF NOT EXISTS info.active_release (
    profile_name text PRIMARY KEY,
    release_sk bigint NOT NULL UNIQUE
        REFERENCES info.dataset_release(release_sk) ON DELETE RESTRICT,
    previous_release_sk bigint
        REFERENCES info.dataset_release(release_sk) ON DELETE RESTRICT,
    activated_at timestamptz NOT NULL,
    activated_by text NOT NULL,
    activation_reason text NOT NULL,
    CHECK (previous_release_sk IS NULL OR previous_release_sk <> release_sk)
);

CREATE TABLE IF NOT EXISTS info.country (
    release_sk bigint NOT NULL
        REFERENCES info.dataset_release(release_sk) ON DELETE RESTRICT,
    country_sk bigint GENERATED ALWAYS AS IDENTITY,
    source_file_sk bigint NOT NULL
        REFERENCES info.source_file(source_file_sk) ON DELETE RESTRICT,
    source_row_no bigint NOT NULL CHECK (source_row_no > 0),
    source_record_sha256 char(64) NOT NULL
        CHECK (source_record_sha256 ~ '^[0-9a-f]{64}$'),
    english_full_name text,
    english_short_name text,
    chinese_short_name text,
    alpha2 text,
    alpha3 text,
    digital_code text,
    phone_code text,
    jet_lag text,
    latitude double precision,
    longitude double precision,
    quality_status text NOT NULL DEFAULT 'valid'
        CHECK (quality_status IN ('valid', 'incomplete')),
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (release_sk, country_sk),
    UNIQUE (release_sk, source_file_sk, source_row_no),
    CHECK (alpha2 IS NULL OR alpha2 ~ '^[A-Z]{2}$'),
    CHECK (alpha3 IS NULL OR alpha3 ~ '^[A-Z]{3}$'),
    CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
    CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180)
);
CREATE UNIQUE INDEX IF NOT EXISTS info_country_alpha2_unique
    ON info.country(release_sk, alpha2)
    WHERE alpha2 IS NOT NULL;

CREATE TABLE IF NOT EXISTS info.country_alias (
    release_sk bigint NOT NULL,
    alias_kind text NOT NULL,
    alias_value text NOT NULL,
    country_sk bigint NOT NULL,
    source_file_sk bigint NOT NULL
        REFERENCES info.source_file(source_file_sk) ON DELETE RESTRICT,
    source_row_no bigint NOT NULL CHECK (source_row_no > 0),
    source_record_sha256 char(64) NOT NULL
        CHECK (source_record_sha256 ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (release_sk, alias_kind, alias_value),
    FOREIGN KEY (release_sk, country_sk)
        REFERENCES info.country(release_sk, country_sk) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS info.autonomous_system (
    release_sk bigint NOT NULL
        REFERENCES info.dataset_release(release_sk) ON DELETE RESTRICT,
    asn bigint NOT NULL CHECK (asn BETWEEN 0 AND 4294967295),
    source_file_sk bigint NOT NULL
        REFERENCES info.source_file(source_file_sk) ON DELETE RESTRICT,
    source_row_no bigint NOT NULL CHECK (source_row_no > 0),
    source_record_sha256 char(64) NOT NULL
        CHECK (source_record_sha256 ~ '^[0-9a-f]{64}$'),
    as_name text,
    country_code text,
    country_name_cn text,
    org_name text,
    org_name_cn text,
    as_type text,
    description text,
    description_cn text,
    is_ddos_provider boolean,
    global_rank integer,
    country_rank integer,
    v4_prefix_count integer,
    v6_prefix_count integer,
    v4_peer_count integer,
    v6_peer_count integer,
    v4_upstream_count integer,
    v6_upstream_count integer,
    v4_downstream_count integer,
    v6_downstream_count integer,
    source_order bigint NOT NULL CHECK (source_order > 0),
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (release_sk, asn),
    UNIQUE (release_sk, source_file_sk, source_row_no)
);
CREATE INDEX IF NOT EXISTS info_autonomous_system_country
    ON info.autonomous_system(release_sk, country_code, asn);
CREATE INDEX IF NOT EXISTS info_autonomous_system_rank
    ON info.autonomous_system(release_sk, global_rank, asn)
    WHERE global_rank IS NOT NULL;

CREATE TABLE IF NOT EXISTS info.as_contact (
    release_sk bigint NOT NULL,
    asn bigint NOT NULL,
    contact_kind text NOT NULL
        CHECK (contact_kind IN ('admin', 'tech', 'abuse')),
    ordinal integer NOT NULL CHECK (ordinal > 0),
    source_file_sk bigint NOT NULL
        REFERENCES info.source_file(source_file_sk) ON DELETE RESTRICT,
    source_row_no bigint NOT NULL CHECK (source_row_no > 0),
    source_record_sha256 char(64) NOT NULL
        CHECK (source_record_sha256 ~ '^[0-9a-f]{64}$'),
    contact_value jsonb NOT NULL,
    PRIMARY KEY (release_sk, asn, contact_kind, ordinal),
    FOREIGN KEY (release_sk, asn)
        REFERENCES info.autonomous_system(release_sk, asn) ON DELETE RESTRICT
);
REVOKE ALL ON info.as_contact FROM PUBLIC;

CREATE TABLE IF NOT EXISTS info.as_policy_member (
    release_sk bigint NOT NULL,
    asn bigint NOT NULL,
    direction text NOT NULL CHECK (direction IN ('import', 'export')),
    ordinal integer NOT NULL CHECK (ordinal > 0),
    token text NOT NULL,
    parsed_asn bigint CHECK (parsed_asn BETWEEN 0 AND 4294967295),
    source_file_sk bigint NOT NULL
        REFERENCES info.source_file(source_file_sk) ON DELETE RESTRICT,
    source_row_no bigint NOT NULL CHECK (source_row_no > 0),
    source_record_sha256 char(64) NOT NULL
        CHECK (source_record_sha256 ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (release_sk, asn, direction, ordinal),
    FOREIGN KEY (release_sk, asn)
        REFERENCES info.autonomous_system(release_sk, asn) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS info.as_relation (
    release_sk bigint NOT NULL,
    source_asn bigint NOT NULL CHECK (source_asn BETWEEN 0 AND 4294967295),
    target_asn bigint NOT NULL CHECK (target_asn BETWEEN 0 AND 4294967295),
    relation_kind text NOT NULL
        CHECK (relation_kind IN (
            'provider', 'customer', 'peer', 'sibling',
            'upstream', 'downstream'
        )),
    afi smallint NOT NULL DEFAULT 0 CHECK (afi IN (0, 4, 6)),
    ordinal integer NOT NULL CHECK (ordinal > 0),
    source_field text NOT NULL,
    source_file_sk bigint NOT NULL
        REFERENCES info.source_file(source_file_sk) ON DELETE RESTRICT,
    source_row_no bigint NOT NULL CHECK (source_row_no > 0),
    source_record_sha256 char(64) NOT NULL
        CHECK (source_record_sha256 ~ '^[0-9a-f]{64}$'),
    source_active boolean NOT NULL,
    PRIMARY KEY (
        release_sk, source_asn, target_asn, relation_kind,
        afi, source_file_sk, source_row_no, ordinal
    )
);
CREATE INDEX IF NOT EXISTS info_as_relation_lookup
    ON info.as_relation(release_sk, source_asn, relation_kind, target_asn);

CREATE TABLE IF NOT EXISTS info.important_as (
    release_sk bigint NOT NULL
        REFERENCES info.dataset_release(release_sk) ON DELETE RESTRICT,
    asn bigint NOT NULL CHECK (asn BETWEEN 0 AND 4294967295),
    source_file_sk bigint NOT NULL
        REFERENCES info.source_file(source_file_sk) ON DELETE RESTRICT,
    source_row_no bigint NOT NULL CHECK (source_row_no > 0),
    source_record_sha256 char(64) NOT NULL
        CHECK (source_record_sha256 ~ '^[0-9a-f]{64}$'),
    label text,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (release_sk, asn),
    UNIQUE (release_sk, source_file_sk, source_row_no)
);

CREATE TABLE IF NOT EXISTS info.prefix (
    release_sk bigint NOT NULL
        REFERENCES info.dataset_release(release_sk) ON DELETE RESTRICT,
    prefix_raw text NOT NULL,
    source_file_sk bigint NOT NULL
        REFERENCES info.source_file(source_file_sk) ON DELETE RESTRICT,
    source_row_no bigint NOT NULL CHECK (source_row_no > 0),
    source_record_sha256 char(64) NOT NULL
        CHECK (source_record_sha256 ~ '^[0-9a-f]{64}$'),
    prefix_cidr cidr NOT NULL,
    canonical_status text NOT NULL
        CHECK (canonical_status IN ('canonical', 'noncanonical')),
    name text,
    description text,
    route_raw text,
    bgp_raw text,
    country_code text,
    source_name text,
    declared_domain_count integer,
    declared_authoritative_domain_count integer,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (release_sk, prefix_raw),
    UNIQUE (release_sk, source_file_sk, source_row_no)
) PARTITION BY LIST (release_sk);

CREATE INDEX IF NOT EXISTS info_prefix_cidr_gist
    ON info.prefix USING gist (prefix_cidr inet_ops);

CREATE TABLE IF NOT EXISTS info.prefix_origin (
    release_sk bigint NOT NULL
        REFERENCES info.dataset_release(release_sk) ON DELETE RESTRICT,
    prefix_raw text NOT NULL,
    asn bigint NOT NULL CHECK (asn BETWEEN 0 AND 4294967295),
    origin_source text NOT NULL CHECK (origin_source IN ('route', 'bgp', 'pfx2as')),
    ordinal integer NOT NULL CHECK (ordinal > 0),
    source_value jsonb,
    source_file_sk bigint NOT NULL
        REFERENCES info.source_file(source_file_sk) ON DELETE RESTRICT,
    source_row_no bigint NOT NULL CHECK (source_row_no > 0),
    source_record_sha256 char(64) NOT NULL
        CHECK (source_record_sha256 ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (
        release_sk, prefix_raw, asn, origin_source,
        source_file_sk, source_row_no, ordinal
    )
) PARTITION BY LIST (release_sk);
CREATE INDEX IF NOT EXISTS info_prefix_origin_lookup
    ON info.prefix_origin(release_sk, prefix_raw, asn);

CREATE TABLE IF NOT EXISTS info.prefix_domain (
    release_sk bigint NOT NULL
        REFERENCES info.dataset_release(release_sk) ON DELETE RESTRICT,
    prefix_raw text NOT NULL,
    domain_key_raw text NOT NULL,
    domain_role text NOT NULL CHECK (domain_role IN ('normal', 'authoritative')),
    ordinal integer NOT NULL CHECK (ordinal > 0),
    source_file_sk bigint NOT NULL
        REFERENCES info.source_file(source_file_sk) ON DELETE RESTRICT,
    source_row_no bigint NOT NULL CHECK (source_row_no > 0),
    source_record_sha256 char(64) NOT NULL
        CHECK (source_record_sha256 ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (
        release_sk, prefix_raw, domain_role, ordinal,
        source_file_sk, source_row_no
    )
) PARTITION BY LIST (release_sk);

CREATE OR REPLACE FUNCTION info.ensure_release_partitions(target_release_sk bigint)
RETURNS void
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, info
AS $function$
DECLARE
    partition_suffix text := target_release_sk::text;
BEGIN
    IF target_release_sk <= 0
       OR NOT EXISTS (
           SELECT 1
           FROM info.dataset_release
           WHERE release_sk = target_release_sk
       ) THEN
        RAISE EXCEPTION '未知或非法的 info release_sk：%', target_release_sk;
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended('info_partition:' || target_release_sk::text, 0)
    );
    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS info.prefix_r%s '
        'PARTITION OF info.prefix FOR VALUES IN (%s)',
        partition_suffix,
        target_release_sk
    );
    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS info.prefix_origin_r%s '
        'PARTITION OF info.prefix_origin FOR VALUES IN (%s)',
        partition_suffix,
        target_release_sk
    );
    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS info.prefix_domain_r%s '
        'PARTITION OF info.prefix_domain FOR VALUES IN (%s)',
        partition_suffix,
        target_release_sk
    );
END
$function$;
REVOKE ALL ON FUNCTION info.ensure_release_partitions(bigint) FROM PUBLIC;

CREATE OR REPLACE FUNCTION info.activate_release(
    target_profile_name text,
    target_release_sk bigint,
    actor text,
    reason text
)
RETURNS void
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, info
AS $function$
DECLARE
    target_status text;
    old_release_sk bigint;
BEGIN
    IF target_profile_name IS NULL OR btrim(target_profile_name) = ''
       OR actor IS NULL OR btrim(actor) = ''
       OR reason IS NULL OR btrim(reason) = '' THEN
        RAISE EXCEPTION 'profile、actor 和 reason 均不能为空';
    END IF;

    PERFORM pg_advisory_xact_lock(73922, hashtext(target_profile_name));
    SELECT status INTO target_status
    FROM info.dataset_release
    WHERE release_sk = target_release_sk
    FOR UPDATE;
    IF target_status IS DISTINCT FROM 'ready' THEN
        RAISE EXCEPTION 'info release % 不是 ready 状态：%',
            target_release_sk, target_status;
    END IF;
    IF (
        SELECT count(*)
        FROM info.source_file
        WHERE release_sk = target_release_sk
          AND load_status = 'loaded'
    ) <> 24 THEN
        RAISE EXCEPTION 'info release % 尚未完整装载 24 个文件', target_release_sk;
    END IF;
    IF EXISTS (
        SELECT 1
        FROM info.quality_result
        WHERE release_sk = target_release_sk
          AND blocking
          AND status <> 'pass'
    ) THEN
        RAISE EXCEPTION 'info release % 存在未通过的阻断质量规则', target_release_sk;
    END IF;

    SELECT release_sk INTO old_release_sk
    FROM info.active_release
    WHERE profile_name = target_profile_name
    FOR UPDATE;

    IF old_release_sk IS NOT NULL AND old_release_sk <> target_release_sk THEN
        UPDATE info.dataset_release
        SET status = 'retired'
        WHERE release_sk = old_release_sk
          AND status = 'active';
    END IF;

    INSERT INTO info.active_release(
        profile_name,
        release_sk,
        previous_release_sk,
        activated_at,
        activated_by,
        activation_reason
    ) VALUES (
        target_profile_name,
        target_release_sk,
        old_release_sk,
        clock_timestamp(),
        actor,
        reason
    )
    ON CONFLICT (profile_name) DO UPDATE
    SET release_sk = EXCLUDED.release_sk,
        previous_release_sk = EXCLUDED.previous_release_sk,
        activated_at = EXCLUDED.activated_at,
        activated_by = EXCLUDED.activated_by,
        activation_reason = EXCLUDED.activation_reason;

    UPDATE info.dataset_release
    SET status = 'active',
        activated_at = clock_timestamp()
    WHERE release_sk = target_release_sk;
END
$function$;
REVOKE ALL ON FUNCTION info.activate_release(text, bigint, text, text) FROM PUBLIC;

CREATE OR REPLACE VIEW info.current_autonomous_system AS
SELECT entity.*
FROM info.autonomous_system AS entity
JOIN info.active_release AS active
  ON active.profile_name = 'core'
 AND active.release_sk = entity.release_sk;

CREATE OR REPLACE VIEW info.current_important_as AS
SELECT entity.*
FROM info.important_as AS entity
JOIN info.active_release AS active
  ON active.profile_name = 'core'
 AND active.release_sk = entity.release_sk;

CREATE OR REPLACE VIEW info.current_prefix AS
SELECT entity.*
FROM info.prefix AS entity
JOIN info.active_release AS active
  ON active.profile_name = 'core'
 AND active.release_sk = entity.release_sk;

CREATE OR REPLACE VIEW info.current_country AS
SELECT entity.*
FROM info.country AS entity
JOIN info.active_release AS active
  ON active.profile_name = 'core'
 AND active.release_sk = entity.release_sk;

COMMIT;
