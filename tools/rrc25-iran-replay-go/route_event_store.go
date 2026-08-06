package replay

import (
	"bufio"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"syscall"
	"time"
)

const (
	RouteEventStoreVersion     = "rrc25-route-event-store/v1"
	RouteEventPartitionVersion = "rrc25-route-event-partition/v1"
	RouteEventParserName       = "domeye_mrt_native"
	RouteEventParserVersion    = "1.0.0"
	RouteEventImporterName     = "domeye_route_event_store"
	RouteEventImporterVersion  = "1.0.0"
	RouteEventSourceDatasetURI = "domeye://raw/rrc25-global-20260224-20260310-v1"
	RouteEventInputIntegrity   = "gzip_passed_and_stream_sha256_verified"
	RouteEventWindowStartUTC   = "2026-02-24T00:00:00Z"
	RouteEventWindowEndUTC     = "2026-03-11T00:00:00Z"
	RouteEventUpdateCount      = 4_320
	RouteEventRepairCount      = 2
)

type RouteEventStoreConfig struct {
	RawRoot          string
	SelectionPath    string
	Output           string
	ImplementationID string
	Workers          int
	Resume           bool
	Progress         func(string)
}

func (config RouteEventStoreConfig) progress(message string) {
	if config.Progress != nil {
		config.Progress(message)
	}
}

type RouteEventStorePreflight struct {
	SchemaVersion       string                     `json:"schema_version"`
	ImportRunID         string                     `json:"import_run_id"`
	DatasetID           string                     `json:"dataset_id"`
	CollectorID         string                     `json:"collector_id"`
	SourceDatasetURI    string                     `json:"source_dataset_uri"`
	WindowStartUTC      string                     `json:"window_start_utc"`
	WindowEndExclusive  string                     `json:"window_end_exclusive_utc"`
	SelectionSHA256     string                     `json:"selection_sha256"`
	ArtifactCount       int                        `json:"artifact_count"`
	UpdateCount         int                        `json:"update_count"`
	RepairArtifactCount int                        `json:"repair_artifact_count"`
	RepairProvenanceSHA string                     `json:"repair_provenance_sha256"`
	RepairArtifacts     []RouteEventRepairArtifact `json:"repair_artifacts"`
	ImplementationID    string                     `json:"implementation_id"`
	ParserName          string                     `json:"parser_name"`
	ParserVersion       string                     `json:"parser_version"`
	ImporterName        string                     `json:"importer_name"`
	ImporterVersion     string                     `json:"importer_version"`
	IdentityCheckMode   string                     `json:"identity_check_mode"`
}

type RouteEventRepairArtifact struct {
	RelativePath               string   `json:"relative_path"`
	OriginalFileSHA256         string   `json:"original_file_sha256"`
	OriginalSizeBytes          int64    `json:"original_size_bytes"`
	OriginalIntegrityStatus    string   `json:"original_integrity_status"`
	ReplacementArtifact        Artifact `json:"replacement_artifact"`
	ReplacementIntegrityStatus string   `json:"replacement_integrity_status"`
	Relationship               string   `json:"relationship"`
}

type frozenRepairArtifact struct {
	RelativePath      string
	OriginalSHA256    string
	OriginalSize      int64
	ReplacementSHA256 string
	ReplacementSize   int64
}

var frozenRouteEventRepairs = []frozenRepairArtifact{
	{
		RelativePath:      "rrc25/2026.02/updates.20260224.0840.gz",
		OriginalSHA256:    "d13aeb7c20aab3c3343e0393249c073c7bbf24bfe9d8e4dc25b686cf081f5bd4",
		OriginalSize:      6_402_174,
		ReplacementSHA256: "8cbb2ff00d3e7340ceb6269f06125ee87740d41eec8d3af0cee542f97e8bce15",
		ReplacementSize:   5_419_419,
	},
	{
		RelativePath:      "rrc25/2026.02/updates.20260226.0635.gz",
		OriginalSHA256:    "20d7bb85dfe0345f71c14057ecd2900d5ee9c4b0e9a5bbb26ab79b9b3b195730",
		OriginalSize:      4_993_286,
		ReplacementSHA256: "44fec02db498bf1445808338e7f1f3af8809bdfe535584bad257e9d365d435fd",
		ReplacementSize:   4_092_451,
	},
}

type RouteEventStoreFile struct {
	Path      string `json:"path"`
	RowCount  int64  `json:"row_count"`
	SizeBytes int64  `json:"size_bytes"`
	SHA256    string `json:"sha256"`
}

type RouteEventPartitionManifest struct {
	SchemaVersion        string              `json:"schema_version"`
	ImportRunID          string              `json:"import_run_id"`
	DatasetID            string              `json:"dataset_id"`
	ArtifactIndex        int                 `json:"artifact_index"`
	Artifact             Artifact            `json:"artifact"`
	Role                 string              `json:"role"`
	InputIntegrityStatus string              `json:"input_integrity_status"`
	IngestTimeUTC        string              `json:"ingest_time_utc"`
	ParseTimeUTC         string              `json:"parse_time_utc"`
	PhysicalRecords      int64               `json:"physical_record_count"`
	RouteEvents          int64               `json:"route_event_count"`
	Announces            int64               `json:"announce_count"`
	Withdraws            int64               `json:"withdraw_count"`
	RIBSnapshots         int64               `json:"rib_snapshot_count"`
	PathCount            int64               `json:"path_count"`
	ParserWarnings       int64               `json:"parser_warning_count"`
	Records              RouteEventStoreFile `json:"raw_records"`
	Events               RouteEventStoreFile `json:"route_events"`
	Paths                RouteEventStoreFile `json:"as_paths"`
	ContentSHA256        string              `json:"content_sha256"`
}

type RouteEventStoreManifest struct {
	SchemaVersion       string                        `json:"schema_version"`
	Status              string                        `json:"status"`
	ImportRunID         string                        `json:"import_run_id"`
	DatasetID           string                        `json:"dataset_id"`
	CollectorID         string                        `json:"collector_id"`
	Source              string                        `json:"source"`
	SourceDatasetURI    string                        `json:"source_dataset_uri"`
	WindowStartUTC      string                        `json:"window_start_utc"`
	WindowEndExclusive  string                        `json:"window_end_exclusive_utc"`
	SelectionSHA256     string                        `json:"selection_sha256"`
	SelectionPath       string                        `json:"selection_path"`
	SourceManifestSHA   string                        `json:"source_manifest_sha256"`
	RepairArtifactCount int                           `json:"repair_artifact_count"`
	RepairProvenanceSHA string                        `json:"repair_provenance_sha256"`
	RepairArtifacts     []RouteEventRepairArtifact    `json:"repair_artifacts"`
	ImplementationID    string                        `json:"implementation_id"`
	ParserName          string                        `json:"parser_name"`
	ParserVersion       string                        `json:"parser_version"`
	ImporterName        string                        `json:"importer_name"`
	ImporterVersion     string                        `json:"importer_version"`
	ArtifactCount       int                           `json:"artifact_count"`
	PhysicalRecords     int64                         `json:"physical_record_count"`
	RouteEvents         int64                         `json:"route_event_count"`
	Announces           int64                         `json:"announce_count"`
	Withdraws           int64                         `json:"withdraw_count"`
	RIBSnapshots        int64                         `json:"rib_snapshot_count"`
	Partitions          []RouteEventPartitionManifest `json:"partitions"`
	ContentSHA256       string                        `json:"content_sha256"`
}

type routeEventStoreMarker struct {
	SchemaVersion    string `json:"schema_version"`
	ImportRunID      string `json:"import_run_id"`
	DatasetID        string `json:"dataset_id"`
	SelectionSHA256  string `json:"selection_sha256"`
	ImplementationID string `json:"implementation_id"`
	WindowStartUTC   string `json:"window_start_utc"`
	WindowEndUTC     string `json:"window_end_exclusive_utc"`
}

