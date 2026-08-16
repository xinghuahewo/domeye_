\set ON_ERROR_STOP on

BEGIN;

DO $block$
BEGIN
    IF to_regclass('info.schema_metadata') IS NULL
       OR to_regclass('info.dataset_release') IS NULL
       OR to_regclass('info.source_file') IS NULL THEN
        RAISE EXCEPTION 'S2 schema 必须建立在已完成 S1 的 info schema 上';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM info.schema_metadata
        WHERE singleton
          AND schema_version = 1
          AND implementation_scope IN ('core_four_files', 'all_24_files')
    ) THEN
        RAISE EXCEPTION '既有 info schema_metadata 与 S2 不兼容';
    END IF;
END
$block$;

ALTER TABLE info.schema_metadata
    DROP CONSTRAINT IF EXISTS schema_metadata_implementation_scope_check;
ALTER TABLE info.schema_metadata
    ADD CONSTRAINT schema_metadata_implementation_scope_check
    CHECK (implementation_scope IN ('core_four_files', 'all_24_files'));

CREATE TABLE IF NOT EXISTS info.source_record (
    release_sk bigint NOT NULL
        REFERENCES info.dataset_release(release_sk) ON DELETE RESTRICT,
    source_file_sk bigint NOT NULL
        REFERENCES info.source_file(source_file_sk) ON DELETE RESTRICT,
    source_row_no bigint NOT NULL CHECK (source_row_no > 0),
    source_record_sha256 char(64) NOT NULL
        CHECK (source_record_sha256 ~ '^[0-9a-f]{64}$'),
    dataset_kind text NOT NULL,
    record_kind text NOT NULL,
    natural_key text,
    disposition text NOT NULL
        CHECK (disposition IN ('accepted', 'quarantined')),
    reason_code text,
    quality_flags jsonb NOT NULL DEFAULT '[]'::jsonb,
    restricted_payload jsonb,
    PRIMARY KEY (release_sk, source_file_sk, source_row_no),
    CHECK (
        (disposition = 'accepted' AND reason_code IS NULL)
        OR (disposition = 'quarantined'
            AND reason_code IS NOT NULL
            AND btrim(reason_code) <> '')
    )
) PARTITION BY LIST (release_sk);
REVOKE ALL ON info.source_record FROM PUBLIC;

CREATE TABLE IF NOT EXISTS info.mapping_record (
    release_sk bigint NOT NULL
        REFERENCES info.dataset_release(release_sk) ON DELETE RESTRICT,
    source_file_sk bigint NOT NULL
        REFERENCES info.source_file(source_file_sk) ON DELETE RESTRICT,
    source_row_no bigint NOT NULL CHECK (source_row_no > 0),
    source_record_sha256 char(64) NOT NULL
        CHECK (source_record_sha256 ~ '^[0-9a-f]{64}$'),
    mapping_kind text NOT NULL
        CHECK (mapping_kind IN (
            'as_prefix_history', 'as_relation', 'private_as_location'
        )),
    natural_key text NOT NULL,
    item_count bigint NOT NULL CHECK (item_count >= 0),
    source_active boolean NOT NULL,
    PRIMARY KEY (release_sk, source_file_sk, source_row_no)
);
CREATE INDEX IF NOT EXISTS info_mapping_record_lookup
    ON info.mapping_record(
        release_sk, mapping_kind, natural_key, source_file_sk
    );

CREATE TABLE IF NOT EXISTS info.domain_record (
    release_sk bigint NOT NULL
        REFERENCES info.dataset_release(release_sk) ON DELETE RESTRICT,
    source_file_sk bigint NOT NULL
        REFERENCES info.source_file(source_file_sk) ON DELETE RESTRICT,
    source_row_no bigint NOT NULL CHECK (source_row_no > 0),
    source_record_sha256 char(64) NOT NULL
        CHECK (source_record_sha256 ~ '^[0-9a-f]{64}$'),
    domain_key_raw text NOT NULL,
    normalized_key text NOT NULL,
    normalization_version text NOT NULL DEFAULT 'identity-v1',
    title text,
    industry text,
    ip_raw text,
    ip_prefix_raw text,
    authoritative_ip_raw text,
    source_priority integer NOT NULL CHECK (source_priority >= 0),
    source_active boolean NOT NULL DEFAULT false,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (release_sk, source_file_sk, source_row_no)
) PARTITION BY LIST (release_sk);
CREATE INDEX IF NOT EXISTS info_domain_record_lookup
    ON info.domain_record(
        release_sk, domain_key_raw, source_priority, source_row_no
    );
CREATE UNIQUE INDEX IF NOT EXISTS info_domain_record_active_key
    ON info.domain_record(release_sk, domain_key_raw)
    WHERE source_active;

