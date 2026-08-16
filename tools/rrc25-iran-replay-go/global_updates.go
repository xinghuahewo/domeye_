package replay

import (
	"compress/gzip"
	"container/heap"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"time"
)

const (
	ExpectedGlobalSpoolManifestSHA256 = "f0719a6168f64b2f4783747920f0b73c4504474cee692427ae3ef292ba0ef08b"
	GlobalDatasetRevision             = "global_replay_r2"
)

type GlobalSpoolManifest struct {
	SchemaVersion string          `json:"schema_version"`
	EngineVersion string          `json:"engine_version"`
	WorkerCount   int             `json:"worker_count"`
	ShardCount    int             `json:"shard_count"`
	Slots         []SlotSpoolMeta `json:"slots"`
}

func LoadGlobalSpoolManifest(
	sourceRoot string,
	inputs FixedInputs,
	expectedSHA256 string,
) (GlobalSpoolManifest, string, error) {
	var manifest GlobalSpoolManifest
	if sourceRoot == "" {
		return manifest, "", fmt.Errorf("global spool source is required")
	}
	raw, err := readJSON(
		filepath.Join(sourceRoot, "spool", "manifest.json"), &manifest,
	)
	if err != nil {
		return manifest, "", err
	}
	digest := sha256.Sum256(raw)
	actualSHA256 := hex.EncodeToString(digest[:])
	if expectedSHA256 != "" && actualSHA256 != expectedSHA256 {
		return manifest, actualSHA256, fmt.Errorf(
			"global spool manifest SHA-256 mismatch: %s", actualSHA256,
		)
	}
	if manifest.SchemaVersion != "rrc25-update-spool-manifest/v1" ||
		manifest.EngineVersion != EngineVersion ||
		manifest.WorkerCount < 1 || manifest.ShardCount < 1 ||
		len(manifest.Slots) != len(inputs.AllUpdate) {
		return manifest, actualSHA256, fmt.Errorf(
			"global spool manifest identity mismatch",
		)
	}
	for index, slot := range manifest.Slots {
		if slot.SchemaVersion != "rrc25-update-shard-spool/v1" ||
			slot.EngineVersion != EngineVersion ||
			slot.ArtifactIndex != index ||
			slot.Artifact != inputs.AllUpdate[index] ||
			len(slot.Shards) != manifest.ShardCount {
			return manifest, actualSHA256, fmt.Errorf(
				"global spool slot %d identity mismatch", index,
			)
		}
		shards := append([]ShardSpoolMeta(nil), slot.Shards...)
		sort.Slice(shards, func(i, j int) bool {
			return shards[i].Shard < shards[j].Shard
		})
		routeEvents := int64(0)
		for shardIndex, shard := range shards {
			expectedPath := filepath.ToSlash(filepath.Join(
				"spool", fmt.Sprintf("%03d", index),
				fmt.Sprintf("shard-%03d.bin", shardIndex),
			))
			if shard.Shard != shardIndex || shard.Path != expectedPath ||
				shard.RecordCount < 0 || shard.SizeBytes < int64(len(spoolMagic)) ||
				len(shard.SHA256) != 64 {
				return manifest, actualSHA256, fmt.Errorf(
					"global spool slot %d shard %d identity mismatch",
					index, shardIndex,
				)
			}
			routeEvents += shard.RecordCount
		}
		if routeEvents != slot.Stats.RouteEvents ||
			slot.Stats.Announces+slot.Stats.Withdraws != slot.Stats.RouteEvents {
			return manifest, actualSHA256, fmt.Errorf(
				"global spool slot %d population mismatch", index,
			)
		}
	}
	return manifest, actualSHA256, nil
}

type globalSpoolCursor struct {
	reader      *spoolReader
	meta        ShardSpoolMeta
	event       ParsedEvent
	count       int64
	haveLast    bool
	lastRecord  uint32
	lastElement uint32
}

