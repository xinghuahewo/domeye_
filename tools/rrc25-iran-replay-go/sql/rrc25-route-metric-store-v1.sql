\set ON_ERROR_STOP on

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE SCHEMA IF NOT EXISTS domeye_data;

CREATE TABLE domeye_data.candidate_registry (
    candidate_id text PRIMARY KEY,
    collector_id text NOT NULL CHECK (collector_id = 'rrc25'),
    window_start_utc timestamptz NOT NULL,
    window_end_exclusive_utc timestamptz NOT NULL,
    expected_state_point_count integer NOT NULL CHECK (expected_state_point_count = 4320),
    expected_country_bucket_count integer NOT NULL CHECK (expected_country_bucket_count = 241),
    mapping_version text NOT NULL CHECK (mapping_version ~ '^[0-9a-f]{64}$'),
    status text NOT NULL CHECK (status IN ('loading', 'complete', 'failed')),
    CHECK (window_start_utc = timestamptz '2026-02-24T00:00:00Z'),
    CHECK (window_end_exclusive_utc = timestamptz '2026-03-11T00:00:00Z')
);

CREATE TABLE domeye_data.dataset_registry (
    dataset_id text PRIMARY KEY,
    candidate_id text NOT NULL REFERENCES domeye_data.candidate_registry(candidate_id),
    dataset_kind text NOT NULL CHECK (dataset_kind IN ('route_event', 'route_state', 'route_metric')),
    collector_id text NOT NULL CHECK (collector_id = 'rrc25'),
    window_start_utc timestamptz NOT NULL,
    window_end_exclusive_utc timestamptz NOT NULL,
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    manifest_sha256 text CHECK (manifest_sha256 IS NULL OR manifest_sha256 ~ '^[0-9a-f]{64}$'),
    mapping_version text NOT NULL CHECK (mapping_version ~ '^[0-9a-f]{64}$'),
    source_dataset_ids text[] NOT NULL DEFAULT ARRAY[]::text[],
    implementation_id text NOT NULL,
    projector_name text,
    projector_version text,
    status text NOT NULL CHECK (status = 'complete'),
    UNIQUE (candidate_id, dataset_kind),
    CHECK (window_start_utc = timestamptz '2026-02-24T00:00:00Z'),
    CHECK (window_end_exclusive_utc = timestamptz '2026-03-11T00:00:00Z')
);

