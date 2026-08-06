package replay

import (
	"bufio"
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"
)

const (
	RouteStateStoreVersion        = "rrc25-route-state-store/v1"
	RouteStateSlotLedgerVersion   = "rrc25-route-state-slot-ledger/v1"
	RouteStateProjectorName       = "domeye_route_state_projector"
	RouteStateProjectorVersion    = "1.0.0"
	RouteStateMidpointSlot        = 2_160
	RouteStateFinalSlot           = 4_320
	RouteStateDefaultShardCount   = 64
	RouteStateDefaultParseWorkers = 8
)

type RouteStateStoreConfig struct {
	RouteEventRoot             string
	RawRoot                    string
	SelectionPath              string
	CompatibleMappingPath      string
	RevisedMappingPath         string
	Output                     string
	RouteEventImplementationID string
	ImplementationID           string
	Workers                    int
	CheckpointShards           int
	Resume                     bool
	Progress                   func(string)
}

func (config RouteStateStoreConfig) progress(message string) {
	if config.Progress != nil {
		config.Progress(message)
	}
}

type RouteStateStorePreflight struct {
	SchemaVersion               string `json:"schema_version"`
	RunID                       string `json:"run_id"`
	DatasetID                   string `json:"dataset_id"`
	CollectorID                 string `json:"collector_id"`
	WindowStartUTC              string `json:"window_start_utc"`
	WindowEndExclusiveUTC       string `json:"window_end_exclusive_utc"`
	StatePointCount             int    `json:"state_point_count"`
	SourceRouteEventDatasetID   string `json:"source_route_event_dataset_id"`
	SourceRouteEventContentSHA  string `json:"source_route_event_content_sha256"`
	SourceRouteEventManifestSHA string `json:"source_route_event_manifest_sha256"`
	SourceRouteEventCount       int64  `json:"source_route_event_count"`
	ImplementationID            string `json:"implementation_id"`
	ProjectorName               string `json:"projector_name"`
	ProjectorVersion            string `json:"projector_version"`
	MappingVersion              string `json:"mapping_version"`
	MappingCompatibleSHA256     string `json:"mapping_compatible_sha256"`
	MappingRevisedSHA256        string `json:"mapping_revised_sha256"`
	RouteStateKey               string `json:"route_state_key"`
	CheckpointSlots             []int  `json:"checkpoint_slots"`
	RouteEventVerificationMode  string `json:"route_event_verification_mode"`
}

type RouteStateSlotRecord struct {
	SchemaVersion             string `json:"schema_version"`
	Slot                      int    `json:"slot"`
	ArtifactIndex             int    `json:"artifact_index"`
	ArtifactID                string `json:"artifact_id"`
	ArtifactTimeUTC           string `json:"artifact_time_utc"`
	StatePointUTC             string `json:"state_point_utc"`
	AttemptedThrough          string `json:"attempted_through"`
	DataThrough               string `json:"data_through"`
	SourcePartitionContentSHA string `json:"source_partition_content_sha256"`
	SourceRouteEventFileSHA   string `json:"source_route_event_file_sha256"`
	RouteEventCount           int64  `json:"route_event_count"`
	AnnounceCount             int64  `json:"announce_count"`
	WithdrawCount             int64  `json:"withdraw_count"`
	TransitionSHA256          string `json:"transition_sha256"`
	RouteStateRecordCount     int64  `json:"route_state_record_count"`
	VisibleRouteCount         int64  `json:"visible_route_count"`
	StateDigest               string `json:"state_digest"`
	QualityStatus             string `json:"quality_status"`
	ContentSHA256             string `json:"content_sha256"`
}

type RouteStateSlotLedgerManifest struct {
	SchemaVersion       string              `json:"schema_version"`
	Status              string              `json:"status"`
	RouteStateDatasetID string              `json:"route_state_dataset_id"`
	StartSlot           int                 `json:"start_slot"`
	EndSlot             int                 `json:"end_slot"`
	SlotCount           int                 `json:"slot_count"`
	FirstStatePointUTC  string              `json:"first_state_point_utc"`
	LastStatePointUTC   string              `json:"last_state_point_utc"`
	File                RouteEventStoreFile `json:"file"`
	ContentSHA256       string              `json:"content_sha256"`
}

type RouteStateCheckpointReference struct {
	Path                  string `json:"path"`
	CheckpointID          string `json:"checkpoint_id"`
	ProcessedSlot         int    `json:"processed_slot"`
	DataThrough           string `json:"data_through"`
	RouteStateRecordCount int64  `json:"route_state_record_count"`
	VisibleRouteCount     int64  `json:"visible_route_count"`
	StateDigest           string `json:"state_digest"`
	ContentSHA256         string `json:"content_sha256"`
}

type RouteStateStoreManifest struct {
	SchemaVersion               string                          `json:"schema_version"`
	Status                      string                          `json:"status"`
	RunID                       string                          `json:"run_id"`
	DatasetID                   string                          `json:"dataset_id"`
	CollectorID                 string                          `json:"collector_id"`
	WindowStartUTC              string                          `json:"window_start_utc"`
	WindowEndExclusiveUTC       string                          `json:"window_end_exclusive_utc"`
	StatePointCount             int                             `json:"state_point_count"`
	SourceRouteEventDatasetID   string                          `json:"source_route_event_dataset_id"`
	SourceRouteEventContentSHA  string                          `json:"source_route_event_content_sha256"`
	SourceRouteEventManifestSHA string                          `json:"source_route_event_manifest_sha256"`
	SourceRouteEventCount       int64                           `json:"source_route_event_count"`
	ImplementationID            string                          `json:"implementation_id"`
	ProjectorName               string                          `json:"projector_name"`
	ProjectorVersion            string                          `json:"projector_version"`
	MappingVersion              string                          `json:"mapping_version"`
	MappingCompatibleSHA256     string                          `json:"mapping_compatible_sha256"`
	MappingRevisedSHA256        string                          `json:"mapping_revised_sha256"`
	RouteStateKey               string                          `json:"route_state_key"`
	CheckpointSlots             []int                           `json:"checkpoint_slots"`
	Checkpoints                 []RouteStateCheckpointReference `json:"checkpoints"`
	SlotLedgers                 []RouteStateSlotLedgerManifest  `json:"slot_ledgers"`
	ProcessedUpdateCount        int                             `json:"processed_update_count"`
	ProcessedRouteEventCount    int64                           `json:"processed_route_event_count"`
	AttemptedThrough            string                          `json:"attempted_through"`
	DataThrough                 string                          `json:"data_through"`
	RouteStateRecordCount       int64                           `json:"route_state_record_count"`
	VisibleRouteCount           int64                           `json:"visible_route_count"`
	WithdrawnRouteCount         int64                           `json:"withdrawn_route_count"`
	FinalStateDigest            string                          `json:"final_state_digest"`
	ContentSHA256               string                          `json:"content_sha256"`
}

type routeStateStoreMarker struct {
	SchemaVersion    string `json:"schema_version"`
	RunID            string `json:"run_id"`
	DatasetID        string `json:"dataset_id"`
	ImplementationID string `json:"implementation_id"`
	SourceDatasetID  string `json:"source_route_event_dataset_id"`
	SourceContentSHA string `json:"source_route_event_content_sha256"`
	MappingVersion   string `json:"mapping_version"`
}

