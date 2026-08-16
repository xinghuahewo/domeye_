package replay

import (
	"bufio"
	"compress/gzip"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestRouteStateConsumesCompleteRouteEventPartitions(t *testing.T) {
	rawRoot := t.TempDir()
	output := t.TempDir()
	rib, update, _, _ := buildSyntheticRouteEventPartitions(t, rawRoot, output)
	parsedRIB, err := parseRouteStatePartition(output, rib)
	if err != nil {
		t.Fatal(err)
	}
	parsedUpdate, err := parseRouteStatePartition(output, update)
	if err != nil {
		t.Fatal(err)
	}
	if parsedRIB.RouteEventCount != 1 || parsedRIB.RIBSnapshotCount != 1 ||
		parsedUpdate.RouteEventCount != 1 || parsedUpdate.AnnounceCount != 1 {
		t.Fatalf("unexpected parsed RouteEvent population: %+v %+v", parsedRIB, parsedUpdate)
	}
	state, _ := NewRouteState(1)
	for _, event := range parsedRIB.Events {
		if err := state.Apply(event); err != nil {
			t.Fatal(err)
		}
	}
	before := state.StateDigest.Hex()
	record, err := applyParsedRouteStatePartition(state, update, parsedUpdate)
	if err != nil {
		t.Fatal(err)
	}
	if record.Slot != 1 || record.RouteEventCount != 1 ||
		record.StateDigest == before || record.RouteStateRecordCount != 1 ||
		record.ContentSHA256 != routeStateSlotContentSHA(record) {
		t.Fatalf("unexpected RouteState slot result: %+v", record)
	}
	for key, value := range state.Routes {
		if key.Collector != RouteStateCollectorRRC25 || !value.Visible ||
			!value.OriginKnown || !value.ASPathKnown || !value.AttributeKnown ||
			value.LastArtifactIndex != 1 {
			t.Fatalf("complete RouteEvent fields were not retained in RouteState: %+v %+v", key, value)
		}
	}
}

func TestRouteStateRejectsChangedSourceCoordinateIdentity(t *testing.T) {
	rawRoot := t.TempDir()
	output := t.TempDir()
	_, update, _, _ := buildSyntheticRouteEventPartitions(t, rawRoot, output)
	file, err := os.Open(filepath.Join(output, filepath.FromSlash(update.Events.Path)))
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	decoded, err := gzip.NewReader(file)
	if err != nil {
		t.Fatal(err)
	}
	defer decoded.Close()
	scanner := bufio.NewScanner(decoded)
	if !scanner.Scan() {
		t.Fatal("missing synthetic RouteEvent")
	}
	var row routeEventRow
	if err := json.Unmarshal(scanner.Bytes(), &row); err != nil {
		t.Fatal(err)
	}
	row.RecordOrdinal++
	if _, err := routeStateEventFromRow(row, update.Artifact, update.ArtifactIndex); err == nil {
		t.Fatal("RouteEvent whose identity no longer matches its source coordinate was accepted")
	}
}

func TestRouteStateSlotLedgerRoundTripAndMismatch(t *testing.T) {
	records := []RouteStateSlotRecord{
		{
			SchemaVersion: RouteStateSlotLedgerVersion, Slot: 1, ArtifactIndex: 1,
			ArtifactID: "artifact-1", ArtifactTimeUTC: "2026-02-24T00:00:00Z",
			StatePointUTC:    "2026-02-24T00:05:00Z",
			AttemptedThrough: "2026-02-24T00:05:00Z", DataThrough: "2026-02-24T00:05:00Z",
			SourcePartitionContentSHA: "partition-1", SourceRouteEventFileSHA: "events-1",
			RouteEventCount: 2, AnnounceCount: 1, WithdrawCount: 1,
			TransitionSHA256: "transition-1", RouteStateRecordCount: 2,
			VisibleRouteCount: 1, StateDigest: "state-1", QualityStatus: "complete",
		},
		{
			SchemaVersion: RouteStateSlotLedgerVersion, Slot: 2, ArtifactIndex: 2,
			ArtifactID: "artifact-2", ArtifactTimeUTC: "2026-02-24T00:05:00Z",
			StatePointUTC:    "2026-02-24T00:10:00Z",
			AttemptedThrough: "2026-02-24T00:10:00Z", DataThrough: "2026-02-24T00:10:00Z",
			SourcePartitionContentSHA: "partition-2", SourceRouteEventFileSHA: "events-2",
			RouteEventCount: 1, AnnounceCount: 1, TransitionSHA256: "transition-2",
			RouteStateRecordCount: 2, VisibleRouteCount: 2,
			StateDigest: "state-2", QualityStatus: "complete",
		},
	}
	for index := range records {
		records[index].ContentSHA256 = routeStateSlotContentSHA(records[index])
	}
	output := t.TempDir()
	manifest, err := WriteRouteStateSlotLedger(output, "dataset", records)
	if err != nil {
		t.Fatal(err)
	}
	loaded, loadedManifest, err := LoadRouteStateSlotLedger(output, "dataset", 1, 2)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(records, loaded) || manifest != loadedManifest {
		t.Fatal("RouteState slot ledger did not round trip")
	}
	changed := append([]RouteStateSlotRecord(nil), loaded...)
	changed[1].StateDigest = "different"
	changed[1].ContentSHA256 = routeStateSlotContentSHA(changed[1])
	if err := CompareRouteStateSlotLedgers(loaded, changed); err == nil {
		t.Fatal("different RouteState slot ledger was accepted")
	}
	if _, err := filepath.Abs(manifest.File.Path); err != nil {
		t.Fatal(err)
	}
}

func TestRouteStateSlotLedgerRejectsInteriorGap(t *testing.T) {
	records := []RouteStateSlotRecord{
		{SchemaVersion: RouteStateSlotLedgerVersion, Slot: 1, ArtifactIndex: 1, QualityStatus: "complete"},
		{SchemaVersion: RouteStateSlotLedgerVersion, Slot: 3, ArtifactIndex: 3, QualityStatus: "complete"},
	}
	for index := range records {
		records[index].ContentSHA256 = routeStateSlotContentSHA(records[index])
	}
	if _, err := WriteRouteStateSlotLedger(t.TempDir(), "dataset", records); err == nil {
		t.Fatal("RouteState slot ledger with an interior gap was accepted")
	}
}

func TestRouteStateSeedProjectsWithoutASecondEventFact(t *testing.T) {
	rawRoot := t.TempDir()
	output := t.TempDir()
	rib, _, _, _ := buildSyntheticRouteEventPartitions(t, rawRoot, output)
	state, _ := NewRouteState(1)
	parsed, err := projectRouteStateSeed(output, rib, state)
	if err != nil {
		t.Fatal(err)
	}
	if len(parsed.Events) != 0 || parsed.RouteEventCount != 1 ||
		len(state.Routes) != 1 || state.ProcessedEventCount != 1 {
		t.Fatalf("Seed RIB was not streamed into the single RouteState authority: %+v %+v", parsed, state)
	}
}
