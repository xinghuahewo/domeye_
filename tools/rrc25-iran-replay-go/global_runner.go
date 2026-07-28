package replay

import (
	"fmt"
	"os"
	"path/filepath"
	"time"
)

type GlobalConfig struct {
	RawRoot           string
	SelectionPath     string
	CompatibleMapping string
	RevisedMapping    string
	Output            string
	SpoolSource       string
	SpoolManifestSHA  string
	CheckpointShards  int
	RouteCapacity     int
	Resume            bool
	Progress          func(string)
}

func (config GlobalConfig) progress(message string) {
	if config.Progress != nil {
		config.Progress(message)
	}
}

type GlobalRIBResult struct {
	RunID    string                      `json:"run_id"`
	State    *GlobalReplayState          `json:"-"`
	Inputs   FixedInputs                 `json:"-"`
	Mapping  *GlobalCountryMapping       `json:"-"`
	Manifest GlobalRIBCheckpointManifest `json:"checkpoint"`
	Quality  GlobalRIBQuality            `json:"quality"`
	Resumed  bool                        `json:"resumed"`
	Output   string                      `json:"output"`
}

type GlobalPreflightResult struct {
	RunID              string `json:"run_id"`
	RIBSHA256          string `json:"rib_sha256"`
	RIBBytes           int64  `json:"rib_bytes"`
	UpdateCount        int    `json:"update_count"`
	UpdateBytes        int64  `json:"update_bytes"`
	FirstUpdate        string `json:"first_update"`
	LastUpdate         string `json:"last_update"`
	MappingVersion     string `json:"mapping_version"`
	CountryCodeCount   int    `json:"country_code_count"`
	UnknownCountryCode string `json:"unknown_country_code"`
}

type globalRunMarker struct {
	SchemaVersion  string `json:"schema_version"`
	EngineVersion  string `json:"engine_version"`
	RunID          string `json:"run_id"`
	Status         string `json:"status"`
	StartedAt      string `json:"started_at"`
	RIBSHA256      string `json:"rib_sha256"`
	UpdateCount    int    `json:"update_count"`
	MappingVersion string `json:"mapping_version"`
}

func prepareGlobalOutput(
	config GlobalConfig,
	runID string,
	inputs FixedInputs,
	mapping *GlobalCountryMapping,
) error {
	info, err := os.Lstat(config.Output)
	if err == nil {
		if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("global output is not a regular directory")
		}
		if !config.Resume {
			return fmt.Errorf("global output exists; use --resume for the same run")
		}
		var marker globalRunMarker
		if _, err := readJSON(
			filepath.Join(config.Output, "RUNNING.json"), &marker,
		); err != nil {
			return fmt.Errorf("global resume requires RUNNING.json: %w", err)
		}
		if marker.SchemaVersion != "rrc25-global-run/v1" ||
			marker.EngineVersion != GlobalEngineVersion ||
			marker.RunID != runID ||
			marker.RIBSHA256 != inputs.RIB.FileSHA256 ||
			marker.UpdateCount != 84 ||
			marker.MappingVersion != mapping.MappingVersion {
			return fmt.Errorf("global RUNNING identity mismatch")
		}
		return nil
	}
	if !os.IsNotExist(err) {
		return err
	}
	if config.Resume {
		return fmt.Errorf("cannot resume absent global output")
	}
	if err := os.MkdirAll(
		filepath.Join(config.Output, "checkpoints"), 0o750,
	); err != nil {
		return err
	}
	return writeJSONAtomic(
		filepath.Join(config.Output, "RUNNING.json"),
		globalRunMarker{
			SchemaVersion: "rrc25-global-run/v1",
			EngineVersion: GlobalEngineVersion,
			RunID:         runID, Status: "running",
			StartedAt:      time.Now().UTC().Format(time.RFC3339),
			RIBSHA256:      inputs.RIB.FileSHA256,
			UpdateCount:    len(inputs.AllUpdate),
			MappingVersion: mapping.MappingVersion,
		},
	)
}

