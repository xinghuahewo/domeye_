package replay

import (
	"bufio"
	"compress/gzip"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/netip"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"sync"
	"time"
)

const (
	RouteStateCheckpointVersion = "rrc25-route-state-checkpoint/v1"
	routeStateRecordBytes       = 145
)

var routeStateCheckpointMagic = [8]byte{'D', 'R', 'S', 'T', 'V', '1', 0, 0}

type RouteStateCheckpointShard struct {
	Shard       int    `json:"shard"`
	Path        string `json:"path"`
	RecordCount int64  `json:"record_count"`
	SizeBytes   int64  `json:"size_bytes"`
	SHA256      string `json:"sha256"`
}

type RouteStateCheckpointManifest struct {
	SchemaVersion               string                      `json:"schema_version"`
	Status                      string                      `json:"status"`
	CheckpointID                string                      `json:"checkpoint_id"`
	RouteStateDatasetID         string                      `json:"route_state_dataset_id"`
	CollectorID                 string                      `json:"collector_id"`
	WindowStartUTC              string                      `json:"window_start_utc"`
	WindowEndExclusiveUTC       string                      `json:"window_end_exclusive_utc"`
	SourceRouteEventDatasetID   string                      `json:"source_route_event_dataset_id"`
	SourceRouteEventContentSHA  string                      `json:"source_route_event_content_sha256"`
	ImplementationID            string                      `json:"implementation_id"`
	ProjectorName               string                      `json:"projector_name"`
	ProjectorVersion            string                      `json:"projector_version"`
	MappingVersion              string                      `json:"mapping_version"`
	MappingCompatibleSHA256     string                      `json:"mapping_compatible_sha256"`
	MappingRevisedSHA256        string                      `json:"mapping_revised_sha256"`
	DataThrough                 string                      `json:"data_through"`
	ProcessedSlot               int                         `json:"processed_slot"`
	ProcessedUpdateCount        int                         `json:"processed_update_count"`
	ProcessedRouteEventCount    int64                       `json:"processed_route_event_count"`
	RouteStateRecordCount       int64                       `json:"route_state_record_count"`
	VisibleRouteCount           int64                       `json:"visible_route_count"`
	WithdrawnRouteCount         int64                       `json:"withdrawn_route_count"`
	QualityPopulation           map[string]int64            `json:"quality_population"`
	StateDigest                 string                      `json:"state_digest"`
	ShardCount                  int                         `json:"shard_count"`
	Shards                      []RouteStateCheckpointShard `json:"shards"`
	RestoreRequiresSeedRIB      bool                        `json:"restore_requires_seed_rib"`
	RestoreRequiresPriorUpdates bool                        `json:"restore_requires_prior_updates"`
	ContentSHA256               string                      `json:"content_sha256"`
}

type RouteStateCheckpointIdentity struct {
	RouteStateDatasetID        string
	SourceRouteEventDatasetID  string
	SourceRouteEventContentSHA string
	ImplementationID           string
	ProjectorName              string
	ProjectorVersion           string
	MappingVersion             string
	MappingCompatibleSHA256    string
	MappingRevisedSHA256       string
	WindowStartUTC             string
	WindowEndExclusiveUTC      string
}

type routeStateCheckpointWriter struct {
	file       *os.File
	compressed *gzip.Writer
	buffer     *bufio.Writer
	hash       *sha256Writer
	count      int64
}

func newRouteStateCheckpointWriter(path string) (*routeStateCheckpointWriter, error) {
	file, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o640)
	if err != nil {
		return nil, err
	}
	hash := &sha256Writer{hash: sha256.New()}
	compressed, err := gzip.NewWriterLevel(io.MultiWriter(file, hash), gzip.BestSpeed)
	if err != nil {
		_ = file.Close()
		return nil, err
	}
	compressed.Header.ModTime = time.Unix(0, 0).UTC()
	compressed.Header.OS = 255
	buffer := bufio.NewWriterSize(compressed, 1<<20)
	if _, err := buffer.Write(routeStateCheckpointMagic[:]); err != nil {
		_ = compressed.Close()
		_ = file.Close()
		return nil, err
	}
	return &routeStateCheckpointWriter{
		file: file, compressed: compressed, buffer: buffer, hash: hash,
	}, nil
}

