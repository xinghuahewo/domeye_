package replay

import (
	"bufio"
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

const (
	PrefixVPEvidenceCatalogVersion = "rrc25-prefix-vp-evidence-catalog/v1"
	PrefixVPEvidencePageVersion    = "rrc25-prefix-vp-evidence-page/v1"
	PrefixVPEvidenceProjector      = "domeye_prefix_vp_evidence_projector"
	PrefixVPEvidenceProjectorV1    = "1.0.0"
)

type PrefixVPEvidenceBuildConfig struct {
	RouteStateRoot    string
	CompatibleMapping string
	RevisedMapping    string
	Countries         []string
	Output            string
	PageSize          int
}

type PrefixVPEvidencePageRef struct {
	Page          int    `json:"page"`
	Path          string `json:"path"`
	RowCount      int    `json:"row_count"`
	SizeBytes     int64  `json:"size_bytes"`
	SHA256        string `json:"sha256"`
	ContentSHA256 string `json:"content_sha256"`
}

type PrefixVPEvidenceCountry struct {
	CountryCode   string                    `json:"country_code"`
	RowCount      int64                     `json:"row_count"`
	PageCount     int                       `json:"page_count"`
	ContentSHA256 string                    `json:"content_sha256"`
	Pages         []PrefixVPEvidencePageRef `json:"pages"`
}

type PrefixVPEvidenceCatalog struct {
	SchemaVersion                 string                    `json:"schema_version"`
	Status                        string                    `json:"status"`
	CollectorID                   string                    `json:"collector_id"`
	WindowStartUTC                string                    `json:"window_start_utc"`
	WindowEndExclusiveUTC         string                    `json:"window_end_exclusive_utc"`
	SourceRouteStateDatasetID     string                    `json:"source_route_state_dataset_id"`
	SourceRouteStateContentSHA256 string                    `json:"source_route_state_content_sha256"`
	SeedRouteStateID              string                    `json:"seed_route_state_id"`
	SeedRouteStateContentSHA256   string                    `json:"seed_route_state_content_sha256"`
	DerivedFromRouteStateID       string                    `json:"derived_from_route_state_id"`
	RouteStateContentSHA256       string                    `json:"route_state_content_sha256"`
	MappingVersion                string                    `json:"mapping_version"`
	MappingCompatibleSHA256       string                    `json:"mapping_compatible_sha256"`
	MappingRevisedSHA256          string                    `json:"mapping_revised_sha256"`
	ProjectorName                 string                    `json:"projector_name"`
	ProjectorVersion              string                    `json:"projector_version"`
	PageSize                      int                       `json:"page_size"`
	CountryCount                  int                       `json:"country_count"`
	RowCount                      int64                     `json:"row_count"`
	Countries                     []PrefixVPEvidenceCountry `json:"countries"`
	ContentSHA256                 string                    `json:"content_sha256"`
}

type PrefixVPEvidenceRow struct {
	CollectorID        string `json:"collector_id"`
	PeerIP             string `json:"peer_ip"`
	PeerASN            uint32 `json:"peer_asn"`
	Prefix             string `json:"prefix"`
	AddressFamily      uint8  `json:"address_family"`
	BaselineOriginASN  uint32 `json:"baseline_origin_asn"`
	Visible            bool   `json:"visible"`
	OriginKnown        bool   `json:"origin_known"`
	OriginASN          uint32 `json:"origin_asn"`
	ASPathSHA256       string `json:"as_path_sha256,omitempty"`
	AttributeSHA256    string `json:"attribute_sha256,omitempty"`
	LastRouteEventID   string `json:"last_route_event_id"`
	LastArtifactIndex  uint16 `json:"last_artifact_index"`
	LastRecordOrdinal  uint32 `json:"last_record_ordinal"`
	LastElementOrdinal uint32 `json:"last_element_ordinal"`
	LastUpdatedUTC     string `json:"last_updated_utc"`
	QualityStatus      string `json:"quality_status"`
}

type prefixVPEvidencePage struct {
	SchemaVersion string                `json:"schema_version"`
	CountryCode   string                `json:"country_code"`
	Page          int                   `json:"page"`
	Rows          []PrefixVPEvidenceRow `json:"rows"`
}

type checkpointStream struct {
	file       *os.File
	compressed *gzip.Reader
	reader     *bufio.Reader
	hash       hashCounter
	meta       RouteStateCheckpointShard
	count      int64
	previous   *RouteStateKey
}

type hashCounter struct {
	hash  [32]byte
	state *sha256Writer
}

func openCheckpointStream(path string, meta RouteStateCheckpointShard) (*checkpointStream, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	state := &sha256Writer{hash: sha256.New()}
	decoded, err := gzip.NewReader(io.TeeReader(file, state))
	if err != nil {
		_ = file.Close()
		return nil, err
	}
	reader := bufio.NewReaderSize(decoded, 1<<20)
	magic := make([]byte, len(routeStateCheckpointMagic))
	if _, err := io.ReadFull(reader, magic); err != nil || !bytes.Equal(magic, routeStateCheckpointMagic[:]) {
		_ = decoded.Close()
		_ = file.Close()
		return nil, fmt.Errorf("RouteState checkpoint magic 无效：%s", path)
	}
	return &checkpointStream{file: file, compressed: decoded, reader: reader, hash: hashCounter{state: state}, meta: meta}, nil
}

func (stream *checkpointStream) Next() (RouteStateKey, RouteStateValue, bool, error) {
	raw := make([]byte, routeStateRecordBytes)
	_, err := io.ReadFull(stream.reader, raw)
	if err == io.EOF {
		return RouteStateKey{}, RouteStateValue{}, false, nil
	}
	if err == io.ErrUnexpectedEOF {
		return RouteStateKey{}, RouteStateValue{}, false, fmt.Errorf("RouteState checkpoint 记录截断")
	}
	if err != nil {
		return RouteStateKey{}, RouteStateValue{}, false, err
	}
	key, value, err := decodeRouteStateRecord(raw)
	if err != nil {
		return RouteStateKey{}, RouteStateValue{}, false, err
	}
	if stream.previous != nil && !routeStateKeyLess(*stream.previous, key) {
		return RouteStateKey{}, RouteStateValue{}, false, fmt.Errorf("RouteState checkpoint 键未严格递增")
	}
	copyKey := key
	stream.previous = &copyKey
	stream.count++
	return key, value, true, nil
}

func (stream *checkpointStream) Close() error {
	if err := stream.compressed.Close(); err != nil {
		_ = stream.file.Close()
		return err
	}
	if _, err := io.Copy(io.Discard, io.TeeReader(stream.file, stream.hash.state)); err != nil {
		_ = stream.file.Close()
		return err
	}
	if err := stream.file.Close(); err != nil {
		return err
	}
	actual := hex.EncodeToString(stream.hash.state.hash.Sum(nil))
	if actual != stream.meta.SHA256 || stream.hash.state.bytes != stream.meta.SizeBytes ||
		stream.count != stream.meta.RecordCount {
		return fmt.Errorf("RouteState checkpoint shard 身份不闭合")
	}
	return nil
}

type countryPageWriter struct {
	root        string
	country     string
	pageSize    int
	page        int
	rows        []PrefixVPEvidenceRow
	rowCount    int64
	contentHash *sha256Writer
	pages       []PrefixVPEvidencePageRef
}

func newCountryPageWriter(root, country string, pageSize int) (*countryPageWriter, error) {
	directory := filepath.Join(root, country)
	if err := os.MkdirAll(directory, 0o750); err != nil {
		return nil, err
	}
	return &countryPageWriter{
		root: root, country: country, pageSize: pageSize,
		rows:        make([]PrefixVPEvidenceRow, 0, pageSize),
		contentHash: &sha256Writer{hash: sha256.New()},
	}, nil
}

func (writer *countryPageWriter) Add(row PrefixVPEvidenceRow) error {
	raw, err := json.Marshal(row)
	if err != nil {
		return err
	}
	_, _ = writer.contentHash.Write(raw)
	_, _ = writer.contentHash.Write([]byte{'\n'})
	writer.rows = append(writer.rows, row)
	writer.rowCount++
	if len(writer.rows) == writer.pageSize {
		return writer.flush()
	}
	return nil
}

func writeDeterministicGzip(path string, raw []byte) (string, int64, error) {
	file, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o640)
	if err != nil {
		return "", 0, err
	}
	hash := &sha256Writer{hash: sha256.New()}
	compressed, err := gzip.NewWriterLevel(io.MultiWriter(file, hash), gzip.BestCompression)
	if err != nil {
		_ = file.Close()
		return "", 0, err
	}
	compressed.Header.ModTime = time.Unix(0, 0).UTC()
	compressed.Header.OS = 255
	if _, err := compressed.Write(raw); err != nil {
		_ = compressed.Close()
		_ = file.Close()
		return "", 0, err
	}
	if err := compressed.Close(); err != nil {
		_ = file.Close()
		return "", 0, err
	}
	if err := file.Sync(); err != nil {
		_ = file.Close()
		return "", 0, err
	}
	if err := file.Close(); err != nil {
		return "", 0, err
	}
	return hex.EncodeToString(hash.hash.Sum(nil)), hash.bytes, nil
}

