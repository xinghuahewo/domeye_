package replay

import (
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

const GlobalWindowPublicationVersion = "rrc25-global-window-publication/v1"

type GlobalWindowPublicationConfig struct {
	GlobalWindowRoot  string
	EventsPath        string
	ExistingRegistry  string
	CompatibleMapping string
	RevisedMapping    string
	Output            string
	Progress          func(string)
}

func (config GlobalWindowPublicationConfig) progress(message string) {
	if config.Progress != nil {
		config.Progress(message)
	}
}

type globalWindowEventRow struct {
	DetailURL       string `json:"detail_url"`
	AttackedCountry string `json:"attacked_country"`
	EventInfo       string `json:"event_info"`
}

type globalWindowEventInput struct {
	Data []globalWindowEventRow `json:"data"`
}

type globalWindowInputSummary struct {
	SchemaVersion string                `json:"schema_version"`
	Selection     GlobalWindowSelection `json:"selection"`
}

type GlobalWindowPublicationEvent struct {
	LegacyReference string `json:"legacy_reference"`
	CountryCode     string `json:"country_code"`
	CountryName     string `json:"country_name"`
	EventTimeUTC    string `json:"event_time_utc"`
	DetectedAt      string `json:"detected_at"`
	IncidentID      string `json:"incident_id"`
	PublicationID   string `json:"publication_id"`
	PackageURI      string `json:"package_uri"`
	Revision        int    `json:"revision"`
}

type GlobalWindowPublicationResult struct {
	SchemaVersion       string                         `json:"schema_version"`
	Status              string                         `json:"status"`
	RunID               string                         `json:"run_id"`
	DatasetID           string                         `json:"dataset_id"`
	CollectorID         string                         `json:"collector_id"`
	WindowStartUTC      string                         `json:"window_start_utc"`
	WindowEndUTC        string                         `json:"window_end_exclusive_utc"`
	ObservationCount    int                            `json:"observation_count_per_country"`
	CountryCount        int                            `json:"country_count"`
	EventCount          int                            `json:"event_count"`
	RegistryPath        string                         `json:"registry_path"`
	RegistrySHA256      string                         `json:"registry_sha256"`
	SpoolManifestSHA256 string                         `json:"spool_manifest_sha256"`
	Events              []GlobalWindowPublicationEvent `json:"events"`
}

type parsedGlobalWindowEvent struct {
	Reference   string
	CountryCode string
	CountryName string
	EventTime   time.Time
	DetectedAt  time.Time
	IncidentID  string
}

type projectionCountryWriter struct {
	Code             string
	Directory        string
	Cohort           GlobalCountryCohortDocument
	Snapshots        *jsonlGzipWriter
	ASNStates        *jsonlGzipWriter
	ObservationCount int
	ASNStateCount    int64
	LastObservedAt   string
}

type projectionCountryArtifact struct {
	Code             string
	Directory        string
	Cohort           GlobalCountryCohortDocument
	ObservationCount int
	ASNStateCount    int64
	LastObservedAt   string
	Hashes           map[string]string
}

func parseGlobalWindowEvents(
	path string,
	windowStart time.Time,
	windowEnd time.Time,
) ([]parsedGlobalWindowEvent, error) {
	var wrapped globalWindowEventInput
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	if err := json.Unmarshal(raw, &wrapped); err != nil || wrapped.Data == nil {
		var rows []globalWindowEventRow
		if fallbackErr := json.Unmarshal(raw, &rows); fallbackErr != nil {
			if err != nil {
				return nil, err
			}
			return nil, fallbackErr
		}
		wrapped.Data = rows
	}
	beijing := time.FixedZone("Asia/Shanghai", 8*60*60)
	seen := make(map[string]struct{})
	result := make([]parsedGlobalWindowEvent, 0, len(wrapped.Data))
	for _, row := range wrapped.Data {
		parts := strings.Split(strings.TrimSpace(row.DetailURL), "/")
		if len(parts) != 5 || parts[0] != "country_outage" ||
			len(parts[2]) != 2 || parts[4] != "r" {
			continue
		}
		parsedLocal, err := time.ParseInLocation(
			"2006-01-02 15:04:05", strings.ReplaceAll(parts[1], "+", " "), beijing,
		)
		if err != nil {
			return nil, fmt.Errorf("event reference time is invalid: %s", row.DetailURL)
		}
		eventUTC := parsedLocal.UTC()
		if eventUTC.Before(windowStart) || !eventUTC.Before(windowEnd) {
			continue
		}
		code := strings.ToUpper(parts[2])
		canonical := strings.Join([]string{
			"country_outage", parsedLocal.Format("2006-01-02 15:04:05"),
			code, parts[3], "r",
		}, "/")
		if _, exists := seen[canonical]; exists {
			return nil, fmt.Errorf("duplicate event reference: %s", canonical)
		}
		seen[canonical] = struct{}{}
		name := strings.TrimSpace(row.AttackedCountry)
		if name == "" {
			name = code
		}
		detectedAt := eventUTC.Truncate(globalWindowSlot)
		if detectedAt.Before(eventUTC) {
			detectedAt = detectedAt.Add(globalWindowSlot)
		}
		firstObservation := windowStart.Add(globalWindowSlot)
		if detectedAt.Before(firstObservation) {
			detectedAt = firstObservation
		}
		if detectedAt.After(windowEnd) {
			detectedAt = windowEnd
		}
		result = append(result, parsedGlobalWindowEvent{
			Reference:   canonical,
			CountryCode: code,
			CountryName: name,
			EventTime:   eventUTC,
			DetectedAt:  detectedAt,
		})
	}
	if len(result) == 0 {
		return nil, fmt.Errorf("event input contains no country outage in the global window")
	}
	sort.Slice(result, func(i, j int) bool { return result[i].Reference < result[j].Reference })
	return result, nil
}

func readRegistryObservations(path string) ([]map[string]any, error) {
	if path == "" {
		return nil, nil
	}
	var payload map[string]any
	if _, err := readJSON(path, &payload); err != nil {
		return nil, err
	}
	if payload["schema_version"] != "country_outage_observation_registry_v1" {
		return nil, fmt.Errorf("existing country outage registry schema mismatch")
	}
	raw, ok := payload["observations"].([]any)
	if !ok {
		return nil, fmt.Errorf("existing country outage registry observations are invalid")
	}
	result := make([]map[string]any, 0, len(raw))
	for _, value := range raw {
		item, ok := value.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("existing registry observation is invalid")
		}
		result = append(result, item)
	}
	return result, nil
}

