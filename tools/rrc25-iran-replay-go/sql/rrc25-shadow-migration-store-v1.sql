BEGIN;

CREATE SCHEMA domeye_migration;
CREATE SCHEMA domeye_control;
CREATE SCHEMA domeye_runtime;

CREATE TABLE domeye_migration.candidate_registry (
    candidate_id text PRIMARY KEY,
    dataset_id text NOT NULL UNIQUE,
    collector_id text NOT NULL CHECK (collector_id = 'rrc25'),
    window_start_utc timestamptz NOT NULL CHECK (window_start_utc = TIMESTAMPTZ '2026-02-24 00:00:00+00'),
    window_end_exclusive_utc timestamptz NOT NULL CHECK (window_end_exclusive_utc = TIMESTAMPTZ '2026-03-11 00:00:00+00'),
    state_point_count integer NOT NULL CHECK (state_point_count = 4320),
    country_bucket_count integer NOT NULL CHECK (country_bucket_count = 241),
    implementation_id text NOT NULL CHECK (implementation_id ~ '^[0-9a-f]{40}$'),
    manifest_sha256 text NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    selected_by_acceptance_runtime boolean NOT NULL DEFAULT false,
    selected_by_production boolean NOT NULL DEFAULT false CHECK (NOT selected_by_production),
    status text NOT NULL CHECK (status IN ('loading', 'complete', 'failed'))
);

CREATE TABLE domeye_migration.import_batch (
    import_batch_id text PRIMARY KEY,
    candidate_id text NOT NULL REFERENCES domeye_migration.candidate_registry(candidate_id),
    source_database text NOT NULL CHECK (source_database = 'bgp_project'),
    source_schema_sha256 text NOT NULL CHECK (source_schema_sha256 ~ '^[0-9a-f]{64}$'),
    source_snapshot_set_sha256 text NOT NULL CHECK (source_snapshot_set_sha256 ~ '^[0-9a-f]{64}$'),
    source_time_semantics text NOT NULL CHECK (source_time_semantics = 'timestamp_without_time_zone_as_Asia/Shanghai_then_UTC'),
    extraction_transaction text NOT NULL CHECK (extraction_transaction = 'repeatable_read_read_only'),
    status text NOT NULL CHECK (status = 'complete'),
    payload jsonb NOT NULL
);

CREATE TABLE domeye_migration.source_table_snapshot (
    candidate_id text NOT NULL REFERENCES domeye_migration.candidate_registry(candidate_id),
    import_batch_id text NOT NULL REFERENCES domeye_migration.import_batch(import_batch_id),
    snapshot_id text NOT NULL,
    source_database text NOT NULL CHECK (source_database = 'bgp_project'),
    source_table text NOT NULL,
    semantic_family text NOT NULL,
    source_time_column text NOT NULL,
    source_time_semantics text NOT NULL CHECK (source_time_semantics = 'timestamp_without_time_zone_as_Asia/Shanghai_then_UTC'),
    source_pk_fields jsonb NOT NULL,
    has_declared_primary_key boolean NOT NULL,
    scope_row_count bigint NOT NULL CHECK (scope_row_count >= 0),
    min_source_time_utc timestamptz,
    max_source_time_utc timestamptz,
    multiset_fingerprint_sha256 text NOT NULL CHECK (multiset_fingerprint_sha256 ~ '^[0-9a-f]{64}$'),
    schema_fragment_sha256 text NOT NULL CHECK (schema_fragment_sha256 ~ '^[0-9a-f]{64}$'),
    disposition text NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (candidate_id, snapshot_id),
    UNIQUE (candidate_id, source_table)
);

CREATE TABLE domeye_migration.legacy_country_outage (
    candidate_id text NOT NULL REFERENCES domeye_migration.candidate_registry(candidate_id),
    import_batch_id text NOT NULL REFERENCES domeye_migration.import_batch(import_batch_id),
    source_record_id text NOT NULL,
    source_table text NOT NULL,
    source_primary_key jsonb NOT NULL,
    legacy_reference text NOT NULL,
    source_code text NOT NULL CHECK (source_code = 'r'),
    collector_id text NOT NULL CHECK (collector_id = 'rrc25'),
    country_code text,
    outage_id integer NOT NULL,
    source_start_time_local timestamp NOT NULL,
    normalized_start_time_utc timestamptz NOT NULL,
    normalized_end_time_utc timestamptz,
    source_row_sha256 text NOT NULL CHECK (source_row_sha256 ~ '^[0-9a-f]{64}$'),
    import_disposition text NOT NULL CHECK (import_disposition IN ('reconciled_to_unified_incident', 'trace_only_not_in_frozen_publication_registry', 'quarantined_invalid_country_code')),
    unified_incident_id text,
    payload jsonb NOT NULL,
    PRIMARY KEY (candidate_id, source_record_id),
    UNIQUE (candidate_id, legacy_reference)
);