CREATE TABLE domeye_data.evidence_object (
    dataset_id text NOT NULL REFERENCES domeye_data.dataset_registry(dataset_id),
    candidate_id text NOT NULL REFERENCES domeye_data.candidate_registry(candidate_id),
    object_role text NOT NULL,
    object_uri text NOT NULL,
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
    row_count bigint CHECK (row_count IS NULL OR row_count >= 0),
    content_sha256 text CHECK (content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'),
    integrity_status text NOT NULL CHECK (integrity_status = 'verified'),
    PRIMARY KEY (dataset_id, object_uri)
);

CREATE TABLE domeye_data.metric_subject (
    candidate_id text NOT NULL REFERENCES domeye_data.candidate_registry(candidate_id),
    metric_dataset_id text NOT NULL REFERENCES domeye_data.dataset_registry(dataset_id),
    subject_type text NOT NULL CHECK (subject_type IN ('collector', 'country', 'asn')),
    subject_id text NOT NULL,
    country_code text,
    sample_encoding text NOT NULL CHECK (sample_encoding IN ('dense_slot', 'change_point')),
    first_state_point_utc timestamptz NOT NULL,
    valid_through_utc timestamptz NOT NULL,
    baseline_route_state_count_v4 bigint NOT NULL CHECK (baseline_route_state_count_v4 >= 0),
    baseline_route_state_count_v6 bigint NOT NULL CHECK (baseline_route_state_count_v6 >= 0),
    absence_semantics text NOT NULL,
    PRIMARY KEY (metric_dataset_id, subject_type, subject_id),
    CHECK ((subject_type = 'asn' AND sample_encoding = 'change_point') OR
           (subject_type <> 'asn' AND sample_encoding = 'dense_slot')),
    CHECK ((subject_type = 'collector' AND subject_id = 'rrc25' AND country_code IS NULL) OR
           (subject_type = 'country' AND subject_id = country_code) OR
           (subject_type = 'asn' AND subject_id ~ '^AS[0-9]+$' AND country_code IS NOT NULL)),
    CHECK (first_state_point_utc >= timestamptz '2026-02-24T00:05:00Z'),
    CHECK (valid_through_utc = timestamptz '2026-03-11T00:00:00Z')
);

CREATE TABLE domeye_data.metric_slot_5m (
    candidate_id text NOT NULL REFERENCES domeye_data.candidate_registry(candidate_id),
    metric_dataset_id text NOT NULL REFERENCES domeye_data.dataset_registry(dataset_id),
    projection_id text NOT NULL,
    slot integer NOT NULL CHECK (slot BETWEEN 1 AND 4320),
    artifact_time_utc timestamptz NOT NULL,
    state_point_utc timestamptz NOT NULL,
    attempted_through timestamptz NOT NULL,
    data_through timestamptz NOT NULL,
    quality_status text NOT NULL CHECK (quality_status = 'complete'),
    gap_status text NOT NULL CHECK (gap_status = 'none'),
    source_route_state_dataset_id text NOT NULL REFERENCES domeye_data.dataset_registry(dataset_id),
    source_route_state_slot_sha256 text NOT NULL CHECK (source_route_state_slot_sha256 ~ '^[0-9a-f]{64}$'),
    source_route_event_file_sha256 text NOT NULL CHECK (source_route_event_file_sha256 ~ '^[0-9a-f]{64}$'),
    transition_sha256 text NOT NULL CHECK (transition_sha256 ~ '^[0-9a-f]{64}$'),
    route_event_count bigint NOT NULL CHECK (route_event_count >= 0),
    announce_count bigint NOT NULL CHECK (announce_count >= 0),
    withdraw_count bigint NOT NULL CHECK (withdraw_count >= 0),
    route_state_record_count bigint NOT NULL CHECK (route_state_record_count >= 0),
    visible_route_count bigint NOT NULL CHECK (visible_route_count BETWEEN 0 AND route_state_record_count),
    route_state_digest text NOT NULL CHECK (route_state_digest ~ '^[0-9a-f]{64}$'),
    country_metric_row_count integer NOT NULL CHECK (country_metric_row_count = 241),
    asn_metric_row_count integer NOT NULL CHECK (asn_metric_row_count >= 0),
    collector_metric_row_count integer NOT NULL CHECK (collector_metric_row_count = 1),
    metric_snapshot_sha256 text NOT NULL CHECK (metric_snapshot_sha256 ~ '^[0-9a-f]{64}$'),
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (metric_dataset_id, state_point_utc),
    UNIQUE (metric_dataset_id, slot),
    CHECK (state_point_utc = artifact_time_utc + interval '5 minutes'),
    CHECK (attempted_through = state_point_utc AND data_through = state_point_utc),
    CHECK (route_event_count = announce_count + withdraw_count),
    CHECK (artifact_time_utc = timestamptz '2026-02-24T00:00:00Z' +
           ((slot - 1) * interval '5 minutes'))
);

CREATE TABLE domeye_data.route_metric_5m (
    candidate_id text NOT NULL REFERENCES domeye_data.candidate_registry(candidate_id),
    metric_dataset_id text NOT NULL REFERENCES domeye_data.dataset_registry(dataset_id),
    projection_id text NOT NULL,
    state_point_utc timestamptz NOT NULL,
    subject_type text NOT NULL CHECK (subject_type IN ('collector', 'country', 'asn')),
    subject_id text NOT NULL,
    country_code text,
    sample_encoding text NOT NULL CHECK (sample_encoding IN ('dense_slot', 'change_point')),
    baseline_route_state_count_v4 bigint NOT NULL CHECK (baseline_route_state_count_v4 >= 0),
    baseline_route_state_count_v6 bigint NOT NULL CHECK (baseline_route_state_count_v6 >= 0),
    cohort_visible_route_state_count_v4 bigint NOT NULL CHECK (cohort_visible_route_state_count_v4 >= 0),
    cohort_visible_route_state_count_v6 bigint NOT NULL CHECK (cohort_visible_route_state_count_v6 >= 0),
    current_visible_route_state_count_v4 bigint NOT NULL CHECK (current_visible_route_state_count_v4 >= 0),
    current_visible_route_state_count_v6 bigint NOT NULL CHECK (current_visible_route_state_count_v6 >= 0),
    announcement_count_v4 bigint NOT NULL CHECK (announcement_count_v4 >= 0),
    announcement_count_v6 bigint NOT NULL CHECK (announcement_count_v6 >= 0),
    withdrawal_count_v4 bigint NOT NULL CHECK (withdrawal_count_v4 >= 0),
    withdrawal_count_v6 bigint NOT NULL CHECK (withdrawal_count_v6 >= 0),
    cohort_visibility_state_v4 text NOT NULL CHECK (cohort_visibility_state_v4 IN ('observed', 'not_applicable')),
    cohort_visibility_state_v6 text NOT NULL CHECK (cohort_visibility_state_v6 IN ('observed', 'not_applicable')),
    PRIMARY KEY (metric_dataset_id, state_point_utc, subject_type, subject_id),
    FOREIGN KEY (metric_dataset_id, subject_type, subject_id)
        REFERENCES domeye_data.metric_subject(metric_dataset_id, subject_type, subject_id),
    CHECK (state_point_utc >= timestamptz '2026-02-24T00:05:00Z' AND
           state_point_utc <= timestamptz '2026-03-11T00:00:00Z'),
    CHECK (cohort_visible_route_state_count_v4 <= baseline_route_state_count_v4),
    CHECK (cohort_visible_route_state_count_v6 <= baseline_route_state_count_v6),
    CHECK ((baseline_route_state_count_v4 = 0 AND cohort_visibility_state_v4 = 'not_applicable') OR
           (baseline_route_state_count_v4 > 0 AND cohort_visibility_state_v4 = 'observed')),
    CHECK ((baseline_route_state_count_v6 = 0 AND cohort_visibility_state_v6 = 'not_applicable') OR
           (baseline_route_state_count_v6 > 0 AND cohort_visibility_state_v6 = 'observed')),
    CHECK ((subject_type = 'asn' AND sample_encoding = 'change_point') OR
           (subject_type <> 'asn' AND sample_encoding = 'dense_slot'))
);

SELECT create_hypertable(
    'domeye_data.route_metric_5m',
    'state_point_utc',
    chunk_time_interval => interval '1 day',
    if_not_exists => TRUE
);

CREATE INDEX route_metric_subject_time_idx
    ON domeye_data.route_metric_5m
    (metric_dataset_id, subject_type, subject_id, state_point_utc DESC);
CREATE INDEX route_metric_country_time_idx
    ON domeye_data.route_metric_5m
    (metric_dataset_id, country_code, state_point_utc DESC);

CREATE TABLE domeye_data.quality_gap (
    metric_dataset_id text NOT NULL REFERENCES domeye_data.dataset_registry(dataset_id),
    gap_start_utc timestamptz NOT NULL,
    gap_end_exclusive_utc timestamptz NOT NULL,
    reason text NOT NULL,
    disposition text NOT NULL CHECK (disposition IN ('open', 'isolated', 'resolved_by_new_dataset')),
    PRIMARY KEY (metric_dataset_id, gap_start_utc, gap_end_exclusive_utc),
    CHECK (gap_start_utc < gap_end_exclusive_utc)
);

CREATE TABLE domeye_data.load_receipt (
    receipt_id text PRIMARY KEY,
    candidate_id text NOT NULL REFERENCES domeye_data.candidate_registry(candidate_id),
    metric_dataset_id text NOT NULL REFERENCES domeye_data.dataset_registry(dataset_id),
    manifest_sha256 text NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    schema_sha256 text NOT NULL CHECK (schema_sha256 ~ '^[0-9a-f]{64}$'),
    database_fingerprint_sha256 text NOT NULL CHECK (database_fingerprint_sha256 ~ '^[0-9a-f]{64}$'),
    loaded_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status = 'complete')
);

