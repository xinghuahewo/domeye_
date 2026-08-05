package replay

import (
	"bufio"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const (
	GlobalWindowSelectionVersion = "rrc25-global-window-selection/v1"
	GlobalWindowRunVersion       = "rrc25-global-window-run/v1"
	GlobalWindowRevision         = GlobalDatasetRevision
	globalWindowSlot             = 5 * time.Minute
)

type GlobalWindowSelection struct {
	SchemaVersion         string     `json:"schema_version"`
	CollectorID           string     `json:"collector_id"`
	WindowStartUTC        string     `json:"window_start_utc"`
	WindowEndExclusiveUTC string     `json:"window_end_exclusive_utc"`
	Timezone              string     `json:"timezone"`
	SourceManifestSHA256  string     `json:"source_manifest_sha256"`
	SourceManifestStatus  string     `json:"source_manifest_status"`
	RepairArtifactCount   int        `json:"repair_artifact_count"`
	RIB                   Artifact   `json:"rib"`
	Updates               []Artifact `json:"updates"`
	InputNotes            []string   `json:"input_notes,omitempty"`
}

type GlobalWindowConfig struct {
	RawRoot           string
	SelectionPath     string
	CompatibleMapping string
	RevisedMapping    string
	Output            string
	Workers           int
	SpoolShards       int
	CheckpointShards  int
	RouteCapacity     int
	CheckpointSlots   int
	Resume            bool
	Progress          func(string)
}

func (config GlobalWindowConfig) progress(message string) {
	if config.Progress != nil {
		config.Progress(message)
	}
}

type GlobalWindowPreflightResult struct {
	SchemaVersion       string   `json:"schema_version"`
	RunID               string   `json:"run_id"`
	DatasetID           string   `json:"dataset_id"`
	Revision            string   `json:"revision"`
	CollectorID         string   `json:"collector_id"`
	WindowStartUTC      string   `json:"window_start_utc"`
	WindowEndExclusive  string   `json:"window_end_exclusive_utc"`
	RIB                 Artifact `json:"rib"`
	UpdateCount         int      `json:"update_count"`
	UpdateBytes         int64    `json:"update_compressed_bytes"`
	FirstUpdate         string   `json:"first_update_utc"`
	LastUpdate          string   `json:"last_update_utc"`
	MappingVersion      string   `json:"mapping_version"`
	CountryCodeCount    int      `json:"country_code_count"`
	RepairArtifactCount int      `json:"repair_artifact_count"`
	IdentityCheckMode   string   `json:"identity_check_mode"`
	SelectionSHA256     string   `json:"selection_sha256"`
}

type GlobalWindowSlotQuality struct {
	SchemaVersion string               `json:"schema_version"`
	ArtifactIndex int                  `json:"artifact_index"`
	Artifact      Artifact             `json:"artifact"`
	ParseStats    UpdateParseStats     `json:"parse_stats"`
	Activity      GlobalActivityReport `json:"activity"`
	Conservation  GlobalConservation   `json:"conservation"`
}

type globalWindowMarker struct {
	SchemaVersion   string `json:"schema_version"`
	RunID           string `json:"run_id"`
	DatasetID       string `json:"dataset_id"`
	Revision        string `json:"revision"`
	SelectionSHA256 string `json:"selection_sha256"`
	MappingVersion  string `json:"mapping_version"`
	WindowStartUTC  string `json:"window_start_utc"`
	WindowEndUTC    string `json:"window_end_exclusive_utc"`
}

type globalWindowProgress struct {
	SchemaVersion           string `json:"schema_version"`
	RunID                   string `json:"run_id"`
	DatasetID               string `json:"dataset_id"`
	Revision                string `json:"revision"`
	Phase                   string `json:"phase"`
	ProcessedUpdateCount    int    `json:"processed_update_count"`
	DataThrough             string `json:"data_through"`
	LatestCheckpoint        string `json:"latest_checkpoint"`
	LatestCheckpointSHA256  string `json:"latest_checkpoint_sha256"`
	LatestObservation       string `json:"latest_observation,omitempty"`
	LatestObservationSHA256 string `json:"latest_observation_sha256,omitempty"`
	LatestQuality           string `json:"latest_quality,omitempty"`
	LatestQualitySHA256     string `json:"latest_quality_sha256,omitempty"`
	PreviousDailyProductSHA string `json:"previous_daily_product_sha256,omitempty"`
	UpdatedAt               string `json:"updated_at"`
}

type GlobalWindowRunResult struct {
	SchemaVersion        string `json:"schema_version"`
	Status               string `json:"status"`
	RunID                string `json:"run_id"`
	DatasetID            string `json:"dataset_id"`
	Revision             string `json:"revision"`
	WindowStartUTC       string `json:"window_start_utc"`
	WindowEndExclusive   string `json:"window_end_exclusive_utc"`
	ProcessedUpdateCount int    `json:"processed_update_count"`
	ObservationCount     int64  `json:"country_observation_count"`
	CountryBucketCount   int    `json:"country_bucket_count"`
	RouteStateRows       int64  `json:"route_state_rows"`
	StateDigest          string `json:"state_digest"`
	FinalCheckpoint      string `json:"final_checkpoint"`
	FinalCheckpointSHA   string `json:"final_checkpoint_sha256"`
	FinalDailyProductSHA string `json:"final_daily_product_sha256"`
	Output               string `json:"output"`
}

func parseGlobalWindowSelection(path string) (
	GlobalWindowSelection,
	string,
	time.Time,
	time.Time,
	error,
) {
	var selection GlobalWindowSelection
	raw, err := readJSON(path, &selection)
	if err != nil {
		return selection, "", time.Time{}, time.Time{}, err
	}
	selectionSHA := sha256.Sum256(raw)
	selectionDigest := hex.EncodeToString(selectionSHA[:])
	if selection.SchemaVersion != GlobalWindowSelectionVersion ||
		selection.CollectorID != "rrc25" || selection.Timezone != "UTC" {
		return selection, selectionDigest, time.Time{}, time.Time{}, fmt.Errorf(
			"global window selection identity mismatch",
		)
	}
	if _, err := hex.DecodeString(selection.SourceManifestSHA256); err != nil ||
		len(selection.SourceManifestSHA256) != sha256.Size*2 ||
		selection.SourceManifestStatus != "verified_manifest_plus_isolated_repairs" {
		return selection, selectionDigest, time.Time{}, time.Time{}, fmt.Errorf(
			"global window source manifest identity mismatch",
		)
	}
	start, err := time.Parse(time.RFC3339, selection.WindowStartUTC)
	if err != nil || start.Format(time.RFC3339) != selection.WindowStartUTC {
		return selection, selectionDigest, time.Time{}, time.Time{}, fmt.Errorf(
			"window start must be canonical UTC RFC3339",
		)
	}
	end, err := time.Parse(time.RFC3339, selection.WindowEndExclusiveUTC)
	if err != nil || end.Format(time.RFC3339) != selection.WindowEndExclusiveUTC ||
		!start.Before(end) || start.Second() != 0 || start.Minute()%5 != 0 ||
		end.Second() != 0 || end.Minute()%5 != 0 {
		return selection, selectionDigest, time.Time{}, time.Time{}, fmt.Errorf(
			"global window must be a positive five-minute-aligned UTC interval",
		)
	}
	if err := validateArtifact(selection.RIB, "rib"); err != nil {
		return selection, selectionDigest, time.Time{}, time.Time{}, err
	}
	if selection.RIB.ArtifactTimeUTC != selection.WindowStartUTC {
		return selection, selectionDigest, time.Time{}, time.Time{}, fmt.Errorf(
			"seed RIB time must equal window start",
		)
	}
	expectedRIBPath := filepath.ToSlash(filepath.Join(
		"rrc25", start.Format("2006.01"),
		"bview."+start.Format("20060102.1504")+".gz",
	))
	if selection.RIB.RelativePath != expectedRIBPath {
		return selection, selectionDigest, time.Time{}, time.Time{}, fmt.Errorf(
			"seed RIB path mismatch: expected=%s actual=%s",
			expectedRIBPath, selection.RIB.RelativePath,
		)
	}
	expectedCount := int(end.Sub(start) / globalWindowSlot)
	if expectedCount <= 0 || expectedCount > int(^uint16(0)) ||
		len(selection.Updates) != expectedCount {
		return selection, selectionDigest, time.Time{}, time.Time{}, fmt.Errorf(
			"expected %d continuous updates, got %d", expectedCount, len(selection.Updates),
		)
	}
	seenIDs := make(map[string]struct{}, len(selection.Updates)+1)
	seenIDs[selection.RIB.ArtifactID] = struct{}{}
	for index, artifact := range selection.Updates {
		if err := validateArtifact(artifact, "update"); err != nil {
			return selection, selectionDigest, time.Time{}, time.Time{}, fmt.Errorf(
				"update %d: %w", index, err,
			)
		}
		expected := start.Add(time.Duration(index) * globalWindowSlot).Format(time.RFC3339)
		if artifact.ArtifactTimeUTC != expected {
			return selection, selectionDigest, time.Time{}, time.Time{}, fmt.Errorf(
				"update %d is not continuous: expected=%s actual=%s",
				index, expected, artifact.ArtifactTimeUTC,
			)
		}
		expectedTime := start.Add(time.Duration(index) * globalWindowSlot)
		expectedPath := filepath.ToSlash(filepath.Join(
			"rrc25", expectedTime.Format("2006.01"),
			"updates."+expectedTime.Format("20060102.1504")+".gz",
		))
		if artifact.RelativePath != expectedPath {
			return selection, selectionDigest, time.Time{}, time.Time{}, fmt.Errorf(
				"update %d path mismatch: expected=%s actual=%s",
				index, expectedPath, artifact.RelativePath,
			)
		}
		if _, exists := seenIDs[artifact.ArtifactID]; exists {
			return selection, selectionDigest, time.Time{}, time.Time{}, fmt.Errorf(
				"duplicate artifact id at update %d", index,
			)
		}
		seenIDs[artifact.ArtifactID] = struct{}{}
	}
	if selection.RepairArtifactCount < 0 ||
		selection.RepairArtifactCount > len(selection.Updates) {
		return selection, selectionDigest, time.Time{}, time.Time{}, fmt.Errorf(
			"repair artifact count is invalid",
		)
	}
	return selection, selectionDigest, start, end, nil
}

func validateGlobalWindowFiles(rawRoot string, selection GlobalWindowSelection) error {
	if rawRoot == "" {
		return fmt.Errorf("raw root is required")
	}
	artifacts := make([]Artifact, 0, len(selection.Updates)+1)
	artifacts = append(artifacts, selection.RIB)
	artifacts = append(artifacts, selection.Updates...)
	for _, artifact := range artifacts {
		path := filepath.Join(rawRoot, filepath.FromSlash(artifact.RelativePath))
		info, err := os.Lstat(path)
		if err != nil {
			return fmt.Errorf("stat %s: %w", path, err)
		}
		if !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 ||
			info.Size() != artifact.SizeBytes {
			return fmt.Errorf("input identity mismatch: %s", path)
		}
	}
	return nil
}

func globalWindowIdentity(
	selection GlobalWindowSelection,
	selectionSHA string,
	mapping *GlobalCountryMapping,
) (string, string) {
	runID := stableID("global_window_run_v1_", map[string]any{
		"engine_version":       GlobalEngineVersion,
		"selection_sha256":     selectionSHA,
		"mapping_version":      mapping.MappingVersion,
		"window_start":         selection.WindowStartUTC,
		"window_end_exclusive": selection.WindowEndExclusiveUTC,
	}, 32)
	datasetID := stableID("global_window_dataset_v1_", map[string]any{
		"run_id":       runID,
		"revision":     GlobalWindowRevision,
		"collector_id": "rrc25",
	}, 32)
	return runID, datasetID
}

func PreflightGlobalWindow(config GlobalWindowConfig) (GlobalWindowPreflightResult, error) {
	selection, selectionSHA, _, _, err := parseGlobalWindowSelection(config.SelectionPath)
	if err != nil {
		return GlobalWindowPreflightResult{}, err
	}
	mapping, err := LoadGlobalCountryMapping(config.CompatibleMapping, config.RevisedMapping)
	if err != nil {
		return GlobalWindowPreflightResult{}, err
	}
	if err := validateGlobalWindowFiles(config.RawRoot, selection); err != nil {
		return GlobalWindowPreflightResult{}, err
	}
	runID, datasetID := globalWindowIdentity(selection, selectionSHA, mapping)
	updateBytes := int64(0)
	for _, artifact := range selection.Updates {
		updateBytes += artifact.SizeBytes
	}
	return GlobalWindowPreflightResult{
		SchemaVersion: "rrc25-global-window-preflight/v1",
		RunID:         runID, DatasetID: datasetID, Revision: GlobalWindowRevision,
		CollectorID: "rrc25", WindowStartUTC: selection.WindowStartUTC,
		WindowEndExclusive: selection.WindowEndExclusiveUTC,
		RIB:                selection.RIB, UpdateCount: len(selection.Updates),
		UpdateBytes:         updateBytes,
		FirstUpdate:         selection.Updates[0].ArtifactTimeUTC,
		LastUpdate:          selection.Updates[len(selection.Updates)-1].ArtifactTimeUTC,
		MappingVersion:      mapping.MappingVersion,
		CountryCodeCount:    len(mapping.CountryCodes()),
		RepairArtifactCount: selection.RepairArtifactCount,
		IdentityCheckMode:   "preflight_stat_then_stream_sha256_during_parse",
		SelectionSHA256:     selectionSHA,
	}, nil
}

func prepareGlobalWindowOutput(
	config GlobalWindowConfig,
	selection GlobalWindowSelection,
	selectionSHA string,
	mapping *GlobalCountryMapping,
	runID string,
	datasetID string,
) error {
	marker := globalWindowMarker{
		SchemaVersion: GlobalWindowRunVersion,
		RunID:         runID, DatasetID: datasetID, Revision: GlobalWindowRevision,
		SelectionSHA256: selectionSHA, MappingVersion: mapping.MappingVersion,
		WindowStartUTC: selection.WindowStartUTC,
		WindowEndUTC:   selection.WindowEndExclusiveUTC,
	}
	info, err := os.Lstat(config.Output)
	if err == nil {
		if !config.Resume || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("global window output exists; use --resume for the same run")
		}
		var existing globalWindowMarker
		if _, err := readJSON(filepath.Join(config.Output, "RUNNING.json"), &existing); err != nil {
			return err
		}
		if existing != marker {
			return fmt.Errorf("global window RUNNING identity mismatch")
		}
		return nil
	}
	if !os.IsNotExist(err) {
		return err
	}
	if config.Resume {
		return fmt.Errorf("cannot resume absent global window output")
	}
	if err := os.MkdirAll(config.Output, 0o750); err != nil {
		return err
	}
	for _, relative := range []string{"observations", "quality", "checkpoints"} {
		if err := os.MkdirAll(filepath.Join(config.Output, relative), 0o750); err != nil {
			return err
		}
	}
	if err := writeJSONAtomic(filepath.Join(config.Output, "RUNNING.json"), marker); err != nil {
		return err
	}
	if err := writeJSONAtomic(filepath.Join(config.Output, "input-summary.json"), map[string]any{
		"schema_version":   GlobalWindowSelectionVersion,
		"selection_sha256": selectionSHA,
		"selection":        selection,
	}); err != nil {
		return err
	}
	return writeJSONAtomic(filepath.Join(config.Output, "mapping-summary.json"), map[string]any{
		"schema_version":    "rrc25-global-window-mapping/v1",
		"mapping_version":   mapping.MappingVersion,
		"compatible_sha256": mapping.CompatibleSHA256,
		"revised_sha256":    mapping.RevisedSHA256,
		"country_codes":     mapping.CountryCodes(),
	})
}

