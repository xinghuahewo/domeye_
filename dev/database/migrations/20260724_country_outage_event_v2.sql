-- 国家中断 Incident/Episode/Observation v2。
-- 本迁移不从旧 event_info 反推时间或数值；旧未知字段保持 NULL/unknown。

BEGIN;

CREATE TABLE IF NOT EXISTS country_outage_incident_v2 (
    incident_id text PRIMARY KEY,
    source text NOT NULL,
    country_code char(2) NOT NULL,
    collector_id text NOT NULL,
    detected_at timestamptz NOT NULL,
    onset_at timestamptz,
    peak_at timestamptz,
    trough_at timestamptz,
    partial_recovery_at timestamptz,
    full_recovery_at timestamptz,
    observation_end_at timestamptz NOT NULL,
    duration_state text NOT NULL,
    recovery_state text NOT NULL,
    cohort_id text NOT NULL,
    peak_snapshot_id text,
    trough_snapshot_id text,
    algorithm_version text NOT NULL,
    legacy_ref text,
    incident_payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    derived_at timestamptz NOT NULL DEFAULT now(),
    CHECK (duration_state IN ('exact','lower_bound','interval','unknown')),
    CHECK (recovery_state IN (
        'ongoing','recovering','partially_recovered','fully_recovered','unknown'
    ))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_country_outage_incident_v2_legacy_ref
ON country_outage_incident_v2 (legacy_ref)
WHERE legacy_ref IS NOT NULL;

CREATE TABLE IF NOT EXISTS country_outage_episode_v2 (
    episode_id text PRIMARY KEY,
    incident_id text NOT NULL
        REFERENCES country_outage_incident_v2(incident_id),
    ordinal integer NOT NULL,
    onset_at timestamptz,
    peak_at timestamptz,
    trough_at timestamptz,
    partial_recovery_at timestamptz,
    full_recovery_at timestamptz,
    observation_end_at timestamptz NOT NULL,
    duration_state text NOT NULL,
    recovery_state text NOT NULL,
    cohort_id text NOT NULL,
    peak_snapshot_id text,
    trough_snapshot_id text,
    algorithm_version text NOT NULL,
    episode_payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    derived_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (incident_id, ordinal)
);

CREATE TABLE IF NOT EXISTS country_outage_observation_v2 (
    snapshot_id text PRIMARY KEY,
    incident_id text REFERENCES country_outage_incident_v2(incident_id),
    source text NOT NULL,
    country_code char(2) NOT NULL,
    collector_id text NOT NULL,
    observed_at timestamptz NOT NULL,
    continuity_state text NOT NULL,
    cohort_id text NOT NULL,
    baseline_asn_count integer NOT NULL,
    affected_asn_count integer,
    affected_asn_ratio double precision,
    visible_asn_count integer,
    visible_asn_ratio double precision,
    baseline_prefix_vp_count bigint,
    visible_prefix_vp_count bigint,
    visible_prefix_vp_ratio double precision,
    affected_asns jsonb,
    state_result_ref jsonb,
    observation_payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, country_code, collector_id, observed_at, cohort_id)
);

DO $migration$
DECLARE
    table_name text;
BEGIN
    FOR table_name IN
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = current_schema()
          AND tablename ~ '^country_outage_[0-9]{6}$'
    LOOP
        EXECUTE format(
            'ALTER TABLE %I ADD COLUMN IF NOT EXISTS incident_id_v2 text',
            table_name
        );
        EXECUTE format(
            'ALTER TABLE %I ADD COLUMN IF NOT EXISTS peak_snapshot_id text',
            table_name
        );
        EXECUTE format(
            'ALTER TABLE %I ADD COLUMN IF NOT EXISTS legacy_semantics jsonb',
            table_name
        );
    END LOOP;
END
$migration$;

COMMIT;
