\set ON_ERROR_STOP on

CREATE SCHEMA domeye_event;

CREATE TYPE domeye_event.lifecycle_stage AS ENUM (
    'detected',
    'ongoing',
    'recovery_candidate',
    'recovered_observation',
    'final'
);

CREATE TYPE domeye_event.publication_kind AS ENUM (
    'legacy_observation',
    'observation',
    'analysis'
);

CREATE TABLE domeye_event.candidate_registry (
    candidate_id text PRIMARY KEY,
    dataset_id text NOT NULL UNIQUE,
    collector_id text NOT NULL CHECK (collector_id = 'rrc25'),
    window_start_utc timestamptz NOT NULL,
    window_end_exclusive_utc timestamptz NOT NULL,
    state_point_count integer NOT NULL CHECK (state_point_count = 4320),
    country_bucket_count integer NOT NULL CHECK (country_bucket_count = 241),
    implementation_id text NOT NULL CHECK (implementation_id ~ '^[0-9a-f]{40}$'),
    manifest_sha256 text NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    status text NOT NULL CHECK (status IN ('loading', 'complete', 'failed')),
    CHECK (window_start_utc = timestamptz '2026-02-24T00:00:00Z'),
    CHECK (window_end_exclusive_utc = timestamptz '2026-03-11T00:00:00Z')
);

CREATE TABLE domeye_event.source_binding (
    candidate_id text NOT NULL REFERENCES domeye_event.candidate_registry(candidate_id),
    source_id text PRIMARY KEY,
    source_kind text NOT NULL CHECK (
        source_kind IN ('route_metric', 'legacy_publication_registry')
    ),
    collector_id text NOT NULL CHECK (collector_id = 'rrc25'),
    window_start_utc timestamptz NOT NULL,
    window_end_exclusive_utc timestamptz NOT NULL,
    dataset_id text,
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    manifest_sha256 text CHECK (
        manifest_sha256 IS NULL OR manifest_sha256 ~ '^[0-9a-f]{64}$'
    ),
    database_name text,
    database_fingerprint_sha256 text CHECK (
        database_fingerprint_sha256 IS NULL OR
        database_fingerprint_sha256 ~ '^[0-9a-f]{64}$'
    ),
    object_uri text NOT NULL,
    object_sha256 text NOT NULL CHECK (object_sha256 ~ '^[0-9a-f]{64}$'),
    metadata jsonb NOT NULL,
    CHECK (window_start_utc = timestamptz '2026-02-24T00:00:00Z'),
    CHECK (window_end_exclusive_utc = timestamptz '2026-03-11T00:00:00Z'),
    CHECK (
        (source_kind = 'route_metric' AND dataset_id IS NOT NULL AND
         manifest_sha256 IS NOT NULL AND database_name IS NOT NULL AND
         database_fingerprint_sha256 IS NOT NULL) OR
        (source_kind = 'legacy_publication_registry' AND dataset_id IS NULL AND
         manifest_sha256 IS NULL AND database_name IS NULL AND
         database_fingerprint_sha256 IS NULL)
    )
);

CREATE TABLE domeye_event.incident (
    candidate_id text NOT NULL REFERENCES domeye_event.candidate_registry(candidate_id),
    incident_id text PRIMARY KEY,
    legacy_reference text NOT NULL UNIQUE,
    country_code text NOT NULL CHECK (country_code ~ '^[A-Z]{2}$'),
    country_name text NOT NULL,
    event_type text NOT NULL CHECK (event_type = 'country_outage'),
    source_system text NOT NULL,
    source_code text NOT NULL CHECK (source_code = 'r'),
    collector_id text NOT NULL CHECK (collector_id = 'rrc25'),
    legacy_event_time_utc timestamptz NOT NULL,
    detected_at timestamptz NOT NULL,
    window_start_utc timestamptz NOT NULL,
    window_end_exclusive_utc timestamptz NOT NULL,
    normal_band_state text NOT NULL CHECK (normal_band_state = 'unknown'),
    normal_band_reason text NOT NULL,
    legacy_current_publication_id text NOT NULL,
    corrected_observation_revision integer NOT NULL CHECK (
        corrected_observation_revision >= 2
    ),
    status text NOT NULL CHECK (status = 'complete'),
    CHECK (detected_at >= timestamptz '2026-02-24T00:05:00Z'),
    CHECK (detected_at < window_end_exclusive_utc),
    CHECK (window_start_utc = timestamptz '2026-02-24T00:00:00Z'),
    CHECK (window_end_exclusive_utc = timestamptz '2026-03-11T00:00:00Z')
);

