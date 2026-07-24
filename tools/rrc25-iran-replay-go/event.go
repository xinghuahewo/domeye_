package replay

import (
	"math"
	"sort"
)

type MetricBand struct {
	Lower       float64 `json:"lower"`
	Upper       float64 `json:"upper"`
	Median      float64 `json:"median"`
	MAD         float64 `json:"mad"`
	SampleCount int     `json:"sample_count"`
}

type NormalBand struct {
	State                 string      `json:"state"`
	VisibleOriginASNRatio *MetricBand `json:"visible_origin_asn_ratio,omitempty"`
	VisiblePrefixVPRatio  *MetricBand `json:"visible_prefix_vp_ratio,omitempty"`
	ConfirmedAnomaly      bool        `json:"confirmed_anomaly_in_catch_up"`
	Unstable              bool        `json:"unstable_over_detection_scale"`
}

func median(values []float64) float64 {
	copy := append([]float64(nil), values...)
	sort.Float64s(copy)
	middle := len(copy) / 2
	if len(copy)%2 == 1 {
		return copy[middle]
	}
	return (copy[middle-1] + copy[middle]) / 2
}

func metricBand(values []float64) MetricBand {
	center := median(values)
	deviations := make([]float64, len(values))
	for index, value := range values {
		deviations[index] = math.Abs(value - center)
	}
	mad := median(deviations)
	width := math.Max(3*mad, 0.001)
	return MetricBand{
		Lower: math.Max(0, center-width), Upper: math.Min(1, center+width),
		Median: center, MAD: mad, SampleCount: len(values),
	}
}

func BuildNormalBand(rows []Observation) NormalBand {
	if len(rows) == 0 {
		return NormalBand{State: "unknown"}
	}
	visibleASN := make([]float64, len(rows))
	visiblePrefix := make([]float64, len(rows))
	confirmed := false
	for index, row := range rows {
		visibleASN[index] = row.VisibleOriginASNRatio
		visiblePrefix[index] = row.VisiblePrefixVPRatio
		if index > 0 && row.AffectedASNRatio > 0.03 &&
			rows[index-1].AffectedASNRatio > 0.03 {
			confirmed = true
		}
	}
	rangeGreater := func(values []float64) bool {
		minimum, maximum := values[0], values[0]
		for _, value := range values[1:] {
			minimum = math.Min(minimum, value)
			maximum = math.Max(maximum, value)
		}
		return maximum-minimum > 0.03
	}
	unstable := rangeGreater(visibleASN) || rangeGreater(visiblePrefix)
	if confirmed || unstable {
		return NormalBand{
			State: "unknown", ConfirmedAnomaly: confirmed, Unstable: unstable,
		}
	}
	asnBand := metricBand(visibleASN)
	prefixBand := metricBand(visiblePrefix)
	return NormalBand{
		State: "usable", VisibleOriginASNRatio: &asnBand,
		VisiblePrefixVPRatio: &prefixBand,
		ConfirmedAnomaly:     confirmed, Unstable: unstable,
	}
}

type Milestone struct {
	At         string  `json:"at"`
	SnapshotID string  `json:"snapshot_id"`
	Metric     string  `json:"metric"`
	Value      float64 `json:"value"`
	Precision  string  `json:"precision"`
}

type Episode struct {
	EpisodeID         string      `json:"episode_id"`
	Ordinal           int         `json:"ordinal"`
	DetectedAt        string      `json:"detected_at"`
	OnsetAt           string      `json:"onset_at"`
	PeakAt            string      `json:"peak_at"`
	TroughAt          string      `json:"trough_at"`
	PartialRecoveryAt *string     `json:"partial_recovery_at"`
	FullRecoveryAt    *string     `json:"full_recovery_at"`
	ObservationEndAt  string      `json:"observation_end_at"`
	DurationState     string      `json:"duration_state"`
	RecoveryState     string      `json:"recovery_state"`
	Milestones        []Milestone `json:"milestones"`
}

type Incident struct {
	SchemaVersion     string     `json:"schema_version"`
	IncidentID        string     `json:"incident_id"`
	LegacyRef         string     `json:"legacy_ref"`
	CountryCode       string     `json:"country_code"`
	CollectorID       string     `json:"collector_id"`
	CohortID          string     `json:"cohort_id"`
	DetectedAt        *string    `json:"detected_at"`
	OnsetAt           *string    `json:"onset_at"`
	PeakAt            *string    `json:"peak_at"`
	TroughAt          *string    `json:"trough_at"`
	PartialRecoveryAt *string    `json:"partial_recovery_at"`
	FullRecoveryAt    *string    `json:"full_recovery_at"`
	ObservationEndAt  string     `json:"observation_end_at"`
	DurationState     string     `json:"duration_state"`
	RecoveryState     string     `json:"recovery_state"`
	NormalBand        NormalBand `json:"normal_band"`
	Episodes          []Episode  `json:"episodes"`
	AlgorithmVersion  string     `json:"algorithm_version"`
}

func pointer(value string) *string {
	copy := value
	return &copy
}