func encodeRouteStateRecord(
	key RouteStateKey,
	value RouteStateValue,
) ([routeStateRecordBytes]byte, error) {
	var raw [routeStateRecordBytes]byte
	at := 0
	raw[at] = key.Collector
	at++
	peer, peerLength, err := routeStateAddressBytes(key.Route.PeerIP)
	if err != nil {
		return raw, err
	}
	raw[at] = peerLength
	at++
	copy(raw[at:at+16], peer[:])
	at += 16
	binary.BigEndian.PutUint32(raw[at:at+4], key.Route.PeerASN)
	at += 4
	raw[at] = key.Route.AFI
	at++
	raw[at] = uint8(key.Route.Prefix.Bits())
	at++
	prefix, prefixLength, err := routeStateAddressBytes(key.Route.Prefix.Addr())
	if err != nil {
		return raw, err
	}
	raw[at] = prefixLength
	at++
	copy(raw[at:at+16], prefix[:])
	at += 16
	flags := uint8(0)
	if value.Visible {
		flags |= 1
	}
	if value.OriginKnown {
		flags |= 2
	}
	if value.ASPathKnown {
		flags |= 4
	}
	if value.AttributeKnown {
		flags |= 8
	}
	raw[at] = flags
	at++
	binary.BigEndian.PutUint32(raw[at:at+4], value.OriginASN)
	at += 4
	copy(raw[at:at+32], value.ASPathDigest[:])
	at += 32
	copy(raw[at:at+32], value.AttributeDigest[:])
	at += 32
	copy(raw[at:at+16], value.LastRouteEventID[:])
	at += 16
	binary.BigEndian.PutUint16(raw[at:at+2], value.LastArtifactIndex)
	at += 2
	binary.BigEndian.PutUint32(raw[at:at+4], value.LastRecordOrdinal)
	at += 4
	binary.BigEndian.PutUint32(raw[at:at+4], value.LastElementOrdinal)
	at += 4
	binary.BigEndian.PutUint64(raw[at:at+8], uint64(value.LastUpdatedMicros))
	at += 8
	raw[at] = value.QualityStatus
	at++
	if at != routeStateRecordBytes {
		return raw, fmt.Errorf("route-state checkpoint record size mismatch")
	}
	return raw, nil
}

