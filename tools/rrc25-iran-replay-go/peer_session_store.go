package replay

import (
	"bufio"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/netip"
	"os"
	"path/filepath"
	"sync"
	"time"
)

const (
	PeerSessionStoreVersion     = "rrc25-peer-session-store/v1"
	PeerSessionPartitionVersion = "rrc25-peer-session-partition/v1"
	PeerSessionExtractorName    = "domeye_peer_session_state_change_extractor"
	PeerSessionExtractorVersion = "1.0.0"
)

var peerSessionStateNames = map[uint16]string{
	1: "idle", 2: "connect", 3: "active", 4: "open_sent", 5: "open_confirm", 6: "established",
}

type PeerSessionStoreConfig struct {
	RawRoot                    string
	SelectionPath              string
	RouteEventRoot             string
	RouteEventImplementationID string
	Output                     string
	ImplementationID           string
	Workers                    int
	Resume                     bool
	Progress                   func(string)
}

func (config PeerSessionStoreConfig) progress(message string) {
	if config.Progress != nil {
		config.Progress(message)
	}
}

type PeerSessionObservationRow struct {
	ObservationID             string `json:"observation_id"`
	SessionID                 string `json:"session_id"`
	CollectorID               string `json:"collector_id"`
	VPID                      string `json:"vp_id"`
	EventTimeUTC              string `json:"event_time_utc"`
	EventEpochMicroseconds    int64  `json:"event_epoch_microseconds"`
	PeerASN                   uint32 `json:"peer_asn"`
	LocalASN                  uint32 `json:"local_asn"`
	InterfaceIndex            uint16 `json:"interface_index"`
	AFI                       uint16 `json:"afi"`
	PeerIP                    string `json:"peer_ip"`
	LocalIP                   string `json:"local_ip"`
	OldState                  uint16 `json:"old_state"`
	OldStateName              string `json:"old_state_name"`
	NewState                  uint16 `json:"new_state"`
	NewStateName              string `json:"new_state_name"`
	ArtifactID                string `json:"artifact_id"`
	FileSHA256                string `json:"file_sha256"`
	RecordOrdinal             uint32 `json:"record_ordinal"`
	UncompressedOffset        int64  `json:"uncompressed_offset"`
	RecordLength              uint32 `json:"record_length"`
	RawRecordSHA256           string `json:"raw_record_sha256"`
	Semantics                 string `json:"semantics"`
	PrefixWithdrawalInference string `json:"prefix_withdrawal_inference"`
}

type PeerSessionPartitionManifest struct {
	SchemaVersion       string              `json:"schema_version"`
	DatasetID           string              `json:"dataset_id"`
	ArtifactIndex       int                 `json:"artifact_index"`
	Artifact            Artifact            `json:"artifact"`
	InputIntegrity      string              `json:"input_integrity_status"`
	PhysicalRecordCount int64               `json:"physical_record_count"`
	ObservationCount    int64               `json:"observation_count"`
	TransitionCounts    map[string]int64    `json:"transition_counts"`
	Observations        RouteEventStoreFile `json:"observations"`
	ContentSHA256       string              `json:"content_sha256"`
}

type PeerSessionStoreManifest struct {
	SchemaVersion               string                         `json:"schema_version"`
	Status                      string                         `json:"status"`
	RunID                       string                         `json:"run_id"`
	DatasetID                   string                         `json:"dataset_id"`
	CollectorID                 string                         `json:"collector_id"`
	WindowStartUTC              string                         `json:"window_start_utc"`
	WindowEndExclusiveUTC       string                         `json:"window_end_exclusive_utc"`
	StatePointCount             int                            `json:"state_point_count"`
	SelectionSHA256             string                         `json:"selection_sha256"`
	SourceManifestSHA256        string                         `json:"source_manifest_sha256"`
	SourceRouteEventDatasetID   string                         `json:"source_route_event_dataset_id"`
	SourceRouteEventContentSHA  string                         `json:"source_route_event_content_sha256"`
	SourceRouteEventManifestSHA string                         `json:"source_route_event_manifest_sha256"`
	ImplementationID            string                         `json:"implementation_id"`
	ExtractorName               string                         `json:"extractor_name"`
	ExtractorVersion            string                         `json:"extractor_version"`
	ObservationSemantics        string                         `json:"observation_semantics"`
	PrefixWithdrawalInference   string                         `json:"prefix_withdrawal_inference"`
	UpdateArtifactCount         int                            `json:"update_artifact_count"`
	PhysicalRecordCount         int64                          `json:"physical_record_count"`
	ObservationCount            int64                          `json:"observation_count"`
	UniqueSessionCount          int                            `json:"unique_session_count"`
	UniquePeerASNCount          int                            `json:"unique_peer_asn_count"`
	TransitionCounts            map[string]int64               `json:"transition_counts"`
	Partitions                  []PeerSessionPartitionManifest `json:"partitions"`
	ContentSHA256               string                         `json:"content_sha256"`
}

