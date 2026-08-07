package replay

import (
	"fmt"
	"net/netip"
	"testing"
)

func frozenSyntheticMetricMapping(rows map[uint32]string) *GlobalCountryMapping {
	mapping := newSyntheticGlobalCountryMapping(rows)
	seen := make(map[string]struct{}, len(mapping.codes))
	for _, code := range mapping.codes {
		seen[code] = struct{}{}
	}
	for first := byte('A'); first <= byte('Z') && len(mapping.codes) < 241; first++ {
		for second := byte('A'); second <= byte('Z') && len(mapping.codes) < 241; second++ {
			code := string([]byte{first, second})
			if _, exists := seen[code]; exists {
				continue
			}
			mapping.codeToID[code] = uint16(len(mapping.codes))
			mapping.codes = append(mapping.codes, code)
			seen[code] = struct{}{}
		}
	}
	return mapping
}

func metricTestKey(peer string, peerASN uint32, prefix string) RouteStateKey {
	parsedPrefix := netip.MustParsePrefix(prefix)
	afi := uint8(6)
	if parsedPrefix.Addr().Is4() {
		afi = 4
	}
	return RouteStateKey{
		Collector: RouteStateCollectorRRC25,
		Route: RouteKey{
			PeerIP: netip.MustParseAddr(peer), PeerASN: peerASN,
			AFI: afi, Prefix: parsedPrefix,
		},
	}
}

func metricTestEvent(
	key RouteStateKey,
	action uint8,
	originKnown bool,
	originASN uint32,
	ordinal uint32,
) routeStateEvent {
	var id [16]byte
	id[0] = byte(ordinal)
	return routeStateEvent{
		Key: key, Action: action, OriginKnown: originKnown, OriginASN: originASN,
		RouteEventID: id, ArtifactIndex: 1, RecordOrdinal: ordinal,
		EventMicros: int64(ordinal + 1), QualityStatus: routeStateQualityClean,
	}
}

func metricRowBySubject(rows []RouteMetricRow, subject string) RouteMetricRow {
	for _, row := range rows {
		if row.SubjectID == subject {
			return row
		}
	}
	panic(fmt.Sprintf("metric row %s is absent", subject))
}