func decodeRouteStateRecord(raw []byte) (RouteStateKey, RouteStateValue, error) {
	if len(raw) != routeStateRecordBytes {
		return RouteStateKey{}, RouteStateValue{}, fmt.Errorf("invalid route-state checkpoint record size")
	}
	at := 0
	collector := raw[at]
	at++
	peerLength := raw[at]
	at++
	peerRaw := raw[at : at+16]
	at += 16
	peerASN := binary.BigEndian.Uint32(raw[at : at+4])
	at += 4
	afi := raw[at]
	at++
	prefixBits := int(raw[at])
	at++
	prefixLength := raw[at]
	at++
	prefixRaw := raw[at : at+16]
	at += 16
	parseAddress := func(value []byte, length uint8) (netip.Addr, error) {
		switch length {
		case 4:
			var raw4 [4]byte
			copy(raw4[:], value[:4])
			return netip.AddrFrom4(raw4), nil
		case 16:
			var raw16 [16]byte
			copy(raw16[:], value[:16])
			return netip.AddrFrom16(raw16), nil
		default:
			return netip.Addr{}, fmt.Errorf("invalid checkpoint address length")
		}
	}
	peer, err := parseAddress(peerRaw, peerLength)
	if err != nil {
		return RouteStateKey{}, RouteStateValue{}, err
	}
	prefixAddress, err := parseAddress(prefixRaw, prefixLength)
	if err != nil {
		return RouteStateKey{}, RouteStateValue{}, err
	}
	prefix, err := prefixAddress.Prefix(prefixBits)
	if err != nil {
		return RouteStateKey{}, RouteStateValue{}, err
	}
	prefix = prefix.Masked()
	if collector != RouteStateCollectorRRC25 ||
		(afi != 4 && afi != 6) || (afi == 4) != prefixAddress.Is4() {
		return RouteStateKey{}, RouteStateValue{}, fmt.Errorf("invalid route-state checkpoint key")
	}
	flags := raw[at]
	at++
	value := RouteStateValue{
		Visible:        flags&1 != 0,
		OriginKnown:    flags&2 != 0,
		ASPathKnown:    flags&4 != 0,
		AttributeKnown: flags&8 != 0,
	}
	value.OriginASN = binary.BigEndian.Uint32(raw[at : at+4])
	at += 4
	copy(value.ASPathDigest[:], raw[at:at+32])
	at += 32
	copy(value.AttributeDigest[:], raw[at:at+32])
	at += 32
	copy(value.LastRouteEventID[:], raw[at:at+16])
	at += 16
	value.LastArtifactIndex = binary.BigEndian.Uint16(raw[at : at+2])
	at += 2
	value.LastRecordOrdinal = binary.BigEndian.Uint32(raw[at : at+4])
	at += 4
	value.LastElementOrdinal = binary.BigEndian.Uint32(raw[at : at+4])
	at += 4
	value.LastUpdatedMicros = int64(binary.BigEndian.Uint64(raw[at : at+8]))
	at += 8
	value.QualityStatus = raw[at]
	at++
	if at != routeStateRecordBytes || flags&^uint8(15) != 0 ||
		value.QualityStatus > routeStateQualityOrphanWithdraw {
		return RouteStateKey{}, RouteStateValue{}, fmt.Errorf("invalid route-state checkpoint value")
	}
	if !value.OriginKnown && value.OriginASN != 0 ||
		!value.ASPathKnown && value.ASPathDigest != [32]byte{} ||
		!value.AttributeKnown && value.AttributeDigest != [32]byte{} ||
		!value.Visible && (value.OriginKnown || value.ASPathKnown) {
		return RouteStateKey{}, RouteStateValue{}, fmt.Errorf("inconsistent route-state checkpoint value")
	}
	return RouteStateKey{
		Collector: collector,
		Route:     RouteKey{PeerIP: peer, PeerASN: peerASN, AFI: afi, Prefix: prefix},
	}, value, nil
}

func (writer *routeStateCheckpointWriter) Write(
	key RouteStateKey,
	value RouteStateValue,
) error {
	raw, err := encodeRouteStateRecord(key, value)
	if err != nil {
		return err
	}
	if _, err := writer.buffer.Write(raw[:]); err != nil {
		return err
	}
	writer.count++
	return nil
}

func (writer *routeStateCheckpointWriter) Close(
	shard int,
	relativePath string,
) (RouteStateCheckpointShard, error) {
	if err := writer.buffer.Flush(); err != nil {
		_ = writer.file.Close()
		return RouteStateCheckpointShard{}, err
	}
	if err := writer.compressed.Close(); err != nil {
		_ = writer.file.Close()
		return RouteStateCheckpointShard{}, err
	}
	if err := writer.file.Sync(); err != nil {
		_ = writer.file.Close()
		return RouteStateCheckpointShard{}, err
	}
	if err := writer.file.Close(); err != nil {
		return RouteStateCheckpointShard{}, err
	}
	return RouteStateCheckpointShard{
		Shard: shard, Path: relativePath, RecordCount: writer.count,
		SizeBytes: writer.hash.bytes,
		SHA256:    hex.EncodeToString(writer.hash.hash.Sum(nil)),
	}, nil
}

func routeStateKeyLess(left RouteStateKey, right RouteStateKey) bool {
	if left.Collector != right.Collector {
		return left.Collector < right.Collector
	}
	return globalCheckpointRouteKeyLess(left.Route, right.Route)
}