CREATE TABLE IF NOT EXISTS info.domain_address (
    release_sk bigint NOT NULL
        REFERENCES info.dataset_release(release_sk) ON DELETE RESTRICT,
    source_file_sk bigint NOT NULL
        REFERENCES info.source_file(source_file_sk) ON DELETE RESTRICT,
    source_row_no bigint NOT NULL CHECK (source_row_no > 0),
    source_record_sha256 char(64) NOT NULL
        CHECK (source_record_sha256 ~ '^[0-9a-f]{64}$'),
    domain_key_raw text NOT NULL,
    address_role text NOT NULL
        CHECK (address_role IN (
            'resolved', 'resolved_prefix', 'authoritative'
        )),
    ordinal integer NOT NULL CHECK (ordinal > 0),
    value_raw text NOT NULL,
    ip_value inet,
    prefix_cidr cidr,
    quality_status text NOT NULL
        CHECK (quality_status IN ('valid', 'unparsed')),
    PRIMARY KEY (
        release_sk, source_file_sk, source_row_no,
        address_role, ordinal
    )
) PARTITION BY LIST (release_sk);
CREATE INDEX IF NOT EXISTS info_domain_address_domain
    ON info.domain_address(release_sk, domain_key_raw, address_role);

CREATE TABLE IF NOT EXISTS info.as_prefix_history (
    release_sk bigint NOT NULL
        REFERENCES info.dataset_release(release_sk) ON DELETE RESTRICT,
    source_file_sk bigint NOT NULL
        REFERENCES info.source_file(source_file_sk) ON DELETE RESTRICT,
    source_row_no bigint NOT NULL CHECK (source_row_no > 0),
    source_record_sha256 char(64) NOT NULL
        CHECK (source_record_sha256 ~ '^[0-9a-f]{64}$'),
    asn bigint NOT NULL CHECK (asn BETWEEN 0 AND 4294967295),
    prefix_raw text NOT NULL,
    prefix_cidr cidr,
    source_value jsonb,
    quality_status text NOT NULL
        CHECK (quality_status IN ('valid', 'invalid_prefix')),
    source_active boolean NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal > 0),
    PRIMARY KEY (
        release_sk, source_file_sk, source_row_no, ordinal
    )
) PARTITION BY LIST (release_sk);
CREATE INDEX IF NOT EXISTS info_as_prefix_history_lookup
    ON info.as_prefix_history(release_sk, asn, prefix_cidr);

CREATE TABLE IF NOT EXISTS info.important_prefix (
    release_sk bigint NOT NULL
        REFERENCES info.dataset_release(release_sk) ON DELETE RESTRICT,
    source_file_sk bigint NOT NULL
        REFERENCES info.source_file(source_file_sk) ON DELETE RESTRICT,
    source_row_no bigint NOT NULL CHECK (source_row_no > 0),
    source_record_sha256 char(64) NOT NULL
        CHECK (source_record_sha256 ~ '^[0-9a-f]{64}$'),
    prefix_raw text NOT NULL,
    prefix_cidr cidr NOT NULL,
    afi smallint NOT NULL CHECK (afi IN (4, 6)),
    number_raw text,
    host_raw text,
    source_active boolean NOT NULL,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (release_sk, source_file_sk, source_row_no)
);
CREATE INDEX IF NOT EXISTS info_important_prefix_lookup
    ON info.important_prefix(release_sk, prefix_raw, source_file_sk, source_row_no);

CREATE TABLE IF NOT EXISTS info.important_domain (
    release_sk bigint NOT NULL
        REFERENCES info.dataset_release(release_sk) ON DELETE RESTRICT,
    source_file_sk bigint NOT NULL
        REFERENCES info.source_file(source_file_sk) ON DELETE RESTRICT,
    source_row_no bigint NOT NULL CHECK (source_row_no > 0),
    source_record_sha256 char(64) NOT NULL
        CHECK (source_record_sha256 ~ '^[0-9a-f]{64}$'),
    domain_key_raw text NOT NULL,
    source_value jsonb,
    source_active boolean NOT NULL,
    PRIMARY KEY (release_sk, source_file_sk, source_row_no)
);
CREATE INDEX IF NOT EXISTS info_important_domain_lookup
    ON info.important_domain(release_sk, domain_key_raw)
    WHERE source_active;