CREATE TABLE domeye_migration.source_field_reconciliation (
    candidate_id text NOT NULL REFERENCES domeye_migration.candidate_registry(candidate_id),
    import_batch_id text NOT NULL REFERENCES domeye_migration.import_batch(import_batch_id),
    reconciliation_id text NOT NULL,
    source_table text NOT NULL,
    source_field text NOT NULL,
    standardized_field text,
    comparison text NOT NULL CHECK (comparison IN ('mapped', 'not_comparable', 'trace_only')),
    reason_code text NOT NULL,
    disposition_status text NOT NULL CHECK (disposition_status = 'closed'),
    source_row_count bigint NOT NULL CHECK (source_row_count >= 0),
    unified_population bigint NOT NULL CHECK (unified_population >= 0),
    payload jsonb NOT NULL,
    PRIMARY KEY (candidate_id, reconciliation_id),
    UNIQUE (candidate_id, source_table, source_field),
    FOREIGN KEY (candidate_id, source_table) REFERENCES domeye_migration.source_table_snapshot(candidate_id, source_table)
);

CREATE TABLE domeye_migration.reconciliation_field (
    candidate_id text NOT NULL REFERENCES domeye_migration.candidate_registry(candidate_id),
    reconciliation_id text NOT NULL,
    source_record_id text NOT NULL,
    field_name text NOT NULL,
    legacy_value jsonb NOT NULL,
    unified_value jsonb NOT NULL,
    comparison text NOT NULL CHECK (comparison IN ('equal', 'mapped', 'not_comparable', 'absent', 'invalid')),
    reason_code text NOT NULL,
    disposition_status text NOT NULL CHECK (disposition_status IN ('closed', 'quarantined')),
    PRIMARY KEY (candidate_id, reconciliation_id),
    FOREIGN KEY (candidate_id, source_record_id) REFERENCES domeye_migration.legacy_country_outage(candidate_id, source_record_id),
    UNIQUE (candidate_id, source_record_id, field_name)
);

CREATE TABLE domeye_control.release_object (
    candidate_id text NOT NULL REFERENCES domeye_migration.candidate_registry(candidate_id),
    object_id text NOT NULL,
    object_kind text NOT NULL,
    object_state text NOT NULL CHECK (object_state IN ('incomplete', 'quarantine', 'candidate', 'formal')),
    path_identity text NOT NULL,
    dataset_id text,
    content_sha256 text,
    runtime_readable boolean NOT NULL,
    retention_policy text NOT NULL CHECK (retention_policy IN ('preserve', 'referenced', 'ephemeral')),
    payload jsonb NOT NULL,
    CHECK ((object_state = 'formal' AND content_sha256 ~ '^[0-9a-f]{64}$') OR object_state <> 'formal'),
    CHECK (NOT runtime_readable OR object_state = 'formal'),
    PRIMARY KEY (candidate_id, object_id)
);

CREATE TABLE domeye_control.object_reference (
    candidate_id text NOT NULL,
    reference_id text NOT NULL,
    object_id text NOT NULL,
    reference_kind text NOT NULL CHECK (reference_kind IN ('dataset', 'publication', 'report', 'migration', 'retention_policy')),
    reference_source_id text NOT NULL,
    purpose text NOT NULL,
    PRIMARY KEY (candidate_id, reference_id),
    FOREIGN KEY (candidate_id, object_id) REFERENCES domeye_control.release_object(candidate_id, object_id)
);

CREATE TABLE domeye_control.release_bundle (
    candidate_id text NOT NULL REFERENCES domeye_migration.candidate_registry(candidate_id),
    bundle_id text NOT NULL,
    bundle_mode text NOT NULL CHECK (bundle_mode IN ('legacy_readonly_rollback', 'unified', 'invalid_incomplete')),
    bundle_state text NOT NULL CHECK (bundle_state IN ('incomplete', 'complete')),
    content_sha256 text,
    coherent_components jsonb NOT NULL,
    payload jsonb NOT NULL,
    CHECK ((bundle_state = 'complete' AND content_sha256 ~ '^[0-9a-f]{64}$') OR bundle_state = 'incomplete'),
    PRIMARY KEY (candidate_id, bundle_id)
);