func sortedRouteStateKeys(
	state *RouteState,
	shardCount int,
) [][]RouteStateKey {
	counts := make([]int, shardCount)
	for key := range state.Routes {
		counts[shardFor(key.Route, shardCount)]++
	}
	keysByShard := make([][]RouteStateKey, shardCount)
	for shard, count := range counts {
		keysByShard[shard] = make([]RouteStateKey, 0, count)
	}
	for key := range state.Routes {
		shard := shardFor(key.Route, shardCount)
		keysByShard[shard] = append(keysByShard[shard], key)
	}
	workers := runtime.GOMAXPROCS(0)
	if workers > 8 {
		workers = 8
	}
	if workers > shardCount {
		workers = shardCount
	}
	work := make(chan int)
	var group sync.WaitGroup
	for worker := 0; worker < workers; worker++ {
		group.Add(1)
		go func() {
			defer group.Done()
			for shard := range work {
				sort.Slice(keysByShard[shard], func(i, j int) bool {
					return routeStateKeyLess(keysByShard[shard][i], keysByShard[shard][j])
				})
			}
		}()
	}
	for shard := range keysByShard {
		work <- shard
	}
	close(work)
	group.Wait()
	return keysByShard
}

func routeStateQualityPopulation(state *RouteState) map[string]int64 {
	result := map[string]int64{
		"clean": 0, "qualified": 0, "degraded": 0, "orphan_withdraw": 0,
	}
	for _, value := range state.Routes {
		result[routeStateQualityName(value.QualityStatus)]++
	}
	return result
}

func routeStateCheckpointContentSHA(manifest RouteStateCheckpointManifest) string {
	value := struct {
		SchemaVersion               string                      `json:"schema_version"`
		CheckpointID                string                      `json:"checkpoint_id"`
		RouteStateDatasetID         string                      `json:"route_state_dataset_id"`
		CollectorID                 string                      `json:"collector_id"`
		WindowStartUTC              string                      `json:"window_start_utc"`
		WindowEndExclusiveUTC       string                      `json:"window_end_exclusive_utc"`
		SourceRouteEventDatasetID   string                      `json:"source_route_event_dataset_id"`
		SourceRouteEventContentSHA  string                      `json:"source_route_event_content_sha256"`
		ImplementationID            string                      `json:"implementation_id"`
		ProjectorName               string                      `json:"projector_name"`
		ProjectorVersion            string                      `json:"projector_version"`
		MappingVersion              string                      `json:"mapping_version"`
		MappingCompatibleSHA256     string                      `json:"mapping_compatible_sha256"`
		MappingRevisedSHA256        string                      `json:"mapping_revised_sha256"`
		DataThrough                 string                      `json:"data_through"`
		ProcessedSlot               int                         `json:"processed_slot"`
		ProcessedUpdateCount        int                         `json:"processed_update_count"`
		ProcessedRouteEventCount    int64                       `json:"processed_route_event_count"`
		RouteStateRecordCount       int64                       `json:"route_state_record_count"`
		VisibleRouteCount           int64                       `json:"visible_route_count"`
		WithdrawnRouteCount         int64                       `json:"withdrawn_route_count"`
		QualityPopulation           map[string]int64            `json:"quality_population"`
		StateDigest                 string                      `json:"state_digest"`
		ShardCount                  int                         `json:"shard_count"`
		Shards                      []RouteStateCheckpointShard `json:"shards"`
		RestoreRequiresSeedRIB      bool                        `json:"restore_requires_seed_rib"`
		RestoreRequiresPriorUpdates bool                        `json:"restore_requires_prior_updates"`
	}{
		SchemaVersion: manifest.SchemaVersion, CheckpointID: manifest.CheckpointID,
		RouteStateDatasetID: manifest.RouteStateDatasetID, CollectorID: manifest.CollectorID,
		WindowStartUTC: manifest.WindowStartUTC, WindowEndExclusiveUTC: manifest.WindowEndExclusiveUTC,
		SourceRouteEventDatasetID:  manifest.SourceRouteEventDatasetID,
		SourceRouteEventContentSHA: manifest.SourceRouteEventContentSHA,
		ImplementationID:           manifest.ImplementationID, ProjectorName: manifest.ProjectorName,
		ProjectorVersion: manifest.ProjectorVersion, MappingVersion: manifest.MappingVersion,
		MappingCompatibleSHA256: manifest.MappingCompatibleSHA256,
		MappingRevisedSHA256:    manifest.MappingRevisedSHA256, DataThrough: manifest.DataThrough,
		ProcessedSlot: manifest.ProcessedSlot, ProcessedUpdateCount: manifest.ProcessedUpdateCount,
		ProcessedRouteEventCount: manifest.ProcessedRouteEventCount,
		RouteStateRecordCount:    manifest.RouteStateRecordCount,
		VisibleRouteCount:        manifest.VisibleRouteCount, WithdrawnRouteCount: manifest.WithdrawnRouteCount,
		QualityPopulation: manifest.QualityPopulation, StateDigest: manifest.StateDigest,
		ShardCount: manifest.ShardCount, Shards: manifest.Shards,
		RestoreRequiresSeedRIB:      manifest.RestoreRequiresSeedRIB,
		RestoreRequiresPriorUpdates: manifest.RestoreRequiresPriorUpdates,
	}
	raw, _ := json.Marshal(value)
	digest := sha256.Sum256(raw)
	return hex.EncodeToString(digest[:])
}