func writeGlobalWindowProgress(output string, progress globalWindowProgress) error {
	progress.SchemaVersion = "rrc25-global-window-progress/v1"
	progress.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
	return writeJSONAtomic(filepath.Join(output, "progress.json"), progress)
}

func loadGlobalWindowProgress(output string) (globalWindowProgress, error) {
	var progress globalWindowProgress
	_, err := readJSON(filepath.Join(output, "progress.json"), &progress)
	return progress, err
}

func validateGlobalWindowProgress(
	progress globalWindowProgress,
	selection GlobalWindowSelection,
	runID string,
	datasetID string,
) error {
	if progress.SchemaVersion != "rrc25-global-window-progress/v1" ||
		progress.RunID != runID || progress.DatasetID != datasetID ||
		progress.Revision != GlobalWindowRevision {
		return fmt.Errorf("global window progress identity mismatch")
	}
	if progress.ProcessedUpdateCount < 0 ||
		progress.ProcessedUpdateCount > len(selection.Updates) {
		return fmt.Errorf("global window progress count is invalid")
	}
	start, _ := time.Parse(time.RFC3339, selection.WindowStartUTC)
	expectedDataThrough := start.Add(
		time.Duration(progress.ProcessedUpdateCount) * globalWindowSlot,
	).Format(time.RFC3339)
	if progress.DataThrough != expectedDataThrough {
		return fmt.Errorf(
			"global window progress data_through mismatch: expected=%s actual=%s",
			expectedDataThrough, progress.DataThrough,
		)
	}
	switch progress.Phase {
	case "spooling", "spooled":
		if progress.ProcessedUpdateCount != 0 {
			return fmt.Errorf("spool phase cannot contain replay progress")
		}
	case "replay":
	case "complete":
		return fmt.Errorf("progress is complete but COMPLETE.json is absent")
	default:
		return fmt.Errorf("global window progress phase is invalid")
	}
	return nil
}

