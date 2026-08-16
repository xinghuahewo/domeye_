package replay

import (
	"testing"
	"time"
)

func observationAt(index int, affected, prefixVisible float64) Observation {
	at := windowStart.Add(time.Duration(index) * 5 * time.Minute).Format(time.RFC3339)
	return Observation{
		SchemaVersion: ObservationVersion, SnapshotID: "snapshot-" + at,
		ObservedAt: at, CohortID: "cohort-test",
		AffectedASNRatio:      affected,
		VisibleOriginASNRatio: 1 - affected,
		VisiblePrefixVPRatio:  prefixVisible,
	}
}

func TestNormalBandAndEventConfirmationUseTwoAndSixSlots(t *testing.T) {
	catchUp := make([]Observation, 25)
	for index := range catchUp {
		catchUp[index] = observationAt(index, 0, 1)
	}
	band := BuildNormalBand(catchUp)
	if band.State != "usable" {
		t.Fatalf("expected usable band: %+v", band)
	}
	formal := make([]Observation, 60)
	for index := range formal {
		switch {
		case index < 10:
			formal[index] = observationAt(index, 0.10, 0.80)
		default:
			formal[index] = observationAt(index, 0, 1)
		}
	}
	incident := DeriveIncident(formal, band)
	if len(incident.Episodes) != 1 {
		t.Fatalf("expected one episode: %+v", incident)
	}
	episode := incident.Episodes[0]
	if episode.OnsetAt != formal[0].ObservedAt ||
		episode.DetectedAt != formal[1].ObservedAt {
		t.Fatalf("two-slot detection mismatch: %+v", episode)
	}
	if episode.PartialRecoveryAt == nil || *episode.PartialRecoveryAt != formal[10].ObservedAt ||
		episode.FullRecoveryAt == nil || *episode.FullRecoveryAt != formal[10].ObservedAt {
		t.Fatalf("six-slot recovery assignment mismatch: %+v", episode)
	}
}

func TestUnstableCatchUpDisablesFullRecovery(t *testing.T) {
	rows := []Observation{
		observationAt(0, 0, 1),
		observationAt(1, 0.04, 0.95),
		observationAt(2, 0.04, 0.95),
	}
	band := BuildNormalBand(rows)
	if band.State != "unknown" || !band.ConfirmedAnomaly {
		t.Fatalf("unstable catch-up must disable normal band: %+v", band)
	}
}
