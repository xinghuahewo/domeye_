package replay

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

type GlobalContinuationResult struct {
	RunID              string `json:"run_id"`
	DatasetID          string `json:"dataset_id"`
	Revision           string `json:"revision"`
	DataThrough        string `json:"data_through"`
	ProductSequence    int    `json:"product_sequence"`
	RouteStateRows     int64  `json:"route_state_rows"`
	StateDigest        string `json:"state_digest"`
	Checkpoint         string `json:"checkpoint"`
	CheckpointSHA256   string `json:"checkpoint_sha256"`
	RestoredWithoutRIB bool   `json:"restored_without_rib"`
}

type GlobalAppendSpoolManifest struct {
	SchemaVersion string        `json:"schema_version"`
	EngineVersion string        `json:"engine_version"`
	DataThrough   string        `json:"data_through"`
	Slot          SlotSpoolMeta `json:"slot"`
}

type GlobalAppendResult struct {
	SchemaVersion              string               `json:"schema_version"`
	EngineVersion              string               `json:"engine_version"`
	Status                     string               `json:"status"`
	RunID                      string               `json:"run_id"`
	DatasetID                  string               `json:"dataset_id"`
	Revision                   string               `json:"revision"`
	PreviousDataThrough        string               `json:"previous_data_through"`
	DataThrough                string               `json:"data_through"`
	ProductSequence            int                  `json:"product_sequence"`
	ArtifactIndex              int                  `json:"artifact_index"`
	InputArtifact              Artifact             `json:"input_artifact"`
	AppendSpoolManifestSHA256  string               `json:"append_spool_manifest_sha256"`
	PreviousCheckpointSHA256   string               `json:"previous_checkpoint_sha256"`
	ProductPath                string               `json:"product_path"`
	ProductSHA256              string               `json:"product_sha256"`
	CheckpointPath             string               `json:"checkpoint_path"`
	CheckpointSHA256           string               `json:"checkpoint_sha256"`
	RouteStateRows             int64                `json:"route_state_rows"`
	StateDigest                string               `json:"state_digest"`
	CountryObservationCount    int                  `json:"country_observation_count"`
	ASNStateCount              int                  `json:"asn_state_count"`
	Activity                   GlobalActivityReport `json:"activity"`
	Conservation               GlobalConservation   `json:"conservation"`
	MalformedOTCAttributes     int64                `json:"malformed_otc_attributes"`
	TreatAsWithdrawRouteEvents int64                `json:"treat_as_withdraw_route_events"`
	LoadedRIB                  bool                 `json:"loaded_rib"`
	ReappliedPriorUpdateCount  int                  `json:"reapplied_prior_update_count"`
}

func newGlobalAppendSpoolManifest(
	artifact Artifact,
	meta SlotSpoolMeta,
) (GlobalAppendSpoolManifest, error) {
	slotStart, err := time.Parse(time.RFC3339, artifact.ArtifactTimeUTC)
	if err != nil {
		return GlobalAppendSpoolManifest{}, fmt.Errorf(
			"append artifact time is invalid: %w", err,
		)
	}
	return GlobalAppendSpoolManifest{
		SchemaVersion: "rrc25-global-append-spool/v2",
		EngineVersion: GlobalEngineVersion,
		DataThrough:   slotStart.Add(5 * time.Minute).Format(time.RFC3339),
		Slot:          meta,
	}, nil
}

func BuildGlobalContinuationCheckpoint(
	config GlobalConfig,
) (GlobalContinuationResult, error) {
	state, _, _, delta, err := LoadGlobalDeltaCheckpoint(
		config, "formal", true,
	)
	if err != nil {
		return GlobalContinuationResult{}, err
	}
	sourcePath := filepath.Join(
		config.Output, "checkpoints", "formal", "manifest.json",
	)
	sourceSHA, _, err := sha256File(sourcePath)
	if err != nil {
		return GlobalContinuationResult{}, err
	}
	if len(delta.Products) == 0 {
		return GlobalContinuationResult{}, fmt.Errorf(
			"formal delta checkpoint has no product chain",
		)
	}
	target := filepath.Join(
		config.Output, "checkpoints", "continuation", "formal-1500",
	)
	manifest, checkpointSHA, err := WriteGlobalContinuationCheckpoint(
		target,
		state,
		GlobalContinuationCheckpointManifest{
			RunID: delta.RunID, DatasetID: delta.DatasetID,
			Revision: delta.Revision, DataThrough: delta.DataThrough,
			ProductSequence:        len(delta.Products),
			ProcessedSlot:          delta.ProcessedSlot,
			ProcessedUpdateCount:   delta.ProcessedUpdateCount,
			PreviousProductSHA256:  delta.Products[len(delta.Products)-1].SHA256,
			SourceCheckpointSHA256: sourceSHA,
			ShardCount:             config.CheckpointShards,
		},
	)
	if err != nil {
		return GlobalContinuationResult{}, err
	}
	config.progress(
		"23:00 完整 RouteState checkpoint 已写入；后续恢复不依赖 RIB 或前 84 槽 UPDATE",
	)
	return GlobalContinuationResult{
		RunID: manifest.RunID, DatasetID: manifest.DatasetID,
		Revision: manifest.Revision, DataThrough: manifest.DataThrough,
		ProductSequence: manifest.ProductSequence,
		RouteStateRows:  manifest.RecordCount, StateDigest: manifest.StateDigest,
		Checkpoint: target, CheckpointSHA256: checkpointSHA,
		RestoredWithoutRIB: true,
	}, nil
}

