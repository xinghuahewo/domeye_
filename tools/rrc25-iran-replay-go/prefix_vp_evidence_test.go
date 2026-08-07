package replay

import (
	"compress/gzip"
	"encoding/json"
	"net/netip"
	"os"
	"path/filepath"
	"testing"
)

func evidenceTestEvent(key RouteStateKey, action uint8, origin uint32, artifact uint16) routeStateEvent {
	return routeStateEvent{
		Key: key, Action: action, OriginKnown: action != actionWithdraw,
		OriginASN: origin, ASPathKnown: action != actionWithdraw,
		AttributeKnown: action != actionWithdraw, ArtifactIndex: artifact,
		EventMicros: 1773187200000000,
	}
}

func TestPrefixVPEvidenceDerivedFromSeedAndFinalRouteState(t *testing.T) {
	mapping := newSyntheticGlobalCountryMapping(map[uint32]string{64500: "IR", 64501: "US"})
	identity := RouteStateCheckpointIdentity{
		RouteStateDatasetID:        "route_state_dataset_test",
		SourceRouteEventDatasetID:  "route_event_dataset_test",
		SourceRouteEventContentSHA: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
		ImplementationID:           "git:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
		ProjectorName:              RouteStateProjectorName, ProjectorVersion: RouteStateProjectorVersion,
		MappingVersion: mapping.MappingVersion,
		WindowStartUTC: RouteEventWindowStartUTC, WindowEndExclusiveUTC: RouteEventWindowEndUTC,
	}
	keyIR := RouteStateKey{Collector: RouteStateCollectorRRC25, Route: RouteKey{
		PeerIP: netip.MustParseAddr("192.0.2.1"), PeerASN: 64496, AFI: 4,
		Prefix: netip.MustParsePrefix("203.0.113.0/24"),
	}}
	keyUS := RouteStateKey{Collector: RouteStateCollectorRRC25, Route: RouteKey{
		PeerIP: netip.MustParseAddr("192.0.2.2"), PeerASN: 64497, AFI: 4,
		Prefix: netip.MustParsePrefix("198.51.100.0/24"),
	}}
	seed, _ := NewRouteState(2)
	if err := seed.Apply(evidenceTestEvent(keyIR, actionAnnounce, 64500, 0)); err != nil {
		t.Fatal(err)
	}
	if err := seed.Apply(evidenceTestEvent(keyUS, actionAnnounce, 64501, 0)); err != nil {
		t.Fatal(err)
	}
	final, _ := NewRouteState(2)
	if err := final.Apply(evidenceTestEvent(keyIR, actionAnnounce, 64500, 0)); err != nil {
		t.Fatal(err)
	}
	if err := final.Apply(evidenceTestEvent(keyUS, actionAnnounce, 64501, 0)); err != nil {
		t.Fatal(err)
	}
	if err := final.Apply(evidenceTestEvent(keyIR, actionWithdraw, 0, 4320)); err != nil {
		t.Fatal(err)
	}
	root := t.TempDir()
	seedDirectory := filepath.Join(root, "seed")
	finalDirectory := filepath.Join(root, "final")
	seedManifest, err := WriteRouteStateCheckpoint(
		seedDirectory, seed, identity, RouteEventWindowStartUTC, 0, 2,
	)
	if err != nil {
		t.Fatal(err)
	}
	finalManifest, err := WriteRouteStateCheckpoint(
		finalDirectory, final, identity, RouteEventWindowEndUTC, RouteStateFinalSlot, 2,
	)
	if err != nil {
		t.Fatal(err)
	}
	output := filepath.Join(root, "evidence")
	if err := os.Mkdir(output, 0o750); err != nil {
		t.Fatal(err)
	}
	catalog, err := buildPrefixVPEvidenceFromCheckpoints(
		output, seedDirectory, seedManifest, finalDirectory, finalManifest,
		mapping, []string{"IR"}, 1, "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
	)
	if err != nil {
		t.Fatal(err)
	}
	if catalog.RowCount != 1 || len(catalog.Countries) != 1 ||
		catalog.Countries[0].RowCount != 1 || catalog.Countries[0].PageCount != 1 {
		t.Fatalf("unexpected evidence population: %+v", catalog)
	}
	pagePath := filepath.Join(output, "pages", filepath.FromSlash(catalog.Countries[0].Pages[0].Path))
	file, err := os.Open(pagePath)
	if err != nil {
		t.Fatal(err)
	}
	decoded, err := gzip.NewReader(file)
	if err != nil {
		t.Fatal(err)
	}
	var page prefixVPEvidencePage
	if err := json.NewDecoder(decoded).Decode(&page); err != nil {
		t.Fatal(err)
	}
	if err := decoded.Close(); err != nil {
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	if len(page.Rows) != 1 || page.Rows[0].Visible ||
		page.Rows[0].BaselineOriginASN != 64500 {
		t.Fatalf("unexpected derived row: %+v", page.Rows)
	}
}
