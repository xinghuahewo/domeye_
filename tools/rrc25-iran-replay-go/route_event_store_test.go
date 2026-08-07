package replay

import (
	"bufio"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/netip"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
)

func readRawRecordRows(t *testing.T, path string) []rawMRTRecordRow {
	t.Helper()
	file, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	decoded, err := gzip.NewReader(file)
	if err != nil {
		t.Fatal(err)
	}
	defer decoded.Close()
	rows := make([]rawMRTRecordRow, 0)
	scanner := bufio.NewScanner(decoded)
	for scanner.Scan() {
		var row rawMRTRecordRow
		if err := json.Unmarshal(scanner.Bytes(), &row); err != nil {
			t.Fatal(err)
		}
		rows = append(rows, row)
	}
	if err := scanner.Err(); err != nil {
		t.Fatal(err)
	}
	return rows
}

func digestFileForTest(t *testing.T, path string) string {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(raw)
	return hex.EncodeToString(digest[:])
}

func buildSyntheticRouteEventPartitions(
	t *testing.T,
	rawRoot string,
	output string,
) (RouteEventPartitionManifest, RouteEventPartitionManifest, Artifact, Artifact) {
	t.Helper()
	if err := os.MkdirAll(filepath.Join(output, "partitions"), 0o750); err != nil {
		t.Fatal(err)
	}
	ribStream := append(
		peerIndexFixture(),
		ribFixture(mustPrefix("203.0.113.0/24"), 4_200_000_001)...,
	)
	rib := writeGzipArtifact(
		t, rawRoot, "rrc25/test-rib.gz", ribStream, CatchUpStartUTC, "rib",
	)
	slot := mustUTC("2026-02-28T09:25:00Z")
	updateStream := updateFixture(slot.Add(time.Second), 4, 4_200_000_001, nil)
	update := writeGzipArtifact(
		t, rawRoot, "rrc25/test-update.gz", updateStream,
		slot.Format(time.RFC3339), "update",
	)
	ribManifest, err := buildRouteEventPartition(
		rawRoot, output, 0, rib, "rib", "run", "dataset", false,
	)
	if err != nil {
		t.Fatal(err)
	}
	updateManifest, err := buildRouteEventPartition(
		rawRoot, output, 1, update, "update", "run", "dataset", false,
	)
	if err != nil {
		t.Fatal(err)
	}
	return ribManifest, updateManifest, rib, update
}

func mustPrefix(value string) netip.Prefix {
	return netip.MustParsePrefix(value)
}