func sha256Text(value string) string {
	digest := sha256.Sum256([]byte(value))
	return hex.EncodeToString(digest[:])
}

func legacyPublicationID(registration map[string]any) string {
	identity := strings.Join([]string{
		fmt.Sprint(registration["incident_id"]),
		fmt.Sprint(registration["revision"]),
		fmt.Sprint(registration["data_through"]),
		fmt.Sprint(registration["package_uri"]),
	}, "|")
	return "publication_v1_" + sha256Text(identity)[:24]
}

func copyPublicationFields(registration map[string]any) map[string]any {
	result := make(map[string]any)
	for _, key := range []string{
		"publication_id", "package_uri", "revision", "publication_state",
		"observation_state", "data_mode", "data_through", "updated_at",
		"is_final", "publication_kind", "processing_status", "missing_slots",
		"supersedes_publication_id", "correction_reason", "capabilities",
		"resource_source", "artifact_set_id", "run_id", "route_state_row_count",
		"vantage_point_count",
	} {
		if value, exists := registration[key]; exists {
			result[key] = value
		}
	}
	if _, exists := result["publication_id"]; !exists {
		result["publication_id"] = legacyPublicationID(registration)
	}
	if _, exists := result["publication_kind"]; !exists {
		result["publication_kind"] = "baseline"
	}
	return result
}

func existingRegistryByReference(
	observations []map[string]any,
) map[string]map[string]any {
	result := make(map[string]map[string]any, len(observations))
	for _, item := range observations {
		if reference, ok := item["legacy_reference"].(string); ok {
			result[reference] = item
		}
	}
	return result
}

func publicationCapabilities() map[string]any {
	return map[string]any{
		"legacy_summary": map[string]any{"state": "available"},
		"fixed_cohort":   map[string]any{"state": "available"},
		"country_resources": map[string]any{
			"state":  "unavailable",
			"reason": "224-310 RRC25 全球状态发布不包含 Core 国家资源聚合轨道",
		},
		"update_activity":  map[string]any{"state": "available"},
		"address_families": map[string]any{"state": "available"},
		"asn_matrix":       map[string]any{"state": "available"},
		"audit":            map[string]any{"state": "available"},
		"normal_band": map[string]any{
			"state":  "unavailable",
			"reason": "固定研究窗口没有可信长期正常参照",
		},
	}
}

