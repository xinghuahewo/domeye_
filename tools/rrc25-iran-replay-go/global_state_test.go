package replay

import (
	"path/filepath"
	"testing"
)

func findGlobalObservation(
	t *testing.T,
	rows []GlobalCountryObservation,
	code string,
) GlobalCountryObservation {
	t.Helper()
	for _, row := range rows {
		if row.CountryCode == code {
			return row
		}
	}
	t.Fatalf("country %s observation not found", code)
	return GlobalCountryObservation{}
}

func TestGlobalStateCountryProjectionMigrationAndWithdraw(t *testing.T) {
	mapping := newSyntheticGlobalCountryMapping(map[uint32]string{
		64500: "IR",
		64501: "US",
	})
	state, err := NewGlobalReplayState(mapping, 4)
	if err != nil {
		t.Fatal(err)
	}
	iranKey := globalRouteKey("192.0.2.1", 64496, "203.0.113.0/24")
	usKey := globalRouteKey("2001:db8::1", 64497, "2001:db8:1::/48")
	unknownKey := globalRouteKey("192.0.2.2", 64498, "198.51.100.0/24")
	if err := state.Seed(iranKey, true, 64500, 10, 0); err != nil {
		t.Fatal(err)
	}
	if err := state.Seed(usKey, true, 64501, 11, 0); err != nil {
		t.Fatal(err)
	}
	if err := state.Seed(unknownKey, true, 65000, 12, 0); err != nil {
		t.Fatal(err)
	}
	rows, _, conservation, err := state.SnapshotAll(
		WindowStartUTC, WindowStartUTC, WindowStartUTC,
		"window_start", NewGlobalSlotActivity(),
	)
	if err != nil {
		t.Fatal(err)
	}
	if conservation.GlobalBaselinePrefixVP != 3 ||
		conservation.CountryBaselineSum != 3 ||
		conservation.UnknownBaselinePrefixVP != 1 {
		t.Fatalf("unexpected seed conservation: %+v", conservation)
	}
	iran := findGlobalObservation(t, rows, "IR")
	if iran.BaselinePrefixVPCount != 1 || iran.VisiblePrefixVPCount != 1 ||
		iran.BaselineASNCount != 1 || !ratioEqual(iran.VisiblePrefixVPRatio, 1) {
		t.Fatalf("unexpected IR seed observation: %+v", iran)
	}

	activity := NewGlobalSlotActivity()
	if err := state.Apply(ParsedEvent{
		Key: iranKey, Action: actionAnnounce, OriginKnown: true,
		OriginASN: 64501, ArtifactIndex: 25, RecordOrdinal: 20,
		ElementOrdinal: 1,
	}, activity); err != nil {
		t.Fatal(err)
	}
	if activity.CountryMigrations != 1 {
		t.Fatalf("expected one country migration: %+v", activity)
	}
	usID, _ := mapping.IDForCode("US")
	if activity.ByCountry[usID].Announce != 1 {
		t.Fatalf("replacement announcement not attributed to US: %+v", activity)
	}
	rows, _, conservation, err = state.SnapshotAll(
		"2026-02-28T10:10:00Z", "2026-02-28T10:05:00Z",
		"2026-02-28T10:10:00Z", "slot_end", activity,
	)
	if err != nil {
		t.Fatal(err)
	}
	iran = findGlobalObservation(t, rows, "IR")
	if iran.VisiblePrefixVPCount != 0 || iran.AffectedASNCount != 1 ||
		iran.UpdateCounts.Announce != 1 ||
		iran.CountryUpdateCounts.Announce != 0 {
		t.Fatalf("IR baseline did not become unavailable: %+v", iran)
	}
	us := findGlobalObservation(t, rows, "US")
	if us.UpdateCounts.Announce != 1 ||
		us.CountryUpdateCounts.Announce != 1 {
		t.Fatalf("collector and country activity semantics diverged: %+v", us)
	}
	if conservation.GlobalCurrentPrefixVP != 3 || conservation.CountryCurrentSum != 3 {
		t.Fatalf("migration duplicated current population: %+v", conservation)
	}

	withdraw := NewGlobalSlotActivity()
	if err := state.Apply(ParsedEvent{
		Key: iranKey, Action: actionWithdraw, ArtifactIndex: 26,
	}, withdraw); err != nil {
		t.Fatal(err)
	}
	if withdraw.ByCountry[usID].Withdraw != 1 {
		t.Fatalf("withdraw did not use stored prior country: %+v", withdraw)
	}
	_, _, conservation, err = state.SnapshotAll(
		"2026-02-28T10:15:00Z", "2026-02-28T10:10:00Z",
		"2026-02-28T10:15:00Z", "slot_end", withdraw,
	)
	if err != nil {
		t.Fatal(err)
	}
	if conservation.GlobalCurrentPrefixVP != 2 {
		t.Fatalf("withdraw left a residual current route: %+v", conservation)
	}
}

