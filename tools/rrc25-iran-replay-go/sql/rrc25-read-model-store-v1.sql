BEGIN;

CREATE SCHEMA domeye_read;

CREATE TABLE domeye_read.candidate_registry (
    candidate_id text PRIMARY KEY,
    dataset_id text NOT NULL UNIQUE,
    collector_id text NOT NULL CHECK (collector_id = 'rrc25'),
    window_start_utc timestamptz NOT NULL,
    window_end_exclusive_utc timestamptz NOT NULL,
    state_point_count integer NOT NULL CHECK (state_point_count = 4320),
    event_count integer NOT NULL CHECK (event_count > 0),
    implementation_id text NOT NULL,
    manifest_sha256 text NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    status text NOT NULL CHECK (status IN ('loading', 'complete', 'failed')),
    CHECK (window_start_utc = TIMESTAMPTZ '2026-02-24 00:00:00+00'),
    CHECK (window_end_exclusive_utc = TIMESTAMPTZ '2026-03-11 00:00:00+00')
);

CREATE TABLE domeye_read.source_binding (
    candidate_id text NOT NULL REFERENCES domeye_read.candidate_registry(candidate_id),
    source_id text NOT NULL,
    source_kind text NOT NULL,
    dataset_id text NOT NULL,
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    manifest_sha256 text CHECK (manifest_sha256 IS NULL OR manifest_sha256 ~ '^[0-9a-f]{64}$'),
    database_name text,
    database_fingerprint_sha256 text,
    object_uri text,
    object_sha256 text,
    metadata jsonb NOT NULL,
    PRIMARY KEY (candidate_id, source_id)
);

CREATE TABLE domeye_read.series_object (
    candidate_id text NOT NULL REFERENCES domeye_read.candidate_registry(candidate_id),
    series_id text NOT NULL,
    country_code text NOT NULL CHECK (country_code ~ '^[A-Z]{2}$'),
    point_count integer NOT NULL CHECK (point_count = 4320),
    artifact_uri text NOT NULL,
    artifact_sha256 text NOT NULL CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    compressed_size_bytes integer NOT NULL CHECK (compressed_size_bytes > 0 AND compressed_size_bytes <= 1048576),
    payload jsonb NOT NULL,
    PRIMARY KEY (candidate_id, series_id),
    UNIQUE (candidate_id, country_code)
);

CREATE TABLE domeye_read.prefix_vp_evidence_view (
    candidate_id text NOT NULL REFERENCES domeye_read.candidate_registry(candidate_id),
    evidence_view_id text NOT NULL,
    incident_id text NOT NULL,
    country_code text NOT NULL CHECK (country_code ~ '^[A-Z]{2}$'),
    publication_id text NOT NULL,
    derived_from_route_state_id text NOT NULL,
    projector_version text NOT NULL,
    row_count bigint NOT NULL CHECK (row_count > 0),
    page_count integer NOT NULL CHECK (page_count > 0),
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    artifact_uri text NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (candidate_id, evidence_view_id),
    UNIQUE (candidate_id, incident_id)
);

CREATE TABLE domeye_read.event_read_model (
    candidate_id text NOT NULL REFERENCES domeye_read.candidate_registry(candidate_id),
    snapshot_id text NOT NULL,
    incident_id text NOT NULL,
    legacy_reference text NOT NULL,
    country_code text NOT NULL CHECK (country_code ~ '^[A-Z]{2}$'),
    observation_publication_id text NOT NULL,
    analysis_publication_id text NOT NULL,
    observation_revision integer NOT NULL CHECK (observation_revision > 0),
    analysis_revision integer NOT NULL CHECK (analysis_revision > 0),
    fact_set_sha256 text NOT NULL CHECK (fact_set_sha256 ~ '^[0-9a-f]{64}$'),
    series_id text NOT NULL,
    evidence_view_id text NOT NULL,
    snapshot_sha256 text NOT NULL CHECK (snapshot_sha256 ~ '^[0-9a-f]{64}$'),
    payload jsonb NOT NULL,
    PRIMARY KEY (candidate_id, snapshot_id),
    UNIQUE (candidate_id, incident_id),
    UNIQUE (candidate_id, legacy_reference),
    FOREIGN KEY (candidate_id, series_id) REFERENCES domeye_read.series_object(candidate_id, series_id),
    FOREIGN KEY (candidate_id, evidence_view_id) REFERENCES domeye_read.prefix_vp_evidence_view(candidate_id, evidence_view_id)
);