func TestRouteEventPartitionIsTraceableImmutableAndDeterministic(t *testing.T) {
	rawRoot := t.TempDir()
	firstOutput := t.TempDir()
	secondOutput := t.TempDir()
	firstRIB, firstUpdate, rib, update := buildSyntheticRouteEventPartitions(
		t, rawRoot, firstOutput,
	)
	ribPath := filepath.Join(rawRoot, filepath.FromSlash(rib.RelativePath))
	updatePath := filepath.Join(rawRoot, filepath.FromSlash(update.RelativePath))
	ribInputDigest := digestFileForTest(t, ribPath)
	updateInputDigest := digestFileForTest(t, updatePath)
	secondRIB, secondUpdate, _, _ := buildSyntheticRouteEventPartitions(
		t, rawRoot, secondOutput,
	)
	if firstRIB.ContentSHA256 != secondRIB.ContentSHA256 ||
		firstUpdate.ContentSHA256 != secondUpdate.ContentSHA256 {
		t.Fatal("same source did not produce deterministic partition identities")
	}
	if digestFileForTest(t, ribPath) != ribInputDigest ||
		digestFileForTest(t, updatePath) != updateInputDigest {
		t.Fatal("source MRT artifacts changed while building RouteEvent partitions")
	}
	if firstRIB.PhysicalRecords != 2 || firstRIB.RouteEvents != 1 ||
		firstRIB.RIBSnapshots != 1 || firstRIB.Announces != 0 ||
		firstRIB.Withdraws != 0 || firstRIB.PathCount != 1 ||
		firstRIB.IngestTimeUTC == "" || firstRIB.ParseTimeUTC == "" {
		t.Fatalf("unexpected RIB population: %+v", firstRIB)
	}
	if firstUpdate.PhysicalRecords != 1 || firstUpdate.RouteEvents != 1 ||
		firstUpdate.Announces != 1 || firstUpdate.PathCount != 1 {
		t.Fatalf("unexpected UPDATE population: %+v", firstUpdate)
	}
	ribRows, err := ReadRouteEventRows(filepath.Join(
		firstOutput, filepath.FromSlash(firstRIB.Events.Path),
	))
	if err != nil {
		t.Fatal(err)
	}
	updateRows, err := ReadRouteEventRows(filepath.Join(
		firstOutput, filepath.FromSlash(firstUpdate.Events.Path),
	))
	if err != nil {
		t.Fatal(err)
	}
	if len(ribRows) != 1 || ribRows[0].Action != "rib_snapshot" ||
		ribRows[0].ASPathID == nil || ribRows[0].OriginASN == nil ||
		*ribRows[0].OriginASN != 4_200_000_001 ||
		ribRows[0].RecordOrdinal != 1 || ribRows[0].ElementOrdinal != 0 {
		t.Fatalf("RIB RouteEvent lost complete fields: %+v", ribRows)
	}
	if len(updateRows) != 1 || updateRows[0].Action != "announce" ||
		updateRows[0].ASPathID == nil || updateRows[0].AttributeSHA256 == nil ||
		!strings.HasPrefix(updateRows[0].RouteEventID, "rte_v1_") ||
		updateRows[0].VPID != VPIdentifier(
			mustAddress("192.0.2.10"), 64500,
		) {
		t.Fatalf("UPDATE RouteEvent lost traceable fields: %+v", updateRows)
	}
	recordRows := readRawRecordRows(t, filepath.Join(
		firstOutput, filepath.FromSlash(firstRIB.Records.Path),
	))
	if len(recordRows) != 2 || recordRows[0].UncompressedOffset != 0 ||
		recordRows[1].UncompressedOffset != int64(len(peerIndexFixture())) ||
		recordRows[1].RecordLength != uint32(len(ribFixture(
			mustPrefix("203.0.113.0/24"), 4_200_000_001,
		))) {
		t.Fatalf("raw record sidecar lost stable coordinates: %+v", recordRows)
	}
	resumed, err := buildRouteEventPartition(
		rawRoot, firstOutput, 1, update, "update", "run", "dataset", true,
	)
	if err != nil || resumed.ContentSHA256 != firstUpdate.ContentSHA256 {
		t.Fatalf("verified partition resume failed: %+v %v", resumed, err)
	}
}

func TestRouteEventPartitionContentIdentityBindsSemanticManifest(t *testing.T) {
	manifest := RouteEventPartitionManifest{
		SchemaVersion: RouteEventPartitionVersion,
		ImportRunID:   "run", DatasetID: "dataset", ArtifactIndex: 1,
		Artifact: Artifact{ArtifactID: "artifact"}, Role: "update",
		InputIntegrityStatus: RouteEventInputIntegrity,
		IngestTimeUTC:        "2026-08-06T00:00:00Z",
		ParseTimeUTC:         "2026-08-06T00:00:01Z",
		PhysicalRecords:      10, RouteEvents: 20, Announces: 15, Withdraws: 5,
		PathCount: 3, ParserWarnings: 1,
		Records: RouteEventStoreFile{Path: "records", RowCount: 10, SHA256: "a"},
		Events:  RouteEventStoreFile{Path: "events", RowCount: 20, SHA256: "b"},
		Paths:   RouteEventStoreFile{Path: "paths", RowCount: 3, SHA256: "c"},
	}
	base := routeEventPartitionContentSHA(manifest)
	operationTimeChanged := manifest
	operationTimeChanged.IngestTimeUTC = "2026-08-07T00:00:00Z"
	operationTimeChanged.ParseTimeUTC = "2026-08-07T00:00:01Z"
	if routeEventPartitionContentSHA(operationTimeChanged) != base {
		t.Fatal("operation time changed semantic partition identity")
	}
	for name, mutate := range map[string]func(*RouteEventPartitionManifest){
		"role": func(value *RouteEventPartitionManifest) { value.Role = "rib" },
		"input integrity": func(value *RouteEventPartitionManifest) {
			value.InputIntegrityStatus = "unchecked"
		},
		"population":      func(value *RouteEventPartitionManifest) { value.RouteEvents++ },
		"parser warnings": func(value *RouteEventPartitionManifest) { value.ParserWarnings++ },
	} {
		t.Run(name, func(t *testing.T) {
			changed := manifest
			mutate(&changed)
			if routeEventPartitionContentSHA(changed) == base {
				t.Fatal("semantic manifest drift did not change partition identity")
			}
		})
	}
}