func (writer *countryPageWriter) flush() error {
	if len(writer.rows) == 0 {
		return nil
	}
	writer.page++
	payload := prefixVPEvidencePage{
		SchemaVersion: PrefixVPEvidencePageVersion,
		CountryCode:   writer.country, Page: writer.page, Rows: writer.rows,
	}
	raw, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	raw = append(raw, '\n')
	relative := filepath.ToSlash(filepath.Join(writer.country, fmt.Sprintf("page-%06d.json.gz", writer.page)))
	compressedSHA, size, err := writeDeterministicGzip(filepath.Join(writer.root, filepath.FromSlash(relative)), raw)
	if err != nil {
		return err
	}
	content := sha256.Sum256(raw)
	writer.pages = append(writer.pages, PrefixVPEvidencePageRef{
		Page: writer.page, Path: relative, RowCount: len(writer.rows), SizeBytes: size,
		SHA256: compressedSHA, ContentSHA256: hex.EncodeToString(content[:]),
	})
	writer.rows = make([]PrefixVPEvidenceRow, 0, writer.pageSize)
	return nil
}

func (writer *countryPageWriter) Close() (PrefixVPEvidenceCountry, error) {
	if err := writer.flush(); err != nil {
		return PrefixVPEvidenceCountry{}, err
	}
	return PrefixVPEvidenceCountry{
		CountryCode: writer.country, RowCount: writer.rowCount,
		PageCount: len(writer.pages), ContentSHA256: hex.EncodeToString(writer.contentHash.hash.Sum(nil)),
		Pages: writer.pages,
	}, nil
}