func writeGlobalRIBMetadata(
	config GlobalConfig,
	runID string,
	inputs FixedInputs,
	mapping *GlobalCountryMapping,
	manifest GlobalRIBCheckpointManifest,
	quality GlobalRIBQuality,
) error {
	if err := writeJSONAtomic(
		filepath.Join(config.Output, "input-summary.json"),
		map[string]any{
			"schema_version": "rrc25-global-input-summary/v1",
			"run_id":         runID, "engine_version": GlobalEngineVersion,
			"rib": inputs.RIB, "catch_up_updates": inputs.CatchUp,
			"formal_updates": inputs.Formal,
			"counts": map[string]any{
				"rib": 1, "catch_up_updates": 25,
				"formal_updates": 59, "update_total": 84,
				"formal_observations": 60,
			},
		},
	); err != nil {
		return err
	}
	if err := writeJSONAtomic(
		filepath.Join(config.Output, "mapping-summary.json"),
		map[string]any{
			"schema_version":    "rrc25-global-mapping-summary/v1",
			"mapping_version":   mapping.MappingVersion,
			"compatible_sha256": mapping.CompatibleSHA256,
			"revised_sha256":    mapping.RevisedSHA256,
			"country_codes":     mapping.CountryCodes(),
			"conflict_count":    mapping.ConflictCount,
			"unknown_row_count": mapping.UnknownRowCount,
		},
	); err != nil {
		return err
	}
	if err := writeJSONAtomic(
		filepath.Join(config.Output, "rib-quality.json"), quality,
	); err != nil {
		return err
	}
	return writeJSONAtomic(
		filepath.Join(config.Output, "progress.json"),
		map[string]any{
			"schema_version": "rrc25-global-progress/v1",
			"run_id":         runID, "phase": "rib",
			"processed_slot": -1,
			"data_through":   CatchUpStartUTC,
			"state_digest":   manifest.StateDigest,
			"checkpoint":     "checkpoints/rib/manifest.json",
			"updated_at":     time.Now().UTC().Format(time.RFC3339),
		},
	)
}

func RunGlobalRIB(config GlobalConfig) (GlobalRIBResult, error) {
	if config.RawRoot == "" || config.SelectionPath == "" ||
		config.CompatibleMapping == "" || config.RevisedMapping == "" ||
		config.Output == "" {
		return GlobalRIBResult{}, fmt.Errorf("global RIB config paths are required")
	}
	if config.CheckpointShards < 1 || config.RouteCapacity < 0 {
		return GlobalRIBResult{}, fmt.Errorf(
			"checkpoint shards and route capacity are invalid",
		)
	}
	inputs, err := ValidateAndSelectInputs(config.SelectionPath)
	if err != nil {
		return GlobalRIBResult{}, err
	}
	mapping, err := LoadGlobalCountryMapping(
		config.CompatibleMapping, config.RevisedMapping,
	)
	if err != nil {
		return GlobalRIBResult{}, err
	}
	if err := ValidateRawFiles(config.RawRoot, inputs); err != nil {
		return GlobalRIBResult{}, err
	}
	runID := GlobalRIBRunIdentity(inputs, mapping)
	if err := prepareGlobalOutput(
		config, runID, inputs, mapping,
	); err != nil {
		return GlobalRIBResult{}, err
	}
	checkpointRoot := filepath.Join(config.Output, "checkpoints")
	manifestPath := filepath.Join(checkpointRoot, "rib", "manifest.json")
	if _, err := os.Stat(manifestPath); err == nil {
		if !config.Resume {
			return GlobalRIBResult{}, fmt.Errorf("global RIB checkpoint exists")
		}
		config.progress("从全球 RIB checkpoint 重建共享状态")
		state, manifest, err := LoadGlobalRIBCheckpoint(
			checkpointRoot, mapping, inputs.RIB,
		)
		if err != nil {
			return GlobalRIBResult{}, err
		}
		var quality GlobalRIBQuality
		if _, err := readJSON(
			filepath.Join(config.Output, "rib-quality.json"), &quality,
		); err != nil {
			return GlobalRIBResult{}, err
		}
		if quality.normalizeCompactMPReach() {
			if err := writeJSONAtomic(
				filepath.Join(config.Output, "rib-quality.json"), quality,
			); err != nil {
				return GlobalRIBResult{}, err
			}
		}
		return GlobalRIBResult{
			RunID: runID, State: state, Inputs: inputs, Mapping: mapping,
			Manifest: manifest, Quality: quality, Resumed: true,
			Output: config.Output,
		}, nil
	} else if !os.IsNotExist(err) {
		return GlobalRIBResult{}, err
	}

	config.progress("完整读取一次 08:00 RIB，保留全部国家与显式未知桶")
	state, manifest, quality, err := SeedGlobalRIB(
		config.RawRoot, inputs.RIB, mapping, checkpointRoot,
		config.CheckpointShards, config.RouteCapacity, config.Progress,
	)
	if err != nil {
		return GlobalRIBResult{}, err
	}
	if err := writeGlobalRIBMetadata(
		config, runID, inputs, mapping, manifest, quality,
	); err != nil {
		return GlobalRIBResult{}, err
	}
	config.progress(fmt.Sprintf(
		"全球 RIB checkpoint 完成：routes=%d countries=%d state=%s",
		len(state.Routes), len(manifest.Countries), manifest.StateDigest,
	))
	return GlobalRIBResult{
		RunID: runID, State: state, Inputs: inputs, Mapping: mapping,
		Manifest: manifest, Quality: quality, Output: config.Output,
	}, nil
}

