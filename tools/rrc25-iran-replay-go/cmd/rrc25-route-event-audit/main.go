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
	output := flag.String("output", "", "已完成 RouteEvent 制品目录")
	storeImplementationID := flag.String(
		"store-implementation-id", "", "被审计制品的 git:<40位小写提交SHA>",
	)
	auditImplementationID := flag.String(
		"audit-implementation-id", "", "本审计器的 git:<40位小写提交SHA>",
	)
	sampleCount := flag.Int(
		"sample-count", replay.RouteEventStoreAuditSamples, "确定性原始元素复算样本数",
	)
	flag.Parse()

	result, err := replay.AuditRouteEventStore(replay.RouteEventStoreAuditConfig{
		StoreConfig: replay.RouteEventStoreConfig{
			RawRoot:          *rawRoot,
			SelectionPath:    *selection,
			Output:           *output,
			ImplementationID: *storeImplementationID,
			Workers:          1,
			Resume:           true,
		},
		AuditImplementationID: *auditImplementationID,
		SampleCount:           *sampleCount,
	})
	if err != nil {
		fail(err)
	}
	printJSON(result)
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
