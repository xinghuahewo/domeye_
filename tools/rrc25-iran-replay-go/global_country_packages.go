package replay

import (
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/netip"
	"os"
	"path/filepath"
	"sort"
)

const GlobalCountryPackageVersion = "rrc25-global-country-package/v1"

type GlobalCountryCohortMember struct {
	ASN           uint32   `json:"asn"`
	AFI           uint8    `json:"afi"`
	PrefixVPCount int      `json:"prefix_vp_count"`
	Prefixes      []string `json:"prefixes"`
}

type GlobalCountryCohortDocument struct {
	SchemaVersion             string                      `json:"schema_version"`
	CohortID                  string                      `json:"cohort_id"`
	CollectorID               string                      `json:"collector_id"`
	CountryCode               string                      `json:"country_code"`
	MappingVersion            string                      `json:"mapping_version"`
	SeedObservedAt            string                      `json:"seed_observed_at"`
	BaselineOriginASNs        []uint32                    `json:"baseline_origin_asns"`
	BaselineOriginASNCount    int                         `json:"baseline_origin_asn_count"`
	BaselinePrefixVPCount     int64                       `json:"baseline_prefix_vp_count"`
	BaselineIPv4PrefixVPCount int64                       `json:"baseline_ipv4_prefix_vp_count"`
	BaselineIPv6PrefixVPCount int64                       `json:"baseline_ipv6_prefix_vp_count"`
	UnknownOriginPrefixVP     int64                       `json:"unknown_origin_prefix_vp_count"`
	MembershipDigest          string                      `json:"membership_digest"`
	Members                   []GlobalCountryCohortMember `json:"members"`
}

type countryMemberKey struct {
	CountryID uint16
	ASN       uint32
	AFI       uint8
}

type countryMemberAccumulator struct {
	PrefixVPCount int
	Prefixes      map[netip.Prefix]struct{}
}

type GlobalPackagedObservation struct {
	SchemaVersion         string              `json:"schema_version"`
	SnapshotID            string              `json:"snapshot_id"`
	ObservedAt            string              `json:"observed_at"`
	SlotStartUTC          string              `json:"slot_start_utc"`
	SlotEndUTC            string              `json:"slot_end_exclusive_utc"`
	SlotRole              string              `json:"slot_role"`
	CountryCode           string              `json:"country_code"`
	CohortID              string              `json:"cohort_id"`
	BaselineASNCount      int                 `json:"baseline_asn_count"`
	BaselinePrefixVPCount int                 `json:"baseline_prefix_vp_count"`
	VisiblePrefixVPCount  int                 `json:"visible_prefix_vp_count"`
	VisiblePrefixVPRatio  float64             `json:"visible_prefix_vp_ratio"`
	AffectedASNCount      int                 `json:"affected_asn_count"`
	AffectedASNRatio      *float64            `json:"affected_asn_ratio"`
	VisibleOriginASNCount int                 `json:"visible_origin_asn_count"`
	VisibleOriginASNRatio *float64            `json:"visible_origin_asn_ratio"`
	IPv4                  FamilyMetrics       `json:"ipv4"`
	IPv6                  FamilyMetrics       `json:"ipv6"`
	DualClassifications   map[string][]uint32 `json:"dual_stack_classifications"`
	UpdateCounts          UpdateCounts        `json:"update_counts"`
	CountryUpdateCounts   UpdateCounts        `json:"country_update_counts"`
	CurrentPrefixVPCount  int                 `json:"current_prefix_vp_count"`
	GlobalStateDigest     string              `json:"global_state_digest"`
}

type GlobalCountryPackageCatalogEntry struct {
	CountryCode           string `json:"country_code"`
	CohortID              string `json:"cohort_id"`
	BaselineOriginASNs    int    `json:"baseline_origin_asn_count"`
	BaselinePrefixVP      int64  `json:"baseline_prefix_vp_count"`
	BaselineIPv4PrefixVP  int64  `json:"baseline_ipv4_prefix_vp_count"`
	BaselineIPv6PrefixVP  int64  `json:"baseline_ipv6_prefix_vp_count"`
	ObservationCount      int    `json:"observation_count"`
	PackagePath           string `json:"package_path"`
	CompleteSHA256        string `json:"complete_sha256"`
	SnapshotsSHA256       string `json:"country_snapshots_sha256"`
	ASNStatesSHA256       string `json:"asn_states_sha256"`
	CohortDocumentSHA256  string `json:"cohort_sha256"`
	UnknownOriginPrefixVP int64  `json:"unknown_origin_prefix_vp_count"`
}