func LoadGlobalRIBFromCheckpoint(
	config GlobalConfig,
) (GlobalRIBResult, error) {
	if config.SelectionPath == "" ||
		config.CompatibleMapping == "" || config.RevisedMapping == "" ||
		config.Output == "" || !config.Resume {
		return GlobalRIBResult{}, fmt.Errorf(
			"global checkpoint load requires paths and --resume",
		)
	}
	inputs, err := ValidateAndSelectInputs(config.SelectionPath)
	if err != nil {
		return GlobalRIBResult{}, err
	}
	mapping, err := LoadGlobalCountryMapping(
		config.CompatibleMapping, config.RevisedMapping,
	)
	if err != nil {
		return GlobalRIBResult{}, err
	}
	runID := GlobalRIBRunIdentity(inputs, mapping)
	if err := prepareGlobalOutput(
		config, runID, inputs, mapping,
	); err != nil {
		return GlobalRIBResult{}, err
	}
	config.progress(
		"直接从全球 RIB checkpoint 重建共享状态，不读取原始 RIB",
	)
	state, manifest, err := LoadGlobalRIBCheckpoint(
		filepath.Join(config.Output, "checkpoints"), mapping, inputs.RIB,
	)
	if err != nil {
		return GlobalRIBResult{}, err
	}
	var quality GlobalRIBQuality
	if _, err := readJSON(
		filepath.Join(config.Output, "rib-quality.json"), &quality,
	); err != nil {
		return GlobalRIBResult{}, err
	}
	if quality.normalizeCompactMPReach() {
		if err := writeJSONAtomic(
			filepath.Join(config.Output, "rib-quality.json"), quality,
		); err != nil {
			return GlobalRIBResult{}, err
		}
	}
	return GlobalRIBResult{
		RunID: runID, State: state, Inputs: inputs, Mapping: mapping,
		Manifest: manifest, Quality: quality, Resumed: true,
		Output: config.Output,
	}, nil
}

func PreflightGlobalReplay(config GlobalConfig) (GlobalPreflightResult, error) {
	if config.RawRoot == "" || config.SelectionPath == "" ||
		config.CompatibleMapping == "" || config.RevisedMapping == "" {
		return GlobalPreflightResult{}, fmt.Errorf("global preflight paths are required")
	}
	inputs, err := ValidateAndSelectInputs(config.SelectionPath)
	if err != nil {
		return GlobalPreflightResult{}, err
	}
	mapping, err := LoadGlobalCountryMapping(
		config.CompatibleMapping, config.RevisedMapping,
	)
	if err != nil {
		return GlobalPreflightResult{}, err
	}
	if err := ValidateRawFiles(config.RawRoot, inputs); err != nil {
		return GlobalPreflightResult{}, err
	}
	updateBytes := int64(0)
	for _, artifact := range inputs.AllUpdate {
		updateBytes += artifact.SizeBytes
	}
	return GlobalPreflightResult{
		RunID:     GlobalRIBRunIdentity(inputs, mapping),
		RIBSHA256: inputs.RIB.FileSHA256, RIBBytes: inputs.RIB.SizeBytes,
		UpdateCount: len(inputs.AllUpdate), UpdateBytes: updateBytes,
		FirstUpdate:        inputs.AllUpdate[0].ArtifactTimeUTC,
		LastUpdate:         inputs.AllUpdate[len(inputs.AllUpdate)-1].ArtifactTimeUTC,
		MappingVersion:     mapping.MappingVersion,
		CountryCodeCount:   len(mapping.CountryCodes()),
		UnknownCountryCode: UnknownCountryCode,
	}, nil
}
