package replay

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	RouteMetricStoreVersion        = "rrc25-route-metric-store/v1"
	RouteMetricSlotVersion         = "rrc25-route-metric-slot/v1"
	RouteMetricDatabaseModel       = "domeye-data-postgresql-timescaledb/v1"
	RouteMetricDefaultParseWorkers = 8
)

type RouteMetricStoreConfig struct {
	RouteEventRoot             string
	RouteStateRoot             string
	RawRoot                    string
	SelectionPath              string
	CompatibleMappingPath      string
	RevisedMappingPath         string
	Output                     string
	RouteEventImplementationID string
	RouteStateImplementationID string
	ImplementationID           string
	Workers                    int
	Resume                     bool
	Progress                   func(string)
}

func (config RouteMetricStoreConfig) progress(message string) {
	if config.Progress != nil {
		config.Progress(message)
	}
}

type RouteMetricStorePreflight struct {
	SchemaVersion               string `json:"schema_version"`
	CandidateID                 string `json:"candidate_id"`
	RunID                       string `json:"run_id"`
	DatasetID                   string `json:"dataset_id"`
	ProjectionID                string `json:"projection_id"`
	CollectorID                 string `json:"collector_id"`
	WindowStartUTC              string `json:"window_start_utc"`
	WindowEndExclusiveUTC       string `json:"window_end_exclusive_utc"`
	StatePointCount             int    `json:"state_point_count"`
	CountryBucketCount          int    `json:"country_bucket_count"`
	ExpectedCountryMetricRows   int64  `json:"expected_country_metric_rows"`
	SourceRouteEventDatasetID   string `json:"source_route_event_dataset_id"`
	SourceRouteEventContentSHA  string `json:"source_route_event_content_sha256"`
	SourceRouteEventManifestSHA string `json:"source_route_event_manifest_sha256"`
	SourceRouteStateDatasetID   string `json:"source_route_state_dataset_id"`
	SourceRouteStateContentSHA  string `json:"source_route_state_content_sha256"`
	SourceRouteStateManifestSHA string `json:"source_route_state_manifest_sha256"`
	SourceRouteStateFinalDigest string `json:"source_route_state_final_digest"`
	RouteEventImplementationID  string `json:"route_event_implementation_id"`
	RouteStateImplementationID  string `json:"route_state_implementation_id"`
	ImplementationID            string `json:"implementation_id"`
	ProjectorName               string `json:"projector_name"`
	ProjectorVersion            string `json:"projector_version"`
	MappingVersion              string `json:"mapping_version"`
	MappingCompatibleSHA256     string `json:"mapping_compatible_sha256"`
	MappingRevisedSHA256        string `json:"mapping_revised_sha256"`
	RouteStateKey               string `json:"route_state_key"`
	ProjectionSource            string `json:"projection_source"`
	DatabaseModel               string `json:"database_model"`
}

type RouteMetricPilotResult struct {
	SchemaVersion             string `json:"schema_version"`
	Status                    string `json:"status"`
	CandidateID               string `json:"candidate_id"`
	MetricDatasetID           string `json:"metric_dataset_id"`
	SourceRouteStateDatasetID string `json:"source_route_state_dataset_id"`
	Slot                      int    `json:"slot"`
	StatePointUTC             string `json:"state_point_utc"`
	CountryMetricRowCount     int    `json:"country_metric_row_count"`
	ASNMetricChangeRowCount   int    `json:"asn_metric_change_row_count"`
	CollectorMetricRowCount   int    `json:"collector_metric_row_count"`
	RouteStateRecordCount     int64  `json:"route_state_record_count"`
	VisibleRouteCount         int64  `json:"visible_route_count"`
	RouteStateDigest          string `json:"route_state_digest"`
	MetricSnapshotSHA256      string `json:"metric_snapshot_sha256"`
	ElapsedMilliseconds       int64  `json:"elapsed_milliseconds"`
}

type RouteMetricSlotRecord struct {
	SchemaVersion              string `json:"schema_version"`
	Slot                       int    `json:"slot"`
	ArtifactTimeUTC            string `json:"artifact_time_utc"`
	StatePointUTC              string `json:"state_point_utc"`
	AttemptedThrough           string `json:"attempted_through"`
	DataThrough                string `json:"data_through"`
	QualityStatus              string `json:"quality_status"`
	GapStatus                  string `json:"gap_status"`
	SourceRouteStateDatasetID  string `json:"source_route_state_dataset_id"`
	SourceRouteStateSlotSHA256 string `json:"source_route_state_slot_sha256"`
	SourceRouteEventFileSHA256 string `json:"source_route_event_file_sha256"`
	TransitionSHA256           string `json:"transition_sha256"`
	RouteEventCount            int64  `json:"route_event_count"`
	AnnounceCount              int64  `json:"announce_count"`
	WithdrawCount              int64  `json:"withdraw_count"`
	RouteStateRecordCount      int64  `json:"route_state_record_count"`
	VisibleRouteCount          int64  `json:"visible_route_count"`
	RouteStateDigest           string `json:"route_state_digest"`
	CountryMetricRowCount      int    `json:"country_metric_row_count"`
	ASNMetricRowCount          int    `json:"asn_metric_row_count"`
	CollectorMetricRowCount    int    `json:"collector_metric_row_count"`
	MetricSnapshotSHA256       string `json:"metric_snapshot_sha256"`
	ContentSHA256              string `json:"content_sha256"`
}

type RouteMetricStoreManifest struct {
	SchemaVersion               string                 `json:"schema_version"`
	Status                      string                 `json:"status"`
	CandidateID                 string                 `json:"candidate_id"`
	RunID                       string                 `json:"run_id"`
	DatasetID                   string                 `json:"dataset_id"`
	ProjectionID                string                 `json:"projection_id"`
	CollectorID                 string                 `json:"collector_id"`
	WindowStartUTC              string                 `json:"window_start_utc"`
	WindowEndExclusiveUTC       string                 `json:"window_end_exclusive_utc"`
	FirstStatePointUTC          string                 `json:"first_state_point_utc"`
	LastStatePointUTC           string                 `json:"last_state_point_utc"`
	StatePointCount             int                    `json:"state_point_count"`
	CountryBucketCount          int                    `json:"country_bucket_count"`
	CountryMetricRowCount       int64                  `json:"country_metric_row_count"`
	ASNMetricChangeRowCount     int64                  `json:"asn_metric_change_row_count"`
	CollectorMetricRowCount     int64                  `json:"collector_metric_row_count"`
	MetricSubjectCount          int64                  `json:"metric_subject_count"`
	SourceRouteEventDatasetID   string                 `json:"source_route_event_dataset_id"`
	SourceRouteEventContentSHA  string                 `json:"source_route_event_content_sha256"`
	SourceRouteEventManifestSHA string                 `json:"source_route_event_manifest_sha256"`
	SourceRouteStateDatasetID   string                 `json:"source_route_state_dataset_id"`
	SourceRouteStateContentSHA  string                 `json:"source_route_state_content_sha256"`
	SourceRouteStateManifestSHA string                 `json:"source_route_state_manifest_sha256"`
	RouteEventImplementationID  string                 `json:"route_event_implementation_id"`
	RouteStateImplementationID  string                 `json:"route_state_implementation_id"`
	ImplementationID            string                 `json:"implementation_id"`
	ProjectorName               string                 `json:"projector_name"`
	ProjectorVersion            string                 `json:"projector_version"`
	MappingVersion              string                 `json:"mapping_version"`
	MappingCompatibleSHA256     string                 `json:"mapping_compatible_sha256"`
	MappingRevisedSHA256        string                 `json:"mapping_revised_sha256"`
	RouteStateKey               string                 `json:"route_state_key"`
	ProjectionSource            string                 `json:"projection_source"`
	MetricDefinitions           []string               `json:"metric_definitions"`
	ASNEncoding                 string                 `json:"asn_encoding"`
	MissingSemantics            string                 `json:"missing_semantics"`
	ZeroSemantics               string                 `json:"zero_semantics"`
	DatabaseModel               string                 `json:"database_model"`
	Files                       []RouteMetricStoreFile `json:"files"`
	AttemptedThrough            string                 `json:"attempted_through"`
	DataThrough                 string                 `json:"data_through"`
	MissingSlotCount            int                    `json:"missing_slot_count"`
	Finality                    string                 `json:"finality"`
	FinalRouteStateRecordCount  int64                  `json:"final_route_state_record_count"`
	FinalVisibleRouteCount      int64                  `json:"final_visible_route_count"`
	FinalRouteStateDigest       string                 `json:"final_route_state_digest"`
	ContentSHA256               string                 `json:"content_sha256"`
}

