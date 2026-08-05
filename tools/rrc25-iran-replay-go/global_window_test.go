package replay

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func globalWindowTestArtifact(kind string, at string, path string, suffix byte) Artifact {
	return Artifact{
		ArtifactID:      "art_v1_" + strings.Repeat(string(suffix), 32),
		ArtifactTimeUTC: at, ArtifactType: kind, CollectorID: "rrc25",
		Compression: "gz", FileSHA256: strings.Repeat(string(suffix), 64),
		RelativePath: path, SizeBytes: 1,
	}
}

func writeGlobalWindowTestSelection(t *testing.T, value GlobalWindowSelection) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "selection.json")
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestGlobalWindowSelectionRequiresEveryFiveMinuteSlot(t *testing.T) {
	selection := GlobalWindowSelection{
		SchemaVersion: GlobalWindowSelectionVersion,
		CollectorID:   "rrc25", Timezone: "UTC",
		SourceManifestSHA256:  strings.Repeat("d", 64),
		SourceManifestStatus:  "verified_manifest_plus_isolated_repairs",
		WindowStartUTC:        "2026-02-24T00:00:00Z",
		WindowEndExclusiveUTC: "2026-02-24T00:10:00Z",
		RIB: globalWindowTestArtifact(
			"rib", "2026-02-24T00:00:00Z",
			"rrc25/2026.02/bview.20260224.0000.gz", 'a',
		),
		Updates: []Artifact{
			globalWindowTestArtifact(
				"update", "2026-02-24T00:00:00Z",
				"rrc25/2026.02/updates.20260224.0000.gz", 'b',
			),
			globalWindowTestArtifact(
				"update", "2026-02-24T00:05:00Z",
				"rrc25/2026.02/updates.20260224.0005.gz", 'c',
			),
		},
	}
	path := writeGlobalWindowTestSelection(t, selection)
	parsed, digest, start, end, err := parseGlobalWindowSelection(path)
	if err != nil {
		t.Fatal(err)
	}
	if len(digest) != 64 || len(parsed.Updates) != 2 ||
		end.Sub(start) != 2*globalWindowSlot {
		t.Fatalf("unexpected parsed selection: %+v %s", parsed, digest)
	}

	selection.Updates[1].ArtifactTimeUTC = "2026-02-24T00:10:00Z"
	path = writeGlobalWindowTestSelection(t, selection)
	if _, _, _, _, err := parseGlobalWindowSelection(path); err == nil ||
		!strings.Contains(err.Error(), "not continuous") {
		t.Fatalf("discontinuous input was not rejected: %v", err)
	}

	selection.Updates[1].ArtifactTimeUTC = "2026-02-24T00:05:00Z"
	selection.Updates[1].RelativePath = "rrc25/2026.02/updates.20260224.0010.gz"
	path = writeGlobalWindowTestSelection(t, selection)
	if _, _, _, _, err := parseGlobalWindowSelection(path); err == nil ||
		!strings.Contains(err.Error(), "path mismatch") {
		t.Fatalf("cross-slot input path was not rejected: %v", err)
	}
}

func TestGlobalWindowProgressMustMatchRunIdentityAndDataThrough(t *testing.T) {
	selection := GlobalWindowSelection{
		WindowStartUTC: "2026-02-24T00:00:00Z",
		Updates:        make([]Artifact, 288),
	}
	progress := globalWindowProgress{
		SchemaVersion: "rrc25-global-window-progress/v1",
		RunID:         "run", DatasetID: "dataset", Revision: GlobalWindowRevision,
		Phase: "replay", ProcessedUpdateCount: 288,
		DataThrough: "2026-02-25T00:00:00Z",
	}
	if err := validateGlobalWindowProgress(
		progress, selection, "run", "dataset",
	); err != nil {
		t.Fatal(err)
	}
	progress.DatasetID = "wrong"
	if err := validateGlobalWindowProgress(
		progress, selection, "run", "dataset",
	); err == nil || !strings.Contains(err.Error(), "identity mismatch") {
		t.Fatalf("mismatched progress identity was not rejected: %v", err)
	}
}