func (cursor *globalSpoolCursor) advance(
	artifactIndex int,
	shardCount int,
) (bool, error) {
	event, err := cursor.reader.Next()
	if err == io.EOF {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	if int(event.ArtifactIndex) != artifactIndex ||
		shardFor(event.Key, shardCount) != cursor.meta.Shard {
		return false, fmt.Errorf("global spool event coordinate mismatch")
	}
	if cursor.haveLast &&
		(event.RecordOrdinal < cursor.lastRecord ||
			(event.RecordOrdinal == cursor.lastRecord &&
				event.ElementOrdinal <= cursor.lastElement)) {
		return false, fmt.Errorf(
			"global spool shard order is not strictly increasing",
		)
	}
	cursor.event = event
	cursor.count++
	cursor.haveLast = true
	cursor.lastRecord = event.RecordOrdinal
	cursor.lastElement = event.ElementOrdinal
	return true, nil
}

type globalSpoolHeap []*globalSpoolCursor

func (values globalSpoolHeap) Len() int { return len(values) }

func (values globalSpoolHeap) Less(i, j int) bool {
	left := values[i].event
	right := values[j].event
	if left.RecordOrdinal != right.RecordOrdinal {
		return left.RecordOrdinal < right.RecordOrdinal
	}
	if left.ElementOrdinal != right.ElementOrdinal {
		return left.ElementOrdinal < right.ElementOrdinal
	}
	return values[i].meta.Shard < values[j].meta.Shard
}

func (values globalSpoolHeap) Swap(i, j int) {
	values[i], values[j] = values[j], values[i]
}

func (values *globalSpoolHeap) Push(value any) {
	*values = append(*values, value.(*globalSpoolCursor))
}

func (values *globalSpoolHeap) Pop() any {
	old := *values
	last := len(old) - 1
	value := old[last]
	old[last] = nil
	*values = old[:last]
	return value
}

func closeGlobalSpoolCursors(cursors []*globalSpoolCursor) error {
	var firstErr error
	for _, cursor := range cursors {
		if cursor == nil || cursor.reader == nil {
			continue
		}
		if err := cursor.reader.Close(); err != nil && firstErr == nil {
			firstErr = err
		}
		cursor.reader = nil
	}
	return firstErr
}

func ApplyGlobalSpoolSlot(
	state *GlobalReplayState,
	sourceRoot string,
	meta SlotSpoolMeta,
) (*GlobalSlotActivity, error) {
	if state == nil {
		return nil, fmt.Errorf("global replay state is required")
	}
	shards := append([]ShardSpoolMeta(nil), meta.Shards...)
	sort.Slice(shards, func(i, j int) bool {
		return shards[i].Shard < shards[j].Shard
	})
	if len(shards) == 0 {
		return nil, fmt.Errorf("global spool slot has no shards")
	}
	cursors := make([]*globalSpoolCursor, 0, len(shards))
	queue := make(globalSpoolHeap, 0, len(shards))
	for shardIndex, shard := range shards {
		if shard.Shard != shardIndex {
			_ = closeGlobalSpoolCursors(cursors)
			return nil, fmt.Errorf("global spool shard sequence mismatch")
		}
		reader, err := openSpool(
			filepath.Join(sourceRoot, filepath.FromSlash(shard.Path)),
		)
		if err != nil {
			_ = closeGlobalSpoolCursors(cursors)
			return nil, err
		}
		cursor := &globalSpoolCursor{reader: reader, meta: shard}
		cursors = append(cursors, cursor)
		hasEvent, err := cursor.advance(meta.ArtifactIndex, len(shards))
		if err != nil {
			_ = closeGlobalSpoolCursors(cursors)
			return nil, err
		}
		if hasEvent {
			heap.Push(&queue, cursor)
		}
	}
	activity := NewGlobalSlotActivity()
	processed := int64(0)
	haveLast := false
	var lastRecord uint32
	var lastElement uint32
	for queue.Len() > 0 {
		cursor := heap.Pop(&queue).(*globalSpoolCursor)
		event := cursor.event
		if haveLast &&
			(event.RecordOrdinal < lastRecord ||
				(event.RecordOrdinal == lastRecord &&
					event.ElementOrdinal <= lastElement)) {
			_ = closeGlobalSpoolCursors(cursors)
			return nil, fmt.Errorf(
				"global merged spool order is not strictly increasing",
			)
		}
		if err := state.Apply(event, activity); err != nil {
			_ = closeGlobalSpoolCursors(cursors)
			return nil, err
		}
		processed++
		haveLast = true
		lastRecord = event.RecordOrdinal
		lastElement = event.ElementOrdinal
		hasEvent, err := cursor.advance(meta.ArtifactIndex, len(shards))
		if err != nil {
			_ = closeGlobalSpoolCursors(cursors)
			return nil, err
		}
		if hasEvent {
			heap.Push(&queue, cursor)
		}
	}
	for _, cursor := range cursors {
		if err := cursor.reader.Verify(cursor.meta); err != nil {
			_ = closeGlobalSpoolCursors(cursors)
			return nil, err
		}
	}
	if err := closeGlobalSpoolCursors(cursors); err != nil {
		return nil, err
	}
	for _, cursor := range cursors {
		if cursor.count != cursor.meta.RecordCount {
			return nil, fmt.Errorf(
				"global spool record count mismatch at slot %d shard %d",
				meta.ArtifactIndex, cursor.meta.Shard,
			)
		}
	}
	if processed != meta.Stats.RouteEvents ||
		activity.Global.Announce != meta.Stats.Announces ||
		activity.Global.Withdraw != meta.Stats.Withdraws {
		return nil, fmt.Errorf(
			"global spool slot %d activity population mismatch",
			meta.ArtifactIndex,
		)
	}
	return activity, nil
}

type GlobalActivityReport struct {
	ByCountry            map[string]UpdateCounts `json:"by_country"`
	Global               UpdateCounts            `json:"global"`
	CountryMigrations    int64                   `json:"country_migrations"`
	ReplacementAnnounces int64                   `json:"replacement_announces"`
	DuplicateAnnounces   int64                   `json:"duplicate_announces"`
	DuplicateWithdraws   int64                   `json:"duplicate_withdraws"`
	WithdrawWithoutState int64                   `json:"withdraw_without_state"`
	AnnouncementsUnknown int64                   `json:"announcements_unknown_country"`
	WithdrawsUnknown     int64                   `json:"withdraws_unknown_country"`
}

func globalActivityReport(
	mapping *GlobalCountryMapping,
	activity *GlobalSlotActivity,
) GlobalActivityReport {
	result := GlobalActivityReport{
		ByCountry: make(map[string]UpdateCounts),
	}
	if activity == nil {
		return result
	}
	for countryID, counts := range activity.ByCountry {
		result.ByCountry[mapping.CountryCode(countryID)] = counts
	}
	result.Global = activity.Global
	result.CountryMigrations = activity.CountryMigrations
	result.ReplacementAnnounces = activity.ReplacementAnnounces
	result.DuplicateAnnounces = activity.DuplicateAnnounces
	result.DuplicateWithdraws = activity.DuplicateWithdraws
	result.WithdrawWithoutState = activity.WithdrawWithoutState
	result.AnnouncementsUnknown = activity.AnnouncementsUnknown
	result.WithdrawsUnknown = activity.WithdrawsUnknown
	return result
}

type GlobalSlotProduct struct {
	SchemaVersion       string                     `json:"schema_version"`
	EngineVersion       string                     `json:"engine_version"`
	RunID               string                     `json:"run_id"`
	DatasetID           string                     `json:"dataset_id"`
	Revision            string                     `json:"revision"`
	CollectorID         string                     `json:"collector_id"`
	Phase               string                     `json:"phase"`
	ProductSequence     int                        `json:"product_sequence"`
	ArtifactIndex       *int                       `json:"artifact_index"`
	InputArtifact       *Artifact                  `json:"input_artifact"`
	ObservedAt          string                     `json:"observed_at"`
	DataThrough         string                     `json:"data_through"`
	SlotStartUTC        string                     `json:"slot_start_utc"`
	SlotEndExclusiveUTC string                     `json:"slot_end_exclusive_utc"`
	SlotRole            string                     `json:"slot_role"`
	SpoolManifestSHA256 string                     `json:"spool_manifest_sha256"`
	PreviousProductSHA  string                     `json:"previous_product_sha256"`
	ASNStatesIncluded   bool                       `json:"asn_states_included"`
	Activity            GlobalActivityReport       `json:"activity"`
	Conservation        GlobalConservation         `json:"conservation"`
	Countries           []GlobalCountryObservation `json:"countries"`
	ASNStates           []GlobalASNStateRow        `json:"asn_states,omitempty"`
}

type GlobalProductReference struct {
	Sequence      int    `json:"sequence"`
	Phase         string `json:"phase"`
	ArtifactIndex *int   `json:"artifact_index"`
	ObservedAt    string `json:"observed_at"`
	Path          string `json:"path"`
	SHA256        string `json:"sha256"`
	StateDigest   string `json:"state_digest"`
}

type GlobalProgress struct {
	SchemaVersion          string `json:"schema_version"`
	EngineVersion          string `json:"engine_version"`
	RunID                  string `json:"run_id"`
	DatasetID              string `json:"dataset_id"`
	Revision               string `json:"revision"`
	Phase                  string `json:"phase"`
	ProductSequence        int    `json:"product_sequence"`
	ProcessedSlot          int    `json:"processed_slot"`
	ProcessedUpdateCount   int    `json:"processed_update_count"`
	FormalObservationCount int    `json:"formal_observation_count"`
	DataThrough            string `json:"data_through"`
	StateDigest            string `json:"state_digest"`
	LastProductPath        string `json:"last_product_path"`
	LastProductSHA256      string `json:"last_product_sha256"`
	SpoolManifestSHA256    string `json:"spool_manifest_sha256"`
	Checkpoint             string `json:"checkpoint"`
	UpdatedAt              string `json:"updated_at"`
}

type GlobalDeltaCheckpointManifest struct {
	SchemaVersion          string                   `json:"schema_version"`
	EngineVersion          string                   `json:"engine_version"`
	RunID                  string                   `json:"run_id"`
	DatasetID              string                   `json:"dataset_id"`
	Revision               string                   `json:"revision"`
	Phase                  string                   `json:"phase"`
	ProcessedSlot          int                      `json:"processed_slot"`
	ProcessedUpdateCount   int                      `json:"processed_update_count"`
	FormalObservationCount int                      `json:"formal_observation_count"`
	DataThrough            string                   `json:"data_through"`
	StateDigest            string                   `json:"state_digest"`
	BaseRIBCheckpoint      string                   `json:"base_rib_checkpoint"`
	BaseRIBStateDigest     string                   `json:"base_rib_state_digest"`
	SpoolManifestSHA256    string                   `json:"spool_manifest_sha256"`
	MappingVersion         string                   `json:"mapping_version"`
	Conservation           GlobalConservation       `json:"conservation"`
	Products               []GlobalProductReference `json:"products"`
}

type GlobalUpdateQuality struct {
	SchemaVersion               string          `json:"schema_version"`
	EngineVersion               string          `json:"engine_version"`
	Status                      string          `json:"status"`
	UpdateArtifactCount         int             `json:"update_artifact_count"`
	UpdatePhysicalRecords       int64           `json:"update_physical_records"`
	UpdateRouteEvents           int64           `json:"update_route_events"`
	UpdateAnnounces             int64           `json:"update_announces"`
	UpdateWithdraws             int64           `json:"update_withdraws"`
	UpdateUnknownOrigins        int64           `json:"update_unknown_origins"`
	UpdateUnknownOptional       map[uint8]int64 `json:"update_unknown_optional_attributes"`
	UpdateMalformedOTC          int64           `json:"update_malformed_otc_attributes"`
	UpdateTreatAsWithdraw       int64           `json:"update_treat_as_withdraw_route_events"`
	CountryMigrations           int64           `json:"country_migrations"`
	ReplacementAnnounces        int64           `json:"replacement_announces"`
	DuplicateAnnounces          int64           `json:"duplicate_announces"`
	DuplicateWithdraws          int64           `json:"duplicate_withdraws"`
	WithdrawWithoutState        int64           `json:"withdraw_without_state"`
	UnknownCountryAnnouncements int64           `json:"unknown_country_announcements"`
	UnknownCountryWithdraws     int64           `json:"unknown_country_withdraws"`
	CatchUpProductCount         int             `json:"catch_up_product_count"`
	FormalObservationCount      int             `json:"formal_observation_count"`
	LastObservationAt           string          `json:"last_observation_at"`
	FinalStateDigest            string          `json:"final_state_digest"`
	SpoolManifestSHA256         string          `json:"spool_manifest_sha256"`
}

func globalDatasetID(
	runID string,
	spoolManifestSHA256 string,
) string {
	return stableID("global_dataset_v1_", map[string]any{
		"run_id": runID, "spool_manifest_sha256": spoolManifestSHA256,
		"revision": GlobalDatasetRevision,
	}, 32)
}

func sha256File(path string) (string, int64, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", 0, err
	}
	defer file.Close()
	hash := sha256.New()
	size, err := io.Copy(hash, file)
	if err != nil {
		return "", 0, err
	}
	return hex.EncodeToString(hash.Sum(nil)), size, nil
}

