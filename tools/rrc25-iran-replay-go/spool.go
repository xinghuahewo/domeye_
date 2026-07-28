package replay

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"hash/fnv"
	"io"
	"net/netip"
	"os"
	"path/filepath"
	"sort"
	"sync"
)

var spoolMagic = [8]byte{'D', 'R', 'S', 'P', 'V', '1', 0, 0}

type ShardSpoolMeta struct {
	Shard       int    `json:"shard"`
	Path        string `json:"path"`
	RecordCount int64  `json:"record_count"`
	SizeBytes   int64  `json:"size_bytes"`
	SHA256      string `json:"sha256"`
}

type SlotSpoolMeta struct {
	SchemaVersion string           `json:"schema_version"`
	EngineVersion string           `json:"engine_version"`
	ArtifactIndex int              `json:"artifact_index"`
	Artifact      Artifact         `json:"artifact"`
	Stats         UpdateParseStats `json:"stats"`
	Shards        []ShardSpoolMeta `json:"shards"`
}

type spoolWriter struct {
	path   string
	file   *os.File
	buffer *bufio.Writer
	hash   *sha256Writer
	count  int64
}

type sha256Writer struct {
	hash interface {
		Write([]byte) (int, error)
		Sum([]byte) []byte
	}
	bytes int64
}

func (writer *sha256Writer) Write(raw []byte) (int, error) {
	n, err := writer.hash.Write(raw)
	writer.bytes += int64(n)
	return n, err
}

func newSpoolWriter(path string) (*spoolWriter, error) {
	file, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o640)
	if err != nil {
		return nil, err
	}
	hash := &sha256Writer{hash: sha256.New()}
	buffer := bufio.NewWriterSize(io.MultiWriter(file, hash), 1<<20)
	if _, err := buffer.Write(spoolMagic[:]); err != nil {
		file.Close()
		return nil, err
	}
	return &spoolWriter{path: path, file: file, buffer: buffer, hash: hash}, nil
}

func addressBytes(address netip.Addr) []byte {
	if address.Is4() {
		value := address.As4()
		return value[:]
	}
	value := address.As16()
	return value[:]
}