func TestRouteEventStoreContentIdentityBindsPopulation(t *testing.T) {
	manifest := RouteEventStoreManifest{
		SchemaVersion: RouteEventStoreVersion,
		Status:        "complete", ImportRunID: "run", DatasetID: "dataset",
		CollectorID: "rrc25", Source: "ripe_ris",
		WindowStartUTC:     RouteEventWindowStartUTC,
		WindowEndExclusive: RouteEventWindowEndUTC,
		SelectionSHA256:    "selection", SelectionPath: "input-selection.json",
		SourceManifestSHA: "source", RepairArtifactCount: 2,
		RepairProvenanceSHA: "repair", ImplementationID: "git:" + strings.Repeat("1", 40),
		ParserName: RouteEventParserName, ParserVersion: RouteEventParserVersion,
		ImporterName: RouteEventImporterName, ImporterVersion: RouteEventImporterVersion,
		ArtifactCount: 1, PhysicalRecords: 10, RouteEvents: 20,
		Announces: 15, Withdraws: 5,
		Partitions: []RouteEventPartitionManifest{{ArtifactIndex: 0, ContentSHA256: "partition"}},
	}
	base := routeEventStoreContentSHA(manifest)
	manifest.RouteEvents++
	if routeEventStoreContentSHA(manifest) == base {
		t.Fatal("aggregate population drift did not change store identity")
	}
}

func TestRouteEventStoreRequiresIdenticalCompleteAndManifest(t *testing.T) {
	output := t.TempDir()
	manifest := RouteEventStoreManifest{
		SchemaVersion: RouteEventStoreVersion,
		Status:        "complete",
		DatasetID:     "dataset",
	}
	if _, err := writeJSONImmutable(filepath.Join(output, "COMPLETE.json"), manifest); err != nil {
		t.Fatal(err)
	}
	if _, err := writeJSONImmutable(filepath.Join(output, "manifest.json"), manifest); err != nil {
		t.Fatal(err)
	}
	if _, err := readIdenticalRouteEventStoreManifests(output); err != nil {
		t.Fatalf("identical global manifests were rejected: %v", err)
	}
	if err := os.WriteFile(
		filepath.Join(output, "manifest.json"), []byte("{}\n"), 0o640,
	); err != nil {
		t.Fatal(err)
	}
	if _, err := readIdenticalRouteEventStoreManifests(output); err == nil ||
		!strings.Contains(err.Error(), "not byte-identical") {
		t.Fatalf("drifted global manifest was accepted: %v", err)
	}
}

func TestRouteEventAuditSelectionIsDeterministicAndIncludesRepairs(t *testing.T) {
	first, err := routeEventAuditPartitionIndexes("dataset", RouteEventStoreAuditSamples)
	if err != nil {
		t.Fatal(err)
	}
	second, err := routeEventAuditPartitionIndexes("dataset", RouteEventStoreAuditSamples)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(first, second) || len(first) != RouteEventStoreAuditSamples {
		t.Fatal("audit partition selection is not deterministic")
	}
	seen := make(map[int]struct{}, len(first))
	for _, index := range first {
		if index < 1 || index > RouteEventUpdateCount {
			t.Fatalf("audit partition index is out of range: %d", index)
		}
		if _, found := seen[index]; found {
			t.Fatalf("duplicate audit partition index: %d", index)
		}
		seen[index] = struct{}{}
	}
	for _, required := range []int{1, firstRepairPartitionIndex, secondRepairPartitionIndex} {
		if _, found := seen[required]; !found {
			t.Fatalf("required audit partition %d is absent", required)
		}
	}
}

