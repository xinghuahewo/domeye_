package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"

	replay "domeye/rrc25-iran-replay-go"
)

func main() {
	rawRoot := flag.String("raw-root", "", "MRT 数据根目录")
	selection := flag.String("selection", "", "冻结 full-selection.json")
	compatible := flag.String("compatible-mapping", "", "全量 compatible mapping JSON")
	revised := flag.String("revised-mapping", "", "伊朗 revised mapping delta JSON")
	output := flag.String("output", "", "create-only 全球重放目录")
	packageOutput := flag.String(
		"package-output", "", "全部国家查询包的 create-only 输出目录",
	)
	basePackageRoot := flag.String(
		"base-package-root", "", "待连续追加的 60 点全部国家包目录",
	)
	iranBaselinePackage := flag.String(
		"iran-baseline-package", "", "既有伊朗不可变验收包目录",
	)
	continuationCheckpoint := flag.String(
		"continuation-checkpoint", "", "可直接恢复的完整全球 RouteState checkpoint",
	)
	appendOutput := flag.String(
		"append-output", "", "单槽连续追加的 create-only 输出目录",
	)
	appendArtifact := flag.String(
		"append-artifact", "", "待追加 UPDATE 的冻结 Artifact JSON",
	)
	spoolSource := flag.String("spool-source", "", "冻结的 84 槽 UPDATE spool 来源目录")
	spoolManifestSHA := flag.String(
		"spool-manifest-sha256", "",
		"冻结 spool manifest SHA-256；省略时使用验收基线",
	)
	checkpointShards := flag.Int("checkpoint-shards", 64, "全球 RIB checkpoint shard 数")
	routeCapacity := flag.Int("route-capacity", 56_000_000, "全球 RouteState 预分配容量")
	resume := flag.Bool("resume", false, "从同一全球运行目录恢复")
	checkpointPhase := flag.String(
		"checkpoint-phase", "formal",
		"verify-delta 使用的 checkpoint 阶段：catch-up 或 formal",
	)
	phase := flag.String(
		"phase", "rib",
		"执行阶段：preflight、rib、updates、verify-delta 或 country-packages",
	)
	flag.Parse()

	config := replay.GlobalConfig{
		RawRoot: *rawRoot, SelectionPath: *selection,
		CompatibleMapping: *compatible, RevisedMapping: *revised,
		Output: *output, CheckpointShards: *checkpointShards,
		SpoolSource: *spoolSource, SpoolManifestSHA: *spoolManifestSHA,
		RouteCapacity: *routeCapacity, Resume: *resume,
		Progress: func(message string) {
			fmt.Fprintln(os.Stderr, message)
		},
	}
	if *phase == "preflight" {
		result, err := replay.PreflightGlobalReplay(config)
		if err != nil {
			fail(err)
		}
		printJSON(map[string]any{
			"status": "complete", "phase": "preflight", "result": result,
		})
		return
	}
	if *phase == "updates" {
		result, err := replay.RunGlobalUpdates(config)
		if err != nil {
			fail(err)
		}
		printJSON(map[string]any{
			"status": "complete", "phase": "updates", "result": result,
		})
		return
	}
	if *phase == "verify-delta" {
		state, _, _, checkpoint, err := replay.LoadGlobalDeltaCheckpoint(
			config, *checkpointPhase, true,
		)
		if err != nil {
			fail(err)
		}
		conservation, err := state.ValidateConservation()
		if err != nil {
			fail(err)
		}
		printJSON(map[string]any{
			"status": "complete", "phase": "verify-delta",
			"checkpoint_phase": checkpoint.Phase,
			"processed_slot":   checkpoint.ProcessedSlot,
			"data_through":     checkpoint.DataThrough,
			"state_digest":     conservation.StateDigest,
			"route_state_rows": conservation.RouteStateRows,
			"conservation":     conservation,
		})
		return
	}
	if *phase == "country-packages" {
		result, err := replay.BuildGlobalCountryPackages(
			config, *packageOutput, *iranBaselinePackage,
		)
		if err != nil {
			fail(err)
		}
		printJSON(map[string]any{
			"status": "complete", "phase": "country-packages",
			"result": result,
		})
		return
	}
	if *phase == "extend-country-packages" {
		result, err := replay.ExtendGlobalCountryPackages(
			*basePackageRoot, *appendOutput, *packageOutput,
			*iranBaselinePackage,
			func(message string) { fmt.Fprintln(os.Stderr, message) },
		)
		if err != nil {
			fail(err)
		}
		printJSON(map[string]any{
			"status": "complete", "phase": "extend-country-packages",
			"result": result,
		})
		return
	}
	if *phase == "continuation-checkpoint" {
		result, err := replay.BuildGlobalContinuationCheckpoint(config)
		if err != nil {
			fail(err)
		}
		printJSON(map[string]any{
			"status": "complete", "phase": "continuation-checkpoint",
			"result": result,
		})
		return
	}
	if *phase == "append" {
		artifact, err := loadArtifact(*appendArtifact)
		if err != nil {
			fail(err)
		}
		result, err := replay.RunGlobalAppend(
			config, *continuationCheckpoint, *appendOutput, artifact,
		)
		if err != nil {
			fail(err)
		}
		printJSON(map[string]any{
			"status": "complete", "phase": "append", "result": result,
		})
		return
	}
	if *phase == "probe-update" {
		artifact, err := loadArtifact(*appendArtifact)
		if err != nil {
			fail(err)
		}
		stats, err := replay.ParseUpdate(
			*rawRoot, artifact, 84,
			func(replay.ParsedEvent) error { return nil },
		)
		if err != nil {
			fail(err)
		}
		printJSON(map[string]any{
			"status": "complete", "phase": "probe-update",
			"artifact": artifact, "stats": stats,
		})
		return
	}
	if *phase != "rib" {
		fail(fmt.Errorf("unsupported phase %q", *phase))
	}
	result, err := replay.RunGlobalRIB(config)
	if err != nil {
		fail(err)
	}
	observations, conservation, err := result.State.SnapshotCountries(
		replay.CatchUpStartUTC, replay.CatchUpStartUTC,
		replay.CatchUpStartUTC, "seed",
		replay.NewGlobalSlotActivity(),
	)
	if err != nil {
		fail(err)
	}
	var iran any
	for _, observation := range observations {
		if observation.CountryCode == "IR" {
			iran = observation
			break
		}
	}
	if iran == nil {
		fail(fmt.Errorf("global RIB checkpoint has no IR seed cohort"))
	}
	printJSON(map[string]any{
		"status": "complete", "phase": "rib",
		"run_id": result.RunID, "resumed": result.Resumed,
		"output":               result.Output,
		"route_state_rows":     result.Manifest.UniqueRouteCount,
		"country_bucket_count": len(result.Manifest.Countries),
		"state_digest":         result.Manifest.StateDigest,
		"conservation":         conservation,
		"iran_seed":            iran,
	})
}

func loadArtifact(path string) (replay.Artifact, error) {
	var artifact replay.Artifact
	if path == "" {
		return artifact, fmt.Errorf("append artifact JSON is required")
	}
	file, err := os.Open(path)
	if err != nil {
		return artifact, err
	}
	defer file.Close()
	decoder := json.NewDecoder(file)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&artifact); err != nil {
		return artifact, err
	}
	return artifact, nil
}

func printJSON(value any) {
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		fail(err)
	}
}

func fail(err error) {
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetEscapeHTML(false)
	_ = encoder.Encode(map[string]any{
		"status": "failed", "error_type": fmt.Sprintf("%T", err),
		"error": err.Error(),
	})
	os.Exit(1)
}