type GlobalCountryPackagesResult struct {
	SchemaVersion       string                             `json:"schema_version"`
	EngineVersion       string                             `json:"engine_version"`
	RunID               string                             `json:"run_id"`
	DatasetID           string                             `json:"dataset_id"`
	Revision            string                             `json:"revision"`
	CollectorID         string                             `json:"collector_id"`
	MappingVersion      string                             `json:"mapping_version"`
	DataThrough         string                             `json:"data_through"`
	ObservationCount    int                                `json:"observation_count"`
	CountryCount        int                                `json:"country_count"`
	PackageRoot         string                             `json:"package_root"`
	FormalManifestSHA   string                             `json:"formal_manifest_sha256"`
	BaseCatalogSHA      string                             `json:"base_catalog_sha256,omitempty"`
	AppendProductSHA    string                             `json:"append_product_sha256,omitempty"`
	PreviousDataThrough string                             `json:"previous_data_through,omitempty"`
	Countries           []GlobalCountryPackageCatalogEntry `json:"countries"`
}

type countryPackageWriter struct {
	Code           string
	Directory      string
	Cohort         GlobalCountryCohortDocument
	Snapshots      *jsonlGzipWriter
	ASNStates      *jsonlGzipWriter
	ObservationIDs map[string]string
}

func buildGlobalCountryCohorts(
	state *GlobalReplayState,
	manifest GlobalRIBCheckpointManifest,
) ([]GlobalCountryCohortDocument, error) {
	if state == nil || state.Mapping == nil {
		return nil, fmt.Errorf("global RouteState is required")
	}
	accumulators := make(map[countryMemberKey]*countryMemberAccumulator)
	unknownOrigins := make(map[uint16]int64)
	for key, route := range state.Routes {
		if route.Dynamic {
			continue
		}
		if !route.BaselineOriginKnown {
			unknownOrigins[route.BaselineCountryID]++
			continue
		}
		memberKey := countryMemberKey{
			CountryID: route.BaselineCountryID,
			ASN:       route.BaselineOriginASN,
			AFI:       key.AFI,
		}
		current := accumulators[memberKey]
		if current == nil {
			current = &countryMemberAccumulator{
				Prefixes: make(map[netip.Prefix]struct{}),
			}
			accumulators[memberKey] = current
		}
		current.PrefixVPCount++
		current.Prefixes[key.Prefix] = struct{}{}
	}

	byCode := make(map[string]GlobalCheckpointCountry, len(manifest.Countries))
	for _, country := range manifest.Countries {
		byCode[country.CountryCode] = country
	}
	keys := make([]countryMemberKey, 0, len(accumulators))
	for key := range accumulators {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool {
		left := state.Mapping.CountryCode(keys[i].CountryID)
		right := state.Mapping.CountryCode(keys[j].CountryID)
		if left != right {
			return left < right
		}
		if keys[i].ASN != keys[j].ASN {
			return keys[i].ASN < keys[j].ASN
		}
		return keys[i].AFI < keys[j].AFI
	})

	documents := make(map[string]*GlobalCountryCohortDocument, len(manifest.Countries))
	asnSets := make(map[string]map[uint32]struct{}, len(manifest.Countries))
	for _, source := range manifest.Countries {
		document := &GlobalCountryCohortDocument{
			SchemaVersion:             GlobalCohortVersion,
			CohortID:                  source.CohortID,
			CollectorID:               manifest.CollectorID,
			CountryCode:               source.CountryCode,
			MappingVersion:            manifest.MappingVersion,
			SeedObservedAt:            manifest.SeedObservedAt,
			BaselinePrefixVPCount:     source.BaselinePrefixVP,
			BaselineIPv4PrefixVPCount: source.BaselineIPv4,
			BaselineIPv6PrefixVPCount: source.BaselineIPv6,
			MembershipDigest:          source.MembershipDigest,
			Members:                   []GlobalCountryCohortMember{},
		}
		documents[source.CountryCode] = document
		asnSets[source.CountryCode] = make(map[uint32]struct{})
	}
	for _, key := range keys {
		code := state.Mapping.CountryCode(key.CountryID)
		document := documents[code]
		if document == nil {
			return nil, fmt.Errorf("cohort country %s absent from RIB manifest", code)
		}
		current := accumulators[key]
		prefixValues := make([]netip.Prefix, 0, len(current.Prefixes))
		for prefix := range current.Prefixes {
			prefixValues = append(prefixValues, prefix)
		}
		prefixes := make([]string, len(prefixValues))
		for index, prefix := range prefixValues {
			prefixes[index] = prefix.String()
		}
		sort.Strings(prefixes)
		document.Members = append(document.Members, GlobalCountryCohortMember{
			ASN: key.ASN, AFI: key.AFI,
			PrefixVPCount: current.PrefixVPCount,
			Prefixes:      prefixes,
		})
		asnSets[code][key.ASN] = struct{}{}
	}
	result := make([]GlobalCountryCohortDocument, 0, len(documents))
	for _, source := range manifest.Countries {
		document := documents[source.CountryCode]
		document.UnknownOriginPrefixVP = unknownOrigins[state.Mapping.CountryIDForCode(
			source.CountryCode,
		)]
		document.BaselineOriginASNs = sortedUint32(asnSets[source.CountryCode])
		document.BaselineOriginASNCount = len(document.BaselineOriginASNs)
		if source.BaselinePrefixVP !=
			source.BaselineIPv4+source.BaselineIPv6 {
			return nil, fmt.Errorf("country %s address-family population mismatch", source.CountryCode)
		}
		knownMemberCount := int64(0)
		for _, member := range document.Members {
			knownMemberCount += int64(member.PrefixVPCount)
		}
		if knownMemberCount+document.UnknownOriginPrefixVP !=
			document.BaselinePrefixVPCount {
			return nil, fmt.Errorf("country %s cohort member population mismatch", source.CountryCode)
		}
		result = append(result, *document)
	}
	return result, nil
}