type routeMetricStoreMarker struct {
	SchemaVersion              string `json:"schema_version"`
	CandidateID                string `json:"candidate_id"`
	RunID                      string `json:"run_id"`
	DatasetID                  string `json:"dataset_id"`
	ProjectionID               string `json:"projection_id"`
	ImplementationID           string `json:"implementation_id"`
	SourceRouteStateDatasetID  string `json:"source_route_state_dataset_id"`
	SourceRouteStateContentSHA string `json:"source_route_state_content_sha256"`
	MappingVersion             string `json:"mapping_version"`
}

type RouteMetricProgress struct {
	SchemaVersion    string `json:"schema_version"`
	Status           string `json:"status"`
	CandidateID      string `json:"candidate_id"`
	DatasetID        string `json:"dataset_id"`
	AttemptedThrough string `json:"attempted_through"`
	DataThrough      string `json:"data_through"`
	ProcessedSlots   int    `json:"processed_slots"`
	DurableSlots     int    `json:"durable_slots"`
	Reason           string `json:"reason,omitempty"`
}

func routeMetricIdentity(
	routeState RouteStateStoreManifest,
	implementationID string,
) (string, string, string, string) {
	candidateID := stableID("domeye_data_candidate_v1_", map[string]any{
		"collector_id":                  "rrc25",
		"window_start_utc":              RouteEventWindowStartUTC,
		"window_end_exclusive_utc":      RouteEventWindowEndUTC,
		"source_route_event_dataset_id": routeState.SourceRouteEventDatasetID,
		"source_route_state_dataset_id": routeState.DatasetID,
		"mapping_version":               routeState.MappingVersion,
		"contract_version":              "domeye-data-layer-224-310/v1.1",
	}, 32)
	runID := stableID("route_metric_run_v1_", map[string]any{
		"candidate_id":                      candidateID,
		"source_route_state_dataset_id":     routeState.DatasetID,
		"source_route_state_content_sha256": routeState.ContentSHA256,
		"implementation_id":                 implementationID,
		"projector_name":                    RouteMetricProjectorName,
		"projector_version":                 RouteMetricProjectorVersion,
	}, 32)
	projectionID := stableID("route_metric_projection_v1_", map[string]any{
		"run_id": runID, "mapping_version": routeState.MappingVersion,
	}, 32)
	datasetID := stableID("route_metric_dataset_v1_", map[string]any{
		"candidate_id": candidateID, "projection_id": projectionID,
	}, 32)
	return candidateID, runID, datasetID, projectionID
}

func routeMetricManifestContentSHA(manifest RouteMetricStoreManifest) string {
	manifest.ContentSHA256 = ""
	raw, _ := json.Marshal(manifest)
	digest := sha256.Sum256(raw)
	return hex.EncodeToString(digest[:])
}

func routeMetricSlotContentSHA(record RouteMetricSlotRecord) string {
	record.ContentSHA256 = ""
	raw, _ := json.Marshal(record)
	digest := sha256.Sum256(raw)
	return hex.EncodeToString(digest[:])
}

func routeMetricSnapshotSHA(snapshot RouteMetricSnapshot) string {
	raw, _ := json.Marshal(snapshot)
	digest := sha256.Sum256(raw)
	return hex.EncodeToString(digest[:])
}

func routeStateIdentityFromManifest(manifest RouteStateStoreManifest) RouteStateCheckpointIdentity {
	return RouteStateCheckpointIdentity{
		RouteStateDatasetID:        manifest.DatasetID,
		SourceRouteEventDatasetID:  manifest.SourceRouteEventDatasetID,
		SourceRouteEventContentSHA: manifest.SourceRouteEventContentSHA,
		ImplementationID:           manifest.ImplementationID,
		ProjectorName:              manifest.ProjectorName,
		ProjectorVersion:           manifest.ProjectorVersion,
		MappingVersion:             manifest.MappingVersion,
		MappingCompatibleSHA256:    manifest.MappingCompatibleSHA256,
		MappingRevisedSHA256:       manifest.MappingRevisedSHA256,
		WindowStartUTC:             manifest.WindowStartUTC,
		WindowEndExclusiveUTC:      manifest.WindowEndExclusiveUTC,
	}
}