func WriteRouteStateCheckpoint(
	directory string,
	state *RouteState,
	identity RouteStateCheckpointIdentity,
	dataThrough string,
	processedSlot int,
	shardCount int,
) (RouteStateCheckpointManifest, error) {
	if state == nil || shardCount < 1 {
		return RouteStateCheckpointManifest{}, fmt.Errorf("route-state and positive shard count are required")
	}
	if _, err := os.Lstat(directory); err == nil {
		return RouteStateCheckpointManifest{}, fmt.Errorf("route-state checkpoint already exists")
	} else if !os.IsNotExist(err) {
		return RouteStateCheckpointManifest{}, err
	}
	temporary := directory + ".tmp"
	if _, err := os.Lstat(temporary); err == nil {
		return RouteStateCheckpointManifest{}, fmt.Errorf("unfinished route-state checkpoint exists")
	} else if !os.IsNotExist(err) {
		return RouteStateCheckpointManifest{}, err
	}
	if err := os.MkdirAll(temporary, 0o750); err != nil {
		return RouteStateCheckpointManifest{}, err
	}
	writers := make([]*routeStateCheckpointWriter, shardCount)
	for shard := 0; shard < shardCount; shard++ {
		writer, err := newRouteStateCheckpointWriter(filepath.Join(
			temporary, fmt.Sprintf("shard-%03d.bin.gz", shard),
		))
		if err != nil {
			return RouteStateCheckpointManifest{}, err
		}
		writers[shard] = writer
	}
	keysByShard := sortedRouteStateKeys(state, shardCount)
	for shard, keys := range keysByShard {
		for _, key := range keys {
			if err := writers[shard].Write(key, state.Routes[key]); err != nil {
				return RouteStateCheckpointManifest{}, err
			}
		}
		keysByShard[shard] = nil
	}
	shards := make([]RouteStateCheckpointShard, 0, shardCount)
	var recordCount int64
	for shard, writer := range writers {
		relative := fmt.Sprintf("shard-%03d.bin.gz", shard)
		meta, err := writer.Close(shard, relative)
		if err != nil {
			return RouteStateCheckpointManifest{}, err
		}
		shards = append(shards, meta)
		recordCount += meta.RecordCount
	}
	if recordCount != int64(len(state.Routes)) {
		return RouteStateCheckpointManifest{}, fmt.Errorf("route-state checkpoint population mismatch")
	}
	checkpointID := stableID("route_state_checkpoint_v1_", map[string]any{
		"route_state_dataset_id": identity.RouteStateDatasetID,
		"processed_slot":         processedSlot,
		"state_digest":           state.StateDigest.Hex(),
	}, 32)
	manifest := RouteStateCheckpointManifest{
		SchemaVersion: RouteStateCheckpointVersion, Status: "complete",
		CheckpointID: checkpointID, RouteStateDatasetID: identity.RouteStateDatasetID,
		CollectorID: "rrc25", WindowStartUTC: identity.WindowStartUTC,
		WindowEndExclusiveUTC:      identity.WindowEndExclusiveUTC,
		SourceRouteEventDatasetID:  identity.SourceRouteEventDatasetID,
		SourceRouteEventContentSHA: identity.SourceRouteEventContentSHA,
		ImplementationID:           identity.ImplementationID, ProjectorName: identity.ProjectorName,
		ProjectorVersion: identity.ProjectorVersion, MappingVersion: identity.MappingVersion,
		MappingCompatibleSHA256: identity.MappingCompatibleSHA256,
		MappingRevisedSHA256:    identity.MappingRevisedSHA256,
		DataThrough:             dataThrough, ProcessedSlot: processedSlot,
		ProcessedUpdateCount:     processedSlot,
		ProcessedRouteEventCount: state.ProcessedEventCount,
		RouteStateRecordCount:    int64(len(state.Routes)),
		VisibleRouteCount:        state.VisibleRouteCount,
		WithdrawnRouteCount:      int64(len(state.Routes)) - state.VisibleRouteCount,
		QualityPopulation:        routeStateQualityPopulation(state), StateDigest: state.StateDigest.Hex(),
		ShardCount: shardCount, Shards: shards,
		RestoreRequiresSeedRIB: false, RestoreRequiresPriorUpdates: false,
	}
	manifest.ContentSHA256 = routeStateCheckpointContentSHA(manifest)
	if _, err := writeJSONImmutable(filepath.Join(temporary, "manifest.json"), manifest); err != nil {
		return RouteStateCheckpointManifest{}, err
	}
	if _, err := writeJSONImmutable(filepath.Join(temporary, "COMPLETE.json"), manifest); err != nil {
		return RouteStateCheckpointManifest{}, err
	}
	if err := os.Rename(temporary, directory); err != nil {
		return RouteStateCheckpointManifest{}, err
	}
	return manifest, nil
}

