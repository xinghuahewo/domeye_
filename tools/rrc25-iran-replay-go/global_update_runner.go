package replay

import (
	"fmt"
	"os"
	"path/filepath"
	"time"
)

type GlobalUpdatesResult struct {
	RunID                  string `json:"run_id"`
	DatasetID              string `json:"dataset_id"`
	Revision               string `json:"revision"`
	SpoolManifestSHA256    string `json:"spool_manifest_sha256"`
	CatchUpProductCount    int    `json:"catch_up_product_count"`
	FormalObservationCount int    `json:"formal_observation_count"`
	DataThrough            string `json:"data_through"`
	FinalStateDigest       string `json:"final_state_digest"`
	Output                 string `json:"output"`
}

func expectedGlobalSpoolSHA(config GlobalConfig) string {
	if config.SpoolManifestSHA != "" {
		return config.SpoolManifestSHA
	}
	return ExpectedGlobalSpoolManifestSHA256
}

func accumulateGlobalActivity(
	quality *GlobalUpdateQuality,
	activity *GlobalSlotActivity,
) {
	quality.CountryMigrations += activity.CountryMigrations
	quality.ReplacementAnnounces += activity.ReplacementAnnounces
	quality.DuplicateAnnounces += activity.DuplicateAnnounces
	quality.DuplicateWithdraws += activity.DuplicateWithdraws
	quality.WithdrawWithoutState += activity.WithdrawWithoutState
	quality.UnknownCountryAnnouncements += activity.AnnouncementsUnknown
	quality.UnknownCountryWithdraws += activity.WithdrawsUnknown
}

func buildGlobalUpdateQuality(
	manifest GlobalSpoolManifest,
	manifestSHA256 string,
) GlobalUpdateQuality {
	result := GlobalUpdateQuality{
		SchemaVersion:         "rrc25-global-update-quality/v1",
		EngineVersion:         GlobalEngineVersion,
		Status:                "pass",
		UpdateArtifactCount:   len(manifest.Slots),
		UpdateUnknownOptional: make(map[uint8]int64),
		SpoolManifestSHA256:   manifestSHA256,
	}
	for _, slot := range manifest.Slots {
		result.UpdatePhysicalRecords += slot.Stats.PhysicalRecords
		result.UpdateRouteEvents += slot.Stats.RouteEvents
		result.UpdateAnnounces += slot.Stats.Announces
		result.UpdateWithdraws += slot.Stats.Withdraws
		result.UpdateUnknownOrigins += slot.Stats.UnknownOrigins
		result.UpdateMalformedOTC += slot.Stats.MalformedOTC
		result.UpdateTreatAsWithdraw += slot.Stats.TreatAsWithdraw
		for attribute, count := range slot.Stats.UnknownOptional {
			result.UpdateUnknownOptional[attribute] += count
		}
	}
	return result
}

func makeGlobalDeltaCheckpoint(
	rib GlobalRIBResult,
	datasetID string,
	phase string,
	processedSlot int,
	formalObservationCount int,
	dataThrough string,
	spoolManifestSHA256 string,
	conservation GlobalConservation,
	products []GlobalProductReference,
) GlobalDeltaCheckpointManifest {
	return GlobalDeltaCheckpointManifest{
		SchemaVersion:          "rrc25-global-delta-checkpoint/v1",
		EngineVersion:          GlobalEngineVersion,
		RunID:                  rib.RunID,
		DatasetID:              datasetID,
		Revision:               GlobalDatasetRevision,
		Phase:                  phase,
		ProcessedSlot:          processedSlot,
		ProcessedUpdateCount:   processedSlot + 1,
		FormalObservationCount: formalObservationCount,
		DataThrough:            dataThrough,
		StateDigest:            conservation.StateDigest,
		BaseRIBCheckpoint:      "checkpoints/rib/manifest.json",
		BaseRIBStateDigest:     rib.Manifest.StateDigest,
		SpoolManifestSHA256:    spoolManifestSHA256,
		MappingVersion:         rib.Mapping.MappingVersion,
		Conservation:           conservation,
		Products: append(
			[]GlobalProductReference(nil), products...,
		),
	}
}