func prepareGlobalAppendSpool(
	ctx context.Context,
	rawRoot string,
	appendRoot string,
	artifact Artifact,
	artifactIndex int,
	shardCount int,
) (SlotSpoolMeta, string, error) {
	if artifactIndex < 0 || artifactIndex > int(^uint16(0)) {
		return SlotSpoolMeta{}, "", fmt.Errorf("append artifact index out of range")
	}
	if shardCount < 1 {
		return SlotSpoolMeta{}, "", fmt.Errorf("append spool shard count must be positive")
	}
	spoolRoot := filepath.Join(appendRoot, "spool")
	if err := os.MkdirAll(spoolRoot, 0o750); err != nil {
		return SlotSpoolMeta{}, "", err
	}
	meta, err := parseUpdateToSpool(
		ctx, rawRoot, spoolRoot, artifact, artifactIndex, shardCount,
	)
	if err != nil {
		return SlotSpoolMeta{}, "", err
	}
	if err := VerifySpoolFiles(appendRoot, []SlotSpoolMeta{meta}); err != nil {
		return SlotSpoolMeta{}, "", err
	}
	manifest, err := newGlobalAppendSpoolManifest(artifact, meta)
	if err != nil {
		return SlotSpoolMeta{}, "", err
	}
	path := filepath.Join(spoolRoot, "append-manifest.json")
	if err := writeJSONAtomic(path, manifest); err != nil {
		return SlotSpoolMeta{}, "", err
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return SlotSpoolMeta{}, "", err
	}
	digest := sha256.Sum256(raw)
	return meta, hex.EncodeToString(digest[:]), nil
}

