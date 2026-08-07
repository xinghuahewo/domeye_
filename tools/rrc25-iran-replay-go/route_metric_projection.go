package replay

import (
	"fmt"
	"sort"
)

const (
	RouteMetricProjectorName    = "domeye_route_metric_projector"
	RouteMetricProjectorVersion = "1.0.0"
)

type routeMetricFamilyValues [2]int64

func (value *routeMetricFamilyValues) add(afi uint8, delta int64) error {
	offset, err := afiOffset(afi)
	if err != nil {
		return err
	}
	value[offset] += delta
	if value[offset] < 0 {
		return fmt.Errorf("route metric population became negative")
	}
	return nil
}

func (value routeMetricFamilyValues) total() int64 {
	return value[0] + value[1]
}

type routeMetricPopulation struct {
	Baseline       routeMetricFamilyValues
	CohortVisible  routeMetricFamilyValues
	CurrentVisible routeMetricFamilyValues
	Announcements  routeMetricFamilyValues
	Withdrawals    routeMetricFamilyValues
}

func (value routeMetricPopulation) stateIsZero() bool {
	return value.Baseline.total() == 0 && value.CohortVisible.total() == 0 &&
		value.CurrentVisible.total() == 0
}

func (value routeMetricPopulation) flowIsZero() bool {
	return value.Announcements.total() == 0 && value.Withdrawals.total() == 0
}

type routeMetricCohort struct {
	OriginKnown bool
	OriginASN   uint32
}

// RouteMetricRow 是统一在线时序层的一行。国家与 collector 每槽稠密；ASN
// 使用首槽快照加变化点。缺少 ASN 变化点表示状态未变且本槽流量为真实零，
// 只有 metric_slot_5m 缺失才表示数据缺失。
type RouteMetricRow struct {
	SchemaVersion                   string `json:"schema_version"`
	StatePointUTC                   string `json:"state_point_utc"`
	SubjectType                     string `json:"subject_type"`
	SubjectID                       string `json:"subject_id"`
	CountryCode                     string `json:"country_code,omitempty"`
	SampleEncoding                  string `json:"sample_encoding"`
	BaselineRouteStateCountV4       int64  `json:"baseline_route_state_count_v4"`
	BaselineRouteStateCountV6       int64  `json:"baseline_route_state_count_v6"`
	CohortVisibleRouteStateCountV4  int64  `json:"cohort_visible_route_state_count_v4"`
	CohortVisibleRouteStateCountV6  int64  `json:"cohort_visible_route_state_count_v6"`
	CurrentVisibleRouteStateCountV4 int64  `json:"current_visible_route_state_count_v4"`
	CurrentVisibleRouteStateCountV6 int64  `json:"current_visible_route_state_count_v6"`
	AnnouncementCountV4             int64  `json:"announcement_count_v4"`
	AnnouncementCountV6             int64  `json:"announcement_count_v6"`
	WithdrawalCountV4               int64  `json:"withdrawal_count_v4"`
	WithdrawalCountV6               int64  `json:"withdrawal_count_v6"`
	CohortVisibilityStateV4         string `json:"cohort_visibility_state_v4"`
	CohortVisibilityStateV6         string `json:"cohort_visibility_state_v6"`
}

type RouteMetricSnapshot struct {
	StatePointUTC string
	Countries     []RouteMetricRow
	ASNs          []RouteMetricRow
	Collector     RouteMetricRow
}

type RouteMetricProjector struct {
	mapping     *GlobalCountryMapping
	cohort      map[RouteStateKey]routeMetricCohort
	countries   []routeMetricPopulation
	asns        map[uint32]*routeMetricPopulation
	collector   routeMetricPopulation
	touchedASNs map[uint32]struct{}
	firstSlot   bool
}