CREATE OR REPLACE FUNCTION domeye_event.lifecycle_transition_allowed(
    previous_stage text,
    next_stage text
) RETURNS boolean
LANGUAGE sql IMMUTABLE STRICT
AS $$
    SELECT CASE previous_stage
        WHEN 'detected' THEN next_stage IN ('ongoing', 'final')
        WHEN 'ongoing' THEN next_stage IN ('recovery_candidate', 'final')
        WHEN 'recovery_candidate' THEN next_stage IN (
            'ongoing', 'recovered_observation', 'final'
        )
        WHEN 'recovered_observation' THEN next_stage IN ('ongoing', 'final')
        WHEN 'final' THEN false
        ELSE false
    END;
$$;

CREATE TABLE domeye_event.event_fact (
    candidate_id text NOT NULL REFERENCES domeye_event.candidate_registry(candidate_id),
    fact_id text PRIMARY KEY,
    incident_id text NOT NULL REFERENCES domeye_event.incident(incident_id),
    fact_sequence integer NOT NULL CHECK (fact_sequence >= 1),
    stage domeye_event.lifecycle_stage NOT NULL,
    observed_at timestamptz NOT NULL,
    data_through timestamptz NOT NULL,
    detector_name text NOT NULL,
    detector_version text NOT NULL,
    source_metric_dataset_id text NOT NULL REFERENCES domeye_event.source_binding(source_id),
    source_state_point_utc timestamptz NOT NULL,
    source_metric_slot_sha256 text NOT NULL CHECK (
        source_metric_slot_sha256 ~ '^[0-9a-f]{64}$'
    ),
    evidence jsonb NOT NULL,
    limitations jsonb NOT NULL,
    previous_fact_id text REFERENCES domeye_event.event_fact(fact_id),
    UNIQUE (incident_id, fact_sequence),
    UNIQUE (incident_id, observed_at),
    CHECK (observed_at = data_through),
    CHECK (source_state_point_utc = data_through),
    CHECK (data_through >= timestamptz '2026-02-24T00:05:00Z'),
    CHECK (data_through <= timestamptz '2026-03-11T00:00:00Z'),
    CHECK ((fact_sequence = 1 AND stage = 'detected' AND previous_fact_id IS NULL) OR
           (fact_sequence > 1 AND previous_fact_id IS NOT NULL)),
    CHECK (limitations ->> 'control_plane_only' = 'true'),
    CHECK (limitations ->> 'normal_band_state' = 'unknown')
);

CREATE OR REPLACE FUNCTION domeye_event.validate_event_fact()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    previous_row domeye_event.event_fact%ROWTYPE;
BEGIN
    IF NEW.fact_sequence = 1 THEN
        RETURN NEW;
    END IF;
    SELECT * INTO previous_row
      FROM domeye_event.event_fact
     WHERE fact_id = NEW.previous_fact_id;
    IF NOT FOUND OR previous_row.incident_id <> NEW.incident_id OR
       previous_row.fact_sequence + 1 <> NEW.fact_sequence OR
       previous_row.observed_at >= NEW.observed_at OR
       NOT domeye_event.lifecycle_transition_allowed(
           previous_row.stage::text, NEW.stage::text
       ) THEN
        RAISE EXCEPTION 'invalid_lifecycle_transition';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER event_fact_validate_before_insert
BEFORE INSERT ON domeye_event.event_fact
FOR EACH ROW EXECUTE FUNCTION domeye_event.validate_event_fact();

