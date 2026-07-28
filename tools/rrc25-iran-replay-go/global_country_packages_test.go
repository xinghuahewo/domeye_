package replay

import (
	"reflect"
	"testing"
)

func TestBuildGlobalCountryCohortsPreservesPrefixVPPopulation(t *testing.T) {
	mapping := newSyntheticGlobalCountryMapping(map[uint32]string{
		64500: "IR",
		64501: "US",
	})
	state, err := NewGlobalReplayState(mapping, 4)
	if err != nil {
		t.Fatal(err)
	}
	rows := []struct {
		key         RouteKey
		originKnown bool
		originASN   uint32
	}{
		{testKey("192.0.2.1", 64496, "203.0.113.0/24"), true, 64500},
		{testKey("192.0.2.2", 64497, "203.0.113.0/24"), true, 64500},
		{testKey("192.0.2.3", 64498, "2001:db8::/32"), true, 64501},
		{testKey("192.0.2.4", 64499, "198.51.100.0/24"), false, 0},
	}
	for index, row := range rows {
		if err := state.Seed(
			row.key, row.originKnown, row.originASN, uint32(index), 0,
		); err != nil {
			t.Fatal(err)
		}
	}
	manifest := GlobalRIBCheckpointManifest{
		CollectorID: "rrc25", SeedObservedAt: CatchUpStartUTC,
		MappingVersion: mapping.MappingVersion,
		Countries:      globalCheckpointCountries(state),
	}
	documents, err := buildGlobalCountryCohorts(state, manifest)
	if err != nil {
		t.Fatal(err)
	}
	byCode := make(map[string]GlobalCountryCohortDocument)
	for _, document := range documents {
		byCode[document.CountryCode] = document
	}
	iran := byCode["IR"]
	if iran.BaselinePrefixVPCount != 2 ||
		iran.BaselineOriginASNCount != 1 ||
		len(iran.Members) != 1 ||
		iran.Members[0].PrefixVPCount != 2 ||
		!reflect.DeepEqual(iran.Members[0].Prefixes, []string{"203.0.113.0/24"}) {
		t.Fatalf("unexpected IR cohort: %+v", iran)
	}
	unknown := byCode[UnknownCountryCode]
	if unknown.BaselinePrefixVPCount != 1 ||
		unknown.UnknownOriginPrefixVP != 1 ||
		unknown.BaselineOriginASNCount != 0 ||
		len(unknown.Members) != 0 {
		t.Fatalf("unexpected unknown cohort: %+v", unknown)
	}
}

func TestPackageObservationUsesLegacyCompatibleSnapshotIdentity(t *testing.T) {
	ratio := 0.5
	observation := GlobalCountryObservation{
		CountryCode:           "IR",
		CohortID:              "cohort-test",
		ObservedAt:            "2026-02-28T10:05:00Z",
		BaselinePrefixVPCount: 10,
		VisiblePrefixVPCount:  8,
		VisiblePrefixVPRatio:  0.8,
		AffectedASNRatio:      &ratio,
		VisibleOriginASNRatio: &ratio,
		UpdateCounts:          UpdateCounts{Announce: 7, Withdraw: 3},
		CountryUpdateCounts:   UpdateCounts{Announce: 2, Withdraw: 1},
	}
	packaged := packageObservation(observation)
	expected := stableID("snapshot_go_v1_", map[string]any{
		"cohort_id":               observation.CohortID,
		"observed_at":             observation.ObservedAt,
		"visible_prefix_vp_count": observation.VisiblePrefixVPCount,
	}, 32)
	if packaged.SnapshotID != expected {
		t.Fatalf("snapshot identity differs: %s != %s", packaged.SnapshotID, expected)
	}
	if packaged.UpdateCounts != observation.UpdateCounts ||
		packaged.CountryUpdateCounts != observation.CountryUpdateCounts {
		t.Fatal("collector and country UPDATE activity were not both preserved")
	}
}