func writeGlobalDeltaCheckpoint(
	output string,
	manifest GlobalDeltaCheckpointManifest,
) (string, string, error) {
	relative := filepath.ToSlash(filepath.Join(
		"checkpoints", manifest.Phase, "manifest.json",
	))
	hash, err := writeJSONImmutable(
		filepath.Join(output, filepath.FromSlash(relative)), manifest,
	)
	return relative, hash, err
}

func validateExistingGlobalProgress(
	progress GlobalProgress,
	runID string,
	datasetID string,
	spoolManifestSHA256 string,
) error {
	if progress.SchemaVersion != "rrc25-global-progress/v1" ||
		progress.RunID != runID {
		return fmt.Errorf("existing global progress identity mismatch")
	}
	if progress.Phase == "rib" && progress.ProductSequence == 0 {
		return nil
	}
	if progress.EngineVersion != GlobalEngineVersion ||
		progress.DatasetID != datasetID ||
		progress.Revision != GlobalDatasetRevision ||
		progress.SpoolManifestSHA256 != spoolManifestSHA256 ||
		progress.ProductSequence < 1 || progress.ProductSequence > 85 ||
		progress.ProcessedSlot < 0 || progress.ProcessedSlot > 83 {
		return fmt.Errorf("existing global update progress identity mismatch")
	}
	return nil
}