func NewRouteMetricProjectorFromSeed(
	state *RouteState,
	mapping *GlobalCountryMapping,
) (*RouteMetricProjector, error) {
	if state == nil || mapping == nil {
		return nil, fmt.Errorf("RouteState seed and mapping are required")
	}
	codes := mapping.CountryCodes()
	if len(codes) != 241 || codes[0] != UnknownCountryCode {
		return nil, fmt.Errorf("route metric country population is not the frozen 241 buckets")
	}
	projector := &RouteMetricProjector{
		mapping:     mapping,
		cohort:      make(map[RouteStateKey]routeMetricCohort, len(state.Routes)),
		countries:   make([]routeMetricPopulation, len(codes)),
		asns:        make(map[uint32]*routeMetricPopulation),
		touchedASNs: make(map[uint32]struct{}),
		firstSlot:   true,
	}
	for key, value := range state.Routes {
		if key.Collector != RouteStateCollectorRRC25 || !value.Visible {
			return nil, fmt.Errorf("RouteState slot-0000 is not a complete visible rrc25 seed")
		}
		cohort := routeMetricCohort{OriginKnown: value.OriginKnown, OriginASN: value.OriginASN}
		projector.cohort[key] = cohort
		countryID := projector.countryID(value)
		if err := projector.countries[countryID].Baseline.add(key.Route.AFI, 1); err != nil {
			return nil, err
		}
		if err := projector.countries[countryID].CohortVisible.add(key.Route.AFI, 1); err != nil {
			return nil, err
		}
		if err := projector.countries[countryID].CurrentVisible.add(key.Route.AFI, 1); err != nil {
			return nil, err
		}
		if err := projector.collector.Baseline.add(key.Route.AFI, 1); err != nil {
			return nil, err
		}
		if err := projector.collector.CohortVisible.add(key.Route.AFI, 1); err != nil {
			return nil, err
		}
		if err := projector.collector.CurrentVisible.add(key.Route.AFI, 1); err != nil {
			return nil, err
		}
		if value.OriginKnown {
			asn := projector.asn(value.OriginASN)
			if err := asn.Baseline.add(key.Route.AFI, 1); err != nil {
				return nil, err
			}
			if err := asn.CohortVisible.add(key.Route.AFI, 1); err != nil {
				return nil, err
			}
			if err := asn.CurrentVisible.add(key.Route.AFI, 1); err != nil {
				return nil, err
			}
		}
	}
	if projector.collector.Baseline.total() != int64(len(state.Routes)) ||
		projector.collector.CurrentVisible.total() != state.VisibleRouteCount {
		return nil, fmt.Errorf("RouteState seed metric population mismatch")
	}
	if err := projector.ValidateConservation(state); err != nil {
		return nil, err
	}
	return projector, nil
}

func (projector *RouteMetricProjector) asn(asn uint32) *routeMetricPopulation {
	value := projector.asns[asn]
	if value == nil {
		value = &routeMetricPopulation{}
		projector.asns[asn] = value
	}
	return value
}

func (projector *RouteMetricProjector) countryID(value RouteStateValue) uint16 {
	if !value.Visible || !value.OriginKnown {
		return 0
	}
	return projector.mapping.CountryID(value.OriginASN)
}

func (projector *RouteMetricProjector) adjustCurrent(
	key RouteStateKey,
	value RouteStateValue,
	delta int64,
) error {
	if !value.Visible {
		return nil
	}
	countryID := projector.countryID(value)
	if err := projector.countries[countryID].CurrentVisible.add(key.Route.AFI, delta); err != nil {
		return err
	}
	if err := projector.collector.CurrentVisible.add(key.Route.AFI, delta); err != nil {
		return err
	}
	if value.OriginKnown {
		if err := projector.asn(value.OriginASN).CurrentVisible.add(key.Route.AFI, delta); err != nil {
			return err
		}
		projector.touchedASNs[value.OriginASN] = struct{}{}
	}
	return nil
}

func cohortVisible(value RouteStateValue, cohort routeMetricCohort) bool {
	if !value.Visible || value.OriginKnown != cohort.OriginKnown {
		return false
	}
	return !cohort.OriginKnown || value.OriginASN == cohort.OriginASN
}

func (projector *RouteMetricProjector) adjustCohort(
	key RouteStateKey,
	cohort routeMetricCohort,
	delta int64,
) error {
	countryID := uint16(0)
	if cohort.OriginKnown {
		countryID = projector.mapping.CountryID(cohort.OriginASN)
	}
	if err := projector.countries[countryID].CohortVisible.add(key.Route.AFI, delta); err != nil {
		return err
	}
	if err := projector.collector.CohortVisible.add(key.Route.AFI, delta); err != nil {
		return err
	}
	if cohort.OriginKnown {
		if err := projector.asn(cohort.OriginASN).CohortVisible.add(key.Route.AFI, delta); err != nil {
			return err
		}
		projector.touchedASNs[cohort.OriginASN] = struct{}{}
	}
	return nil
}

