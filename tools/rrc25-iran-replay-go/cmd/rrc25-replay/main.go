package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"runtime"

	replay "domeye/rrc25-iran-replay-go"
)

func main() {
	rawRoot := flag.String("raw-root", "", "MRT 数据根目录")
	selection := flag.String("selection", "", "冻结 full-selection.json")
	compatible := flag.String("compatible-mapping", "", "compatible mapping JSON")
	revised := flag.String("revised-mapping", "", "revised mapping JSON")
	output := flag.String("output", "", "create-only 结果目录")
	workers := flag.Int("workers", runtime.NumCPU(), "UPDATE 并行解析 worker 数")
	shards := flag.Int("shards", 32, "稳定状态 shard 数")
	resume := flag.Bool("resume", false, "从同一结果目录 checkpoint 恢复")
	singleUpdate := flag.String(
		"validate-single-update", "",
		"只验证指定 UTC 槽 UPDATE，例如 2026-02-28T09:25:00Z",
	)
	flag.Parse()

	if *rawRoot == "" || *selection == "" {
		fail(fmt.Errorf("--raw-root and --selection are required"))
	}
	if *singleUpdate != "" {
		stats, err := replay.ValidateSingleUpdate(*rawRoot, *selection, *singleUpdate)
		if err != nil {
			fail(err)
		}
		printJSON(map[string]any{
			"status": "complete", "mode": "single_update_validation",
			"artifact_time_utc": *singleUpdate, "stats": stats,
		})
		return
	}
	if *compatible == "" || *revised == "" || *output == "" {
		fail(fmt.Errorf(
			"--compatible-mapping, --revised-mapping and --output are required",
		))
	}
	incident, err := replay.Run(context.Background(), replay.Config{
		RawRoot: *rawRoot, SelectionPath: *selection,
		CompatibleMapping: *compatible, RevisedMapping: *revised,
		Output: *output, Workers: *workers, Shards: *shards, Resume: *resume,
		Progress: func(message string) {
			fmt.Fprintln(os.Stderr, message)
		},
	})
	if err != nil {
		fail(err)
	}
	printJSON(map[string]any{
		"status": "complete", "mode": "full_replay", "incident": incident,
	})
}

func printJSON(value any) {
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		fail(err)
	}
}

func fail(err error) {
	printJSON(map[string]any{
		"status": "failed", "error_type": fmt.Sprintf("%T", err),
		"error": err.Error(),
	})
	os.Exit(1)
}