func RunGlobalUpdates(config GlobalConfig) (GlobalUpdatesResult, error) {
	if config.SpoolSource == "" {
		return GlobalUpdatesResult{}, fmt.Errorf("global spool source is required")
	}
	rib, err := LoadGlobalRIBFromCheckpoint(config)
	if err != nil {
		return GlobalUpdatesResult{}, err
	}
	spool, spoolSHA256, err := LoadGlobalSpoolManifest(
		config.SpoolSource, rib.Inputs, expectedGlobalSpoolSHA(config),
	)
	if err != nil {
		return GlobalUpdatesResult{}, err
	}
	config.progress("校验冻结的 84 槽 UPDATE spool，不重新解析 MRT")
	if err := VerifySpoolFiles(config.SpoolSource, spool.Slots); err != nil {
		return GlobalUpdatesResult{}, err
	}
	config.progress("84 槽 UPDATE spool 文件哈希校验完成")

	datasetID := globalDatasetID(rib.RunID, spoolSHA256)
	progressPath := filepath.Join(config.Output, "progress.json")
	var previousProgress *GlobalProgress
	if _, err := os.Stat(progressPath); err == nil {
		existing, err := loadGlobalProgress(progressPath)
		if err != nil {
			return GlobalUpdatesResult{}, err
		}
		if err := validateExistingGlobalProgress(
			existing, rib.RunID, datasetID, spoolSHA256,
		); err != nil {
			return GlobalUpdatesResult{}, err
		}
		previousProgress = &existing
	} else if !os.IsNotExist(err) {
		return GlobalUpdatesResult{}, err
	}
	if previousProgress == nil {
		return GlobalUpdatesResult{}, fmt.Errorf(
			"global RIB progress is required before UPDATE application",
		)
	}

	quality := buildGlobalUpdateQuality(spool, spoolSHA256)
	products := make([]GlobalProductReference, 0, 85)
	previousProductSHA256 := ""

	writeProduct := func(
		phase string,
		productIndex int,
		productSequence int,
		artifactIndex int,
		artifact *Artifact,
		observedAt string,
		slotStart string,
		slotEnd string,
		role string,
		activity *GlobalSlotActivity,
		includeASNStates bool,
		formalObservationCount int,
		checkpoint string,
	) (GlobalConservation, error) {
		var observations []GlobalCountryObservation
		var asnStates []GlobalASNStateRow
		var conservation GlobalConservation
		var snapshotErr error
		if includeASNStates {
			observations, asnStates, conservation, snapshotErr =
				rib.State.SnapshotAll(
					observedAt, slotStart, slotEnd, role, activity,
				)
		} else {
			observations, conservation, snapshotErr =
				rib.State.SnapshotCountries(
					observedAt, slotStart, slotEnd, role, activity,
				)
		}
		if snapshotErr != nil {
			return conservation, snapshotErr
		}
		path, relative := globalProductPath(
			config.Output, phase, productIndex,
		)
		var artifactPointer *int
		if artifact != nil && artifactIndex >= 0 {
			artifactPointer = intPointer(artifactIndex)
		}
		product := GlobalSlotProduct{
			SchemaVersion:       "rrc25-global-slot-product/v1",
			EngineVersion:       GlobalEngineVersion,
			RunID:               rib.RunID,
			DatasetID:           datasetID,
			Revision:            GlobalDatasetRevision,
			CollectorID:         "rrc25",
			Phase:               phase,
			ProductSequence:     productSequence,
			ArtifactIndex:       artifactPointer,
			InputArtifact:       artifact,
			ObservedAt:          observedAt,
			DataThrough:         observedAt,
			SlotStartUTC:        slotStart,
			SlotEndExclusiveUTC: slotEnd,
			SlotRole:            role,
			SpoolManifestSHA256: spoolSHA256,
			PreviousProductSHA:  previousProductSHA256,
			ASNStatesIncluded:   includeASNStates,
			Activity: globalActivityReport(
				rib.Mapping, activity,
			),
			Conservation: conservation,
			Countries:    observations,
			ASNStates:    asnStates,
		}
		productSHA256, err := writeGlobalProductImmutable(path, product)
		if err != nil {
			return conservation, err
		}
		reference := GlobalProductReference{
			Sequence: productSequence, Phase: phase,
			ArtifactIndex: artifactPointer, ObservedAt: observedAt,
			Path: relative, SHA256: productSHA256,
			StateDigest: conservation.StateDigest,
		}
		products = append(products, reference)
		previousProductSHA256 = productSHA256
		candidate := GlobalProgress{
			RunID: rib.RunID, DatasetID: datasetID,
			Revision: GlobalDatasetRevision, Phase: phase,
			ProductSequence: productSequence,
			ProcessedSlot:   artifactIndex,
			ProcessedUpdateCount: func() int {
				if artifactIndex < 0 {
					return 0
				}
				return artifactIndex + 1
			}(),
			FormalObservationCount: formalObservationCount,
			DataThrough:            observedAt,
			StateDigest:            conservation.StateDigest,
			LastProductPath:        relative,
			LastProductSHA256:      productSHA256,
			SpoolManifestSHA256:    spoolSHA256,
			Checkpoint:             checkpoint,
		}
		if err := advanceGlobalProgress(
			progressPath, previousProgress, candidate,
		); err != nil {
			return conservation, err
		}
		return conservation, nil
	}

	for index := 0; index < 25; index++ {
		activity, err := ApplyGlobalSpoolSlot(
			rib.State, config.SpoolSource, spool.Slots[index],
		)
		if err != nil {
			return GlobalUpdatesResult{}, fmt.Errorf(
				"apply catch-up slot %d: %w", index, err,
			)
		}
		accumulateGlobalActivity(&quality, activity)
		slot := mustUTC(rib.Inputs.CatchUp[index].ArtifactTimeUTC)
		checkpoint := "checkpoints/rib/manifest.json"
		conservation, err := writeProduct(
			"catch-up", index+1, index+1, index,
			&rib.Inputs.CatchUp[index],
			slot.Add(5*time.Minute).Format(time.RFC3339),
			slot.Format(time.RFC3339),
			slot.Add(5*time.Minute).Format(time.RFC3339),
			"catch_up_slot_end", activity, false, 0, checkpoint,
		)
		if err != nil {
			return GlobalUpdatesResult{}, err
		}
		if index == 24 {
			delta := makeGlobalDeltaCheckpoint(
				rib, datasetID, "catch-up", 24, 0,
				WindowStartUTC, spoolSHA256, conservation, products,
			)
			checkpoint, _, err = writeGlobalDeltaCheckpoint(
				config.Output, delta,
			)
			if err != nil {
				return GlobalUpdatesResult{}, err
			}
			if previousProgress.ProductSequence == 25 {
				candidate := *previousProgress
				candidate.Checkpoint = checkpoint
				candidate.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
				if err := writeJSONAtomic(progressPath, candidate); err != nil {
					return GlobalUpdatesResult{}, err
				}
				*previousProgress = candidate
			}
		}
		config.progress(fmt.Sprintf(
			"全球 catch-up %d/25 已应用并生成国家状态", index+1,
		))
	}

	initialConservation, err := writeProduct(
		"formal", 0, 26, 24, nil,
		WindowStartUTC, WindowStartUTC, WindowStartUTC,
		"window_start", NewGlobalSlotActivity(), true, 1,
		"checkpoints/catch-up/manifest.json",
	)
	if err != nil {
		return GlobalUpdatesResult{}, err
	}
	_ = initialConservation
	config.progress("全球正式窗口起点 18:05 状态已生成")

	var finalConservation GlobalConservation
	for index := 0; index < 59; index++ {
		globalIndex := index + 25
		activity, err := ApplyGlobalSpoolSlot(
			rib.State, config.SpoolSource, spool.Slots[globalIndex],
		)
		if err != nil {
			return GlobalUpdatesResult{}, fmt.Errorf(
				"apply formal slot %d: %w", index, err,
			)
		}
		accumulateGlobalActivity(&quality, activity)
		slot := mustUTC(rib.Inputs.Formal[index].ArtifactTimeUTC)
		checkpoint := "checkpoints/catch-up/manifest.json"
		conservation, err := writeProduct(
			"formal", index+1, index+27, globalIndex,
			&rib.Inputs.Formal[index],
			slot.Add(5*time.Minute).Format(time.RFC3339),
			slot.Format(time.RFC3339),
			slot.Add(5*time.Minute).Format(time.RFC3339),
			"slot_end", activity, true, index+2, checkpoint,
		)
		if err != nil {
			return GlobalUpdatesResult{}, err
		}
		finalConservation = conservation
		if index == 58 {
			delta := makeGlobalDeltaCheckpoint(
				rib, datasetID, "formal", 83, 60,
				WindowEndUTC, spoolSHA256, conservation, products,
			)
			checkpoint, _, err = writeGlobalDeltaCheckpoint(
				config.Output, delta,
			)
			if err != nil {
				return GlobalUpdatesResult{}, err
			}
			if previousProgress.ProductSequence == 85 {
				candidate := *previousProgress
				candidate.Checkpoint = checkpoint
				candidate.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
				if err := writeJSONAtomic(progressPath, candidate); err != nil {
					return GlobalUpdatesResult{}, err
				}
				*previousProgress = candidate
			}
		}
		config.progress(fmt.Sprintf(
			"全球正式窗口 %d/59 已应用并生成全部国家状态", index+1,
		))
	}
	if len(products) != 85 ||
		products[25].ObservedAt != WindowStartUTC ||
		products[84].ObservedAt != WindowEndUTC ||
		previousProgress.ProductSequence != 85 ||
		previousProgress.FormalObservationCount != 60 ||
		previousProgress.DataThrough != WindowEndUTC {
		return GlobalUpdatesResult{}, fmt.Errorf(
			"global observation population is not 25+60",
		)
	}
	quality.CatchUpProductCount = 25
	quality.FormalObservationCount = 60
	quality.LastObservationAt = WindowEndUTC
	quality.FinalStateDigest = finalConservation.StateDigest
	if _, err := writeJSONImmutable(
		filepath.Join(config.Output, "update-quality.json"), quality,
	); err != nil {
		return GlobalUpdatesResult{}, err
	}
	summary := GlobalUpdatesResult{
		RunID: rib.RunID, DatasetID: datasetID,
		Revision:            GlobalDatasetRevision,
		SpoolManifestSHA256: spoolSHA256,
		CatchUpProductCount: 25, FormalObservationCount: 60,
		DataThrough:      WindowEndUTC,
		FinalStateDigest: finalConservation.StateDigest,
		Output:           config.Output,
	}
	if _, err := writeJSONImmutable(
		filepath.Join(config.Output, "updates-summary.json"), summary,
	); err != nil {
		return GlobalUpdatesResult{}, err
	}
	return summary, nil
}