func TestRouteMetricProjectionUsesOnlyRouteStateTransitions(t *testing.T) {
	mapping := frozenSyntheticMetricMapping(map[uint32]string{
		64500: "IR", 64501: "US", 64502: "IR",
	})
	state, _ := NewRouteState(4)
	irV4 := metricTestKey("192.0.2.1", 64496, "10.0.0.0/24")
	usV6 := metricTestKey("2001:db8::1", 64497, "2001:db8:1::/48")
	unknownV4 := metricTestKey("192.0.2.2", 64498, "10.0.1.0/24")
	for index, event := range []routeStateEvent{
		metricTestEvent(irV4, actionRIBSnapshot, true, 64500, 1),
		metricTestEvent(usV6, actionRIBSnapshot, true, 64501, 2),
		metricTestEvent(unknownV4, actionRIBSnapshot, false, 0, 3),
	} {
		event.ArtifactIndex = 0
		event.RecordOrdinal = uint32(index)
		if err := state.Apply(event); err != nil {
			t.Fatal(err)
		}
	}
	projector, err := NewRouteMetricProjectorFromSeed(state, mapping)
	if err != nil {
		t.Fatal(err)
	}
	orphan := metricTestKey("192.0.2.3", 64499, "10.0.2.0/24")
	events := []routeStateEvent{
		metricTestEvent(irV4, actionWithdraw, false, 0, 10),
		metricTestEvent(irV4, actionAnnounce, true, 64500, 11),
		metricTestEvent(usV6, actionAnnounce, true, 64502, 12),
		metricTestEvent(unknownV4, actionWithdraw, false, 0, 13),
		metricTestEvent(orphan, actionWithdraw, false, 0, 14),
	}
	for _, event := range events {
		if err := projector.Apply(state, event); err != nil {
			t.Fatal(err)
		}
	}
	snapshot, err := projector.Snapshot(state, "2026-02-24T00:05:00Z")
	if err != nil {
		t.Fatal(err)
	}
	if len(snapshot.Countries) != 241 {
		t.Fatalf("country rows=%d", len(snapshot.Countries))
	}
	if snapshot.Collector.BaselineRouteStateCountV4 != 2 ||
		snapshot.Collector.BaselineRouteStateCountV6 != 1 ||
		snapshot.Collector.CurrentVisibleRouteStateCountV4 != 1 ||
		snapshot.Collector.CurrentVisibleRouteStateCountV6 != 1 ||
		snapshot.Collector.AnnouncementCountV4 != 1 ||
		snapshot.Collector.AnnouncementCountV6 != 1 ||
		snapshot.Collector.WithdrawalCountV4 != 3 {
		t.Fatalf("unexpected collector row: %+v", snapshot.Collector)
	}
	ir := metricRowBySubject(snapshot.Countries, "IR")
	if ir.BaselineRouteStateCountV4 != 1 || ir.BaselineRouteStateCountV6 != 0 ||
		ir.CohortVisibleRouteStateCountV4 != 1 || ir.CurrentVisibleRouteStateCountV4 != 1 ||
		ir.CurrentVisibleRouteStateCountV6 != 1 || ir.AnnouncementCountV4 != 1 ||
		ir.AnnouncementCountV6 != 1 || ir.WithdrawalCountV4 != 1 ||
		ir.CohortVisibilityStateV6 != "not_applicable" {
		t.Fatalf("unexpected IR row: %+v", ir)
	}
	us := metricRowBySubject(snapshot.Countries, "US")
	if us.BaselineRouteStateCountV6 != 1 || us.CohortVisibleRouteStateCountV6 != 0 ||
		us.CurrentVisibleRouteStateCountV6 != 0 {
		t.Fatalf("unexpected US row: %+v", us)
	}
	unknown := metricRowBySubject(snapshot.Countries, UnknownCountryCode)
	if unknown.BaselineRouteStateCountV4 != 1 || unknown.CurrentVisibleRouteStateCountV4 != 0 ||
		unknown.WithdrawalCountV4 != 2 {
		t.Fatalf("unexpected unknown row: %+v", unknown)
	}
	oldASN := metricRowBySubject(snapshot.ASNs, "AS64501")
	if oldASN.CurrentVisibleRouteStateCountV6 != 0 || oldASN.CohortVisibleRouteStateCountV6 != 0 {
		t.Fatalf("replacement old ASN zero transition was lost: %+v", oldASN)
	}
	newASN := metricRowBySubject(snapshot.ASNs, "AS64502")
	if newASN.BaselineRouteStateCountV6 != 0 || newASN.CurrentVisibleRouteStateCountV6 != 1 ||
		newASN.AnnouncementCountV6 != 1 {
		t.Fatalf("replacement new ASN row mismatch: %+v", newASN)
	}
	if len(state.Routes) != 4 || state.VisibleRouteCount != 2 {
		t.Fatalf("projector did not preserve unique RouteState semantics")
	}

	next, err := projector.Snapshot(state, "2026-02-24T00:10:00Z")
	if err != nil {
		t.Fatal(err)
	}
	if len(next.ASNs) != 0 || next.Collector.AnnouncementCountV4 != 0 ||
		next.Collector.WithdrawalCountV4 != 0 {
		t.Fatalf("unchanged ASN slot must use carry-forward plus true zero flow: %+v", next)
	}
}

func TestRouteMetricProjectionRejectsNonFrozenCountryPopulation(t *testing.T) {
	state, _ := NewRouteState(1)
	key := metricTestKey("192.0.2.1", 64496, "10.0.0.0/24")
	if err := state.Apply(metricTestEvent(key, actionRIBSnapshot, true, 64500, 1)); err != nil {
		t.Fatal(err)
	}
	if _, err := NewRouteMetricProjectorFromSeed(
		state, newSyntheticGlobalCountryMapping(map[uint32]string{64500: "IR"}),
	); err == nil {
		t.Fatal("non-frozen country population was accepted")
	}
}