type parsedRouteStatePartition struct {
	Index            int
	Events           []routeStateEvent
	RouteEventCount  int64
	AnnounceCount    int64
	WithdrawCount    int64
	RIBSnapshotCount int64
	TransitionSHA256 string
}

type RouteStatePartitionPilotResult struct {
	SchemaVersion       string `json:"schema_version"`
	Status              string `json:"status"`
	PartitionIndex      int    `json:"partition_index"`
	ArtifactID          string `json:"artifact_id"`
	RouteEventCount     int64  `json:"route_event_count"`
	AnnounceCount       int64  `json:"announce_count"`
	WithdrawCount       int64  `json:"withdraw_count"`
	RIBSnapshotCount    int64  `json:"rib_snapshot_count"`
	TransitionSHA256    string `json:"transition_sha256"`
	ElapsedMilliseconds int64  `json:"elapsed_milliseconds"`
}

type RouteStateReplayAuditResult struct {
	SchemaVersion                string `json:"schema_version"`
	Status                       string `json:"status"`
	RouteStateDatasetID          string `json:"route_state_dataset_id"`
	ImplementationID             string `json:"implementation_id"`
	SourceRouteEventDatasetID    string `json:"source_route_event_dataset_id"`
	SourceCheckpointID           string `json:"source_checkpoint_id"`
	SourceCheckpointSlot         int    `json:"source_checkpoint_slot"`
	ComparedSlotCount            int    `json:"compared_slot_count"`
	FinalStateDigest             string `json:"final_state_digest"`
	FinalRouteStateRecordCount   int64  `json:"final_route_state_record_count"`
	FinalVisibleRouteCount       int64  `json:"final_visible_route_count"`
	FormalFinalCheckpointID      string `json:"formal_final_checkpoint_id"`
	ReplayedFinalCheckpointID    string `json:"replayed_final_checkpoint_id"`
	CheckpointManifestByteEqual  bool   `json:"checkpoint_manifest_byte_equal"`
	CheckpointShardIdentityEqual bool   `json:"checkpoint_shard_identity_equal"`
	ContentSHA256                string `json:"content_sha256"`
}

func routeStateIdentity(
	routeEventManifest RouteEventStoreManifest,
	mapping *GlobalCountryMapping,
	implementationID string,
) (string, string) {
	runID := stableID("route_state_run_v1_", map[string]any{
		"source_route_event_dataset_id":     routeEventManifest.DatasetID,
		"source_route_event_content_sha256": routeEventManifest.ContentSHA256,
		"mapping_version":                   mapping.MappingVersion,
		"implementation_id":                 implementationID,
		"projector_name":                    RouteStateProjectorName,
		"projector_version":                 RouteStateProjectorVersion,
	}, 32)
	datasetID := stableID("route_state_dataset_v1_", map[string]any{
		"run_id": runID, "collector_id": "rrc25",
	}, 32)
	return runID, datasetID
}

func quickRouteEventSource(
	config RouteStateStoreConfig,
) (RouteEventStoreManifest, string, error) {
	manifest, err := readIdenticalRouteEventStoreManifests(config.RouteEventRoot)
	if err != nil {
		return manifest, "", err
	}
	manifestSHA, _, err := sha256File(filepath.Join(config.RouteEventRoot, "manifest.json"))
	if err != nil {
		return manifest, "", err
	}
	if manifest.SchemaVersion != RouteEventStoreVersion || manifest.Status != "complete" ||
		manifest.CollectorID != "rrc25" || manifest.WindowStartUTC != RouteEventWindowStartUTC ||
		manifest.WindowEndExclusive != RouteEventWindowEndUTC ||
		manifest.ImplementationID != config.RouteEventImplementationID ||
		manifest.ArtifactCount != RouteEventUpdateCount+1 || len(manifest.Partitions) != RouteEventUpdateCount+1 ||
		manifest.ContentSHA256 != routeEventStoreContentSHA(manifest) ||
		manifest.Announces+manifest.Withdraws+manifest.RIBSnapshots != manifest.RouteEvents {
		return manifest, "", fmt.Errorf("RouteState source RouteEvent identity mismatch")
	}
	for index, partition := range manifest.Partitions {
		if partition.ArtifactIndex != index || partition.DatasetID != manifest.DatasetID ||
			partition.ImportRunID != manifest.ImportRunID || partition.ParserWarnings != 0 ||
			partition.InputIntegrityStatus != RouteEventInputIntegrity {
			return manifest, "", fmt.Errorf("RouteState source partition %d is not complete", index)
		}
	}
	return manifest, manifestSHA, nil
}

func PilotRouteStatePartition(
	config RouteStateStoreConfig,
	partitionIndex int,
) (RouteStatePartitionPilotResult, error) {
	manifest, _, err := quickRouteEventSource(config)
	if err != nil {
		return RouteStatePartitionPilotResult{}, err
	}
	if partitionIndex < 0 || partitionIndex >= len(manifest.Partitions) {
		return RouteStatePartitionPilotResult{}, fmt.Errorf("RouteState pilot partition index is out of range")
	}
	started := time.Now()
	parsed, err := parseRouteStatePartition(config.RouteEventRoot, manifest.Partitions[partitionIndex])
	if err != nil {
		return RouteStatePartitionPilotResult{}, err
	}
	return RouteStatePartitionPilotResult{
		SchemaVersion: "rrc25-route-state-partition-pilot/v1", Status: "complete",
		PartitionIndex:  partitionIndex,
		ArtifactID:      manifest.Partitions[partitionIndex].Artifact.ArtifactID,
		RouteEventCount: parsed.RouteEventCount, AnnounceCount: parsed.AnnounceCount,
		WithdrawCount: parsed.WithdrawCount, RIBSnapshotCount: parsed.RIBSnapshotCount,
		TransitionSHA256:    parsed.TransitionSHA256,
		ElapsedMilliseconds: time.Since(started).Milliseconds(),
	}, nil
}

func PreflightRouteStateStore(
	config RouteStateStoreConfig,
) (RouteStateStorePreflight, error) {
	if config.RouteEventRoot == "" || config.CompatibleMappingPath == "" ||
		config.RevisedMappingPath == "" || config.ImplementationID == "" ||
		config.RouteEventImplementationID == "" {
		return RouteStateStorePreflight{}, fmt.Errorf("RouteState preflight paths and identities are required")
	}
	if err := validateRouteEventImplementationID(config.ImplementationID); err != nil {
		return RouteStateStorePreflight{}, err
	}
	if err := validateRouteEventImplementationID(config.RouteEventImplementationID); err != nil {
		return RouteStateStorePreflight{}, fmt.Errorf("source RouteEvent %w", err)
	}
	routeEvents, manifestSHA, err := quickRouteEventSource(config)
	if err != nil {
		return RouteStateStorePreflight{}, err
	}
	mapping, err := LoadGlobalCountryMapping(
		config.CompatibleMappingPath, config.RevisedMappingPath,
	)
	if err != nil {
		return RouteStateStorePreflight{}, err
	}
	runID, datasetID := routeStateIdentity(routeEvents, mapping, config.ImplementationID)
	return RouteStateStorePreflight{
		SchemaVersion: RouteStateStoreVersion, RunID: runID, DatasetID: datasetID,
		CollectorID: "rrc25", WindowStartUTC: RouteEventWindowStartUTC,
		WindowEndExclusiveUTC: RouteEventWindowEndUTC, StatePointCount: RouteStateFinalSlot,
		SourceRouteEventDatasetID:   routeEvents.DatasetID,
		SourceRouteEventContentSHA:  routeEvents.ContentSHA256,
		SourceRouteEventManifestSHA: manifestSHA, SourceRouteEventCount: routeEvents.RouteEvents,
		ImplementationID: config.ImplementationID, ProjectorName: RouteStateProjectorName,
		ProjectorVersion: RouteStateProjectorVersion, MappingVersion: mapping.MappingVersion,
		MappingCompatibleSHA256:    mapping.CompatibleSHA256,
		MappingRevisedSHA256:       mapping.RevisedSHA256,
		RouteStateKey:              "collector + VP/peer + prefix + address_family",
		CheckpointSlots:            []int{0, RouteStateMidpointSlot, RouteStateFinalSlot},
		RouteEventVerificationMode: "complete_twin_manifest_and_all_partition_files_rehashed_before_projection",
	}, nil
}