func loadRouteStateCheckpointShard(
	path string,
	meta RouteStateCheckpointShard,
	expectedShard int,
	shardCount int,
	state *RouteState,
) error {
	sha, size, err := sha256File(path)
	if err != nil {
		return err
	}
	if sha != meta.SHA256 || size != meta.SizeBytes {
		return fmt.Errorf("route-state checkpoint shard identity mismatch")
	}
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	decoded, err := gzip.NewReader(file)
	if err != nil {
		return err
	}
	defer decoded.Close()
	magic := make([]byte, len(routeStateCheckpointMagic))
	if _, err := io.ReadFull(decoded, magic); err != nil {
		return err
	}
	if string(magic) != string(routeStateCheckpointMagic[:]) {
		return fmt.Errorf("route-state checkpoint magic mismatch")
	}
	raw := make([]byte, routeStateRecordBytes)
	var count int64
	for {
		_, err := io.ReadFull(decoded, raw)
		if err == io.EOF {
			break
		}
		if err != nil {
			return err
		}
		key, value, err := decodeRouteStateRecord(raw)
		if err != nil {
			return err
		}
		if shardFor(key.Route, shardCount) != expectedShard {
			return fmt.Errorf("route-state checkpoint key is in the wrong shard")
		}
		if _, exists := state.Routes[key]; exists {
			return fmt.Errorf("duplicate route-state checkpoint key")
		}
		state.Routes[key] = value
		state.StateDigest.Add(routeStateRecordDigest(key, value))
		if value.Visible {
			state.VisibleRouteCount++
		}
		count++
	}
	if err := decoded.Close(); err != nil {
		return err
	}
	if count != meta.RecordCount {
		return fmt.Errorf("route-state checkpoint shard population mismatch")
	}
	return nil
}

