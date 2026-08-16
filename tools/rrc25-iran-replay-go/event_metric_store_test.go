package replay

import (
	"net/netip"
	"os"
	"path/filepath"
	"testing"
)

func syntheticEventMetricDefinition() eventMetricDefinition {
	members := []EventCohortMember{
		{
			SchemaVersion: EventCohortMemberVersion,
			CohortID:      "cohort-ir", CohortMemberID: "member-a", CountryCode: "IR",
			Prefix: "10.0.0.0/24", AddressFamily: "ipv4", CountryOriginASNs: []uint32{65000},
			ExpectedDirectionCount: 2,
			ExpectedDirections: []EventCohortDirection{
				{PeerASN: 100, RouteObservationCount: 1},
				{PeerASN: 200, RouteObservationCount: 1},
			},
		},
		{
			SchemaVersion: EventCohortMemberVersion,
			CohortID:      "cohort-ir", CohortMemberID: "member-b", CountryCode: "IR",
			Prefix: "10.0.1.0/24", AddressFamily: "ipv4", CountryOriginASNs: []uint32{65000},
			ExpectedDirectionCount: 2,
			ExpectedDirections: []EventCohortDirection{
				{PeerASN: 100, RouteObservationCount: 1},
				{PeerASN: 200, RouteObservationCount: 1},
			},
		},
	}
	return eventMetricDefinition{
		EventMetricID: "metric-ir",
		Binding: EventCohortBinding{
			IncidentID: "incident-ir", LegacyReference: "country_outage/test/IR/1/r",
			CountryCode: "IR", CohortID: "cohort-ir",
			CohortStatePointUTC:        eventMetricStatePoint(10),
			WindowStartUTC:             eventMetricStatePoint(1),
			ProjectionEndStatePointUTC: eventMetricStatePoint(20),
		},
		Cohort: EventCohortManifest{
			SchemaVersion: EventCohortVersion, Status: "complete", CohortID: "cohort-ir",
			CountryCode: "IR", CohortStatePointUTC: eventMetricStatePoint(10), CohortStateSlot: 10,
			MemberCount: 2, ExpectedDirectionRelationCount: 4,
		},
		Members: members,
	}
}

func eventMetricRoute(peer string, peerASN uint32, prefix string, origin uint32, action uint8) routeStateEvent {
	return routeStateEvent{
		Key: RouteStateKey{Collector: RouteStateCollectorRRC25, Route: RouteKey{
			PeerIP: netip.MustParseAddr(peer), PeerASN: peerASN,
			AFI: 4, Prefix: netip.MustParsePrefix(prefix).Masked(),
		}},
		Action: action, OriginKnown: action != actionWithdraw, OriginASN: origin,
	}
}

func newSyntheticEventMetricProjector(t *testing.T) *eventMetricProjector {
	t.Helper()
	projector, err := newEventMetricProjector(
		newSyntheticGlobalCountryMapping(map[uint32]string{65000: "IR", 65100: "US"}),
		[]eventMetricDefinition{syntheticEventMetricDefinition()},
	)
	if err != nil {
		t.Fatal(err)
	}
	return projector
}

