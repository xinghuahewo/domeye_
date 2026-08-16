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
	"io"
	"os"
	"path/filepath"
	"reflect"
	"time"
)

const (
	RouteEventStoreAuditVersion  = "rrc25-route-event-store-audit/v1"
	RouteEventStoreSampleVersion = "sha256_partition_and_event_min/v1"
	RouteEventStoreAuditSamples  = 100
	firstRepairPartitionIndex    = 105
	secondRepairPartitionIndex   = 656
)

type RouteEventStoreAuditConfig struct {
	StoreConfig           RouteEventStoreConfig
	AuditImplementationID string
	SampleCount           int
}

type RouteEventStoreAuditSample struct {
	SampleOrdinal   int     `json:"sample_ordinal"`
	PartitionIndex  int     `json:"partition_index"`
	ArtifactID      string  `json:"artifact_id"`
	ArtifactSHA256  string  `json:"artifact_sha256"`
	Action          string  `json:"action"`
	RouteEventID    string  `json:"route_event_id"`
	VPID            string  `json:"vp_id"`
	AFISAFI         string  `json:"afi_safi"`
	Prefix          string  `json:"prefix"`
	ASPathID        *string `json:"as_path_id"`
	RecordOrdinal   uint32  `json:"record_ordinal"`
	ElementOrdinal  uint32  `json:"element_ordinal"`
	RawRecordSHA256 string  `json:"raw_record_sha256"`
}

type RouteEventStoreAuditResult struct {
	SchemaVersion           string                       `json:"schema_version"`
	Status                  string                       `json:"status"`
	AuditImplementationID   string                       `json:"audit_implementation_id"`
	StoreImplementationID   string                       `json:"store_implementation_id"`
	ImportRunID             string                       `json:"import_run_id"`
	DatasetID               string                       `json:"dataset_id"`
	StoreContentSHA256      string                       `json:"store_content_sha256"`
	SelectionSHA256         string                       `json:"selection_sha256"`
	CollectorID             string                       `json:"collector_id"`
	SourceDatasetURI        string                       `json:"source_dataset_uri"`
	WindowStartUTC          string                       `json:"window_start_utc"`
	WindowEndExclusiveUTC   string                       `json:"window_end_exclusive_utc"`
	ArtifactCount           int                          `json:"artifact_count"`
	PhysicalRecordCount     int64                        `json:"physical_record_count"`
	RouteEventCount         int64                        `json:"route_event_count"`
	AnnounceCount           int64                        `json:"announce_count"`
	WithdrawCount           int64                        `json:"withdraw_count"`
	RIBSnapshotCount        int64                        `json:"rib_snapshot_count"`
	SampleAlgorithm         string                       `json:"sample_algorithm"`
	SampleCount             int                          `json:"sample_count"`
	SampleAnnounceCount     int                          `json:"sample_announce_count"`
	SampleWithdrawCount     int                          `json:"sample_withdraw_count"`
	VerifiedSourceArtifacts int                          `json:"verified_source_artifact_count"`
	Samples                 []RouteEventStoreAuditSample `json:"samples"`
	StartedAtUTC            string                       `json:"started_at_utc"`
	CompletedAtUTC          string                       `json:"completed_at_utc"`
	ContentSHA256           string                       `json:"content_sha256"`
}

func routeEventAuditPartitionIndexes(datasetID string, count int) ([]int, error) {
	if count < 3 || count > RouteEventUpdateCount {
		return nil, fmt.Errorf("audit sample count must be between 3 and %d", RouteEventUpdateCount)
	}
	result := []int{1, firstRepairPartitionIndex, secondRepairPartitionIndex}
	seen := map[int]struct{}{1: {}, firstRepairPartitionIndex: {}, secondRepairPartitionIndex: {}}
	for ordinal := 0; len(result) < count; ordinal++ {
		digest := sha256.Sum256([]byte(fmt.Sprintf(
			"%s|%s|%d", RouteEventStoreSampleVersion, datasetID, ordinal,
		)))
		index := int(binary.BigEndian.Uint64(digest[:8])%uint64(RouteEventUpdateCount)) + 1
		if _, found := seen[index]; found {
			continue
		}
		seen[index] = struct{}{}
		result = append(result, index)
	}
	return result, nil
}