type rawMRTRecordRow struct {
	RecordOrdinal      uint32 `json:"record_ordinal"`
	MRTTimeUTC         string `json:"mrt_time_utc"`
	MRTType            uint16 `json:"mrt_type"`
	MRTSubtype         uint16 `json:"mrt_subtype"`
	UncompressedOffset int64  `json:"uncompressed_offset"`
	RecordLength       uint32 `json:"record_length"`
	RecordSHA256       string `json:"record_sha256"`
}

type routeEventRow struct {
	RouteEventID       string            `json:"route_event_id"`
	EventTimeUTC       string            `json:"event_time_utc"`
	RouteOriginatedUTC *string           `json:"route_originated_time_utc,omitempty"`
	VPID               string            `json:"vp_id"`
	VPPeerIP           string            `json:"vp_peer_ip"`
	VPASN              uint32            `json:"vp_asn"`
	Action             string            `json:"action"`
	AFISAFI            string            `json:"afi_safi"`
	Prefix             string            `json:"prefix"`
	ASPathID           *string           `json:"as_path_id"`
	OriginASN          *uint32           `json:"origin_asn"`
	AttributeSHA256    *string           `json:"attribute_sha256"`
	RecordOrdinal      uint32            `json:"record_ordinal"`
	ElementOrdinal     uint32            `json:"element_ordinal"`
	QualityFlags       []string          `json:"quality_flags"`
	ParserWarnings     []string          `json:"parser_warnings,omitempty"`
	MissingReasons     map[string]string `json:"missing_reasons"`
}

type asPathRow struct {
	ASPathID     string         `json:"as_path_id"`
	ASPath       ASPathSnapshot `json:"as_path"`
	QualityFlags []string       `json:"quality_flags"`
}

type deterministicJSONLGzipWriter struct {
	file   *os.File
	buffer *bufio.Writer
	gzip   *gzip.Writer
	hash   *sha256Writer
	rows   int64
}

func newDeterministicJSONLGzipWriter(path string) (*deterministicJSONLGzipWriter, error) {
	file, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o640)
	if err != nil {
		return nil, err
	}
	hash := &sha256Writer{hash: sha256.New()}
	compressed := gzip.NewWriter(io.MultiWriter(file, hash))
	compressed.Header.ModTime = time.Unix(0, 0).UTC()
	compressed.Header.OS = 255
	return &deterministicJSONLGzipWriter{
		file: file, gzip: compressed,
		buffer: bufio.NewWriterSize(compressed, 1<<20), hash: hash,
	}, nil
}

func (writer *deterministicJSONLGzipWriter) Write(value any) error {
	raw, err := json.Marshal(value)
	if err != nil {
		return err
	}
	if _, err := writer.buffer.Write(raw); err != nil {
		return err
	}
	if err := writer.buffer.WriteByte('\n'); err != nil {
		return err
	}
	writer.rows++
	return nil
}

func (writer *deterministicJSONLGzipWriter) Close(relativePath string) (RouteEventStoreFile, error) {
	if err := writer.buffer.Flush(); err != nil {
		_ = writer.file.Close()
		return RouteEventStoreFile{}, err
	}
	if err := writer.gzip.Close(); err != nil {
		_ = writer.file.Close()
		return RouteEventStoreFile{}, err
	}
	if err := writer.file.Sync(); err != nil {
		_ = writer.file.Close()
		return RouteEventStoreFile{}, err
	}
	if err := writer.file.Close(); err != nil {
		return RouteEventStoreFile{}, err
	}
	return RouteEventStoreFile{
		Path: relativePath, RowCount: writer.rows,
		SizeBytes: writer.hash.bytes,
		SHA256:    hex.EncodeToString(writer.hash.hash.Sum(nil)),
	}, nil
}

func (writer *deterministicJSONLGzipWriter) Abort() {
	if writer == nil || writer.file == nil {
		return
	}
	_ = writer.buffer.Flush()
	_ = writer.gzip.Close()
	_ = writer.file.Close()
	writer.file = nil
}

func canonicalTimeFromMicros(micros int64) string {
	return time.Unix(0, micros*1_000).UTC().Format("2006-01-02T15:04:05.999999Z")
}

func operationTimeUTC() string {
	return time.Now().UTC().Truncate(time.Microsecond).Format(
		"2006-01-02T15:04:05.999999Z",
	)
}

func routeEventID(fileSHA256 string, recordOrdinal, elementOrdinal uint32) string {
	identity := fmt.Sprintf(
		`{"schema":"route_event_id_v1","file_sha256":%q,"record_ordinal":%d,"element_ordinal":%d}`,
		fileSHA256, recordOrdinal, elementOrdinal,
	)
	digest := sha256.Sum256([]byte(identity))
	return "rte_v1_" + hex.EncodeToString(digest[:])[:32]
}

func asPathIdentity(path ASPathSnapshot) (string, error) {
	raw, err := json.Marshal(struct {
		Schema   string          `json:"schema"`
		Segments []ASPathSegment `json:"segments"`
	}{Schema: "as_path_id_v1", Segments: path.Segments})
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(raw)
	return "asp_v1_" + hex.EncodeToString(digest[:]), nil
}

func pathQuality(path ASPathSnapshot, warnings []string) []string {
	flags := make(map[string]struct{})
	if len(path.Segments) == 0 {
		flags["empty_as_path"] = struct{}{}
	}
	for _, segment := range path.Segments {
		switch segment.SegmentType {
		case asSetSegment:
			flags["as_set_present"] = struct{}{}
		case confedSequenceSegment, confedSetSegment:
			flags["confederation_segment_present"] = struct{}{}
		}
	}
	if !path.Origin().Known {
		flags["origin_ambiguous"] = struct{}{}
	}
	if len(warnings) > 0 {
		flags["parser_warning"] = struct{}{}
	}
	result := make([]string, 0, len(flags))
	for flag := range flags {
		result = append(result, flag)
	}
	sort.Strings(result)
	return result
}

func sortedUniqueStrings(values []string) []string {
	unique := make(map[string]struct{}, len(values))
	for _, value := range values {
		if value != "" {
			unique[value] = struct{}{}
		}
	}
	result := make([]string, 0, len(unique))
	for value := range unique {
		result = append(result, value)
	}
	sort.Strings(result)
	return result
}

func routeAction(value uint8) (string, error) {
	switch value {
	case actionAnnounce:
		return "announce", nil
	case actionWithdraw:
		return "withdraw", nil
	case actionRIBSnapshot:
		return "rib_snapshot", nil
	default:
		return "", fmt.Errorf("unsupported route action %d", value)
	}
}

func routeAFISAFI(value uint8) (string, error) {
	switch value {
	case 4:
		return "ipv4_unicast", nil
	case 6:
		return "ipv6_unicast", nil
	default:
		return "", fmt.Errorf("unsupported route AFI %d", value)
	}
}

type routeEventPartitionBuilder struct {
	artifact       Artifact
	artifactIndex  int
	role           string
	importRunID    string
	datasetID      string
	records        *deterministicJSONLGzipWriter
	events         *deterministicJSONLGzipWriter
	paths          map[string]asPathRow
	physical       int64
	routeEvents    int64
	announces      int64
	withdraws      int64
	ribSnapshots   int64
	parserWarnings int64
}

