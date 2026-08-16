package replay

import (
	"compress/gzip"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"time"
)

func copyJSONLines(
	source string,
	target *jsonlGzipWriter,
) (int, string, error) {
	file, err := os.Open(source)
	if err != nil {
		return 0, "", err
	}
	defer file.Close()
	compressed, err := gzip.NewReader(file)
	if err != nil {
		return 0, "", err
	}
	defer compressed.Close()
	decoder := json.NewDecoder(compressed)
	count := 0
	lastObservedAt := ""
	for {
		var row map[string]any
		if err := decoder.Decode(&row); err != nil {
			if err == io.EOF {
				break
			}
			return 0, "", err
		}
		if err := target.Write(row); err != nil {
			return 0, "", err
		}
		if observedAt, ok := row["observed_at"].(string); ok {
			lastObservedAt = observedAt
		}
		count++
	}
	return count, lastObservedAt, nil
}

func numericJSONInt(value any) (int64, bool) {
	switch typed := value.(type) {
	case float64:
		if typed != float64(int64(typed)) {
			return 0, false
		}
		return int64(typed), true
	case int64:
		return typed, true
	case int:
		return int64(typed), true
	default:
		return 0, false
	}
}

func addJSONInt(target map[string]any, key string, delta int64) error {
	current, ok := numericJSONInt(target[key])
	if !ok {
		return fmt.Errorf("quality field %s is not an integer", key)
	}
	target[key] = current + delta
	return nil
}

func verifyBaseCountryPackage(
	directory string,
	entry GlobalCountryPackageCatalogEntry,
) (map[string]any, error) {
	completePath := filepath.Join(directory, "COMPLETE.json")
	actualComplete, err := sha256RegularFile(completePath)
	if err != nil {
		return nil, err
	}
	if actualComplete != entry.CompleteSHA256 {
		return nil, fmt.Errorf("base country COMPLETE hash mismatch: %s", entry.CountryCode)
	}
	complete, err := copyJSONValue(completePath)
	if err != nil {
		return nil, err
	}
	rawHashes, ok := complete["deliverable_sha256"].(map[string]any)
	if !ok {
		return nil, fmt.Errorf("base country deliverable hashes missing")
	}
	for name, rawExpected := range rawHashes {
		expected, ok := rawExpected.(string)
		if !ok || len(expected) != 64 {
			return nil, fmt.Errorf("base country deliverable hash invalid")
		}
		actual, err := sha256RegularFile(filepath.Join(directory, name))
		if err != nil {
			return nil, err
		}
		if actual != expected {
			return nil, fmt.Errorf(
				"base country deliverable hash mismatch: %s/%s",
				entry.CountryCode, name,
			)
		}
	}
	return complete, nil
}