func writeJSONLinesGzipAtomic(path string, values []any) (string, int64, error) {
	temporary := path + ".tmp"
	if err := os.Remove(temporary); err != nil && !os.IsNotExist(err) {
		return "", 0, err
	}
	file, err := os.OpenFile(temporary, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o640)
	if err != nil {
		return "", 0, err
	}
	failed := true
	defer func() {
		if failed {
			_ = file.Close()
			_ = os.Remove(temporary)
		}
	}()
	compressed, err := gzip.NewWriterLevel(file, gzip.BestSpeed)
	if err != nil {
		return "", 0, err
	}
	compressed.Name = ""
	compressed.Comment = ""
	compressed.ModTime = time.Time{}
	writer := bufio.NewWriterSize(compressed, 1<<20)
	encoder := json.NewEncoder(writer)
	encoder.SetEscapeHTML(false)
	for _, value := range values {
		if err := encoder.Encode(value); err != nil {
			return "", 0, err
		}
	}
	if err := writer.Flush(); err != nil {
		return "", 0, err
	}
	if err := compressed.Close(); err != nil {
		return "", 0, err
	}
	if err := file.Sync(); err != nil {
		return "", 0, err
	}
	if err := file.Close(); err != nil {
		return "", 0, err
	}
	temporarySHA, temporarySize, err := sha256File(temporary)
	if err != nil {
		return "", 0, err
	}
	if _, err := os.Lstat(path); err == nil {
		existingSHA, existingSize, err := sha256File(path)
		if err != nil {
			return "", 0, err
		}
		if existingSHA != temporarySHA || existingSize != temporarySize {
			return "", 0, fmt.Errorf("immutable daily output mismatch: %s", path)
		}
		if err := os.Remove(temporary); err != nil {
			return "", 0, err
		}
		failed = false
		return existingSHA, existingSize, nil
	} else if !os.IsNotExist(err) {
		return "", 0, err
	}
	if err := os.Rename(temporary, path); err != nil {
		return "", 0, err
	}
	failed = false
	return temporarySHA, temporarySize, nil
}