func (builder *routeEventPartitionBuilder) addPath(
	path ASPathSnapshot,
	warnings []string,
) (*string, []string, error) {
	id, err := asPathIdentity(path)
	if err != nil {
		return nil, nil, err
	}
	dictionaryFlags := pathQuality(path, nil)
	eventFlags := pathQuality(path, warnings)
	row := asPathRow{
		ASPathID: id, ASPath: path, QualityFlags: dictionaryFlags,
	}
	if existing, found := builder.paths[id]; found {
		existingRaw, _ := json.Marshal(existing)
		rowRaw, _ := json.Marshal(row)
		if string(existingRaw) != string(rowRaw) {
			return nil, nil, fmt.Errorf("AS_PATH content identity collision: %s", id)
		}
	} else {
		builder.paths[id] = row
	}
	return &id, eventFlags, nil
}

func (builder *routeEventPartitionBuilder) writeParsedEvent(
	event ParsedEvent,
	originatedUTC *string,
) error {
	action, err := routeAction(event.Action)
	if err != nil {
		return err
	}
	afiSafi, err := routeAFISAFI(event.Key.AFI)
	if err != nil {
		return err
	}
	var pathID *string
	qualityFlags := make([]string, 0)
	missing := make(map[string]string)
	var origin *uint32
	if action == "withdraw" {
		missing["as_path"] = "not_applicable"
		missing["origin_asn"] = "not_applicable"
	} else if event.ASPath == nil {
		return fmt.Errorf("%s event is missing AS_PATH", action)
	} else {
		pathID, qualityFlags, err = builder.addPath(*event.ASPath, event.PathWarnings)
		if err != nil {
			return err
		}
		if event.OriginKnown {
			value := event.OriginASN
			origin = &value
		} else {
			missing["origin_asn"] = "not_observed"
		}
	}
	if len(event.PathWarnings) > 0 {
		builder.parserWarnings++
	}
	var attributeSHA *string
	if event.AttributeSHA256 != "" {
		value := event.AttributeSHA256
		attributeSHA = &value
	}
	row := routeEventRow{
		RouteEventID:       routeEventID(builder.artifact.FileSHA256, event.RecordOrdinal, event.ElementOrdinal),
		EventTimeUTC:       canonicalTimeFromMicros(event.EventMicros),
		RouteOriginatedUTC: originatedUTC,
		VPID:               VPIdentifier(event.Key.PeerIP, event.Key.PeerASN),
		VPPeerIP:           event.Key.PeerIP.String(),
		VPASN:              event.Key.PeerASN,
		Action:             action,
		AFISAFI:            afiSafi,
		Prefix:             event.Key.Prefix.String(),
		ASPathID:           pathID,
		OriginASN:          origin,
		AttributeSHA256:    attributeSHA,
		RecordOrdinal:      event.RecordOrdinal,
		ElementOrdinal:     event.ElementOrdinal,
		QualityFlags:       qualityFlags,
		ParserWarnings:     sortedUniqueStrings(event.PathWarnings),
		MissingReasons:     missing,
	}
	if err := builder.events.Write(row); err != nil {
		return err
	}
	builder.routeEvents++
	switch action {
	case "announce":
		builder.announces++
	case "withdraw":
		builder.withdraws++
	case "rib_snapshot":
		builder.ribSnapshots++
	}
	return nil
}

func (builder *routeEventPartitionBuilder) writeRIBRecord(
	record MRTRecordEvidence,
	peers []peer,
	recordOrdinal uint32,
) error {
	var afi uint8
	switch record.Subtype {
	case ribIPv4Unicast:
		afi = 4
	case ribIPv6Unicast:
		afi = 6
	default:
		return fmt.Errorf("unsupported TABLE_DUMP_V2 subtype %d", record.Subtype)
	}
	cursor := cursor{raw: record.Payload, field: "route-event-RIB"}
	if _, err := cursor.u32("sequence"); err != nil {
		return err
	}
	bits, err := cursor.u8("prefix-length")
	if err != nil {
		return err
	}
	rawPrefix, err := cursor.take((int(bits)+7)/8, "prefix")
	if err != nil {
		return err
	}
	prefix, err := parsePrefix(rawPrefix, bits, afi)
	if err != nil {
		return err
	}
	entryCount, err := cursor.u16("entry-count")
	if err != nil {
		return err
	}
	for index := 0; index < int(entryCount); index++ {
		peerIndex, err := cursor.u16("peer-index")
		if err != nil {
			return err
		}
		if int(peerIndex) >= len(peers) {
			return fmt.Errorf("route-event RIB peer index out of range")
		}
		originated, err := cursor.u32("originated-time")
		if err != nil {
			return err
		}
		attributeLength, err := cursor.u16("attribute-length")
		if err != nil {
			return err
		}
		attributes, err := cursor.take(int(attributeLength), "attributes")
		if err != nil {
			return err
		}
		parsed, err := parseRIBAttributes(attributes, 4)
		if err != nil {
			return err
		}
		attributeDigest := sha256.Sum256(attributes)
		path := parsed.Path
		originatedText := time.Unix(int64(originated), 0).UTC().Format(time.RFC3339)
		event := ParsedEvent{
			Key: RouteKey{
				PeerIP: peers[peerIndex].IP, PeerASN: peers[peerIndex].ASN,
				AFI: afi, Prefix: prefix,
			},
			Action:          actionRIBSnapshot,
			OriginKnown:     parsed.Origin.Known,
			OriginASN:       parsed.Origin.ASN,
			ASPath:          &path,
			AttributeSHA256: hex.EncodeToString(attributeDigest[:]),
			PathWarnings:    append([]string(nil), parsed.PathWarnings...),
			EventMicros:     int64(record.Timestamp) * 1_000_000,
			ArtifactIndex:   uint16(builder.artifactIndex),
			RecordOrdinal:   recordOrdinal,
			ElementOrdinal:  uint32(index),
		}
		if err := builder.writeParsedEvent(event, &originatedText); err != nil {
			return err
		}
	}
	return cursor.finish()
}

func (builder *routeEventPartitionBuilder) consumeArtifact(rawRoot string) error {
	offset := int64(0)
	return withVerifiedGzip(rawRoot, builder.artifact, func(reader io.Reader) error {
		var peers []peer
		for recordOrdinal := uint32(0); ; recordOrdinal++ {
			record, err := readMRTRecordEvidence(reader, offset)
			if err == io.EOF {
				break
			}
			if err != nil {
				return err
			}
			offset += int64(record.RecordLength)
			builder.physical++
			if err := builder.records.Write(rawMRTRecordRow{
				RecordOrdinal: recordOrdinal,
				MRTTimeUTC:    time.Unix(int64(record.Timestamp), 0).UTC().Format(time.RFC3339),
				MRTType:       record.MRTType, MRTSubtype: record.Subtype,
				UncompressedOffset: record.UncompressedOffset,
				RecordLength:       record.RecordLength, RecordSHA256: record.RecordSHA256,
			}); err != nil {
				return err
			}
			switch builder.role {
			case "rib":
				if record.MRTType != mrtTableDumpV2 {
					return fmt.Errorf("unsupported route-event RIB MRT type %d", record.MRTType)
				}
				if record.Subtype == peerIndexTable {
					peers, err = parsePeerIndex(record.Payload)
					if err != nil {
						return err
					}
					continue
				}
				if peers == nil {
					return fmt.Errorf("route-event RIB record before peer index table")
				}
				if err := builder.writeRIBRecord(record, peers, recordOrdinal); err != nil {
					return fmt.Errorf("RIB record %d: %w", recordOrdinal, err)
				}
			case "update":
				if record.MRTType != mrtBGP4MP && record.MRTType != mrtBGP4MPET {
					return fmt.Errorf("unsupported route-event UPDATE MRT type %d", record.MRTType)
				}
				peer, eventMicros, asnWidth, messageType, body, err := parseBGP4MP(
					record.Timestamp, record.MRTType, record.Subtype, record.Payload,
				)
				if err != nil {
					return fmt.Errorf("record %d: %w", recordOrdinal, err)
				}
				if messageType == 0 || messageType == 1 || messageType == 3 || messageType == 4 {
					continue
				}
				if messageType != 2 {
					return fmt.Errorf("unsupported BGP message type %d", messageType)
				}
				stats := UpdateParseStats{UnknownOptional: make(map[uint8]int64)}
				events, err := decodeUpdateEvents(
					peer, eventMicros, body, asnWidth,
					uint16(builder.artifactIndex), recordOrdinal, &stats,
				)
				if err != nil {
					return fmt.Errorf("record %d: %w", recordOrdinal, err)
				}
				slot, _ := time.Parse(time.RFC3339, builder.artifact.ArtifactTimeUTC)
				for _, event := range events {
					at := time.Unix(0, event.EventMicros*1_000).UTC()
					if at.Before(slot) || !at.Before(slot.Add(globalWindowSlot)) {
						return fmt.Errorf("record %d event outside artifact slot", recordOrdinal)
					}
					if err := builder.writeParsedEvent(event, nil); err != nil {
						return err
					}
				}
			default:
				return fmt.Errorf("unsupported route-event artifact role %q", builder.role)
			}
		}
		return nil
	})
}

