package replay

import (
	"bufio"
	"compress/gzip"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"io"
	"net/netip"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"sync"
)

var globalContinuationMagic = [8]byte{'D', 'G', 'S', 'T', 'V', '1', 0, 0}

const (
	GlobalContinuationCheckpointVersion       = "rrc25-global-route-state-checkpoint/v2"
	globalContinuationCheckpointVersionLegacy = "rrc25-global-route-state-checkpoint/v1"
)

type GlobalContinuationCheckpointManifest struct {
	SchemaVersion              string                    `json:"schema_version"`
	EngineVersion              string                    `json:"engine_version"`
	CreatedAt                  string                    `json:"created_at,omitempty"`
	IdentityTime               string                    `json:"identity_time,omitempty"`
	CollectorID                string                    `json:"collector_id"`
	RunID                      string                    `json:"run_id"`
	DatasetID                  string                    `json:"dataset_id"`
	Revision                   string                    `json:"revision"`
	MappingVersion             string                    `json:"mapping_version"`
	DataThrough                string                    `json:"data_through"`
	ProductSequence            int                       `json:"product_sequence"`
	ProcessedSlot              int                       `json:"processed_slot"`
	ProcessedUpdateCount       int                       `json:"processed_update_count"`
	PreviousProductSHA256      string                    `json:"previous_product_sha256"`
	SourceCheckpointSHA256     string                    `json:"source_checkpoint_sha256"`
	PreviousCheckpointSHA256   string                    `json:"previous_checkpoint_sha256,omitempty"`
	ShardCount                 int                       `json:"shard_count"`
	RecordCount                int64                     `json:"record_count"`
	StateDigest                string                    `json:"state_digest"`
	Conservation               GlobalConservation        `json:"conservation"`
	Countries                  []GlobalCheckpointCountry `json:"countries"`
	Shards                     []GlobalCheckpointShard   `json:"shards"`
	RestoreRequiresRIB         bool                      `json:"restore_requires_rib"`
	RestoreRequiresPriorUpdate bool                      `json:"restore_requires_prior_updates"`
}

type globalContinuationShardWriter struct {
	shard      int
	file       *os.File
	compressed *gzip.Writer
	buffer     *bufio.Writer
	hash       *sha256Writer
	count      int64
}

func newGlobalContinuationShardWriter(
	path string,
	shard int,
) (*globalContinuationShardWriter, error) {
	file, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o640)
	if err != nil {
		return nil, err
	}
	hash := &sha256Writer{hash: sha256.New()}
	compressed, err := gzip.NewWriterLevel(
		io.MultiWriter(file, hash), gzip.BestSpeed,
	)
	if err != nil {
		_ = file.Close()
		return nil, err
	}
	buffer := bufio.NewWriterSize(compressed, 1<<20)
	if _, err := buffer.Write(globalContinuationMagic[:]); err != nil {
		_ = compressed.Close()
		_ = file.Close()
		return nil, err
	}
	return &globalContinuationShardWriter{
		shard: shard, file: file, compressed: compressed,
		buffer: buffer, hash: hash,
	}, nil
}

func (writer *globalContinuationShardWriter) Write(
	key RouteKey,
	route globalRoute,
) error {
	peerIP := addressBytes(key.PeerIP)
	prefixAddress := addressBytes(key.Prefix.Addr())
	var header [46]byte
	if route.BaselineOriginKnown {
		header[0] |= 1
	}
	if route.CurrentPresent {
		header[0] |= 2
	}
	if route.CurrentOriginKnown {
		header[0] |= 4
	}
	if route.Dynamic {
		header[0] |= 8
	}
	header[1] = key.AFI
	header[2] = uint8(key.Prefix.Bits())
	header[3] = uint8(len(peerIP))
	binary.BigEndian.PutUint32(header[4:8], key.PeerASN)
	binary.BigEndian.PutUint32(header[8:12], route.BaselineOriginASN)
	binary.BigEndian.PutUint16(header[12:14], route.BaselineCountryID)
	binary.BigEndian.PutUint32(header[14:18], route.CurrentOriginASN)
	binary.BigEndian.PutUint16(header[18:20], route.CurrentCountryID)
	binary.BigEndian.PutUint32(header[20:24], route.RIBRecordOrdinal)
	binary.BigEndian.PutUint32(header[24:28], route.RIBElementOrdinal)
	binary.BigEndian.PutUint16(header[28:30], route.LastArtifactIndex)
	binary.BigEndian.PutUint32(header[30:34], route.LastRecordOrdinal)
	binary.BigEndian.PutUint32(header[34:38], route.LastElementOrdinal)
	binary.BigEndian.PutUint64(header[38:46], uint64(route.LastEventMicros))
	if _, err := writer.buffer.Write(header[:]); err != nil {
		return err
	}
	if _, err := writer.buffer.Write(peerIP); err != nil {
		return err
	}
	if _, err := writer.buffer.Write(prefixAddress); err != nil {
		return err
	}
	writer.count++
	return nil
}