func TestEventMetricPrefixAndASNClassificationUsesUniqueDirections(t *testing.T) {
	projector := newSyntheticEventMetricProjector(t)
	event := projector.Events[0]
	projector.CurrentSlot = 1
	rows := []routeStateEvent{
		eventMetricRoute("192.0.2.1", 100, "10.0.0.0/24", 65000, actionAnnounce),
		eventMetricRoute("192.0.2.2", 100, "10.0.0.0/24", 65000, actionAnnounce),
		eventMetricRoute("192.0.2.3", 200, "10.0.0.0/24", 65100, actionAnnounce),
		eventMetricRoute("192.0.2.1", 100, "10.0.1.0/24", 65000, actionAnnounce),
		eventMetricRoute("192.0.2.3", 200, "10.0.1.0/24", 65000, actionAnnounce),
	}
	for _, row := range rows {
		if err := projector.Apply(row); err != nil {
			t.Fatal(err)
		}
	}
	if event.NormalPrefixes != 2 || event.PartialPrefixes != 0 || event.CompletePrefixes != 0 {
		t.Fatalf("unexpected initial prefix classes: %+v", event.seriesRow(1, true, "").Values)
	}
	if event.NormalASNs != 1 || event.AffectedASNs != 0 || event.InterruptedASNs != 0 {
		t.Fatalf("unexpected initial ASN classes: %+v", event.ASNs[65000])
	}
	// 同一 peer ASN 下两个会话只是一个方向，撤掉一个不得改变前缀分类。
	if err := projector.Apply(eventMetricRoute("192.0.2.1", 100, "10.0.0.0/24", 0, actionWithdraw)); err != nil {
		t.Fatal(err)
	}
	if event.Members[0].Classification != eventPrefixNormal {
		t.Fatal("one session withdrawal expanded a peer-ASN direction")
	}
	if err := projector.Apply(eventMetricRoute("192.0.2.2", 100, "10.0.0.0/24", 0, actionWithdraw)); err != nil {
		t.Fatal(err)
	}
	if event.Members[0].Classification != eventPrefixPartial || event.AffectedASNs != 1 {
		t.Fatalf("partial interruption was not projected: %+v", event.ASNs[65000])
	}
	if err := projector.Apply(eventMetricRoute("192.0.2.3", 200, "10.0.0.0/24", 0, actionWithdraw)); err != nil {
		t.Fatal(err)
	}
	if event.Members[0].Classification != eventPrefixComplete || event.AffectedASNs != 1 {
		t.Fatalf("complete prefix interruption changed AS rule: %+v", event.ASNs[65000])
	}
	for _, peer := range []struct {
		ip  string
		asn uint32
	}{{"192.0.2.1", 100}, {"192.0.2.3", 200}} {
		if err := projector.Apply(eventMetricRoute(peer.ip, peer.asn, "10.0.1.0/24", 0, actionWithdraw)); err != nil {
			t.Fatal(err)
		}
	}
	if event.InterruptedASNs != 1 || event.AffectedASNs != 0 {
		t.Fatalf("AS route interruption requires every fixed prefix complete: %+v", event.ASNs[65000])
	}
}

func TestEventMetricDirectionVisibilityDoesNotRequireSameOrigin(t *testing.T) {
	projector := newSyntheticEventMetricProjector(t)
	projector.CurrentSlot = 1
	if err := projector.Apply(eventMetricRoute("192.0.2.1", 100, "10.0.0.0/24", 65100, actionAnnounce)); err != nil {
		t.Fatal(err)
	}
	member := projector.Events[0].Members[0]
	if member.Classification != eventPrefixPartial || member.VisibleDirectionCount != 1 {
		t.Fatalf("a visible non-country origin direction was discarded: %+v", member)
	}
}

func TestEventMetricNewPrefixIsIndependentAndCumulative(t *testing.T) {
	projector := newSyntheticEventMetricProjector(t)
	event := projector.Events[0]
	projector.CurrentSlot = 11
	announce := eventMetricRoute("192.0.2.1", 100, "10.0.2.0/24", 65000, actionAnnounce)
	if err := projector.Apply(announce); err != nil {
		t.Fatal(err)
	}
	values := event.seriesRow(11, true, "").Values
	if values.NewVisibleIPv4PrefixCount != 1 || values.NewCumulativeIPv4PrefixCount != 1 ||
		values.NewVisibleIPv4AddressCount != 256 || values.NewCumulativeIPv4AddressCount != 256 {
		t.Fatalf("new prefix track is invalid: %+v", values)
	}
	if event.PartialPrefixes != 0 || event.CompletePrefixes != 2 {
		t.Fatal("new prefix offset the fixed cohort interruption")
	}
	if err := projector.Apply(eventMetricRoute("192.0.2.1", 100, "10.0.2.0/24", 0, actionWithdraw)); err != nil {
		t.Fatal(err)
	}
	values = event.seriesRow(11, true, "").Values
	if values.NewVisibleIPv4PrefixCount != 0 || values.NewCumulativeIPv4PrefixCount != 1 ||
		values.NewVisibleIPv4AddressCount != 0 || values.NewCumulativeIPv4AddressCount != 256 {
		t.Fatalf("new prefix cumulative/current tracks were mixed: %+v", values)
	}
}