func TestGlobalStateFixedCohortDynamicRouteAndDeterministicDigest(t *testing.T) {
	mapping := newSyntheticGlobalCountryMapping(map[uint32]string{64500: "IR"})
	first, _ := NewGlobalReplayState(mapping, 2)
	second, _ := NewGlobalReplayState(mapping, 2)
	keys := []RouteKey{
		globalRouteKey("192.0.2.1", 64496, "203.0.113.0/24"),
		globalRouteKey("192.0.2.2", 64497, "203.0.114.0/24"),
	}
	for index, key := range keys {
		if err := first.Seed(key, true, 64500, uint32(index), 0); err != nil {
			t.Fatal(err)
		}
	}
	for index := len(keys) - 1; index >= 0; index-- {
		if err := second.Seed(keys[index], true, 64500, uint32(index), 0); err != nil {
			t.Fatal(err)
		}
	}
	if first.StateDigest.Hex() != second.StateDigest.Hex() ||
		first.CohortID(1) != second.CohortID(1) {
		t.Fatal("state or cohort identity depends on seed insertion order")
	}
	cohortID := first.CohortID(1)
	dynamic := globalRouteKey("192.0.2.3", 64498, "198.51.100.0/24")
	activity := NewGlobalSlotActivity()
	if err := first.Apply(ParsedEvent{
		Key: dynamic, Action: actionAnnounce, OriginKnown: true, OriginASN: 64500,
	}, activity); err != nil {
		t.Fatal(err)
	}
	if first.CohortID(1) != cohortID {
		t.Fatal("dynamic route changed the fixed cohort")
	}
	conservation, err := first.ValidateConservation()
	if err != nil {
		t.Fatal(err)
	}
	if conservation.GlobalBaselinePrefixVP != 2 ||
		conservation.GlobalCurrentPrefixVP != 3 {
		t.Fatalf("dynamic population was not separated: %+v", conservation)
	}
	if err := first.Apply(ParsedEvent{Key: dynamic, Action: actionWithdraw}, NewGlobalSlotActivity()); err != nil {
		t.Fatal(err)
	}
	if _, exists := first.Routes[dynamic]; exists {
		t.Fatal("withdrawn dynamic route remained in state")
	}
}

func TestGlobalIranCohortIdentityMatchesExistingIranEngine(t *testing.T) {
	globalMapping := newSyntheticGlobalCountryMapping(map[uint32]string{
		64500: "IR",
		64501: "US",
	})
	globalMapping.MappingVersion = "shared-mapping-version"
	globalState, _ := NewGlobalReplayState(globalMapping, 3)
	first := globalRouteKey("192.0.2.1", 64496, "203.0.113.0/24")
	second := globalRouteKey("192.0.2.2", 64497, "203.0.114.0/24")
	other := globalRouteKey("192.0.2.3", 64498, "198.51.100.0/24")
	for _, key := range []RouteKey{first, second} {
		if err := globalState.Seed(key, true, 64500, 1, 0); err != nil {
			t.Fatal(err)
		}
	}
	if err := globalState.Seed(other, true, 64501, 2, 0); err != nil {
		t.Fatal(err)
	}
	iranMapping := &CountryMapping{
		byASN: map[uint32]Membership{
			64500: MembershipIR,
			64501: MembershipOther,
		},
		MappingVersion: "shared-mapping-version",
	}
	iranState, err := NewReplayState(iranMapping, map[RouteKey]uint32{
		first: 64500, second: 64500,
	})
	if err != nil {
		t.Fatal(err)
	}
	iranID, _ := globalMapping.IDForCode("IR")
	if globalState.CohortID(iranID) != iranState.CohortID {
		t.Fatalf(
			"global IR cohort %s differs from existing IR cohort %s",
			globalState.CohortID(iranID), iranState.CohortID,
		)
	}
}