func loadRouteMetricSource(
	config RouteMetricStoreConfig,
	verifyCheckpointFiles bool,
) (
	RouteStateStoreManifest,
	RouteStateStorePreflight,
	[]RouteStateSlotRecord,
	string,
	error,
) {
	var manifest RouteStateStoreManifest
	sourceConfig := RouteStateStoreConfig{
		RouteEventRoot:             config.RouteEventRoot,
		RawRoot:                    config.RawRoot,
		SelectionPath:              config.SelectionPath,
		CompatibleMappingPath:      config.CompatibleMappingPath,
		RevisedMappingPath:         config.RevisedMappingPath,
		RouteEventImplementationID: config.RouteEventImplementationID,
		ImplementationID:           config.RouteStateImplementationID,
	}
	preflight, err := PreflightRouteStateStore(sourceConfig)
	if err != nil {
		return manifest, preflight, nil, "", err
	}
	manifestRaw, err := readJSON(filepath.Join(config.RouteStateRoot, "manifest.json"), &manifest)
	if err != nil {
		return manifest, preflight, nil, "", err
	}
	var complete RouteStateStoreManifest
	completeRaw, err := readJSON(filepath.Join(config.RouteStateRoot, "COMPLETE.json"), &complete)
	if err != nil {
		return manifest, preflight, nil, "", err
	}
	if !bytes.Equal(manifestRaw, completeRaw) || manifest.SchemaVersion != RouteStateStoreVersion ||
		manifest.Status != "complete" || manifest.DatasetID != preflight.DatasetID ||
		manifest.RunID != preflight.RunID || manifest.CollectorID != "rrc25" ||
		manifest.WindowStartUTC != RouteEventWindowStartUTC ||
		manifest.WindowEndExclusiveUTC != RouteEventWindowEndUTC ||
		manifest.StatePointCount != RouteStateFinalSlot ||
		manifest.SourceRouteEventDatasetID != preflight.SourceRouteEventDatasetID ||
		manifest.SourceRouteEventContentSHA != preflight.SourceRouteEventContentSHA ||
		manifest.SourceRouteEventManifestSHA != preflight.SourceRouteEventManifestSHA ||
		manifest.ImplementationID != config.RouteStateImplementationID ||
		manifest.MappingVersion != preflight.MappingVersion ||
		manifest.RouteStateKey != "collector + VP/peer + prefix + address_family" ||
		manifest.ProcessedUpdateCount != RouteStateFinalSlot ||
		manifest.AttemptedThrough != RouteEventWindowEndUTC || manifest.DataThrough != RouteEventWindowEndUTC ||
		manifest.ContentSHA256 != routeStateStoreContentSHA(manifest) ||
		len(manifest.Checkpoints) != 3 || len(manifest.SlotLedgers) != 2 {
		return manifest, preflight, nil, "", fmt.Errorf("S2 RouteState source identity mismatch")
	}
	manifestSHA, _, err := sha256File(filepath.Join(config.RouteStateRoot, "manifest.json"))
	if err != nil {
		return manifest, preflight, nil, "", err
	}
	identity := routeStateIdentityFromManifest(manifest)
	if verifyCheckpointFiles {
		for index, slot := range []int{0, RouteStateMidpointSlot, RouteStateFinalSlot} {
			directory := filepath.Join(config.RouteStateRoot, "checkpoints", fmt.Sprintf("slot-%04d", slot))
			checkpoint, _, err := readRouteStateCheckpointManifestQuick(directory, identity)
			if err != nil {
				return manifest, preflight, nil, "", err
			}
			if checkpointReference(
				fmt.Sprintf("checkpoints/slot-%04d", slot), checkpoint,
			) != manifest.Checkpoints[index] {
				return manifest, preflight, nil, "", fmt.Errorf("S2 checkpoint reference mismatch")
			}
		}
	}
	first, firstManifest, err := LoadRouteStateSlotLedger(
		config.RouteStateRoot, manifest.DatasetID, 1, RouteStateMidpointSlot,
	)
	if err != nil {
		return manifest, preflight, nil, "", err
	}
	second, secondManifest, err := LoadRouteStateSlotLedger(
		config.RouteStateRoot, manifest.DatasetID, RouteStateMidpointSlot+1, RouteStateFinalSlot,
	)
	if err != nil {
		return manifest, preflight, nil, "", err
	}
	if firstManifest != manifest.SlotLedgers[0] || secondManifest != manifest.SlotLedgers[1] ||
		first[len(first)-1].StateDigest != manifest.Checkpoints[1].StateDigest ||
		second[len(second)-1].StateDigest != manifest.FinalStateDigest {
		return manifest, preflight, nil, "", fmt.Errorf("S2 RouteState ledger source mismatch")
	}
	return manifest, preflight, append(first, second...), manifestSHA, nil
}

func PreflightRouteMetricStore(
	config RouteMetricStoreConfig,
) (RouteMetricStorePreflight, error) {
	if config.RouteEventRoot == "" || config.RouteStateRoot == "" ||
		config.CompatibleMappingPath == "" || config.RevisedMappingPath == "" ||
		config.RouteEventImplementationID == "" || config.RouteStateImplementationID == "" ||
		config.ImplementationID == "" {
		return RouteMetricStorePreflight{}, fmt.Errorf("route metric paths and identities are required")
	}
	for label, value := range map[string]string{
		"RouteEvent":   config.RouteEventImplementationID,
		"RouteState":   config.RouteStateImplementationID,
		"route metric": config.ImplementationID,
	} {
		if err := validateRouteEventImplementationID(value); err != nil {
			return RouteMetricStorePreflight{}, fmt.Errorf("%s implementation %w", label, err)
		}
	}
	routeState, _, _, manifestSHA, err := loadRouteMetricSource(config, false)
	if err != nil {
		return RouteMetricStorePreflight{}, err
	}
	mapping, err := LoadGlobalCountryMapping(config.CompatibleMappingPath, config.RevisedMappingPath)
	if err != nil {
		return RouteMetricStorePreflight{}, err
	}
	if len(mapping.CountryCodes()) != 241 || mapping.MappingVersion != routeState.MappingVersion {
		return RouteMetricStorePreflight{}, fmt.Errorf("route metric frozen mapping population mismatch")
	}
	candidateID, runID, datasetID, projectionID := routeMetricIdentity(routeState, config.ImplementationID)
	return RouteMetricStorePreflight{
		SchemaVersion: RouteMetricStoreVersion,
		CandidateID:   candidateID, RunID: runID, DatasetID: datasetID, ProjectionID: projectionID,
		CollectorID: "rrc25", WindowStartUTC: RouteEventWindowStartUTC,
		WindowEndExclusiveUTC: RouteEventWindowEndUTC, StatePointCount: RouteStateFinalSlot,
		CountryBucketCount: 241, ExpectedCountryMetricRows: 241 * RouteStateFinalSlot,
		SourceRouteEventDatasetID:   routeState.SourceRouteEventDatasetID,
		SourceRouteEventContentSHA:  routeState.SourceRouteEventContentSHA,
		SourceRouteEventManifestSHA: routeState.SourceRouteEventManifestSHA,
		SourceRouteStateDatasetID:   routeState.DatasetID,
		SourceRouteStateContentSHA:  routeState.ContentSHA256,
		SourceRouteStateManifestSHA: manifestSHA,
		SourceRouteStateFinalDigest: routeState.FinalStateDigest,
		RouteEventImplementationID:  config.RouteEventImplementationID,
		RouteStateImplementationID:  config.RouteStateImplementationID,
		ImplementationID:            config.ImplementationID,
		ProjectorName:               RouteMetricProjectorName, ProjectorVersion: RouteMetricProjectorVersion,
		MappingVersion:          routeState.MappingVersion,
		MappingCompatibleSHA256: routeState.MappingCompatibleSHA256,
		MappingRevisedSHA256:    routeState.MappingRevisedSHA256,
		RouteStateKey:           routeState.RouteStateKey,
		ProjectionSource:        "same_route_state_apply_transition_only",
		DatabaseModel:           RouteMetricDatabaseModel,
	}, nil
}