func fileSHA256(path string) (string, int64, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", 0, err
	}
	defer file.Close()
	hash := sha256.New()
	bytes, err := io.Copy(hash, file)
	if err != nil {
		return "", 0, err
	}
	return hex.EncodeToString(hash.Sum(nil)), bytes, nil
}

func verifyRouteEventStoreFile(root string, meta RouteEventStoreFile) error {
	path := filepath.Join(root, filepath.FromSlash(meta.Path))
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 ||
		info.Size() != meta.SizeBytes {
		return fmt.Errorf("route-event store file identity mismatch: %s", path)
	}
	digest, bytes, err := fileSHA256(path)
	if err != nil {
		return err
	}
	if bytes != meta.SizeBytes || digest != meta.SHA256 {
		return fmt.Errorf("route-event store file SHA-256 mismatch: %s", path)
	}
	return nil
}

func routeEventPartitionContentSHA(manifest RouteEventPartitionManifest) string {
	value := struct {
		SchemaVersion        string              `json:"schema_version"`
		ImportRunID          string              `json:"import_run_id"`
		DatasetID            string              `json:"dataset_id"`
		ArtifactIndex        int                 `json:"artifact_index"`
		Artifact             Artifact            `json:"artifact"`
		Role                 string              `json:"role"`
		InputIntegrityStatus string              `json:"input_integrity_status"`
		PhysicalRecords      int64               `json:"physical_record_count"`
		RouteEvents          int64               `json:"route_event_count"`
		Announces            int64               `json:"announce_count"`
		Withdraws            int64               `json:"withdraw_count"`
		RIBSnapshots         int64               `json:"rib_snapshot_count"`
		PathCount            int64               `json:"path_count"`
		ParserWarnings       int64               `json:"parser_warning_count"`
		Records              RouteEventStoreFile `json:"records"`
		Events               RouteEventStoreFile `json:"events"`
		Paths                RouteEventStoreFile `json:"paths"`
	}{
		SchemaVersion: manifest.SchemaVersion,
		ImportRunID:   manifest.ImportRunID, DatasetID: manifest.DatasetID,
		ArtifactIndex: manifest.ArtifactIndex, Artifact: manifest.Artifact,
		Role:                 manifest.Role,
		InputIntegrityStatus: manifest.InputIntegrityStatus,
		PhysicalRecords:      manifest.PhysicalRecords,
		RouteEvents:          manifest.RouteEvents,
		Announces:            manifest.Announces,
		Withdraws:            manifest.Withdraws,
		RIBSnapshots:         manifest.RIBSnapshots,
		PathCount:            manifest.PathCount,
		ParserWarnings:       manifest.ParserWarnings,
		Records:              manifest.Records,
		Events:               manifest.Events,
		Paths:                manifest.Paths,
	}
	raw, _ := json.Marshal(value)
	digest := sha256.Sum256(raw)
	return hex.EncodeToString(digest[:])
}

func loadAndVerifyRouteEventPartition(
	output string,
	index int,
	artifact Artifact,
	importRunID string,
	datasetID string,
) (RouteEventPartitionManifest, error) {
	var manifest RouteEventPartitionManifest
	path := filepath.Join(output, "partitions", fmt.Sprintf("%04d", index), "manifest.json")
	if _, err := readJSON(path, &manifest); err != nil {
		return manifest, err
	}
	if manifest.SchemaVersion != RouteEventPartitionVersion ||
		manifest.ImportRunID != importRunID || manifest.DatasetID != datasetID ||
		manifest.ArtifactIndex != index || manifest.Artifact != artifact ||
		manifest.ContentSHA256 != routeEventPartitionContentSHA(manifest) {
		return manifest, fmt.Errorf("route-event partition %d identity mismatch", index)
	}
	expectedRole := "update"
	if index == 0 {
		expectedRole = "rib"
	}
	if manifest.Role != expectedRole {
		return manifest, fmt.Errorf("route-event partition %d role mismatch", index)
	}
	if manifest.InputIntegrityStatus != RouteEventInputIntegrity {
		return manifest, fmt.Errorf("route-event partition %d input integrity mismatch", index)
	}
	ingestTime, ingestErr := time.Parse(time.RFC3339Nano, manifest.IngestTimeUTC)
	parseTime, parseErr := time.Parse(time.RFC3339Nano, manifest.ParseTimeUTC)
	if ingestErr != nil || parseErr != nil || parseTime.Before(ingestTime) {
		return manifest, fmt.Errorf(
			"route-event partition %d operation time mismatch", index,
		)
	}
	for _, meta := range []RouteEventStoreFile{manifest.Records, manifest.Events, manifest.Paths} {
		if err := verifyRouteEventStoreFile(output, meta); err != nil {
			return manifest, err
		}
	}
	if manifest.Records.RowCount != manifest.PhysicalRecords ||
		manifest.Events.RowCount != manifest.RouteEvents ||
		manifest.Paths.RowCount != manifest.PathCount ||
		manifest.Announces+manifest.Withdraws+manifest.RIBSnapshots != manifest.RouteEvents {
		return manifest, fmt.Errorf("route-event partition %d population mismatch", index)
	}
	return manifest, nil
}