func dailyProductSHA(observationSHA string, qualitySHA string) string {
	digest := sha256.Sum256([]byte(observationSHA + "\n" + qualitySHA + "\n"))
	return hex.EncodeToString(digest[:])
}

func interfaceValues[T any](values []T) []any {
	result := make([]any, len(values))
	for index := range values {
		result[index] = values[index]
	}
	return result
}

func globalBaselineCountryCount(state *GlobalReplayState) int {
	count := 0
	for _, population := range state.Countries {
		if population.BaselinePrefixVP > 0 {
			count++
		}
	}
	return count
}

func ensureGlobalWindowCheckpoint(
	directory string,
	state *GlobalReplayState,
	manifest GlobalContinuationCheckpointManifest,
) (GlobalContinuationCheckpointManifest, string, error) {
	if _, err := os.Lstat(directory); err == nil {
		restored, existing, sha, err := LoadGlobalContinuationCheckpoint(
			directory, state.Mapping,
		)
		if err != nil {
			return existing, sha, err
		}
		if existing.RunID != manifest.RunID || existing.DatasetID != manifest.DatasetID ||
			existing.DataThrough != manifest.DataThrough ||
			existing.ProcessedUpdateCount != manifest.ProcessedUpdateCount ||
			existing.StateDigest != state.StateDigest.Hex() ||
			restored.StateDigest.Hex() != state.StateDigest.Hex() {
			return existing, sha, fmt.Errorf("immutable daily checkpoint mismatch")
		}
		return existing, sha, nil
	} else if !os.IsNotExist(err) {
		return manifest, "", err
	}
	return WriteGlobalContinuationCheckpoint(directory, state, manifest)
}