func selectAuditedRouteEvent(
	path string,
	datasetID string,
	partitionIndex int,
	action string,
) (routeEventRow, error) {
	file, err := os.Open(path)
	if err != nil {
		return routeEventRow{}, err
	}
	defer file.Close()
	decoded, err := gzip.NewReader(file)
	if err != nil {
		return routeEventRow{}, err
	}
	defer decoded.Close()
	scanner := bufio.NewScanner(decoded)
	scanner.Buffer(make([]byte, 64<<10), 4<<20)
	var selected routeEventRow
	var selectedDigest [sha256.Size]byte
	found := false
	for scanner.Scan() {
		var row routeEventRow
		if err := json.Unmarshal(scanner.Bytes(), &row); err != nil {
			return routeEventRow{}, err
		}
		if row.Action != action {
			continue
		}
		digest := sha256.Sum256([]byte(fmt.Sprintf(
			"%s|%s|%d|%s|%s",
			RouteEventStoreSampleVersion, datasetID, partitionIndex, action, row.RouteEventID,
		)))
		if !found || bytes.Compare(digest[:], selectedDigest[:]) < 0 {
			selected = row
			selectedDigest = digest
			found = true
		}
	}
	if err := scanner.Err(); err != nil {
		return routeEventRow{}, err
	}
	if !found {
		return routeEventRow{}, fmt.Errorf(
			"partition %d has no %s RouteEvent", partitionIndex, action,
		)
	}
	return selected, nil
}

func readAuditedRawRecordRow(path string, ordinal uint32) (rawMRTRecordRow, error) {
	file, err := os.Open(path)
	if err != nil {
		return rawMRTRecordRow{}, err
	}
	defer file.Close()
	decoded, err := gzip.NewReader(file)
	if err != nil {
		return rawMRTRecordRow{}, err
	}
	defer decoded.Close()
	scanner := bufio.NewScanner(decoded)
	scanner.Buffer(make([]byte, 64<<10), 4<<20)
	for scanner.Scan() {
		var row rawMRTRecordRow
		if err := json.Unmarshal(scanner.Bytes(), &row); err != nil {
			return rawMRTRecordRow{}, err
		}
		if row.RecordOrdinal == ordinal {
			return row, nil
		}
		if row.RecordOrdinal > ordinal {
			break
		}
	}
	if err := scanner.Err(); err != nil {
		return rawMRTRecordRow{}, err
	}
	return rawMRTRecordRow{}, fmt.Errorf("raw record ordinal %d is absent", ordinal)
}

func readAuditedASPath(path string, id string) (asPathRow, error) {
	file, err := os.Open(path)
	if err != nil {
		return asPathRow{}, err
	}
	defer file.Close()
	decoded, err := gzip.NewReader(file)
	if err != nil {
		return asPathRow{}, err
	}
	defer decoded.Close()
	scanner := bufio.NewScanner(decoded)
	scanner.Buffer(make([]byte, 64<<10), 4<<20)
	for scanner.Scan() {
		var row asPathRow
		if err := json.Unmarshal(scanner.Bytes(), &row); err != nil {
			return asPathRow{}, err
		}
		if row.ASPathID == id {
			return row, nil
		}
	}
	if err := scanner.Err(); err != nil {
		return asPathRow{}, err
	}
	return asPathRow{}, fmt.Errorf("AS_PATH %s is absent", id)
}