CREATE TABLE domeye_event.publication (
    candidate_id text NOT NULL REFERENCES domeye_event.candidate_registry(candidate_id),
    publication_id text PRIMARY KEY,
    incident_id text NOT NULL REFERENCES domeye_event.incident(incident_id),
    publication_kind domeye_event.publication_kind NOT NULL,
    revision integer NOT NULL CHECK (revision >= 1),
    sequence_in_revision integer NOT NULL CHECK (sequence_in_revision >= 1),
    data_through timestamptz NOT NULL,
    observed_at timestamptz NOT NULL,
    event_fact_id text REFERENCES domeye_event.event_fact(fact_id),
    derived_from_observation_publication_id text REFERENCES domeye_event.publication(publication_id),
    previous_publication_id text REFERENCES domeye_event.publication(publication_id),
    correction_of_publication_id text REFERENCES domeye_event.publication(publication_id),
    supersedes_publication_id text REFERENCES domeye_event.publication(publication_id),
    source_metric_dataset_id text REFERENCES domeye_event.source_binding(source_id),
    source_metric_slot_sha256 text CHECK (
        source_metric_slot_sha256 IS NULL OR
        source_metric_slot_sha256 ~ '^[0-9a-f]{64}$'
    ),
    is_final boolean NOT NULL,
    validation_state text NOT NULL CHECK (validation_state = 'verified'),
    fact_set_sha256 text NOT NULL CHECK (fact_set_sha256 ~ '^[0-9a-f]{64}$'),
    payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    artifact_uri text,
    artifact_sha256 text CHECK (
        artifact_sha256 IS NULL OR artifact_sha256 ~ '^[0-9a-f]{64}$'
    ),
    snapshot jsonb NOT NULL,
    UNIQUE (incident_id, publication_kind, revision, sequence_in_revision),
    CHECK (data_through >= timestamptz '2026-02-24T00:05:00Z'),
    CHECK (data_through <= timestamptz '2026-03-11T00:00:00Z'),
    CHECK (
        (publication_kind = 'legacy_observation' AND event_fact_id IS NULL AND
         derived_from_observation_publication_id IS NULL AND
         source_metric_dataset_id IS NULL AND source_metric_slot_sha256 IS NULL AND
         artifact_uri IS NOT NULL AND artifact_sha256 IS NOT NULL) OR
        (publication_kind = 'observation' AND event_fact_id IS NOT NULL AND
         derived_from_observation_publication_id IS NULL AND
         source_metric_dataset_id IS NOT NULL AND source_metric_slot_sha256 IS NOT NULL AND
         artifact_uri IS NULL AND artifact_sha256 IS NULL) OR
        (publication_kind = 'analysis' AND event_fact_id IS NULL AND
         derived_from_observation_publication_id IS NOT NULL AND
         source_metric_dataset_id IS NOT NULL AND source_metric_slot_sha256 IS NOT NULL AND
         artifact_uri IS NULL AND artifact_sha256 IS NULL)
    )
);

CREATE INDEX publication_incident_time_idx
    ON domeye_event.publication (incident_id, publication_kind, data_through DESC);
CREATE INDEX publication_correction_idx
    ON domeye_event.publication (correction_of_publication_id)
    WHERE correction_of_publication_id IS NOT NULL;
CREATE INDEX publication_supersedes_idx
    ON domeye_event.publication (supersedes_publication_id)
    WHERE supersedes_publication_id IS NOT NULL;

CREATE OR REPLACE FUNCTION domeye_event.validate_publication()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    linked domeye_event.publication%ROWTYPE;
    fact domeye_event.event_fact%ROWTYPE;