func (writer *globalContinuationShardWriter) Close(
	relativePath string,
) (GlobalCheckpointShard, error) {
	if err := writer.buffer.Flush(); err != nil {
		_ = writer.file.Close()
		return GlobalCheckpointShard{}, err
	}
	if err := writer.compressed.Close(); err != nil {
		_ = writer.file.Close()
		return GlobalCheckpointShard{}, err
	}
	if err := writer.file.Sync(); err != nil {
		_ = writer.file.Close()
		return GlobalCheckpointShard{}, err
	}
	if err := writer.file.Close(); err != nil {
		return GlobalCheckpointShard{}, err
	}
	return GlobalCheckpointShard{
		Shard: writer.shard, Path: relativePath,
		RecordCount: writer.count, SizeBytes: writer.hash.bytes,
		SHA256: hex.EncodeToString(writer.hash.hash.Sum(nil)),
	}, nil
}

func globalCheckpointRouteKeyLess(left RouteKey, right RouteKey) bool {
	if order := left.PeerIP.Compare(right.PeerIP); order != 0 {
		return order < 0
	}
	if left.PeerASN != right.PeerASN {
		return left.PeerASN < right.PeerASN
	}
	if left.AFI != right.AFI {
		return left.AFI < right.AFI
	}
	if order := left.Prefix.Addr().Compare(right.Prefix.Addr()); order != 0 {
		return order < 0
	}
	return left.Prefix.Bits() < right.Prefix.Bits()
}

func sortedGlobalCheckpointKeys(
	state *GlobalReplayState,
	shardCount int,
) [][]RouteKey {
	counts := make([]int, shardCount)
	for key := range state.Routes {
		counts[shardFor(key, shardCount)]++
	}
	keysByShard := make([][]RouteKey, shardCount)
	for shard, count := range counts {
		keysByShard[shard] = make([]RouteKey, 0, count)
	}
	for key := range state.Routes {
		shard := shardFor(key, shardCount)
		keysByShard[shard] = append(keysByShard[shard], key)
	}
	workerCount := runtime.GOMAXPROCS(0)
	if workerCount > 8 {
		workerCount = 8
	}
	if workerCount > shardCount {
		workerCount = shardCount
	}
	work := make(chan int)
	var workers sync.WaitGroup
	for worker := 0; worker < workerCount; worker++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			for shard := range work {
				keys := keysByShard[shard]
				sort.Slice(keys, func(i int, j int) bool {
					return globalCheckpointRouteKeyLess(keys[i], keys[j])
				})
			}
		}()
	}
	for shard := range keysByShard {
		work <- shard
	}
	close(work)
	workers.Wait()
	return keysByShard
}

