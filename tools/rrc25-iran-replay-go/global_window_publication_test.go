package replay

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestParseGlobalWindowEventsKeepsCoveredCountryOutages(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "events.json")
	payload := map[string]any{
		"data": []map[string]any{
			{
				"detail_url":       "country_outage/2026-03-09 22:09:38/MW/2/r",
				"attacked_country": "马拉维",
			},
			{
				"detail_url":       "country_outage/2026-03-12 00:00:00/MW/3/r",
				"attacked_country": "马拉维",
			},
			{
				"detail_url": "hijack/2026-03-09 22:09:38/MW/2/r",
			},
		},
	}
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	start, _ := time.Parse(time.RFC3339, "2026-02-24T00:00:00Z")
	end, _ := time.Parse(time.RFC3339, "2026-03-11T00:00:00Z")
	events, err := parseGlobalWindowEvents(path, start, end)
	if err != nil {
		t.Fatal(err)
	}
	if len(events) != 1 {
		t.Fatalf("expected one covered event, got %d", len(events))
	}
	if events[0].CountryCode != "MW" || events[0].CountryName != "马拉维" {
		t.Fatalf("unexpected event identity: %+v", events[0])
	}
	if events[0].EventTime.Format(time.RFC3339) != "2026-03-09T14:09:38Z" ||
		events[0].DetectedAt.Format(time.RFC3339) != "2026-03-09T14:10:00Z" {
		t.Fatalf("unexpected event time alignment: %+v", events[0])
	}
}

func TestCompareProjectedObservationRequiresExactCompactReconciliation(t *testing.T) {
	ratio := 0.9
	exact := GlobalCountryObservation{
		CountryCode: "MW", CohortID: "cohort-mw",
		ObservedAt:       "2026-03-09T14:10:00Z",
		BaselineASNCount: 2, BaselinePrefixVPCount: 10,
		VisiblePrefixVPCount: 9, CurrentPrefixVPCount: 9,
		VisiblePrefixVPRatio: ratio, VisibleOriginASNCount: 2,
		AffectedASNCount: 1,
		DualClassifications: map[string][]uint32{
			"fully_invisible": {}, "partially_visible": {64500},
		},
		IPv4: FamilyMetrics{
			BaselineASNCount: 2, VisibleASNCount: 2,
			PartiallyVisibleASNs:  []uint32{64500},
			FullyInvisibleASNs:    []uint32{},
			BaselinePrefixVPCount: 10, VisiblePrefixVPCount: 9,
		},
		IPv6: FamilyMetrics{
			FullyVisibleASNs: []uint32{}, PartiallyVisibleASNs: []uint32{},
			FullyInvisibleASNs: []uint32{},
		},
	}
	summary := GlobalCountrySummary{
		CountryCode: "MW", CohortID: "cohort-mw",
		ObservedAt:       "2026-03-09T14:10:00Z",
		BaselinePrefixVP: 10, VisiblePrefixVP: 9, CurrentPrefixVP: 9,
		VisiblePrefixVPRatio: ratio, BaselineASNCount: 2,
		VisibleOriginASNCount: 2, AffectedASNCount: 1,
		IPv4: GlobalWindowFamilySummary{
			BaselinePrefixVP: 10, VisiblePrefixVP: 9,
			BaselineASNCount: 2, VisibleASNCount: 2, AffectedASNCount: 1,
		},
		IPv6: GlobalWindowFamilySummary{},
	}
	if err := compareProjectedObservation(exact, summary); err != nil {
		t.Fatal(err)
	}
	summary.VisiblePrefixVP = 8
	if err := compareProjectedObservation(exact, summary); err == nil {
		t.Fatal("expected compact summary mismatch")
	}
}