func RunGlobalAppend(
	config GlobalConfig,
	checkpointDirectory string,
	appendOutput string,
	artifact Artifact,
) (GlobalAppendResult, error) {
	if config.RawRoot == "" || checkpointDirectory == "" || appendOutput == "" {
		return GlobalAppendResult{}, fmt.Errorf(
			"raw root, continuation checkpoint and append output are required",
		)
	}
	if _, err := os.Lstat(appendOutput); err == nil {
		return GlobalAppendResult{}, fmt.Errorf("append output already exists")
	} else if !os.IsNotExist(err) {
		return GlobalAppendResult{}, err
	}
	if artifact.ArtifactType != "update" ||
		artifact.CollectorID != "rrc25" ||
		artifact.Compression != "gz" ||
		len(artifact.FileSHA256) != 64 ||
		artifact.SizeBytes < 1 {
		return GlobalAppendResult{}, fmt.Errorf("append artifact identity is invalid")
	}
	mapping, err := LoadGlobalCountryMapping(
		config.CompatibleMapping, config.RevisedMapping,
	)
	if err != nil {
		return GlobalAppendResult{}, err
	}
	state, checkpoint, checkpointSHA, err :=
		LoadGlobalContinuationCheckpoint(checkpointDirectory, mapping)
	if err != nil {
		return GlobalAppendResult{}, err
	}
	slotStart, err := time.Parse(time.RFC3339, artifact.ArtifactTimeUTC)
	if err != nil || artifact.ArtifactTimeUTC != checkpoint.DataThrough {
		return GlobalAppendResult{}, fmt.Errorf(
			"append UPDATE is not contiguous with checkpoint data_through",
		)
	}
	if err := os.MkdirAll(appendOutput, 0o750); err != nil {
		return GlobalAppendResult{}, err
	}
	config.progress(
		"已直接恢复 23:00 完整 RouteState；未读取 RIB，未重放前 84 槽 UPDATE",
	)
	artifactIndex := checkpoint.ProcessedUpdateCount
	meta, spoolSHA, err := prepareGlobalAppendSpool(
		context.Background(), config.RawRoot, appendOutput,
		artifact, artifactIndex, checkpoint.ShardCount,
	)
	if err != nil {
		return GlobalAppendResult{}, err
	}
	config.progress("23:00–23:05 UPDATE 已解析为单槽有序 spool")
	activity, err := ApplyGlobalSpoolSlot(state, appendOutput, meta)
	if err != nil {
		return GlobalAppendResult{}, err
	}
	observedAt := slotStart.Add(5 * time.Minute).Format(time.RFC3339)
	observations, asnStates, conservation, err := state.SnapshotAll(
		observedAt, artifact.ArtifactTimeUTC, observedAt,
		"continuous_append_slot_end", activity,
	)
	if err != nil {
		return GlobalAppendResult{}, err
	}
	productSequence := checkpoint.ProductSequence + 1
	productPath, productRelative := globalProductPath(
		appendOutput, "append", productSequence,
	)
	product := GlobalSlotProduct{
		SchemaVersion: "rrc25-global-slot-product/v1",
		EngineVersion: GlobalEngineVersion,
		RunID:         checkpoint.RunID, DatasetID: checkpoint.DatasetID,
		Revision: checkpoint.Revision, CollectorID: "rrc25",
		Phase: "append", ProductSequence: productSequence,
		ArtifactIndex: intPointer(artifactIndex), InputArtifact: &artifact,
		ObservedAt: observedAt, DataThrough: observedAt,
		SlotStartUTC:        artifact.ArtifactTimeUTC,
		SlotEndExclusiveUTC: observedAt,
		SlotRole:            "continuous_append_slot_end",
		SpoolManifestSHA256: spoolSHA,
		PreviousProductSHA:  checkpoint.PreviousProductSHA256,
		ASNStatesIncluded:   true,
		Activity:            globalActivityReport(mapping, activity),
		Conservation:        conservation, Countries: observations,
		ASNStates: asnStates,
	}
	productSHA, err := writeGlobalProductImmutable(productPath, product)
	if err != nil {
		return GlobalAppendResult{}, err
	}
	nextCheckpointDirectory := filepath.Join(
		appendOutput, "checkpoints", "continuation",
		observedAt[0:4]+observedAt[5:7]+observedAt[8:10]+"T"+
			observedAt[11:13]+observedAt[14:16]+observedAt[17:19]+"Z",
	)
	next, nextSHA, err := WriteGlobalContinuationCheckpoint(
		nextCheckpointDirectory,
		state,
		GlobalContinuationCheckpointManifest{
			RunID: checkpoint.RunID, DatasetID: checkpoint.DatasetID,
			Revision: checkpoint.Revision, DataThrough: observedAt,
			ProductSequence:          productSequence,
			ProcessedSlot:            checkpoint.ProcessedSlot + 1,
			ProcessedUpdateCount:     checkpoint.ProcessedUpdateCount + 1,
			PreviousProductSHA256:    productSHA,
			SourceCheckpointSHA256:   productSHA,
			PreviousCheckpointSHA256: checkpointSHA,
			ShardCount:               checkpoint.ShardCount,
		},
	)
	if err != nil {
		return GlobalAppendResult{}, err
	}
	result := GlobalAppendResult{
		SchemaVersion: "rrc25-global-continuous-append/v1",
		EngineVersion: GlobalEngineVersion, Status: "complete",
		RunID: checkpoint.RunID, DatasetID: checkpoint.DatasetID,
		Revision:            checkpoint.Revision,
		PreviousDataThrough: checkpoint.DataThrough,
		DataThrough:         observedAt, ProductSequence: productSequence,
		ArtifactIndex: artifactIndex, InputArtifact: artifact,
		AppendSpoolManifestSHA256: spoolSHA,
		PreviousCheckpointSHA256:  checkpointSHA,
		ProductPath:               productRelative, ProductSHA256: productSHA,
		CheckpointPath:   nextCheckpointDirectory,
		CheckpointSHA256: nextSHA,
		RouteStateRows:   next.RecordCount, StateDigest: next.StateDigest,
		CountryObservationCount:    len(observations),
		ASNStateCount:              len(asnStates),
		Activity:                   product.Activity,
		Conservation:               conservation,
		MalformedOTCAttributes:     meta.Stats.MalformedOTC,
		TreatAsWithdrawRouteEvents: meta.Stats.TreatAsWithdraw,
		LoadedRIB:                  false, ReappliedPriorUpdateCount: 0,
	}
	if _, err := writeJSONImmutable(
		filepath.Join(appendOutput, "append-summary.json"), result,
	); err != nil {
		return GlobalAppendResult{}, err
	}
	if _, err := writeJSONImmutable(
		filepath.Join(appendOutput, "COMPLETE.json"), map[string]any{
			"schema_version": "rrc25-global-continuous-append-complete/v1",
			"engine_version": GlobalEngineVersion,
			"status":         "complete", "run_id": result.RunID,
			"dataset_id": result.DatasetID, "revision": result.Revision,
			"data_through":                 result.DataThrough,
			"product_sha256":               result.ProductSHA256,
			"checkpoint_sha256":            result.CheckpointSHA256,
			"loaded_rib":                   false,
			"reapplied_prior_update_count": 0,
		},
	); err != nil {
		return GlobalAppendResult{}, err
	}
	config.progress(
		"新 UPDATE 已追加至 23:05，并生成下一代可继续恢复的完整 RouteState checkpoint",
	)
	return result, nil
}