func (mapping *GlobalCountryMapping) CountryIDForCode(code string) uint16 {
	id, exists := mapping.IDForCode(code)
	if !exists {
		return 0
	}
	return id
}

func packageSnapshotID(observation GlobalCountryObservation) string {
	return stableID("snapshot_go_v1_", map[string]any{
		"cohort_id":               observation.CohortID,
		"observed_at":             observation.ObservedAt,
		"visible_prefix_vp_count": observation.VisiblePrefixVPCount,
	}, 32)
}

func packageObservation(
	observation GlobalCountryObservation,
) GlobalPackagedObservation {
	return GlobalPackagedObservation{
		SchemaVersion:         GlobalObservationVersion,
		SnapshotID:            packageSnapshotID(observation),
		ObservedAt:            observation.ObservedAt,
		SlotStartUTC:          observation.SlotStartUTC,
		SlotEndUTC:            observation.SlotEndUTC,
		SlotRole:              observation.SlotRole,
		CountryCode:           observation.CountryCode,
		CohortID:              observation.CohortID,
		BaselineASNCount:      observation.BaselineASNCount,
		BaselinePrefixVPCount: observation.BaselinePrefixVPCount,
		VisiblePrefixVPCount:  observation.VisiblePrefixVPCount,
		VisiblePrefixVPRatio:  observation.VisiblePrefixVPRatio,
		AffectedASNCount:      observation.AffectedASNCount,
		AffectedASNRatio:      observation.AffectedASNRatio,
		VisibleOriginASNCount: observation.VisibleOriginASNCount,
		VisibleOriginASNRatio: observation.VisibleOriginASNRatio,
		IPv4:                  observation.IPv4,
		IPv6:                  observation.IPv6,
		DualClassifications:   observation.DualClassifications,
		UpdateCounts:          observation.UpdateCounts,
		CountryUpdateCounts:   observation.CountryUpdateCounts,
		CurrentPrefixVPCount:  observation.CurrentPrefixVPCount,
		GlobalStateDigest:     observation.StateDigest,
	}
}

func loadGlobalSlotProduct(path string) (GlobalSlotProduct, error) {
	var product GlobalSlotProduct
	file, err := os.Open(path)
	if err != nil {
		return product, err
	}
	defer file.Close()
	compressed, err := gzip.NewReader(file)
	if err != nil {
		return product, err
	}
	defer compressed.Close()
	if err := json.NewDecoder(compressed).Decode(&product); err != nil {
		return product, err
	}
	return product, nil
}

func copyJSONValue(path string) (map[string]any, error) {
	var result map[string]any
	if _, err := readJSON(path, &result); err != nil {
		return nil, err
	}
	return result, nil
}

func sha256RegularFile(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()
	digest := sha256.New()
	if _, err := io.Copy(digest, file); err != nil {
		return "", err
	}
	return hex.EncodeToString(digest.Sum(nil)), nil
}

