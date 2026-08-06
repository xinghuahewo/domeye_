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
	"sort"
	"time"
)

var globalRIBMagic = [8]byte{'D', 'G', 'R', 'I', 'B', 'V', '1', 0}

type GlobalCheckpointShard struct {
	Shard       int    `json:"shard"`
	Path        string `json:"path"`
	RecordCount int64  `json:"record_count"`
	SizeBytes   int64  `json:"size_bytes"`
	SHA256      string `json:"sha256"`
}

type GlobalCheckpointCountry struct {
	CountryCode      string `json:"country_code"`
	CohortID         string `json:"cohort_id"`
	BaselinePrefixVP int64  `json:"baseline_prefix_vp"`
	BaselineIPv4     int64  `json:"baseline_ipv4_prefix_vp"`
	BaselineIPv6     int64  `json:"baseline_ipv6_prefix_vp"`
	MembershipDigest string `json:"membership_digest"`
}

type GlobalRIBCheckpointManifest struct {
	SchemaVersion    string                    `json:"schema_version"`
	EngineVersion    string                    `json:"engine_version"`
	CreatedAt        string                    `json:"created_at"`
	CollectorID      string                    `json:"collector_id"`
	SeedObservedAt   string                    `json:"seed_observed_at"`
	InputArtifact    Artifact                  `json:"input_artifact"`
	MappingVersion   string                    `json:"mapping_version"`
	MappingBaseHash  string                    `json:"mapping_base_sha256"`
	MappingDeltaHash string                    `json:"mapping_delta_sha256"`
	ShardCount       int                       `json:"shard_count"`
	RecordCount      int64                     `json:"record_count"`
	UniqueRouteCount int64                     `json:"unique_route_count"`
	StateDigest      string                    `json:"state_digest"`
	Conservation     GlobalConservation        `json:"conservation"`
	Countries        []GlobalCheckpointCountry `json:"countries"`
	Shards           []GlobalCheckpointShard   `json:"shards"`
}

type globalRIBShardWriter struct {
	shard      int
	file       *os.File
	compressed *gzip.Writer
	buffer     *bufio.Writer
	hash       *sha256Writer
	count      int64
}

func newGlobalRIBShardWriter(path string, shard int) (*globalRIBShardWriter, error) {
	file, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o640)
	if err != nil {
		return nil, err
	}
	hash := &sha256Writer{hash: sha256.New()}
	compressed, err := gzip.NewWriterLevel(io.MultiWriter(file, hash), gzip.BestSpeed)
	if err != nil {
		file.Close()
		return nil, err
	}
	buffer := bufio.NewWriterSize(compressed, 1<<20)
	if _, err := buffer.Write(globalRIBMagic[:]); err != nil {
		compressed.Close()
		file.Close()
		return nil, err
	}
	return &globalRIBShardWriter{
		shard: shard, file: file, compressed: compressed,
		buffer: buffer, hash: hash,
	}, nil
}