CREATE TABLE domeye_read.report_snapshot (
    candidate_id text NOT NULL REFERENCES domeye_read.candidate_registry(candidate_id),
    report_snapshot_id text NOT NULL,
    incident_id text NOT NULL,
    report_version integer NOT NULL CHECK (report_version > 0),
    event_snapshot_id text NOT NULL,
    observation_publication_id text NOT NULL,
    analysis_publication_id text NOT NULL,
    snapshot_sha256 text NOT NULL CHECK (snapshot_sha256 ~ '^[0-9a-f]{64}$'),
    payload jsonb NOT NULL,
    PRIMARY KEY (candidate_id, report_snapshot_id),
    UNIQUE (candidate_id, incident_id, report_version),
    FOREIGN KEY (candidate_id, event_snapshot_id) REFERENCES domeye_read.event_read_model(candidate_id, snapshot_id)
);

CREATE TABLE domeye_read.report_pointer (
    candidate_id text NOT NULL REFERENCES domeye_read.candidate_registry(candidate_id),
    incident_id text NOT NULL,
    current_report_snapshot_id text NOT NULL,
    pointer_version bigint NOT NULL CHECK (pointer_version > 0),
    reason text NOT NULL,
    PRIMARY KEY (candidate_id, incident_id),
    FOREIGN KEY (candidate_id, current_report_snapshot_id) REFERENCES domeye_read.report_snapshot(candidate_id, report_snapshot_id)
);

CREATE TABLE domeye_read.load_receipt (
    receipt_id text PRIMARY KEY,
    candidate_id text NOT NULL REFERENCES domeye_read.candidate_registry(candidate_id),
    dataset_id text NOT NULL,
    manifest_sha256 text NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    schema_sha256 text NOT NULL CHECK (schema_sha256 ~ '^[0-9a-f]{64}$'),
    database_fingerprint_sha256 text NOT NULL CHECK (database_fingerprint_sha256 ~ '^[0-9a-f]{64}$'),
    loaded_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status = 'complete')
);

CREATE FUNCTION domeye_read.reject_immutable_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'immutable read-model object cannot be updated or deleted';
END;
$$;

CREATE FUNCTION domeye_read.validate_candidate_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'candidate identity cannot be deleted';
    END IF;
    IF OLD.status <> 'loading' OR NEW.status NOT IN ('complete', 'failed') THEN
        RAISE EXCEPTION 'candidate status transition is invalid';
    END IF;
    IF (to_jsonb(NEW) - 'status') <> (to_jsonb(OLD) - 'status') THEN
        RAISE EXCEPTION 'candidate identity fields are immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER validate_candidate_transition
BEFORE UPDATE OR DELETE ON domeye_read.candidate_registry
FOR EACH ROW EXECUTE FUNCTION domeye_read.validate_candidate_transition();

DO $$
DECLARE
    table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'source_binding', 'series_object', 'prefix_vp_evidence_view',
        'event_read_model', 'report_snapshot', 'load_receipt'
    ] LOOP
        EXECUTE format(
            'CREATE TRIGGER reject_mutation BEFORE UPDATE OR DELETE ON domeye_read.%I '
            'FOR EACH ROW EXECUTE FUNCTION domeye_read.reject_immutable_mutation()',
            table_name
        );
    END LOOP;
END;
$$;

CREATE FUNCTION domeye_read.validate_report_pointer_update() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    old_report_version integer;
    new_report_version integer;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'report pointer cannot be deleted';
    END IF;
    IF NEW.candidate_id <> OLD.candidate_id OR NEW.incident_id <> OLD.incident_id THEN
        RAISE EXCEPTION 'report pointer identity cannot change';
    END IF;
    IF NEW.pointer_version <> OLD.pointer_version + 1 THEN
        RAISE EXCEPTION 'report pointer version must advance by one';
    END IF;
    SELECT report_version INTO old_report_version
      FROM domeye_read.report_snapshot
     WHERE candidate_id = OLD.candidate_id
       AND report_snapshot_id = OLD.current_report_snapshot_id;
    SELECT report_version INTO new_report_version
      FROM domeye_read.report_snapshot
     WHERE candidate_id = NEW.candidate_id
       AND report_snapshot_id = NEW.current_report_snapshot_id
       AND incident_id = NEW.incident_id;
    IF new_report_version IS NULL OR new_report_version <> old_report_version + 1 THEN
        RAISE EXCEPTION 'report pointer target must be the next immutable report version';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER validate_report_pointer_update
BEFORE UPDATE OR DELETE ON domeye_read.report_pointer
FOR EACH ROW EXECUTE FUNCTION domeye_read.validate_report_pointer_update();

COMMIT;