func LoadRouteStateCheckpoint(
	directory string,
	identity RouteStateCheckpointIdentity,
) (*RouteState, RouteStateCheckpointManifest, error) {
	var manifest RouteStateCheckpointManifest
	manifestRaw, err := readJSON(filepath.Join(directory, "manifest.json"), &manifest)
	if err != nil {
		return nil, manifest, err
	}
	var complete RouteStateCheckpointManifest
	completeRaw, err := readJSON(filepath.Join(directory, "COMPLETE.json"), &complete)
	if err != nil {
		return nil, manifest, err
	}
	if string(manifestRaw) != string(completeRaw) {
		return nil, manifest, fmt.Errorf("route-state checkpoint manifests are not byte-identical")
	}
	if manifest.SchemaVersion != RouteStateCheckpointVersion || manifest.Status != "complete" ||
		manifest.RouteStateDatasetID != identity.RouteStateDatasetID || manifest.CollectorID != "rrc25" ||
		manifest.WindowStartUTC != identity.WindowStartUTC ||
		manifest.WindowEndExclusiveUTC != identity.WindowEndExclusiveUTC ||
		manifest.SourceRouteEventDatasetID != identity.SourceRouteEventDatasetID ||
		manifest.SourceRouteEventContentSHA != identity.SourceRouteEventContentSHA ||
		manifest.ImplementationID != identity.ImplementationID ||
		manifest.ProjectorName != identity.ProjectorName || manifest.ProjectorVersion != identity.ProjectorVersion ||
		manifest.MappingVersion != identity.MappingVersion ||
		manifest.MappingCompatibleSHA256 != identity.MappingCompatibleSHA256 ||
		manifest.MappingRevisedSHA256 != identity.MappingRevisedSHA256 ||
		manifest.ShardCount < 1 || len(manifest.Shards) != manifest.ShardCount ||
		manifest.RestoreRequiresSeedRIB || manifest.RestoreRequiresPriorUpdates ||
		manifest.ContentSHA256 != routeStateCheckpointContentSHA(manifest) {
		return nil, manifest, fmt.Errorf("route-state checkpoint identity mismatch")
	}
	if manifest.ProcessedSlot < 0 || manifest.ProcessedSlot > RouteStateFinalSlot ||
		manifest.ProcessedUpdateCount != manifest.ProcessedSlot ||
		manifest.DataThrough != time.Date(2026, 2, 24, 0, 0, 0, 0, time.UTC).Add(
			time.Duration(manifest.ProcessedSlot)*5*time.Minute,
		).Format(time.RFC3339) ||
		manifest.CheckpointID != stableID("route_state_checkpoint_v1_", map[string]any{
			"route_state_dataset_id": identity.RouteStateDatasetID,
			"processed_slot":         manifest.ProcessedSlot,
			"state_digest":           manifest.StateDigest,
		}, 32) {
		return nil, manifest, fmt.Errorf("route-state checkpoint position mismatch")
	}
	state, err := NewRouteState(int(manifest.RouteStateRecordCount))
	if err != nil {
		return nil, manifest, err
	}
	for shard, meta := range manifest.Shards {
		if meta.Shard != shard || meta.Path != fmt.Sprintf("shard-%03d.bin.gz", shard) {
			return nil, manifest, fmt.Errorf("route-state checkpoint shard order mismatch")
		}
		if err := loadRouteStateCheckpointShard(
			filepath.Join(directory, filepath.FromSlash(meta.Path)), meta,
			shard, manifest.ShardCount, state,
		); err != nil {
			return nil, manifest, err
		}
	}
	state.ProcessedEventCount = manifest.ProcessedRouteEventCount
	quality := routeStateQualityPopulation(state)
	if int64(len(state.Routes)) != manifest.RouteStateRecordCount ||
		state.VisibleRouteCount != manifest.VisibleRouteCount ||
		int64(len(state.Routes))-state.VisibleRouteCount != manifest.WithdrawnRouteCount ||
		state.StateDigest.Hex() != manifest.StateDigest ||
		!mapsEqualStringInt64(quality, manifest.QualityPopulation) {
		return nil, manifest, fmt.Errorf("route-state checkpoint reconciliation failed")
	}
	return state, manifest, nil
}

func mapsEqualStringInt64(left, right map[string]int64) bool {
	if len(left) != len(right) {
		return false
	}
	for key, value := range left {
		if right[key] != value {
			return false
		}
	}
	return true
}