func TestRouteEventAuditReplaysSyntheticRawElement(t *testing.T) {
	rawRoot := t.TempDir()
	output := t.TempDir()
	_, updateManifest, _, update := buildSyntheticRouteEventPartitions(t, rawRoot, output)
	candidate, err := selectAuditedRouteEvent(
		filepath.Join(output, filepath.FromSlash(updateManifest.Events.Path)),
		"dataset", 1, "announce",
	)
	if err != nil {
		t.Fatal(err)
	}
	rawRow, err := readAuditedRawRecordRow(
		filepath.Join(output, filepath.FromSlash(updateManifest.Records.Path)),
		candidate.RecordOrdinal,
	)
	if err != nil {
		t.Fatal(err)
	}
	replayed, record, err := replayAuditedUpdateElement(
		rawRoot, update, 1, candidate.RecordOrdinal, candidate.ElementOrdinal,
	)
	if err != nil {
		t.Fatal(err)
	}
	if rawRow.RecordSHA256 != record.RecordSHA256 ||
		rawRow.UncompressedOffset != record.UncompressedOffset ||
		rawRow.RecordLength != record.RecordLength {
		t.Fatal("audit replay did not close the raw record sidecar")
	}
	expected, err := auditedRouteEventRow(update, replayed)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(candidate, expected) {
		t.Fatalf("audit replay did not reproduce RouteEvent: %+v %+v", candidate, expected)
	}
	if candidate.ASPathID == nil {
		t.Fatal("synthetic announcement has no AS_PATH")
	}
	candidatePath, err := readAuditedASPath(
		filepath.Join(output, filepath.FromSlash(updateManifest.Paths.Path)),
		*candidate.ASPathID,
	)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(candidatePath.ASPath, *replayed.ASPath) {
		t.Fatal("audit replay did not reproduce AS_PATH dictionary content")
	}
}

func mustAddress(value string) netip.Addr {
	return netip.MustParseAddr(value)
}