// PilotRouteMetricFirstSlot 用正式 S2 Seed Checkpoint 和第一个 UPDATE 槽验证
// RouteState 转移、国家/ASN/collector 投影与 S2 ledger 对账，但不形成可发布候选。
func PilotRouteMetricFirstSlot(
	config RouteMetricStoreConfig,
) (RouteMetricPilotResult, error) {
	started := time.Now()
	preflight, err := PreflightRouteMetricStore(config)
	if err != nil {
		return RouteMetricPilotResult{}, err
	}
	routeState, _, ledger, _, err := loadRouteMetricSource(config, false)
	if err != nil {
		return RouteMetricPilotResult{}, err
	}
	routeEvents, _, err := quickRouteEventSource(RouteStateStoreConfig{
		RouteEventRoot:             config.RouteEventRoot,
		RouteEventImplementationID: config.RouteEventImplementationID,
		ImplementationID:           config.RouteStateImplementationID,
	})
	if err != nil {
		return RouteMetricPilotResult{}, err
	}
	mapping, err := LoadGlobalCountryMapping(config.CompatibleMappingPath, config.RevisedMappingPath)
	if err != nil {
		return RouteMetricPilotResult{}, err
	}
	state, seed, err := LoadRouteStateCheckpoint(
		filepath.Join(config.RouteStateRoot, "checkpoints", "slot-0000"),
		routeStateIdentityFromManifest(routeState),
	)
	if err != nil {
		return RouteMetricPilotResult{}, err
	}
	if checkpointReference("checkpoints/slot-0000", seed) != routeState.Checkpoints[0] {
		return RouteMetricPilotResult{}, fmt.Errorf("S2 pilot seed checkpoint identity mismatch")
	}
	projector, err := NewRouteMetricProjectorFromSeed(state, mapping)
	if err != nil {
		return RouteMetricPilotResult{}, err
	}
	parsed, err := parseRouteStatePartition(config.RouteEventRoot, routeEvents.Partitions[1])
	if err != nil {
		return RouteMetricPilotResult{}, err
	}
	for _, event := range parsed.Events {
		if err := projector.Apply(state, event); err != nil {
			return RouteMetricPilotResult{}, err
		}
	}
	actual, err := applyRouteMetricSourceRecord(state, routeEvents.Partitions[1], parsed)
	if err != nil {
		return RouteMetricPilotResult{}, err
	}
	if err := compareRouteMetricSourceSlot(actual, ledger[0]); err != nil {
		return RouteMetricPilotResult{}, err
	}
	snapshot, err := projector.Snapshot(state, actual.StatePointUTC)
	if err != nil {
		return RouteMetricPilotResult{}, err
	}
	if snapshot.Collector.AnnouncementCountV4+snapshot.Collector.AnnouncementCountV6 != actual.AnnounceCount ||
		snapshot.Collector.WithdrawalCountV4+snapshot.Collector.WithdrawalCountV6 != actual.WithdrawCount {
		return RouteMetricPilotResult{}, fmt.Errorf("S3 pilot flow conservation mismatch")
	}
	return RouteMetricPilotResult{
		SchemaVersion: "rrc25-route-metric-pilot/v1", Status: "complete",
		CandidateID: preflight.CandidateID, MetricDatasetID: preflight.DatasetID,
		SourceRouteStateDatasetID: preflight.SourceRouteStateDatasetID,
		Slot:                      1, StatePointUTC: actual.StatePointUTC,
		CountryMetricRowCount: len(snapshot.Countries), ASNMetricChangeRowCount: len(snapshot.ASNs),
		CollectorMetricRowCount: 1, RouteStateRecordCount: actual.RouteStateRecordCount,
		VisibleRouteCount: actual.VisibleRouteCount, RouteStateDigest: actual.StateDigest,
		MetricSnapshotSHA256: routeMetricSnapshotSHA(snapshot),
		ElapsedMilliseconds:  time.Since(started).Milliseconds(),
	}, nil
}

type routeMetricDayWriters struct {
	root           string
	dateUTC        string
	country        *routeMetricTSVWriter
	asn            *routeMetricTSVWriter
	collector      *routeMetricTSVWriter
	slots          *routeMetricTSVWriter
	lastStatePoint string
}

var routeMetricSlotHeader = []string{
	"candidate_id", "metric_dataset_id", "projection_id", "slot", "artifact_time_utc",
	"state_point_utc", "attempted_through", "data_through", "quality_status", "gap_status",
	"source_route_state_dataset_id", "source_route_state_slot_sha256",
	"source_route_event_file_sha256", "transition_sha256", "route_event_count", "announce_count",
	"withdraw_count", "route_state_record_count", "visible_route_count", "route_state_digest",
	"country_metric_row_count", "asn_metric_row_count", "collector_metric_row_count",
	"metric_snapshot_sha256", "content_sha256",
}

func newRouteMetricDayWriters(root string, dateUTC string) (*routeMetricDayWriters, error) {
	compact := strings.ReplaceAll(dateUTC, "-", "")
	result := &routeMetricDayWriters{root: root, dateUTC: dateUTC}
	var err error
	result.country, err = newRouteMetricTSVWriter(
		root, "metrics/country-"+compact+".tsv.gz", "country_metric", dateUTC, routeMetricHeader,
	)
	if err != nil {
		return nil, err
	}
	result.asn, err = newRouteMetricTSVWriter(
		root, "metrics/asn-"+compact+".tsv.gz", "asn_metric_change", dateUTC, routeMetricHeader,
	)
	if err != nil {
		return nil, err
	}
	result.collector, err = newRouteMetricTSVWriter(
		root, "metrics/collector-"+compact+".tsv.gz", "collector_metric", dateUTC, routeMetricHeader,
	)
	if err != nil {
		return nil, err
	}
	result.slots, err = newRouteMetricTSVWriter(
		root, "quality/slots-"+compact+".tsv.gz", "metric_slot", dateUTC, routeMetricSlotHeader,
	)
	if err != nil {
		return nil, err
	}
	return result, nil
}

func (writers *routeMetricDayWriters) Abort() {
	writers.country.Abort()
	writers.asn.Abort()
	writers.collector.Abort()
	writers.slots.Abort()
}

func (writers *routeMetricDayWriters) WriteSnapshot(
	preflight RouteMetricStorePreflight,
	snapshot RouteMetricSnapshot,
	slot RouteMetricSlotRecord,
) error {
	for _, row := range snapshot.Countries {
		if err := writers.country.Write(routeMetricFields(
			preflight.CandidateID, preflight.DatasetID, preflight.ProjectionID, row,
		)); err != nil {
			return err
		}
	}
	for _, row := range snapshot.ASNs {
		if err := writers.asn.Write(routeMetricFields(
			preflight.CandidateID, preflight.DatasetID, preflight.ProjectionID, row,
		)); err != nil {
			return err
		}
	}
	if err := writers.collector.Write(routeMetricFields(
		preflight.CandidateID, preflight.DatasetID, preflight.ProjectionID, snapshot.Collector,
	)); err != nil {
		return err
	}
	fields := []string{
		preflight.CandidateID, preflight.DatasetID, preflight.ProjectionID,
		strconv.Itoa(slot.Slot), slot.ArtifactTimeUTC, slot.StatePointUTC,
		slot.AttemptedThrough, slot.DataThrough, slot.QualityStatus, slot.GapStatus,
		slot.SourceRouteStateDatasetID, slot.SourceRouteStateSlotSHA256,
		slot.SourceRouteEventFileSHA256, slot.TransitionSHA256,
		metricInt(slot.RouteEventCount), metricInt(slot.AnnounceCount), metricInt(slot.WithdrawCount),
		metricInt(slot.RouteStateRecordCount), metricInt(slot.VisibleRouteCount), slot.RouteStateDigest,
		strconv.Itoa(slot.CountryMetricRowCount), strconv.Itoa(slot.ASNMetricRowCount),
		strconv.Itoa(slot.CollectorMetricRowCount), slot.MetricSnapshotSHA256, slot.ContentSHA256,
	}
	if err := writers.slots.Write(fields); err != nil {
		return err
	}
	writers.lastStatePoint = slot.StatePointUTC
	return nil
}