func verifyRouteEventSource(
	config RouteStateStoreConfig,
) (RouteEventStoreManifest, string, error) {
	if config.RawRoot == "" || config.SelectionPath == "" {
		return RouteEventStoreManifest{}, "", fmt.Errorf("raw root and selection are required for formal RouteState projection")
	}
	manifest, err := RunRouteEventStore(RouteEventStoreConfig{
		RawRoot: config.RawRoot, SelectionPath: config.SelectionPath,
		Output: config.RouteEventRoot, ImplementationID: config.RouteEventImplementationID,
		Workers: 1, Resume: true,
	})
	if err != nil {
		return manifest, "", err
	}
	manifestSHA, _, err := sha256File(filepath.Join(config.RouteEventRoot, "manifest.json"))
	return manifest, manifestSHA, err
}

func routeStateSlotContentSHA(record RouteStateSlotRecord) string {
	record.ContentSHA256 = ""
	raw, _ := json.Marshal(record)
	digest := sha256.Sum256(raw)
	return hex.EncodeToString(digest[:])
}

func parseRouteStatePartition(
	root string,
	partition RouteEventPartitionManifest,
) (parsedRouteStatePartition, error) {
	return parseRouteStatePartitionWithConsumer(root, partition, true, nil)
}

func parseRouteStatePartitionWithConsumer(
	root string,
	partition RouteEventPartitionManifest,
	collectEvents bool,
	consume func(routeStateEvent) error,
) (parsedRouteStatePartition, error) {
	result := parsedRouteStatePartition{Index: partition.ArtifactIndex}
	file, err := os.Open(filepath.Join(root, filepath.FromSlash(partition.Events.Path)))
	if err != nil {
		return result, err
	}
	defer file.Close()
	decoded, err := gzip.NewReader(file)
	if err != nil {
		return result, err
	}
	defer decoded.Close()
	scanner := bufio.NewScanner(decoded)
	scanner.Buffer(make([]byte, 64<<10), 4<<20)
	if partition.RouteEvents > int64(^uint(0)>>1) {
		return result, fmt.Errorf("RouteEvent partition is too large")
	}
	if collectEvents {
		result.Events = make([]routeStateEvent, 0, int(partition.RouteEvents))
	}
	transition := sha256.New()
	first := true
	var lastRecord uint32
	var lastElement uint32
	artifactStart, err := time.Parse(time.RFC3339, partition.Artifact.ArtifactTimeUTC)
	if err != nil {
		return result, err
	}
	startMicros := artifactStart.UnixMicro()
	endMicros := artifactStart.Add(5 * time.Minute).UnixMicro()
	for scanner.Scan() {
		var row routeEventRow
		if err := json.Unmarshal(scanner.Bytes(), &row); err != nil {
			return result, err
		}
		if !first && (row.RecordOrdinal < lastRecord ||
			(row.RecordOrdinal == lastRecord && row.ElementOrdinal <= lastElement)) {
			return result, fmt.Errorf("RouteEvent partition %d is not strictly ordered", partition.ArtifactIndex)
		}
		first = false
		lastRecord, lastElement = row.RecordOrdinal, row.ElementOrdinal
		event, err := routeStateEventFromRow(row, partition.Artifact, partition.ArtifactIndex)
		if err != nil {
			return result, err
		}
		if partition.Role == "rib" {
			if event.Action != actionRIBSnapshot {
				return result, fmt.Errorf("Seed RIB partition contains non-snapshot event")
			}
			result.RIBSnapshotCount++
		} else {
			if event.Action == actionRIBSnapshot || event.EventMicros < startMicros || event.EventMicros >= endMicros {
				return result, fmt.Errorf("UPDATE partition %d event is outside its ordered slot", partition.ArtifactIndex)
			}
			if event.Action == actionAnnounce {
				result.AnnounceCount++
			} else {
				result.WithdrawCount++
			}
		}
		_, _ = transition.Write(event.RouteEventID[:])
		var position [9]byte
		position[0] = event.Action
		binary.BigEndian.PutUint32(position[1:5], event.RecordOrdinal)
		binary.BigEndian.PutUint32(position[5:9], event.ElementOrdinal)
		_, _ = transition.Write(position[:])
		if consume != nil {
			if err := consume(event); err != nil {
				return result, err
			}
		}
		if collectEvents {
			result.Events = append(result.Events, event)
		}
		result.RouteEventCount++
	}
	if err := scanner.Err(); err != nil {
		return result, err
	}
	if err := decoded.Close(); err != nil {
		return result, err
	}
	if result.RouteEventCount != partition.RouteEvents ||
		result.AnnounceCount != partition.Announces || result.WithdrawCount != partition.Withdraws ||
		result.RIBSnapshotCount != partition.RIBSnapshots {
		return result, fmt.Errorf("RouteEvent partition %d population mismatch", partition.ArtifactIndex)
	}
	result.TransitionSHA256 = hex.EncodeToString(transition.Sum(nil))
	return result, nil
}

func projectRouteStateSeed(
	root string,
	partition RouteEventPartitionManifest,
	state *RouteState,
) (parsedRouteStatePartition, error) {
	if partition.ArtifactIndex != 0 || partition.Role != "rib" {
		return parsedRouteStatePartition{}, fmt.Errorf("RouteState Seed must be partition 0000 RIB")
	}
	return parseRouteStatePartitionWithConsumer(
		root, partition, false, state.Apply,
	)
}

