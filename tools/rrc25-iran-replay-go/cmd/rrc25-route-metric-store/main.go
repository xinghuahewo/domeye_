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
	routeState := flag.String("route-state-root", "", "S2 唯一 RouteState 制品目录")
	rawRoot := flag.String("raw-root", "", "S1 冻结 RRC25 原始输入根目录")
	selection := flag.String("selection", "", "S1 冻结 selection JSON")
	compatible := flag.String("compatible-mapping", "", "冻结 compatible mapping JSON")
	revised := flag.String("revised-mapping", "", "冻结 revised mapping JSON")
	output := flag.String("output", "", "create-only S3 指标文件候选目录")
	routeEventImplementation := flag.String(
		"route-event-implementation-id", "", "S1 RouteEvent 的 git:<40位提交SHA>",
	)
	routeStateImplementation := flag.String(
		"route-state-implementation-id", "", "S2 RouteState 的 git:<40位提交SHA>",
	)
	implementation := flag.String(
		"implementation-id", "", "S3 指标投影器的 git:<40位提交SHA>",
	)
	phase := flag.String("phase", "preflight", "执行阶段：preflight、pilot 或 run")
	workers := flag.Int("workers", replay.RouteMetricDefaultParseWorkers, "RouteEvent 并行解析 worker 数")
	resume := flag.Bool("resume", false, "完整候选逐文件复核；不接续未完成候选")
	flag.Parse()

	config := replay.RouteMetricStoreConfig{
		RouteEventRoot: *routeEvents, RouteStateRoot: *routeState,
		RawRoot: *rawRoot, SelectionPath: *selection,
		CompatibleMappingPath: *compatible, RevisedMappingPath: *revised,
		Output: *output, RouteEventImplementationID: *routeEventImplementation,
		RouteStateImplementationID: *routeStateImplementation,
		ImplementationID:           *implementation, Workers: *workers, Resume: *resume,
		Progress: func(message string) { fmt.Fprintln(os.Stderr, message) },
	}
	switch *phase {
	case "preflight":
		result, err := replay.PreflightRouteMetricStore(config)
		if err != nil {
			fail(err)
		}
		printJSON(map[string]any{"status": "complete", "phase": "preflight", "result": result})
	case "pilot":
		result, err := replay.PilotRouteMetricFirstSlot(config)
		if err != nil {
			fail(err)
		}
		printJSON(map[string]any{"status": "complete", "phase": "pilot", "result": result})
	case "run":
		result, err := replay.RunRouteMetricStore(config)
		if err != nil {
			fail(err)
		}
		printJSON(map[string]any{"status": "complete", "phase": "run", "result": result})
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