func removeRegularTemp(path string) error {
	info, err := os.Lstat(path)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 {
		return fmt.Errorf("refusing non-regular temporary output: %s", path)
	}
	return os.Remove(path)
}

func writeGlobalProductImmutable(
	path string,
	product GlobalSlotProduct,
) (string, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return "", err
	}
	temp := path + ".tmp"
	if err := removeRegularTemp(temp); err != nil {
		return "", err
	}
	file, err := os.OpenFile(temp, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o640)
	if err != nil {
		return "", err
	}
	hash := sha256.New()
	compressed, err := gzip.NewWriterLevel(io.MultiWriter(file, hash), gzip.BestSpeed)
	if err != nil {
		file.Close()
		return "", err
	}
	encoder := json.NewEncoder(compressed)
	encoder.SetEscapeHTML(false)
	writeErr := encoder.Encode(product)
	closeErr := compressed.Close()
	syncErr := file.Sync()
	fileErr := file.Close()
	for _, candidate := range []error{writeErr, closeErr, syncErr, fileErr} {
		if candidate != nil {
			_ = os.Remove(temp)
			return "", candidate
		}
	}
	digest := hex.EncodeToString(hash.Sum(nil))
	if _, err := os.Stat(path); err == nil {
		existing, _, hashErr := sha256File(path)
		_ = os.Remove(temp)
		if hashErr != nil {
			return "", hashErr
		}
		if existing != digest {
			return "", fmt.Errorf("immutable global product mismatch: %s", path)
		}
		return digest, nil
	} else if !os.IsNotExist(err) {
		_ = os.Remove(temp)
		return "", err
	}
	if err := os.Rename(temp, path); err != nil {
		return "", err
	}
	return digest, nil
}