func routeStateValueEvidenceRow(key RouteStateKey, seed, current RouteStateValue) PrefixVPEvidenceRow {
	row := PrefixVPEvidenceRow{
		CollectorID: "rrc25", PeerIP: key.Route.PeerIP.String(), PeerASN: key.Route.PeerASN,
		Prefix: key.Route.Prefix.String(), AddressFamily: key.Route.AFI,
		BaselineOriginASN: seed.OriginASN, Visible: current.Visible,
		OriginKnown: current.OriginKnown, OriginASN: current.OriginASN,
		LastRouteEventID:  hex.EncodeToString(current.LastRouteEventID[:]),
		LastArtifactIndex: current.LastArtifactIndex, LastRecordOrdinal: current.LastRecordOrdinal,
		LastElementOrdinal: current.LastElementOrdinal,
		LastUpdatedUTC:     time.UnixMicro(current.LastUpdatedMicros).UTC().Format(time.RFC3339Nano),
		QualityStatus:      routeStateQualityName(current.QualityStatus),
	}
	if current.ASPathKnown {
		row.ASPathSHA256 = hex.EncodeToString(current.ASPathDigest[:])
	}
	if current.AttributeKnown {
		row.AttributeSHA256 = hex.EncodeToString(current.AttributeDigest[:])
	}
	return row
}

