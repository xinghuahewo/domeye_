package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"

	replay "domeye/rrc25-iran-replay-go"
)

func main() {
	rawRoot := flag.String("raw-root", "", "冻结 RRC25 MRT 输入根目录")
	selection := flag.String("selection", "", "长窗 selection JSON")
	compatible := flag.String("compatible-mapping", "", "全量 compatible mapping JSON")
	revised := flag.String("revised-mapping", "", "伊朗 revised mapping delta JSON")
	output := flag.String("output", "", "create-only 长窗运行目录")
	phase := flag.String("phase", "preflight", "执行阶段：preflight 或 run")
	workers := flag.Int("workers", 12, "UPDATE 并行解析 worker 数")
	spoolShards := flag.Int("spool-shards", 64, "RouteDelta 稳定 shard 数")
	checkpointShards := flag.Int("checkpoint-shards", 64, "RouteState checkpoint shard 数")
	routeCapacity := flag.Int("route-capacity", 60_000_000, "全球 RouteState 预分配容量")
	checkpointSlots := flag.Int("checkpoint-slots", 288, "每个可恢复 checkpoint 的五分钟槽数")
	resume := flag.Bool("resume", false, "从同一输出目录安全恢复")
	flag.Parse()

	config := replay.GlobalWindowConfig{
		RawRoot: *rawRoot, SelectionPath: *selection,
		CompatibleMapping: *compatible, RevisedMapping: *revised,
		Output: *output, Workers: *workers, SpoolShards: *spoolShards,
		CheckpointShards: *checkpointShards, RouteCapacity: *routeCapacity,
		CheckpointSlots: *checkpointSlots, Resume: *resume,
		Progress: func(message string) { fmt.Fprintln(os.Stderr, message) },
	}
	if *phase == "preflight" {
		result, err := replay.PreflightGlobalWindow(config)
		if err != nil {
			fail(err)
		}
		printJSON(map[string]any{
			"status": "complete", "phase": "preflight", "result": result,
		})
		return
	}
	if *phase != "run" {
		fail(fmt.Errorf("unsupported phase %q", *phase))
	}
	result, err := replay.RunGlobalWindow(config)
	if err != nil {
		fail(err)
	}
	printJSON(map[string]any{
		"status": "complete", "phase": "run", "result": result,
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
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetEscapeHTML(false)
	_ = encoder.Encode(map[string]any{
		"status": "failed", "error_type": fmt.Sprintf("%T", err),
		"error": err.Error(),
	})
	os.Exit(1)
}