BEGIN
    IF NEW.publication_kind = 'observation' THEN
        SELECT * INTO fact FROM domeye_event.event_fact WHERE fact_id = NEW.event_fact_id;
        IF NOT FOUND OR fact.incident_id <> NEW.incident_id OR
           fact.data_through > NEW.data_through THEN
            RAISE EXCEPTION 'invalid_observation_fact';
        END IF;
    ELSIF NEW.publication_kind = 'analysis' THEN
        SELECT * INTO linked FROM domeye_event.publication
         WHERE publication_id = NEW.derived_from_observation_publication_id;
        IF NOT FOUND OR linked.publication_kind <> 'observation' OR
           linked.incident_id <> NEW.incident_id OR
           linked.data_through <> NEW.data_through OR
           linked.fact_set_sha256 <> NEW.fact_set_sha256 OR
           linked.source_metric_slot_sha256 <> NEW.source_metric_slot_sha256 THEN
            RAISE EXCEPTION 'invalid_analysis_derivation';
        END IF;
    END IF;

    IF NEW.previous_publication_id IS NOT NULL THEN
        SELECT * INTO linked FROM domeye_event.publication
         WHERE publication_id = NEW.previous_publication_id;
        IF NOT FOUND OR linked.incident_id <> NEW.incident_id OR
           linked.publication_kind <> NEW.publication_kind OR
           linked.revision <> NEW.revision OR
           linked.sequence_in_revision + 1 <> NEW.sequence_in_revision OR
           linked.data_through >= NEW.data_through THEN
            RAISE EXCEPTION 'invalid_publication_sequence';
        END IF;
    END IF;

    IF NEW.correction_of_publication_id IS NOT NULL THEN
        SELECT * INTO linked FROM domeye_event.publication
         WHERE publication_id = NEW.correction_of_publication_id;
        IF NOT FOUND OR linked.incident_id <> NEW.incident_id OR
           linked.publication_kind <> 'legacy_observation' OR
           NEW.publication_kind <> 'observation' OR linked.revision >= NEW.revision THEN
            RAISE EXCEPTION 'invalid_publication_correction';
        END IF;
    END IF;

    IF NEW.supersedes_publication_id IS NOT NULL THEN
        SELECT * INTO linked FROM domeye_event.publication
         WHERE publication_id = NEW.supersedes_publication_id;
        IF NOT FOUND OR linked.incident_id <> NEW.incident_id OR
           linked.revision >= NEW.revision OR NOT NEW.is_final THEN
            RAISE EXCEPTION 'invalid_publication_supersession';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER publication_validate_before_insert
BEFORE INSERT ON domeye_event.publication
FOR EACH ROW EXECUTE FUNCTION domeye_event.validate_publication();

CREATE TABLE domeye_event.pointer_plan (
    candidate_id text NOT NULL REFERENCES domeye_event.candidate_registry(candidate_id),
    incident_id text PRIMARY KEY REFERENCES domeye_event.incident(incident_id),
    initial_observation_publication_id text NOT NULL REFERENCES domeye_event.publication(publication_id),
    final_observation_publication_id text NOT NULL REFERENCES domeye_event.publication(publication_id),
    final_analysis_publication_id text NOT NULL REFERENCES domeye_event.publication(publication_id)
);

CREATE TABLE domeye_event.publication_pointer (
    candidate_id text NOT NULL REFERENCES domeye_event.candidate_registry(candidate_id),
    incident_id text PRIMARY KEY REFERENCES domeye_event.incident(incident_id),
    current_observation_publication_id text NOT NULL REFERENCES domeye_event.publication(publication_id),
    current_analysis_publication_id text REFERENCES domeye_event.publication(publication_id),
    pointer_version bigint NOT NULL CHECK (pointer_version >= 1),
    updated_at timestamptz NOT NULL,
    last_reason text NOT NULL
);

CREATE TABLE domeye_event.pointer_audit (
    audit_id bigserial PRIMARY KEY,
    candidate_id text NOT NULL REFERENCES domeye_event.candidate_registry(candidate_id),
    incident_id text NOT NULL REFERENCES domeye_event.incident(incident_id),
    old_observation_publication_id text,
    new_observation_publication_id text NOT NULL,
    old_analysis_publication_id text,
    new_analysis_publication_id text,
    old_pointer_version bigint,
    new_pointer_version bigint NOT NULL,
    changed_at timestamptz NOT NULL,
    reason text NOT NULL,
    transaction_id bigint NOT NULL
);

CREATE OR REPLACE FUNCTION domeye_event.validate_pointer_target()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    observation_row domeye_event.publication%ROWTYPE;
    analysis_row domeye_event.publication%ROWTYPE;
    old_observation_row domeye_event.publication%ROWTYPE;
    old_analysis_row domeye_event.publication%ROWTYPE;
