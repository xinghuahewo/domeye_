package replay

import (
	"fmt"
	"sort"
)

const GlobalWindowSummaryVersion = "rrc25-global-country-summary-5m/v1"

type GlobalWindowFamilySummary struct {
	BaselinePrefixVP int64 `json:"baseline_prefix_vp"`
	VisiblePrefixVP  int64 `json:"visible_prefix_vp"`
	CurrentPrefixVP  int64 `json:"current_prefix_vp"`
	BaselineASNCount int   `json:"baseline_origin_asn_count"`
	VisibleASNCount  int   `json:"visible_origin_asn_count"`
	AffectedASNCount int   `json:"affected_origin_asn_count"`
}

type GlobalCountrySummary struct {
	SchemaVersion          string                    `json:"schema_version"`
	SnapshotID             string                    `json:"snapshot_id"`
	CollectorID            string                    `json:"collector_id"`
	CountryCode            string                    `json:"country_code"`
	MappingVersion         string                    `json:"mapping_version"`
	CohortID               string                    `json:"cohort_id"`
	SeedObservedAt         string                    `json:"seed_observed_at"`
	ObservedAt             string                    `json:"observed_at"`
	SlotStartUTC           string                    `json:"slot_start_utc"`
	SlotEndExclusiveUTC    string                    `json:"slot_end_exclusive_utc"`
	ContinuityState        string                    `json:"continuity_state"`
	BaselinePrefixVP       int64                     `json:"baseline_prefix_vp"`
	VisiblePrefixVP        int64                     `json:"visible_prefix_vp"`
	CurrentPrefixVP        int64                     `json:"current_prefix_vp"`
	VisiblePrefixVPRatio   float64                   `json:"visible_prefix_vp_ratio"`
	BaselineASNCount       int                       `json:"baseline_origin_asn_count"`
	VisibleOriginASNCount  int                       `json:"visible_origin_asn_count"`
	AffectedASNCount       int                       `json:"affected_origin_asn_count"`
	FullyInvisibleASNCount int                       `json:"fully_invisible_origin_asn_count"`
	IPv4                   GlobalWindowFamilySummary `json:"ipv4"`
	IPv6                   GlobalWindowFamilySummary `json:"ipv6"`
	CollectorUpdateCounts  UpdateCounts              `json:"collector_update_counts"`
	CountryUpdateCounts    UpdateCounts              `json:"country_update_counts"`
	GlobalStateDigest      string                    `json:"global_state_digest"`
}

type compactASNState struct {
	allVisible   bool
	allInvisible bool
}

type compactCountryState struct {
	byASN  map[uint32]compactASNState
	family [2]GlobalWindowFamilySummary
}

