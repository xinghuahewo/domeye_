package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"

	replay "domeye/rrc25-iran-replay-go"
)

func main() {
	var config replay.PrefixVPEvidenceBuildConfig
	var countries string
	flag.StringVar(&config.RouteStateRoot, "route-state-root", "", "S2 RouteState 完成目录")
	flag.StringVar(&config.CompatibleMapping, "compatible-mapping", "", "冻结 compatible mapping")
	flag.StringVar(&config.RevisedMapping, "revised-mapping", "", "冻结 revised mapping")
	flag.StringVar(&countries, "countries", "", "逗号分隔的事件国家代码")
	flag.StringVar(&config.Output, "output", "", "create-only Evidence 输出目录")
	flag.IntVar(&config.PageSize, "page-size", 1000, "每个下钻页的 RouteState 行数")
	flag.Parse()
	for _, country := range strings.Split(countries, ",") {
		if strings.TrimSpace(country) != "" {
			config.Countries = append(config.Countries, strings.TrimSpace(country))
		}
	}
	result, err := replay.BuildPrefixVPEvidence(config)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(result); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