func buildRouteEventPartition(
	rawRoot string,
	output string,
	index int,
	artifact Artifact,
	role string,
	importRunID string,
	datasetID string,
	resume bool,
) (RouteEventPartitionManifest, error) {
	finalDirectory := filepath.Join(output, "partitions", fmt.Sprintf("%04d", index))
	if _, err := os.Lstat(finalDirectory); err == nil {
		if !resume {
			return RouteEventPartitionManifest{}, fmt.Errorf(
				"route-event partition already exists: %s", finalDirectory,
			)
		}
		return loadAndVerifyRouteEventPartition(
			output, index, artifact, importRunID, datasetID,
		)
	} else if !os.IsNotExist(err) {
		return RouteEventPartitionManifest{}, err
	}
	tempDirectory := finalDirectory + ".tmp"
	if info, err := os.Lstat(tempDirectory); err == nil {
		if !resume || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return RouteEventPartitionManifest{}, fmt.Errorf(
				"route-event temporary partition exists: %s", tempDirectory,
			)
		}
		if err := os.RemoveAll(tempDirectory); err != nil {
			return RouteEventPartitionManifest{}, err
		}
	} else if !os.IsNotExist(err) {
		return RouteEventPartitionManifest{}, err
	}
	if err := os.MkdirAll(tempDirectory, 0o750); err != nil {
		return RouteEventPartitionManifest{}, err
	}
	ingestTimeUTC := operationTimeUTC()
	records, err := newDeterministicJSONLGzipWriter(filepath.Join(tempDirectory, "records.jsonl.gz"))
	if err != nil {
		return RouteEventPartitionManifest{}, err
	}
	recordsClosed := false
	defer func() {
		if !recordsClosed {
			records.Abort()
		}
	}()
	events, err := newDeterministicJSONLGzipWriter(filepath.Join(tempDirectory, "events.jsonl.gz"))
	if err != nil {
		return RouteEventPartitionManifest{}, err
	}
	eventsClosed := false
	defer func() {
		if !eventsClosed {
			events.Abort()
		}
	}()
	builder := &routeEventPartitionBuilder{
		artifact: artifact, artifactIndex: index, role: role,
		importRunID: importRunID, datasetID: datasetID,
		records: records, events: events, paths: make(map[string]asPathRow),
	}
	if err := builder.consumeArtifact(rawRoot); err != nil {
		return RouteEventPartitionManifest{}, err
	}
	partitionPath := filepath.ToSlash(filepath.Join("partitions", fmt.Sprintf("%04d", index)))
	recordMeta, err := records.Close(filepath.ToSlash(filepath.Join(partitionPath, "records.jsonl.gz")))
	if err != nil {
		return RouteEventPartitionManifest{}, err
	}
	recordsClosed = true
	eventMeta, err := events.Close(filepath.ToSlash(filepath.Join(partitionPath, "events.jsonl.gz")))
	if err != nil {
		return RouteEventPartitionManifest{}, err
	}
	eventsClosed = true
	pathWriter, err := newDeterministicJSONLGzipWriter(filepath.Join(tempDirectory, "paths.jsonl.gz"))
	if err != nil {
		return RouteEventPartitionManifest{}, err
	}
	pathWriterClosed := false
	defer func() {
		if !pathWriterClosed {
			pathWriter.Abort()
		}
	}()
	pathIDs := make([]string, 0, len(builder.paths))
	for id := range builder.paths {
		pathIDs = append(pathIDs, id)
	}
	sort.Strings(pathIDs)
	for _, id := range pathIDs {
		if err := pathWriter.Write(builder.paths[id]); err != nil {
			return RouteEventPartitionManifest{}, err
		}
	}
	pathMeta, err := pathWriter.Close(filepath.ToSlash(filepath.Join(partitionPath, "paths.jsonl.gz")))
	if err != nil {
		return RouteEventPartitionManifest{}, err
	}
	pathWriterClosed = true
	manifest := RouteEventPartitionManifest{
		SchemaVersion: RouteEventPartitionVersion,
		ImportRunID:   importRunID, DatasetID: datasetID,
		ArtifactIndex: index, Artifact: artifact, Role: role,
		InputIntegrityStatus: RouteEventInputIntegrity,
		IngestTimeUTC:        ingestTimeUTC, ParseTimeUTC: operationTimeUTC(),
		PhysicalRecords: builder.physical, RouteEvents: builder.routeEvents,
		Announces: builder.announces, Withdraws: builder.withdraws,
		RIBSnapshots: builder.ribSnapshots, PathCount: int64(len(builder.paths)),
		ParserWarnings: builder.parserWarnings,
		Records:        recordMeta, Events: eventMeta, Paths: pathMeta,
	}
	manifest.ContentSHA256 = routeEventPartitionContentSHA(manifest)
	if _, err := writeJSONImmutable(filepath.Join(tempDirectory, "manifest.json"), manifest); err != nil {
		return RouteEventPartitionManifest{}, err
	}
	if err := os.Rename(tempDirectory, finalDirectory); err != nil {
		return RouteEventPartitionManifest{}, err
	}
	return manifest, nil
}

func routeEventRepairArtifacts(
	selection GlobalWindowSelection,
) ([]RouteEventRepairArtifact, error) {
	byPath := make(map[string]Artifact, len(selection.Updates))
	for _, artifact := range selection.Updates {
		byPath[artifact.RelativePath] = artifact
	}
	repairs := make([]RouteEventRepairArtifact, 0, len(frozenRouteEventRepairs))
	for _, expected := range frozenRouteEventRepairs {
		replacement, ok := byPath[expected.RelativePath]
		if !ok || replacement.ArtifactType != "update" ||
			replacement.FileSHA256 != expected.ReplacementSHA256 ||
			replacement.SizeBytes != expected.ReplacementSize {
			return nil, fmt.Errorf(
				"repair replacement identity mismatch: %s", expected.RelativePath,
			)
		}
		repairs = append(repairs, RouteEventRepairArtifact{
			RelativePath:               expected.RelativePath,
			OriginalFileSHA256:         expected.OriginalSHA256,
			OriginalSizeBytes:          expected.OriginalSize,
			OriginalIntegrityStatus:    "gzip_failed_preserved_read_only",
			ReplacementArtifact:        replacement,
			ReplacementIntegrityStatus: "gzip_passed_and_stream_verified",
			Relationship:               "isolated_replacement_without_overwrite",
		})
	}
	sort.Slice(repairs, func(left, right int) bool {
		return repairs[left].RelativePath < repairs[right].RelativePath
	})
	return repairs, nil
}

func repairProvenanceSHA256(repairs []RouteEventRepairArtifact) string {
	raw, _ := json.Marshal(struct {
		SchemaVersion string                     `json:"schema_version"`
		Repairs       []RouteEventRepairArtifact `json:"repairs"`
	}{
		SchemaVersion: "rrc25-repair-provenance/v1",
		Repairs:       repairs,
	})
	digest := sha256.Sum256(raw)
	return hex.EncodeToString(digest[:])
}

func routeEventStoreProvenance(
	selection GlobalWindowSelection,
) ([]RouteEventRepairArtifact, string, error) {
	repairs, err := routeEventRepairArtifacts(selection)
	if err != nil {
		return nil, "", err
	}
	if len(repairs) != RouteEventRepairCount {
		return nil, "", fmt.Errorf(
			"expected %d repair provenance records, got %d",
			RouteEventRepairCount, len(repairs),
		)
	}
	return repairs, repairProvenanceSHA256(repairs), nil
}

func validateRouteEventImplementationID(value string) error {
	if len(value) != len("git:")+40 || value[:len("git:")] != "git:" {
		return fmt.Errorf("implementation id must be git:<40 lowercase hex>")
	}
	raw := value[len("git:"):]
	if raw != strings.ToLower(raw) {
		return fmt.Errorf("implementation id must be git:<40 lowercase hex>")
	}
	if _, err := hex.DecodeString(raw); err != nil {
		return fmt.Errorf("implementation id must be git:<40 lowercase hex>")
	}
	return nil
}

func routeEventStoreIdentity(
	selectionSHA, repairProvenanceSHA, implementationID string,
) (string, string) {
	identity := map[string]any{
		"schema_version":           RouteEventStoreVersion,
		"selection_sha256":         selectionSHA,
		"repair_provenance_sha256": repairProvenanceSHA,
		"implementation_id":        implementationID,
		"source_dataset_uri":       RouteEventSourceDatasetURI,
		"parser_name":              RouteEventParserName,
		"parser_version":           RouteEventParserVersion,
		"importer_name":            RouteEventImporterName,
		"importer_version":         RouteEventImporterVersion,
	}
	importRunID := stableID("run_v1_", identity, 32)
	datasetID := stableID("route_event_dataset_v1_", map[string]any{
		"import_run_id": importRunID, "collector_id": "rrc25",
	}, 32)
	return importRunID, datasetID
}