func WriteGlobalContinuationCheckpoint(
	checkpointDirectory string,
	state *GlobalReplayState,
	manifest GlobalContinuationCheckpointManifest,
) (GlobalContinuationCheckpointManifest, string, error) {
	if state == nil {
		return manifest, "", fmt.Errorf("global replay state is required")
	}
	if manifest.ShardCount < 1 {
		return manifest, "", fmt.Errorf("continuation checkpoint shard count must be positive")
	}
	if _, err := os.Lstat(checkpointDirectory); err == nil {
		return manifest, "", fmt.Errorf("continuation checkpoint already exists")
	} else if !os.IsNotExist(err) {
		return manifest, "", err
	}
	temp := checkpointDirectory + ".tmp"
	if _, err := os.Lstat(temp); err == nil {
		return manifest, "", fmt.Errorf("unfinished continuation checkpoint exists")
	} else if !os.IsNotExist(err) {
		return manifest, "", err
	}
	if err := os.MkdirAll(temp, 0o750); err != nil {
		return manifest, "", err
	}
	writers := make([]*globalContinuationShardWriter, manifest.ShardCount)
	closeWriters := func() {
		for _, writer := range writers {
			if writer == nil {
				continue
			}
			_ = writer.buffer.Flush()
			_ = writer.compressed.Close()
			_ = writer.file.Close()
		}
	}
	for shard := 0; shard < manifest.ShardCount; shard++ {
		writer, err := newGlobalContinuationShardWriter(
			filepath.Join(temp, fmt.Sprintf("shard-%03d.bin.gz", shard)),
			shard,
		)
		if err != nil {
			closeWriters()
			return manifest, "", err
		}
		writers[shard] = writer
	}
	keysByShard := sortedGlobalCheckpointKeys(state, manifest.ShardCount)
	for shard, keys := range keysByShard {
		for _, key := range keys {
			if err := writers[shard].Write(key, state.Routes[key]); err != nil {
				closeWriters()
				return manifest, "", err
			}
		}
		keysByShard[shard] = nil
	}
	shards := make([]GlobalCheckpointShard, 0, manifest.ShardCount)
	recordCount := int64(0)
	for shard, writer := range writers {
		meta, err := writer.Close(fmt.Sprintf("shard-%03d.bin.gz", shard))
		writers[shard] = nil
		if err != nil {
			closeWriters()
			return manifest, "", err
		}
		shards = append(shards, meta)
		recordCount += meta.RecordCount
	}
	conservation, err := state.ValidateConservation()
	if err != nil {
		return manifest, "", err
	}
	manifest.SchemaVersion = GlobalContinuationCheckpointVersion
	manifest.EngineVersion = GlobalEngineVersion
	manifest.CreatedAt = ""
	manifest.IdentityTime = manifest.DataThrough
	manifest.CollectorID = "rrc25"
	manifest.MappingVersion = state.Mapping.MappingVersion
	manifest.RecordCount = recordCount
	manifest.StateDigest = state.StateDigest.Hex()
	manifest.Conservation = conservation
	manifest.Countries = globalCheckpointCountries(state)
	manifest.Shards = shards
	manifest.RestoreRequiresRIB = false
	manifest.RestoreRequiresPriorUpdate = false
	if recordCount != int64(len(state.Routes)) {
		return manifest, "", fmt.Errorf("continuation checkpoint population mismatch")
	}
	if err := writeJSONAtomic(filepath.Join(temp, "manifest.json"), manifest); err != nil {
		return manifest, "", err
	}
	if err := os.Rename(temp, checkpointDirectory); err != nil {
		return manifest, "", err
	}
	hash, _, err := sha256File(filepath.Join(checkpointDirectory, "manifest.json"))
	return manifest, hash, err
}

func validateRestoredRoute(
	mapping *GlobalCountryMapping,
	route globalRoute,
) error {
	if route.Dynamic {
		if route.BaselineOriginKnown || route.BaselineOriginASN != 0 ||
			route.BaselineCountryID != 0 || !route.CurrentPresent {
			return fmt.Errorf("invalid dynamic route state")
		}
	} else if route.BaselineOriginKnown {
		if mapping.CountryID(route.BaselineOriginASN) != route.BaselineCountryID {
			return fmt.Errorf("baseline country mapping mismatch")
		}
	} else if route.BaselineOriginASN != 0 || route.BaselineCountryID != 0 {
		return fmt.Errorf("unknown baseline origin has non-zero identity")
	}
	if route.CurrentPresent {
		if route.CurrentOriginKnown {
			if mapping.CountryID(route.CurrentOriginASN) != route.CurrentCountryID {
				return fmt.Errorf("current country mapping mismatch")
			}
		} else if route.CurrentOriginASN != 0 || route.CurrentCountryID != 0 {
			return fmt.Errorf("unknown current origin has non-zero identity")
		}
	} else if route.CurrentOriginKnown || route.CurrentOriginASN != 0 ||
		route.CurrentCountryID != 0 {
		return fmt.Errorf("withdrawn route has current identity")
	}
	return nil
}

func (state *GlobalReplayState) restoreRoute(
	key RouteKey,
	route globalRoute,
) error {
	if _, exists := state.Routes[key]; exists {
		return fmt.Errorf("duplicate continuation checkpoint route")
	}
	if err := validateRestoredRoute(state.Mapping, route); err != nil {
		return err
	}
	offset, err := afiOffset(key.AFI)
	if err != nil {
		return err
	}
	state.Routes[key] = route
	state.StateDigest.Add(routeIdentityDigest(key, route))
	if !route.Dynamic {
		state.SeedRouteRows++
		country := state.country(route.BaselineCountryID)
		country.BaselinePrefixVP++
		country.BaselineByAFI[offset]++
		country.CohortDigest.Add(cohortMemberDigest(key, route))
		visible := globalBaselineVisible(route)
		if visible {
			country.VisiblePrefixVP++
			country.VisibleByAFI[offset]++
		}
		if route.BaselineOriginKnown {
			counterKey := globalASNKey{
				CountryID: route.BaselineCountryID,
				ASN:       route.BaselineOriginASN, AFI: key.AFI,
			}
			counter := state.Counters[counterKey]
			counter.Total++
			if visible {
				counter.Visible++
			}
			state.Counters[counterKey] = counter
		}
	}
	if route.CurrentPresent {
		current := state.country(route.CurrentCountryID)
		current.CurrentPrefixVP++
		current.CurrentByAFI[offset]++
	}
	return nil
}