func replayAuditedUpdateElement(
	rawRoot string,
	artifact Artifact,
	artifactIndex int,
	recordOrdinal uint32,
	elementOrdinal uint32,
) (ParsedEvent, MRTRecordEvidence, error) {
	var selected ParsedEvent
	var selectedRecord MRTRecordEvidence
	found := false
	offset := int64(0)
	err := withVerifiedGzip(rawRoot, artifact, func(reader io.Reader) error {
		for ordinal := uint32(0); ; ordinal++ {
			record, err := readMRTRecordEvidence(reader, offset)
			if err == io.EOF {
				break
			}
			if err != nil {
				return err
			}
			offset += int64(record.RecordLength)
			if ordinal != recordOrdinal {
				continue
			}
			if record.MRTType != mrtBGP4MP && record.MRTType != mrtBGP4MPET {
				return fmt.Errorf("audited record has unsupported MRT type %d", record.MRTType)
			}
			peer, eventMicros, asnWidth, messageType, body, err := parseBGP4MP(
				record.Timestamp, record.MRTType, record.Subtype, record.Payload,
			)
			if err != nil {
				return err
			}
			if messageType != 2 {
				return fmt.Errorf("audited RouteEvent points to BGP message type %d", messageType)
			}
			stats := UpdateParseStats{UnknownOptional: make(map[uint8]int64)}
			events, err := decodeUpdateEvents(
				peer, eventMicros, body, asnWidth, uint16(artifactIndex), ordinal, &stats,
			)
			if err != nil {
				return err
			}
			for _, event := range events {
				if event.ElementOrdinal == elementOrdinal {
					selected = event
					selectedRecord = record
					selectedRecord.Payload = nil
					found = true
					break
				}
			}
		}
		return nil
	})
	if err != nil {
		return ParsedEvent{}, MRTRecordEvidence{}, err
	}
	if !found {
		return ParsedEvent{}, MRTRecordEvidence{}, fmt.Errorf(
			"raw element %d/%d is absent", recordOrdinal, elementOrdinal,
		)
	}
	return selected, selectedRecord, nil
}

func auditedRouteEventRow(artifact Artifact, event ParsedEvent) (routeEventRow, error) {
	action, err := routeAction(event.Action)
	if err != nil {
		return routeEventRow{}, err
	}
	afiSafi, err := routeAFISAFI(event.Key.AFI)
	if err != nil {
		return routeEventRow{}, err
	}
	missing := make(map[string]string)
	var pathID *string
	var origin *uint32
	qualityFlags := make([]string, 0)
	if action == "withdraw" {
		missing["as_path"] = "not_applicable"
		missing["origin_asn"] = "not_applicable"
	} else {
		if event.ASPath == nil {
			return routeEventRow{}, fmt.Errorf("audited announcement is missing AS_PATH")
		}
		value, err := asPathIdentity(*event.ASPath)
		if err != nil {
			return routeEventRow{}, err
		}
		pathID = &value
		qualityFlags = pathQuality(*event.ASPath, event.PathWarnings)
		if event.OriginKnown {
			value := event.OriginASN
			origin = &value
		} else {
			missing["origin_asn"] = "not_observed"
		}
	}
	var attributeSHA *string
	if event.AttributeSHA256 != "" {
		value := event.AttributeSHA256
		attributeSHA = &value
	}
	warnings := sortedUniqueStrings(event.PathWarnings)
	if len(warnings) == 0 {
		warnings = nil
	}
	return routeEventRow{
		RouteEventID:    routeEventID(artifact.FileSHA256, event.RecordOrdinal, event.ElementOrdinal),
		EventTimeUTC:    canonicalTimeFromMicros(event.EventMicros),
		VPID:            VPIdentifier(event.Key.PeerIP, event.Key.PeerASN),
		VPPeerIP:        event.Key.PeerIP.String(),
		VPASN:           event.Key.PeerASN,
		Action:          action,
		AFISAFI:         afiSafi,
		Prefix:          event.Key.Prefix.String(),
		ASPathID:        pathID,
		OriginASN:       origin,
		AttributeSHA256: attributeSHA,
		RecordOrdinal:   event.RecordOrdinal,
		ElementOrdinal:  event.ElementOrdinal,
		QualityFlags:    qualityFlags,
		ParserWarnings:  warnings,
		MissingReasons:  missing,
	}, nil
}