func TestRouteEventPartitionRejectsTamperedMaterialization(t *testing.T) {
	rawRoot := t.TempDir()
	output := t.TempDir()
	_, updateManifest, _, update := buildSyntheticRouteEventPartitions(t, rawRoot, output)
	eventPath := filepath.Join(output, filepath.FromSlash(updateManifest.Events.Path))
	file, err := os.OpenFile(eventPath, os.O_APPEND|os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := file.Write([]byte("tampered")); err != nil {
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := buildRouteEventPartition(
		rawRoot, output, 1, update, "update", "run", "dataset", true,
	); err == nil || !strings.Contains(err.Error(), "identity mismatch") {
		t.Fatalf("tampered materialization was accepted: %v", err)
	}
}

func TestRouteEventStoreSelectionIsFrozenTo224310(t *testing.T) {
	selection := GlobalWindowSelection{
		CollectorID:           "rrc25",
		WindowStartUTC:        RouteEventWindowStartUTC,
		WindowEndExclusiveUTC: RouteEventWindowEndUTC,
		RepairArtifactCount:   RouteEventRepairCount,
		Updates:               make([]Artifact, RouteEventUpdateCount),
	}
	selection.RIB.FileSHA256 = strings.Repeat("f", 64)
	selection.RIB.RelativePath = "rrc25/test-rib.gz"
	for index := range selection.Updates {
		selection.Updates[index].FileSHA256 = fmt.Sprintf("%064x", index+1)
		selection.Updates[index].RelativePath = fmt.Sprintf("rrc25/test-%04d.gz", index)
	}
	for repairIndex, repair := range frozenRouteEventRepairs {
		selection.Updates[repairIndex] = Artifact{
			ArtifactType: "update",
			RelativePath: repair.RelativePath,
			FileSHA256:   repair.ReplacementSHA256,
			SizeBytes:    repair.ReplacementSize,
		}
	}
	if err := validateRouteEventStoreSelection(selection); err != nil {
		t.Fatal(err)
	}
	selection.Updates[0].FileSHA256 = strings.Repeat("0", 64)
	if err := validateRouteEventStoreSelection(selection); err == nil ||
		!strings.Contains(err.Error(), "repair replacement identity mismatch") {
		t.Fatalf("drifted repair replacement was accepted: %v", err)
	}
	selection.Updates[0].FileSHA256 = frozenRouteEventRepairs[0].ReplacementSHA256
	selection.Updates[10].FileSHA256 = selection.Updates[11].FileSHA256
	if err := validateRouteEventStoreSelection(selection); err == nil ||
		!strings.Contains(err.Error(), "duplicate source file SHA-256") {
		t.Fatalf("duplicate source artifact content was accepted: %v", err)
	}
	selection.Updates[10].FileSHA256 = fmt.Sprintf("%064x", 11)
	selection.WindowEndExclusiveUTC = "2026-03-10T00:00:00Z"
	if err := validateRouteEventStoreSelection(selection); err == nil {
		t.Fatal("drifted RouteEvent window was accepted")
	}
}

func TestWriteBytesImmutablePreservesExactSelection(t *testing.T) {
	selectionPath := filepath.Join(t.TempDir(), "input-selection.json")
	original := []byte("{\n  \"schema_version\": \"selection/v1\"\n}\n")
	if err := writeBytesImmutable(selectionPath, original); err != nil {
		t.Fatal(err)
	}
	if err := writeBytesImmutable(selectionPath, original); err != nil {
		t.Fatalf("same immutable selection was not reusable: %v", err)
	}
	if err := writeBytesImmutable(selectionPath, append(original, ' ')); err == nil ||
		!strings.Contains(err.Error(), "immutable file mismatch") {
		t.Fatalf("drifted immutable selection was accepted: %v", err)
	}
	actual, err := os.ReadFile(selectionPath)
	if err != nil {
		t.Fatal(err)
	}
	if string(actual) != string(original) {
		t.Fatal("immutable selection bytes were overwritten")
	}
}

func TestRouteEventStoreIdentityBindsImplementation(t *testing.T) {
	first := "git:" + strings.Repeat("1", 40)
	second := "git:" + strings.Repeat("2", 40)
	if err := validateRouteEventImplementationID(first); err != nil {
		t.Fatal(err)
	}
	if err := validateRouteEventImplementationID("git:" + strings.Repeat("A", 40)); err == nil {
		t.Fatal("uppercase implementation identity was accepted")
	}
	firstRun, firstDataset := routeEventStoreIdentity("selection", "repair", first)
	secondRun, secondDataset := routeEventStoreIdentity("selection", "repair", second)
	if firstRun == secondRun || firstDataset == secondDataset {
		t.Fatal("different implementation identities produced the same store identity")
	}
}

func TestRouteEventStoreRejectsConcurrentWriter(t *testing.T) {
	output := t.TempDir()
	first, err := acquireRouteEventStoreLock(output)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := acquireRouteEventStoreLock(output); err == nil ||
		!strings.Contains(err.Error(), "active writer") {
		t.Fatalf("concurrent writer was accepted: %v", err)
	}
	if err := first.Close(); err != nil {
		t.Fatal(err)
	}
	second, err := acquireRouteEventStoreLock(output)
	if err != nil {
		t.Fatalf("released writer lock was not reusable: %v", err)
	}
	if err := second.Close(); err != nil {
		t.Fatal(err)
	}
}

func TestASPathDictionaryIdentityDoesNotDependOnEventWarning(t *testing.T) {
	path := newASPathSnapshot([]ASPathSegment{{
		SegmentType: asSequenceSegment,
		ASNs:        []uint32{64500, 64496},
	}})
	builder := routeEventPartitionBuilder{paths: make(map[string]asPathRow)}
	firstID, firstFlags, err := builder.addPath(path, nil)
	if err != nil {
		t.Fatal(err)
	}
	secondID, secondFlags, err := builder.addPath(
		path, []string{"as4_path_in_four_byte_message_discarded"},
	)
	if err != nil {
		t.Fatal(err)
	}
	if *firstID != *secondID || len(builder.paths) != 1 {
		t.Fatal("event warning changed AS_PATH content identity")
	}
	if len(firstFlags) != 0 || len(secondFlags) != 1 ||
		secondFlags[0] != "parser_warning" {
		t.Fatalf("event warning flags were not isolated: %v %v", firstFlags, secondFlags)
	}
}