func writeJSONImmutable(path string, value any) (string, error) {
	raw, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return "", err
	}
	raw = append(raw, '\n')
	digest := sha256.Sum256(raw)
	expected := hex.EncodeToString(digest[:])
	if existing, err := os.ReadFile(path); err == nil {
		actual := sha256.Sum256(existing)
		if hex.EncodeToString(actual[:]) != expected {
			return "", fmt.Errorf("immutable JSON mismatch: %s", path)
		}
		return expected, nil
	} else if !os.IsNotExist(err) {
		return "", err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return "", err
	}
	temp := path + ".tmp"
	if err := removeRegularTemp(temp); err != nil {
		return "", err
	}
	if err := os.WriteFile(temp, raw, 0o640); err != nil {
		return "", err
	}
	file, err := os.OpenFile(temp, os.O_RDWR, 0)
	if err != nil {
		return "", err
	}
	if err := file.Sync(); err != nil {
		file.Close()
		return "", err
	}
	if err := file.Close(); err != nil {
		return "", err
	}
	if err := os.Rename(temp, path); err != nil {
		return "", err
	}
	return expected, nil
}

func loadGlobalProgress(path string) (GlobalProgress, error) {
	var progress GlobalProgress
	if _, err := readJSON(path, &progress); err != nil {
		return progress, err
	}
	return progress, nil
}