func seedOrRestoreGlobalWindow(
	config GlobalWindowConfig,
	selection GlobalWindowSelection,
	mapping *GlobalCountryMapping,
	progress globalWindowProgress,
) (*GlobalReplayState, globalWindowProgress, error) {
	if progress.ProcessedUpdateCount > 0 {
		checkpoint := filepath.Join(config.Output, filepath.FromSlash(progress.LatestCheckpoint))
		state, manifest, checkpointSHA, err := LoadGlobalContinuationCheckpoint(
			checkpoint, mapping,
		)
		if err != nil {
			return nil, progress, err
		}
		if manifest.RunID != progress.RunID || manifest.DatasetID != progress.DatasetID ||
			manifest.ProcessedUpdateCount != progress.ProcessedUpdateCount ||
			manifest.DataThrough != progress.DataThrough ||
			checkpointSHA != progress.LatestCheckpointSHA256 {
			return nil, progress, fmt.Errorf("global window resume checkpoint mismatch")
		}
		config.progress(fmt.Sprintf(
			"从日 checkpoint 恢复：slots=%d data_through=%s",
			progress.ProcessedUpdateCount, progress.DataThrough,
		))
		return state, progress, nil
	}
	checkpointRoot := filepath.Join(config.Output, "checkpoints")
	manifestPath := filepath.Join(checkpointRoot, "rib", "manifest.json")
	var state *GlobalReplayState
	if _, err := os.Stat(manifestPath); err == nil {
		loaded, _, err := LoadGlobalRIBCheckpoint(checkpointRoot, mapping, selection.RIB)
		if err != nil {
			return nil, progress, err
		}
		state = loaded
		config.progress("从 2/24 00:00 RIB checkpoint 恢复全球初始状态")
	} else if !os.IsNotExist(err) {
		return nil, progress, err
	} else {
		config.progress("开始完整读取 2/24 00:00 RIB，建立唯一全球初始状态")
		loaded, manifest, quality, err := SeedGlobalRIBAt(
			config.RawRoot, selection.RIB, mapping, checkpointRoot,
			config.CheckpointShards, config.RouteCapacity,
			selection.WindowStartUTC, config.Progress,
		)
		if err != nil {
			return nil, progress, err
		}
		state = loaded
		if err := writeJSONAtomic(filepath.Join(config.Output, "rib-quality.json"), quality); err != nil {
			return nil, progress, err
		}
		config.progress(fmt.Sprintf(
			"全球 RIB 完成：routes=%d countries=%d state=%s",
			manifest.UniqueRouteCount, len(manifest.Countries), manifest.StateDigest,
		))
	}
	ribSHA, _, err := sha256File(manifestPath)
	if err != nil {
		return nil, progress, err
	}
	progress.Phase = "replay"
	progress.ProcessedUpdateCount = 0
	progress.DataThrough = selection.WindowStartUTC
	progress.LatestCheckpoint = "checkpoints/rib"
	progress.LatestCheckpointSHA256 = ribSHA
	if err := writeGlobalWindowProgress(config.Output, progress); err != nil {
		return nil, progress, err
	}
	return state, progress, nil
}