type peerSessionStoreMarker struct {
	SchemaVersion               string `json:"schema_version"`
	RunID                       string `json:"run_id"`
	DatasetID                   string `json:"dataset_id"`
	ImplementationID            string `json:"implementation_id"`
	SourceRouteEventDatasetID   string `json:"source_route_event_dataset_id"`
	SourceRouteEventContentSHA  string `json:"source_route_event_content_sha256"`
	SourceRouteEventManifestSHA string `json:"source_route_event_manifest_sha256"`
	SelectionSHA256             string `json:"selection_sha256"`
}

func peerSessionStoreIdentity(selectionSHA string, source RouteEventStoreManifest, implementationID string) (string, string) {
	runID := stableID("peer_session_run_v1_", map[string]any{
		"schema_version": PeerSessionStoreVersion, "selection_sha256": selectionSHA,
		"source_route_event_dataset_id":     source.DatasetID,
		"source_route_event_content_sha256": source.ContentSHA256,
		"implementation_id":                 implementationID, "extractor_name": PeerSessionExtractorName,
		"extractor_version": PeerSessionExtractorVersion,
	}, 32)
	datasetID := stableID("peer_session_dataset_v1_", map[string]any{"run_id": runID, "collector_id": "rrc25"}, 32)
	return runID, datasetID
}

func peerSessionObservationID(fileSHA string, ordinal uint32) string {
	return stableID("pso_v1_", map[string]any{
		"schema": "peer_session_observation_id_v1", "file_sha256": fileSHA, "record_ordinal": ordinal,
	}, 32)
}

func peerSessionID(peerIP netip.Addr, peerASN, localASN uint32, localIP netip.Addr, interfaceIndex, afi uint16) string {
	return stableID("session_v1_", map[string]any{
		"schema": "rrc25_peer_session_id_v1", "collector_id": "rrc25",
		"peer_ip": peerIP.String(), "peer_asn": peerASN, "local_ip": localIP.String(),
		"local_asn": localASN, "interface_index": interfaceIndex, "afi": afi,
	}, 32)
}