CREATE TABLE domeye_control.bundle_object (
    candidate_id text NOT NULL,
    bundle_id text NOT NULL,
    object_id text NOT NULL,
    purpose text NOT NULL,
    PRIMARY KEY (candidate_id, bundle_id, object_id),
    FOREIGN KEY (candidate_id, bundle_id) REFERENCES domeye_control.release_bundle(candidate_id, bundle_id),
    FOREIGN KEY (candidate_id, object_id) REFERENCES domeye_control.release_object(candidate_id, object_id)
);

CREATE TABLE domeye_control.release_pointer (
    pointer_name text PRIMARY KEY CHECK (pointer_name = 'rrc25_224_310_acceptance'),
    candidate_id text NOT NULL,
    current_bundle_id text NOT NULL,
    pointer_version bigint NOT NULL CHECK (pointer_version > 0),
    selected_by_production boolean NOT NULL DEFAULT false CHECK (NOT selected_by_production),
    reason text NOT NULL,
    FOREIGN KEY (candidate_id, current_bundle_id) REFERENCES domeye_control.release_bundle(candidate_id, bundle_id)
);

CREATE TABLE domeye_control.switch_audit (
    audit_id text PRIMARY KEY,
    candidate_id text NOT NULL,
    pointer_name text NOT NULL,
    from_bundle_id text,
    to_bundle_id text NOT NULL,
    from_version bigint NOT NULL,
    to_version bigint NOT NULL,
    actor_identity text NOT NULL,
    reason text NOT NULL,
    switched_at timestamptz NOT NULL,
    FOREIGN KEY (candidate_id, to_bundle_id) REFERENCES domeye_control.release_bundle(candidate_id, bundle_id)
);

CREATE TABLE domeye_control.role_contract (
    candidate_id text NOT NULL REFERENCES domeye_migration.candidate_registry(candidate_id),
    identity_name text NOT NULL,
    identity_kind text NOT NULL CHECK (identity_kind IN ('migration_reader', 'publisher', 'runtime')),
    can_read_legacy boolean NOT NULL,
    can_write_legacy boolean NOT NULL CHECK (NOT can_write_legacy),
    can_read_control_base boolean NOT NULL,
    can_write_control_base boolean NOT NULL,
    can_execute_atomic_switch boolean NOT NULL,
    can_read_runtime_view boolean NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (candidate_id, identity_name)
);

CREATE TABLE domeye_control.dlae_evidence (
    candidate_id text NOT NULL REFERENCES domeye_migration.candidate_registry(candidate_id),
    acceptance_id text NOT NULL CHECK (acceptance_id ~ '^DLAE-(0[1-9]|1[0-6])$'),
    status text NOT NULL CHECK (status = 'passed'),
    evidence_refs jsonb NOT NULL,
    evidence_sha256 text NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
    payload jsonb NOT NULL,
    PRIMARY KEY (candidate_id, acceptance_id)
);

CREATE TABLE domeye_control.load_receipt (
    receipt_id text PRIMARY KEY,
    candidate_id text NOT NULL REFERENCES domeye_migration.candidate_registry(candidate_id),
    dataset_id text NOT NULL,
    manifest_sha256 text NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    schema_sha256 text NOT NULL CHECK (schema_sha256 ~ '^[0-9a-f]{64}$'),
    database_fingerprint_sha256 text NOT NULL CHECK (database_fingerprint_sha256 ~ '^[0-9a-f]{64}$'),
    selected_bundle_id text NOT NULL,
    selected_pointer_version bigint NOT NULL,
    loaded_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status = 'complete')
);

CREATE FUNCTION domeye_control.reject_immutable_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'immutable S6 object cannot be updated or deleted';
END;
$$;