func applyParsedRouteStatePartition(
	state *RouteState,
	partition RouteEventPartitionManifest,
	parsed parsedRouteStatePartition,
) (RouteStateSlotRecord, error) {
	if parsed.Index != partition.ArtifactIndex {
		return RouteStateSlotRecord{}, fmt.Errorf("RouteState partition order mismatch")
	}
	for _, event := range parsed.Events {
		if err := state.Apply(event); err != nil {
			return RouteStateSlotRecord{}, err
		}
	}
	parsed.Events = nil
	statePoint, err := time.Parse(time.RFC3339, partition.Artifact.ArtifactTimeUTC)
	if err != nil {
		return RouteStateSlotRecord{}, err
	}
	statePoint = statePoint.Add(5 * time.Minute)
	record := RouteStateSlotRecord{
		SchemaVersion: RouteStateSlotLedgerVersion,
		Slot:          partition.ArtifactIndex, ArtifactIndex: partition.ArtifactIndex,
		ArtifactID: partition.Artifact.ArtifactID, ArtifactTimeUTC: partition.Artifact.ArtifactTimeUTC,
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

func processRouteStateUpdates(
	config RouteStateStoreConfig,
	manifest RouteEventStoreManifest,
	state *RouteState,
	startSlot int,
	endSlot int,
) ([]RouteStateSlotRecord, error) {
	if startSlot < 1 || endSlot > RouteStateFinalSlot || startSlot > endSlot {
		return nil, fmt.Errorf("invalid RouteState slot range")
	}
	workers := config.Workers
	if workers < 1 {
		workers = RouteStateDefaultParseWorkers
	}
	if workers > endSlot-startSlot+1 {
		workers = endSlot - startSlot + 1
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
				parsed, err := parseRouteStatePartition(config.RouteEventRoot, manifest.Partitions[index])
				results <- result{value: parsed, err: err}
			}
		}()
	}
	nextSchedule := startSlot
	inflight := 0
	for inflight < workers && nextSchedule <= endSlot {
		jobs <- nextSchedule
		nextSchedule++
		inflight++
	}
	pending := make(map[int]parsedRouteStatePartition, workers)
	ledger := make([]RouteStateSlotRecord, 0, endSlot-startSlot+1)
	nextApply := startSlot
	for nextApply <= endSlot {
		item := <-results
		inflight--
		if item.err != nil {
			close(jobs)
			group.Wait()
			return nil, item.err
		}
		pending[item.value.Index] = item.value
		for {
			parsed, exists := pending[nextApply]
			if !exists {
				break
			}
			delete(pending, nextApply)
			record, err := applyParsedRouteStatePartition(
				state, manifest.Partitions[nextApply], parsed,
			)
			if err != nil {
				close(jobs)
				group.Wait()
				return nil, err
			}
			ledger = append(ledger, record)
			config.progress(fmt.Sprintf(
				"RouteState 槽已闭合 %d/%d state_records=%d visible=%d",
				nextApply, RouteStateFinalSlot, len(state.Routes), state.VisibleRouteCount,
			))
			nextApply++
			if nextSchedule <= endSlot {
				jobs <- nextSchedule
				nextSchedule++
				inflight++
			}
		}
	}
	close(jobs)
	group.Wait()
	if inflight != 0 || len(pending) != 0 || len(ledger) != endSlot-startSlot+1 {
		return nil, fmt.Errorf("RouteState update pipeline did not close")
	}
	return ledger, nil
}

func routeStateSlotLedgerContentSHA(manifest RouteStateSlotLedgerManifest) string {
	manifest.ContentSHA256 = ""
	raw, _ := json.Marshal(manifest)
	digest := sha256.Sum256(raw)
	return hex.EncodeToString(digest[:])
}

func validateRouteStateSlotRecord(record RouteStateSlotRecord) error {
	if record.Slot < 1 || record.Slot > RouteStateFinalSlot ||
		record.SchemaVersion != RouteStateSlotLedgerVersion ||
		record.ArtifactIndex != record.Slot || record.ArtifactID == "" ||
		record.SourcePartitionContentSHA == "" || record.SourceRouteEventFileSHA == "" ||
		record.TransitionSHA256 == "" || record.StateDigest == "" ||
		record.QualityStatus != "complete" ||
		record.RouteEventCount < 0 || record.AnnounceCount < 0 || record.WithdrawCount < 0 ||
		record.RouteEventCount != record.AnnounceCount+record.WithdrawCount ||
		record.RouteStateRecordCount < 0 || record.VisibleRouteCount < 0 ||
		record.VisibleRouteCount > record.RouteStateRecordCount {
		return fmt.Errorf("invalid RouteState slot record")
	}
	artifactTime := time.Date(2026, 2, 24, 0, 0, 0, 0, time.UTC).Add(
		time.Duration(record.Slot-1) * 5 * time.Minute,
	)
	statePoint := artifactTime.Add(5 * time.Minute).Format(time.RFC3339)
	if record.ArtifactTimeUTC != artifactTime.Format(time.RFC3339) ||
		record.StatePointUTC != statePoint || record.AttemptedThrough != statePoint ||
		record.DataThrough != statePoint || record.ContentSHA256 != routeStateSlotContentSHA(record) {
		return fmt.Errorf("RouteState slot time or content mismatch")
	}
	return nil
}

func WriteRouteStateSlotLedger(
	output string,
	datasetID string,
	records []RouteStateSlotRecord,
) (RouteStateSlotLedgerManifest, error) {
	if len(records) == 0 {
		return RouteStateSlotLedgerManifest{}, fmt.Errorf("RouteState slot ledger cannot be empty")
	}
	start, end := records[0].Slot, records[len(records)-1].Slot
	if end-start+1 != len(records) {
		return RouteStateSlotLedgerManifest{}, fmt.Errorf("RouteState slot ledger is not continuous")
	}
	directory := filepath.Join(output, "slot-ledgers")
	if err := os.MkdirAll(directory, 0o750); err != nil {
		return RouteStateSlotLedgerManifest{}, err
	}
	relative := filepath.ToSlash(filepath.Join(
		"slot-ledgers", fmt.Sprintf("slots-%04d-%04d.jsonl.gz", start, end),
	))
	finalPath := filepath.Join(output, filepath.FromSlash(relative))
	temporary := finalPath + ".tmp"
	if err := removeRegularTemp(temporary); err != nil {
		return RouteStateSlotLedgerManifest{}, err
	}
	writer, err := newDeterministicJSONLGzipWriter(temporary)
	if err != nil {
		return RouteStateSlotLedgerManifest{}, err
	}
	for index, record := range records {
		if record.Slot != start+index || validateRouteStateSlotRecord(record) != nil {
			writer.Abort()
			return RouteStateSlotLedgerManifest{}, fmt.Errorf("invalid RouteState slot ledger record")
		}
		if err := writer.Write(record); err != nil {
			writer.Abort()
			return RouteStateSlotLedgerManifest{}, err
		}
	}
	meta, err := writer.Close(relative)
	if err != nil {
		return RouteStateSlotLedgerManifest{}, err
	}
	if existingSHA, existingSize, err := sha256File(finalPath); err == nil {
		if existingSHA != meta.SHA256 || existingSize != meta.SizeBytes {
			return RouteStateSlotLedgerManifest{}, fmt.Errorf("immutable RouteState slot ledger mismatch")
		}
		if err := os.Remove(temporary); err != nil {
			return RouteStateSlotLedgerManifest{}, err
		}
	} else if !os.IsNotExist(err) {
		return RouteStateSlotLedgerManifest{}, err
	} else if err := os.Rename(temporary, finalPath); err != nil {
		return RouteStateSlotLedgerManifest{}, err
	}
	manifest := RouteStateSlotLedgerManifest{
		SchemaVersion: RouteStateSlotLedgerVersion, Status: "complete",
		RouteStateDatasetID: datasetID, StartSlot: start, EndSlot: end,
		SlotCount: len(records), FirstStatePointUTC: records[0].StatePointUTC,
		LastStatePointUTC: records[len(records)-1].StatePointUTC, File: meta,
	}
	manifest.ContentSHA256 = routeStateSlotLedgerContentSHA(manifest)
	manifestPath := filepath.Join(directory, fmt.Sprintf("slots-%04d-%04d.manifest.json", start, end))
	if _, err := writeJSONImmutable(manifestPath, manifest); err != nil {
		return RouteStateSlotLedgerManifest{}, err
	}
	return manifest, nil
}

