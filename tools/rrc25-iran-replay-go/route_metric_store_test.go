package replay

import (
	"os"
	"path/filepath"
	"testing"
)

func TestRouteMetricTSVIsDeterministicAndTamperClosed(t *testing.T) {
	write := func(root string) RouteMetricStoreFile {
		writer, err := newRouteMetricTSVWriter(
			root, "metrics/test.tsv.gz", "test_metric", "2026-02-24",
			[]string{"subject", "value"},
		)
		if err != nil {
			t.Fatal(err)
		}
		if err := writer.Write([]string{"rrc25", "0"}); err != nil {
			t.Fatal(err)
		}
		if err := writer.Write([]string{"IR", "1"}); err != nil {
			t.Fatal(err)
		}
		meta, err := writer.Close(root)
		if err != nil {
			t.Fatal(err)
		}
		return meta
	}
	firstRoot := t.TempDir()
	secondRoot := t.TempDir()
	first := write(firstRoot)
	second := write(secondRoot)
	if first != second || first.RowCount != 2 {
		t.Fatalf("deterministic TSV identity mismatch: first=%+v second=%+v", first, second)
	}
	if err := verifyRouteMetricStoreFile(firstRoot, first); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(firstRoot, filepath.FromSlash(first.Path))
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_APPEND, 0)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := file.Write([]byte{0}); err != nil {
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	if err := verifyRouteMetricStoreFile(firstRoot, first); err == nil {
		t.Fatal("tampered route metric TSV was accepted")
	}
}

func TestRouteMetricSlotContentIdentityChangesWithWatermark(t *testing.T) {
	record := RouteMetricSlotRecord{
		SchemaVersion: RouteMetricSlotVersion,
		Slot:          1, ArtifactTimeUTC: "2026-02-24T00:00:00Z",
		StatePointUTC:    "2026-02-24T00:05:00Z",
		AttemptedThrough: "2026-02-24T00:05:00Z",
		DataThrough:      "2026-02-24T00:05:00Z",
		QualityStatus:    "complete", GapStatus: "none",
		SourceRouteStateDatasetID:  "route_state_dataset_v1_test",
		SourceRouteStateSlotSHA256: "state", SourceRouteEventFileSHA256: "event",
		TransitionSHA256: "transition", RouteEventCount: 2, AnnounceCount: 1,
		WithdrawCount: 1, RouteStateRecordCount: 3, VisibleRouteCount: 2,
		RouteStateDigest: "digest", CountryMetricRowCount: 241,
		ASNMetricRowCount: 2, CollectorMetricRowCount: 1,
		MetricSnapshotSHA256: "metric",
	}
	record.ContentSHA256 = routeMetricSlotContentSHA(record)
	changed := record
	changed.DataThrough = "2026-02-24T00:00:00Z"
	changed.ContentSHA256 = routeMetricSlotContentSHA(changed)
	if record.ContentSHA256 == changed.ContentSHA256 {
		t.Fatal("data_through change did not change the slot content identity")
	}
}