BEGIN
    SELECT * INTO observation_row FROM domeye_event.publication
     WHERE publication_id = NEW.current_observation_publication_id;
    IF NOT FOUND OR observation_row.incident_id <> NEW.incident_id OR
       observation_row.publication_kind NOT IN ('legacy_observation', 'observation') OR
       observation_row.validation_state <> 'verified' THEN
        RAISE EXCEPTION 'invalid_observation_publication';
    END IF;
    IF NEW.current_analysis_publication_id IS NOT NULL THEN
        SELECT * INTO analysis_row FROM domeye_event.publication
         WHERE publication_id = NEW.current_analysis_publication_id;
        IF NOT FOUND OR analysis_row.incident_id <> NEW.incident_id OR
           analysis_row.publication_kind <> 'analysis' OR
           analysis_row.validation_state <> 'verified' OR
           analysis_row.data_through > observation_row.data_through THEN
            RAISE EXCEPTION 'invalid_analysis_publication';
        END IF;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        SELECT * INTO old_observation_row FROM domeye_event.publication
         WHERE publication_id = OLD.current_observation_publication_id;
        IF NEW.current_observation_publication_id <>
           OLD.current_observation_publication_id THEN
            IF observation_row.publication_kind <> 'observation' OR
               observation_row.data_through < old_observation_row.data_through OR
               observation_row.revision < old_observation_row.revision OR
               (observation_row.revision = old_observation_row.revision AND
                observation_row.sequence_in_revision <=
                old_observation_row.sequence_in_revision) OR
               (old_observation_row.publication_kind = 'legacy_observation' AND
                (observation_row.revision <= old_observation_row.revision OR
                 observation_row.supersedes_publication_id IS DISTINCT FROM
                 old_observation_row.publication_id)) OR
               (old_observation_row.publication_kind = 'observation' AND
                observation_row.revision > old_observation_row.revision AND
                (NOT observation_row.is_final OR
                 observation_row.supersedes_publication_id IS DISTINCT FROM
                 old_observation_row.publication_id)) THEN
                RAISE EXCEPTION 'invalid_pointer_regression';
            END IF;
        END IF;
        IF OLD.current_analysis_publication_id IS NOT NULL THEN
            IF NEW.current_analysis_publication_id IS NULL THEN
                RAISE EXCEPTION 'invalid_analysis_pointer_regression';
            END IF;
            IF NEW.current_analysis_publication_id <>
               OLD.current_analysis_publication_id THEN
                SELECT * INTO old_analysis_row FROM domeye_event.publication
                 WHERE publication_id = OLD.current_analysis_publication_id;
                IF analysis_row.data_through < old_analysis_row.data_through OR
                   analysis_row.revision < old_analysis_row.revision OR
                   (analysis_row.revision = old_analysis_row.revision AND
                    analysis_row.sequence_in_revision <=
                    old_analysis_row.sequence_in_revision) THEN
                    RAISE EXCEPTION 'invalid_analysis_pointer_regression';
                END IF;
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER pointer_validate_before_write
BEFORE INSERT OR UPDATE ON domeye_event.publication_pointer
FOR EACH ROW EXECUTE FUNCTION domeye_event.validate_pointer_target();

CREATE OR REPLACE FUNCTION domeye_event.audit_pointer_change()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO domeye_event.pointer_audit(
        candidate_id,incident_id,old_observation_publication_id,
        new_observation_publication_id,old_analysis_publication_id,
        new_analysis_publication_id,old_pointer_version,new_pointer_version,
        changed_at,reason,transaction_id
    ) VALUES (
        NEW.candidate_id,NEW.incident_id,
        CASE WHEN TG_OP='INSERT' THEN NULL ELSE OLD.current_observation_publication_id END,
        NEW.current_observation_publication_id,
        CASE WHEN TG_OP='INSERT' THEN NULL ELSE OLD.current_analysis_publication_id END,
        NEW.current_analysis_publication_id,
        CASE WHEN TG_OP='INSERT' THEN NULL ELSE OLD.pointer_version END,
        NEW.pointer_version,NEW.updated_at,NEW.last_reason,txid_current()
    );
    RETURN NEW;
END;
$$;

CREATE TRIGGER pointer_audit_after_write
AFTER INSERT OR UPDATE ON domeye_event.publication_pointer
FOR EACH ROW EXECUTE FUNCTION domeye_event.audit_pointer_change();

