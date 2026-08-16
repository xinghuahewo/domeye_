package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"

	replay "domeye/rrc25-iran-replay-go"
)

func main() {
	globalWindowRoot := flag.String("global-window-root", "", "已闭合的224-310全球运行目录")
	events := flag.String("events", "", "冻结的国家中断事件列表JSON")
	existingRegistry := flag.String("existing-registry", "", "需要保留历史revision的现行注册表")
	compatible := flag.String("compatible-mapping", "", "全量compatible mapping JSON")
	revised := flag.String("revised-mapping", "", "伊朗revised mapping delta JSON")
	output := flag.String("output", "", "create-only生产发布物目录")
	flag.Parse()

	result, err := replay.BuildGlobalWindowPublication(
		replay.GlobalWindowPublicationConfig{
			GlobalWindowRoot:  *globalWindowRoot,
			EventsPath:        *events,
			ExistingRegistry:  *existingRegistry,
			CompatibleMapping: *compatible,
			RevisedMapping:    *revised,
			Output:            *output,
			Progress:          func(message string) { fmt.Fprintln(os.Stderr, message) },
		},
	)
	if err != nil {
		printJSON(map[string]any{
			"status":     "failed",
			"error_type": fmt.Sprintf("%T", err),
			"error":      err.Error(),
		})
		os.Exit(1)
	}
	printJSON(map[string]any{"status": "complete", "result": result})
}

func printJSON(value any) {
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