func (writers *routeMetricDayWriters) Close() ([]RouteMetricStoreFile, error) {
	result := make([]RouteMetricStoreFile, 0, 4)
	for _, writer := range []*routeMetricTSVWriter{
		writers.country, writers.asn, writers.collector, writers.slots,
	} {
		meta, err := writer.Close(writers.root)
		if err != nil {
			return nil, err
		}
		result = append(result, meta)
	}
	return result, nil
}

func compareRouteMetricSourceSlot(
	actual RouteStateSlotRecord,
	expected RouteStateSlotRecord,
) error {
	if actual != expected {
		return fmt.Errorf("S3 RouteState transition diverged from S2 slot %d", expected.Slot)
	}
	return nil
}

func routeMetricSlotRecord(
	preflight RouteMetricStorePreflight,
	source RouteStateSlotRecord,
	snapshot RouteMetricSnapshot,
) RouteMetricSlotRecord {
	record := RouteMetricSlotRecord{
		SchemaVersion: RouteMetricSlotVersion, Slot: source.Slot,
		ArtifactTimeUTC: source.ArtifactTimeUTC, StatePointUTC: source.StatePointUTC,
		AttemptedThrough: source.AttemptedThrough, DataThrough: source.DataThrough,
		QualityStatus: "complete", GapStatus: "none",
		SourceRouteStateDatasetID:  preflight.SourceRouteStateDatasetID,
		SourceRouteStateSlotSHA256: source.ContentSHA256,
		SourceRouteEventFileSHA256: source.SourceRouteEventFileSHA,
		TransitionSHA256:           source.TransitionSHA256,
		RouteEventCount:            source.RouteEventCount, AnnounceCount: source.AnnounceCount,
		WithdrawCount:         source.WithdrawCount,
		RouteStateRecordCount: source.RouteStateRecordCount, VisibleRouteCount: source.VisibleRouteCount,
		RouteStateDigest:      source.StateDigest,
		CountryMetricRowCount: len(snapshot.Countries), ASNMetricRowCount: len(snapshot.ASNs),
		CollectorMetricRowCount: 1, MetricSnapshotSHA256: routeMetricSnapshotSHA(snapshot),
	}
	record.ContentSHA256 = routeMetricSlotContentSHA(record)
	return record
}

func writeRouteMetricProgress(output string, progress RouteMetricProgress) error {
	return writeJSONAtomic(filepath.Join(output, "progress.json"), progress)
}

func prepareRouteMetricOutput(
	config RouteMetricStoreConfig,
	preflight RouteMetricStorePreflight,
) (*routeEventStoreLock, error) {
	marker := routeMetricStoreMarker{
		SchemaVersion: RouteMetricStoreVersion, CandidateID: preflight.CandidateID,
		RunID: preflight.RunID, DatasetID: preflight.DatasetID, ProjectionID: preflight.ProjectionID,
		ImplementationID:           config.ImplementationID,
		SourceRouteStateDatasetID:  preflight.SourceRouteStateDatasetID,
		SourceRouteStateContentSHA: preflight.SourceRouteStateContentSHA,
		MappingVersion:             preflight.MappingVersion,
	}
	created := false
	if info, err := os.Lstat(config.Output); err == nil {
		if !config.Resume || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return nil, fmt.Errorf("route metric output exists; use --resume only for a complete identical run")
		}
	} else if !os.IsNotExist(err) {
		return nil, err
	} else {
		if config.Resume {
			return nil, fmt.Errorf("cannot resume absent route metric output")
		}
		if err := os.MkdirAll(config.Output, 0o750); err != nil {
			return nil, err
		}
		created = true
	}
	lock, err := acquireRouteEventStoreLock(config.Output)
	if err != nil {
		return nil, err
	}
	if created {
		if _, err := writeJSONImmutable(filepath.Join(config.Output, "RUNNING.json"), marker); err != nil {
			_ = lock.Close()
			return nil, err
		}
		if err := writeRouteMetricProgress(config.Output, RouteMetricProgress{
			SchemaVersion: "rrc25-route-metric-progress/v1", Status: "running",
			CandidateID: preflight.CandidateID, DatasetID: preflight.DatasetID,
			AttemptedThrough: RouteEventWindowStartUTC, DataThrough: RouteEventWindowStartUTC,
		}); err != nil {
			_ = lock.Close()
			return nil, err
		}
	} else {
		var existing routeMetricStoreMarker
		if _, err := readJSON(filepath.Join(config.Output, "RUNNING.json"), &existing); err != nil {
			_ = lock.Close()
			return nil, err
		}
		if existing != marker {
			_ = lock.Close()
			return nil, fmt.Errorf("route metric RUNNING identity mismatch")
		}
	}
	return lock, nil
}

func loadCompleteRouteMetricStore(
	output string,
	preflight RouteMetricStorePreflight,
) (RouteMetricStoreManifest, error) {
	var manifest RouteMetricStoreManifest
	manifestRaw, err := readJSON(filepath.Join(output, "manifest.json"), &manifest)
	if err != nil {
		return manifest, err
	}
	var complete RouteMetricStoreManifest
	completeRaw, err := readJSON(filepath.Join(output, "COMPLETE.json"), &complete)
	if err != nil {
		return manifest, err
	}
	if !bytes.Equal(manifestRaw, completeRaw) || manifest.SchemaVersion != RouteMetricStoreVersion ||
		manifest.Status != "complete" || manifest.CandidateID != preflight.CandidateID ||
		manifest.RunID != preflight.RunID || manifest.DatasetID != preflight.DatasetID ||
		manifest.ProjectionID != preflight.ProjectionID || manifest.CollectorID != "rrc25" ||
		manifest.WindowStartUTC != RouteEventWindowStartUTC ||
		manifest.WindowEndExclusiveUTC != RouteEventWindowEndUTC ||
		manifest.StatePointCount != RouteStateFinalSlot || manifest.CountryBucketCount != 241 ||
		manifest.CountryMetricRowCount != 241*RouteStateFinalSlot ||
		manifest.CollectorMetricRowCount != RouteStateFinalSlot ||
		manifest.SourceRouteEventDatasetID != preflight.SourceRouteEventDatasetID ||
		manifest.SourceRouteEventContentSHA != preflight.SourceRouteEventContentSHA ||
		manifest.SourceRouteEventManifestSHA != preflight.SourceRouteEventManifestSHA ||
		manifest.SourceRouteStateDatasetID != preflight.SourceRouteStateDatasetID ||
		manifest.SourceRouteStateContentSHA != preflight.SourceRouteStateContentSHA ||
		manifest.RouteEventImplementationID != preflight.RouteEventImplementationID ||
		manifest.RouteStateImplementationID != preflight.RouteStateImplementationID ||
		manifest.ImplementationID != preflight.ImplementationID ||
		manifest.AttemptedThrough != RouteEventWindowEndUTC || manifest.DataThrough != RouteEventWindowEndUTC ||
		manifest.MissingSlotCount != 0 || manifest.Finality != "final" ||
		manifest.ContentSHA256 != routeMetricManifestContentSHA(manifest) {
		return manifest, fmt.Errorf("complete route metric identity mismatch")
	}
	for _, file := range manifest.Files {
		if err := verifyRouteMetricStoreFile(output, file); err != nil {
			return manifest, err
		}
	}
	return manifest, nil
}