func routeEventStoreAuditContentSHA(result RouteEventStoreAuditResult) string {
	value := struct {
		SchemaVersion         string                       `json:"schema_version"`
		AuditImplementationID string                       `json:"audit_implementation_id"`
		StoreImplementationID string                       `json:"store_implementation_id"`
		ImportRunID           string                       `json:"import_run_id"`
		DatasetID             string                       `json:"dataset_id"`
		StoreContentSHA256    string                       `json:"store_content_sha256"`
		SelectionSHA256       string                       `json:"selection_sha256"`
		SampleAlgorithm       string                       `json:"sample_algorithm"`
		Samples               []RouteEventStoreAuditSample `json:"samples"`
	}{
		SchemaVersion:         result.SchemaVersion,
		AuditImplementationID: result.AuditImplementationID,
		StoreImplementationID: result.StoreImplementationID,
		ImportRunID:           result.ImportRunID,
		DatasetID:             result.DatasetID,
		StoreContentSHA256:    result.StoreContentSHA256,
		SelectionSHA256:       result.SelectionSHA256,
		SampleAlgorithm:       result.SampleAlgorithm,
		Samples:               result.Samples,
	}
	raw, _ := json.Marshal(value)
	digest := sha256.Sum256(raw)
	return hex.EncodeToString(digest[:])
}