func parsePeerSessionStateChange(record MRTRecordEvidence, artifact Artifact, ordinal uint32) (PeerSessionObservationRow, bool, error) {
	if record.MRTType != mrtBGP4MP && record.MRTType != mrtBGP4MPET {
		return PeerSessionObservationRow{}, false, nil
	}
	if record.Subtype != 0 && record.Subtype != 5 {
		return PeerSessionObservationRow{}, false, nil
	}
	cursor := cursor{raw: record.Payload, field: "STATE_CHANGE"}
	eventMicros := int64(record.Timestamp) * 1_000_000
	if record.MRTType == mrtBGP4MPET {
		micros, err := cursor.u32("microseconds")
		if err != nil || micros >= 1_000_000 {
			return PeerSessionObservationRow{}, false, fmt.Errorf("invalid STATE_CHANGE extended timestamp")
		}
		eventMicros += int64(micros)
	}
	asnWidth := 2
	if record.Subtype == 5 {
		asnWidth = 4
	}
	peerRaw, err := cursor.take(asnWidth, "peer-asn")
	if err != nil {
		return PeerSessionObservationRow{}, false, err
	}
	localRaw, err := cursor.take(asnWidth, "local-asn")
	if err != nil {
		return PeerSessionObservationRow{}, false, err
	}
	interfaceIndex, err := cursor.u16("interface-index")
	if err != nil {
		return PeerSessionObservationRow{}, false, err
	}
	afi, err := cursor.u16("afi")
	if err != nil {
		return PeerSessionObservationRow{}, false, err
	}
	addressBytes := 0
	switch afi {
	case 1:
		addressBytes = 4
	case 2:
		addressBytes = 16
	default:
		return PeerSessionObservationRow{}, false, fmt.Errorf("unsupported STATE_CHANGE AFI %d", afi)
	}
	peerIPRaw, err := cursor.take(addressBytes, "peer-ip")
	if err != nil {
		return PeerSessionObservationRow{}, false, err
	}
	localIPRaw, err := cursor.take(addressBytes, "local-ip")
	if err != nil {
		return PeerSessionObservationRow{}, false, err
	}
	oldState, err := cursor.u16("old-state")
	if err != nil {
		return PeerSessionObservationRow{}, false, err
	}
	newState, err := cursor.u16("new-state")
	if err != nil {
		return PeerSessionObservationRow{}, false, err
	}
	if err := cursor.finish(); err != nil {
		return PeerSessionObservationRow{}, false, err
	}
	oldName, oldOK := peerSessionStateNames[oldState]
	newName, newOK := peerSessionStateNames[newState]
	if !oldOK || !newOK {
		return PeerSessionObservationRow{}, false, fmt.Errorf("STATE_CHANGE FSM state must be 1..6")
	}
	peerIP, err := parseAddress(peerIPRaw)
	if err != nil {
		return PeerSessionObservationRow{}, false, err
	}
	localIP, err := parseAddress(localIPRaw)
	if err != nil {
		return PeerSessionObservationRow{}, false, err
	}
	peerASN := uint32(binary.BigEndian.Uint16(peerRaw))
	localASN := uint32(binary.BigEndian.Uint16(localRaw))
	if asnWidth == 4 {
		peerASN = binary.BigEndian.Uint32(peerRaw)
		localASN = binary.BigEndian.Uint32(localRaw)
	}
	slot, _ := time.Parse(time.RFC3339, artifact.ArtifactTimeUTC)
	if eventMicros < slot.UnixMicro() || eventMicros >= slot.Add(globalWindowSlot).UnixMicro() {
		return PeerSessionObservationRow{}, false, fmt.Errorf("STATE_CHANGE event outside artifact slot")
	}
	return PeerSessionObservationRow{
		ObservationID: peerSessionObservationID(artifact.FileSHA256, ordinal),
		SessionID:     peerSessionID(peerIP, peerASN, localASN, localIP, interfaceIndex, afi),
		CollectorID:   "rrc25", VPID: VPIdentifier(peerIP, peerASN),
		EventTimeUTC: canonicalTimeFromMicros(eventMicros), EventEpochMicroseconds: eventMicros,
		PeerASN: peerASN, LocalASN: localASN, InterfaceIndex: interfaceIndex, AFI: afi,
		PeerIP: peerIP.String(), LocalIP: localIP.String(), OldState: oldState, OldStateName: oldName,
		NewState: newState, NewStateName: newName, ArtifactID: artifact.ArtifactID,
		FileSHA256: artifact.FileSHA256, RecordOrdinal: ordinal,
		UncompressedOffset: record.UncompressedOffset, RecordLength: record.RecordLength,
		RawRecordSHA256: record.RecordSHA256, Semantics: "single_peer_session_transition",
		PrefixWithdrawalInference: "not_permitted",
	}, true, nil
}

func peerSessionPartitionContentSHA(value PeerSessionPartitionManifest) string {
	copy := value
	copy.ContentSHA256 = ""
	raw, _ := json.Marshal(copy)
	digest := sha256.Sum256(raw)
	return hex.EncodeToString(digest[:])
}

func peerSessionStoreContentSHA(value PeerSessionStoreManifest) string {
	copy := value
	copy.ContentSHA256 = ""
	raw, _ := json.Marshal(copy)
	digest := sha256.Sum256(raw)
	return hex.EncodeToString(digest[:])
}