func runGlobalWindowReplay(
	config GlobalWindowConfig,
	selection GlobalWindowSelection,
	mapping *GlobalCountryMapping,
	metas []SlotSpoolMeta,
	state *GlobalReplayState,
	progress globalWindowProgress,
) (GlobalWindowRunResult, error) {
	if progress.ProcessedUpdateCount < 0 ||
		progress.ProcessedUpdateCount > len(selection.Updates) ||
		progress.ProcessedUpdateCount%config.CheckpointSlots != 0 {
		return GlobalWindowRunResult{}, fmt.Errorf("resume progress is not at a daily checkpoint")
	}
	startIndex := progress.ProcessedUpdateCount
	baselineCountryCount := globalBaselineCountryCount(state)
	observationCount := int64(startIndex) * int64(baselineCountryCount)
	for dayStart := startIndex; dayStart < len(selection.Updates); {
		dayEnd := dayStart + config.CheckpointSlots
		if dayEnd > len(selection.Updates) {
			dayEnd = len(selection.Updates)
		}
		observations := make(
			[]GlobalCountrySummary, 0, (dayEnd-dayStart)*baselineCountryCount,
		)
		quality := make([]GlobalWindowSlotQuality, 0, dayEnd-dayStart)
		for index := dayStart; index < dayEnd; index++ {
			activity, err := ApplyGlobalSpoolSlot(state, config.Output, metas[index])
			if err != nil {
				return GlobalWindowRunResult{}, fmt.Errorf("apply slot %d: %w", index, err)
			}
			artifact := selection.Updates[index]
			slotStart, _ := time.Parse(time.RFC3339, artifact.ArtifactTimeUTC)
			observedAt := slotStart.Add(globalWindowSlot).Format(time.RFC3339)
			rows, conservation, err := state.SnapshotGlobalCountrySummaries(
				observedAt, artifact.ArtifactTimeUTC, observedAt, activity,
			)
			if err != nil {
				return GlobalWindowRunResult{}, fmt.Errorf("snapshot slot %d: %w", index, err)
			}
			observations = append(observations, rows...)
			quality = append(quality, GlobalWindowSlotQuality{
				SchemaVersion: "rrc25-global-window-slot-quality/v1",
				ArtifactIndex: index, Artifact: artifact,
				ParseStats:   metas[index].Stats,
				Activity:     globalActivityReport(mapping, activity),
				Conservation: conservation,
			})
			if (index+1)%12 == 0 || index+1 == dayEnd {
				config.progress(fmt.Sprintf(
					"全球状态已推进 %d/%d，data_through=%s",
					index+1, len(selection.Updates), observedAt,
				))
			}
		}
		dayStartTime, _ := time.Parse(time.RFC3339, selection.Updates[dayStart].ArtifactTimeUTC)
		dayKey := dayStartTime.Format("20060102")
		observationRelative := filepath.ToSlash(filepath.Join(
			"observations", "country-observation-"+dayKey+".jsonl.gz",
		))
		qualityRelative := filepath.ToSlash(filepath.Join(
			"quality", "slot-quality-"+dayKey+".jsonl.gz",
		))
		observationSHA, _, err := writeJSONLinesGzipAtomic(
			filepath.Join(config.Output, filepath.FromSlash(observationRelative)),
			interfaceValues(observations),
		)
		if err != nil {
			return GlobalWindowRunResult{}, err
		}
		qualitySHA, _, err := writeJSONLinesGzipAtomic(
			filepath.Join(config.Output, filepath.FromSlash(qualityRelative)),
			interfaceValues(quality),
		)
		if err != nil {
			return GlobalWindowRunResult{}, err
		}
		productSHA := dailyProductSHA(observationSHA, qualitySHA)
		dataThroughTime, _ := time.Parse(
			time.RFC3339, selection.Updates[dayEnd-1].ArtifactTimeUTC,
		)
		dataThrough := dataThroughTime.Add(globalWindowSlot).Format(time.RFC3339)
		checkpointKey := strings.NewReplacer("-", "", ":", "").Replace(dataThrough)
		checkpointRelative := filepath.ToSlash(filepath.Join(
			"checkpoints", "daily", checkpointKey,
		))
		checkpointDirectory := filepath.Join(config.Output, filepath.FromSlash(checkpointRelative))
		checkpoint, checkpointSHA, err := ensureGlobalWindowCheckpoint(
			checkpointDirectory, state,
			GlobalContinuationCheckpointManifest{
				RunID: progress.RunID, DatasetID: progress.DatasetID,
				Revision: GlobalWindowRevision, DataThrough: dataThrough,
				ProductSequence: (dayEnd + config.CheckpointSlots - 1) /
					config.CheckpointSlots,
				ProcessedSlot: dayEnd - 1, ProcessedUpdateCount: dayEnd,
				PreviousProductSHA256:    productSHA,
				SourceCheckpointSHA256:   progress.LatestCheckpointSHA256,
				PreviousCheckpointSHA256: progress.LatestCheckpointSHA256,
				ShardCount:               config.CheckpointShards,
			},
		)
		if err != nil {
			return GlobalWindowRunResult{}, err
		}
		observationCount += int64(len(observations))
		progress.Phase = "replay"
		progress.ProcessedUpdateCount = dayEnd
		progress.DataThrough = dataThrough
		progress.LatestCheckpoint = checkpointRelative
		progress.LatestCheckpointSHA256 = checkpointSHA
		progress.LatestObservation = observationRelative
		progress.LatestObservationSHA256 = observationSHA
		progress.LatestQuality = qualityRelative
		progress.LatestQualitySHA256 = qualitySHA
		progress.PreviousDailyProductSHA = productSHA
		if err := writeGlobalWindowProgress(config.Output, progress); err != nil {
			return GlobalWindowRunResult{}, err
		}
		config.progress(fmt.Sprintf(
			"日分区闭合：%s slots=%d checkpoint=%s state=%s",
			dayKey, dayEnd-dayStart, checkpointRelative, checkpoint.StateDigest,
		))
		dayStart = dayEnd
	}
	conservation, err := state.ValidateConservation()
	if err != nil {
		return GlobalWindowRunResult{}, err
	}
	result := GlobalWindowRunResult{
		SchemaVersion: "rrc25-global-window-complete/v1", Status: "complete",
		RunID: progress.RunID, DatasetID: progress.DatasetID,
		Revision:             GlobalWindowRevision,
		WindowStartUTC:       selection.WindowStartUTC,
		WindowEndExclusive:   selection.WindowEndExclusiveUTC,
		ProcessedUpdateCount: len(selection.Updates),
		ObservationCount:     observationCount,
		CountryBucketCount:   baselineCountryCount,
		RouteStateRows:       conservation.RouteStateRows,
		StateDigest:          conservation.StateDigest,
		FinalCheckpoint:      progress.LatestCheckpoint,
		FinalCheckpointSHA:   progress.LatestCheckpointSHA256,
		FinalDailyProductSHA: progress.PreviousDailyProductSHA,
		Output:               config.Output,
	}
	if err := writeJSONAtomic(filepath.Join(config.Output, "COMPLETE.json"), result); err != nil {
		return GlobalWindowRunResult{}, err
	}
	progress.Phase = "complete"
	if err := writeGlobalWindowProgress(config.Output, progress); err != nil {
		return GlobalWindowRunResult{}, err
	}
	return result, nil
}

