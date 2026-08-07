package replay

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func syntheticRouteStateIdentity() RouteStateCheckpointIdentity {
	return RouteStateCheckpointIdentity{
		RouteStateDatasetID:        "route_state_dataset_test",
		SourceRouteEventDatasetID:  "route_event_dataset_test",
		SourceRouteEventContentSHA: strings.Repeat("a", 64),
		ImplementationID:           "git:" + strings.Repeat("b", 40),
		ProjectorName:              "domeye_route_state_projector",
		ProjectorVersion:           "1.0.0",
		MappingVersion:             strings.Repeat("c", 64),
		MappingCompatibleSHA256:    strings.Repeat("d", 64),
		MappingRevisedSHA256:       strings.Repeat("e", 64),
		WindowStartUTC:             RouteEventWindowStartUTC,
		WindowEndExclusiveUTC:      RouteEventWindowEndUTC,
	}
}

func syntheticRouteStateEvent(
	peer, prefix string,
	action uint8,
	artifact uint16,
	record uint32,
) routeStateEvent {
	key := globalRouteKey(peer, 64500, prefix)
	var eventID [16]byte
	eventID[0] = byte(record + 1)
	var path [32]byte
	path[0] = 1
	var attribute [32]byte
	attribute[0] = 2
	return routeStateEvent{
		Key:    RouteStateKey{Collector: RouteStateCollectorRRC25, Route: key},
		Action: action, OriginKnown: action != actionWithdraw, OriginASN: 64510,
		ASPathKnown: action != actionWithdraw, ASPathDigest: path,
		AttributeKnown: true, AttributeDigest: attribute, RouteEventID: eventID,
		ArtifactIndex: artifact, RecordOrdinal: record,
		EventMicros: int64(artifact)*300_000_000 + int64(record),
	}
}

func TestRouteStateIsSinglePrefixVPAuthority(t *testing.T) {
	state, err := NewRouteState(4)
	if err != nil {
		t.Fatal(err)
	}
	announce := syntheticRouteStateEvent(
		"192.0.2.1", "203.0.113.0/24", actionRIBSnapshot, 0, 1,
	)
	if err := state.Apply(announce); err != nil {
		t.Fatal(err)
	}
	if len(state.Routes) != 1 || state.VisibleRouteCount != 1 {
		t.Fatalf("unexpected seed state: %+v", state)
	}
	withdraw := syntheticRouteStateEvent(
		"192.0.2.1", "203.0.113.0/24", actionWithdraw, 1, 2,
	)
	if err := state.Apply(withdraw); err != nil {
		t.Fatal(err)
	}
	value := state.Routes[announce.Key]
	if value.Visible || value.OriginKnown || value.ASPathKnown ||
		!value.AttributeKnown || value.LastArtifactIndex != 1 {
		t.Fatalf("withdraw did not update the same RouteState fact: %+v", value)
	}
	orphan := syntheticRouteStateEvent(
		"192.0.2.2", "198.51.100.0/24", actionWithdraw, 1, 3,
	)
	if err := state.Apply(orphan); err != nil {
		t.Fatal(err)
	}
	if state.Routes[orphan.Key].QualityStatus != routeStateQualityOrphanWithdraw ||
		state.VisibleRouteCount != 0 || len(state.Routes) != 2 {
		t.Fatal("orphan withdraw quality was not retained in RouteState")
	}
}

func TestRouteStateCheckpointIsDeterministicSnapshotOfSameState(t *testing.T) {
	state, _ := NewRouteState(4)
	for _, event := range []routeStateEvent{
		syntheticRouteStateEvent("192.0.2.1", "203.0.113.0/24", actionRIBSnapshot, 0, 1),
		syntheticRouteStateEvent("2001:db8::1", "2001:db8:1::/48", actionRIBSnapshot, 0, 2),
		syntheticRouteStateEvent("192.0.2.1", "203.0.113.0/24", actionWithdraw, 1, 3),
	} {
		if err := state.Apply(event); err != nil {
			t.Fatal(err)
		}
	}
	identity := syntheticRouteStateIdentity()
	first := filepath.Join(t.TempDir(), "checkpoint")
	second := filepath.Join(t.TempDir(), "checkpoint")
	firstManifest, err := WriteRouteStateCheckpoint(
		first, state, identity, "2026-02-24T00:05:00Z", 1, 3,
	)
	if err != nil {
		t.Fatal(err)
	}
	secondManifest, err := WriteRouteStateCheckpoint(
		second, state, identity, "2026-02-24T00:05:00Z", 1, 3,
	)
	if err != nil {
		t.Fatal(err)
	}
	firstComplete, _ := os.ReadFile(filepath.Join(first, "COMPLETE.json"))
	firstPublished, _ := os.ReadFile(filepath.Join(first, "manifest.json"))
	secondComplete, _ := os.ReadFile(filepath.Join(second, "COMPLETE.json"))
	if string(firstComplete) != string(firstPublished) ||
		string(firstComplete) != string(secondComplete) ||
		!reflect.DeepEqual(firstManifest.Shards, secondManifest.Shards) ||
		firstManifest.ContentSHA256 != secondManifest.ContentSHA256 {
		t.Fatal("same RouteState did not produce a deterministic checkpoint")
	}
	restored, loaded, err := LoadRouteStateCheckpoint(first, identity)
	if err != nil {
		t.Fatal(err)
	}
	if loaded.StateDigest != state.StateDigest.Hex() ||
		!reflect.DeepEqual(state.Routes, restored.Routes) ||
		state.VisibleRouteCount != restored.VisibleRouteCount ||
		state.ProcessedEventCount != restored.ProcessedEventCount {
		t.Fatal("checkpoint did not restore the same RouteState")
	}
}

func TestRouteStateCheckpointRejectsCorruptionAndVersionConflict(t *testing.T) {
	state, _ := NewRouteState(1)
	if err := state.Apply(syntheticRouteStateEvent(
		"192.0.2.1", "203.0.113.0/24", actionRIBSnapshot, 0, 1,
	)); err != nil {
		t.Fatal(err)
	}
	identity := syntheticRouteStateIdentity()
	directory := filepath.Join(t.TempDir(), "checkpoint")
	manifest, err := WriteRouteStateCheckpoint(
		directory, state, identity, RouteEventWindowStartUTC, 0, 1,
	)
	if err != nil {
		t.Fatal(err)
	}
	shard := filepath.Join(directory, manifest.Shards[0].Path)
	file, err := os.OpenFile(shard, os.O_WRONLY|os.O_APPEND, 0)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := file.Write([]byte{0}); err != nil {
		t.Fatal(err)
	}
	_ = file.Close()
	if _, _, err := LoadRouteStateCheckpoint(directory, identity); err == nil {
		t.Fatal("corrupt RouteState checkpoint was accepted")
	}

	clean := filepath.Join(t.TempDir(), "checkpoint")
	if _, err := WriteRouteStateCheckpoint(
		clean, state, identity, RouteEventWindowStartUTC, 0, 1,
	); err != nil {
		t.Fatal(err)
	}
	wrong := identity
	wrong.ProjectorVersion = "2.0.0"
	if _, _, err := LoadRouteStateCheckpoint(clean, wrong); err == nil {
		t.Fatal("incompatible RouteState checkpoint was accepted")
	}
}