func writePackageStaticDocuments(
	writer *countryPackageWriter,
	config GlobalConfig,
	rib GlobalRIBResult,
	checkpoint GlobalDeltaCheckpointManifest,
	ribQuality GlobalRIBQuality,
	updateQuality GlobalUpdateQuality,
	completedAt string,
	iranBaselinePackage string,
) error {
	if err := writeJSONAtomic(
		filepath.Join(writer.Directory, "cohort.json"), writer.Cohort,
	); err != nil {
		return err
	}
	inputSummary, err := copyJSONValue(
		filepath.Join(config.Output, "input-summary.json"),
	)
	if err != nil {
		return err
	}
	if err := writeJSONAtomic(
		filepath.Join(writer.Directory, "input-summary.json"), inputSummary,
	); err != nil {
		return err
	}

	var incident, episodes, waves map[string]any
	if writer.Code == "IR" && iranBaselinePackage != "" {
		for name, target := range map[string]*map[string]any{
			"incident.json": &incident,
			"episodes.json": &episodes,
			"waves.json":    &waves,
		} {
			value, err := copyJSONValue(filepath.Join(iranBaselinePackage, name))
			if err != nil {
				return err
			}
			*target = value
		}
	} else {
		stateWindowID := stableID("global_country_state_v1_", map[string]any{
			"dataset_id":   checkpoint.DatasetID,
			"country_code": writer.Code,
			"cohort_id":    writer.Cohort.CohortID,
			"window_start": WindowStartUTC,
			"window_end":   WindowEndUTC,
		}, 32)
		incident = map[string]any{
			"schema_version":      "rrc25-global-country-state-window/v1",
			"incident_id":         stateWindowID,
			"country_code":        writer.Code,
			"collector_id":        "rrc25",
			"cohort_id":           writer.Cohort.CohortID,
			"detected_at":         nil,
			"onset_at":            nil,
			"peak_at":             nil,
			"trough_at":           nil,
			"partial_recovery_at": nil,
			"full_recovery_at":    nil,
			"observation_end_at":  WindowEndUTC,
			"duration_state":      "window_only",
			"recovery_state":      "not_assessed",
			"normal_band": map[string]any{
				"state":  "unknown",
				"reason": "该国家切片只表示同期状态，不构成国家中断事实",
			},
			"episodes":          []any{},
			"algorithm_version": GlobalEngineVersion,
		}
		episodes = map[string]any{
			"schema_version": "rrc25-global-country-state-window-episodes/v1",
			"incident_id":    stateWindowID,
			"episodes":       []any{},
		}
		waves = map[string]any{
			"schema_version": "rrc25-global-country-state-window-waves/v1",
			"waves":          []any{},
		}
	}
	for name, value := range map[string]any{
		"incident.json": incident,
		"episodes.json": episodes,
		"waves.json":    waves,
	} {
		if err := writeJSONAtomic(filepath.Join(writer.Directory, name), value); err != nil {
			return err
		}
	}

	inputBytes := rib.Inputs.RIB.SizeBytes
	for _, artifact := range rib.Inputs.AllUpdate {
		inputBytes += artifact.SizeBytes
	}
	quality := map[string]any{
		"schema_version":                     "rrc25-global-country-package-quality/v1",
		"status":                             "pass",
		"engine_version":                     GlobalEngineVersion,
		"run_id":                             rib.RunID,
		"dataset_id":                         checkpoint.DatasetID,
		"revision":                           checkpoint.Revision,
		"country_code":                       writer.Code,
		"cohort_id":                          writer.Cohort.CohortID,
		"rib_physical_records":               ribQuality.RIBPhysicalRecords,
		"rib_entries":                        ribQuality.RIBEntries,
		"rib_retained_members":               writer.Cohort.BaselinePrefixVPCount,
		"rib_unknown_origins":                ribQuality.RIBUnknownOrigins,
		"rib_mapping_unknown":                ribQuality.RIBMappingUnknown,
		"update_physical_records":            updateQuality.UpdatePhysicalRecords,
		"update_route_events":                updateQuality.UpdateRouteEvents,
		"update_unknown_origins":             updateQuality.UpdateUnknownOrigins,
		"update_optional_unknown_attributes": updateQuality.UpdateUnknownOptional,
		"input_compressed_bytes":             inputBytes,
		"observation_count":                  checkpoint.FormalObservationCount,
		"last_observation_at":                checkpoint.DataThrough,
		"global_state_digest":                checkpoint.StateDigest,
		"packaged_at":                        completedAt,
		"checkpoint_resume_count":            0,
		"failures":                           nil,
	}
	if err := writeJSONAtomic(filepath.Join(writer.Directory, "QUALITY.json"), quality); err != nil {
		return err
	}
	return nil
}