func dailySelectedSummaries(
	root string,
	selection GlobalWindowSelection,
	dayStart int,
	dayEnd int,
	countryCodes map[string]struct{},
) (map[string]GlobalCountrySummary, string, error) {
	dayTime, _ := time.Parse(time.RFC3339, selection.Updates[dayStart].ArtifactTimeUTC)
	dayKey := dayTime.Format("20060102")
	observationPath := filepath.Join(
		root, "observations", "country-observation-"+dayKey+".jsonl.gz",
	)
	qualityPath := filepath.Join(root, "quality", "slot-quality-"+dayKey+".jsonl.gz")
	observationSHA, err := sha256RegularFile(observationPath)
	if err != nil {
		return nil, dayKey, err
	}
	qualitySHA, err := sha256RegularFile(qualityPath)
	if err != nil {
		return nil, dayKey, err
	}
	dataThroughTime, _ := time.Parse(
		time.RFC3339, selection.Updates[dayEnd-1].ArtifactTimeUTC,
	)
	dataThrough := dataThroughTime.Add(globalWindowSlot).Format(time.RFC3339)
	checkpointKey := strings.NewReplacer("-", "", ":", "").Replace(dataThrough)
	var checkpoint GlobalContinuationCheckpointManifest
	if _, err := readJSON(filepath.Join(
		root, "checkpoints", "daily", checkpointKey, "manifest.json",
	), &checkpoint); err != nil {
		return nil, dayKey, err
	}
	if checkpoint.DataThrough != dataThrough ||
		checkpoint.ProcessedUpdateCount != dayEnd ||
		checkpoint.PreviousProductSHA256 != dailyProductSHA(observationSHA, qualitySHA) {
		return nil, dayKey, fmt.Errorf("daily product identity mismatch: %s", dayKey)
	}
	file, err := os.Open(observationPath)
	if err != nil {
		return nil, dayKey, err
	}
	defer file.Close()
	compressed, err := gzip.NewReader(file)
	if err != nil {
		return nil, dayKey, err
	}
	defer compressed.Close()
	decoder := json.NewDecoder(compressed)
	selected := make(map[string]GlobalCountrySummary, (dayEnd-dayStart)*len(countryCodes))
	total := 0
	for {
		var row GlobalCountrySummary
		if err := decoder.Decode(&row); err != nil {
			if err == io.EOF {
				break
			}
			return nil, dayKey, err
		}
		total++
		if _, wanted := countryCodes[row.CountryCode]; !wanted {
			continue
		}
		if row.SchemaVersion != GlobalWindowSummaryVersion || row.CollectorID != "rrc25" {
			return nil, dayKey, fmt.Errorf("daily country summary identity mismatch")
		}
		key := row.ObservedAt + "|" + row.CountryCode
		if _, duplicate := selected[key]; duplicate {
			return nil, dayKey, fmt.Errorf("duplicate daily country summary: %s", key)
		}
		selected[key] = row
	}
	expectedTotal := (dayEnd - dayStart) * 241
	if total != expectedTotal || len(selected) != (dayEnd-dayStart)*len(countryCodes) {
		return nil, dayKey, fmt.Errorf(
			"daily summary population mismatch: day=%s total=%d/%d selected=%d/%d",
			dayKey, total, expectedTotal, len(selected),
			(dayEnd-dayStart)*len(countryCodes),
		)
	}
	return selected, dayKey, nil
}

func compareProjectedObservation(
	exact GlobalCountryObservation,
	summary GlobalCountrySummary,
) error {
	if exact.CountryCode != summary.CountryCode ||
		exact.CohortID != summary.CohortID ||
		exact.ObservedAt != summary.ObservedAt ||
		exact.BaselinePrefixVPCount != int(summary.BaselinePrefixVP) ||
		exact.VisiblePrefixVPCount != int(summary.VisiblePrefixVP) ||
		exact.BaselineASNCount != summary.BaselineASNCount ||
		exact.VisibleOriginASNCount != summary.VisibleOriginASNCount ||
		exact.AffectedASNCount != summary.AffectedASNCount ||
		len(exact.DualClassifications["fully_invisible"]) !=
			summary.FullyInvisibleASNCount ||
		math.Abs(exact.VisiblePrefixVPRatio-summary.VisiblePrefixVPRatio) > 1e-12 {
		return fmt.Errorf(
			"projected country observation mismatch: %s %s "+
				"cohort=%s/%s baseline_prefix=%d/%d visible_prefix=%d/%d "+
				"baseline_asn=%d/%d visible_asn=%d/%d affected_asn=%d/%d "+
				"fully_invisible=%d/%d ratio=%.12f/%.12f",
			exact.CountryCode, exact.ObservedAt, exact.CohortID, summary.CohortID,
			exact.BaselinePrefixVPCount, summary.BaselinePrefixVP,
			exact.VisiblePrefixVPCount, summary.VisiblePrefixVP,
			exact.BaselineASNCount, summary.BaselineASNCount,
			exact.VisibleOriginASNCount, summary.VisibleOriginASNCount,
			exact.AffectedASNCount, summary.AffectedASNCount,
			len(exact.DualClassifications["fully_invisible"]),
			summary.FullyInvisibleASNCount,
			exact.VisiblePrefixVPRatio, summary.VisiblePrefixVPRatio,
		)
	}
	for _, pair := range []struct {
		exact   FamilyMetrics
		summary GlobalWindowFamilySummary
	}{
		{exact.IPv4, summary.IPv4},
		{exact.IPv6, summary.IPv6},
	} {
		affected := len(pair.exact.PartiallyVisibleASNs) + len(pair.exact.FullyInvisibleASNs)
		if pair.exact.BaselinePrefixVPCount != int(pair.summary.BaselinePrefixVP) ||
			pair.exact.VisiblePrefixVPCount != int(pair.summary.VisiblePrefixVP) ||
			pair.exact.BaselineASNCount != pair.summary.BaselineASNCount ||
			pair.exact.VisibleASNCount != pair.summary.VisibleASNCount ||
			affected != pair.summary.AffectedASNCount {
			return fmt.Errorf("projected address-family observation mismatch: %s %s", exact.CountryCode, exact.ObservedAt)
		}
	}
	return nil
}

func linkProjectionFile(source string, target string) error {
	if err := os.Link(source, target); err != nil {
		return fmt.Errorf("link projection artifact %s: %w", filepath.Base(source), err)
	}
	return nil
}

func publicationID(
	incidentID string,
	datasetID string,
	dataThrough string,
	packageURI string,
) string {
	identity := strings.Join([]string{incidentID, datasetID, dataThrough, packageURI}, "|")
	return "publication_global_window_v1_" + sha256Text(identity)[:24]
}