func (writer *spoolWriter) Write(event ParsedEvent) error {
	peerIP := addressBytes(event.Key.PeerIP)
	prefixAddress := addressBytes(event.Key.Prefix.Addr())
	flags := uint8(0)
	if event.OriginKnown {
		flags = 1
	}
	header := make([]byte, 31)
	header[0] = event.Action
	header[1] = event.Key.AFI
	header[2] = uint8(event.Key.Prefix.Bits())
	header[3] = uint8(len(peerIP))
	header[4] = flags
	binary.BigEndian.PutUint32(header[5:9], event.Key.PeerASN)
	binary.BigEndian.PutUint64(header[9:17], uint64(event.EventMicros))
	binary.BigEndian.PutUint16(header[17:19], event.ArtifactIndex)
	binary.BigEndian.PutUint32(header[19:23], event.RecordOrdinal)
	binary.BigEndian.PutUint32(header[23:27], event.ElementOrdinal)
	binary.BigEndian.PutUint32(header[27:31], event.OriginASN)
	if _, err := writer.buffer.Write(header); err != nil {
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

func (writer *spoolWriter) Close(shard int, relativePath string) (ShardSpoolMeta, error) {
	if err := writer.buffer.Flush(); err != nil {
		writer.file.Close()
		return ShardSpoolMeta{}, err
	}
	if err := writer.file.Sync(); err != nil {
		writer.file.Close()
		return ShardSpoolMeta{}, err
	}
	if err := writer.file.Close(); err != nil {
		return ShardSpoolMeta{}, err
	}
	return ShardSpoolMeta{
		Shard:       shard,
		Path:        relativePath,
		RecordCount: writer.count,
		SizeBytes:   writer.hash.bytes,
		SHA256:      hex.EncodeToString(writer.hash.hash.Sum(nil)),
	}, nil
}

func shardFor(key RouteKey, shardCount int) int {
	hash := fnv.New64a()
	_, _ = hash.Write([]byte(key.Canonical()))
	return int(hash.Sum64() % uint64(shardCount))
}

func writeJSONAtomic(path string, value any) error {
	raw, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	raw = append(raw, '\n')
	temp := path + ".tmp"
	if err := os.WriteFile(temp, raw, 0o640); err != nil {
		return err
	}
	file, err := os.OpenFile(temp, os.O_RDWR, 0)
	if err != nil {
		return err
	}
	if err := file.Sync(); err != nil {
		file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	return os.Rename(temp, path)
}

func parseUpdateToSpool(
	ctx context.Context,
	rawRoot, spoolRoot string,
	artifact Artifact,
	index, shardCount int,
) (SlotSpoolMeta, error) {
	finalDirectory := filepath.Join(spoolRoot, fmt.Sprintf("%03d", index))
	metaPath := filepath.Join(finalDirectory, "meta.json")
	if raw, err := os.ReadFile(metaPath); err == nil {
		var existing SlotSpoolMeta
		if json.Unmarshal(raw, &existing) == nil &&
			existing.EngineVersion == EngineVersion &&
			existing.Artifact.FileSHA256 == artifact.FileSHA256 &&
			len(existing.Shards) == shardCount {
			return existing, nil
		}
		return SlotSpoolMeta{}, fmt.Errorf("existing spool metadata mismatch at slot %d", index)
	}
	tempDirectory := finalDirectory + ".tmp"
	if err := os.RemoveAll(tempDirectory); err != nil {
		return SlotSpoolMeta{}, err
	}
	if err := os.MkdirAll(tempDirectory, 0o750); err != nil {
		return SlotSpoolMeta{}, err
	}
	writers := make([]*spoolWriter, shardCount)
	for shard := 0; shard < shardCount; shard++ {
		path := filepath.Join(tempDirectory, fmt.Sprintf("shard-%03d.bin", shard))
		writer, err := newSpoolWriter(path)
		if err != nil {
			return SlotSpoolMeta{}, err
		}
		writers[shard] = writer
	}
	stats, parseErr := ParseUpdate(
		rawRoot,
		artifact,
		uint16(index),
		func(event ParsedEvent) error {
			select {
			case <-ctx.Done():
				return ctx.Err()
			default:
			}
			return writers[shardFor(event.Key, shardCount)].Write(event)
		},
	)
	shards := make([]ShardSpoolMeta, 0, shardCount)
	var closeErr error
	for shard, writer := range writers {
		if writer == nil {
			continue
		}
		relative := filepath.ToSlash(filepath.Join(
			"spool", fmt.Sprintf("%03d", index), fmt.Sprintf("shard-%03d.bin", shard),
		))
		meta, err := writer.Close(shard, relative)
		if err != nil && closeErr == nil {
			closeErr = err
		}
		shards = append(shards, meta)
	}
	if parseErr != nil {
		return SlotSpoolMeta{}, parseErr
	}
	if closeErr != nil {
		return SlotSpoolMeta{}, closeErr
	}
	meta := SlotSpoolMeta{
		SchemaVersion: "rrc25-update-shard-spool/v1",
		EngineVersion: EngineVersion,
		ArtifactIndex: index,
		Artifact:      artifact,
		Stats:         stats,
		Shards:        shards,
	}
	if err := writeJSONAtomic(filepath.Join(tempDirectory, "meta.json"), meta); err != nil {
		return SlotSpoolMeta{}, err
	}
	if err := os.Rename(tempDirectory, finalDirectory); err != nil {
		return SlotSpoolMeta{}, err
	}
	return meta, nil
}

func ParseAllUpdates(
	ctx context.Context,
	rawRoot, outputRoot string,
	artifacts []Artifact,
	workerCount, shardCount int,
	progress func(string),
) ([]SlotSpoolMeta, error) {
	if workerCount < 1 || shardCount < 1 {
		return nil, fmt.Errorf("worker and shard counts must be positive")
	}
	spoolRoot := filepath.Join(outputRoot, "spool")
	if err := os.MkdirAll(spoolRoot, 0o750); err != nil {
		return nil, err
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	type job struct {
		index    int
		artifact Artifact
	}
	type result struct {
		index int
		meta  SlotSpoolMeta
		err   error
	}
	jobs := make(chan job)
	results := make(chan result, len(artifacts))
	var workers sync.WaitGroup
	for worker := 0; worker < workerCount; worker++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			for job := range jobs {
				meta, err := parseUpdateToSpool(
					ctx, rawRoot, spoolRoot, job.artifact, job.index, shardCount,
				)
				results <- result{index: job.index, meta: meta, err: err}
				if err != nil {
					cancel()
					return
				}
			}
		}()
	}
	go func() {
		defer close(jobs)
		for index, artifact := range artifacts {
			select {
			case jobs <- job{index: index, artifact: artifact}:
			case <-ctx.Done():
				return
			}
		}
	}()
	go func() {
		workers.Wait()
		close(results)
	}()
	metas := make([]SlotSpoolMeta, len(artifacts))
	completed := 0
	var firstErr error
	for result := range results {
		if result.err != nil {
			if firstErr == nil {
				firstErr = fmt.Errorf("parse slot %d: %w", result.index, result.err)
			}
			continue
		}
		metas[result.index] = result.meta
		completed++
		if progress != nil {
			progress(fmt.Sprintf("UPDATE 并行解析 %d/%d 完成", completed, len(artifacts)))
		}
	}
	if firstErr != nil {
		return nil, firstErr
	}
	if completed != len(artifacts) {
		return nil, fmt.Errorf("parallel update parse incomplete")
	}
	if err := writeJSONAtomic(
		filepath.Join(spoolRoot, "manifest.json"),
		map[string]any{
			"schema_version": "rrc25-update-spool-manifest/v1",
			"engine_version": EngineVersion,
			"worker_count":   workerCount,
			"shard_count":    shardCount,
			"slots":          metas,
		},
	); err != nil {
		return nil, err
	}
	return metas, nil
}

type spoolReader struct {
	file   *os.File
	buffer *bufio.Reader
}

func openSpool(path string) (*spoolReader, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	buffer := bufio.NewReaderSize(file, 1<<20)
	magic := make([]byte, len(spoolMagic))
	if _, err := io.ReadFull(buffer, magic); err != nil {
		file.Close()
		return nil, err
	}
	if string(magic) != string(spoolMagic[:]) {
		file.Close()
		return nil, fmt.Errorf("invalid spool magic: %s", path)
	}
	return &spoolReader{file: file, buffer: buffer}, nil
}

func (reader *spoolReader) Next() (ParsedEvent, error) {
	header := make([]byte, 31)
	if _, err := io.ReadFull(reader.buffer, header); err != nil {
		if err == io.EOF {
			return ParsedEvent{}, io.EOF
		}
		return ParsedEvent{}, err
	}
	afi := header[1]
	peerIPLength := int(header[3])
	addressLength := 4
	if afi == 6 {
		addressLength = 16
	}
	if (peerIPLength != 4 && peerIPLength != 16) || (afi != 4 && afi != 6) {
		return ParsedEvent{}, fmt.Errorf("invalid spool address framing")
	}
	rawPeerIP := make([]byte, peerIPLength)
	if _, err := io.ReadFull(reader.buffer, rawPeerIP); err != nil {
		return ParsedEvent{}, err
	}
	rawPrefix := make([]byte, addressLength)
	if _, err := io.ReadFull(reader.buffer, rawPrefix); err != nil {
		return ParsedEvent{}, err
	}
	peerIP, err := parseAddress(rawPeerIP)
	if err != nil {
		return ParsedEvent{}, err
	}
	prefixAddress, err := parsePrefixAddress(rawPrefix, afi)
	if err != nil {
		return ParsedEvent{}, err
	}
	prefix := netip.PrefixFrom(prefixAddress, int(header[2]))
	if !prefix.IsValid() || prefix.Masked() != prefix {
		return ParsedEvent{}, fmt.Errorf("invalid canonical spool prefix")
	}
	return ParsedEvent{
		Key: RouteKey{
			PeerIP: peerIP, PeerASN: binary.BigEndian.Uint32(header[5:9]),
			AFI: afi, Prefix: prefix,
		},
		Action: header[0], OriginKnown: header[4]&1 != 0,
		EventMicros:    int64(binary.BigEndian.Uint64(header[9:17])),
		ArtifactIndex:  binary.BigEndian.Uint16(header[17:19]),
		RecordOrdinal:  binary.BigEndian.Uint32(header[19:23]),
		ElementOrdinal: binary.BigEndian.Uint32(header[23:27]),
		OriginASN:      binary.BigEndian.Uint32(header[27:31]),
	}, nil
}

func (reader *spoolReader) Close() error {
	return reader.file.Close()
}

func VerifySpoolFiles(outputRoot string, metas []SlotSpoolMeta) error {
	for _, slot := range metas {
		sorted := append([]ShardSpoolMeta(nil), slot.Shards...)
		sort.Slice(sorted, func(i, j int) bool { return sorted[i].Shard < sorted[j].Shard })
		for _, shard := range sorted {
			path := filepath.Join(outputRoot, filepath.FromSlash(shard.Path))
			file, err := os.Open(path)
			if err != nil {
				return err
			}
			hash := sha256.New()
			n, err := io.Copy(hash, file)
			file.Close()
			if err != nil {
				return err
			}
			if n != shard.SizeBytes || hex.EncodeToString(hash.Sum(nil)) != shard.SHA256 {
				return fmt.Errorf("spool hash mismatch: %s", path)
			}
		}
	}
	return nil
}