func TestGlobalUnknownOriginRemainsInAddressFamilyPopulation(t *testing.T) {
	mapping := newSyntheticGlobalCountryMapping(map[uint32]string{
		64500: "IR",
	})
	state, err := NewGlobalReplayState(mapping, 2)
	if err != nil {
		t.Fatal(err)
	}
	if err := state.Seed(
		testKey("192.0.2.1", 64496, "203.0.113.0/24"),
		false, 0, 0, 0,
	); err != nil {
		t.Fatal(err)
	}
	ipv6Key := testKey("2001:db8::1", 64497, "2001:db8::/32")
	ipv6Key.AFI = 6
	if err := state.Seed(
		ipv6Key, false, 0, 1, 0,
	); err != nil {
		t.Fatal(err)
	}
	observations, _, conservation, err := state.SnapshotAll(
		CatchUpStartUTC, CatchUpStartUTC, CatchUpStartUTC, "seed",
		NewGlobalSlotActivity(),
	)
	if err != nil {
		t.Fatal(err)
	}
	unknown := findGlobalObservation(t, observations, UnknownCountryCode)
	if unknown.BaselineASNCount != 0 ||
		unknown.BaselinePrefixVPCount != 2 ||
		unknown.IPv4.BaselinePrefixVPCount != 1 ||
		unknown.IPv6.BaselinePrefixVPCount != 1 ||
		unknown.IPv4.VisiblePrefixVPCount != 1 ||
		unknown.IPv6.VisiblePrefixVPCount != 1 {
		t.Fatalf("unknown address-family population is not closed: %+v", unknown)
	}
	if conservation.GlobalBaselinePrefixVP != 2 ||
		conservation.CountryBaselineSum != 2 {
		t.Fatalf("unexpected conservation: %+v", conservation)
	}
}

func TestGlobalRIBCheckpointRoundTripAndIdentity(t *testing.T) {
	mapping := newSyntheticGlobalCountryMapping(map[uint32]string{
		64500: "IR",
		64501: "US",
	})
	root := t.TempDir()
	checkpointRoot := filepath.Join(root, "checkpoints")
	writer, err := NewGlobalRIBCheckpointWriter(checkpointRoot, 4)
	if err != nil {
		t.Fatal(err)
	}
	state, _ := NewGlobalReplayState(mapping, 4)
	entries := []struct {
		key    RouteKey
		known  bool
		origin uint32
	}{
		{globalRouteKey("192.0.2.1", 64496, "203.0.113.0/24"), true, 64500},
		{globalRouteKey("2001:db8::1", 64497, "2001:db8:1::/48"), true, 64501},
		{globalRouteKey("2001:db8::2", 64499, "::ffff:5c2f:90c0/127"), true, 64501},
		{globalRouteKey("192.0.2.2", 64498, "198.51.100.0/24"), false, 0},
	}
	for index, entry := range entries {
		if err := state.Seed(
			entry.key, entry.known, entry.origin, uint32(index+10), 0,
		); err != nil {
			writer.Abort()
			t.Fatal(err)
		}
		if err := writer.Write(
			entry.key, entry.known, entry.origin, uint32(index+10), 0,
		); err != nil {
			writer.Abort()
			t.Fatal(err)
		}
	}
	artifact := Artifact{
		ArtifactID: "art_v1_00000000000000000000000000000000",
		FileSHA256: string(make([]byte, 64)), SizeBytes: 123,
	}
	manifest, err := writer.Finalize(state, artifact)
	if err != nil {
		t.Fatal(err)
	}
	restored, loaded, err := LoadGlobalRIBCheckpoint(
		checkpointRoot, mapping, artifact,
	)
	if err != nil {
		t.Fatal(err)
	}
	if loaded.StateDigest != manifest.StateDigest ||
		restored.StateDigest.Hex() != state.StateDigest.Hex() ||
		len(restored.Routes) != len(entries) {
		t.Fatalf("global RIB checkpoint did not round trip: %+v", loaded)
	}
	if _, err := restored.ValidateConservation(); err != nil {
		t.Fatal(err)
	}
}