func prefixVPCatalogContentSHA(catalog PrefixVPEvidenceCatalog) string {
	catalog.ContentSHA256 = ""
	raw, _ := json.Marshal(catalog)
	digest := sha256.Sum256(raw)
	return hex.EncodeToString(digest[:])
}

func buildPrefixVPEvidenceFromCheckpoints(
	output string,
	seedDirectory string,
	seed RouteStateCheckpointManifest,
	finalDirectory string,
	final RouteStateCheckpointManifest,
	mapping *GlobalCountryMapping,
	countries []string,
	pageSize int,
	sourceContentSHA string,
) (PrefixVPEvidenceCatalog, error) {
	if pageSize < 1 || seed.ShardCount != final.ShardCount || len(seed.Shards) != len(final.Shards) {
		return PrefixVPEvidenceCatalog{}, fmt.Errorf("Prefix×VP Evidence checkpoint/page 配置无效")
	}
	target := make(map[uint16]string, len(countries))
	writers := make(map[string]*countryPageWriter, len(countries))
	for _, country := range countries {
		id, exists := mapping.IDForCode(country)
		if !exists || country == UnknownCountryCode {
			return PrefixVPEvidenceCatalog{}, fmt.Errorf("Evidence 国家不在冻结 mapping：%s", country)
		}
		target[id] = country
		writer, err := newCountryPageWriter(filepath.Join(output, "pages"), country, pageSize)
		if err != nil {
			return PrefixVPEvidenceCatalog{}, err
		}
		writers[country] = writer
	}
	for shard := range seed.Shards {
		seedStream, err := openCheckpointStream(filepath.Join(seedDirectory, seed.Shards[shard].Path), seed.Shards[shard])
		if err != nil {
			return PrefixVPEvidenceCatalog{}, err
		}
		finalStream, err := openCheckpointStream(filepath.Join(finalDirectory, final.Shards[shard].Path), final.Shards[shard])
		if err != nil {
			_ = seedStream.Close()
			return PrefixVPEvidenceCatalog{}, err
		}
		finalKey, finalValue, finalOK, err := finalStream.Next()
		if err != nil {
			return PrefixVPEvidenceCatalog{}, err
		}
		for {
			seedKey, seedValue, seedOK, err := seedStream.Next()
			if err != nil {
				return PrefixVPEvidenceCatalog{}, err
			}
			if !seedOK {
				break
			}
			for finalOK && routeStateKeyLess(finalKey, seedKey) {
				finalKey, finalValue, finalOK, err = finalStream.Next()
				if err != nil {
					return PrefixVPEvidenceCatalog{}, err
				}
			}
			if !finalOK || routeStateKeyLess(seedKey, finalKey) || routeStateKeyLess(finalKey, seedKey) {
				return PrefixVPEvidenceCatalog{}, fmt.Errorf("Seed RouteState key 在终点不存在")
			}
			if seedValue.OriginKnown {
				if country, selected := target[mapping.CountryID(seedValue.OriginASN)]; selected {
					if err := writers[country].Add(routeStateValueEvidenceRow(seedKey, seedValue, finalValue)); err != nil {
						return PrefixVPEvidenceCatalog{}, err
					}
				}
			}
		}
		for finalOK {
			finalKey, finalValue, finalOK, err = finalStream.Next()
			if err != nil {
				return PrefixVPEvidenceCatalog{}, err
			}
		}
		if err := seedStream.Close(); err != nil {
			return PrefixVPEvidenceCatalog{}, err
		}
		if err := finalStream.Close(); err != nil {
			return PrefixVPEvidenceCatalog{}, err
		}
	}
	sortedCountries := append([]string(nil), countries...)
	sort.Strings(sortedCountries)
	entries := make([]PrefixVPEvidenceCountry, 0, len(sortedCountries))
	var rowCount int64
	for _, country := range sortedCountries {
		entry, err := writers[country].Close()
		if err != nil {
			return PrefixVPEvidenceCatalog{}, err
		}
		entries = append(entries, entry)
		rowCount += entry.RowCount
	}
	catalog := PrefixVPEvidenceCatalog{
		SchemaVersion: PrefixVPEvidenceCatalogVersion, Status: "complete", CollectorID: "rrc25",
		WindowStartUTC: seed.WindowStartUTC, WindowEndExclusiveUTC: final.WindowEndExclusiveUTC,
		SourceRouteStateDatasetID:     final.RouteStateDatasetID,
		SourceRouteStateContentSHA256: sourceContentSHA,
		SeedRouteStateID:              seed.CheckpointID, SeedRouteStateContentSHA256: seed.ContentSHA256,
		DerivedFromRouteStateID: final.CheckpointID, RouteStateContentSHA256: final.ContentSHA256,
		MappingVersion: mapping.MappingVersion, MappingCompatibleSHA256: mapping.CompatibleSHA256,
		MappingRevisedSHA256: mapping.RevisedSHA256,
		ProjectorName:        PrefixVPEvidenceProjector, ProjectorVersion: PrefixVPEvidenceProjectorV1,
		PageSize: pageSize, CountryCount: len(entries), RowCount: rowCount, Countries: entries,
	}
	catalog.ContentSHA256 = prefixVPCatalogContentSHA(catalog)
	return catalog, nil
}