func buildPeerSessionPartition(config PeerSessionStoreConfig, datasetID string, index int, artifact Artifact, expectedPhysical int64) (PeerSessionPartitionManifest, error) {
	finalDirectory := filepath.Join(config.Output, "partitions", fmt.Sprintf("%04d", index))
	if _, err := os.Lstat(finalDirectory); err == nil {
		if !config.Resume {
			return PeerSessionPartitionManifest{}, fmt.Errorf("peer-session partition already exists: %s", finalDirectory)
		}
		return loadPeerSessionPartition(config.Output, datasetID, index, artifact, expectedPhysical)
	} else if !os.IsNotExist(err) {
		return PeerSessionPartitionManifest{}, err
	}
	temporary := finalDirectory + ".tmp"
	if _, err := os.Lstat(temporary); err == nil {
		if !config.Resume {
			return PeerSessionPartitionManifest{}, fmt.Errorf("peer-session temporary partition exists: %s", temporary)
		}
		if err := os.RemoveAll(temporary); err != nil {
			return PeerSessionPartitionManifest{}, err
		}
	} else if !os.IsNotExist(err) {
		return PeerSessionPartitionManifest{}, err
	}
	if err := os.MkdirAll(temporary, 0o750); err != nil {
		return PeerSessionPartitionManifest{}, err
	}
	writer, err := newDeterministicJSONLGzipWriter(filepath.Join(temporary, "observations.jsonl.gz"))
	if err != nil {
		return PeerSessionPartitionManifest{}, err
	}
	closed := false
	defer func() {
		if !closed {
			writer.Abort()
		}
	}()
	physical := int64(0)
	observations := int64(0)
	transitions := make(map[string]int64)
	offset := int64(0)
	err = withVerifiedGzip(config.RawRoot, artifact, func(reader io.Reader) error {
		for ordinal := uint32(0); ; ordinal++ {
			record, err := readMRTRecordEvidence(reader, offset)
			if err == io.EOF {
				break
			}
			if err != nil {
				return err
			}
			offset += int64(record.RecordLength)
			physical++
			row, selected, err := parsePeerSessionStateChange(record, artifact, ordinal)
			if err != nil {
				return fmt.Errorf("record %d: %w", ordinal, err)
			}
			if !selected {
				continue
			}
			if err := writer.Write(row); err != nil {
				return err
			}
			observations++
			transitions[fmt.Sprintf("%d->%d", row.OldState, row.NewState)]++
		}
		return nil
	})
	if err != nil {
		return PeerSessionPartitionManifest{}, err
	}
	if physical != expectedPhysical {
		return PeerSessionPartitionManifest{}, fmt.Errorf("partition %d physical population mismatch: %d != %d", index, physical, expectedPhysical)
	}
	partitionPath := filepath.ToSlash(filepath.Join("partitions", fmt.Sprintf("%04d", index)))
	fileMeta, err := writer.Close(filepath.ToSlash(filepath.Join(partitionPath, "observations.jsonl.gz")))
	if err != nil {
		return PeerSessionPartitionManifest{}, err
	}
	closed = true
	manifest := PeerSessionPartitionManifest{
		SchemaVersion: PeerSessionPartitionVersion, DatasetID: datasetID,
		ArtifactIndex: index, Artifact: artifact, InputIntegrity: RouteEventInputIntegrity,
		PhysicalRecordCount: physical, ObservationCount: observations,
		TransitionCounts: transitions, Observations: fileMeta,
	}
	manifest.ContentSHA256 = peerSessionPartitionContentSHA(manifest)
	if _, err := writeJSONImmutable(filepath.Join(temporary, "manifest.json"), manifest); err != nil {
		return PeerSessionPartitionManifest{}, err
	}
	if err := os.Rename(temporary, finalDirectory); err != nil {
		return PeerSessionPartitionManifest{}, err
	}
	return manifest, nil
}