func LoadRouteStateSlotLedger(
	output string,
	datasetID string,
	startSlot int,
	endSlot int,
) ([]RouteStateSlotRecord, RouteStateSlotLedgerManifest, error) {
	var manifest RouteStateSlotLedgerManifest
	manifestPath := filepath.Join(
		output, "slot-ledgers", fmt.Sprintf("slots-%04d-%04d.manifest.json", startSlot, endSlot),
	)
	if _, err := readJSON(manifestPath, &manifest); err != nil {
		return nil, manifest, err
	}
	if manifest.SchemaVersion != RouteStateSlotLedgerVersion || manifest.Status != "complete" ||
		manifest.RouteStateDatasetID != datasetID || manifest.StartSlot != startSlot ||
		manifest.EndSlot != endSlot || manifest.SlotCount != endSlot-startSlot+1 ||
		manifest.ContentSHA256 != routeStateSlotLedgerContentSHA(manifest) {
		return nil, manifest, fmt.Errorf("RouteState slot ledger identity mismatch")
	}
	path := filepath.Join(output, filepath.FromSlash(manifest.File.Path))
	sha, size, err := sha256File(path)
	if err != nil {
		return nil, manifest, err
	}
	if sha != manifest.File.SHA256 || size != manifest.File.SizeBytes {
		return nil, manifest, fmt.Errorf("RouteState slot ledger file identity mismatch")
	}
	file, err := os.Open(path)
	if err != nil {
		return nil, manifest, err
	}
	defer file.Close()
	decoded, err := gzip.NewReader(file)
	if err != nil {
		return nil, manifest, err
	}
	defer decoded.Close()
	scanner := bufio.NewScanner(decoded)
	scanner.Buffer(make([]byte, 64<<10), 1<<20)
	records := make([]RouteStateSlotRecord, 0, manifest.SlotCount)
	for scanner.Scan() {
		var record RouteStateSlotRecord
		if err := json.Unmarshal(scanner.Bytes(), &record); err != nil {
			return nil, manifest, err
		}
		if err := validateRouteStateSlotRecord(record); err != nil {
			return nil, manifest, err
		}
		records = append(records, record)
	}
	if err := scanner.Err(); err != nil {
		return nil, manifest, err
	}
	if len(records) != manifest.SlotCount || records[0].Slot != startSlot ||
		records[len(records)-1].Slot != endSlot ||
		records[0].StatePointUTC != manifest.FirstStatePointUTC ||
		records[len(records)-1].StatePointUTC != manifest.LastStatePointUTC {
		return nil, manifest, fmt.Errorf("RouteState slot ledger population mismatch")
	}
	for index, record := range records {
		if record.Slot != startSlot+index {
			return nil, manifest, fmt.Errorf("RouteState slot ledger sequence mismatch")
		}
	}
	return records, manifest, nil
}

func prepareRouteStateOutput(
	config RouteStateStoreConfig,
	preflight RouteStateStorePreflight,
) (*routeEventStoreLock, error) {
	marker := routeStateStoreMarker{
		SchemaVersion: RouteStateStoreVersion, RunID: preflight.RunID, DatasetID: preflight.DatasetID,
		ImplementationID: config.ImplementationID,
		SourceDatasetID:  preflight.SourceRouteEventDatasetID,
		SourceContentSHA: preflight.SourceRouteEventContentSHA,
		MappingVersion:   preflight.MappingVersion,
	}
	created := false
	if info, err := os.Lstat(config.Output); err == nil {
		if !config.Resume || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return nil, fmt.Errorf("RouteState output exists; use --resume for the same run")
		}
	} else if !os.IsNotExist(err) {
		return nil, err
	} else {
		if config.Resume {
			return nil, fmt.Errorf("cannot resume absent RouteState output")
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
	} else {
		var existing routeStateStoreMarker
		if _, err := readJSON(filepath.Join(config.Output, "RUNNING.json"), &existing); err != nil {
			_ = lock.Close()
			return nil, err
		}
		if existing != marker {
			_ = lock.Close()
			return nil, fmt.Errorf("RouteState RUNNING identity mismatch")
		}
	}
	return lock, nil
}

func checkpointReference(path string, manifest RouteStateCheckpointManifest) RouteStateCheckpointReference {
	return RouteStateCheckpointReference{
		Path: path, CheckpointID: manifest.CheckpointID, ProcessedSlot: manifest.ProcessedSlot,
		DataThrough: manifest.DataThrough, RouteStateRecordCount: manifest.RouteStateRecordCount,
		VisibleRouteCount: manifest.VisibleRouteCount, StateDigest: manifest.StateDigest,
		ContentSHA256: manifest.ContentSHA256,
	}
}

func routeStateStoreContentSHA(manifest RouteStateStoreManifest) string {
	manifest.ContentSHA256 = ""
	raw, _ := json.Marshal(manifest)
	digest := sha256.Sum256(raw)
	return hex.EncodeToString(digest[:])
}