func TestGlobalWindowSummaryUsesBoundSeedAndCompactASNCounts(t *testing.T) {
	mapping := newSyntheticGlobalCountryMapping(map[uint32]string{
		64500: "IR",
		64501: "US",
	})
	state, err := NewGlobalReplayState(mapping, 4)
	if err != nil {
		t.Fatal(err)
	}
	state.SeedObservedAt = "2026-02-24T00:00:00Z"
	ir4a := globalRouteKey("192.0.2.1", 64496, "203.0.113.0/24")
	ir4b := globalRouteKey("192.0.2.2", 64497, "203.0.114.0/24")
	ir6 := globalRouteKey("2001:db8::1", 64498, "2001:db8:1::/48")
	us4 := globalRouteKey("192.0.2.3", 64499, "198.51.100.0/24")
	for _, row := range []struct {
		key RouteKey
		asn uint32
	}{{ir4a, 64500}, {ir4b, 64500}, {ir6, 64500}, {us4, 64501}} {
		if err := state.Seed(row.key, true, row.asn, 1, 0); err != nil {
			t.Fatal(err)
		}
	}
	activity := NewGlobalSlotActivity()
	if err := state.Apply(ParsedEvent{
		Key: ir4a, Action: actionWithdraw,
		EventMicros: mustUTC("2026-02-24T00:01:00Z").UnixMicro(),
	}, activity); err != nil {
		t.Fatal(err)
	}
	rows, conservation, err := state.SnapshotGlobalCountrySummaries(
		"2026-02-24T00:05:00Z", "2026-02-24T00:00:00Z",
		"2026-02-24T00:05:00Z", activity,
	)
	if err != nil {
		t.Fatal(err)
	}
	if conservation.Status != "pass" || len(rows) != 2 {
		t.Fatalf("unexpected conservation or countries: %+v rows=%d", conservation, len(rows))
	}
	var iran GlobalCountrySummary
	for _, row := range rows {
		if row.CountryCode == "IR" {
			iran = row
		}
	}
	if iran.SeedObservedAt != state.SeedObservedAt ||
		iran.BaselinePrefixVP != 3 || iran.VisiblePrefixVP != 2 ||
		iran.BaselineASNCount != 1 || iran.VisibleOriginASNCount != 1 ||
		iran.AffectedASNCount != 1 || iran.FullyInvisibleASNCount != 0 ||
		iran.IPv4.BaselinePrefixVP != 2 || iran.IPv4.VisiblePrefixVP != 1 ||
		iran.IPv6.BaselinePrefixVP != 1 || iran.IPv6.VisiblePrefixVP != 1 ||
		iran.CountryUpdateCounts.Withdraw != 1 ||
		iran.CollectorUpdateCounts.Withdraw != 1 {
		t.Fatalf("unexpected compact IR summary: %+v", iran)
	}
}

func TestGlobalWindowContinuationPreservesSeedIdentity(t *testing.T) {
	mapping := newSyntheticGlobalCountryMapping(map[uint32]string{64500: "IR"})
	state, err := NewGlobalReplayState(mapping, 1)
	if err != nil {
		t.Fatal(err)
	}
	state.SeedObservedAt = "2026-02-24T00:00:00Z"
	key := globalRouteKey("192.0.2.1", 64496, "203.0.113.0/24")
	if err := state.Seed(key, true, 64500, 1, 0); err != nil {
		t.Fatal(err)
	}
	directory := filepath.Join(t.TempDir(), "checkpoint")
	manifest, sha, err := WriteGlobalContinuationCheckpoint(
		directory, state, GlobalContinuationCheckpointManifest{
			RunID: "run", DatasetID: "dataset", Revision: GlobalDatasetRevision,
			DataThrough: "2026-02-25T00:00:00Z", ProductSequence: 1,
			ProcessedSlot: 287, ProcessedUpdateCount: 288,
			PreviousProductSHA256:  strings.Repeat("a", 64),
			SourceCheckpointSHA256: strings.Repeat("b", 64),
			ShardCount:             2,
		},
	)
	if err != nil {
		t.Fatal(err)
	}
	if len(sha) != 64 || manifest.SeedObservedAt != state.SeedObservedAt {
		t.Fatalf("checkpoint omitted seed identity: %+v", manifest)
	}
	restored, loaded, _, err := LoadGlobalContinuationCheckpoint(directory, mapping)
	if err != nil {
		t.Fatal(err)
	}
	iranID, _ := mapping.IDForCode("IR")
	if restored.SeedObservedAt != state.SeedObservedAt ||
		restored.CohortID(iranID) != state.CohortID(iranID) ||
		loaded.SeedObservedAt != state.SeedObservedAt {
		t.Fatal("continuation checkpoint changed the long-window seed identity")
	}
}