func loadPeerSessionPartition(root, datasetID string, index int, artifact Artifact, expectedPhysical int64) (PeerSessionPartitionManifest, error) {
	directory := filepath.Join(root, "partitions", fmt.Sprintf("%04d", index))
	var manifest PeerSessionPartitionManifest
	if _, err := readJSON(filepath.Join(directory, "manifest.json"), &manifest); err != nil {
		return manifest, err
	}
	if manifest.SchemaVersion != PeerSessionPartitionVersion || manifest.DatasetID != datasetID ||
		manifest.ArtifactIndex != index || manifest.Artifact != artifact ||
		manifest.InputIntegrity != RouteEventInputIntegrity || manifest.PhysicalRecordCount != expectedPhysical ||
		manifest.ObservationCount != manifest.Observations.RowCount ||
		manifest.ContentSHA256 != peerSessionPartitionContentSHA(manifest) {
		return manifest, fmt.Errorf("peer-session partition %d identity mismatch", index)
	}
	if err := verifyRouteEventStoreFile(root, manifest.Observations); err != nil {
		return manifest, err
	}
	return manifest, nil
}

func validatePeerSessionSources(config PeerSessionStoreConfig) (GlobalWindowSelection, string, RouteEventStoreManifest, string, error) {
	if err := validateRouteEventImplementationID(config.ImplementationID); err != nil {
		return GlobalWindowSelection{}, "", RouteEventStoreManifest{}, "", err
	}
	if err := validateRouteEventImplementationID(config.RouteEventImplementationID); err != nil {
		return GlobalWindowSelection{}, "", RouteEventStoreManifest{}, "", err
	}
	selection, selectionSHA, _, _, err := parseGlobalWindowSelection(config.SelectionPath)
	if err != nil {
		return selection, selectionSHA, RouteEventStoreManifest{}, "", err
	}
	if err := validateRouteEventStoreSelection(selection); err != nil {
		return selection, selectionSHA, RouteEventStoreManifest{}, "", err
	}
	source, err := readIdenticalRouteEventStoreManifests(config.RouteEventRoot)
	if err != nil {
		return selection, selectionSHA, source, "", err
	}
	sourceManifestSHA, _, err := sha256File(filepath.Join(config.RouteEventRoot, "manifest.json"))
	if err != nil {
		return selection, selectionSHA, source, "", err
	}
	if source.SchemaVersion != RouteEventStoreVersion || source.Status != "complete" ||
		source.ImplementationID != config.RouteEventImplementationID || source.SelectionSHA256 != selectionSHA ||
		source.CollectorID != "rrc25" || source.WindowStartUTC != RouteEventWindowStartUTC ||
		source.WindowEndExclusive != RouteEventWindowEndUTC || len(source.Partitions) != len(selection.Updates)+1 ||
		source.ContentSHA256 != routeEventStoreContentSHA(source) {
		return selection, selectionSHA, source, sourceManifestSHA, fmt.Errorf("source RouteEvent identity mismatch")
	}
	for index, artifact := range selection.Updates {
		partition := source.Partitions[index+1]
		if partition.Artifact != artifact || partition.ArtifactIndex != index+1 || partition.Role != "update" {
			return selection, selectionSHA, source, sourceManifestSHA, fmt.Errorf("source RouteEvent partition %d mismatch", index+1)
		}
	}
	return selection, selectionSHA, source, sourceManifestSHA, nil
}

