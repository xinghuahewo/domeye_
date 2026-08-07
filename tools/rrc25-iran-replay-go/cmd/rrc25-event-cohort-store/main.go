package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"

	replay "domeye/rrc25-iran-replay-go"
)

func main() {
	routeEvents := flag.String("route-events", "", "正式 RouteEvent 完成目录")
	routeState := flag.String("route-state", "", "正式 RouteState 完成目录")
	rawRoot := flag.String("raw-root", "", "冻结 RRC25 MRT 输入根目录")
	selection := flag.String("selection", "", "224-310 selection JSON")
	peerSessions := flag.String("peer-sessions", "", "正式会话事实完成目录")
	compatibleMapping := flag.String("compatible-mapping", "", "冻结全局 ASN 国家映射")
	revisedMapping := flag.String("revised-mapping", "", "冻结伊朗修订映射")
	lifecycle := flag.String("event-lifecycle", "", "冻结事件生命周期快照")
	output := flag.String("output", "", "create-only 事件 cohort 制品目录")
	routeEventImplementation := flag.String("route-event-implementation-id", "", "正式 RouteEvent 实现身份")
	routeStateImplementation := flag.String("route-state-implementation-id", "", "正式 RouteState 实现身份")
	peerSessionImplementation := flag.String("peer-session-implementation-id", "", "会话事实实现身份")
	implementationID := flag.String("implementation-id", "", "当前候选实现身份，格式 git:<40位SHA>")
	workers := flag.Int("workers", 8, "RouteEvent 分区预解析 worker 数")
	resume := flag.Bool("resume", false, "校验并续跑同一输出身份")
	flag.Parse()

	manifest, err := replay.RunEventCohortStore(replay.EventCohortStoreConfig{
		RouteEventRoot: *routeEvents, RouteStateRoot: *routeState,
		RawRoot: *rawRoot, SelectionPath: *selection, PeerSessionRoot: *peerSessions,
		CompatibleMappingPath: *compatibleMapping, RevisedMappingPath: *revisedMapping,
		LifecycleSnapshotPath: *lifecycle, Output: *output,
		RouteEventImplementationID:  *routeEventImplementation,
		RouteStateImplementationID:  *routeStateImplementation,
		PeerSessionImplementationID: *peerSessionImplementation,
		ImplementationID:            *implementationID, Workers: *workers, Resume: *resume,
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