func processRouteMetricUpdates(
	config RouteMetricStoreConfig,
	preflight RouteMetricStorePreflight,
	routeEvents RouteEventStoreManifest,
	state *RouteState,
	projector *RouteMetricProjector,
	formalLedger []RouteStateSlotRecord,
	progress *RouteMetricProgress,
) ([]RouteMetricStoreFile, int64, int64, int64, map[uint32]string, error) {
	workers := config.Workers
	if workers < 1 {
		workers = RouteMetricDefaultParseWorkers
	}
	type result struct {
		value parsedRouteStatePartition
		err   error
	}
	jobs := make(chan int)
	results := make(chan result, workers)
	var group sync.WaitGroup
	for worker := 0; worker < workers; worker++ {
		group.Add(1)
		go func() {
			defer group.Done()
			for index := range jobs {
				parsed, err := parseRouteStatePartition(config.RouteEventRoot, routeEvents.Partitions[index])
				results <- result{value: parsed, err: err}
			}
		}()
	}
	nextSchedule := 1
	inflight := 0
	for inflight < workers && nextSchedule <= RouteStateFinalSlot {
		jobs <- nextSchedule
		nextSchedule++
		inflight++
	}
	pending := make(map[int]parsedRouteStatePartition, workers)
	nextApply := 1
	files := make([]RouteMetricStoreFile, 0, 61)
	var day *routeMetricDayWriters
	var countryRows int64
	var asnRows int64
	var collectorRows int64
	firstSeenASN := make(map[uint32]string)
	closeDay := func() error {
		if day == nil {
			return nil
		}
		closed, err := day.Close()
		if err != nil {
			return err
		}
		files = append(files, closed...)
		progress.DataThrough = day.lastStatePoint
		progress.DurableSlots = progress.ProcessedSlots
		if err := writeRouteMetricProgress(config.Output, *progress); err != nil {
			return err
		}
		day = nil
		return nil
	}
	for nextApply <= RouteStateFinalSlot {
		item := <-results
		inflight--
		if item.err != nil {
			close(jobs)
			group.Wait()
			if day != nil {
				day.Abort()
			}
			return nil, 0, 0, 0, nil, item.err
		}
		pending[item.value.Index] = item.value
		for {
			parsed, exists := pending[nextApply]
			if !exists {
				break
			}
			delete(pending, nextApply)
			partition := routeEvents.Partitions[nextApply]
			artifactStart, err := time.Parse(time.RFC3339, partition.Artifact.ArtifactTimeUTC)
			if err != nil {
				close(jobs)
				group.Wait()
				return nil, 0, 0, 0, nil, err
			}
			statePoint := artifactStart.Add(5 * time.Minute).Format(time.RFC3339)
			progress.AttemptedThrough = statePoint
			if err := writeRouteMetricProgress(config.Output, *progress); err != nil {
				close(jobs)
				group.Wait()
				return nil, 0, 0, 0, nil, err
			}
			for _, event := range parsed.Events {
				if err := projector.Apply(state, event); err != nil {
					close(jobs)
					group.Wait()
					return nil, 0, 0, 0, nil, err
				}
			}
			actual, err := applyRouteMetricSourceRecord(state, partition, parsed)
			if err != nil {
				close(jobs)
				group.Wait()
				return nil, 0, 0, 0, nil, err
			}
			expected := formalLedger[nextApply-1]
			if err := compareRouteMetricSourceSlot(actual, expected); err != nil {
				close(jobs)
				group.Wait()
				return nil, 0, 0, 0, nil, err
			}
			snapshot, err := projector.Snapshot(state, statePoint)
			if err != nil {
				close(jobs)
				group.Wait()
				return nil, 0, 0, 0, nil, err
			}
			if snapshot.Collector.AnnouncementCountV4+snapshot.Collector.AnnouncementCountV6 != expected.AnnounceCount ||
				snapshot.Collector.WithdrawalCountV4+snapshot.Collector.WithdrawalCountV6 != expected.WithdrawCount {
				close(jobs)
				group.Wait()
				return nil, 0, 0, 0, nil, fmt.Errorf("route metric flow conservation mismatch at slot %d", nextApply)
			}
			dateUTC := partition.Artifact.ArtifactTimeUTC[:10]
			if day == nil || day.dateUTC != dateUTC {
				if err := closeDay(); err != nil {
					close(jobs)
					group.Wait()
					return nil, 0, 0, 0, nil, err
				}
				day, err = newRouteMetricDayWriters(config.Output, dateUTC)
				if err != nil {
					close(jobs)
					group.Wait()
					return nil, 0, 0, 0, nil, err
				}
			}
			slot := routeMetricSlotRecord(preflight, expected, snapshot)
			if err := day.WriteSnapshot(preflight, snapshot, slot); err != nil {
				close(jobs)
				group.Wait()
				return nil, 0, 0, 0, nil, err
			}
			countryRows += int64(len(snapshot.Countries))
			asnRows += int64(len(snapshot.ASNs))
			collectorRows++
			for _, row := range snapshot.ASNs {
				asn, err := strconv.ParseUint(strings.TrimPrefix(row.SubjectID, "AS"), 10, 32)
				if err != nil {
					return nil, 0, 0, 0, nil, err
				}
				if firstSeenASN[uint32(asn)] == "" {
					firstSeenASN[uint32(asn)] = statePoint
				}
			}
			progress.ProcessedSlots = nextApply
			config.progress(fmt.Sprintf(
				"S3 指标槽已闭合 %d/%d country_rows=%d asn_change_rows=%d",
				nextApply, RouteStateFinalSlot, countryRows, asnRows,
			))
			nextApply++
			if nextSchedule <= RouteStateFinalSlot {
				jobs <- nextSchedule
				nextSchedule++
				inflight++
			}
		}
	}
	close(jobs)
	group.Wait()
	if err := closeDay(); err != nil {
		return nil, 0, 0, 0, nil, err
	}
	if inflight != 0 || len(pending) != 0 || progress.ProcessedSlots != RouteStateFinalSlot {
		return nil, 0, 0, 0, nil, fmt.Errorf("route metric update pipeline did not close")
	}
	return files, countryRows, asnRows, collectorRows, firstSeenASN, nil
}