func readGlobalContinuationShard(
	path string,
	meta GlobalCheckpointShard,
	shardCount int,
	state *GlobalReplayState,
) (int64, error) {
	file, err := os.Open(path)
	if err != nil {
		return 0, err
	}
	defer file.Close()
	hash := sha256.New()
	counting := &sha256Writer{hash: hash}
	tee := io.TeeReader(file, counting)
	compressed, err := gzip.NewReader(tee)
	if err != nil {
		return 0, err
	}
	reader := bufio.NewReaderSize(compressed, 1<<20)
	var magic [8]byte
	if _, err := io.ReadFull(reader, magic[:]); err != nil {
		_ = compressed.Close()
		return 0, err
	}
	if magic != globalContinuationMagic {
		_ = compressed.Close()
		return 0, fmt.Errorf("invalid continuation checkpoint magic")
	}
	count := int64(0)
	for {
		var header [46]byte
		if _, err := io.ReadFull(reader, header[:]); err != nil {
			if err == io.EOF {
				break
			}
			_ = compressed.Close()
			return 0, err
		}
		afi := header[1]
		addressLength := 4
		if afi == 6 {
			addressLength = 16
		} else if afi != 4 {
			_ = compressed.Close()
			return 0, fmt.Errorf("invalid continuation checkpoint AFI")
		}
		peerLength := int(header[3])
		if peerLength != 4 && peerLength != 16 {
			_ = compressed.Close()
			return 0, fmt.Errorf("invalid continuation checkpoint peer IP")
		}
		peerRaw := make([]byte, peerLength)
		prefixRaw := make([]byte, addressLength)
		if _, err := io.ReadFull(reader, peerRaw); err != nil {
			_ = compressed.Close()
			return 0, err
		}
		if _, err := io.ReadFull(reader, prefixRaw); err != nil {
			_ = compressed.Close()
			return 0, err
		}
		peerIP, err := parseAddress(peerRaw)
		if err != nil {
			_ = compressed.Close()
			return 0, err
		}
		prefixAddress, err := parsePrefixAddress(prefixRaw, afi)
		if err != nil {
			_ = compressed.Close()
			return 0, err
		}
		key := RouteKey{
			PeerIP:  peerIP,
			PeerASN: binary.BigEndian.Uint32(header[4:8]),
			AFI:     afi,
			Prefix: netip.PrefixFrom(
				prefixAddress, int(header[2]),
			).Masked(),
		}
		if shardFor(key, shardCount) != meta.Shard {
			_ = compressed.Close()
			return 0, fmt.Errorf("continuation checkpoint shard mismatch")
		}
		flags := header[0]
		route := globalRoute{
			BaselineOriginKnown: flags&1 != 0,
			CurrentPresent:      flags&2 != 0,
			CurrentOriginKnown:  flags&4 != 0,
			Dynamic:             flags&8 != 0,
			BaselineOriginASN:   binary.BigEndian.Uint32(header[8:12]),
			BaselineCountryID:   binary.BigEndian.Uint16(header[12:14]),
			CurrentOriginASN:    binary.BigEndian.Uint32(header[14:18]),
			CurrentCountryID:    binary.BigEndian.Uint16(header[18:20]),
			RIBRecordOrdinal:    binary.BigEndian.Uint32(header[20:24]),
			RIBElementOrdinal:   binary.BigEndian.Uint32(header[24:28]),
			LastArtifactIndex:   binary.BigEndian.Uint16(header[28:30]),
			LastRecordOrdinal:   binary.BigEndian.Uint32(header[30:34]),
			LastElementOrdinal:  binary.BigEndian.Uint32(header[34:38]),
			LastEventMicros:     int64(binary.BigEndian.Uint64(header[38:46])),
		}
		if err := state.restoreRoute(key, route); err != nil {
			_ = compressed.Close()
			return 0, err
		}
		count++
	}
	if err := compressed.Close(); err != nil {
		return 0, err
	}
	if _, err := io.Copy(io.Discard, tee); err != nil {
		return 0, err
	}
	if count != meta.RecordCount || counting.bytes != meta.SizeBytes ||
		hex.EncodeToString(hash.Sum(nil)) != meta.SHA256 {
		return 0, fmt.Errorf("continuation checkpoint shard identity mismatch")
	}
	return count, nil
}