func RunPeerSessionStore(config PeerSessionStoreConfig) (PeerSessionStoreManifest, error) {
	selection, selectionSHA, source, sourceManifestSHA, err := validatePeerSessionSources(config)
	if err != nil {
		return PeerSessionStoreManifest{}, err
	}
	if config.Workers < 1 {
		config.Workers = 8
	}
	runID, datasetID := peerSessionStoreIdentity(selectionSHA, source, config.ImplementationID)
	marker := peerSessionStoreMarker{
		SchemaVersion: PeerSessionStoreVersion, RunID: runID, DatasetID: datasetID,
		ImplementationID: config.ImplementationID, SourceRouteEventDatasetID: source.DatasetID,
		SourceRouteEventContentSHA: source.ContentSHA256, SourceRouteEventManifestSHA: sourceManifestSHA,
		SelectionSHA256: selectionSHA,
	}
	if err := os.MkdirAll(config.Output, 0o750); err != nil {
		return PeerSessionStoreManifest{}, err
	}
	if _, err := writeJSONImmutable(filepath.Join(config.Output, "RUNNING.json"), marker); err != nil {
		return PeerSessionStoreManifest{}, err
	}
	if raw, err := os.ReadFile(config.SelectionPath); err != nil {
		return PeerSessionStoreManifest{}, err
	} else if err := writeBytesImmutable(filepath.Join(config.Output, "input-selection.json"), raw); err != nil {
		return PeerSessionStoreManifest{}, err
	}

	partitions := make([]PeerSessionPartitionManifest, len(selection.Updates))
	type job struct{ index int }
	jobs := make(chan job)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var firstErr error
	var errOnce sync.Once
	var wait sync.WaitGroup
	for worker := 0; worker < config.Workers; worker++ {
		wait.Add(1)
		go func() {
			defer wait.Done()
			for item := range jobs {
				if ctx.Err() != nil {
					continue
				}
				artifact := selection.Updates[item.index]
				expectedPhysical := source.Partitions[item.index+1].PhysicalRecords
				manifest, err := buildPeerSessionPartition(config, datasetID, item.index, artifact, expectedPhysical)
				if err != nil {
					errOnce.Do(func() { firstErr = err; cancel() })
					continue
				}
				partitions[item.index] = manifest
				config.progress(fmt.Sprintf("peer-session partition %04d complete observations=%d", item.index, manifest.ObservationCount))
			}
		}()
	}
	for index := range selection.Updates {
		if ctx.Err() != nil {
			break
		}
		jobs <- job{index: index}
	}
	close(jobs)
	wait.Wait()
	if firstErr != nil {
		return PeerSessionStoreManifest{}, firstErr
	}

	var physical, observationCount int64
	transitionCounts := make(map[string]int64)
	sessions := make(map[string]struct{})
	peerASNs := make(map[uint32]struct{})
	for index, partition := range partitions {
		if partition.SchemaVersion == "" {
			return PeerSessionStoreManifest{}, fmt.Errorf("missing peer-session partition %d", index)
		}
		physical += partition.PhysicalRecordCount
		observationCount += partition.ObservationCount
		for key, count := range partition.TransitionCounts {
			transitionCounts[key] += count
		}
		path := filepath.Join(config.Output, filepath.FromSlash(partition.Observations.Path))
		file, err := os.Open(path)
		if err != nil {
			return PeerSessionStoreManifest{}, err
		}
		decoded, err := gzipJSONLines(file, func(raw []byte) error {
			var row PeerSessionObservationRow
			if err := json.Unmarshal(raw, &row); err != nil {
				return err
			}
			sessions[row.SessionID] = struct{}{}
			peerASNs[row.PeerASN] = struct{}{}
			return nil
		})
		_ = file.Close()
		if err != nil || decoded != partition.ObservationCount {
			return PeerSessionStoreManifest{}, fmt.Errorf("peer-session partition %d observation decode mismatch", index)
		}
	}
	manifest := PeerSessionStoreManifest{
		SchemaVersion: PeerSessionStoreVersion, Status: "complete", RunID: runID, DatasetID: datasetID,
		CollectorID: "rrc25", WindowStartUTC: selection.WindowStartUTC,
		WindowEndExclusiveUTC: selection.WindowEndExclusiveUTC, StatePointCount: len(selection.Updates),
		SelectionSHA256: selectionSHA, SourceManifestSHA256: selection.SourceManifestSHA256,
		SourceRouteEventDatasetID: source.DatasetID, SourceRouteEventContentSHA: source.ContentSHA256,
		SourceRouteEventManifestSHA: sourceManifestSHA, ImplementationID: config.ImplementationID,
		ExtractorName: PeerSessionExtractorName, ExtractorVersion: PeerSessionExtractorVersion,
		ObservationSemantics: "single_peer_session_transition", PrefixWithdrawalInference: "not_permitted",
		UpdateArtifactCount: len(selection.Updates), PhysicalRecordCount: physical,
		ObservationCount: observationCount, UniqueSessionCount: len(sessions), UniquePeerASNCount: len(peerASNs),
		TransitionCounts: transitionCounts, Partitions: partitions,
	}
	manifest.ContentSHA256 = peerSessionStoreContentSHA(manifest)
	if _, err := writeJSONImmutable(filepath.Join(config.Output, "manifest.json"), manifest); err != nil {
		return PeerSessionStoreManifest{}, err
	}
	if _, err := writeJSONImmutable(filepath.Join(config.Output, "COMPLETE.json"), manifest); err != nil {
		return PeerSessionStoreManifest{}, err
	}
	return LoadPeerSessionStore(config.Output, config)
}