func RunGlobalWindow(config GlobalWindowConfig) (GlobalWindowRunResult, error) {
	if config.RawRoot == "" || config.SelectionPath == "" ||
		config.CompatibleMapping == "" || config.RevisedMapping == "" ||
		config.Output == "" || config.Workers < 1 || config.SpoolShards < 1 ||
		config.CheckpointShards < 1 || config.RouteCapacity < 0 ||
		config.CheckpointSlots < 1 {
		return GlobalWindowRunResult{}, fmt.Errorf("global window config is incomplete")
	}
	selection, selectionSHA, _, _, err := parseGlobalWindowSelection(config.SelectionPath)
	if err != nil {
		return GlobalWindowRunResult{}, err
	}
	mapping, err := LoadGlobalCountryMapping(config.CompatibleMapping, config.RevisedMapping)
	if err != nil {
		return GlobalWindowRunResult{}, err
	}
	if err := validateGlobalWindowFiles(config.RawRoot, selection); err != nil {
		return GlobalWindowRunResult{}, err
	}
	runID, datasetID := globalWindowIdentity(selection, selectionSHA, mapping)
	if err := prepareGlobalWindowOutput(
		config, selection, selectionSHA, mapping, runID, datasetID,
	); err != nil {
		return GlobalWindowRunResult{}, err
	}
	if _, err := os.Stat(filepath.Join(config.Output, "COMPLETE.json")); err == nil {
		var complete GlobalWindowRunResult
		if _, err := readJSON(filepath.Join(config.Output, "COMPLETE.json"), &complete); err != nil {
			return GlobalWindowRunResult{}, err
		}
		if complete.RunID != runID || complete.DatasetID != datasetID {
			return GlobalWindowRunResult{}, fmt.Errorf("global window COMPLETE identity mismatch")
		}
		return complete, nil
	} else if !os.IsNotExist(err) {
		return GlobalWindowRunResult{}, err
	}
	progress := globalWindowProgress{
		RunID: runID, DatasetID: datasetID, Revision: GlobalWindowRevision,
		Phase: "spooling", ProcessedUpdateCount: 0,
		DataThrough: selection.WindowStartUTC,
	}
	if config.Resume {
		if existing, err := loadGlobalWindowProgress(config.Output); err == nil {
			if err := validateGlobalWindowProgress(
				existing, selection, runID, datasetID,
			); err != nil {
				return GlobalWindowRunResult{}, err
			}
			progress = existing
		} else if !os.IsNotExist(err) {
			return GlobalWindowRunResult{}, err
		}
	}
	config.progress(fmt.Sprintf(
		"开始/恢复 UPDATE RouteDelta 解析：slots=%d workers=%d shards=%d",
		len(selection.Updates), config.Workers, config.SpoolShards,
	))
	metas, err := ParseAllUpdates(
		context.Background(), config.RawRoot, config.Output,
		selection.Updates, config.Workers, config.SpoolShards, config.Progress,
	)
	if err != nil {
		return GlobalWindowRunResult{}, err
	}
	if err := writeJSONAtomic(filepath.Join(config.Output, "spool-summary.json"), map[string]any{
		"schema_version": "rrc25-global-window-spool-summary/v1",
		"run_id":         runID, "slot_count": len(metas),
		"first_slot": selection.Updates[0].ArtifactTimeUTC,
		"last_slot":  selection.Updates[len(selection.Updates)-1].ArtifactTimeUTC,
	}); err != nil {
		return GlobalWindowRunResult{}, err
	}
	if progress.Phase == "spooling" {
		progress.Phase = "spooled"
		if err := writeGlobalWindowProgress(config.Output, progress); err != nil {
			return GlobalWindowRunResult{}, err
		}
	}
	state, progress, err := seedOrRestoreGlobalWindow(
		config, selection, mapping, progress,
	)
	if err != nil {
		return GlobalWindowRunResult{}, err
	}
	return runGlobalWindowReplay(
		config, selection, mapping, metas, state, progress,
	)
}
