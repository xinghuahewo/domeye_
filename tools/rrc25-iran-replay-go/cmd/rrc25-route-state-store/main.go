package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"

	replay "domeye/rrc25-iran-replay-go"
)

func main() {
	routeEvents := flag.String("route-event-root", "", "S1 完整 RouteEvent 制品目录")
	rawRoot := flag.String("raw-root", "", "S1 冻结 RRC25 原始输入根目录")
	selection := flag.String("selection", "", "S1 冻结 selection JSON")
	compatible := flag.String("compatible-mapping", "", "冻结 compatible mapping JSON")
	revised := flag.String("revised-mapping", "", "冻结 revised mapping JSON")
	output := flag.String("output", "", "create-only RouteState 制品目录")
	auditOutput := flag.String("audit-output", "", "create-only checkpoint 接续审计目录")
	routeEventImplementation := flag.String(
		"route-event-implementation-id", "", "S1 RouteEvent 的 git:<40位提交SHA>",
	)
	implementation := flag.String(
		"implementation-id", "", "S2 RouteState 的 git:<40位提交SHA>",
	)
	phase := flag.String("phase", "preflight", "执行阶段：preflight、pilot、run 或 replay-audit")
	partition := flag.Int("partition-index", 1, "pilot 的 RouteEvent 分区索引")
	workers := flag.Int("workers", replay.RouteStateDefaultParseWorkers, "RouteEvent 并行解析 worker 数")
	checkpointShards := flag.Int(
		"checkpoint-shards", replay.RouteStateDefaultShardCount, "RouteState checkpoint shard 数",
	)
	resume := flag.Bool("resume", false, "校验并续跑同一 RouteState 输出身份")
	flag.Parse()

	config := replay.RouteStateStoreConfig{
		RouteEventRoot: *routeEvents, RawRoot: *rawRoot, SelectionPath: *selection,
		CompatibleMappingPath: *compatible, RevisedMappingPath: *revised,
		Output: *output, RouteEventImplementationID: *routeEventImplementation,
		ImplementationID: *implementation, Workers: *workers,
		CheckpointShards: *checkpointShards, Resume: *resume,
		Progress: func(message string) { fmt.Fprintln(os.Stderr, message) },
	}
	switch *phase {
	case "preflight":
		result, err := replay.PreflightRouteStateStore(config)
		if err != nil {
			fail(err)
		}
		printJSON(map[string]any{"status": "complete", "phase": "preflight", "result": result})
	case "pilot":
		result, err := replay.PilotRouteStatePartition(config, *partition)
		if err != nil {
			fail(err)
		}
		printJSON(map[string]any{"status": "complete", "phase": "pilot", "result": result})
	case "run":
		result, err := replay.RunRouteStateStore(config)
		if err != nil {
			fail(err)
		}
		printJSON(map[string]any{"status": "complete", "phase": "run", "result": result})
	case "replay-audit":
		result, err := replay.AuditRouteStateReplay(config, *auditOutput)
		if err != nil {
			fail(err)
		}
		printJSON(map[string]any{"status": "complete", "phase": "replay-audit", "result": result})
	default:
		fail(fmt.Errorf("unsupported phase %q", *phase))
	}
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
		"status": "failed", "error_type": fmt.Sprintf("%T", err), "error": err.Error(),
	})
	os.Exit(1)
}