CREATE FUNCTION domeye_migration.validate_candidate_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'candidate identity cannot be deleted';
    END IF;
    IF OLD.status <> 'loading' OR NEW.status NOT IN ('complete', 'failed') THEN
        RAISE EXCEPTION 'candidate status transition is invalid';
    END IF;
    IF (to_jsonb(NEW) - ARRAY['status','selected_by_acceptance_runtime']) <>
       (to_jsonb(OLD) - ARRAY['status','selected_by_acceptance_runtime']) THEN
        RAISE EXCEPTION 'candidate identity fields are immutable';
    END IF;
    IF NEW.status = 'complete' AND NOT NEW.selected_by_acceptance_runtime THEN
        RAISE EXCEPTION 'complete S6 candidate must be selected by acceptance runtime';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER validate_candidate_transition
BEFORE UPDATE OR DELETE ON domeye_migration.candidate_registry
FOR EACH ROW EXECUTE FUNCTION domeye_migration.validate_candidate_transition();

DO $$
DECLARE
    relation_name text;
BEGIN
    FOREACH relation_name IN ARRAY ARRAY[
        'domeye_migration.source_table_snapshot',
        'domeye_migration.import_batch',
        'domeye_migration.legacy_country_outage',
        'domeye_migration.source_field_reconciliation',
        'domeye_migration.reconciliation_field',
        'domeye_control.release_object',
        'domeye_control.object_reference',
        'domeye_control.release_bundle',
        'domeye_control.bundle_object',
        'domeye_control.switch_audit',
        'domeye_control.role_contract',
        'domeye_control.dlae_evidence',
        'domeye_control.load_receipt'
    ] LOOP
        EXECUTE format(
            'CREATE TRIGGER reject_mutation BEFORE UPDATE OR DELETE ON %s '
            'FOR EACH ROW EXECUTE FUNCTION domeye_control.reject_immutable_mutation()',
            relation_name
        );
    END LOOP;
END;
$$;

CREATE FUNCTION domeye_control.validate_pointer_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF current_setting('domeye.atomic_switch', true) <> 'on' THEN
        RAISE EXCEPTION 'release pointer may change only through atomic switch';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'release pointer cannot be deleted';
    END IF;
    IF NEW.pointer_name <> OLD.pointer_name OR NEW.candidate_id <> OLD.candidate_id THEN
        RAISE EXCEPTION 'release pointer identity cannot change';
    END IF;
    IF NEW.pointer_version <> OLD.pointer_version + 1 THEN
        RAISE EXCEPTION 'release pointer version must advance by one';
    END IF;
    IF NEW.selected_by_production THEN
        RAISE EXCEPTION 'acceptance switch cannot select production';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER validate_pointer_mutation
BEFORE UPDATE OR DELETE ON domeye_control.release_pointer
FOR EACH ROW EXECUTE FUNCTION domeye_control.validate_pointer_mutation();

CREATE FUNCTION domeye_control.switch_release(
    expected_pointer_version bigint,
    target_bundle_id text,
    switch_reason text
) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, domeye_control, domeye_migration
AS $$
DECLARE
    pointer_row domeye_control.release_pointer%ROWTYPE;
    target_row domeye_control.release_bundle%ROWTYPE;
    invalid_object_count bigint;
    new_version bigint;
    audit_identity text;