func TestEventMetricUnknownSlotDoesNotEmitZeros(t *testing.T) {
	projector := newSyntheticEventMetricProjector(t)
	row := projector.Events[0].seriesRow(3, false, "source_slot_incomplete")
	if row.ValueState != "unknown" || row.Values != nil || row.MissingReason == nil {
		t.Fatalf("unknown slot was coerced into numeric zero: %+v", row)
	}
}

func TestEventMetricCohortPointMustMatchCountryPrefixPopulation(t *testing.T) {
	projector := newSyntheticEventMetricProjector(t)
	projector.CurrentSlot = 10
	for _, prefix := range []string{"10.0.0.0/24", "10.0.1.0/24"} {
		if err := projector.Apply(eventMetricRoute("192.0.2.1", 100, prefix, 65000, actionAnnounce)); err != nil {
			t.Fatal(err)
		}
		if err := projector.Apply(eventMetricRoute("192.0.2.3", 200, prefix, 65100, actionAnnounce)); err != nil {
			t.Fatal(err)
		}
	}
	if err := projector.ValidateCohortsAtCurrentSlot(); err != nil {
		t.Fatalf("matching fixed cohort was rejected: %v", err)
	}
	if err := projector.Apply(eventMetricRoute("192.0.2.1", 100, "10.0.9.0/24", 65000, actionAnnounce)); err != nil {
		t.Fatal(err)
	}
	if err := projector.ValidateCohortsAtCurrentSlot(); err == nil {
		t.Fatal("country prefix outside the formal cohort was accepted at the freeze point")
	}
}

func TestEventMetricWriterUsesBaselineAndChangePoints(t *testing.T) {
	definition := syntheticEventMetricDefinition()
	definition.Binding.WindowStartUTC = eventMetricStatePoint(1)
	definition.Binding.ProjectionEndStatePointUTC = eventMetricStatePoint(3)
	projector, err := newEventMetricProjector(
		newSyntheticGlobalCountryMapping(map[uint32]string{65000: "IR", 65100: "US"}),
		[]eventMetricDefinition{definition},
	)
	if err != nil {
		t.Fatal(err)
	}
	projector.CurrentSlot = 1
	for _, prefix := range []string{"10.0.0.0/24", "10.0.1.0/24"} {
		for _, peer := range []struct {
			ip  string
			asn uint32
		}{{"192.0.2.1", 100}, {"192.0.2.3", 200}} {
			if err := projector.Apply(eventMetricRoute(peer.ip, peer.asn, prefix, 65000, actionAnnounce)); err != nil {
				t.Fatal(err)
			}
		}
	}
	root := t.TempDir()
	manager := newEventMetricWriterManager(root)
	if err := manager.writeSlot(projector, 1); err != nil {
		t.Fatal(err)
	}
	projector.CurrentSlot = 2
	if err := projector.Apply(eventMetricRoute("192.0.2.1", 100, "10.0.0.0/24", 0, actionWithdraw)); err != nil {
		t.Fatal(err)
	}
	if err := manager.writeSlot(projector, 2); err != nil {
		t.Fatal(err)
	}
	projector.CurrentSlot = 3
	if err := manager.writeSlot(projector, 3); err != nil {
		t.Fatal(err)
	}
	if len(manager.completed) != 1 || len(manager.active) != 0 {
		t.Fatalf("writer lifecycle did not close: completed=%d active=%d", len(manager.completed), len(manager.active))
	}
	manifest := manager.completed[0]
	if manifest.Series.RowCount != 3 || manifest.PrefixStates.RowCount != 3 ||
		manifest.ASNStates.RowCount != 2 || manifest.NewPrefixStates.RowCount != 0 {
		t.Fatalf("baseline/change-point population mismatch: %+v", manifest)
	}
	if err := verifyRouteEventStoreFile(root, manifest.Series); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(root, filepath.FromSlash(manifest.Directory), "manifest.json")); err != nil {
		t.Fatal(err)
	}
}