func RunRouteStateStore(config RouteStateStoreConfig) (RouteStateStoreManifest, error) {
	if config.Output == "" {
		return RouteStateStoreManifest{}, fmt.Errorf("RouteState output is required")
	}
	if config.Workers < 1 {
		config.Workers = RouteStateDefaultParseWorkers
	}
	if config.CheckpointShards < 1 {
		config.CheckpointShards = RouteStateDefaultShardCount
	}
	preflight, err := PreflightRouteStateStore(config)
	if err != nil {
		return RouteStateStoreManifest{}, err
	}
	verifiedEvents, verifiedManifestSHA, err := verifyRouteEventSource(config)
	if err != nil {
		return RouteStateStoreManifest{}, err
	}
	if verifiedEvents.DatasetID != preflight.SourceRouteEventDatasetID ||
		verifiedEvents.ContentSHA256 != preflight.SourceRouteEventContentSHA ||
		verifiedManifestSHA != preflight.SourceRouteEventManifestSHA {
		return RouteStateStoreManifest{}, fmt.Errorf("RouteEvent identity changed after RouteState preflight")
	}
	mapping, err := LoadGlobalCountryMapping(config.CompatibleMappingPath, config.RevisedMappingPath)
	if err != nil {
		return RouteStateStoreManifest{}, err
	}
	identity := RouteStateCheckpointIdentity{
		RouteStateDatasetID:        preflight.DatasetID,
		SourceRouteEventDatasetID:  verifiedEvents.DatasetID,
		SourceRouteEventContentSHA: verifiedEvents.ContentSHA256,
		ImplementationID:           config.ImplementationID, ProjectorName: RouteStateProjectorName,
		ProjectorVersion: RouteStateProjectorVersion, MappingVersion: mapping.MappingVersion,
		MappingCompatibleSHA256: mapping.CompatibleSHA256, MappingRevisedSHA256: mapping.RevisedSHA256,
		WindowStartUTC: RouteEventWindowStartUTC, WindowEndExclusiveUTC: RouteEventWindowEndUTC,
	}
	lock, err := prepareRouteStateOutput(config, preflight)
	if err != nil {
		return RouteStateStoreManifest{}, err
	}
	defer lock.Close()
	if config.Resume {
		if _, err := os.Lstat(filepath.Join(config.Output, "COMPLETE.json")); err == nil {
			return loadCompleteRouteStateStore(config.Output, identity, preflight)
		} else if !os.IsNotExist(err) {
			return RouteStateStoreManifest{}, err
		}
	}
	checkpointPath := func(slot int) string {
		return filepath.Join(config.Output, "checkpoints", fmt.Sprintf("slot-%04d", slot))
	}
	if err := os.MkdirAll(filepath.Join(config.Output, "checkpoints"), 0o750); err != nil {
		return RouteStateStoreManifest{}, err
	}
	var state *RouteState
	var seedCheckpoint RouteStateCheckpointManifest
	var midpointCheckpoint RouteStateCheckpointManifest
	var finalCheckpoint RouteStateCheckpointManifest
	var firstLedger RouteStateSlotLedgerManifest
	var secondLedger RouteStateSlotLedgerManifest
	startSlot := 1
	if _, err := os.Lstat(checkpointPath(RouteStateFinalSlot)); err == nil {
		state, finalCheckpoint, err = LoadRouteStateCheckpoint(
			checkpointPath(RouteStateFinalSlot), identity,
		)
		if err != nil {
			return RouteStateStoreManifest{}, err
		}
		startSlot = RouteStateFinalSlot + 1
	} else if _, err := os.Lstat(checkpointPath(RouteStateMidpointSlot)); err == nil {
		state, midpointCheckpoint, err = LoadRouteStateCheckpoint(
			checkpointPath(RouteStateMidpointSlot), identity,
		)
		if err != nil {
			return RouteStateStoreManifest{}, err
		}
		_, firstLedger, err = LoadRouteStateSlotLedger(
			config.Output, preflight.DatasetID, 1, RouteStateMidpointSlot,
		)
		if err != nil {
			return RouteStateStoreManifest{}, err
		}
		startSlot = RouteStateMidpointSlot + 1
	} else if _, err := os.Lstat(checkpointPath(0)); err == nil {
		state, seedCheckpoint, err = LoadRouteStateCheckpoint(checkpointPath(0), identity)
		if err != nil {
			return RouteStateStoreManifest{}, err
		}
		startSlot = 1
	} else {
		state, err = NewRouteState(int(verifiedEvents.RIBSnapshots))
		if err != nil {
			return RouteStateStoreManifest{}, err
		}
		config.progress("从完整 RouteEvent partition 0000 建立唯一 RouteState Seed")
		seed, err := projectRouteStateSeed(
			config.RouteEventRoot, verifiedEvents.Partitions[0], state,
		)
		if err != nil {
			return RouteStateStoreManifest{}, err
		}
		if len(seed.Events) != 0 || seed.RouteEventCount != verifiedEvents.RIBSnapshots {
			return RouteStateStoreManifest{}, fmt.Errorf("RouteState Seed streaming population mismatch")
		}
		seedCheckpoint, err = WriteRouteStateCheckpoint(
			checkpointPath(0), state, identity, RouteEventWindowStartUTC, 0, config.CheckpointShards,
		)
		if err != nil {
			return RouteStateStoreManifest{}, err
		}
	}
	if seedCheckpoint.CheckpointID == "" {
		_, seedCheckpoint, err = LoadRouteStateCheckpoint(checkpointPath(0), identity)
		if err != nil {
			return RouteStateStoreManifest{}, err
		}
	}
	if startSlot <= RouteStateMidpointSlot {
		ledger, err := processRouteStateUpdates(
			config, verifiedEvents, state, startSlot, RouteStateMidpointSlot,
		)
		if err != nil {
			return RouteStateStoreManifest{}, err
		}
		firstLedger, err = WriteRouteStateSlotLedger(config.Output, preflight.DatasetID, ledger)
		if err != nil {
			return RouteStateStoreManifest{}, err
		}
		midpointCheckpoint, err = WriteRouteStateCheckpoint(
			checkpointPath(RouteStateMidpointSlot), state, identity,
			ledger[len(ledger)-1].DataThrough, RouteStateMidpointSlot, config.CheckpointShards,
		)
		if err != nil {
			return RouteStateStoreManifest{}, err
		}
		startSlot = RouteStateMidpointSlot + 1
	}
	if midpointCheckpoint.CheckpointID == "" {
		_, midpointCheckpoint, err = LoadRouteStateCheckpoint(
			checkpointPath(RouteStateMidpointSlot), identity,
		)
		if err != nil {
			return RouteStateStoreManifest{}, err
		}
		if firstLedger.ContentSHA256 == "" {
			_, firstLedger, err = LoadRouteStateSlotLedger(
				config.Output, preflight.DatasetID, 1, RouteStateMidpointSlot,
			)
			if err != nil {
				return RouteStateStoreManifest{}, err
			}
		}
	}
	if startSlot <= RouteStateFinalSlot {
		ledger, err := processRouteStateUpdates(
			config, verifiedEvents, state, startSlot, RouteStateFinalSlot,
		)
		if err != nil {
			return RouteStateStoreManifest{}, err
		}
		secondLedger, err = WriteRouteStateSlotLedger(config.Output, preflight.DatasetID, ledger)
		if err != nil {
			return RouteStateStoreManifest{}, err
		}
		finalCheckpoint, err = WriteRouteStateCheckpoint(
			checkpointPath(RouteStateFinalSlot), state, identity,
			ledger[len(ledger)-1].DataThrough, RouteStateFinalSlot, config.CheckpointShards,
		)
		if err != nil {
			return RouteStateStoreManifest{}, err
		}
	}
	if secondLedger.ContentSHA256 == "" {
		_, secondLedger, err = LoadRouteStateSlotLedger(
			config.Output, preflight.DatasetID, RouteStateMidpointSlot+1, RouteStateFinalSlot,
		)
		if err != nil {
			return RouteStateStoreManifest{}, err
		}
	}
	manifest := RouteStateStoreManifest{
		SchemaVersion: RouteStateStoreVersion, Status: "complete",
		RunID: preflight.RunID, DatasetID: preflight.DatasetID, CollectorID: "rrc25",
		WindowStartUTC: RouteEventWindowStartUTC, WindowEndExclusiveUTC: RouteEventWindowEndUTC,
		StatePointCount:             RouteStateFinalSlot,
		SourceRouteEventDatasetID:   verifiedEvents.DatasetID,
		SourceRouteEventContentSHA:  verifiedEvents.ContentSHA256,
		SourceRouteEventManifestSHA: verifiedManifestSHA, SourceRouteEventCount: verifiedEvents.RouteEvents,
		ImplementationID: config.ImplementationID, ProjectorName: RouteStateProjectorName,
		ProjectorVersion: RouteStateProjectorVersion, MappingVersion: mapping.MappingVersion,
		MappingCompatibleSHA256: mapping.CompatibleSHA256, MappingRevisedSHA256: mapping.RevisedSHA256,
		RouteStateKey:   "collector + VP/peer + prefix + address_family",
		CheckpointSlots: []int{0, RouteStateMidpointSlot, RouteStateFinalSlot},
		Checkpoints: []RouteStateCheckpointReference{
			checkpointReference("checkpoints/slot-0000", seedCheckpoint),
			checkpointReference("checkpoints/slot-2160", midpointCheckpoint),
			checkpointReference("checkpoints/slot-4320", finalCheckpoint),
		},
		SlotLedgers:              []RouteStateSlotLedgerManifest{firstLedger, secondLedger},
		ProcessedUpdateCount:     RouteStateFinalSlot,
		ProcessedRouteEventCount: finalCheckpoint.ProcessedRouteEventCount,
		AttemptedThrough:         RouteEventWindowEndUTC, DataThrough: RouteEventWindowEndUTC,
		RouteStateRecordCount: finalCheckpoint.RouteStateRecordCount,
		VisibleRouteCount:     finalCheckpoint.VisibleRouteCount,
		WithdrawnRouteCount:   finalCheckpoint.WithdrawnRouteCount,
		FinalStateDigest:      finalCheckpoint.StateDigest,
	}
	manifest.ContentSHA256 = routeStateStoreContentSHA(manifest)
	if manifest.ProcessedRouteEventCount != verifiedEvents.RouteEvents ||
		manifest.VisibleRouteCount+manifest.WithdrawnRouteCount != manifest.RouteStateRecordCount ||
		firstLedger.SlotCount+secondLedger.SlotCount != RouteStateFinalSlot {
		return RouteStateStoreManifest{}, fmt.Errorf("RouteState store final population mismatch")
	}
	if _, err := writeJSONImmutable(filepath.Join(config.Output, "manifest.json"), manifest); err != nil {
		return RouteStateStoreManifest{}, err
	}
	if _, err := writeJSONImmutable(filepath.Join(config.Output, "COMPLETE.json"), manifest); err != nil {
		return RouteStateStoreManifest{}, err
	}
	return manifest, nil
}