func AuditRouteEventStore(config RouteEventStoreAuditConfig) (RouteEventStoreAuditResult, error) {
	started := operationTimeUTC()
	if err := validateRouteEventImplementationID(config.AuditImplementationID); err != nil {
		return RouteEventStoreAuditResult{}, fmt.Errorf("audit %w", err)
	}
	if config.SampleCount == 0 {
		config.SampleCount = RouteEventStoreAuditSamples
	}
	config.StoreConfig.Resume = true
	if config.StoreConfig.Workers < 1 {
		config.StoreConfig.Workers = 1
	}
	manifest, err := RunRouteEventStore(config.StoreConfig)
	if err != nil {
		return RouteEventStoreAuditResult{}, err
	}
	indexes, err := routeEventAuditPartitionIndexes(manifest.DatasetID, config.SampleCount)
	if err != nil {
		return RouteEventStoreAuditResult{}, err
	}
	result := RouteEventStoreAuditResult{
		SchemaVersion:         RouteEventStoreAuditVersion,
		Status:                "complete",
		AuditImplementationID: config.AuditImplementationID,
		StoreImplementationID: manifest.ImplementationID,
		ImportRunID:           manifest.ImportRunID,
		DatasetID:             manifest.DatasetID,
		StoreContentSHA256:    manifest.ContentSHA256,
		SelectionSHA256:       manifest.SelectionSHA256,
		CollectorID:           manifest.CollectorID,
		SourceDatasetURI:      manifest.SourceDatasetURI,
		WindowStartUTC:        manifest.WindowStartUTC,
		WindowEndExclusiveUTC: manifest.WindowEndExclusive,
		ArtifactCount:         manifest.ArtifactCount,
		PhysicalRecordCount:   manifest.PhysicalRecords,
		RouteEventCount:       manifest.RouteEvents,
		AnnounceCount:         manifest.Announces,
		WithdrawCount:         manifest.Withdraws,
		RIBSnapshotCount:      manifest.RIBSnapshots,
		SampleAlgorithm:       RouteEventStoreSampleVersion,
		SampleCount:           len(indexes),
		Samples:               make([]RouteEventStoreAuditSample, 0, len(indexes)),
		StartedAtUTC:          started,
	}
	for ordinal, partitionIndex := range indexes {
		partition := manifest.Partitions[partitionIndex]
		action := "announce"
		if ordinal == 0 {
			action = "withdraw"
		}
		eventPath := filepath.Join(
			config.StoreConfig.Output, filepath.FromSlash(partition.Events.Path),
		)
		candidate, err := selectAuditedRouteEvent(
			eventPath, manifest.DatasetID, partitionIndex, action,
		)
		if err != nil {
			return RouteEventStoreAuditResult{}, err
		}
		rawRow, err := readAuditedRawRecordRow(filepath.Join(
			config.StoreConfig.Output, filepath.FromSlash(partition.Records.Path),
		), candidate.RecordOrdinal)
		if err != nil {
			return RouteEventStoreAuditResult{}, err
		}
		replayed, rawRecord, err := replayAuditedUpdateElement(
			config.StoreConfig.RawRoot, partition.Artifact, partitionIndex,
			candidate.RecordOrdinal, candidate.ElementOrdinal,
		)
		if err != nil {
			return RouteEventStoreAuditResult{}, err
		}
		expectedRaw := rawMRTRecordRow{
			RecordOrdinal:      candidate.RecordOrdinal,
			MRTTimeUTC:         time.Unix(int64(rawRecord.Timestamp), 0).UTC().Format(time.RFC3339),
			MRTType:            rawRecord.MRTType,
			MRTSubtype:         rawRecord.Subtype,
			UncompressedOffset: rawRecord.UncompressedOffset,
			RecordLength:       rawRecord.RecordLength,
			RecordSHA256:       rawRecord.RecordSHA256,
		}
		if rawRow != expectedRaw {
			return RouteEventStoreAuditResult{}, fmt.Errorf(
				"sample %d raw record sidecar mismatch", ordinal,
			)
		}
		expected, err := auditedRouteEventRow(partition.Artifact, replayed)
		if err != nil {
			return RouteEventStoreAuditResult{}, err
		}
		if !reflect.DeepEqual(candidate, expected) {
			return RouteEventStoreAuditResult{}, fmt.Errorf(
				"sample %d RouteEvent mismatch", ordinal,
			)
		}
		if candidate.ASPathID != nil {
			candidatePath, err := readAuditedASPath(filepath.Join(
				config.StoreConfig.Output, filepath.FromSlash(partition.Paths.Path),
			), *candidate.ASPathID)
			if err != nil {
				return RouteEventStoreAuditResult{}, err
			}
			expectedPath := asPathRow{
				ASPathID:     *candidate.ASPathID,
				ASPath:       *replayed.ASPath,
				QualityFlags: pathQuality(*replayed.ASPath, nil),
			}
			if !reflect.DeepEqual(candidatePath, expectedPath) {
				return RouteEventStoreAuditResult{}, fmt.Errorf(
					"sample %d AS_PATH dictionary mismatch", ordinal,
				)
			}
		}
		sample := RouteEventStoreAuditSample{
			SampleOrdinal:   ordinal,
			PartitionIndex:  partitionIndex,
			ArtifactID:      partition.Artifact.ArtifactID,
			ArtifactSHA256:  partition.Artifact.FileSHA256,
			Action:          candidate.Action,
			RouteEventID:    candidate.RouteEventID,
			VPID:            candidate.VPID,
			AFISAFI:         candidate.AFISAFI,
			Prefix:          candidate.Prefix,
			ASPathID:        candidate.ASPathID,
			RecordOrdinal:   candidate.RecordOrdinal,
			ElementOrdinal:  candidate.ElementOrdinal,
			RawRecordSHA256: rawRecord.RecordSHA256,
		}
		result.Samples = append(result.Samples, sample)
		result.VerifiedSourceArtifacts++
		if candidate.Action == "announce" {
			result.SampleAnnounceCount++
		} else {
			result.SampleWithdrawCount++
		}
	}
	result.CompletedAtUTC = operationTimeUTC()
	result.ContentSHA256 = routeEventStoreAuditContentSHA(result)
	return result, nil
}