BEGIN
    SELECT * INTO pointer_row FROM domeye_control.release_pointer
     WHERE pointer_name = 'rrc25_224_310_acceptance' FOR UPDATE;
    IF pointer_row.pointer_version <> expected_pointer_version THEN
        RAISE EXCEPTION 'stale release pointer version';
    END IF;
    SELECT * INTO target_row FROM domeye_control.release_bundle
     WHERE candidate_id = pointer_row.candidate_id AND bundle_id = target_bundle_id;
    IF target_row.bundle_id IS NULL OR target_row.bundle_state <> 'complete' THEN
        RAISE EXCEPTION 'target release bundle is not complete';
    END IF;
    SELECT count(*) INTO invalid_object_count
      FROM domeye_control.bundle_object bo
      JOIN domeye_control.release_object ro USING (candidate_id, object_id)
     WHERE bo.candidate_id = pointer_row.candidate_id
       AND bo.bundle_id = target_bundle_id
       AND (ro.object_state <> 'formal' OR ro.content_sha256 IS NULL);
    IF invalid_object_count <> 0 THEN
        RAISE EXCEPTION 'target release bundle has non-formal object';
    END IF;
    IF target_row.bundle_mode = 'unified' AND NOT (
        target_row.coherent_components ? 'route_event_dataset_id' AND
        target_row.coherent_components ? 'route_state_dataset_id' AND
        target_row.coherent_components ? 'metric_dataset_id' AND
        target_row.coherent_components ? 'event_publication_dataset_id' AND
        target_row.coherent_components ? 'read_model_dataset_id' AND
        target_row.coherent_components ? 'migration_dataset_id'
    ) THEN
        RAISE EXCEPTION 'unified release bundle is not coherent';
    END IF;
    PERFORM set_config('domeye.atomic_switch', 'on', true);
    new_version := pointer_row.pointer_version + 1;
    UPDATE domeye_control.release_pointer
       SET current_bundle_id = target_bundle_id,
           pointer_version = new_version,
           reason = switch_reason,
           selected_by_production = false
     WHERE pointer_name = pointer_row.pointer_name;
    audit_identity := 'switch_audit_v1_' || substr(md5(
        pointer_row.candidate_id || ':' || new_version::text || ':' || target_bundle_id || ':' || switch_reason
    ), 1, 32);
    INSERT INTO domeye_control.switch_audit(
        audit_id,candidate_id,pointer_name,from_bundle_id,to_bundle_id,
        from_version,to_version,actor_identity,reason,switched_at
    ) VALUES (
        audit_identity,pointer_row.candidate_id,pointer_row.pointer_name,
        pointer_row.current_bundle_id,target_bundle_id,pointer_row.pointer_version,
        new_version,current_setting('role'),switch_reason,clock_timestamp()
    );
    RETURN new_version;
END;
$$;

CREATE VIEW domeye_runtime.selected_release AS
SELECT p.pointer_name,p.candidate_id,p.current_bundle_id AS bundle_id,
       p.pointer_version,b.bundle_mode,b.content_sha256,b.coherent_components,
       c.dataset_id,c.collector_id,c.window_start_utc,c.window_end_exclusive_utc,
       c.state_point_count,c.country_bucket_count,c.manifest_sha256,
       c.selected_by_acceptance_runtime,c.selected_by_production
  FROM domeye_control.release_pointer p
  JOIN domeye_control.release_bundle b
    ON b.candidate_id=p.candidate_id AND b.bundle_id=p.current_bundle_id
  JOIN domeye_migration.candidate_registry c ON c.candidate_id=p.candidate_id
 WHERE b.bundle_state='complete' AND b.bundle_mode='unified'
   AND c.status='complete' AND c.selected_by_acceptance_runtime
   AND NOT c.selected_by_production AND NOT p.selected_by_production;

CREATE VIEW domeye_runtime.selected_object AS
SELECT r.candidate_id,r.object_id,r.object_kind,r.path_identity,r.dataset_id,
       r.content_sha256,bo.purpose
  FROM domeye_runtime.selected_release s
  JOIN domeye_control.bundle_object bo
    ON bo.candidate_id=s.candidate_id AND bo.bundle_id=s.bundle_id
  JOIN domeye_control.release_object r
    ON r.candidate_id=bo.candidate_id AND r.object_id=bo.object_id
 WHERE r.object_state='formal' AND r.runtime_readable;

CREATE VIEW domeye_control.retention_eligibility AS
SELECT o.candidate_id,o.object_id,o.object_state,o.retention_policy,
       count(DISTINCT r.reference_id) AS reference_count,
       count(DISTINCT bo.bundle_id) FILTER (
           WHERE p.current_bundle_id=bo.bundle_id
       ) AS selected_bundle_reference_count,
       (o.object_state IN ('candidate','quarantine')
        AND o.retention_policy='ephemeral'
        AND count(DISTINCT r.reference_id)=0
        AND count(DISTINCT bo.bundle_id) FILTER (
            WHERE p.current_bundle_id=bo.bundle_id
        )=0) AS eligible_for_collection
  FROM domeye_control.release_object o
  LEFT JOIN domeye_control.object_reference r USING(candidate_id,object_id)
  LEFT JOIN domeye_control.bundle_object bo USING(candidate_id,object_id)
  LEFT JOIN domeye_control.release_pointer p USING(candidate_id)
 GROUP BY o.candidate_id,o.object_id,o.object_state,o.retention_policy;

REVOKE ALL ON SCHEMA domeye_migration, domeye_control, domeye_runtime FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA domeye_migration, domeye_control, domeye_runtime FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA domeye_migration, domeye_control FROM PUBLIC;

COMMIT;