CREATE TABLE IF NOT EXISTS info.private_as_location (
    release_sk bigint NOT NULL
        REFERENCES info.dataset_release(release_sk) ON DELETE RESTRICT,
    source_file_sk bigint NOT NULL
        REFERENCES info.source_file(source_file_sk) ON DELETE RESTRICT,
    source_row_no bigint NOT NULL CHECK (source_row_no > 0),
    source_record_sha256 char(64) NOT NULL
        CHECK (source_record_sha256 ~ '^[0-9a-f]{64}$'),
    public_asn_raw text NOT NULL,
    public_asn bigint CHECK (public_asn BETWEEN 0 AND 4294967295),
    private_asn_raw text NOT NULL,
    private_asn bigint CHECK (private_asn BETWEEN 0 AND 4294967295),
    ip_num jsonb,
    city text,
    quality_status text NOT NULL
        CHECK (quality_status IN ('valid', 'invalid_asn')),
    source_active boolean NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal > 0),
    PRIMARY KEY (
        release_sk, source_file_sk, source_row_no, ordinal
    )
);
CREATE INDEX IF NOT EXISTS info_private_as_location_lookup
    ON info.private_as_location(release_sk, public_asn, private_asn)
    WHERE source_active;

CREATE TABLE IF NOT EXISTS info.route_triplet_baseline (
    release_sk bigint NOT NULL
        REFERENCES info.dataset_release(release_sk) ON DELETE RESTRICT,
    source_file_sk bigint NOT NULL
        REFERENCES info.source_file(source_file_sk) ON DELETE RESTRICT,
    source_row_no bigint NOT NULL CHECK (source_row_no > 0),
    source_record_sha256 char(64) NOT NULL
        CHECK (source_record_sha256 ~ '^[0-9a-f]{64}$'),
    first_as bigint NOT NULL CHECK (first_as BETWEEN 0 AND 4294967295),
    second_as bigint NOT NULL CHECK (second_as BETWEEN 0 AND 4294967295),
    third_as bigint NOT NULL CHECK (third_as BETWEEN 0 AND 4294967295),
    appear_time_raw text,
    appear_num bigint,
    stability double precision NOT NULL,
    is_leak boolean,
    source_active boolean NOT NULL,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (release_sk, source_file_sk, source_row_no)
) PARTITION BY LIST (release_sk);
CREATE INDEX IF NOT EXISTS info_route_triplet_lookup
    ON info.route_triplet_baseline(
        release_sk, first_as, second_as, third_as
    ) INCLUDE (stability);

CREATE TABLE IF NOT EXISTS info.dns_observation (
    release_sk bigint NOT NULL
        REFERENCES info.dataset_release(release_sk) ON DELETE RESTRICT,
    source_file_sk bigint NOT NULL
        REFERENCES info.source_file(source_file_sk) ON DELETE RESTRICT,
    source_row_no bigint NOT NULL CHECK (source_row_no > 0),
    source_record_sha256 char(64) NOT NULL
        CHECK (source_record_sha256 ~ '^[0-9a-f]{64}$'),
    dataset_kind text NOT NULL CHECK (dataset_kind IN ('top_nx', 'top_ip')),
    domain_raw text,
    ip_raw text,
    source_index_raw text,
    raw_line text,
    quality_status text NOT NULL
        CHECK (quality_status IN ('valid', 'incomplete')),
    PRIMARY KEY (release_sk, source_file_sk, source_row_no)
) PARTITION BY LIST (release_sk);
CREATE INDEX IF NOT EXISTS info_dns_observation_lookup
    ON info.dns_observation(release_sk, dataset_kind, domain_raw);

CREATE TABLE IF NOT EXISTS info.as_rank (
    release_sk bigint NOT NULL
        REFERENCES info.dataset_release(release_sk) ON DELETE RESTRICT,
    source_file_sk bigint NOT NULL
        REFERENCES info.source_file(source_file_sk) ON DELETE RESTRICT,
    source_row_no bigint NOT NULL CHECK (source_row_no > 0),
    source_record_sha256 char(64) NOT NULL
        CHECK (source_record_sha256 ~ '^[0-9a-f]{64}$'),
    asn bigint NOT NULL CHECK (asn BETWEEN 0 AND 4294967295),
    rank_value text,
    country_code text,
    organization_name text,
    as_name text,
    as_type text,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (release_sk, source_file_sk, source_row_no)
);
CREATE INDEX IF NOT EXISTS info_as_rank_lookup
    ON info.as_rank(release_sk, asn, source_file_sk);

CREATE TABLE IF NOT EXISTS info.organization (
    release_sk bigint NOT NULL
        REFERENCES info.dataset_release(release_sk) ON DELETE RESTRICT,
    source_file_sk bigint NOT NULL
        REFERENCES info.source_file(source_file_sk) ON DELETE RESTRICT,
    source_row_no bigint NOT NULL CHECK (source_row_no > 0),
    source_record_sha256 char(64) NOT NULL
        CHECK (source_record_sha256 ~ '^[0-9a-f]{64}$'),
    org_key text NOT NULL,
    country_code text,
    country_name_cn text,
    org_name text,
    org_name_cn text,
    sibling_as_count integer,
    v4_prefix_count integer,
    v6_prefix_count integer,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (release_sk, source_file_sk, source_row_no)
);
CREATE INDEX IF NOT EXISTS info_organization_lookup
    ON info.organization(release_sk, org_key, source_file_sk, source_row_no);