func LoadGlobalContinuationCheckpoint(
	checkpointDirectory string,
	mapping *GlobalCountryMapping,
) (
	*GlobalReplayState,
	GlobalContinuationCheckpointManifest,
	string,
	error,
) {
	var manifest GlobalContinuationCheckpointManifest
	raw, err := readJSON(filepath.Join(checkpointDirectory, "manifest.json"), &manifest)
	if err != nil {
		return nil, manifest, "", err
	}
	digest := sha256.Sum256(raw)
	manifestSHA := hex.EncodeToString(digest[:])
	if (manifest.SchemaVersion != GlobalContinuationCheckpointVersion &&
		manifest.SchemaVersion != globalContinuationCheckpointVersionLegacy) ||
		manifest.EngineVersion != GlobalEngineVersion ||
		manifest.CollectorID != "rrc25" ||
		manifest.MappingVersion != mapping.MappingVersion ||
		manifest.Revision != GlobalDatasetRevision ||
		manifest.RunID == "" || manifest.DatasetID == "" ||
		manifest.DataThrough == "" || manifest.ProductSequence < 1 ||
		len(manifest.PreviousProductSHA256) != 64 ||
		len(manifest.SourceCheckpointSHA256) != 64 ||
		(manifest.PreviousCheckpointSHA256 != "" &&
			len(manifest.PreviousCheckpointSHA256) != 64) ||
		manifest.ShardCount < 1 ||
		len(manifest.Shards) != manifest.ShardCount ||
		manifest.RestoreRequiresRIB ||
		manifest.RestoreRequiresPriorUpdate {
		return nil, manifest, manifestSHA, fmt.Errorf(
			"continuation checkpoint identity mismatch",
		)
	}
	if manifest.SchemaVersion == GlobalContinuationCheckpointVersion &&
		(manifest.IdentityTime != manifest.DataThrough ||
			manifest.CreatedAt != "") {
		return nil, manifest, manifestSHA, fmt.Errorf(
			"continuation checkpoint deterministic identity mismatch",
		)
	}
	state, err := NewGlobalReplayState(mapping, int(manifest.RecordCount))
	if err != nil {
		return nil, manifest, manifestSHA, err
	}
	shards := append([]GlobalCheckpointShard(nil), manifest.Shards...)
	sort.Slice(shards, func(i, j int) bool {
		return shards[i].Shard < shards[j].Shard
	})
	recordCount := int64(0)
	for index, shard := range shards {
		if shard.Shard != index ||
			shard.Path != fmt.Sprintf("shard-%03d.bin.gz", index) {
			return nil, manifest, manifestSHA, fmt.Errorf(
				"continuation checkpoint shard sequence mismatch",
			)
		}
		count, err := readGlobalContinuationShard(
			filepath.Join(checkpointDirectory, shard.Path),
			shard, manifest.ShardCount, state,
		)
		if err != nil {
			return nil, manifest, manifestSHA, fmt.Errorf(
				"load continuation checkpoint shard %d: %w", index, err,
			)
		}
		recordCount += count
	}
	conservation, err := state.ValidateConservation()
	if err != nil {
		return nil, manifest, manifestSHA, err
	}
	if recordCount != manifest.RecordCount ||
		state.StateDigest.Hex() != manifest.StateDigest ||
		conservation != manifest.Conservation {
		return nil, manifest, manifestSHA, fmt.Errorf(
			"continuation checkpoint reconciliation failed",
		)
	}
	actualCountries := globalCheckpointCountries(state)
	if len(actualCountries) != len(manifest.Countries) {
		return nil, manifest, manifestSHA, fmt.Errorf(
			"continuation checkpoint country population mismatch",
		)
	}
	for index := range actualCountries {
		if actualCountries[index] != manifest.Countries[index] {
			return nil, manifest, manifestSHA, fmt.Errorf(
				"continuation checkpoint country identity mismatch",
			)
		}
	}
	return state, manifest, manifestSHA, nil
}