// SnapshotGlobalCountrySummaries 只物化国家级计数，不复制每个五分钟点的完整
// ASN 列表。事件窗口需要 ASN 明细时，应从最近 checkpoint 加 RouteDelta 定向
// 重建；该函数不会把 8 万余 ASN 的状态数组重复写入每个槽。
func (state *GlobalReplayState) SnapshotGlobalCountrySummaries(
	observedAt string,
	slotStart string,
	slotEnd string,
	activity *GlobalSlotActivity,
) ([]GlobalCountrySummary, GlobalConservation, error) {
	if state == nil || state.Mapping == nil {
		return nil, GlobalConservation{}, fmt.Errorf("global replay state is required")
	}
	if activity == nil {
		activity = NewGlobalSlotActivity()
	}
	compact := make(map[uint16]*compactCountryState)
	for key, counter := range state.Counters {
		if counter.Total <= 0 || counter.Visible < 0 || counter.Visible > counter.Total {
			return nil, GlobalConservation{}, fmt.Errorf(
				"invalid ASN counter country=%d asn=%d afi=%d total=%d visible=%d",
				key.CountryID, key.ASN, key.AFI, counter.Total, counter.Visible,
			)
		}
		value := compact[key.CountryID]
		if value == nil {
			value = &compactCountryState{byASN: make(map[uint32]compactASNState)}
			compact[key.CountryID] = value
		}
		offset, err := afiOffset(key.AFI)
		if err != nil {
			return nil, GlobalConservation{}, err
		}
		family := &value.family[offset]
		family.BaselineASNCount++
		if counter.Visible > 0 {
			family.VisibleASNCount++
		}
		if counter.Visible < counter.Total {
			family.AffectedASNCount++
		}
		asn, exists := value.byASN[key.ASN]
		if !exists {
			asn = compactASNState{allVisible: true, allInvisible: true}
		}
		if counter.Visible != counter.Total {
			asn.allVisible = false
		}
		if counter.Visible != 0 {
			asn.allInvisible = false
		}
		value.byASN[key.ASN] = asn
	}

	countryIDs := make([]int, 0, len(state.Countries))
	for countryID, population := range state.Countries {
		if population.BaselinePrefixVP > 0 {
			countryIDs = append(countryIDs, int(countryID))
		}
	}
	sort.Slice(countryIDs, func(i, j int) bool {
		return state.Mapping.CountryCode(uint16(countryIDs[i])) <
			state.Mapping.CountryCode(uint16(countryIDs[j]))
	})
	seedObservedAt := state.SeedObservedAt
	if seedObservedAt == "" {
		seedObservedAt = CatchUpStartUTC
	}
	stateDigest := state.StateDigest.Hex()
	result := make([]GlobalCountrySummary, 0, len(countryIDs))
	for _, rawID := range countryIDs {
		countryID := uint16(rawID)
		population := state.country(countryID)
		value := compact[countryID]
		if value == nil {
			value = &compactCountryState{byASN: make(map[uint32]compactASNState)}
		}
		value.family[0].BaselinePrefixVP = population.BaselineByAFI[0]
		value.family[0].VisiblePrefixVP = population.VisibleByAFI[0]
		value.family[0].CurrentPrefixVP = population.CurrentByAFI[0]
		value.family[1].BaselinePrefixVP = population.BaselineByAFI[1]
		value.family[1].VisiblePrefixVP = population.VisibleByAFI[1]
		value.family[1].CurrentPrefixVP = population.CurrentByAFI[1]
		visibleASNs := 0
		affectedASNs := 0
		fullyInvisibleASNs := 0
		for _, asn := range value.byASN {
			if asn.allVisible {
				visibleASNs++
				continue
			}
			affectedASNs++
			if asn.allInvisible {
				fullyInvisibleASNs++
			} else {
				visibleASNs++
			}
		}
		code := state.Mapping.CountryCode(countryID)
		cohortID := state.CohortID(countryID)
		ratio := float64(population.VisiblePrefixVP) /
			float64(population.BaselinePrefixVP)
		snapshotID := stableID("global_summary_v1_", map[string]any{
			"country_code":      code,
			"cohort_id":         cohortID,
			"observed_at":       observedAt,
			"visible_prefix_vp": population.VisiblePrefixVP,
			"state_digest":      stateDigest,
		}, 32)
		result = append(result, GlobalCountrySummary{
			SchemaVersion: GlobalWindowSummaryVersion,
			SnapshotID:    snapshotID, CollectorID: "rrc25",
			CountryCode: code, MappingVersion: state.Mapping.MappingVersion,
			CohortID: cohortID, SeedObservedAt: seedObservedAt,
			ObservedAt: observedAt, SlotStartUTC: slotStart,
			SlotEndExclusiveUTC: slotEnd, ContinuityState: "continuous",
			BaselinePrefixVP:       population.BaselinePrefixVP,
			VisiblePrefixVP:        population.VisiblePrefixVP,
			CurrentPrefixVP:        population.CurrentPrefixVP,
			VisiblePrefixVPRatio:   ratio,
			BaselineASNCount:       len(value.byASN),
			VisibleOriginASNCount:  visibleASNs,
			AffectedASNCount:       affectedASNs,
			FullyInvisibleASNCount: fullyInvisibleASNs,
			IPv4:                   value.family[0], IPv6: value.family[1],
			CollectorUpdateCounts: activity.Global,
			CountryUpdateCounts:   activity.ByCountry[countryID],
			GlobalStateDigest:     stateDigest,
		})
	}
	conservation, err := state.ValidateConservationFast()
	if err != nil {
		return nil, conservation, err
	}
	return result, conservation, nil
}