func applyRouteMetricSourceRecord(
	state *RouteState,
	partition RouteEventPartitionManifest,
	parsed parsedRouteStatePartition,
) (RouteStateSlotRecord, error) {
	if parsed.Index != partition.ArtifactIndex {
		return RouteStateSlotRecord{}, fmt.Errorf("route metric partition order mismatch")
	}
	statePoint, err := time.Parse(time.RFC3339, partition.Artifact.ArtifactTimeUTC)
	if err != nil {
		return RouteStateSlotRecord{}, err
	}
	statePoint = statePoint.Add(5 * time.Minute)
	record := RouteStateSlotRecord{
		SchemaVersion: RouteStateSlotLedgerVersion,
		Slot:          parsed.Index, ArtifactIndex: parsed.Index,
		ArtifactID:       partition.Artifact.ArtifactID,
		ArtifactTimeUTC:  partition.Artifact.ArtifactTimeUTC,
		StatePointUTC:    statePoint.Format(time.RFC3339),
		AttemptedThrough: statePoint.Format(time.RFC3339), DataThrough: statePoint.Format(time.RFC3339),
		SourcePartitionContentSHA: partition.ContentSHA256,
		SourceRouteEventFileSHA:   partition.Events.SHA256,
		RouteEventCount:           parsed.RouteEventCount, AnnounceCount: parsed.AnnounceCount,
		WithdrawCount: parsed.WithdrawCount, TransitionSHA256: parsed.TransitionSHA256,
		RouteStateRecordCount: int64(len(state.Routes)), VisibleRouteCount: state.VisibleRouteCount,
		StateDigest: state.StateDigest.Hex(), QualityStatus: "complete",
	}
	record.ContentSHA256 = routeStateSlotContentSHA(record)
	return record, nil
}

var routeMetricSubjectHeader = []string{
	"candidate_id", "metric_dataset_id", "subject_type", "subject_id", "country_code",
	"sample_encoding", "first_state_point_utc", "valid_through_utc",
	"baseline_route_state_count_v4", "baseline_route_state_count_v6",
	"absence_semantics",
}

func writeRouteMetricSubjects(
	output string,
	preflight RouteMetricStorePreflight,
	projector *RouteMetricProjector,
	firstSeenASN map[uint32]string,
) (RouteMetricStoreFile, int64, error) {
	writer, err := newRouteMetricTSVWriter(
		output, "registry/metric-subjects.tsv.gz", "metric_subject", "", routeMetricSubjectHeader,
	)
	if err != nil {
		return RouteMetricStoreFile{}, 0, err
	}
	codes := projector.mapping.CountryCodes()
	for countryID, code := range codes {
		value := projector.countries[countryID]
		if err := writer.Write([]string{
			preflight.CandidateID, preflight.DatasetID, "country", code, code, "dense_slot",
			"2026-02-24T00:05:00Z", RouteEventWindowEndUTC,
			metricInt(value.Baseline[0]), metricInt(value.Baseline[1]),
			"row_absence_is_missing_because_country_is_dense",
		}); err != nil {
			writer.Abort()
			return RouteMetricStoreFile{}, 0, err
		}
	}
	if err := writer.Write([]string{
		preflight.CandidateID, preflight.DatasetID, "collector", "rrc25", `\N`, "dense_slot",
		"2026-02-24T00:05:00Z", RouteEventWindowEndUTC,
		metricInt(projector.collector.Baseline[0]), metricInt(projector.collector.Baseline[1]),
		"row_absence_is_missing_because_collector_is_dense",
	}); err != nil {
		writer.Abort()
		return RouteMetricStoreFile{}, 0, err
	}
	asns := make([]uint32, 0, len(projector.asns))
	for asn := range projector.asns {
		asns = append(asns, asn)
	}
	sort.Slice(asns, func(i, j int) bool { return asns[i] < asns[j] })
	for _, asn := range asns {
		value := projector.asns[asn]
		first := firstSeenASN[asn]
		if first == "" {
			return RouteMetricStoreFile{}, 0, fmt.Errorf("ASN metric subject has no first observation")
		}
		country := projector.mapping.CountryCode(projector.mapping.CountryID(asn))
		if err := writer.Write([]string{
			preflight.CandidateID, preflight.DatasetID, "asn", fmt.Sprintf("AS%d", asn), country,
			"change_point", first, RouteEventWindowEndUTC,
			metricInt(value.Baseline[0]), metricInt(value.Baseline[1]),
			"state_columns_carry_forward_flow_columns_zero_when_complete_slot_has_no_row",
		}); err != nil {
			writer.Abort()
			return RouteMetricStoreFile{}, 0, err
		}
	}
	meta, err := writer.Close(output)
	return meta, int64(len(codes) + 1 + len(asns)), err
}