func publicationIncidentID(reference string, countryCode string) string {
	return stableID("incident_global_window_v1_", map[string]any{
		"legacy_reference": reference,
		"country_code":     countryCode,
		"collector_id":     "rrc25",
	}, 32)
}

func buildEventRegistration(
	event *parsedGlobalWindowEvent,
	existing map[string]any,
	artifact projectionCountryArtifact,
	stagingRoot string,
	finalRoot string,
	complete GlobalWindowRunResult,
	completedAt string,
	inputBytes int64,
	ribQuality GlobalRIBQuality,
	updateStats UpdateParseStats,
) (map[string]any, GlobalWindowPublicationEvent, error) {
	if existing != nil {
		if value, ok := existing["incident_id"].(string); ok && value != "" {
			event.IncidentID = value
		}
	}
	if event.IncidentID == "" {
		event.IncidentID = publicationIncidentID(event.Reference, event.CountryCode)
	}
	eventKey := strings.ToLower(event.CountryCode) + "-" + sha256Text(event.Reference)[:20]
	stagingDirectory := filepath.Join(stagingRoot, "events", eventKey)
	finalDirectory := filepath.Join(finalRoot, "events", eventKey)
	if err := os.MkdirAll(stagingDirectory, 0o750); err != nil {
		return nil, GlobalWindowPublicationEvent{}, err
	}
	for _, name := range []string{
		"cohort.json", "country-snapshots.jsonl.gz", "asn-states.jsonl.gz",
	} {
		if err := linkProjectionFile(
			filepath.Join(artifact.Directory, name), filepath.Join(stagingDirectory, name),
		); err != nil {
			return nil, GlobalWindowPublicationEvent{}, err
		}
	}
	if err := linkProjectionFile(
		filepath.Join(stagingRoot, "shared", "input-summary.json"),
		filepath.Join(stagingDirectory, "input-summary.json"),
	); err != nil {
		return nil, GlobalWindowPublicationEvent{}, err
	}
	incident := map[string]any{
		"schema_version":      "rrc25-global-country-event-window/v1",
		"incident_id":         event.IncidentID,
		"country_code":        event.CountryCode,
		"collector_id":        "rrc25",
		"cohort_id":           artifact.Cohort.CohortID,
		"legacy_reference":    event.Reference,
		"legacy_event_time":   event.EventTime.Format(time.RFC3339),
		"detected_at":         event.DetectedAt.Format(time.RFC3339),
		"onset_at":            nil,
		"peak_at":             nil,
		"trough_at":           nil,
		"partial_recovery_at": nil,
		"full_recovery_at":    nil,
		"observation_end_at":  complete.WindowEndExclusive,
		"duration_state":      "legacy_fact_not_recomputed",
		"recovery_state":      "not_assessed",
		"normal_band": map[string]any{
			"state":  "unknown",
			"reason": "固定224-310研究窗口没有可信长期正常参照",
		},
		"episodes":          []any{},
		"algorithm_version": GlobalEngineVersion,
	}
	for name, value := range map[string]any{
		"incident.json": incident,
		"episodes.json": map[string]any{
			"schema_version": "rrc25-global-country-event-episodes/v1",
			"incident_id":    event.IncidentID,
			"episodes":       []any{},
		},
		"waves.json": map[string]any{
			"schema_version": "rrc25-global-country-event-waves/v1",
			"incident_id":    event.IncidentID,
			"waves":          []any{},
		},
	} {
		if err := writeJSONAtomic(filepath.Join(stagingDirectory, name), value); err != nil {
			return nil, GlobalWindowPublicationEvent{}, err
		}
	}
	quality := map[string]any{
		"schema_version":          "rrc25-global-window-event-package-quality/v1",
		"status":                  "pass",
		"engine_version":          GlobalEngineVersion,
		"run_id":                  complete.RunID,
		"dataset_id":              complete.DatasetID,
		"revision":                complete.Revision,
		"country_code":            event.CountryCode,
		"cohort_id":               artifact.Cohort.CohortID,
		"rib_physical_records":    ribQuality.RIBPhysicalRecords,
		"rib_entries":             ribQuality.RIBEntries,
		"rib_retained_members":    artifact.Cohort.BaselinePrefixVPCount,
		"rib_unknown_origins":     ribQuality.RIBUnknownOrigins,
		"rib_mapping_unknown":     ribQuality.RIBMappingUnknown,
		"update_physical_records": updateStats.PhysicalRecords,
		"update_route_events":     updateStats.RouteEvents,
		"update_unknown_origins":  updateStats.UnknownOrigins,
		"input_compressed_bytes":  inputBytes,
		"observation_count":       artifact.ObservationCount,
		"asn_state_count":         artifact.ASNStateCount,
		"last_observation_at":     artifact.LastObservedAt,
		"global_state_digest":     complete.StateDigest,
		"packaged_at":             completedAt,
		"checkpoint_resume_count": 0,
		"failures":                nil,
	}
	if err := writeJSONAtomic(filepath.Join(stagingDirectory, "QUALITY.json"), quality); err != nil {
		return nil, GlobalWindowPublicationEvent{}, err
	}
	deliverables := []string{
		"QUALITY.json", "asn-states.jsonl.gz", "cohort.json",
		"country-snapshots.jsonl.gz", "episodes.json", "incident.json",
		"input-summary.json", "waves.json",
	}
	hashes := make(map[string]string, len(deliverables))
	for _, name := range deliverables {
		hash, err := sha256RegularFile(filepath.Join(stagingDirectory, name))
		if err != nil {
			return nil, GlobalWindowPublicationEvent{}, err
		}
		hashes[name] = hash
	}
	packageComplete := map[string]any{
		"schema_version":      GlobalWindowPublicationVersion,
		"engine_version":      GlobalEngineVersion,
		"status":              "complete",
		"run_id":              complete.RunID,
		"dataset_id":          complete.DatasetID,
		"revision":            complete.Revision,
		"country_code":        event.CountryCode,
		"cohort_id":           artifact.Cohort.CohortID,
		"incident_id":         event.IncidentID,
		"completed_at":        completedAt,
		"observation_count":   artifact.ObservationCount,
		"asn_state_count":     artifact.ASNStateCount,
		"last_observation_at": artifact.LastObservedAt,
		"global_state_digest": complete.StateDigest,
		"deliverable_sha256":  hashes,
	}
	if err := writeJSONAtomic(filepath.Join(stagingDirectory, "COMPLETE.json"), packageComplete); err != nil {
		return nil, GlobalWindowPublicationEvent{}, err
	}

	publications := []any{}
	revision := 1
	var supersedes any
	if existing != nil {
		if raw, ok := existing["publications"].([]any); ok && len(raw) > 0 {
			publications = append(publications, raw...)
			for _, rawPublication := range raw {
				if publication, ok := rawPublication.(map[string]any); ok {
					if value, ok := publication["revision"].(float64); ok && int(value) >= revision {
						revision = int(value) + 1
					}
				}
			}
			supersedes = existing["current_publication_id"]
		} else {
			old := copyPublicationFields(existing)
			publications = append(publications, old)
			supersedes = old["publication_id"]
			if value, ok := existing["revision"].(float64); ok {
				revision = int(value) + 1
			} else if value, ok := existing["revision"].(int); ok {
				revision = value + 1
			} else {
				revision = 2
			}
		}
	}
	currentPublicationID := publicationID(
		event.IncidentID, complete.DatasetID, artifact.LastObservedAt, finalDirectory,
	)
	currentPublication := map[string]any{
		"publication_id":    currentPublicationID,
		"package_uri":       finalDirectory,
		"revision":          revision,
		"publication_state": "published",
		"observation_state": "state_complete",
		"data_mode":         "replay",
		"data_through":      artifact.LastObservedAt,
		"updated_at":        completedAt,
		"is_final":          true,
		"publication_kind":  "global_window_projection",
		"correction_reason": "采用RRC25 224-310全球固定cohort状态重放",
		"processing_status": map[string]any{
			"state":                      "final",
			"updated_at":                 completedAt,
			"attempted_through":          artifact.LastObservedAt,
			"reason":                     nil,
			"last_complete_data_through": artifact.LastObservedAt,
		},
		"capabilities": publicationCapabilities(),
		"resource_source": map[string]any{
			"state":  "unavailable",
			"reason": "本次全球RouteState发布不包含Core国家资源聚合轨道",
		},
		"artifact_set_id":       complete.DatasetID + ":" + event.CountryCode,
		"run_id":                complete.RunID,
		"route_state_row_count": complete.RouteStateRows,
		"vantage_point_count":   nil,
	}
	if supersedes != nil && fmt.Sprint(supersedes) != "<nil>" && fmt.Sprint(supersedes) != "" {
		currentPublication["supersedes_publication_id"] = supersedes
	}
	publications = append(publications, currentPublication)
	registration := map[string]any{
		"incident_id":      event.IncidentID,
		"legacy_reference": event.Reference,
		"country": map[string]any{
			"code": event.CountryCode,
			"name": event.CountryName,
		},
		"display_name":            event.CountryName + " BGP 路由观测",
		"collector_ids":           []string{"rrc25"},
		"vantage_point_count":     nil,
		"vantage_point_semantics": "RRC25 RouteState中的唯一VP身份",
		"display_timezone":        "Asia/Shanghai",
		"interval_seconds":        300,
		"revision":                revision,
		"publication_state":       "published",
		"observation_state":       "state_complete",
		"data_mode":               "replay",
		"data_through":            artifact.LastObservedAt,
		"updated_at":              completedAt,
		"is_final":                true,
		"package_uri":             finalDirectory,
		"capabilities":            publicationCapabilities(),
		"resource_source": map[string]any{
			"state":  "unavailable",
			"reason": "本次全球RouteState发布不包含Core国家资源聚合轨道",
		},
		"processing_status":      currentPublication["processing_status"],
		"publications":           publications,
		"current_publication_id": currentPublicationID,
	}
	return registration, GlobalWindowPublicationEvent{
		LegacyReference: event.Reference,
		CountryCode:     event.CountryCode,
		CountryName:     event.CountryName,
		EventTimeUTC:    event.EventTime.Format(time.RFC3339),
		DetectedAt:      event.DetectedAt.Format(time.RFC3339),
		IncidentID:      event.IncidentID,
		PublicationID:   currentPublicationID,
		PackageURI:      finalDirectory,
		Revision:        revision,
	}, nil
}