func validateRouteEventStoreSelection(selection GlobalWindowSelection) error {
	if selection.CollectorID != "rrc25" ||
		selection.WindowStartUTC != RouteEventWindowStartUTC ||
		selection.WindowEndExclusiveUTC != RouteEventWindowEndUTC ||
		len(selection.Updates) != RouteEventUpdateCount ||
		selection.RepairArtifactCount != RouteEventRepairCount {
		return fmt.Errorf(
			"RouteEvent store selection must be frozen rrc25 224-310 with 4320 updates and 2 repair artifacts",
		)
	}
	seenFileSHA := make(map[string]string, len(selection.Updates)+1)
	for _, artifact := range append([]Artifact{selection.RIB}, selection.Updates...) {
		if previous, found := seenFileSHA[artifact.FileSHA256]; found {
			return fmt.Errorf(
				"duplicate source file SHA-256 would duplicate RouteEvent identity: %s and %s",
				previous, artifact.RelativePath,
			)
		}
		seenFileSHA[artifact.FileSHA256] = artifact.RelativePath
	}
	_, _, err := routeEventStoreProvenance(selection)
	return err
}

func PreflightRouteEventStore(config RouteEventStoreConfig) (RouteEventStorePreflight, error) {
	if err := validateRouteEventImplementationID(config.ImplementationID); err != nil {
		return RouteEventStorePreflight{}, err
	}
	selection, selectionSHA, _, _, err := parseGlobalWindowSelection(config.SelectionPath)
	if err != nil {
		return RouteEventStorePreflight{}, err
	}
	if err := validateRouteEventStoreSelection(selection); err != nil {
		return RouteEventStorePreflight{}, err
	}
	if err := validateGlobalWindowFiles(config.RawRoot, selection); err != nil {
		return RouteEventStorePreflight{}, err
	}
	repairs, repairSHA, err := routeEventStoreProvenance(selection)
	if err != nil {
		return RouteEventStorePreflight{}, err
	}
	importRunID, datasetID := routeEventStoreIdentity(
		selectionSHA, repairSHA, config.ImplementationID,
	)
	return RouteEventStorePreflight{
		SchemaVersion: RouteEventStoreVersion,
		ImportRunID:   importRunID, DatasetID: datasetID,
		CollectorID:        "rrc25",
		SourceDatasetURI:   RouteEventSourceDatasetURI,
		WindowStartUTC:     selection.WindowStartUTC,
		WindowEndExclusive: selection.WindowEndExclusiveUTC,
		SelectionSHA256:    selectionSHA,
		ArtifactCount:      len(selection.Updates) + 1, UpdateCount: len(selection.Updates),
		RepairArtifactCount: selection.RepairArtifactCount,
		RepairProvenanceSHA: repairSHA,
		RepairArtifacts:     repairs,
		ImplementationID:    config.ImplementationID,
		ParserName:          RouteEventParserName, ParserVersion: RouteEventParserVersion,
		ImporterName: RouteEventImporterName, ImporterVersion: RouteEventImporterVersion,
		IdentityCheckMode: "preflight_stat_then_stream_sha256_during_parse",
	}, nil
}

func readExactSelection(path, expectedSHA string) ([]byte, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	digest := sha256.Sum256(raw)
	if hex.EncodeToString(digest[:]) != expectedSHA {
		return nil, fmt.Errorf("selection changed after validation: %s", path)
	}
	return raw, nil
}

func writeBytesImmutable(path string, raw []byte) error {
	if existing, err := os.ReadFile(path); err == nil {
		if !bytes.Equal(existing, raw) {
			return fmt.Errorf("immutable file mismatch: %s", path)
		}
		return nil
	} else if !os.IsNotExist(err) {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return err
	}
	temporary := path + ".tmp"
	if err := removeRegularTemp(temporary); err != nil {
		return err
	}
	file, err := os.OpenFile(temporary, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o640)
	if err != nil {
		return err
	}
	removeTemporary := true
	defer func() {
		_ = file.Close()
		if removeTemporary {
			_ = os.Remove(temporary)
		}
	}()
	if _, err := file.Write(raw); err != nil {
		return err
	}
	if err := file.Sync(); err != nil {
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	if err := os.Rename(temporary, path); err != nil {
		return err
	}
	removeTemporary = false
	return nil
}

func prepareRouteEventStoreOutput(
	config RouteEventStoreConfig,
	selection GlobalWindowSelection,
	selectionSHA string,
	importRunID string,
	datasetID string,
) (*routeEventStoreLock, error) {
	selectionRaw, err := readExactSelection(config.SelectionPath, selectionSHA)
	if err != nil {
		return nil, err
	}
	marker := routeEventStoreMarker{
		SchemaVersion: RouteEventStoreVersion,
		ImportRunID:   importRunID, DatasetID: datasetID,
		SelectionSHA256:  selectionSHA,
		ImplementationID: config.ImplementationID,
		WindowStartUTC:   selection.WindowStartUTC,
		WindowEndUTC:     selection.WindowEndExclusiveUTC,
	}
	created := false
	if info, err := os.Lstat(config.Output); err == nil {
		if !config.Resume || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return nil, fmt.Errorf("route-event output exists; use --resume for the same run")
		}
	} else if !os.IsNotExist(err) {
		return nil, err
	} else {
		if config.Resume {
			return nil, fmt.Errorf("cannot resume absent route-event output")
		}
		if err := os.MkdirAll(filepath.Join(config.Output, "partitions"), 0o750); err != nil {
			return nil, err
		}
		created = true
	}
	lock, err := acquireRouteEventStoreLock(config.Output)
	if err != nil {
		return nil, err
	}
	succeeded := false
	defer func() {
		if !succeeded {
			_ = lock.Close()
		}
	}()
	if created {
		if _, err := writeJSONImmutable(
			filepath.Join(config.Output, "RUNNING.json"), marker,
		); err != nil {
			return nil, err
		}
	} else {
		var existing routeEventStoreMarker
		if _, err := readJSON(
			filepath.Join(config.Output, "RUNNING.json"), &existing,
		); err != nil {
			return nil, err
		}
		if existing != marker {
			return nil, fmt.Errorf("route-event RUNNING identity mismatch")
		}
	}
	if err := writeBytesImmutable(
		filepath.Join(config.Output, "input-selection.json"), selectionRaw,
	); err != nil {
		return nil, err
	}
	succeeded = true
	return lock, nil
}

type routeEventStoreLock struct {
	file *os.File
}

func acquireRouteEventStoreLock(output string) (*routeEventStoreLock, error) {
	path := filepath.Join(output, ".writer.lock")
	file, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o640)
	if err != nil {
		return nil, err
	}
	if err := syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		_ = file.Close()
		return nil, fmt.Errorf("route-event output already has an active writer: %w", err)
	}
	return &routeEventStoreLock{file: file}, nil
}

func (lock *routeEventStoreLock) Close() error {
	if lock == nil || lock.file == nil {
		return nil
	}
	unlockErr := syscall.Flock(int(lock.file.Fd()), syscall.LOCK_UN)
	closeErr := lock.file.Close()
	lock.file = nil
	if unlockErr != nil {
		return unlockErr
	}
	return closeErr
}