func (projector *RouteMetricProjector) recordFlow(
	event routeStateEvent,
	previous RouteStateValue,
	previousExists bool,
	current RouteStateValue,
) error {
	var attributed RouteStateValue
	switch event.Action {
	case actionAnnounce:
		attributed = current
	case actionWithdraw:
		if previousExists && previous.Visible {
			attributed = previous
		}
	default:
		return fmt.Errorf("route metric projector only accepts UPDATE actions")
	}
	countryID := projector.countryID(attributed)
	var countryFlow *routeMetricFamilyValues
	var collectorFlow *routeMetricFamilyValues
	if event.Action == actionAnnounce {
		countryFlow = &projector.countries[countryID].Announcements
		collectorFlow = &projector.collector.Announcements
	} else {
		countryFlow = &projector.countries[countryID].Withdrawals
		collectorFlow = &projector.collector.Withdrawals
	}
	if err := countryFlow.add(event.Key.Route.AFI, 1); err != nil {
		return err
	}
	if err := collectorFlow.add(event.Key.Route.AFI, 1); err != nil {
		return err
	}
	if attributed.Visible && attributed.OriginKnown {
		asn := projector.asn(attributed.OriginASN)
		if event.Action == actionAnnounce {
			if err := asn.Announcements.add(event.Key.Route.AFI, 1); err != nil {
				return err
			}
		} else if err := asn.Withdrawals.add(event.Key.Route.AFI, 1); err != nil {
			return err
		}
		projector.touchedASNs[attributed.OriginASN] = struct{}{}
	}
	return nil
}

// Apply 先让唯一 RouteState 提交事件，再根据返回的前后值更新派生计数。
func (projector *RouteMetricProjector) Apply(
	state *RouteState,
	event routeStateEvent,
) error {
	if state == nil {
		return fmt.Errorf("RouteState is required")
	}
	transition, err := state.ApplyWithTransition(event)
	if err != nil {
		return err
	}
	if transition.PreviousExists {
		if err := projector.adjustCurrent(event.Key, transition.Previous, -1); err != nil {
			return err
		}
	}
	if err := projector.adjustCurrent(event.Key, transition.Current, 1); err != nil {
		return err
	}
	if cohort, exists := projector.cohort[event.Key]; exists {
		before := transition.PreviousExists && cohortVisible(transition.Previous, cohort)
		after := cohortVisible(transition.Current, cohort)
		if before != after {
			delta := int64(-1)
			if after {
				delta = 1
			}
			if err := projector.adjustCohort(event.Key, cohort, delta); err != nil {
				return err
			}
		}
	}
	return projector.recordFlow(
		event, transition.Previous, transition.PreviousExists, transition.Current,
	)
}

func metricVisibilityState(baseline int64) string {
	if baseline == 0 {
		return "not_applicable"
	}
	return "observed"
}

func routeMetricRow(
	statePoint string,
	subjectType string,
	subjectID string,
	countryCode string,
	encoding string,
	value routeMetricPopulation,
) RouteMetricRow {
	return RouteMetricRow{
		SchemaVersion: "rrc25-route-metric-row/v1", StatePointUTC: statePoint,
		SubjectType: subjectType, SubjectID: subjectID, CountryCode: countryCode,
		SampleEncoding:            encoding,
		BaselineRouteStateCountV4: value.Baseline[0], BaselineRouteStateCountV6: value.Baseline[1],
		CohortVisibleRouteStateCountV4: value.CohortVisible[0], CohortVisibleRouteStateCountV6: value.CohortVisible[1],
		CurrentVisibleRouteStateCountV4: value.CurrentVisible[0], CurrentVisibleRouteStateCountV6: value.CurrentVisible[1],
		AnnouncementCountV4: value.Announcements[0], AnnouncementCountV6: value.Announcements[1],
		WithdrawalCountV4: value.Withdrawals[0], WithdrawalCountV6: value.Withdrawals[1],
		CohortVisibilityStateV4: metricVisibilityState(value.Baseline[0]),
		CohortVisibilityStateV6: metricVisibilityState(value.Baseline[1]),
	}
}