CREATE TABLE IF NOT EXISTS info.organization_as (
    release_sk bigint NOT NULL,
    source_file_sk bigint NOT NULL
        REFERENCES info.source_file(source_file_sk) ON DELETE RESTRICT,
    source_row_no bigint NOT NULL CHECK (source_row_no > 0),
    source_record_sha256 char(64) NOT NULL
        CHECK (source_record_sha256 ~ '^[0-9a-f]{64}$'),
    org_key text NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal > 0),
    asn_token text NOT NULL,
    asn bigint CHECK (asn BETWEEN 0 AND 4294967295),
    quality_status text NOT NULL
        CHECK (quality_status IN ('valid', 'invalid_asn')),
    PRIMARY KEY (
        release_sk, source_file_sk, source_row_no, ordinal
    ),
    FOREIGN KEY (release_sk, source_file_sk, source_row_no)
        REFERENCES info.organization(
            release_sk, source_file_sk, source_row_no
        ) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS info.organization_prefix (
    release_sk bigint NOT NULL,
    source_file_sk bigint NOT NULL
        REFERENCES info.source_file(source_file_sk) ON DELETE RESTRICT,
    source_row_no bigint NOT NULL CHECK (source_row_no > 0),
    source_record_sha256 char(64) NOT NULL
        CHECK (source_record_sha256 ~ '^[0-9a-f]{64}$'),
    org_key text NOT NULL,
    afi smallint NOT NULL CHECK (afi IN (4, 6)),
    ordinal integer NOT NULL CHECK (ordinal > 0),
    prefix_raw text NOT NULL,
    prefix_cidr cidr,
    quality_status text NOT NULL
        CHECK (quality_status IN ('valid', 'invalid_prefix')),
    PRIMARY KEY (
        release_sk, source_file_sk, source_row_no, afi, ordinal
    ),
    FOREIGN KEY (release_sk, source_file_sk, source_row_no)
        REFERENCES info.organization(
            release_sk, source_file_sk, source_row_no
        ) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS info.legacy_record (
    release_sk bigint NOT NULL
        REFERENCES info.dataset_release(release_sk) ON DELETE RESTRICT,
    source_file_sk bigint NOT NULL
        REFERENCES info.source_file(source_file_sk) ON DELETE RESTRICT,
    source_row_no bigint NOT NULL CHECK (source_row_no > 0),
    source_record_sha256 char(64) NOT NULL
        CHECK (source_record_sha256 ~ '^[0-9a-f]{64}$'),
    dataset_kind text NOT NULL,
    natural_key text,
    payload jsonb NOT NULL,
    payload_sha256 char(64) NOT NULL
        CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    source_active boolean NOT NULL DEFAULT false,
    PRIMARY KEY (release_sk, source_file_sk, source_row_no)
) PARTITION BY LIST (release_sk);
REVOKE ALL ON info.legacy_record FROM PUBLIC;
CREATE INDEX IF NOT EXISTS info_legacy_record_lookup
    ON info.legacy_record(
        release_sk, source_file_sk, natural_key
    );

CREATE OR REPLACE FUNCTION info.ensure_release_partitions(target_release_sk bigint)
RETURNS void
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, info
AS $function$
DECLARE
    partition_suffix text := target_release_sk::text;
    parent_name text;
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
    FOREACH parent_name IN ARRAY ARRAY[
        'prefix',
        'prefix_origin',
        'prefix_domain',
        'source_record',
        'domain_record',
        'domain_address',
        'as_prefix_history',
        'route_triplet_baseline',
        'dns_observation',
        'legacy_record'
    ]
    LOOP
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS info.%I '
            'PARTITION OF info.%I FOR VALUES IN (%s)',
            parent_name || '_r' || partition_suffix,
            parent_name,
            target_release_sk
        );
    END LOOP;
END
$function$;
REVOKE ALL ON FUNCTION info.ensure_release_partitions(bigint) FROM PUBLIC;

UPDATE info.schema_metadata
SET implementation_scope = 'all_24_files'
WHERE singleton
  AND schema_version = 1;

DO $block$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM info.schema_metadata
        WHERE singleton
          AND schema_version = 1
          AND implementation_scope = 'all_24_files'
    ) THEN
        RAISE EXCEPTION 'S2 schema_metadata 未能升级到 all_24_files';
    END IF;
END
$block$;

COMMIT;
