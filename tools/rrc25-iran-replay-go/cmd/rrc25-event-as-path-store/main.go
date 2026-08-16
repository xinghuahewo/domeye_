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
	eventCohorts := flag.String("event-cohorts", "", "正式 S1 事件 cohort 完成目录")
	eventMetrics := flag.String("event-metrics", "", "正式 S2 事件指标完成目录")
	asSnapshot := flag.String("as-attribute-snapshot", "", "既有 AS 特征页使用的冻结 as_entity.csv")
	output := flag.String("output", "", "create-only S3 AS 属性与路径关联制品目录")
	routeEventImplementation := flag.String("route-event-implementation-id", "", "正式 RouteEvent 实现身份")
	cohortImplementation := flag.String("event-cohort-implementation-id", "", "正式 S1 cohort 实现身份")
	metricImplementation := flag.String("event-metric-implementation-id", "", "正式 S2 指标实现身份")
	implementationID := flag.String("implementation-id", "", "当前 S3 候选实现身份，格式 git:<40位SHA>")
	workers := flag.Int("workers", 16, "AS_PATH 分区读取 worker 数")
	resume := flag.Bool("resume", false, "校验已完成的同一输出身份")
	flag.Parse()

	manifest, err := replay.RunEventASPathStore(replay.EventASPathStoreConfig{
		RouteEventRoot: *routeEvents, EventCohortRoot: *eventCohorts,
		EventMetricRoot: *eventMetrics, ASSnapshotPath: *asSnapshot, Output: *output,
		RouteEventImplementationID:  *routeEventImplementation,
		EventCohortImplementationID: *cohortImplementation,
		EventMetricImplementationID: *metricImplementation,
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