func (projector *RouteMetricProjector) resetFlows() {
	projector.collector.Announcements = routeMetricFamilyValues{}
	projector.collector.Withdrawals = routeMetricFamilyValues{}
	for index := range projector.countries {
		projector.countries[index].Announcements = routeMetricFamilyValues{}
		projector.countries[index].Withdrawals = routeMetricFamilyValues{}
	}
	for asn := range projector.touchedASNs {
		value := projector.asns[asn]
		value.Announcements = routeMetricFamilyValues{}
		value.Withdrawals = routeMetricFamilyValues{}
	}
	projector.touchedASNs = make(map[uint32]struct{})
}

func (projector *RouteMetricProjector) Snapshot(
	state *RouteState,
	statePoint string,
) (RouteMetricSnapshot, error) {
	if err := projector.ValidateConservation(state); err != nil {
		return RouteMetricSnapshot{}, err
	}
	codes := projector.mapping.CountryCodes()
	countries := make([]RouteMetricRow, 0, len(codes))
	for countryID, code := range codes {
		countries = append(countries, routeMetricRow(
			statePoint, "country", code, code, "dense_slot",
			projector.countries[countryID],
		))
	}
	asnValues := make([]uint32, 0)
	if projector.firstSlot {
		asnValues = make([]uint32, 0, len(projector.asns))
		for asn := range projector.asns {
			asnValues = append(asnValues, asn)
		}
	} else {
		asnValues = make([]uint32, 0, len(projector.touchedASNs))
		for asn := range projector.touchedASNs {
			asnValues = append(asnValues, asn)
		}
	}
	sort.Slice(asnValues, func(i, j int) bool { return asnValues[i] < asnValues[j] })
	asns := make([]RouteMetricRow, 0, len(asnValues))
	for _, asn := range asnValues {
		value := *projector.asns[asn]
		countryCode := projector.mapping.CountryCode(projector.mapping.CountryID(asn))
		asns = append(asns, routeMetricRow(
			statePoint, "asn", fmt.Sprintf("AS%d", asn), countryCode,
			"change_point", value,
		))
	}
	snapshot := RouteMetricSnapshot{
		StatePointUTC: statePoint, Countries: countries, ASNs: asns,
		Collector: routeMetricRow(
			statePoint, "collector", "rrc25", "", "dense_slot", projector.collector,
		),
	}
	projector.firstSlot = false
	projector.resetFlows()
	return snapshot, nil
}

func (projector *RouteMetricProjector) ValidateConservation(state *RouteState) error {
	if state == nil || len(projector.countries) != 241 {
		return fmt.Errorf("route metric conservation inputs are invalid")
	}
	var baseline routeMetricFamilyValues
	var cohort routeMetricFamilyValues
	var current routeMetricFamilyValues
	var announcements routeMetricFamilyValues
	var withdrawals routeMetricFamilyValues
	for _, country := range projector.countries {
		for index := 0; index < 2; index++ {
			if country.CohortVisible[index] > country.Baseline[index] {
				return fmt.Errorf("country cohort visibility exceeds baseline")
			}
			baseline[index] += country.Baseline[index]
			cohort[index] += country.CohortVisible[index]
			current[index] += country.CurrentVisible[index]
			announcements[index] += country.Announcements[index]
			withdrawals[index] += country.Withdrawals[index]
		}
	}
	if baseline != projector.collector.Baseline || cohort != projector.collector.CohortVisible ||
		current != projector.collector.CurrentVisible ||
		announcements != projector.collector.Announcements ||
		withdrawals != projector.collector.Withdrawals ||
		current.total() != state.VisibleRouteCount {
		return fmt.Errorf("route metric country/collector/RouteState conservation mismatch")
	}
	for _, value := range projector.asns {
		for index := 0; index < 2; index++ {
			if value.CohortVisible[index] > value.Baseline[index] {
				return fmt.Errorf("ASN cohort visibility exceeds baseline")
			}
		}
	}
	return nil
}