func DeriveIncident(rows []Observation, band NormalBand) Incident {
	incident := Incident{
		SchemaVersion: "rrc25-country-incident-go/v1",
		LegacyRef:     "country_outage/2026-02-27 09:12:32/IR/1/r",
		CountryCode:   "IR", CollectorID: "rrc25",
		ObservationEndAt: WindowEndUTC, DurationState: "unavailable",
		RecoveryState: "no_confirmed_episode", NormalBand: band,
		Episodes: []Episode{}, AlgorithmVersion: EngineVersion,
	}
	if len(rows) == 0 {
		return incident
	}
	incident.CohortID = rows[0].CohortID
	incident.IncidentID = stableID("incident_go_v1_", map[string]any{
		"legacy_ref": incident.LegacyRef, "cohort_id": incident.CohortID,
		"window_start": WindowStartUTC, "window_end": WindowEndUTC,
	}, 32)
	anomaly := make([]bool, len(rows))
	partialOK := make([]bool, len(rows))
	fullOK := make([]bool, len(rows))
	for index, row := range rows {
		anomaly[index] = row.AffectedASNRatio > 0.03
		partialOK[index] = row.VisibleOriginASNRatio >= 0.99 &&
			row.VisiblePrefixVPRatio >= 0.99
		fullOK[index] = band.State == "usable" &&
			row.VisibleOriginASNRatio >= band.VisibleOriginASNRatio.Lower &&
			row.VisibleOriginASNRatio <= band.VisibleOriginASNRatio.Upper &&
			row.VisiblePrefixVPRatio >= band.VisiblePrefixVPRatio.Lower &&
			row.VisiblePrefixVPRatio <= band.VisiblePrefixVPRatio.Upper
	}
	ordinal := 0
	for index := 0; index+1 < len(rows); {
		if !anomaly[index] || !anomaly[index+1] {
			index++
			continue
		}
		ordinal++
		start := index
		detected := index + 1
		end := len(rows) - 1
		partial := -1
		full := -1
		for cursor := detected; cursor+5 < len(rows); cursor++ {
			if partial < 0 {
				ok := true
				for candidate := cursor; candidate < cursor+6; candidate++ {
					ok = ok && partialOK[candidate]
				}
				if ok {
					partial = cursor
				}
			}
			ok := true
			for candidate := cursor; candidate < cursor+6; candidate++ {
				ok = ok && fullOK[candidate]
			}
			if ok {
				full = cursor
				end = cursor + 5
				break
			}
		}
		peak := start
		trough := start
		for candidate := start + 1; candidate <= end; candidate++ {
			if rows[candidate].AffectedASNRatio > rows[peak].AffectedASNRatio {
				peak = candidate
			}
			if rows[candidate].VisiblePrefixVPRatio < rows[trough].VisiblePrefixVPRatio {
				trough = candidate
			}
		}
		precision := "five_minute_state"
		if start == 0 {
			precision = "left_censored_at_window_start"
		}
		episode := Episode{
			EpisodeID: stableID("episode_go_v1_", map[string]any{
				"incident_id": incident.IncidentID, "ordinal": ordinal,
				"snapshot_id": rows[start].SnapshotID,
			}, 32),
			Ordinal: ordinal, DetectedAt: rows[detected].ObservedAt,
			OnsetAt: rows[start].ObservedAt, PeakAt: rows[peak].ObservedAt,
			TroughAt:         rows[trough].ObservedAt,
			ObservationEndAt: rows[end].ObservedAt,
			DurationState:    "lower_bound", RecoveryState: "ongoing",
			Milestones: []Milestone{
				{At: rows[start].ObservedAt, SnapshotID: rows[start].SnapshotID,
					Metric: "affected_asn_ratio", Value: rows[start].AffectedASNRatio,
					Precision: precision},
				{At: rows[peak].ObservedAt, SnapshotID: rows[peak].SnapshotID,
					Metric: "peak_affected_asn_ratio", Value: rows[peak].AffectedASNRatio,
					Precision: "five_minute_state"},
				{At: rows[trough].ObservedAt, SnapshotID: rows[trough].SnapshotID,
					Metric:    "trough_visible_prefix_vp_ratio",
					Value:     rows[trough].VisiblePrefixVPRatio,
					Precision: "five_minute_state"},
			},
		}
		if partial >= 0 {
			episode.PartialRecoveryAt = pointer(rows[partial].ObservedAt)
			episode.RecoveryState = "partially_recovered"
		}
		if full >= 0 {
			episode.FullRecoveryAt = pointer(rows[full].ObservedAt)
			episode.DurationState = "exact"
			episode.RecoveryState = "fully_recovered"
		} else if start == 0 {
			episode.DurationState = "interval"
		} else if rows[end].AffectedASNRatio < rows[peak].AffectedASNRatio {
			episode.RecoveryState = "recovering"
		}
		incident.Episodes = append(incident.Episodes, episode)
		if full >= 0 {
			index = end + 1
		} else {
			break
		}
	}
	if len(incident.Episodes) > 0 {
		first := incident.Episodes[0]
		last := incident.Episodes[len(incident.Episodes)-1]
		incident.OnsetAt = pointer(first.OnsetAt)
		incident.DetectedAt = pointer(first.DetectedAt)
		incident.PeakAt = pointer(first.PeakAt)
		incident.TroughAt = pointer(first.TroughAt)
		incident.PartialRecoveryAt = last.PartialRecoveryAt
		incident.FullRecoveryAt = last.FullRecoveryAt
		incident.DurationState = last.DurationState
		incident.RecoveryState = last.RecoveryState
	}
	return incident
}