func loadCompleteRouteStateStore(
	output string,
	identity RouteStateCheckpointIdentity,
	preflight RouteStateStorePreflight,
) (RouteStateStoreManifest, error) {
	var manifest RouteStateStoreManifest
	manifestRaw, err := readJSON(filepath.Join(output, "manifest.json"), &manifest)
	if err != nil {
		return manifest, err
	}
	var complete RouteStateStoreManifest
	completeRaw, err := readJSON(filepath.Join(output, "COMPLETE.json"), &complete)
	if err != nil {
		return manifest, err
	}
	if !bytes.Equal(manifestRaw, completeRaw) || manifest.SchemaVersion != RouteStateStoreVersion ||
		manifest.Status != "complete" || manifest.RunID != preflight.RunID ||
		manifest.DatasetID != preflight.DatasetID || manifest.CollectorID != "rrc25" ||
		manifest.ContentSHA256 != routeStateStoreContentSHA(manifest) ||
		manifest.ProcessedUpdateCount != RouteStateFinalSlot || manifest.StatePointCount != RouteStateFinalSlot ||
		manifest.AttemptedThrough != RouteEventWindowEndUTC || manifest.DataThrough != RouteEventWindowEndUTC ||
		manifest.SourceRouteEventDatasetID != preflight.SourceRouteEventDatasetID ||
		manifest.SourceRouteEventContentSHA != preflight.SourceRouteEventContentSHA ||
		manifest.SourceRouteEventManifestSHA != preflight.SourceRouteEventManifestSHA ||
		manifest.SourceRouteEventCount != preflight.SourceRouteEventCount ||
		manifest.ImplementationID != preflight.ImplementationID ||
		manifest.ProjectorName != preflight.ProjectorName ||
		manifest.ProjectorVersion != preflight.ProjectorVersion ||
		manifest.MappingVersion != preflight.MappingVersion ||
		manifest.MappingCompatibleSHA256 != preflight.MappingCompatibleSHA256 ||
		manifest.MappingRevisedSHA256 != preflight.MappingRevisedSHA256 ||
		manifest.RouteStateKey != preflight.RouteStateKey ||
		len(manifest.Checkpoints) != 3 || len(manifest.SlotLedgers) != 2 {
		return manifest, fmt.Errorf("complete RouteState store identity mismatch")
	}
	for index, slot := range []int{0, RouteStateMidpointSlot, RouteStateFinalSlot} {
		_, checkpoint, err := LoadRouteStateCheckpoint(
			filepath.Join(output, "checkpoints", fmt.Sprintf("slot-%04d", slot)), identity,
		)
		if err != nil {
			return manifest, err
		}
		if checkpoint.ProcessedSlot != slot ||
			checkpointReference(
				fmt.Sprintf("checkpoints/slot-%04d", slot), checkpoint,
			) != manifest.Checkpoints[index] {
			return manifest, fmt.Errorf("complete RouteState checkpoint slot mismatch")
		}
	}
	first, firstManifest, err := LoadRouteStateSlotLedger(output, preflight.DatasetID, 1, RouteStateMidpointSlot)
	if err != nil {
		return manifest, err
	}
	second, secondManifest, err := LoadRouteStateSlotLedger(
		output, preflight.DatasetID, RouteStateMidpointSlot+1, RouteStateFinalSlot,
	)
	if err != nil {
		return manifest, err
	}
	if firstManifest.ContentSHA256 != manifest.SlotLedgers[0].ContentSHA256 ||
		secondManifest.ContentSHA256 != manifest.SlotLedgers[1].ContentSHA256 ||
		first[len(first)-1].StateDigest != manifest.Checkpoints[1].StateDigest ||
		second[len(second)-1].StateDigest != manifest.FinalStateDigest {
		return manifest, fmt.Errorf("complete RouteState ledger/checkpoint mismatch")
	}
	return manifest, nil
}

func readRouteStateCheckpointManifestQuick(
	directory string,
	identity RouteStateCheckpointIdentity,
) (RouteStateCheckpointManifest, []byte, error) {
	var manifest RouteStateCheckpointManifest
	manifestRaw, err := readJSON(filepath.Join(directory, "manifest.json"), &manifest)
	if err != nil {
		return manifest, nil, err
	}
	var complete RouteStateCheckpointManifest
	completeRaw, err := readJSON(filepath.Join(directory, "COMPLETE.json"), &complete)
	if err != nil {
		return manifest, nil, err
	}
	if !bytes.Equal(manifestRaw, completeRaw) ||
		manifest.SchemaVersion != RouteStateCheckpointVersion || manifest.Status != "complete" ||
		manifest.RouteStateDatasetID != identity.RouteStateDatasetID || manifest.CollectorID != "rrc25" ||
		manifest.SourceRouteEventDatasetID != identity.SourceRouteEventDatasetID ||
		manifest.SourceRouteEventContentSHA != identity.SourceRouteEventContentSHA ||
		manifest.ImplementationID != identity.ImplementationID ||
		manifest.ProjectorName != identity.ProjectorName || manifest.ProjectorVersion != identity.ProjectorVersion ||
		manifest.MappingVersion != identity.MappingVersion ||
		manifest.ContentSHA256 != routeStateCheckpointContentSHA(manifest) {
		return manifest, nil, fmt.Errorf("RouteState checkpoint quick identity mismatch")
	}
	for shard, meta := range manifest.Shards {
		if meta.Shard != shard || meta.Path != fmt.Sprintf("shard-%03d.bin.gz", shard) {
			return manifest, nil, fmt.Errorf("RouteState checkpoint shard order mismatch")
		}
		sha, size, err := sha256File(filepath.Join(directory, meta.Path))
		if err != nil {
			return manifest, nil, err
		}
		if sha != meta.SHA256 || size != meta.SizeBytes {
			return manifest, nil, fmt.Errorf("RouteState checkpoint shard identity mismatch")
		}
	}
	return manifest, manifestRaw, nil
}