func routeEventStoreContentSHA(manifest RouteEventStoreManifest) string {
	type partitionIdentity struct {
		ArtifactIndex int    `json:"artifact_index"`
		ContentSHA256 string `json:"content_sha256"`
	}
	partitions := make([]partitionIdentity, 0, len(manifest.Partitions))
	for _, partition := range manifest.Partitions {
		partitions = append(partitions, partitionIdentity{
			ArtifactIndex: partition.ArtifactIndex,
			ContentSHA256: partition.ContentSHA256,
		})
	}
	value := struct {
		SchemaVersion       string              `json:"schema_version"`
		Status              string              `json:"status"`
		ImportRunID         string              `json:"import_run_id"`
		DatasetID           string              `json:"dataset_id"`
		CollectorID         string              `json:"collector_id"`
		Source              string              `json:"source"`
		SourceDatasetURI    string              `json:"source_dataset_uri"`
		WindowStartUTC      string              `json:"window_start_utc"`
		WindowEndExclusive  string              `json:"window_end_exclusive_utc"`
		SelectionSHA256     string              `json:"selection_sha256"`
		SelectionPath       string              `json:"selection_path"`
		SourceManifestSHA   string              `json:"source_manifest_sha256"`
		RepairArtifactCount int                 `json:"repair_artifact_count"`
		RepairProvenanceSHA string              `json:"repair_provenance_sha256"`
		ImplementationID    string              `json:"implementation_id"`
		ParserName          string              `json:"parser_name"`
		ParserVersion       string              `json:"parser_version"`
		ImporterName        string              `json:"importer_name"`
		ImporterVersion     string              `json:"importer_version"`
		ArtifactCount       int                 `json:"artifact_count"`
		PhysicalRecords     int64               `json:"physical_record_count"`
		RouteEvents         int64               `json:"route_event_count"`
		Announces           int64               `json:"announce_count"`
		Withdraws           int64               `json:"withdraw_count"`
		RIBSnapshots        int64               `json:"rib_snapshot_count"`
		Partitions          []partitionIdentity `json:"partitions"`
	}{
		SchemaVersion:       manifest.SchemaVersion,
		Status:              manifest.Status,
		ImportRunID:         manifest.ImportRunID,
		DatasetID:           manifest.DatasetID,
		CollectorID:         manifest.CollectorID,
		Source:              manifest.Source,
		SourceDatasetURI:    manifest.SourceDatasetURI,
		WindowStartUTC:      manifest.WindowStartUTC,
		WindowEndExclusive:  manifest.WindowEndExclusive,
		SelectionSHA256:     manifest.SelectionSHA256,
		SelectionPath:       manifest.SelectionPath,
		SourceManifestSHA:   manifest.SourceManifestSHA,
		RepairArtifactCount: manifest.RepairArtifactCount,
		RepairProvenanceSHA: manifest.RepairProvenanceSHA,
		ImplementationID:    manifest.ImplementationID,
		ParserName:          manifest.ParserName,
		ParserVersion:       manifest.ParserVersion,
		ImporterName:        manifest.ImporterName,
		ImporterVersion:     manifest.ImporterVersion,
		ArtifactCount:       manifest.ArtifactCount,
		PhysicalRecords:     manifest.PhysicalRecords,
		RouteEvents:         manifest.RouteEvents,
		Announces:           manifest.Announces,
		Withdraws:           manifest.Withdraws,
		RIBSnapshots:        manifest.RIBSnapshots,
		Partitions:          partitions,
	}
	raw, _ := json.Marshal(value)
	digest := sha256.Sum256(raw)
	return hex.EncodeToString(digest[:])
}

func readIdenticalRouteEventStoreManifests(
	output string,
) (RouteEventStoreManifest, error) {
	var complete RouteEventStoreManifest
	completeRaw, err := readJSON(filepath.Join(output, "COMPLETE.json"), &complete)
	if err != nil {
		return complete, err
	}
	var published RouteEventStoreManifest
	publishedRaw, err := readJSON(filepath.Join(output, "manifest.json"), &published)
	if err != nil {
		return complete, err
	}
	if !bytes.Equal(completeRaw, publishedRaw) {
		return complete, fmt.Errorf(
			"route-event COMPLETE.json and manifest.json are not byte-identical",
		)
	}
	return complete, nil
}

func loadCompleteRouteEventStore(
	config RouteEventStoreConfig,
	selection GlobalWindowSelection,
	selectionSHA string,
	importRunID string,
	datasetID string,
) (RouteEventStoreManifest, error) {
	manifest, err := readIdenticalRouteEventStoreManifests(config.Output)
	if err != nil {
		return manifest, err
	}
	repairs, repairSHA, err := routeEventStoreProvenance(selection)
	if err != nil {
		return manifest, err
	}
	if manifest.SchemaVersion != RouteEventStoreVersion || manifest.Status != "complete" ||
		manifest.ImportRunID != importRunID || manifest.DatasetID != datasetID ||
		manifest.CollectorID != "rrc25" || manifest.Source != "ripe_ris" ||
		manifest.SourceDatasetURI != RouteEventSourceDatasetURI ||
		manifest.WindowStartUTC != RouteEventWindowStartUTC ||
		manifest.WindowEndExclusive != RouteEventWindowEndUTC ||
		manifest.SelectionSHA256 != selectionSHA || manifest.SelectionPath != "input-selection.json" ||
		manifest.SourceManifestSHA != selection.SourceManifestSHA256 ||
		manifest.RepairArtifactCount != RouteEventRepairCount ||
		manifest.RepairProvenanceSHA != repairSHA ||
		manifest.ImplementationID != config.ImplementationID ||
		len(manifest.RepairArtifacts) != len(repairs) ||
		repairProvenanceSHA256(manifest.RepairArtifacts) != repairSHA ||
		manifest.ParserName != RouteEventParserName ||
		manifest.ParserVersion != RouteEventParserVersion ||
		manifest.ImporterName != RouteEventImporterName ||
		manifest.ImporterVersion != RouteEventImporterVersion ||
		manifest.ArtifactCount != len(selection.Updates)+1 ||
		len(manifest.Partitions) != len(selection.Updates)+1 ||
		manifest.ContentSHA256 != routeEventStoreContentSHA(manifest) {
		return manifest, fmt.Errorf("complete route-event store identity mismatch")
	}
	artifacts := append([]Artifact{selection.RIB}, selection.Updates...)
	var physicalRecords int64
	var routeEvents int64
	var announces int64
	var withdraws int64
	var ribSnapshots int64
	for index, artifact := range artifacts {
		loaded, err := loadAndVerifyRouteEventPartition(
			config.Output, index, artifact, importRunID, datasetID,
		)
		if err != nil {
			return manifest, err
		}
		if loaded != manifest.Partitions[index] {
			return manifest, fmt.Errorf("complete partition %d digest mismatch", index)
		}
		physicalRecords += loaded.PhysicalRecords
		routeEvents += loaded.RouteEvents
		announces += loaded.Announces
		withdraws += loaded.Withdraws
		ribSnapshots += loaded.RIBSnapshots
	}
	if manifest.PhysicalRecords != physicalRecords ||
		manifest.RouteEvents != routeEvents ||
		manifest.Announces != announces ||
		manifest.Withdraws != withdraws ||
		manifest.RIBSnapshots != ribSnapshots ||
		announces+withdraws+ribSnapshots != routeEvents {
		return manifest, fmt.Errorf("complete route-event store population mismatch")
	}
	return manifest, nil
}