func gzipJSONLines(reader io.Reader, consume func([]byte) error) (int64, error) {
	compressed, err := gzip.NewReader(reader)
	if err != nil {
		return 0, err
	}
	defer compressed.Close()
	scanner := bufio.NewScanner(compressed)
	scanner.Buffer(make([]byte, 64*1024), 16*1024*1024)
	var rows int64
	for scanner.Scan() {
		if err := consume(bytes.Clone(scanner.Bytes())); err != nil {
			return rows, err
		}
		rows++
	}
	return rows, scanner.Err()
}

func LoadPeerSessionStore(root string, config PeerSessionStoreConfig) (PeerSessionStoreManifest, error) {
	selection, selectionSHA, source, sourceManifestSHA, err := validatePeerSessionSources(config)
	if err != nil {
		return PeerSessionStoreManifest{}, err
	}
	runID, datasetID := peerSessionStoreIdentity(selectionSHA, source, config.ImplementationID)
	var manifest PeerSessionStoreManifest
	completeRaw, err := readJSON(filepath.Join(root, "COMPLETE.json"), &manifest)
	if err != nil {
		return manifest, err
	}
	var published PeerSessionStoreManifest
	publishedRaw, err := readJSON(filepath.Join(root, "manifest.json"), &published)
	if err != nil {
		return manifest, err
	}
	if !bytes.Equal(completeRaw, publishedRaw) || manifest.SchemaVersion != PeerSessionStoreVersion ||
		manifest.Status != "complete" || manifest.RunID != runID || manifest.DatasetID != datasetID ||
		manifest.CollectorID != "rrc25" || manifest.WindowStartUTC != RouteEventWindowStartUTC ||
		manifest.WindowEndExclusiveUTC != RouteEventWindowEndUTC || manifest.StatePointCount != RouteEventUpdateCount ||
		manifest.SelectionSHA256 != selectionSHA || manifest.SourceRouteEventDatasetID != source.DatasetID ||
		manifest.SourceRouteEventContentSHA != source.ContentSHA256 ||
		manifest.SourceRouteEventManifestSHA != sourceManifestSHA || manifest.ImplementationID != config.ImplementationID ||
		manifest.ExtractorName != PeerSessionExtractorName || manifest.ExtractorVersion != PeerSessionExtractorVersion ||
		manifest.ObservationSemantics != "single_peer_session_transition" ||
		manifest.PrefixWithdrawalInference != "not_permitted" ||
		manifest.UpdateArtifactCount != len(selection.Updates) || len(manifest.Partitions) != len(selection.Updates) ||
		manifest.ContentSHA256 != peerSessionStoreContentSHA(manifest) {
		return manifest, fmt.Errorf("complete peer-session store identity mismatch")
	}
	var physical, observations int64
	transitionCounts := make(map[string]int64)
	for index, artifact := range selection.Updates {
		partition, err := loadPeerSessionPartition(
			root, datasetID, index, artifact, source.Partitions[index+1].PhysicalRecords,
		)
		if err != nil {
			return manifest, err
		}
		listed := manifest.Partitions[index]
		if listed.ContentSHA256 != peerSessionPartitionContentSHA(listed) ||
			listed.ContentSHA256 != partition.ContentSHA256 {
			return manifest, fmt.Errorf("peer-session partition list mismatch: %d", index)
		}
		physical += partition.PhysicalRecordCount
		observations += partition.ObservationCount
		for key, count := range partition.TransitionCounts {
			transitionCounts[key] += count
		}
	}
	if physical != manifest.PhysicalRecordCount || observations != manifest.ObservationCount ||
		!mapsEqualStringInt64(transitionCounts, manifest.TransitionCounts) {
		return manifest, fmt.Errorf("peer-session aggregate population mismatch")
	}
	return manifest, nil
}