func routeStateReplayAuditContentSHA(result RouteStateReplayAuditResult) string {
	result.ContentSHA256 = ""
	raw, _ := json.Marshal(result)
	digest := sha256.Sum256(raw)
	return hex.EncodeToString(digest[:])
}

func AuditRouteStateReplay(
	config RouteStateStoreConfig,
	auditOutput string,
) (RouteStateReplayAuditResult, error) {
	if auditOutput == "" {
		return RouteStateReplayAuditResult{}, fmt.Errorf("RouteState replay audit output is required")
	}
	if _, err := os.Lstat(auditOutput); err == nil {
		return RouteStateReplayAuditResult{}, fmt.Errorf("RouteState replay audit output already exists")
	} else if !os.IsNotExist(err) {
		return RouteStateReplayAuditResult{}, err
	}
	preflight, err := PreflightRouteStateStore(config)
	if err != nil {
		return RouteStateReplayAuditResult{}, err
	}
	verifiedEvents, manifestSHA, err := verifyRouteEventSource(config)
	if err != nil {
		return RouteStateReplayAuditResult{}, err
	}
	if verifiedEvents.DatasetID != preflight.SourceRouteEventDatasetID ||
		verifiedEvents.ContentSHA256 != preflight.SourceRouteEventContentSHA ||
		manifestSHA != preflight.SourceRouteEventManifestSHA {
		return RouteStateReplayAuditResult{}, fmt.Errorf("RouteEvent source changed before replay audit")
	}
	mapping, err := LoadGlobalCountryMapping(config.CompatibleMappingPath, config.RevisedMappingPath)
	if err != nil {
		return RouteStateReplayAuditResult{}, err
	}
	identity := RouteStateCheckpointIdentity{
		RouteStateDatasetID:        preflight.DatasetID,
		SourceRouteEventDatasetID:  verifiedEvents.DatasetID,
		SourceRouteEventContentSHA: verifiedEvents.ContentSHA256,
		ImplementationID:           config.ImplementationID, ProjectorName: RouteStateProjectorName,
		ProjectorVersion: RouteStateProjectorVersion, MappingVersion: mapping.MappingVersion,
		MappingCompatibleSHA256: mapping.CompatibleSHA256, MappingRevisedSHA256: mapping.RevisedSHA256,
		WindowStartUTC: RouteEventWindowStartUTC, WindowEndExclusiveUTC: RouteEventWindowEndUTC,
	}
	midpointDirectory := filepath.Join(
		config.Output, "checkpoints", fmt.Sprintf("slot-%04d", RouteStateMidpointSlot),
	)
	state, midpoint, err := LoadRouteStateCheckpoint(midpointDirectory, identity)
	if err != nil {
		return RouteStateReplayAuditResult{}, err
	}
	formalLedger, _, err := LoadRouteStateSlotLedger(
		config.Output, preflight.DatasetID, RouteStateMidpointSlot+1, RouteStateFinalSlot,
	)
	if err != nil {
		return RouteStateReplayAuditResult{}, err
	}
	replayedLedger, err := processRouteStateUpdates(
		config, verifiedEvents, state, RouteStateMidpointSlot+1, RouteStateFinalSlot,
	)
	if err != nil {
		return RouteStateReplayAuditResult{}, err
	}
	if err := CompareRouteStateSlotLedgers(formalLedger, replayedLedger); err != nil {
		return RouteStateReplayAuditResult{}, err
	}
	formalFinalDirectory := filepath.Join(
		config.Output, "checkpoints", fmt.Sprintf("slot-%04d", RouteStateFinalSlot),
	)
	formalFinal, formalRaw, err := readRouteStateCheckpointManifestQuick(formalFinalDirectory, identity)
	if err != nil {
		return RouteStateReplayAuditResult{}, err
	}
	if err := os.MkdirAll(auditOutput, 0o750); err != nil {
		return RouteStateReplayAuditResult{}, err
	}
	replayedDirectory := filepath.Join(auditOutput, "final-checkpoint")
	replayedFinal, err := WriteRouteStateCheckpoint(
		replayedDirectory, state, identity, RouteEventWindowEndUTC,
		RouteStateFinalSlot, formalFinal.ShardCount,
	)
	if err != nil {
		return RouteStateReplayAuditResult{}, err
	}
	replayedRaw, err := os.ReadFile(filepath.Join(replayedDirectory, "manifest.json"))
	if err != nil {
		return RouteStateReplayAuditResult{}, err
	}
	manifestEqual := bytes.Equal(formalRaw, replayedRaw)
	shardsEqual := len(formalFinal.Shards) == len(replayedFinal.Shards)
	if shardsEqual {
		for index := range formalFinal.Shards {
			if formalFinal.Shards[index] != replayedFinal.Shards[index] {
				shardsEqual = false
				break
			}
		}
	}
	if !manifestEqual || !shardsEqual || !routeStateCheckpointReferencesEqual(formalFinal, replayedFinal) {
		return RouteStateReplayAuditResult{}, fmt.Errorf("checkpoint continuation did not reproduce final RouteState")
	}
	result := RouteStateReplayAuditResult{
		SchemaVersion: "rrc25-route-state-replay-audit/v1", Status: "complete",
		RouteStateDatasetID: preflight.DatasetID, ImplementationID: config.ImplementationID,
		SourceRouteEventDatasetID: verifiedEvents.DatasetID,
		SourceCheckpointID:        midpoint.CheckpointID, SourceCheckpointSlot: midpoint.ProcessedSlot,
		ComparedSlotCount: len(formalLedger), FinalStateDigest: replayedFinal.StateDigest,
		FinalRouteStateRecordCount:   replayedFinal.RouteStateRecordCount,
		FinalVisibleRouteCount:       replayedFinal.VisibleRouteCount,
		FormalFinalCheckpointID:      formalFinal.CheckpointID,
		ReplayedFinalCheckpointID:    replayedFinal.CheckpointID,
		CheckpointManifestByteEqual:  manifestEqual,
		CheckpointShardIdentityEqual: shardsEqual,
	}
	result.ContentSHA256 = routeStateReplayAuditContentSHA(result)
	return result, nil
}

func routeStateCheckpointReferencesEqual(
	left, right RouteStateCheckpointManifest,
) bool {
	leftRaw, _ := json.Marshal(left)
	rightRaw, _ := json.Marshal(right)
	return bytes.Equal(leftRaw, rightRaw)
}

func CompareRouteStateSlotLedgers(
	left []RouteStateSlotRecord,
	right []RouteStateSlotRecord,
) error {
	if len(left) != len(right) {
		return fmt.Errorf("RouteState slot ledger lengths differ")
	}
	for index := range left {
		if left[index] != right[index] {
			return fmt.Errorf("RouteState slot ledger differs at slot %d", left[index].Slot)
		}
	}
	return nil
}

func SortedRouteStateKeysForAudit(state *RouteState) []RouteStateKey {
	keys := make([]RouteStateKey, 0, len(state.Routes))
	for key := range state.Routes {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool { return routeStateKeyLess(keys[i], keys[j]) })
	return keys
}