func BuildRouteEventStoreArtifact(
	config RouteEventStoreConfig,
	artifactIndex int,
) (RouteEventPartitionManifest, error) {
	if config.RawRoot == "" || config.SelectionPath == "" || config.Output == "" {
		return RouteEventPartitionManifest{}, fmt.Errorf(
			"raw root, selection and output are required",
		)
	}
	if err := validateRouteEventImplementationID(config.ImplementationID); err != nil {
		return RouteEventPartitionManifest{}, err
	}
	selection, selectionSHA, _, _, err := parseGlobalWindowSelection(config.SelectionPath)
	if err != nil {
		return RouteEventPartitionManifest{}, err
	}
	if err := validateRouteEventStoreSelection(selection); err != nil {
		return RouteEventPartitionManifest{}, err
	}
	if err := validateGlobalWindowFiles(config.RawRoot, selection); err != nil {
		return RouteEventPartitionManifest{}, err
	}
	artifacts := append([]Artifact{selection.RIB}, selection.Updates...)
	if artifactIndex < 0 || artifactIndex >= len(artifacts) {
		return RouteEventPartitionManifest{}, fmt.Errorf(
			"artifact index must be between 0 and %d", len(artifacts)-1,
		)
	}
	_, repairSHA, err := routeEventStoreProvenance(selection)
	if err != nil {
		return RouteEventPartitionManifest{}, err
	}
	importRunID, datasetID := routeEventStoreIdentity(
		selectionSHA, repairSHA, config.ImplementationID,
	)
	lock, err := prepareRouteEventStoreOutput(
		config, selection, selectionSHA, importRunID, datasetID,
	)
	if err != nil {
		return RouteEventPartitionManifest{}, err
	}
	defer lock.Close()
	role := "update"
	if artifactIndex == 0 {
		role = "rib"
	}
	return buildRouteEventPartition(
		config.RawRoot, config.Output, artifactIndex, artifacts[artifactIndex], role,
		importRunID, datasetID, config.Resume,
	)
}

func RunRouteEventStore(config RouteEventStoreConfig) (RouteEventStoreManifest, error) {
	if config.RawRoot == "" || config.SelectionPath == "" || config.Output == "" {
		return RouteEventStoreManifest{}, fmt.Errorf("raw root, selection and output are required")
	}
	if err := validateRouteEventImplementationID(config.ImplementationID); err != nil {
		return RouteEventStoreManifest{}, err
	}
	if config.Workers < 1 {
		return RouteEventStoreManifest{}, fmt.Errorf("workers must be positive")
	}
	selection, selectionSHA, _, _, err := parseGlobalWindowSelection(config.SelectionPath)
	if err != nil {
		return RouteEventStoreManifest{}, err
	}
	if err := validateRouteEventStoreSelection(selection); err != nil {
		return RouteEventStoreManifest{}, err
	}
	if err := validateGlobalWindowFiles(config.RawRoot, selection); err != nil {
		return RouteEventStoreManifest{}, err
	}
	repairs, repairSHA, err := routeEventStoreProvenance(selection)
	if err != nil {
		return RouteEventStoreManifest{}, err
	}
	importRunID, datasetID := routeEventStoreIdentity(
		selectionSHA, repairSHA, config.ImplementationID,
	)
	lock, err := prepareRouteEventStoreOutput(
		config, selection, selectionSHA, importRunID, datasetID,
	)
	if err != nil {
		return RouteEventStoreManifest{}, err
	}
	defer lock.Close()
	if config.Resume {
		if _, err := os.Lstat(filepath.Join(config.Output, "COMPLETE.json")); err == nil {
			return loadCompleteRouteEventStore(
				config, selection, selectionSHA, importRunID, datasetID,
			)
		} else if !os.IsNotExist(err) {
			return RouteEventStoreManifest{}, err
		}
	}
	type artifactJob struct {
		index    int
		artifact Artifact
		role     string
	}
	artifacts := make([]artifactJob, 0, len(selection.Updates)+1)
	artifacts = append(artifacts, artifactJob{index: 0, artifact: selection.RIB, role: "rib"})
	for index, artifact := range selection.Updates {
		artifacts = append(artifacts, artifactJob{
			index: index + 1, artifact: artifact, role: "update",
		})
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	type artifactResult struct {
		index    int
		manifest RouteEventPartitionManifest
		err      error
	}
	jobs := make(chan artifactJob)
	results := make(chan artifactResult, len(artifacts))
	var workers sync.WaitGroup
	for worker := 0; worker < config.Workers; worker++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			for job := range jobs {
				manifest, err := buildRouteEventPartition(
					config.RawRoot, config.Output, job.index, job.artifact, job.role,
					importRunID, datasetID, config.Resume,
				)
				results <- artifactResult{index: job.index, manifest: manifest, err: err}
				if err != nil {
					cancel()
					return
				}
			}
		}()
	}
	go func() {
		defer close(jobs)
		for _, job := range artifacts {
			select {
			case jobs <- job:
			case <-ctx.Done():
				return
			}
		}
	}()
	go func() {
		workers.Wait()
		close(results)
	}()
	partitions := make([]RouteEventPartitionManifest, len(artifacts))
	completed := 0
	var firstErr error
	for result := range results {
		if result.err != nil {
			if firstErr == nil {
				firstErr = fmt.Errorf("artifact %d: %w", result.index, result.err)
			}
			continue
		}
		partitions[result.index] = result.manifest
		completed++
		config.progress(fmt.Sprintf(
			"RouteEvent 分区已闭合 %d/%d artifact=%s events=%d",
			completed, len(artifacts), result.manifest.Artifact.RelativePath,
			result.manifest.RouteEvents,
		))
	}
	if firstErr != nil {
		return RouteEventStoreManifest{}, firstErr
	}
	if completed != len(artifacts) {
		return RouteEventStoreManifest{}, fmt.Errorf(
			"route-event partition population incomplete: %d/%d", completed, len(artifacts),
		)
	}
	manifest := RouteEventStoreManifest{
		SchemaVersion: RouteEventStoreVersion, Status: "complete",
		ImportRunID: importRunID, DatasetID: datasetID,
		CollectorID:         "rrc25",
		Source:              "ripe_ris",
		SourceDatasetURI:    RouteEventSourceDatasetURI,
		WindowStartUTC:      selection.WindowStartUTC,
		WindowEndExclusive:  selection.WindowEndExclusiveUTC,
		SelectionSHA256:     selectionSHA,
		SelectionPath:       "input-selection.json",
		SourceManifestSHA:   selection.SourceManifestSHA256,
		RepairArtifactCount: selection.RepairArtifactCount,
		RepairProvenanceSHA: repairSHA,
		RepairArtifacts:     repairs,
		ImplementationID:    config.ImplementationID,
		ParserName:          RouteEventParserName, ParserVersion: RouteEventParserVersion,
		ImporterName: RouteEventImporterName, ImporterVersion: RouteEventImporterVersion,
		ArtifactCount: len(partitions), Partitions: partitions,
	}
	for _, partition := range partitions {
		manifest.PhysicalRecords += partition.PhysicalRecords
		manifest.RouteEvents += partition.RouteEvents
		manifest.Announces += partition.Announces
		manifest.Withdraws += partition.Withdraws
		manifest.RIBSnapshots += partition.RIBSnapshots
	}
	manifest.ContentSHA256 = routeEventStoreContentSHA(manifest)
	if manifest.Announces+manifest.Withdraws+manifest.RIBSnapshots != manifest.RouteEvents {
		return RouteEventStoreManifest{}, fmt.Errorf("route-event store population mismatch")
	}
	if _, err := writeJSONImmutable(filepath.Join(config.Output, "manifest.json"), manifest); err != nil {
		return RouteEventStoreManifest{}, err
	}
	if _, err := writeJSONImmutable(filepath.Join(config.Output, "COMPLETE.json"), manifest); err != nil {
		return RouteEventStoreManifest{}, err
	}
	return manifest, nil
}

func ReadRouteEventRows(path string) ([]routeEventRow, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	decoded, err := gzip.NewReader(file)
	if err != nil {
		return nil, err
	}
	defer decoded.Close()
	result := make([]routeEventRow, 0)
	scanner := bufio.NewScanner(decoded)
	scanner.Buffer(make([]byte, 64<<10), 4<<20)
	for scanner.Scan() {
		var row routeEventRow
		if err := json.Unmarshal(scanner.Bytes(), &row); err != nil {
			return nil, err
		}
		result = append(result, row)
	}
	return result, scanner.Err()
}
