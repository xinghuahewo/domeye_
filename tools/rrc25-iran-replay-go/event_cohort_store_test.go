package replay

import (
	"bufio"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/netip"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestEventCohortSlotUsesAlignedPreDetectionStatePoint(t *testing.T) {
	tests := map[string]int{
		"2026-02-24T00:00:00Z": 0,
		"2026-02-27T01:10:00Z": 3*288 + 14,
		"2026-03-10T23:55:00Z": RouteStateFinalSlot - 1,
	}
	for value, expected := range tests {
		slot, err := eventCohortSlot(value)
		if err != nil || slot != expected {
			t.Fatalf("slot(%s)=%d err=%v, want %d", value, slot, err, expected)
		}
	}
	for _, invalid := range []string{
		"2026-02-23T23:55:00Z", "2026-02-24T00:01:00Z", "2026-03-11T00:00:00Z",
	} {
		if _, err := eventCohortSlot(invalid); err == nil {
			t.Fatalf("expected invalid state point rejection: %s", invalid)
		}
	}
}

func TestEventCohortTargetsShareOnlySameCountryAndStatePoint(t *testing.T) {
	mapping := newSyntheticGlobalCountryMapping(map[uint32]string{1: "IR", 2: "MW"})
	snapshot := EventLifecycleSnapshot{Events: []EventLifecycle{
		{LegacyReference: "ir-1", CountryCode: "IR", CohortStatePointUTC: "2026-02-27T01:10:00Z"},
		{LegacyReference: "ir-2", CountryCode: "IR", CohortStatePointUTC: "2026-02-27T01:10:00Z"},
		{LegacyReference: "mw-1", CountryCode: "MW", CohortStatePointUTC: "2026-02-27T01:10:00Z"},
	}}
	targets, references, err := buildEventCohortTargets(snapshot, mapping, "dataset-v1")
	if err != nil {
		t.Fatal(err)
	}
	if len(targets) != 2 || len(targets[0].Events)+len(targets[1].Events) != 3 {
		t.Fatalf("unexpected target population: %+v", targets)
	}
	if references["ir-1"] != references["ir-2"] || references["mw-1"] == references["ir-1"] {
		t.Fatalf("event target sharing is incorrect: %+v", references)
	}
}

func syntheticCohortEvent(
	peerIP string,
	peerASN uint32,
	prefix string,
	origin uint32,
	action uint8,
	ordinal uint32,
) routeStateEvent {
	path := sha256.Sum256([]byte("path-" + peerIP))
	eventID := sha256.Sum256([]byte("event-" + peerIP + prefix))
	return routeStateEvent{
		Key: RouteStateKey{
			Collector: RouteStateCollectorRRC25,
			Route: RouteKey{
				PeerIP: netip.MustParseAddr(peerIP), PeerASN: peerASN,
				AFI: 4, Prefix: netip.MustParsePrefix(prefix),
			},
		},
		Action: action, OriginKnown: action != actionWithdraw, OriginASN: origin,
		ASPathKnown: action != actionWithdraw, ASPathDigest: path,
		RouteEventID: [16]byte(eventID[:16]), ArtifactIndex: 1,
		RecordOrdinal: ordinal, EventMicros: time.Date(2026, 2, 24, 0, 1, 0, 0, time.UTC).UnixMicro(),
	}
}

func decodeSingleCohortMember(t *testing.T, path string) EventCohortMember {
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
	scanner := bufio.NewScanner(decoded)
	if !scanner.Scan() {
		t.Fatalf("missing member row: %v", scanner.Err())
	}
	var member EventCohortMember
	if err := json.Unmarshal(scanner.Bytes(), &member); err != nil {
		t.Fatal(err)
	}
	if scanner.Scan() || scanner.Err() != nil {
		t.Fatalf("expected exactly one member row: %v", scanner.Err())
	}
	return member
}