CREATE OR REPLACE FUNCTION domeye_event.advance_publication_pointer(
    p_incident_id text,
    p_observation_publication_id text,
    p_analysis_publication_id text,
    p_expected_pointer_version bigint,
    p_reason text
) RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    current_row domeye_event.publication_pointer%ROWTYPE;
BEGIN
    SELECT * INTO current_row FROM domeye_event.publication_pointer
     WHERE incident_id = p_incident_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'pointer_not_found';
    END IF;
    IF current_row.pointer_version <> p_expected_pointer_version THEN
        RAISE EXCEPTION 'pointer_version_conflict';
    END IF;
    IF p_reason IS NULL OR btrim(p_reason) = '' THEN
        RAISE EXCEPTION 'pointer_reason_required';
    END IF;
    UPDATE domeye_event.publication_pointer
       SET current_observation_publication_id = p_observation_publication_id,
           current_analysis_publication_id = p_analysis_publication_id,
           pointer_version = pointer_version + 1,
           updated_at = clock_timestamp(),
           last_reason = p_reason
     WHERE incident_id = p_incident_id;
    RETURN current_row.pointer_version + 1;
END;
$$;

CREATE VIEW domeye_event.current_publication_state AS
SELECT pointer.candidate_id,
       pointer.incident_id,
       pointer.current_observation_publication_id AS observation_publication_id,
       observation.revision AS observation_revision,
       observation.data_through AS observation_data_through,
       pointer.current_analysis_publication_id AS analysis_publication_id,
       analysis.revision AS analysis_revision,
       analysis.data_through AS analysis_data_through,
       CASE
         WHEN analysis.publication_id IS NULL THEN NULL
         ELSE extract(epoch FROM (
             observation.data_through - analysis.data_through
         ))::bigint
       END AS analysis_lag_seconds,
       pointer.pointer_version,
       pointer.updated_at
  FROM domeye_event.publication_pointer pointer
  JOIN domeye_event.publication observation
    ON observation.publication_id = pointer.current_observation_publication_id
  LEFT JOIN domeye_event.publication analysis
    ON analysis.publication_id = pointer.current_analysis_publication_id;

CREATE TABLE domeye_event.load_receipt (
    receipt_id text PRIMARY KEY,
    candidate_id text NOT NULL REFERENCES domeye_event.candidate_registry(candidate_id),
    dataset_id text NOT NULL,
    manifest_sha256 text NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    schema_sha256 text NOT NULL CHECK (schema_sha256 ~ '^[0-9a-f]{64}$'),
    database_fingerprint_sha256 text NOT NULL CHECK (
        database_fingerprint_sha256 ~ '^[0-9a-f]{64}$'
    ),
    loaded_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status = 'complete')
);

CREATE OR REPLACE FUNCTION domeye_event.reject_immutable_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'immutable_relation:%', TG_TABLE_NAME;
END;
$$;

CREATE TRIGGER source_binding_immutable
BEFORE UPDATE OR DELETE ON domeye_event.source_binding
FOR EACH ROW EXECUTE FUNCTION domeye_event.reject_immutable_mutation();
CREATE TRIGGER incident_immutable
BEFORE UPDATE OR DELETE ON domeye_event.incident
FOR EACH ROW EXECUTE FUNCTION domeye_event.reject_immutable_mutation();
CREATE TRIGGER event_fact_immutable
BEFORE UPDATE OR DELETE ON domeye_event.event_fact
FOR EACH ROW EXECUTE FUNCTION domeye_event.reject_immutable_mutation();
CREATE TRIGGER publication_immutable
BEFORE UPDATE OR DELETE ON domeye_event.publication
FOR EACH ROW EXECUTE FUNCTION domeye_event.reject_immutable_mutation();
CREATE TRIGGER pointer_audit_immutable
BEFORE UPDATE OR DELETE ON domeye_event.pointer_audit
FOR EACH ROW EXECUTE FUNCTION domeye_event.reject_immutable_mutation();
CREATE TRIGGER load_receipt_immutable
BEFORE UPDATE OR DELETE ON domeye_event.load_receipt
FOR EACH ROW EXECUTE FUNCTION domeye_event.reject_immutable_mutation();

REVOKE UPDATE, DELETE ON domeye_event.source_binding FROM PUBLIC;
REVOKE UPDATE, DELETE ON domeye_event.incident FROM PUBLIC;
REVOKE UPDATE, DELETE ON domeye_event.event_fact FROM PUBLIC;
REVOKE UPDATE, DELETE ON domeye_event.publication FROM PUBLIC;
REVOKE UPDATE, DELETE ON domeye_event.pointer_audit FROM PUBLIC;
REVOKE UPDATE, DELETE ON domeye_event.load_receipt FROM PUBLIC;
REVOKE UPDATE ON domeye_event.publication_pointer FROM PUBLIC;