func ExtendGlobalCountryPackages(
	basePackageRoot string,
	appendRoot string,
	packageOutput string,
	iranBaselinePackage string,
	progress func(string),
) (GlobalCountryPackagesResult, error) {
	var result GlobalCountryPackagesResult
	if basePackageRoot == "" || appendRoot == "" || packageOutput == "" ||
		iranBaselinePackage == "" {
		return result, fmt.Errorf(
			"base package, append root, package output and Iran baseline are required",
		)
	}
	if _, err := os.Lstat(packageOutput); err == nil {
		return result, fmt.Errorf("extended country package output already exists")
	} else if !os.IsNotExist(err) {
		return result, err
	}
	staging := packageOutput + ".staging"
	if _, err := os.Lstat(staging); err == nil {
		return result, fmt.Errorf("extended country package staging already exists")
	} else if !os.IsNotExist(err) {
		return result, err
	}

	var base GlobalCountryPackagesResult
	if _, err := readJSON(filepath.Join(basePackageRoot, "catalog.json"), &base); err != nil {
		return result, err
	}
	if base.SchemaVersion != GlobalCountryPackageVersion ||
		base.EngineVersion != GlobalEngineVersion ||
		base.ObservationCount != 60 ||
		base.DataThrough != WindowEndUTC ||
		base.CountryCount != len(base.Countries) {
		return result, fmt.Errorf("base country package catalog identity mismatch")
	}
	baseCatalogSHA, err := sha256RegularFile(
		filepath.Join(basePackageRoot, "catalog.json"),
	)
	if err != nil {
		return result, err
	}
	var baseComplete map[string]any
	if _, err := readJSON(
		filepath.Join(basePackageRoot, "COMPLETE.json"), &baseComplete,
	); err != nil {
		return result, err
	}
	if baseComplete["catalog_sha256"] != baseCatalogSHA {
		return result, fmt.Errorf("base package catalog hash is not closed")
	}

	var appendSummary GlobalAppendResult
	if _, err := readJSON(
		filepath.Join(appendRoot, "append-summary.json"), &appendSummary,
	); err != nil {
		return result, err
	}
	productPath := filepath.Join(
		appendRoot, filepath.FromSlash(appendSummary.ProductPath),
	)
	productSHA, err := sha256RegularFile(productPath)
	if err != nil {
		return result, err
	}
	if appendSummary.Status != "complete" ||
		appendSummary.RunID != base.RunID ||
		appendSummary.DatasetID != base.DatasetID ||
		appendSummary.Revision != base.Revision ||
		appendSummary.PreviousDataThrough != base.DataThrough ||
		appendSummary.ProductSequence != 86 ||
		appendSummary.ProductSHA256 != productSHA {
		return result, fmt.Errorf("append product identity mismatch")
	}
	product, err := loadGlobalSlotProduct(productPath)
	if err != nil {
		return result, err
	}
	if product.Phase != "append" ||
		product.ProductSequence != appendSummary.ProductSequence ||
		product.ObservedAt != appendSummary.DataThrough ||
		!product.ASNStatesIncluded ||
		len(product.Countries) != base.CountryCount {
		return result, fmt.Errorf("append slot product is incomplete")
	}
	var spoolMeta SlotSpoolMeta
	if _, err := readJSON(filepath.Join(
		appendRoot, "spool", fmt.Sprintf("%03d", appendSummary.ArtifactIndex),
		"meta.json",
	), &spoolMeta); err != nil {
		return result, err
	}
	if spoolMeta.Artifact != appendSummary.InputArtifact {
		return result, fmt.Errorf("append spool artifact differs from summary")
	}

	observations := make(map[string]GlobalCountryObservation, len(product.Countries))
	asnRows := make(map[string][]GlobalASNStateRow, len(product.Countries))
	for _, observation := range product.Countries {
		if _, exists := observations[observation.CountryCode]; exists {
			return result, fmt.Errorf("duplicate append country observation")
		}
		observations[observation.CountryCode] = observation
	}
	for _, row := range product.ASNStates {
		asnRows[row.CountryCode] = append(asnRows[row.CountryCode], row)
	}
	for code := range asnRows {
		sort.Slice(asnRows[code], func(i, j int) bool {
			return asnRows[code][i].ASN < asnRows[code][j].ASN
		})
	}

	if err := os.MkdirAll(filepath.Join(staging, "countries"), 0o750); err != nil {
		return result, err
	}
	entries := make([]GlobalCountryPackageCatalogEntry, 0, len(base.Countries))
	for index, entry := range base.Countries {
		baseDirectory := filepath.Join(
			basePackageRoot, filepath.FromSlash(entry.PackagePath),
		)
		baseCountryComplete, err := verifyBaseCountryPackage(
			baseDirectory, entry,
		)
		if err != nil {
			return result, err
		}
		observation, exists := observations[entry.CountryCode]
		if !exists || observation.CohortID != entry.CohortID {
			return result, fmt.Errorf(
				"append observation missing or cohort mismatch: %s",
				entry.CountryCode,
			)
		}
		directory := filepath.Join(staging, "countries", entry.CountryCode)
		if err := os.MkdirAll(directory, 0o750); err != nil {
			return result, err
		}
		var cohort GlobalCountryCohortDocument
		if _, err := readJSON(filepath.Join(baseDirectory, "cohort.json"), &cohort); err != nil {
			return result, err
		}
		if cohort.CohortID != entry.CohortID ||
			cohort.CountryCode != entry.CountryCode {
			return result, fmt.Errorf("base country cohort identity mismatch")
		}
		for _, name := range []string{"cohort.json", "episodes.json", "waves.json"} {
			value, err := copyJSONValue(filepath.Join(baseDirectory, name))
			if err != nil {
				return result, err
			}
			if err := writeJSONAtomic(filepath.Join(directory, name), value); err != nil {
				return result, err
			}
		}
		incident, err := copyJSONValue(filepath.Join(baseDirectory, "incident.json"))
		if err != nil {
			return result, err
		}
		incident["observation_end_at"] = product.ObservedAt
		if err := writeJSONAtomic(filepath.Join(directory, "incident.json"), incident); err != nil {
			return result, err
		}
		inputSummary, err := copyJSONValue(
			filepath.Join(baseDirectory, "input-summary.json"),
		)
		if err != nil {
			return result, err
		}
		inputSummary["continuous_append"] = map[string]any{
			"artifact":                     appendSummary.InputArtifact,
			"artifact_index":               appendSummary.ArtifactIndex,
			"product_sha256":               appendSummary.ProductSHA256,
			"previous_checkpoint_sha256":   appendSummary.PreviousCheckpointSHA256,
			"checkpoint_sha256":            appendSummary.CheckpointSHA256,
			"loaded_rib":                   false,
			"reapplied_prior_update_count": 0,
		}
		if err := writeJSONAtomic(
			filepath.Join(directory, "input-summary.json"), inputSummary,
		); err != nil {
			return result, err
		}

		snapshotWriter, err := newJSONLGzipWriter(
			filepath.Join(directory, "country-snapshots.jsonl.gz"),
		)
		if err != nil {
			return result, err
		}
		snapshotCount, lastSnapshot, err := copyJSONLines(
			filepath.Join(baseDirectory, "country-snapshots.jsonl.gz"),
			snapshotWriter,
		)
		if err != nil {
			_ = snapshotWriter.Close()
			return result, err
		}
		if snapshotCount != 60 || lastSnapshot != base.DataThrough {
			_ = snapshotWriter.Close()
			return result, fmt.Errorf("base country snapshot timeline mismatch")
		}
		packaged := packageObservation(observation)
		if err := snapshotWriter.Write(packaged); err != nil {
			_ = snapshotWriter.Close()
			return result, err
		}
		if err := snapshotWriter.Close(); err != nil {
			return result, err
		}

		asnWriter, err := newJSONLGzipWriter(
			filepath.Join(directory, "asn-states.jsonl.gz"),
		)
		if err != nil {
			return result, err
		}
		baseASNCount, lastASNAt, err := copyJSONLines(
			filepath.Join(baseDirectory, "asn-states.jsonl.gz"), asnWriter,
		)
		if err != nil {
			_ = asnWriter.Close()
			return result, err
		}
		expectedBaseASNCount := entry.BaselineOriginASNs * 60
		if baseASNCount != expectedBaseASNCount ||
			(expectedBaseASNCount > 0 && lastASNAt != base.DataThrough) {
			_ = asnWriter.Close()
			return result, fmt.Errorf("base country ASN timeline mismatch")
		}
		if len(asnRows[entry.CountryCode]) != entry.BaselineOriginASNs {
			_ = asnWriter.Close()
			return result, fmt.Errorf("append country ASN population mismatch")
		}
		for _, row := range asnRows[entry.CountryCode] {
			if row.CohortID != entry.CohortID ||
				row.ObservedAt != product.ObservedAt {
				_ = asnWriter.Close()
				return result, fmt.Errorf("append ASN state identity mismatch")
			}
			if err := asnWriter.Write(map[string]any{
				"schema_version":              "rrc25-global-country-asn-state/v1",
				"snapshot_id":                 packaged.SnapshotID,
				"observed_at":                 row.ObservedAt,
				"country_code":                row.CountryCode,
				"cohort_id":                   row.CohortID,
				"asn":                         row.ASN,
				"classification":              row.Classification,
				"ipv4_invisible_ipv6_visible": row.IPv4InvisibleIPv6Visible,
			}); err != nil {
				_ = asnWriter.Close()
				return result, err
			}
		}
		if err := asnWriter.Close(); err != nil {
			return result, err
		}

		quality, err := copyJSONValue(filepath.Join(baseDirectory, "QUALITY.json"))
		if err != nil {
			return result, err
		}
		for key, delta := range map[string]int64{
			"update_physical_records": spoolMeta.Stats.PhysicalRecords,
			"update_route_events":     spoolMeta.Stats.RouteEvents,
			"update_unknown_origins":  spoolMeta.Stats.UnknownOrigins,
			"input_compressed_bytes":  appendSummary.InputArtifact.SizeBytes,
		} {
			if err := addJSONInt(quality, key, delta); err != nil {
				return result, err
			}
		}
		quality["observation_count"] = 61
		quality["last_observation_at"] = product.ObservedAt
		quality["global_state_digest"] = product.Conservation.StateDigest
		quality["checkpoint_resume_count"] = 1
		quality["continuous_append_product_sha256"] = productSHA
		quality["continuous_append_malformed_otc_attributes"] =
			spoolMeta.Stats.MalformedOTC
		quality["continuous_append_treat_as_withdraw_route_events"] =
			spoolMeta.Stats.TreatAsWithdraw
		quality["packaged_at"] = time.Now().UTC().Format(time.RFC3339)
		if err := writeJSONAtomic(filepath.Join(directory, "QUALITY.json"), quality); err != nil {
			return result, err
		}

		deliverables := []string{
			"QUALITY.json", "asn-states.jsonl.gz", "cohort.json",
			"country-snapshots.jsonl.gz", "episodes.json", "incident.json",
			"input-summary.json", "waves.json",
		}
		hashes := make(map[string]string, len(deliverables))
		for _, name := range deliverables {
			hash, err := sha256RegularFile(filepath.Join(directory, name))
			if err != nil {
				return result, err
			}
			hashes[name] = hash
		}
		complete := map[string]any{
			"schema_version": GlobalCountryPackageVersion,
			"engine_version": GlobalEngineVersion, "status": "complete",
			"run_id": base.RunID, "dataset_id": base.DatasetID,
			"revision": base.Revision, "country_code": entry.CountryCode,
			"cohort_id":                     entry.CohortID,
			"completed_at":                  time.Now().UTC().Format(time.RFC3339),
			"observation_count":             61,
			"last_observation_at":           product.ObservedAt,
			"global_formal_manifest_sha256": base.FormalManifestSHA,
			"global_state_digest":           product.Conservation.StateDigest,
			"base_complete_sha256":          entry.CompleteSHA256,
			"append_product_sha256":         productSHA,
			"deliverable_sha256":            hashes,
		}
		if err := writeJSONAtomic(filepath.Join(directory, "COMPLETE.json"), complete); err != nil {
			return result, err
		}
		completeSHA, err := sha256RegularFile(filepath.Join(directory, "COMPLETE.json"))
		if err != nil {
			return result, err
		}
		entries = append(entries, GlobalCountryPackageCatalogEntry{
			CountryCode: entry.CountryCode, CohortID: entry.CohortID,
			BaselineOriginASNs:    entry.BaselineOriginASNs,
			BaselinePrefixVP:      entry.BaselinePrefixVP,
			BaselineIPv4PrefixVP:  entry.BaselineIPv4PrefixVP,
			BaselineIPv6PrefixVP:  entry.BaselineIPv6PrefixVP,
			ObservationCount:      61,
			PackagePath:           filepath.ToSlash(filepath.Join("countries", entry.CountryCode)),
			CompleteSHA256:        completeSHA,
			SnapshotsSHA256:       hashes["country-snapshots.jsonl.gz"],
			ASNStatesSHA256:       hashes["asn-states.jsonl.gz"],
			CohortDocumentSHA256:  hashes["cohort.json"],
			UnknownOriginPrefixVP: entry.UnknownOriginPrefixVP,
		})
		if progress != nil && ((index+1)%20 == 0 || index+1 == len(base.Countries)) {
			progress(fmt.Sprintf(
				"连续追加国家包 %d/%d 已生成", index+1, len(base.Countries),
			))
		}
		_ = baseCountryComplete
	}
	iranComparison, err := verifyPackageIranBaseline(
		filepath.Join(staging, "countries", "IR"), iranBaselinePackage,
	)
	if err != nil {
		return result, err
	}
	iranComparison["append_observed_at"] = product.ObservedAt
	iranComparison["append_product_sha256"] = productSHA
	if err := writeJSONAtomic(
		filepath.Join(staging, "iran-baseline-comparison.json"),
		iranComparison,
	); err != nil {
		return result, err
	}

	result = GlobalCountryPackagesResult{
		SchemaVersion: GlobalCountryPackageVersion,
		EngineVersion: GlobalEngineVersion,
		RunID:         base.RunID, DatasetID: base.DatasetID,
		Revision: base.Revision, CollectorID: base.CollectorID,
		MappingVersion: base.MappingVersion,
		DataThrough:    product.ObservedAt, ObservationCount: 61,
		CountryCount: len(entries), PackageRoot: packageOutput,
		FormalManifestSHA:   base.FormalManifestSHA,
		BaseCatalogSHA:      baseCatalogSHA,
		AppendProductSHA:    productSHA,
		PreviousDataThrough: base.DataThrough,
		Countries:           entries,
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
		"schema_version": "rrc25-global-country-package-catalog/v1",
		"engine_version": GlobalEngineVersion, "status": "complete",
		"run_id": base.RunID, "dataset_id": base.DatasetID,
		"revision": base.Revision, "country_count": len(entries),
		"observation_count_per_country":   61,
		"previous_data_through":           base.DataThrough,
		"data_through":                    product.ObservedAt,
		"base_catalog_sha256":             baseCatalogSHA,
		"append_product_sha256":           productSHA,
		"catalog_sha256":                  catalogSHA,
		"iran_baseline_comparison_sha256": comparisonSHA,
		"loaded_rib":                      false, "reapplied_prior_update_count": 0,
	}); err != nil {
		return result, err
	}
	if err := os.Rename(staging, packageOutput); err != nil {
		return result, err
	}
	return result, nil
}