func LoadGlobalDeltaCheckpoint(
	config GlobalConfig,
	phase string,
	verifySpoolFiles bool,
) (
	*GlobalReplayState,
	FixedInputs,
	*GlobalCountryMapping,
	GlobalDeltaCheckpointManifest,
	error,
) {
	var checkpoint GlobalDeltaCheckpointManifest
	if phase != "catch-up" && phase != "formal" {
		return nil, FixedInputs{}, nil, checkpoint, fmt.Errorf(
			"unsupported global delta checkpoint phase %q", phase,
		)
	}
	inputs, err := ValidateAndSelectInputs(config.SelectionPath)
	if err != nil {
		return nil, inputs, nil, checkpoint, err
	}
	mapping, err := LoadGlobalCountryMapping(
		config.CompatibleMapping, config.RevisedMapping,
	)
	if err != nil {
		return nil, inputs, nil, checkpoint, err
	}
	state, ribManifest, err := LoadGlobalRIBCheckpoint(
		filepath.Join(config.Output, "checkpoints"), mapping, inputs.RIB,
	)
	if err != nil {
		return nil, inputs, mapping, checkpoint, err
	}
	if _, err := readJSON(filepath.Join(
		config.Output, "checkpoints", phase, "manifest.json",
	), &checkpoint); err != nil {
		return nil, inputs, mapping, checkpoint, err
	}
	spool, spoolSHA256, err := LoadGlobalSpoolManifest(
		config.SpoolSource, inputs, expectedGlobalSpoolSHA(config),
	)
	if err != nil {
		return nil, inputs, mapping, checkpoint, err
	}
	runID := GlobalRIBRunIdentity(inputs, mapping)
	datasetID := globalDatasetID(runID, spoolSHA256)
	expectedProcessedSlot := 24
	expectedUpdateCount := 25
	expectedFormalCount := 0
	expectedDataThrough := WindowStartUTC
	expectedProductCount := 25
	if phase == "formal" {
		expectedProcessedSlot = 83
		expectedUpdateCount = 84
		expectedFormalCount = 60
		expectedDataThrough = WindowEndUTC
		expectedProductCount = 85
	}
	if checkpoint.SchemaVersion != "rrc25-global-delta-checkpoint/v1" ||
		checkpoint.EngineVersion != GlobalEngineVersion ||
		checkpoint.RunID != runID || checkpoint.DatasetID != datasetID ||
		checkpoint.Revision != GlobalDatasetRevision ||
		checkpoint.Phase != phase ||
		checkpoint.BaseRIBStateDigest != ribManifest.StateDigest ||
		checkpoint.SpoolManifestSHA256 != spoolSHA256 ||
		checkpoint.MappingVersion != mapping.MappingVersion ||
		checkpoint.BaseRIBCheckpoint != "checkpoints/rib/manifest.json" ||
		checkpoint.ProcessedSlot != expectedProcessedSlot ||
		checkpoint.ProcessedUpdateCount != expectedUpdateCount ||
		checkpoint.FormalObservationCount != expectedFormalCount ||
		checkpoint.DataThrough != expectedDataThrough ||
		len(checkpoint.Products) != expectedProductCount {
		return nil, inputs, mapping, checkpoint, fmt.Errorf(
			"global delta checkpoint identity mismatch",
		)
	}
	if verifySpoolFiles {
		if err := VerifySpoolFiles(config.SpoolSource, spool.Slots); err != nil {
			return nil, inputs, mapping, checkpoint, err
		}
	}
	for index := 0; index <= checkpoint.ProcessedSlot; index++ {
		if _, err := ApplyGlobalSpoolSlot(
			state, config.SpoolSource, spool.Slots[index],
		); err != nil {
			return nil, inputs, mapping, checkpoint, err
		}
	}
	conservation, err := state.ValidateConservation()
	if err != nil {
		return nil, inputs, mapping, checkpoint, err
	}
	if state.StateDigest.Hex() != checkpoint.StateDigest ||
		conservation != checkpoint.Conservation {
		return nil, inputs, mapping, checkpoint, fmt.Errorf(
			"global delta checkpoint reconciliation failed",
		)
	}
	for index, product := range checkpoint.Products {
		if product.Sequence != index+1 {
			return nil, inputs, mapping, checkpoint, fmt.Errorf(
				"global delta checkpoint product sequence mismatch",
			)
		}
		actual, _, err := sha256File(filepath.Join(
			config.Output, filepath.FromSlash(product.Path),
		))
		if err != nil || actual != product.SHA256 {
			if err != nil {
				return nil, inputs, mapping, checkpoint, err
			}
			return nil, inputs, mapping, checkpoint, fmt.Errorf(
				"global delta checkpoint product mismatch: %s", product.Path,
			)
		}
	}
	return state, inputs, mapping, checkpoint, nil
}