func progressEquivalent(left, right GlobalProgress) bool {
	return left.RunID == right.RunID &&
		left.DatasetID == right.DatasetID &&
		left.Revision == right.Revision &&
		left.Phase == right.Phase &&
		left.ProductSequence == right.ProductSequence &&
		left.ProcessedSlot == right.ProcessedSlot &&
		left.ProcessedUpdateCount == right.ProcessedUpdateCount &&
		left.FormalObservationCount == right.FormalObservationCount &&
		left.DataThrough == right.DataThrough &&
		left.StateDigest == right.StateDigest &&
		left.LastProductPath == right.LastProductPath &&
		left.LastProductSHA256 == right.LastProductSHA256 &&
		left.SpoolManifestSHA256 == right.SpoolManifestSHA256
}

func advanceGlobalProgress(
	path string,
	previous *GlobalProgress,
	candidate GlobalProgress,
) error {
	candidate.SchemaVersion = "rrc25-global-progress/v1"
	candidate.EngineVersion = GlobalEngineVersion
	candidate.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
	if previous != nil {
		if candidate.ProductSequence < previous.ProductSequence {
			return nil
		}
		if candidate.ProductSequence == previous.ProductSequence {
			if !progressEquivalent(*previous, candidate) {
				return fmt.Errorf("global progress identity mismatch")
			}
			return nil
		}
	}
	if err := writeJSONAtomic(path, candidate); err != nil {
		return err
	}
	if previous != nil {
		*previous = candidate
	}
	return nil
}

func globalProductPath(
	output string,
	phase string,
	index int,
) (string, string) {
	name := fmt.Sprintf("%s-%03d.json.gz", phase, index)
	relative := filepath.ToSlash(filepath.Join("slots", phase, name))
	return filepath.Join(output, filepath.FromSlash(relative)), relative
}

func intPointer(value int) *int {
	result := value
	return &result
}