func runRouteMetricStore(
	config RouteMetricStoreConfig,
	preflight RouteMetricStorePreflight,
	progress *RouteMetricProgress,
) (RouteMetricStoreManifest, error) {
	routeState, _, formalLedger, manifestSHA, err := loadRouteMetricSource(config, true)
	if err != nil {
		return RouteMetricStoreManifest{}, err
	}
	if manifestSHA != preflight.SourceRouteStateManifestSHA ||
		routeState.ContentSHA256 != preflight.SourceRouteStateContentSHA {
		return RouteMetricStoreManifest{}, fmt.Errorf("S2 RouteState source changed after S3 preflight")
	}
	sourceConfig := RouteStateStoreConfig{
		RouteEventRoot: config.RouteEventRoot, RawRoot: config.RawRoot,
		SelectionPath:              config.SelectionPath,
		CompatibleMappingPath:      config.CompatibleMappingPath,
		RevisedMappingPath:         config.RevisedMappingPath,
		RouteEventImplementationID: config.RouteEventImplementationID,
		ImplementationID:           config.RouteStateImplementationID,
	}
	routeEvents, routeEventManifestSHA, err := verifyRouteEventSource(sourceConfig)
	if err != nil {
		return RouteMetricStoreManifest{}, err
	}
	if routeEvents.DatasetID != preflight.SourceRouteEventDatasetID ||
		routeEvents.ContentSHA256 != preflight.SourceRouteEventContentSHA ||
		routeEventManifestSHA != routeState.SourceRouteEventManifestSHA {
		return RouteMetricStoreManifest{}, fmt.Errorf("S1 RouteEvent source changed before S3 projection")
	}
	mapping, err := LoadGlobalCountryMapping(config.CompatibleMappingPath, config.RevisedMappingPath)
	if err != nil {
		return RouteMetricStoreManifest{}, err
	}
	identity := routeStateIdentityFromManifest(routeState)
	state, seed, err := LoadRouteStateCheckpoint(
		filepath.Join(config.RouteStateRoot, "checkpoints", "slot-0000"), identity,
	)
	if err != nil {
		return RouteMetricStoreManifest{}, err
	}
	if checkpointReference("checkpoints/slot-0000", seed) != routeState.Checkpoints[0] {
		return RouteMetricStoreManifest{}, fmt.Errorf("S2 seed checkpoint changed before S3 projection")
	}
	projector, err := NewRouteMetricProjectorFromSeed(state, mapping)
	if err != nil {
		return RouteMetricStoreManifest{}, err
	}
	files, countryRows, asnRows, collectorRows, firstSeenASN, err := processRouteMetricUpdates(
		config, preflight, routeEvents, state, projector, formalLedger, progress,
	)
	if err != nil {
		return RouteMetricStoreManifest{}, err
	}
	subjectFile, subjectCount, err := writeRouteMetricSubjects(
		config.Output, preflight, projector, firstSeenASN,
	)
	if err != nil {
		return RouteMetricStoreManifest{}, err
	}
	files = append(files, subjectFile)
	if countryRows != preflight.ExpectedCountryMetricRows || collectorRows != RouteStateFinalSlot ||
		progress.DataThrough != RouteEventWindowEndUTC || progress.DurableSlots != RouteStateFinalSlot ||
		int64(len(state.Routes)) != routeState.RouteStateRecordCount ||
		state.VisibleRouteCount != routeState.VisibleRouteCount ||
		state.StateDigest.Hex() != routeState.FinalStateDigest {
		return RouteMetricStoreManifest{}, fmt.Errorf("S3 final metric population or RouteState identity mismatch")
	}
	manifest := RouteMetricStoreManifest{
		SchemaVersion: RouteMetricStoreVersion, Status: "complete",
		CandidateID: preflight.CandidateID, RunID: preflight.RunID,
		DatasetID: preflight.DatasetID, ProjectionID: preflight.ProjectionID,
		CollectorID: "rrc25", WindowStartUTC: RouteEventWindowStartUTC,
		WindowEndExclusiveUTC: RouteEventWindowEndUTC,
		FirstStatePointUTC:    "2026-02-24T00:05:00Z", LastStatePointUTC: RouteEventWindowEndUTC,
		StatePointCount: RouteStateFinalSlot, CountryBucketCount: 241,
		CountryMetricRowCount: countryRows, ASNMetricChangeRowCount: asnRows,
		CollectorMetricRowCount: collectorRows, MetricSubjectCount: subjectCount,
		SourceRouteEventDatasetID:   preflight.SourceRouteEventDatasetID,
		SourceRouteEventContentSHA:  preflight.SourceRouteEventContentSHA,
		SourceRouteEventManifestSHA: preflight.SourceRouteEventManifestSHA,
		SourceRouteStateDatasetID:   preflight.SourceRouteStateDatasetID,
		SourceRouteStateContentSHA:  preflight.SourceRouteStateContentSHA,
		SourceRouteStateManifestSHA: preflight.SourceRouteStateManifestSHA,
		RouteEventImplementationID:  preflight.RouteEventImplementationID,
		RouteStateImplementationID:  preflight.RouteStateImplementationID,
		ImplementationID:            preflight.ImplementationID,
		ProjectorName:               preflight.ProjectorName, ProjectorVersion: preflight.ProjectorVersion,
		MappingVersion:          preflight.MappingVersion,
		MappingCompatibleSHA256: preflight.MappingCompatibleSHA256,
		MappingRevisedSHA256:    preflight.MappingRevisedSHA256,
		RouteStateKey:           preflight.RouteStateKey, ProjectionSource: preflight.ProjectionSource,
		MetricDefinitions: []string{
			"baseline_route_state_count_by_seed_origin_and_address_family",
			"cohort_visible_route_state_count_requires_visible_and_same_seed_origin",
			"current_visible_route_state_count_by_current_origin_and_address_family",
			"announcement_and_withdrawal_route_event_count_by_attributed_origin_and_address_family",
		},
		ASNEncoding:      "first_slot_snapshot_plus_change_points_state_locf_flow_zero_on_complete_absence",
		MissingSemantics: "missing_metric_slot_is_missing_not_zero_and_blocks_data_through",
		ZeroSemantics:    "explicit_integer_zero_is_observed_zero; zero_baseline_ratio_is_not_applicable",
		DatabaseModel:    RouteMetricDatabaseModel, Files: files,
		AttemptedThrough: RouteEventWindowEndUTC, DataThrough: RouteEventWindowEndUTC,
		MissingSlotCount: 0, Finality: "final",
		FinalRouteStateRecordCount: int64(len(state.Routes)),
		FinalVisibleRouteCount:     state.VisibleRouteCount,
		FinalRouteStateDigest:      state.StateDigest.Hex(),
	}
	manifest.ContentSHA256 = routeMetricManifestContentSHA(manifest)
	if _, err := writeJSONImmutable(filepath.Join(config.Output, "manifest.json"), manifest); err != nil {
		return RouteMetricStoreManifest{}, err
	}
	if _, err := writeJSONImmutable(filepath.Join(config.Output, "COMPLETE.json"), manifest); err != nil {
		return RouteMetricStoreManifest{}, err
	}
	progress.Status = "complete"
	progress.AttemptedThrough = RouteEventWindowEndUTC
	progress.DataThrough = RouteEventWindowEndUTC
	progress.Reason = ""
	if err := writeRouteMetricProgress(config.Output, *progress); err != nil {
		return RouteMetricStoreManifest{}, err
	}
	return manifest, nil
}

func RunRouteMetricStore(config RouteMetricStoreConfig) (RouteMetricStoreManifest, error) {
	if config.Output == "" {
		return RouteMetricStoreManifest{}, fmt.Errorf("route metric output is required")
	}
	if config.Workers < 1 {
		config.Workers = RouteMetricDefaultParseWorkers
	}
	preflight, err := PreflightRouteMetricStore(config)
	if err != nil {
		return RouteMetricStoreManifest{}, err
	}
	lock, err := prepareRouteMetricOutput(config, preflight)
	if err != nil {
		return RouteMetricStoreManifest{}, err
	}
	defer lock.Close()
	if config.Resume {
		if _, err := os.Lstat(filepath.Join(config.Output, "COMPLETE.json")); err == nil {
			return loadCompleteRouteMetricStore(config.Output, preflight)
		} else if !os.IsNotExist(err) {
			return RouteMetricStoreManifest{}, err
		}
		return RouteMetricStoreManifest{}, fmt.Errorf("unfinished route metric output is not a complete resumable candidate")
	}
	progress := RouteMetricProgress{
		SchemaVersion: "rrc25-route-metric-progress/v1", Status: "running",
		CandidateID: preflight.CandidateID, DatasetID: preflight.DatasetID,
		AttemptedThrough: RouteEventWindowStartUTC, DataThrough: RouteEventWindowStartUTC,
	}
	manifest, err := runRouteMetricStore(config, preflight, &progress)
	if err != nil {
		progress.Status = "failed"
		progress.Reason = err.Error()
		_ = writeRouteMetricProgress(config.Output, progress)
		return RouteMetricStoreManifest{}, err
	}
	return manifest, nil
}