func (writer *globalRIBShardWriter) Write(
	key RouteKey,
	originKnown bool,
	originASN uint32,
	recordOrdinal uint32,
	elementOrdinal uint32,
) error {
	peerIP := addressBytes(key.PeerIP)
	prefixAddress := addressBytes(key.Prefix.Addr())
	var header [20]byte
	if originKnown {
		header[0] = 1
	}
	header[1] = key.AFI
	header[2] = uint8(key.Prefix.Bits())
	header[3] = uint8(len(peerIP))
	binary.BigEndian.PutUint32(header[4:8], key.PeerASN)
	binary.BigEndian.PutUint32(header[8:12], originASN)
	binary.BigEndian.PutUint32(header[12:16], recordOrdinal)
	binary.BigEndian.PutUint32(header[16:20], elementOrdinal)
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

func (writer *globalRIBShardWriter) Close(
	relativePath string,
) (GlobalCheckpointShard, error) {
	if err := writer.buffer.Flush(); err != nil {
		writer.file.Close()
		return GlobalCheckpointShard{}, err
	}
	if err := writer.compressed.Close(); err != nil {
		writer.file.Close()
		return GlobalCheckpointShard{}, err
	}
	if err := writer.file.Sync(); err != nil {
		writer.file.Close()
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

type GlobalRIBCheckpointWriter struct {
	root       string
	tempRoot   string
	shardCount int
	writers    []*globalRIBShardWriter
	closed     bool
}

func NewGlobalRIBCheckpointWriter(
	checkpointRoot string,
	shardCount int,
) (*GlobalRIBCheckpointWriter, error) {
	if shardCount < 1 {
		return nil, fmt.Errorf("checkpoint shard count must be positive")
	}
	root := filepath.Join(checkpointRoot, "rib")
	tempRoot := root + ".tmp"
	if _, err := os.Stat(root); err == nil {
		return nil, fmt.Errorf("global RIB checkpoint already exists")
	} else if !os.IsNotExist(err) {
		return nil, err
	}
	if _, err := os.Stat(tempRoot); err == nil {
		return nil, fmt.Errorf("unfinished global RIB checkpoint exists")
	} else if !os.IsNotExist(err) {
		return nil, err
	}
	if err := os.MkdirAll(tempRoot, 0o750); err != nil {
		return nil, err
	}
	result := &GlobalRIBCheckpointWriter{
		root: root, tempRoot: tempRoot, shardCount: shardCount,
		writers: make([]*globalRIBShardWriter, shardCount),
	}
	for shard := 0; shard < shardCount; shard++ {
		path := filepath.Join(tempRoot, fmt.Sprintf("shard-%03d.bin.gz", shard))
		shardWriter, err := newGlobalRIBShardWriter(path, shard)
		if err != nil {
			result.Abort()
			return nil, err
		}
		result.writers[shard] = shardWriter
	}
	return result, nil
}

func (writer *GlobalRIBCheckpointWriter) Write(
	key RouteKey,
	originKnown bool,
	originASN uint32,
	recordOrdinal uint32,
	elementOrdinal uint32,
) error {
	if writer == nil || writer.closed {
		return fmt.Errorf("global RIB checkpoint writer is closed")
	}
	shard := shardFor(key, writer.shardCount)
	return writer.writers[shard].Write(
		key, originKnown, originASN, recordOrdinal, elementOrdinal,
	)
}

func globalCheckpointCountries(
	state *GlobalReplayState,
) []GlobalCheckpointCountry {
	ids := make([]int, 0, len(state.Countries))
	for id, population := range state.Countries {
		if population.BaselinePrefixVP > 0 {
			ids = append(ids, int(id))
		}
	}
	sort.Ints(ids)
	result := make([]GlobalCheckpointCountry, 0, len(ids))
	for _, rawID := range ids {
		id := uint16(rawID)
		population := state.country(id)
		result = append(result, GlobalCheckpointCountry{
			CountryCode:      state.Mapping.CountryCode(id),
			CohortID:         state.CohortID(id),
			BaselinePrefixVP: population.BaselinePrefixVP,
			BaselineIPv4:     population.BaselineByAFI[0],
			BaselineIPv6:     population.BaselineByAFI[1],
			MembershipDigest: population.CohortDigest.Hex(),
		})
	}
	return result
}

func (writer *GlobalRIBCheckpointWriter) Finalize(
	state *GlobalReplayState,
	artifact Artifact,
) (GlobalRIBCheckpointManifest, error) {
	if writer == nil || writer.closed {
		return GlobalRIBCheckpointManifest{}, fmt.Errorf(
			"global RIB checkpoint writer is closed",
		)
	}
	shards := make([]GlobalCheckpointShard, 0, writer.shardCount)
	recordCount := int64(0)
	var firstErr error
	for shard, shardWriter := range writer.writers {
		relative := filepath.ToSlash(filepath.Join(
			"checkpoints", "rib", fmt.Sprintf("shard-%03d.bin.gz", shard),
		))
		meta, err := shardWriter.Close(relative)
		if err != nil && firstErr == nil {
			firstErr = err
		}
		if err == nil {
			shards = append(shards, meta)
			recordCount += meta.RecordCount
		}
	}
	writer.closed = true
	if firstErr != nil {
		_ = os.RemoveAll(writer.tempRoot)
		return GlobalRIBCheckpointManifest{}, firstErr
	}
	conservation, err := state.ValidateConservation()
	if err != nil {
		return GlobalRIBCheckpointManifest{}, err
	}
	manifest := GlobalRIBCheckpointManifest{
		SchemaVersion: "rrc25-global-rib-checkpoint/v1",
		EngineVersion: GlobalEngineVersion,
		CreatedAt:     time.Now().UTC().Format(time.RFC3339),
		CollectorID:   "rrc25", SeedObservedAt: func() string {
			if state.SeedObservedAt != "" {
				return state.SeedObservedAt
			}
			return CatchUpStartUTC
		}(),
		InputArtifact:    artifact,
		MappingVersion:   state.Mapping.MappingVersion,
		MappingBaseHash:  state.Mapping.CompatibleSHA256,
		MappingDeltaHash: state.Mapping.RevisedSHA256,
		ShardCount:       writer.shardCount, RecordCount: recordCount,
		UniqueRouteCount: int64(len(state.Routes)),
		StateDigest:      state.StateDigest.Hex(), Conservation: conservation,
		Countries: globalCheckpointCountries(state), Shards: shards,
	}
	if err := writeJSONAtomic(
		filepath.Join(writer.tempRoot, "manifest.json"), manifest,
	); err != nil {
		return GlobalRIBCheckpointManifest{}, err
	}
	if err := os.Rename(writer.tempRoot, writer.root); err != nil {
		return GlobalRIBCheckpointManifest{}, err
	}
	return manifest, nil
}

func (writer *GlobalRIBCheckpointWriter) Abort() {
	if writer == nil || writer.closed {
		return
	}
	writer.closed = true
	for _, shardWriter := range writer.writers {
		if shardWriter == nil {
			continue
		}
		_ = shardWriter.buffer.Flush()
		_ = shardWriter.compressed.Close()
		_ = shardWriter.file.Close()
	}
	_ = os.RemoveAll(writer.tempRoot)
}

func readGlobalRIBShard(
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
	magic := make([]byte, len(globalRIBMagic))
	if _, err := io.ReadFull(reader, magic); err != nil {
		compressed.Close()
		return 0, err
	}
	if string(magic) != string(globalRIBMagic[:]) {
		compressed.Close()
		return 0, fmt.Errorf("invalid global RIB checkpoint magic")
	}
	count := int64(0)
	for {
		var header [20]byte
		if _, err := io.ReadFull(reader, header[:]); err != nil {
			if err == io.EOF {
				break
			}
			compressed.Close()
			return 0, err
		}
		originKnown := header[0]&1 != 0
		afi := header[1]
		prefixBits := header[2]
		peerIPLength := int(header[3])
		addressLength := 4
		if afi == 6 {
			addressLength = 16
		} else if afi != 4 {
			compressed.Close()
			return 0, fmt.Errorf("invalid checkpoint AFI")
		}
		if peerIPLength != 4 && peerIPLength != 16 {
			compressed.Close()
			return 0, fmt.Errorf("invalid checkpoint peer IP length")
		}
		peerRaw := make([]byte, peerIPLength)
		if _, err := io.ReadFull(reader, peerRaw); err != nil {
			compressed.Close()
			return 0, err
		}
		prefixRaw := make([]byte, addressLength)
		if _, err := io.ReadFull(reader, prefixRaw); err != nil {
			compressed.Close()
			return 0, err
		}
		peerIP, err := parseAddress(peerRaw)
		if err != nil {
			compressed.Close()
			return 0, err
		}
		prefixAddress, err := parsePrefixAddress(prefixRaw, afi)
		if err != nil {
			compressed.Close()
			return 0, err
		}
		prefix := netip.PrefixFrom(prefixAddress, int(prefixBits)).Masked()
		key := RouteKey{
			PeerIP: peerIP, PeerASN: binary.BigEndian.Uint32(header[4:8]),
			AFI: afi, Prefix: prefix,
		}
		actualShard := shardFor(key, shardCount)
		if actualShard != meta.Shard {
			compressed.Close()
			return 0, fmt.Errorf(
				"global RIB checkpoint shard coordinate mismatch: expected=%d actual=%d key=%s record=%d",
				meta.Shard, actualShard, key.Canonical(), count,
			)
		}
		if err := state.Seed(
			key, originKnown, binary.BigEndian.Uint32(header[8:12]),
			binary.BigEndian.Uint32(header[12:16]),
			binary.BigEndian.Uint32(header[16:20]),
		); err != nil {
			compressed.Close()
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
		return 0, fmt.Errorf("global RIB checkpoint shard identity mismatch")
	}
	return count, nil
}

func LoadGlobalRIBCheckpoint(
	checkpointRoot string,
	mapping *GlobalCountryMapping,
	expectedArtifact Artifact,
) (*GlobalReplayState, GlobalRIBCheckpointManifest, error) {
	root := filepath.Join(checkpointRoot, "rib")
	var manifest GlobalRIBCheckpointManifest
	if _, err := readJSON(
		filepath.Join(root, "manifest.json"), &manifest,
	); err != nil {
		return nil, manifest, err
	}
	if manifest.SchemaVersion != "rrc25-global-rib-checkpoint/v1" ||
		manifest.EngineVersion != GlobalEngineVersion ||
		manifest.MappingVersion != mapping.MappingVersion ||
		manifest.InputArtifact.FileSHA256 != expectedArtifact.FileSHA256 ||
		manifest.InputArtifact.SizeBytes != expectedArtifact.SizeBytes ||
		manifest.ShardCount < 1 || len(manifest.Shards) != manifest.ShardCount {
		return nil, manifest, fmt.Errorf("global RIB checkpoint identity mismatch")
	}
	state, err := NewGlobalReplayState(mapping, int(manifest.UniqueRouteCount))
	if err != nil {
		return nil, manifest, err
	}
	state.SeedObservedAt = manifest.SeedObservedAt
	if parsed, err := time.Parse(time.RFC3339, manifest.SeedObservedAt); err == nil {
		state.SeedEventMicros = parsed.UnixMicro()
	} else {
		return nil, manifest, fmt.Errorf("global RIB seed time is invalid")
	}
	shards := append([]GlobalCheckpointShard(nil), manifest.Shards...)
	sort.Slice(shards, func(i, j int) bool { return shards[i].Shard < shards[j].Shard })
	recordCount := int64(0)
	for _, shard := range shards {
		if shard.Shard < 0 || shard.Shard >= manifest.ShardCount {
			return nil, manifest, fmt.Errorf(
				"global RIB checkpoint shard coordinate mismatch",
			)
		}
		expectedRelative := filepath.ToSlash(filepath.Join(
			"checkpoints", "rib", fmt.Sprintf("shard-%03d.bin.gz", shard.Shard),
		))
		if shard.Path != expectedRelative {
			return nil, manifest, fmt.Errorf("global RIB checkpoint shard path mismatch")
		}
		count, err := readGlobalRIBShard(
			filepath.Join(root, fmt.Sprintf("shard-%03d.bin.gz", shard.Shard)),
			shard, manifest.ShardCount, state,
		)
		if err != nil {
			return nil, manifest, fmt.Errorf(
				"load global RIB checkpoint shard %d: %w", shard.Shard, err,
			)
		}
		recordCount += count
	}
	conservation, err := state.ValidateConservation()
	if err != nil {
		return nil, manifest, err
	}
	if recordCount != manifest.RecordCount ||
		int64(len(state.Routes)) != manifest.UniqueRouteCount ||
		state.StateDigest.Hex() != manifest.StateDigest ||
		conservation.GlobalBaselinePrefixVP !=
			manifest.Conservation.GlobalBaselinePrefixVP {
		return nil, manifest, fmt.Errorf("global RIB checkpoint reconciliation failed")
	}
	return state, manifest, nil
}
