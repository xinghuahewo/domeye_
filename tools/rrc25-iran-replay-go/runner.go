package replay

import (
	"bufio"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

type Config struct {
	RawRoot           string
	SelectionPath     string
	CompatibleMapping string
	RevisedMapping    string
	Output            string
	Workers           int
	Shards            int
	Resume            bool
	Progress          func(string)
}

func (config Config) progress(message string) {
	if config.Progress != nil {
		config.Progress(message)
	}
}

func latestCheckpoint(checkpointRoot string) (string, error) {
	for _, pattern := range []string{"formal-*.json.gz", "catch-up-*.json.gz", "rib.json.gz"} {
		matches, err := filepath.Glob(filepath.Join(checkpointRoot, pattern))
		if err != nil {
			return "", err
		}
		sort.Strings(matches)
		if len(matches) > 0 {
			return matches[len(matches)-1], nil
		}
	}
	return "", nil
}

func prepareOutput(config Config) error {
	info, err := os.Stat(config.Output)
	if err == nil {
		if !info.IsDir() {
			return fmt.Errorf("output exists and is not a directory")
		}
		if !config.Resume {
			return fmt.Errorf("output exists; use --resume for the same run")
		}
		if _, err := os.Stat(filepath.Join(config.Output, "RUNNING.json")); err != nil {
			return fmt.Errorf("resume requires RUNNING.json")
		}
		return nil
	}
	if !os.IsNotExist(err) {
		return err
	}
	if config.Resume {
		return fmt.Errorf("cannot resume absent output")
	}
	if err := os.MkdirAll(filepath.Join(config.Output, "checkpoints"), 0o750); err != nil {
		return err
	}
	return writeJSONAtomic(filepath.Join(config.Output, "RUNNING.json"), map[string]any{
		"schema_version": "rrc25-iran-go-run/v1",
		"engine_version": EngineVersion,
		"started_at":     time.Now().UTC().Format(time.RFC3339),
		"status":         "running",
	})
}

func loadQuality(path string) (Quality, error) {
	var quality Quality
	_, err := readJSON(path, &quality)
	return quality, err
}

func Run(ctx context.Context, config Config) (Incident, error) {
	if config.Workers < 1 || config.Shards < 1 {
		return Incident{}, fmt.Errorf("workers and shards must be positive")
	}
	inputs, err := ValidateAndSelectInputs(config.SelectionPath)
	if err != nil {
		return Incident{}, err
	}
	mapping, err := LoadCountryMapping(config.CompatibleMapping, config.RevisedMapping)
	if err != nil {
		return Incident{}, err
	}
	if err := ValidateRawFiles(config.RawRoot, inputs); err != nil {
		return Incident{}, err
	}
	if err := prepareOutput(config); err != nil {
		return Incident{}, err
	}
	checkpointRoot := filepath.Join(config.Output, "checkpoints")
	if err := os.MkdirAll(checkpointRoot, 0o750); err != nil {
		return Incident{}, err
	}

	var state *ReplayState
	var checkpoint Checkpoint
	var quality Quality
	checkpointPath, err := latestCheckpoint(checkpointRoot)
	if err != nil {
		return Incident{}, err
	}
	if checkpointPath != "" {
		state, checkpoint, err = LoadCheckpoint(checkpointPath, mapping)
		if err != nil {
			return Incident{}, err
		}
		quality, err = loadQuality(filepath.Join(config.Output, "rib-quality.json"))
		if err != nil {
			return Incident{}, err
		}
		quality.CheckpointResumeCount++
		config.progress("从 checkpoint 恢复：" + filepath.Base(checkpointPath))
	} else {
		config.progress("完整读取 08:00 RIB，仅冻结确定 IR 状态")
		baseline, ribQuality, err := SeedRIB(config.RawRoot, inputs.RIB, mapping)
		if err != nil {
			return Incident{}, err
		}
		state, err = NewReplayState(mapping, baseline)
		if err != nil {
			return Incident{}, err
		}
		quality = ribQuality
		if err := writeJSONAtomic(
			filepath.Join(config.Output, "rib-quality.json"), quality,
		); err != nil {
			return Incident{}, err
		}
		if err := WriteCheckpoint(
			filepath.Join(checkpointRoot, "rib.json.gz"), state, "rib", -1,
		); err != nil {
			return Incident{}, err
		}
		checkpoint = state.checkpoint("rib", -1)
		config.progress(fmt.Sprintf(
			"RIB checkpoint 完成：%d 个 IR Prefix×VP", len(state.Baseline),
		))
	}

	config.progress(fmt.Sprintf(
		"并行解析 84 个 UPDATE：workers=%d shards=%d", config.Workers, config.Shards,
	))
	metas, err := ParseAllUpdates(
		ctx, config.RawRoot, config.Output, inputs.AllUpdate,
		config.Workers, config.Shards, config.Progress,
	)
	if err != nil {
		return Incident{}, err
	}
	if err := VerifySpoolFiles(config.Output, metas); err != nil {
		return Incident{}, err
	}
	config.progress("84 个 UPDATE spool 校验完成")

	catchUpStartIndex := 0
	if checkpoint.Phase == "catch-up" {
		catchUpStartIndex = checkpoint.ProcessedSlot + 1
	} else if checkpoint.Phase == "formal" {
		catchUpStartIndex = 25
	}
	for index := catchUpStartIndex; index < 25; index++ {
		if err := state.ApplySlot(config.Output, metas[index]); err != nil {
			return Incident{}, err
		}
		slot, _ := time.Parse(time.RFC3339, inputs.CatchUp[index].ArtifactTimeUTC)
		observation := state.Snapshot(
			slot.Add(5*time.Minute).Format(time.RFC3339),
			slot.Format(time.RFC3339), slot.Add(5*time.Minute).Format(time.RFC3339),
			"catch_up_slot_end",
			UpdateCounts{
				Announce: metas[index].Stats.Announces,
				Withdraw: metas[index].Stats.Withdraws,
			},
		)
		state.CatchUpMetrics = append(state.CatchUpMetrics, observation)
		path := filepath.Join(checkpointRoot, fmt.Sprintf("catch-up-%03d.json.gz", index+1))
		if err := WriteCheckpoint(path, state, "catch-up", index); err != nil {
			return Incident{}, err
		}
		config.progress(fmt.Sprintf("catch-up %d/25 应用并 checkpoint 完成", index+1))
	}

	formalStartIndex := 0
	if checkpoint.Phase == "formal" {
		formalStartIndex = checkpoint.ProcessedSlot + 1
	}
	if len(state.FormalObservations) == 0 {
		initial := state.Snapshot(
			WindowStartUTC, WindowStartUTC, WindowStartUTC,
			"window_start", UpdateCounts{},
		)
		state.FormalObservations = append(state.FormalObservations, initial)
		if err := WriteCheckpoint(
			filepath.Join(checkpointRoot, "formal-000.json.gz"),
			state, "formal", -1,
		); err != nil {
			return Incident{}, err
		}
	}
	for index := formalStartIndex; index < 59; index++ {
		globalIndex := index + 25
		if err := state.ApplySlot(config.Output, metas[globalIndex]); err != nil {
			return Incident{}, err
		}
		slot, _ := time.Parse(time.RFC3339, inputs.Formal[index].ArtifactTimeUTC)
		observation := state.Snapshot(
			slot.Add(5*time.Minute).Format(time.RFC3339),
			slot.Format(time.RFC3339), slot.Add(5*time.Minute).Format(time.RFC3339),
			"slot_end",
			UpdateCounts{
				Announce: metas[globalIndex].Stats.Announces,
				Withdraw: metas[globalIndex].Stats.Withdraws,
			},
		)
		state.FormalObservations = append(state.FormalObservations, observation)
		path := filepath.Join(checkpointRoot, fmt.Sprintf("formal-%03d.json.gz", index+1))
		if err := WriteCheckpoint(path, state, "formal", index); err != nil {
			return Incident{}, err
		}
		config.progress(fmt.Sprintf("正式窗口 %d/59 应用并 checkpoint 完成", index+1))
	}
	if len(state.CatchUpMetrics) != 25 || len(state.FormalObservations) != 60 ||
		state.FormalObservations[59].ObservedAt != WindowEndUTC {
		return Incident{}, fmt.Errorf("observation population is not 25+60")
	}
	band := BuildNormalBand(state.CatchUpMetrics)
	incident := DeriveIncident(state.FormalObservations, band)
	if err := finalizePackage(
		config, inputs, mapping, metas, state, quality, incident,
	); err != nil {
		return Incident{}, err
	}
	return incident, nil
}

type jsonlGzipWriter struct {
	file       *os.File
	compressed *gzip.Writer
	buffer     *bufio.Writer
	encoder    *json.Encoder
	count      int64
}

func newJSONLGzipWriter(path string) (*jsonlGzipWriter, error) {
	file, err := os.OpenFile(path, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o640)
	if err != nil {
		return nil, err
	}
	compressed, err := gzip.NewWriterLevel(file, gzip.BestSpeed)
	if err != nil {
		file.Close()
		return nil, err
	}
	buffer := bufio.NewWriterSize(compressed, 1<<20)
	return &jsonlGzipWriter{
		file: file, compressed: compressed, buffer: buffer,
		encoder: json.NewEncoder(buffer),
	}, nil
}

func (writer *jsonlGzipWriter) Write(value any) error {
	if err := writer.encoder.Encode(value); err != nil {
		return err
	}
	writer.count++
	return nil
}

func (writer *jsonlGzipWriter) Close() error {
	if err := writer.buffer.Flush(); err != nil {
		return err
	}
	if err := writer.compressed.Close(); err != nil {
		return err
	}
	if err := writer.file.Sync(); err != nil {
		return err
	}
	return writer.file.Close()
}

func writeCohort(path string, state *ReplayState, mapping *CountryMapping) error {
	type member struct {
		ASN           uint32   `json:"asn"`
		AFI           uint8    `json:"afi"`
		PrefixVPCount int      `json:"prefix_vp_count"`
		Prefixes      []string `json:"prefixes"`
	}
	type group struct {
		keys     int
		prefixes map[string]struct{}
	}
	groups := make(map[asnFamilyKey]*group)
	for key, origin := range state.Baseline {
		groupKey := asnFamilyKey{ASN: origin, AFI: key.AFI}
		if groups[groupKey] == nil {
			groups[groupKey] = &group{prefixes: make(map[string]struct{})}
		}
		groups[groupKey].keys++
		groups[groupKey].prefixes[key.Prefix.String()] = struct{}{}
	}
	members := make([]member, 0, len(groups))
	for key, group := range groups {
		prefixes := make([]string, 0, len(group.prefixes))
		for prefix := range group.prefixes {
			prefixes = append(prefixes, prefix)
		}
		sort.Strings(prefixes)
		members = append(members, member{
			ASN: key.ASN, AFI: key.AFI, PrefixVPCount: group.keys, Prefixes: prefixes,
		})
	}
	sort.Slice(members, func(i, j int) bool {
		if members[i].ASN == members[j].ASN {
			return members[i].AFI < members[j].AFI
		}
		return members[i].ASN < members[j].ASN
	})
	return writeJSONAtomic(path, map[string]any{
		"schema_version": "rrc25-country-cohort-go/v1",
		"cohort_id":      state.CohortID, "collector_id": "rrc25",
		"country_code": "IR", "mapping_version": mapping.MappingVersion,
		"seed_observed_at":          CatchUpStartUTC,
		"baseline_origin_asns":      sortedUint32(state.BaselineASNs),
		"baseline_origin_asn_count": len(state.BaselineASNs),
		"baseline_prefix_vp_count":  len(state.Baseline),
		"members":                   members,
	})
}

func writeStateOutputs(config Config, state *ReplayState) error {
	countryWriter, err := newJSONLGzipWriter(
		filepath.Join(config.Output, "country-snapshots.jsonl.gz"),
	)
	if err != nil {
		return err
	}
	for _, observation := range state.FormalObservations {
		if err := countryWriter.Write(observation); err != nil {
			return err
		}
	}
	if err := countryWriter.Close(); err != nil {
		return err
	}
	asnWriter, err := newJSONLGzipWriter(filepath.Join(config.Output, "asn-states.jsonl.gz"))
	if err != nil {
		return err
	}
	for _, observation := range state.FormalObservations {
		for class, asns := range observation.DualClassifications {
			if class == "ipv4_invisible_ipv6_visible" {
				continue
			}
			for _, asn := range asns {
				row := map[string]any{
					"schema_version": "rrc25-country-asn-state-go/v1",
					"snapshot_id":    observation.SnapshotID,
					"observed_at":    observation.ObservedAt,
					"cohort_id":      observation.CohortID,
					"asn":            asn, "classification": class,
					"ipv4_invisible_ipv6_visible": containsUint32(
						observation.DualClassifications["ipv4_invisible_ipv6_visible"], asn,
					),
				}
				if err := asnWriter.Write(row); err != nil {
					return err
				}
			}
		}
	}
	if err := asnWriter.Close(); err != nil {
		return err
	}
	routeWriter, err := newJSONLGzipWriter(filepath.Join(config.Output, "route-states.jsonl.gz"))
	if err != nil {
		return err
	}
	mapping := state.Mapping
	for index, observation := range state.FormalObservations {
		path := filepath.Join(config.Output, "checkpoints", fmt.Sprintf("formal-%03d.json.gz", index))
		snapshotState, _, err := LoadCheckpoint(path, mapping)
		if err != nil {
			return err
		}
		keys := make([]RouteKey, 0, len(snapshotState.Routes))
		for key := range snapshotState.Routes {
			keys = append(keys, key)
		}
		sort.Slice(keys, func(i, j int) bool { return keys[i].Less(keys[j]) })
		for _, key := range keys {
			entry := snapshotState.Routes[key]
			baselineOrigin, baseline := snapshotState.Baseline[key]
			row := map[string]any{
				"schema_version": "rrc25-country-route-state-go/v1",
				"snapshot_id":    observation.SnapshotID,
				"observed_at":    observation.ObservedAt,
				"vp_id":          VPIdentifier(key.PeerIP, key.PeerASN),
				"peer_ip":        key.PeerIP.String(), "peer_asn": key.PeerASN,
				"afi": key.AFI, "prefix": key.Prefix.String(),
				"present":              entry.Value.Present,
				"current_origin_known": entry.Value.OriginKnown,
				"current_origin_asn":   entry.Value.OriginASN,
				"population_role":      map[bool]string{true: "baseline", false: "dynamic"}[baseline],
			}
			if baseline {
				row["baseline_origin_asn"] = baselineOrigin
				row["baseline_member_visible"] = routeVisible(entry.Value, baselineOrigin)
			}
			if err := routeWriter.Write(row); err != nil {
				return err
			}
		}
	}
	return routeWriter.Close()
}

func containsUint32(values []uint32, expected uint32) bool {
	index := sort.Search(len(values), func(index int) bool { return values[index] >= expected })
	return index < len(values) && values[index] == expected
}

func finalizePackage(
	config Config,
	inputs FixedInputs,
	mapping *CountryMapping,
	metas []SlotSpoolMeta,
	state *ReplayState,
	quality Quality,
	incident Incident,
) error {
	if err := writeCohort(filepath.Join(config.Output, "cohort.json"), state, mapping); err != nil {
		return err
	}
	if err := writeStateOutputs(config, state); err != nil {
		return err
	}
	if err := writeJSONAtomic(filepath.Join(config.Output, "incident.json"), incident); err != nil {
		return err
	}
	if err := writeJSONAtomic(filepath.Join(config.Output, "episodes.json"), map[string]any{
		"schema_version": "rrc25-country-episodes-go/v1",
		"incident_id":    incident.IncidentID, "episodes": incident.Episodes,
	}); err != nil {
		return err
	}
	waves := make([]map[string]any, 0, len(incident.Episodes))
	for index, episode := range incident.Episodes {
		waves = append(waves, map[string]any{
			"wave_id": stableID("wave_go_v1_", map[string]any{
				"episode_id": episode.EpisodeID, "ordinal": index + 1,
			}, 32),
			"episode_id": episode.EpisodeID, "ordinal": index + 1,
			"onset_at": episode.OnsetAt, "peak_at": episode.PeakAt,
			"observation_end_at": episode.ObservationEndAt,
			"causal_relation":    "not_assessed",
		})
	}
	if err := writeJSONAtomic(filepath.Join(config.Output, "waves.json"), map[string]any{
		"schema_version": "rrc25-country-waves-go/v1", "waves": waves,
	}); err != nil {
		return err
	}
	inputSummary := map[string]any{
		"schema_version": "rrc25-iran-go-input-summary/v1",
		"engine_version": EngineVersion,
		"rib":            inputs.RIB, "catch_up_updates": inputs.CatchUp,
		"formal_updates": inputs.Formal,
		"counts": map[string]any{
			"rib": 1, "catch_up_updates": 25, "formal_updates": 59,
			"state_observations":      60,
			"rib_compressed_bytes":    ExpectedRIBBytes,
			"update_compressed_bytes": ExpectedUpdateBytes,
			"total_compressed_bytes":  ExpectedTotalBytes,
		},
		"mapping_version": mapping.MappingVersion,
		"worker_count":    config.Workers, "shard_count": config.Shards,
	}
	if err := writeJSONAtomic(filepath.Join(config.Output, "input-summary.json"), inputSummary); err != nil {
		return err
	}
	for _, meta := range metas {
		quality.UpdatePhysicalRecords += meta.Stats.PhysicalRecords
		quality.UpdateRouteEvents += meta.Stats.RouteEvents
		quality.UpdateUnknownOrigins += meta.Stats.UnknownOrigins
		for key, count := range meta.Stats.UnknownOptional {
			quality.UpdateOptionalUnknownAttrs[key] += count
		}
	}
	quality.InputCompressedBytes = ExpectedTotalBytes
	quality.ObservationCount = len(state.FormalObservations)
	quality.LastObservationAt = state.FormalObservations[len(state.FormalObservations)-1].ObservedAt
	quality.Status = "pass"
	if err := writeJSONAtomic(filepath.Join(config.Output, "QUALITY.json"), quality); err != nil {
		return err
	}
	report := buildChineseReport(state, incident, quality)
	if err := os.WriteFile(filepath.Join(config.Output, "重放观察报告.md"), []byte(report), 0o640); err != nil {
		return err
	}
	hashes, err := hashDeliverables(config.Output)
	if err != nil {
		return err
	}
	if err := writeJSONAtomic(filepath.Join(config.Output, "COMPLETE.json"), map[string]any{
		"schema_version": "rrc25-iran-go-complete/v1",
		"engine_version": EngineVersion, "status": "complete",
		"completed_at":      time.Now().UTC().Format(time.RFC3339),
		"observation_count": 60, "last_observation_at": WindowEndUTC,
		"deliverable_sha256": hashes,
	}); err != nil {
		return err
	}
	return os.Remove(filepath.Join(config.Output, "RUNNING.json"))
}

func buildChineseReport(state *ReplayState, incident Incident, quality Quality) string {
	first := state.FormalObservations[0]
	last := state.FormalObservations[len(state.FormalObservations)-1]
	var builder strings.Builder
	builder.WriteString("# RRC25 伊朗事件 18:05–23:00 状态重放观察报告\n\n")
	builder.WriteString("## 执行结论\n\n")
	builder.WriteString(fmt.Sprintf(
		"- 独立 Go 引擎完整读取 1 个 RIB 与 84 个 UPDATE，输出 60 个正式观察点；最后观察时间为 `%s`。\n",
		last.ObservedAt,
	))
	builder.WriteString(fmt.Sprintf(
		"- 固定 cohort 包含 %d 个 origin ASN、%d 个 Prefix×VP；起点可见率 %.6f，窗口末可见率 %.6f。\n",
		first.BaselineASNCount, first.BaselinePrefixVPCount,
		first.VisiblePrefixVPRatio, last.VisiblePrefixVPRatio,
	))
	builder.WriteString(fmt.Sprintf(
		"- QUALITY 状态为 `%s`；UPDATE 路由元素共 %d 条。\n",
		quality.Status, quality.UpdateRouteEvents,
	))
	builder.WriteString("\n## 事件模型\n\n")
	builder.WriteString(fmt.Sprintf("- onset：`%v`\n", incident.OnsetAt))
	builder.WriteString(fmt.Sprintf("- detected：`%v`\n", incident.DetectedAt))
	builder.WriteString(fmt.Sprintf("- peak：`%v`\n", incident.PeakAt))
	builder.WriteString(fmt.Sprintf("- trough：`%v`\n", incident.TroughAt))
	builder.WriteString(fmt.Sprintf("- partial recovery：`%v`\n", incident.PartialRecoveryAt))
	builder.WriteString(fmt.Sprintf("- full recovery：`%v`\n", incident.FullRecoveryAt))
	builder.WriteString(fmt.Sprintf("- recovery state：`%s`\n", incident.RecoveryState))
	builder.WriteString("\n## 解释边界\n\n")
	builder.WriteString("- 本报告只代表 RRC25 的控制面观测，不代表全球路由器或实际用户流量。\n")
	builder.WriteString("- 映射未知、AS_SET 与 confederation 未被补成 IR 或 0。\n")
	builder.WriteString("- 观察窗口止于北京时间 23:00，窗口外恢复不回填本次事件。\n")
	builder.WriteString("- 时间先后不证明前兆导致主事件，也不支持政治、物理线路或行为意图归因。\n")
	return builder.String()
}

func hashDeliverables(root string) (map[string]string, error) {
	names := []string{
		"input-summary.json", "cohort.json", "country-snapshots.jsonl.gz",
		"asn-states.jsonl.gz", "route-states.jsonl.gz", "incident.json",
		"episodes.json", "waves.json", "QUALITY.json", "重放观察报告.md",
	}
	result := make(map[string]string, len(names))
	for _, name := range names {
		file, err := os.Open(filepath.Join(root, name))
		if err != nil {
			return nil, err
		}
		hash := sha256.New()
		if _, err := io.Copy(hash, file); err != nil {
			file.Close()
			return nil, err
		}
		file.Close()
		result[name] = hex.EncodeToString(hash.Sum(nil))
	}
	return result, nil
}

func ValidateSingleUpdate(
	rawRoot, selectionPath, artifactTime string,
) (UpdateParseStats, error) {
	inputs, err := ValidateAndSelectInputs(selectionPath)
	if err != nil {
		return UpdateParseStats{}, err
	}
	for index, artifact := range inputs.AllUpdate {
		if artifact.ArtifactTimeUTC == artifactTime {
			return ParseUpdate(rawRoot, artifact, uint16(index), func(ParsedEvent) error {
				return nil
			})
		}
	}
	return UpdateParseStats{}, fmt.Errorf("artifact time is outside fixed 84-slot input")
}