func TestEventCohortGroupsMultipleSessionsIntoPeerASNDirection(t *testing.T) {
	mapping := newSyntheticGlobalCountryMapping(map[uint32]string{64510: "IR"})
	state, err := NewRouteState(3)
	if err != nil {
		t.Fatal(err)
	}
	events := []routeStateEvent{
		syntheticCohortEvent("192.0.2.1", 64500, "203.0.113.0/24", 64510, actionAnnounce, 1),
		syntheticCohortEvent("192.0.2.2", 64500, "203.0.113.0/24", 64510, actionAnnounce, 2),
		syntheticCohortEvent("192.0.2.3", 64501, "203.0.113.0/24", 64510, actionAnnounce, 3),
	}
	for _, event := range events {
		if err := state.Apply(event); err != nil {
			t.Fatal(err)
		}
	}
	wanted := map[uint16]struct{}{mapping.CountryID(64510): {}}
	index := newEventCountryRouteIndex(state, mapping, wanted)
	withdrew := syntheticCohortEvent("192.0.2.1", 64500, "203.0.113.0/24", 0, actionWithdraw, 4)
	if err := applyEventCohortRouteState(state, index, mapping, withdrew); err != nil {
		t.Fatal(err)
	}
	withdrew.OriginASN = 64510
	withdrew.Key.Route.PeerIP = netip.MustParseAddr("192.0.2.2")
	withdrew.RecordOrdinal = 5
	if err := applyEventCohortRouteState(state, index, mapping, withdrew); err != nil {
		t.Fatal(err)
	}

	root := t.TempDir()
	target := eventCohortTarget{
		CountryCode: "IR", CountryID: mapping.CountryID(64510),
		StatePoint: RouteEventWindowStartUTC, StateSlot: 0,
	}
	manifest, err := writeEventCohort(
		EventCohortStoreConfig{Output: root}, target, state, index[target.CountryID],
		RouteStateStoreManifest{DatasetID: "route-state-v1"}, strings.Repeat("a", 64),
		RouteStateCheckpointManifest{CheckpointID: "checkpoint-v1", ProcessedSlot: 0},
	)
	if err != nil {
		t.Fatal(err)
	}
	if manifest.MemberCount != 1 || manifest.ExpectedDirectionRelationCount != 1 ||
		manifest.RouteObservationCount != 1 {
		t.Fatalf("direction/session aggregation mismatch: %+v", manifest)
	}
	member := decodeSingleCohortMember(
		t, filepath.Join(root, filepath.FromSlash(manifest.Members.Path)),
	)
	if member.ExpectedDirectionCount != 1 || member.ExpectedDirections[0].PeerASN != 64501 ||
		member.ExpectedDirections[0].RouteObservationCount != 1 ||
		member.OriginASNs[0] != 64510 {
		t.Fatalf("unexpected cohort member: %+v", member)
	}
	if member.ExpectedDirections[0].RouteObservations[0].ASPathStatus != "known" ||
		!strings.HasPrefix(member.ExpectedDirections[0].RouteObservations[0].ASPathID, "asp_v1_") {
		t.Fatalf("AS_PATH reference was not preserved: %+v", member)
	}
}

func TestLifecycleSnapshotHashMatchesCanonicalNestedObjects(t *testing.T) {
	event := map[string]any{
		"incident_id": "incident-ir", "legacy_reference": "ir-1", "country_code": "IR",
		"source_code": "r", "collector_id": "rrc25", "detected_at_utc": "2026-02-27T01:12:32Z",
		"cohort_state_point_utc":           "2026-02-27T01:10:00Z",
		"window_start_utc":                 "2026-02-27T00:10:00Z",
		"requested_window_start_utc":       "2026-02-27T00:10:00Z",
		"left_boundary_missing_slot_count": 0, "event_end_at_utc": nil,
		"event_duration_seconds": nil, "projection_end_state_point_utc": RouteEventWindowEndUTC,
		"lifecycle_state": "event_end_unknown", "is_final_in_data_range": false,
		"lifecycle_source": "legacy_country_outage_event_fact",
	}
	payload := map[string]any{
		"schema_version": EventLifecycleVersion, "status": "complete", "collector_id": "rrc25",
		"window_start_utc": RouteEventWindowStartUTC, "window_end_exclusive_utc": RouteEventWindowEndUTC,
		"interval_seconds": 300, "event_count": 1, "incident_input_sha256": strings.Repeat("a", 64),
		"detail_source_semantics": "existing_read_only_legacy_event_fact",
		"window_semantics":        "twelve_complete_slots_before_detection_then_lifecycle_or_range_cap",
		"lifecycle_state_counts":  map[string]any{"event_end_unknown": 1},
		"events":                  []any{event},
	}
	canonical, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(canonical)
	contentSHA := hex.EncodeToString(digest[:])
	payload["content_sha256"] = contentSHA
	payload["snapshot_id"] = "event_lifecycle_snapshot_v1_" + contentSHA[:32]
	raw, err := json.MarshalIndent(payload, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "snapshot.json")
	if err := os.WriteFile(path, append(raw, '\n'), 0o600); err != nil {
		t.Fatal(err)
	}
	loaded, _, err := loadEventLifecycleSnapshot(path)
	if err != nil {
		t.Fatal(err)
	}
	if loaded.ContentSHA256 != contentSHA || loaded.EventCount != 1 {
		t.Fatalf("unexpected lifecycle snapshot: %+v", loaded)
	}
}