func BuildGlobalWindowPublication(
	config GlobalWindowPublicationConfig,
) (GlobalWindowPublicationResult, error) {
	var result GlobalWindowPublicationResult
	for label, value := range map[string]string{
		"global window root": config.GlobalWindowRoot,
		"events path":        config.EventsPath,
		"compatible mapping": config.CompatibleMapping,
		"revised mapping":    config.RevisedMapping,
		"output":             config.Output,
	} {
		if value == "" {
			return result, fmt.Errorf("%s is required", label)
		}
	}
	if !filepath.IsAbs(config.Output) {
		return result, fmt.Errorf("publication output must be absolute")
	}
	if _, err := os.Lstat(config.Output); err == nil {
		return result, fmt.Errorf("publication output already exists")
	} else if !os.IsNotExist(err) {
		return result, err
	}
	staging := config.Output + ".staging"
	if _, err := os.Lstat(staging); err == nil {
		return result, fmt.Errorf("publication staging already exists")
	} else if !os.IsNotExist(err) {
		return result, err
	}
	if err := os.MkdirAll(filepath.Join(staging, "shared", "countries"), 0o750); err != nil {
		return result, err
	}
	if err := os.MkdirAll(filepath.Join(staging, "events"), 0o750); err != nil {
		return result, err
	}
	failed := true
	defer func() {
		if failed {
			_ = os.RemoveAll(staging)
		}
	}()

	var complete GlobalWindowRunResult
	if _, err := readJSON(filepath.Join(config.GlobalWindowRoot, "COMPLETE.json"), &complete); err != nil {
		return result, err
	}
	var input globalWindowInputSummary
	if _, err := readJSON(filepath.Join(config.GlobalWindowRoot, "input-summary.json"), &input); err != nil {
		return result, err
	}
	selection := input.Selection
	windowStart, err := time.Parse(time.RFC3339, selection.WindowStartUTC)
	if err != nil {
		return result, err
	}
	windowEnd, err := time.Parse(time.RFC3339, selection.WindowEndExclusiveUTC)
	if err != nil {
		return result, err
	}
	if complete.Status != "complete" || complete.WindowStartUTC != selection.WindowStartUTC ||
		complete.WindowEndExclusive != selection.WindowEndExclusiveUTC ||
		complete.ProcessedUpdateCount != len(selection.Updates) ||
		len(selection.Updates) != 4320 || complete.ObservationCount != 1041120 ||
		complete.CountryBucketCount != 241 {
		return result, fmt.Errorf("global window complete identity mismatch")
	}
	events, err := parseGlobalWindowEvents(config.EventsPath, windowStart, windowEnd)
	if err != nil {
		return result, err
	}
	countryCodes := make(map[string]struct{})
	for _, event := range events {
		countryCodes[event.CountryCode] = struct{}{}
	}
	mapping, err := LoadGlobalCountryMapping(config.CompatibleMapping, config.RevisedMapping)
	if err != nil {
		return result, err
	}
	config.progress(fmt.Sprintf("装入 %d 个事件国家的固定RIB cohort", len(countryCodes)))
	state, ribManifest, err := LoadSelectedGlobalRIBCheckpoint(
		filepath.Join(config.GlobalWindowRoot, "checkpoints"), mapping,
		selection.RIB, countryCodes,
	)
	if err != nil {
		return result, err
	}
	cohorts, err := BuildSelectedGlobalCountryCohorts(state, ribManifest, countryCodes)
	if err != nil {
		return result, err
	}
	writers := make(map[string]*projectionCountryWriter, len(cohorts))
	for _, cohort := range cohorts {
		directory := filepath.Join(staging, "shared", "countries", cohort.CountryCode)
		if err := os.MkdirAll(directory, 0o750); err != nil {
			return result, err
		}
		if err := writeJSONAtomic(filepath.Join(directory, "cohort.json"), cohort); err != nil {
			return result, err
		}
		snapshots, err := newJSONLGzipWriter(filepath.Join(directory, "country-snapshots.jsonl.gz"))
		if err != nil {
			return result, err
		}
		asnStates, err := newJSONLGzipWriter(filepath.Join(directory, "asn-states.jsonl.gz"))
		if err != nil {
			_ = snapshots.Close()
			return result, err
		}
		writers[cohort.CountryCode] = &projectionCountryWriter{
			Code:      cohort.CountryCode,
			Directory: directory,
			Cohort:    cohort,
			Snapshots: snapshots,
			ASNStates: asnStates,
		}
	}
	closeWriters := func() error {
		var firstErr error
		codes := make([]string, 0, len(writers))
		for code := range writers {
			codes = append(codes, code)
		}
		sort.Strings(codes)
		for _, code := range codes {
			writer := writers[code]
			for _, stream := range []*jsonlGzipWriter{writer.Snapshots, writer.ASNStates} {
				if stream != nil {
					if err := stream.Close(); err != nil && firstErr == nil {
						firstErr = err
					}
				}
			}
			writer.Snapshots = nil
			writer.ASNStates = nil
		}
		return firstErr
	}

	sharedInput := map[string]any{
		"schema_version":           GlobalWindowSelectionVersion,
		"collector_id":             "rrc25",
		"window_start_utc":         selection.WindowStartUTC,
		"window_end_exclusive_utc": selection.WindowEndExclusiveUTC,
		"rib":                      selection.RIB,
		"catch_up_updates":         []Artifact{},
		"formal_updates":           selection.Updates,
	}
	if err := writeJSONAtomic(filepath.Join(staging, "shared", "input-summary.json"), sharedInput); err != nil {
		_ = closeWriters()
		return result, err
	}
	spoolManifest, spoolManifestSHA, err := LoadGlobalSpoolManifest(
		config.GlobalWindowRoot,
		FixedInputs{RIB: selection.RIB, Formal: selection.Updates, AllUpdate: selection.Updates},
		"",
	)
	if err != nil {
		_ = closeWriters()
		return result, err
	}
	if len(spoolManifest.Slots) != len(selection.Updates) {
		_ = closeWriters()
		return result, fmt.Errorf("global window spool population mismatch")
	}
	for dayStart := 0; dayStart < len(selection.Updates); {
		dayEnd := dayStart + 288
		if dayEnd > len(selection.Updates) {
			dayEnd = len(selection.Updates)
		}
		daily, dayKey, err := dailySelectedSummaries(
			config.GlobalWindowRoot, selection, dayStart, dayEnd, countryCodes,
		)
		if err != nil {
			_ = closeWriters()
			return result, err
		}
		for index := dayStart; index < dayEnd; index++ {
			if _, err := ApplyGlobalSpoolSlotSelected(
				state, config.GlobalWindowRoot, spoolManifest.Slots[index],
			); err != nil {
				_ = closeWriters()
				return result, fmt.Errorf("project slot %d: %w", index, err)
			}
			artifact := selection.Updates[index]
			slotStart, _ := time.Parse(time.RFC3339, artifact.ArtifactTimeUTC)
			observedAt := slotStart.Add(globalWindowSlot).Format(time.RFC3339)
			observations, asnRows, _, err := state.SnapshotAllFast(
				observedAt, artifact.ArtifactTimeUTC, observedAt, "global_window",
				NewGlobalSlotActivity(),
			)
			if err != nil {
				_ = closeWriters()
				return result, err
			}
			snapshotIDs := make(map[string]string, len(observations))
			for _, observation := range observations {
				summary, exists := daily[observedAt+"|"+observation.CountryCode]
				if !exists {
					_ = closeWriters()
					return result, fmt.Errorf("selected daily summary is missing")
				}
				if err := compareProjectedObservation(observation, summary); err != nil {
					_ = closeWriters()
					return result, err
				}
				observation.UpdateCounts = summary.CollectorUpdateCounts
				observation.CountryUpdateCounts = summary.CountryUpdateCounts
				observation.CurrentPrefixVPCount = int(summary.CurrentPrefixVP)
				observation.StateDigest = summary.GlobalStateDigest
				packaged := packageObservation(observation)
				writer := writers[observation.CountryCode]
				if writer == nil {
					_ = closeWriters()
					return result, fmt.Errorf("country projection writer is missing")
				}
				if err := writer.Snapshots.Write(packaged); err != nil {
					_ = closeWriters()
					return result, err
				}
				writer.ObservationCount++
				writer.LastObservedAt = observedAt
				snapshotIDs[observation.CountryCode] = packaged.SnapshotID
			}
			for _, row := range asnRows {
				writer := writers[row.CountryCode]
				snapshotID := snapshotIDs[row.CountryCode]
				if writer == nil || snapshotID == "" {
					_ = closeWriters()
					return result, fmt.Errorf("ASN projection has no matching country snapshot")
				}
				row.SnapshotID = snapshotID
				if err := writer.ASNStates.Write(row); err != nil {
					_ = closeWriters()
					return result, err
				}
				writer.ASNStateCount++
			}
			if (index+1)%12 == 0 || index+1 == dayEnd {
				config.progress(fmt.Sprintf("国家发布状态已推进 %d/%d", index+1, len(selection.Updates)))
			}
		}
		config.progress("国家发布日分区对账通过：" + dayKey)
		dayStart = dayEnd
	}
	if err := closeWriters(); err != nil {
		return result, err
	}

	artifacts := make(map[string]projectionCountryArtifact, len(writers))
	for code, writer := range writers {
		if writer.ObservationCount != len(selection.Updates) ||
			writer.LastObservedAt != selection.WindowEndExclusiveUTC {
			return result, fmt.Errorf("country %s projection population mismatch", code)
		}
		hashes := make(map[string]string)
		for _, name := range []string{
			"cohort.json", "country-snapshots.jsonl.gz", "asn-states.jsonl.gz",
		} {
			hash, err := sha256RegularFile(filepath.Join(writer.Directory, name))
			if err != nil {
				return result, err
			}
			hashes[name] = hash
		}
		artifacts[code] = projectionCountryArtifact{
			Code:             code,
			Directory:        writer.Directory,
			Cohort:           writer.Cohort,
			ObservationCount: writer.ObservationCount,
			ASNStateCount:    writer.ASNStateCount,
			LastObservedAt:   writer.LastObservedAt,
			Hashes:           hashes,
		}
	}
	var ribQuality GlobalRIBQuality
	if _, err := readJSON(filepath.Join(config.GlobalWindowRoot, "rib-quality.json"), &ribQuality); err != nil {
		return result, err
	}
	updateStats := UpdateParseStats{UnknownOptional: make(map[uint8]int64)}
	inputBytes := selection.RIB.SizeBytes
	for _, slot := range spoolManifest.Slots {
		inputBytes += slot.Artifact.SizeBytes
		updateStats.PhysicalRecords += slot.Stats.PhysicalRecords
		updateStats.RouteEvents += slot.Stats.RouteEvents
		updateStats.Announces += slot.Stats.Announces
		updateStats.Withdraws += slot.Stats.Withdraws
		updateStats.UnknownOrigins += slot.Stats.UnknownOrigins
		for key, value := range slot.Stats.UnknownOptional {
			updateStats.UnknownOptional[key] += value
		}
	}
	progressUpdatedAt := time.Now().UTC().Format(time.RFC3339)
	var sourceProgress map[string]any
	if _, err := readJSON(filepath.Join(config.GlobalWindowRoot, "progress.json"), &sourceProgress); err == nil {
		if value, ok := sourceProgress["updated_at"].(string); ok && value != "" {
			progressUpdatedAt = value
		}
	}
	existingObservations, err := readRegistryObservations(config.ExistingRegistry)
	if err != nil {
		return result, err
	}
	existingByReference := existingRegistryByReference(existingObservations)
	registrations := make([]any, 0, len(existingObservations)+len(events))
	covered := make(map[string]struct{}, len(events))
	publicationEvents := make([]GlobalWindowPublicationEvent, 0, len(events))
	for index := range events {
		event := &events[index]
		registration, publicationEvent, err := buildEventRegistration(
			event, existingByReference[event.Reference], artifacts[event.CountryCode],
			staging, config.Output, complete, progressUpdatedAt, inputBytes,
			ribQuality, updateStats,
		)
		if err != nil {
			return result, err
		}
		registrations = append(registrations, registration)
		publicationEvents = append(publicationEvents, publicationEvent)
		covered[event.Reference] = struct{}{}
	}
	for _, existing := range existingObservations {
		reference, _ := existing["legacy_reference"].(string)
		if _, replaced := covered[reference]; !replaced {
			registrations = append(registrations, existing)
		}
	}
	sort.Slice(registrations, func(i, j int) bool {
		left, _ := registrations[i].(map[string]any)
		right, _ := registrations[j].(map[string]any)
		return fmt.Sprint(left["legacy_reference"]) < fmt.Sprint(right["legacy_reference"])
	})
	registry := map[string]any{
		"schema_version": "country_outage_observation_registry_v1",
		"scope":          "rrc25_global_window_20260224_20260310",
		"observations":   registrations,
	}
	registryPath := filepath.Join(staging, "country-outage-registry.json")
	if err := writeJSONAtomic(registryPath, registry); err != nil {
		return result, err
	}
	registrySHA, err := sha256RegularFile(registryPath)
	if err != nil {
		return result, err
	}
	result = GlobalWindowPublicationResult{
		SchemaVersion:       GlobalWindowPublicationVersion,
		Status:              "complete",
		RunID:               complete.RunID,
		DatasetID:           complete.DatasetID,
		CollectorID:         "rrc25",
		WindowStartUTC:      selection.WindowStartUTC,
		WindowEndUTC:        selection.WindowEndExclusiveUTC,
		ObservationCount:    len(selection.Updates),
		CountryCount:        len(countryCodes),
		EventCount:          len(events),
		RegistryPath:        filepath.Join(config.Output, "country-outage-registry.json"),
		RegistrySHA256:      registrySHA,
		SpoolManifestSHA256: spoolManifestSHA,
		Events:              publicationEvents,
	}
	if err := writeJSONAtomic(filepath.Join(staging, "catalog.json"), result); err != nil {
		return result, err
	}
	catalogSHA, err := sha256RegularFile(filepath.Join(staging, "catalog.json"))
	if err != nil {
		return result, err
	}
	if err := writeJSONAtomic(filepath.Join(staging, "COMPLETE.json"), map[string]any{
		"schema_version":                GlobalWindowPublicationVersion,
		"status":                        "complete",
		"run_id":                        complete.RunID,
		"dataset_id":                    complete.DatasetID,
		"collector_id":                  "rrc25",
		"window_start_utc":              selection.WindowStartUTC,
		"window_end_exclusive_utc":      selection.WindowEndExclusiveUTC,
		"event_count":                   len(events),
		"country_count":                 len(countryCodes),
		"observation_count_per_country": len(selection.Updates),
		"registry_sha256":               registrySHA,
		"catalog_sha256":                catalogSHA,
		"spool_manifest_sha256":         spoolManifestSHA,
		"completed_at":                  progressUpdatedAt,
	}); err != nil {
		return result, err
	}
	if err := os.Rename(staging, config.Output); err != nil {
		return result, err
	}
	failed = false
	return result, nil
}