CREATE OR REPLACE FUNCTION domeye_data.query_route_metric_5m(
    p_metric_dataset_id text,
    p_subject_type text,
    p_subject_id text,
    p_start_utc timestamptz,
    p_end_exclusive_utc timestamptz
)
RETURNS TABLE (
    state_point_utc timestamptz,
    value_state text,
    baseline_route_state_count_v4 bigint,
    baseline_route_state_count_v6 bigint,
    cohort_visible_route_state_count_v4 bigint,
    cohort_visible_route_state_count_v6 bigint,
    current_visible_route_state_count_v4 bigint,
    current_visible_route_state_count_v6 bigint,
    announcement_count_v4 bigint,
    announcement_count_v6 bigint,
    withdrawal_count_v4 bigint,
    withdrawal_count_v6 bigint,
    cohort_visibility_ratio_v4 numeric,
    cohort_visibility_ratio_v6 numeric
)
LANGUAGE sql
STABLE
AS $$
    SELECT
        slot.state_point_utc,
        CASE
            WHEN subject.metric_dataset_id IS NULL THEN 'invalid_identity'
            WHEN state_row.state_point_utc IS NULL THEN 'not_applicable'
            ELSE 'observed'
        END AS value_state,
        state_row.baseline_route_state_count_v4,
        state_row.baseline_route_state_count_v6,
        state_row.cohort_visible_route_state_count_v4,
        state_row.cohort_visible_route_state_count_v6,
        state_row.current_visible_route_state_count_v4,
        state_row.current_visible_route_state_count_v6,
        CASE WHEN state_row.state_point_utc IS NULL THEN NULL
             WHEN p_subject_type = 'asn' THEN COALESCE(exact_row.announcement_count_v4, 0)
             ELSE exact_row.announcement_count_v4 END,
        CASE WHEN state_row.state_point_utc IS NULL THEN NULL
             WHEN p_subject_type = 'asn' THEN COALESCE(exact_row.announcement_count_v6, 0)
             ELSE exact_row.announcement_count_v6 END,
        CASE WHEN state_row.state_point_utc IS NULL THEN NULL
             WHEN p_subject_type = 'asn' THEN COALESCE(exact_row.withdrawal_count_v4, 0)
             ELSE exact_row.withdrawal_count_v4 END,
        CASE WHEN state_row.state_point_utc IS NULL THEN NULL
             WHEN p_subject_type = 'asn' THEN COALESCE(exact_row.withdrawal_count_v6, 0)
             ELSE exact_row.withdrawal_count_v6 END,
        CASE WHEN state_row.baseline_route_state_count_v4 > 0
             THEN state_row.cohort_visible_route_state_count_v4::numeric /
                  state_row.baseline_route_state_count_v4::numeric END,
        CASE WHEN state_row.baseline_route_state_count_v6 > 0
             THEN state_row.cohort_visible_route_state_count_v6::numeric /
                  state_row.baseline_route_state_count_v6::numeric END
    FROM domeye_data.metric_slot_5m AS slot
    LEFT JOIN domeye_data.metric_subject AS subject
      ON subject.metric_dataset_id = p_metric_dataset_id
     AND subject.subject_type = p_subject_type
     AND subject.subject_id = p_subject_id
    LEFT JOIN LATERAL (
        SELECT metric.*
        FROM domeye_data.route_metric_5m AS metric
        WHERE metric.metric_dataset_id = p_metric_dataset_id
          AND metric.subject_type = p_subject_type
          AND metric.subject_id = p_subject_id
          AND ((p_subject_type = 'asn' AND metric.state_point_utc <= slot.state_point_utc) OR
               (p_subject_type <> 'asn' AND metric.state_point_utc = slot.state_point_utc))
        ORDER BY metric.state_point_utc DESC
        LIMIT 1
    ) AS state_row ON TRUE
    LEFT JOIN domeye_data.route_metric_5m AS exact_row
      ON exact_row.metric_dataset_id = p_metric_dataset_id
     AND exact_row.subject_type = p_subject_type
     AND exact_row.subject_id = p_subject_id
     AND exact_row.state_point_utc = slot.state_point_utc
    WHERE slot.metric_dataset_id = p_metric_dataset_id
      AND slot.state_point_utc >= p_start_utc
      AND slot.state_point_utc < p_end_exclusive_utc
    ORDER BY slot.state_point_utc;
$$;