func closeCountryPackageWriters(
	writers map[string]*countryPackageWriter,
) error {
	codes := make([]string, 0, len(writers))
	for code := range writers {
		codes = append(codes, code)
	}
	sort.Strings(codes)
	var firstErr error
	for _, code := range codes {
		writer := writers[code]
		for _, stream := range []*jsonlGzipWriter{writer.Snapshots, writer.ASNStates} {
			if stream != nil {
				if err := stream.Close(); err != nil && firstErr == nil {
					firstErr = err
				}
			}
		}
	}
	return firstErr
}

func verifyPackageIranBaseline(
	generatedRoot string,
	baselineRoot string,
) (map[string]any, error) {
	if baselineRoot == "" {
		return nil, fmt.Errorf("Iran baseline package is required")
	}
	generatedCohort, err := copyJSONValue(filepath.Join(generatedRoot, "cohort.json"))
	if err != nil {
		return nil, err
	}
	baselineCohort, err := copyJSONValue(filepath.Join(baselineRoot, "cohort.json"))
	if err != nil {
		return nil, err
	}
	for _, key := range []string{
		"cohort_id", "mapping_version", "baseline_origin_asn_count",
		"baseline_prefix_vp_count", "baseline_origin_asns", "members",
	} {
		left, _ := json.Marshal(generatedCohort[key])
		right, _ := json.Marshal(baselineCohort[key])
		if string(left) != string(right) {
			return nil, fmt.Errorf("Iran cohort differs at %s", key)
		}
	}

	loadLines := func(path string) ([]map[string]any, error) {
		file, err := os.Open(path)
		if err != nil {
			return nil, err
		}
		defer file.Close()
		compressed, err := gzip.NewReader(file)
		if err != nil {
			return nil, err
		}
		defer compressed.Close()
		decoder := json.NewDecoder(compressed)
		rows := []map[string]any{}
		for {
			var row map[string]any
			if err := decoder.Decode(&row); err != nil {
				if err == io.EOF {
					break
				}
				return nil, err
			}
			rows = append(rows, row)
		}
		return rows, nil
	}
	generatedSnapshots, err := loadLines(
		filepath.Join(generatedRoot, "country-snapshots.jsonl.gz"),
	)
	if err != nil {
		return nil, err
	}
	baselineSnapshots, err := loadLines(
		filepath.Join(baselineRoot, "country-snapshots.jsonl.gz"),
	)
	if err != nil {
		return nil, err
	}
	if len(generatedSnapshots) < 60 || len(baselineSnapshots) != 60 {
		return nil, fmt.Errorf("Iran baseline snapshot population is not 60")
	}
	snapshotFields := []string{
		"snapshot_id", "observed_at", "slot_start_utc",
		"slot_end_exclusive_utc", "slot_role", "cohort_id",
		"baseline_asn_count", "baseline_prefix_vp_count",
		"visible_prefix_vp_count", "visible_prefix_vp_ratio",
		"affected_asn_count", "affected_asn_ratio",
		"visible_origin_asn_count", "visible_origin_asn_ratio",
		"ipv4", "ipv6", "dual_stack_classifications", "update_counts",
	}
	for index := range baselineSnapshots {
		for _, key := range snapshotFields {
			left, _ := json.Marshal(generatedSnapshots[index][key])
			right, _ := json.Marshal(baselineSnapshots[index][key])
			if string(left) != string(right) {
				return nil, fmt.Errorf(
					"Iran snapshot %d differs at %s", index, key,
				)
			}
		}
	}
	generatedASNs, err := loadLines(filepath.Join(generatedRoot, "asn-states.jsonl.gz"))
	if err != nil {
		return nil, err
	}
	baselineASNs, err := loadLines(filepath.Join(baselineRoot, "asn-states.jsonl.gz"))
	if err != nil {
		return nil, err
	}
	if len(generatedASNs) < len(baselineASNs) {
		return nil, fmt.Errorf("Iran baseline ASN state population is incomplete")
	}
	sort.Slice(generatedASNs, func(i, j int) bool {
		leftAt := fmt.Sprint(generatedASNs[i]["observed_at"])
		rightAt := fmt.Sprint(generatedASNs[j]["observed_at"])
		if leftAt != rightAt {
			return leftAt < rightAt
		}
		return fmt.Sprint(generatedASNs[i]["asn"]) <
			fmt.Sprint(generatedASNs[j]["asn"])
	})
	sort.Slice(baselineASNs, func(i, j int) bool {
		leftAt := fmt.Sprint(baselineASNs[i]["observed_at"])
		rightAt := fmt.Sprint(baselineASNs[j]["observed_at"])
		if leftAt != rightAt {
			return leftAt < rightAt
		}
		return fmt.Sprint(baselineASNs[i]["asn"]) <
			fmt.Sprint(baselineASNs[j]["asn"])
	})
	asnFields := []string{
		"snapshot_id", "observed_at", "cohort_id", "asn",
		"classification", "ipv4_invisible_ipv6_visible",
	}
	for index := range baselineASNs {
		for _, key := range asnFields {
			left, _ := json.Marshal(generatedASNs[index][key])
			right, _ := json.Marshal(baselineASNs[index][key])
			if string(left) != string(right) {
				return nil, fmt.Errorf(
					"Iran ASN state %d differs at %s", index, key,
				)
			}
		}
	}
	return map[string]any{
		"status":                    "pass",
		"cohort_id":                 generatedCohort["cohort_id"],
		"baseline_snapshot_count":   len(baselineSnapshots),
		"generated_snapshot_count":  len(generatedSnapshots),
		"baseline_asn_state_count":  len(baselineASNs),
		"generated_asn_state_count": len(generatedASNs),
		"snapshot_fields_compared":  snapshotFields,
		"asn_fields_compared":       asnFields,
	}, nil
}

