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
	selection := flag.String("selection", "", "224-310 selection JSON")
	routeEvents := flag.String("route-events", "", "正式 RouteEvent 完成目录")
	routeEventImplementation := flag.String("route-event-implementation-id", "", "正式 RouteEvent 实现身份")
	output := flag.String("output", "", "create-only peer session 制品目录")
	implementationID := flag.String("implementation-id", "", "当前候选实现身份，格式 git:<40位SHA>")
	workers := flag.Int("workers", 8, "原始 UPDATE 并行扫描 worker 数")
	resume := flag.Bool("resume", false, "校验并续跑同一输出身份")
	flag.Parse()

	manifest, err := replay.RunPeerSessionStore(replay.PeerSessionStoreConfig{
		RawRoot: *rawRoot, SelectionPath: *selection, RouteEventRoot: *routeEvents,
		RouteEventImplementationID: *routeEventImplementation, Output: *output,
		ImplementationID: *implementationID, Workers: *workers, Resume: *resume,
		Progress: func(message string) { fmt.Fprintln(os.Stderr, message) },
	})
	if err != nil {
		printJSON(map[string]any{"status": "failed", "error_type": fmt.Sprintf("%T", err), "error": err.Error()})
		os.Exit(1)
	}
	printJSON(map[string]any{"status": "complete", "result": manifest})
}

func printJSON(value any) {
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
