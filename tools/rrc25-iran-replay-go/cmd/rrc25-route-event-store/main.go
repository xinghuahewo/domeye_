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
	selection := flag.String("selection", "", "224-310 长窗 selection JSON")
	output := flag.String("output", "", "create-only RouteEvent 制品目录")
	implementationID := flag.String(
		"implementation-id", "", "候选实现身份，格式为 git:<40位小写提交SHA>",
	)
	phase := flag.String("phase", "preflight", "执行阶段：preflight、artifact 或 run")
	artifactIndex := flag.Int("artifact-index", -1, "artifact 阶段的分区索引；0 为 Seed RIB")
	workers := flag.Int("workers", 8, "原始 artifact 并行解析 worker 数")
	resume := flag.Bool("resume", false, "校验并续跑同一输出身份")
	flag.Parse()

	config := replay.RouteEventStoreConfig{
		RawRoot: *rawRoot, SelectionPath: *selection,
		Output: *output, ImplementationID: *implementationID,
		Workers: *workers, Resume: *resume,
		Progress: func(message string) { fmt.Fprintln(os.Stderr, message) },
	}
	switch *phase {
	case "preflight":
		result, err := replay.PreflightRouteEventStore(config)
		if err != nil {
			fail(err)
		}
		printJSON(map[string]any{
			"status": "complete", "phase": "preflight", "result": result,
		})
	case "run":
		result, err := replay.RunRouteEventStore(config)
		if err != nil {
			fail(err)
		}
		printJSON(map[string]any{
			"status": "complete", "phase": "run", "result": result,
		})
	case "artifact":
		result, err := replay.BuildRouteEventStoreArtifact(config, *artifactIndex)
		if err != nil {
			fail(err)
		}
		printJSON(map[string]any{
			"status": "complete", "phase": "artifact", "result": result,
		})
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
		"status": "failed", "error_type": fmt.Sprintf("%T", err),
		"error": err.Error(),
	})
	os.Exit(1)
}