func BuildGlobalCountryPackages(
	config GlobalConfig,
	packageRoot string,
	iranBaselinePackage string,
) (GlobalCountryPackagesResult, error) {
	var result GlobalCountryPackagesResult
	if packageRoot == "" || iranBaselinePackage == "" {
		return result, fmt.Errorf("country package output and Iran baseline are required")
	}
	if info, err := os.Lstat(packageRoot); err == nil {
		if !info.IsDir() {
			return result, fmt.Errorf("country package output is not a directory")
		}
		return result, fmt.Errorf("country package output already exists")
	} else if !os.IsNotExist(err) {
		return result, err
	}
	staging := packageRoot + ".staging"
	if _, err := os.Lstat(staging); err == nil {
		return result, fmt.Errorf("country package staging already exists")
	} else if !os.IsNotExist(err) {
		return result, err
	}
	if err := os.MkdirAll(filepath.Join(staging, "countries"), 0o750); err != nil {
		return result, err
	}

	config.progress("从 RIB checkpoint 读取固定 cohort，不读取原始 RIB")
	rib, err := LoadGlobalRIBFromCheckpoint(config)
	if err != nil {
		return result, err
	}
	var checkpoint GlobalDeltaCheckpointManifest
	formalManifestPath := filepath.Join(
		config.Output, "checkpoints", "formal", "manifest.json",
	)
	if _, err := readJSON(formalManifestPath, &checkpoint); err != nil {
		return result, err
	}
	if checkpoint.RunID != rib.RunID ||
		checkpoint.Phase != "formal" ||
		checkpoint.FormalObservationCount != 60 ||
		checkpoint.DataThrough != WindowEndUTC ||
		len(checkpoint.Products) != 85 {
		return result, fmt.Errorf("formal checkpoint is not complete")
	}
	formalManifestSHA, err := sha256RegularFile(formalManifestPath)
	if err != nil {
		return result, err
	}
	var updateQuality GlobalUpdateQuality
	if _, err := readJSON(
		filepath.Join(config.Output, "update-quality.json"), &updateQuality,
	); err != nil {
		return result, err
	}
	var progress GlobalProgress
	if _, err := readJSON(filepath.Join(config.Output, "progress.json"), &progress); err != nil {
		return result, err
	}

	config.progress("从共享 seed RouteState 生成全部国家固定 cohort")
	cohorts, err := buildGlobalCountryCohorts(
		rib.State, rib.Manifest,
	)
	if err != nil {
		return result, err
	}
	writers := make(map[string]*countryPackageWriter, len(cohorts))
	for _, cohort := range cohorts {
		directory := filepath.Join(staging, "countries", cohort.CountryCode)
		if err := os.MkdirAll(directory, 0o750); err != nil {
			return result, err
		}
		snapshots, err := newJSONLGzipWriter(
			filepath.Join(directory, "country-snapshots.jsonl.gz"),
		)
		if err != nil {
			return result, err
		}
		asnStates, err := newJSONLGzipWriter(
			filepath.Join(directory, "asn-states.jsonl.gz"),
		)
		if err != nil {
			snapshots.Close()
			return result, err
		}
		writer := &countryPackageWriter{
			Code: cohort.CountryCode, Directory: directory, Cohort: cohort,
			Snapshots: snapshots, ASNStates: asnStates,
			ObservationIDs: make(map[string]string, 60),
		}
		writers[cohort.CountryCode] = writer
		if err := writePackageStaticDocuments(
			writer, config, rib, checkpoint, rib.Quality, updateQuality,
			progress.UpdatedAt, iranBaselinePackage,
		); err != nil {
			closeCountryPackageWriters(writers)
			return result, err
		}
	}

	config.progress("从 60 个共享正式产品分发全部国家状态，不重放 UPDATE")
	formalReferences := make([]GlobalProductReference, 0, 60)
	for _, reference := range checkpoint.Products {
		if reference.Phase == "formal" {
			formalReferences = append(formalReferences, reference)
		}
	}
	if len(formalReferences) != 60 {
		closeCountryPackageWriters(writers)
		return result, fmt.Errorf("formal product population is not 60")
	}
	for index, reference := range formalReferences {
		path := filepath.Join(config.Output, filepath.FromSlash(reference.Path))
		actualSHA, err := sha256RegularFile(path)
		if err != nil || actualSHA != reference.SHA256 {
			closeCountryPackageWriters(writers)
			if err != nil {
				return result, err
			}
			return result, fmt.Errorf("formal product hash mismatch: %s", reference.Path)
		}
		product, err := loadGlobalSlotProduct(path)
		if err != nil {
			closeCountryPackageWriters(writers)
			return result, err
		}
		if product.Phase != "formal" ||
			product.ObservedAt != reference.ObservedAt ||
			len(product.Countries) != len(writers) ||
			!product.ASNStatesIncluded {
			closeCountryPackageWriters(writers)
			return result, fmt.Errorf("formal product %d identity mismatch", index)
		}
		for _, observation := range product.Countries {
			writer := writers[observation.CountryCode]
			if writer == nil || observation.CohortID != writer.Cohort.CohortID {
				closeCountryPackageWriters(writers)
				return result, fmt.Errorf("formal country observation identity mismatch")
			}
			packaged := packageObservation(observation)
			writer.ObservationIDs[observation.ObservedAt] = packaged.SnapshotID
			if err := writer.Snapshots.Write(packaged); err != nil {
				closeCountryPackageWriters(writers)
				return result, err
			}
		}
		for _, row := range product.ASNStates {
			writer := writers[row.CountryCode]
			if writer == nil || row.CohortID != writer.Cohort.CohortID {
				closeCountryPackageWriters(writers)
				return result, fmt.Errorf("formal ASN state identity mismatch")
			}
			snapshotID := writer.ObservationIDs[row.ObservedAt]
			if snapshotID == "" {
				closeCountryPackageWriters(writers)
				return result, fmt.Errorf("ASN state has no matching country snapshot")
			}
			if err := writer.ASNStates.Write(map[string]any{
				"schema_version":              "rrc25-global-country-asn-state/v1",
				"snapshot_id":                 snapshotID,
				"observed_at":                 row.ObservedAt,
				"country_code":                row.CountryCode,
				"cohort_id":                   row.CohortID,
				"asn":                         row.ASN,
				"classification":              row.Classification,
				"ipv4_invisible_ipv6_visible": row.IPv4InvisibleIPv6Visible,
			}); err != nil {
				closeCountryPackageWriters(writers)
				return result, err
			}
		}
		config.progress(fmt.Sprintf("国家状态产品 %d/60 已分发", index+1))
	}
	if err := closeCountryPackageWriters(writers); err != nil {
		return result, err
	}

	iranComparison, err := verifyPackageIranBaseline(
		filepath.Join(staging, "countries", "IR"),
		iranBaselinePackage,
	)
	if err != nil {
		return result, err
	}
	if err := writeJSONAtomic(
		filepath.Join(staging, "iran-baseline-comparison.json"),
		iranComparison,
	); err != nil {
		return result, err
	}

	codes := make([]string, 0, len(writers))
	for code := range writers {
		codes = append(codes, code)
	}
	sort.Strings(codes)
	entries := make([]GlobalCountryPackageCatalogEntry, 0, len(codes))
	for _, code := range codes {
		writer := writers[code]
		deliverables := []string{
			"QUALITY.json", "asn-states.jsonl.gz", "cohort.json",
			"country-snapshots.jsonl.gz", "episodes.json", "incident.json",
			"input-summary.json", "waves.json",
		}
		hashes := make(map[string]string, len(deliverables))
		for _, name := range deliverables {
			hash, err := sha256RegularFile(filepath.Join(writer.Directory, name))
			if err != nil {
				return result, err
			}
			hashes[name] = hash
		}
		complete := map[string]any{
			"schema_version":                GlobalCountryPackageVersion,
			"engine_version":                GlobalEngineVersion,
			"status":                        "complete",
			"run_id":                        rib.RunID,
			"dataset_id":                    checkpoint.DatasetID,
			"revision":                      checkpoint.Revision,
			"country_code":                  code,
			"cohort_id":                     writer.Cohort.CohortID,
			"completed_at":                  progress.UpdatedAt,
			"observation_count":             60,
			"last_observation_at":           checkpoint.DataThrough,
			"global_formal_manifest_sha256": formalManifestSHA,
			"global_state_digest":           checkpoint.StateDigest,
			"deliverable_sha256":            hashes,
		}
		if err := writeJSONAtomic(
			filepath.Join(writer.Directory, "COMPLETE.json"), complete,
		); err != nil {
			return result, err
		}
		completeSHA, err := sha256RegularFile(
			filepath.Join(writer.Directory, "COMPLETE.json"),
		)
		if err != nil {
			return result, err
		}
		relative := filepath.ToSlash(filepath.Join("countries", code))
		entries = append(entries, GlobalCountryPackageCatalogEntry{
			CountryCode: code, CohortID: writer.Cohort.CohortID,
			BaselineOriginASNs:   writer.Cohort.BaselineOriginASNCount,
			BaselinePrefixVP:     writer.Cohort.BaselinePrefixVPCount,
			BaselineIPv4PrefixVP: writer.Cohort.BaselineIPv4PrefixVPCount,
			BaselineIPv6PrefixVP: writer.Cohort.BaselineIPv6PrefixVPCount,
			ObservationCount:     60, PackagePath: relative,
			CompleteSHA256:        completeSHA,
			SnapshotsSHA256:       hashes["country-snapshots.jsonl.gz"],
			ASNStatesSHA256:       hashes["asn-states.jsonl.gz"],
			CohortDocumentSHA256:  hashes["cohort.json"],
			UnknownOriginPrefixVP: writer.Cohort.UnknownOriginPrefixVP,
		})
	}
	result = GlobalCountryPackagesResult{
		SchemaVersion: GlobalCountryPackageVersion,
		EngineVersion: GlobalEngineVersion,
		RunID:         rib.RunID, DatasetID: checkpoint.DatasetID,
		Revision: checkpoint.Revision, CollectorID: "rrc25",
		MappingVersion:   rib.Mapping.MappingVersion,
		DataThrough:      checkpoint.DataThrough,
		ObservationCount: 60, CountryCount: len(entries),
		PackageRoot: packageRoot, FormalManifestSHA: formalManifestSHA,
		Countries: entries,
	}
	if err := writeJSONAtomic(filepath.Join(staging, "catalog.json"), result); err != nil {
		return result, err
	}
	catalogSHA, err := sha256RegularFile(filepath.Join(staging, "catalog.json"))
	if err != nil {
		return result, err
	}
	comparisonSHA, err := sha256RegularFile(
		filepath.Join(staging, "iran-baseline-comparison.json"),
	)
	if err != nil {
		return result, err
	}
	if err := writeJSONAtomic(filepath.Join(staging, "COMPLETE.json"), map[string]any{
		"schema_version":                  "rrc25-global-country-package-catalog/v1",
		"engine_version":                  GlobalEngineVersion,
		"status":                          "complete",
		"run_id":                          rib.RunID,
		"dataset_id":                      checkpoint.DatasetID,
		"revision":                        checkpoint.Revision,
		"country_count":                   len(entries),
		"observation_count_per_country":   60,
		"data_through":                    checkpoint.DataThrough,
		"catalog_sha256":                  catalogSHA,
		"iran_baseline_comparison_sha256": comparisonSHA,
	}); err != nil {
		return result, err
	}
	if err := os.Rename(staging, packageRoot); err != nil {
		return result, err
	}
	return result, nil
}