func BuildPrefixVPEvidence(config PrefixVPEvidenceBuildConfig) (PrefixVPEvidenceCatalog, error) {
	if config.RouteStateRoot == "" || config.Output == "" || config.PageSize < 1 || len(config.Countries) == 0 {
		return PrefixVPEvidenceCatalog{}, fmt.Errorf("Prefix×VP Evidence 构建参数不完整")
	}
	if _, err := os.Lstat(config.Output); err == nil {
		return PrefixVPEvidenceCatalog{}, fmt.Errorf("Evidence 输出已存在")
	} else if !os.IsNotExist(err) {
		return PrefixVPEvidenceCatalog{}, err
	}
	var root RouteStateStoreManifest
	manifestRaw, err := readJSON(filepath.Join(config.RouteStateRoot, "manifest.json"), &root)
	if err != nil {
		return PrefixVPEvidenceCatalog{}, err
	}
	var complete RouteStateStoreManifest
	completeRaw, err := readJSON(filepath.Join(config.RouteStateRoot, "COMPLETE.json"), &complete)
	if err != nil {
		return PrefixVPEvidenceCatalog{}, err
	}
	if !bytes.Equal(manifestRaw, completeRaw) || root.SchemaVersion != RouteStateStoreVersion ||
		root.Status != "complete" || root.CollectorID != "rrc25" || root.StatePointCount != RouteStateFinalSlot ||
		root.WindowStartUTC != RouteEventWindowStartUTC || root.WindowEndExclusiveUTC != RouteEventWindowEndUTC ||
		root.DataThrough != RouteEventWindowEndUTC || root.ContentSHA256 != routeStateStoreContentSHA(root) {
		return PrefixVPEvidenceCatalog{}, fmt.Errorf("来源 RouteState 完成身份无效")
	}
	mapping, err := LoadGlobalCountryMapping(config.CompatibleMapping, config.RevisedMapping)
	if err != nil {
		return PrefixVPEvidenceCatalog{}, err
	}
	if mapping.MappingVersion != root.MappingVersion || mapping.CompatibleSHA256 != root.MappingCompatibleSHA256 ||
		mapping.RevisedSHA256 != root.MappingRevisedSHA256 {
		return PrefixVPEvidenceCatalog{}, fmt.Errorf("来源 RouteState mapping 身份冲突")
	}
	identity := RouteStateCheckpointIdentity{
		RouteStateDatasetID: root.DatasetID, SourceRouteEventDatasetID: root.SourceRouteEventDatasetID,
		SourceRouteEventContentSHA: root.SourceRouteEventContentSHA, ImplementationID: root.ImplementationID,
		ProjectorName: root.ProjectorName, ProjectorVersion: root.ProjectorVersion,
		MappingVersion: root.MappingVersion, MappingCompatibleSHA256: root.MappingCompatibleSHA256,
		MappingRevisedSHA256: root.MappingRevisedSHA256, WindowStartUTC: root.WindowStartUTC,
		WindowEndExclusiveUTC: root.WindowEndExclusiveUTC,
	}
	seedDirectory := filepath.Join(config.RouteStateRoot, "checkpoints", "slot-0000")
	finalDirectory := filepath.Join(config.RouteStateRoot, "checkpoints", "slot-4320")
	seed, _, err := readRouteStateCheckpointManifestQuick(seedDirectory, identity)
	if err != nil {
		return PrefixVPEvidenceCatalog{}, err
	}
	final, _, err := readRouteStateCheckpointManifestQuick(finalDirectory, identity)
	if err != nil {
		return PrefixVPEvidenceCatalog{}, err
	}
	if seed.ProcessedSlot != 0 || final.ProcessedSlot != RouteStateFinalSlot ||
		seed.CheckpointID != root.Checkpoints[0].CheckpointID || final.CheckpointID != root.Checkpoints[2].CheckpointID {
		return PrefixVPEvidenceCatalog{}, fmt.Errorf("来源 RouteState checkpoint 身份冲突")
	}
	countries := make([]string, 0, len(config.Countries))
	seen := map[string]bool{}
	for _, raw := range config.Countries {
		country := strings.TrimSpace(raw)
		if !countryCodePattern.MatchString(country) || seen[country] {
			return PrefixVPEvidenceCatalog{}, fmt.Errorf("Evidence 国家列表无效")
		}
		seen[country] = true
		countries = append(countries, country)
	}
	temporary := config.Output + ".tmp"
	if _, err := os.Lstat(temporary); err == nil {
		return PrefixVPEvidenceCatalog{}, fmt.Errorf("Evidence 临时目录已存在")
	} else if !os.IsNotExist(err) {
		return PrefixVPEvidenceCatalog{}, err
	}
	if err := os.MkdirAll(temporary, 0o750); err != nil {
		return PrefixVPEvidenceCatalog{}, err
	}
	catalog, err := buildPrefixVPEvidenceFromCheckpoints(
		temporary, seedDirectory, seed, finalDirectory, final, mapping, countries,
		config.PageSize, root.ContentSHA256,
	)
	if err != nil {
		return PrefixVPEvidenceCatalog{}, err
	}
	raw, err := json.Marshal(catalog)
	if err != nil {
		return PrefixVPEvidenceCatalog{}, err
	}
	raw = append(raw, '\n')
	if err := os.WriteFile(filepath.Join(temporary, "catalog.json"), raw, 0o640); err != nil {
		return PrefixVPEvidenceCatalog{}, err
	}
	if err := os.WriteFile(filepath.Join(temporary, "COMPLETE.json"), raw, 0o640); err != nil {
		return PrefixVPEvidenceCatalog{}, err
	}
	if err := os.Rename(temporary, config.Output); err != nil {
		return PrefixVPEvidenceCatalog{}, err
	}
	return catalog, nil
}
