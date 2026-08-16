package replay

import (
	"bufio"
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/netip"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"sync"
	"time"
)

const (
	EventMetricStoreVersion     = "rrc25-event-metric-store/v1"
	EventMetricVersion          = "rrc25-event-metric/v1"
	EventMetricSeriesVersion    = "rrc25-event-metric-series/v1"
	EventMetricPrefixVersion    = "rrc25-event-prefix-state/v1"
	EventMetricASNVersion       = "rrc25-event-asn-state/v1"
	EventMetricNewPrefixVersion = "rrc25-event-new-prefix-state/v1"
	EventMetricProjectorName    = "domeye_country_outage_event_metric_projector"
	EventMetricProjectorVersion = "1.0.0"

	eventPrefixNormal   = "normal"
	eventPrefixPartial  = "partially_interrupted"
	eventPrefixComplete = "completely_interrupted"
	eventPrefixUnknown  = "unknown"

	eventASNNormal           = "normal"
	eventASNAffected         = "affected"
	eventASNRouteInterrupted = "route_interrupted"
	eventASNUnknown          = "unknown"
)

type EventMetricStoreConfig struct {
	RouteEventRoot              string
	RouteStateRoot              string
	RawRoot                     string
	SelectionPath               string
	EventCohortRoot             string
	PeerSessionRoot             string
	CompatibleMappingPath       string
	RevisedMappingPath          string
	LifecycleSnapshotPath       string
	Output                      string
	RouteEventImplementationID  string
	RouteStateImplementationID  string
	EventCohortImplementationID string
	PeerSessionImplementationID string
	ImplementationID            string
	Workers                     int
	Resume                      bool
	Progress                    func(string)
}

func (config EventMetricStoreConfig) progress(message string) {
	if config.Progress != nil {
		config.Progress(message)
	}
}

type eventMetricPrefixKey struct {
	AFI    uint8
	Prefix netip.Prefix
}

type eventMetricDirectionKey struct {
	eventMetricPrefixKey
	PeerASN uint32
}

type eventMetricDirectionReference struct {
	EventIndex  int
	MemberIndex int
}

type eventMetricRouteValue struct {
	Visible     bool
	OriginKnown bool
	OriginASN   uint32
}

type eventMetricMemberState struct {
	CohortMemberID         string
	Prefix                 eventMetricPrefixKey
	CountryOriginASNs      []uint32
	ExpectedDirectionCount int
	VisibleDirectionCount  int
	Classification         string
	LastEmitted            EventMetricPrefixStateRow
}

type eventMetricASNState struct {
	ASN              uint32
	TotalPrefixes    int
	NormalPrefixes   int
	PartialPrefixes  int
	CompletePrefixes int
	UnknownPrefixes  int
	Classification   string
	LastEmitted      EventMetricASNStateRow
}

type eventMetricNewPrefixState struct {
	Prefix            eventMetricPrefixKey
	FirstObservedSlot int
	Visible           bool
	LastEmitted       EventMetricNewPrefixStateRow
}

type eventMetricEventState struct {
	EventMetricID       string
	Binding             EventCohortBinding
	CountryID           uint16
	Cohort              EventCohortManifest
	WindowStartSlot     int
	ProjectionEndSlot   int
	Members             []eventMetricMemberState
	MemberByPrefix      map[eventMetricPrefixKey]int
	ASNs                map[uint32]*eventMetricASNState
	NewPrefixes         map[eventMetricPrefixKey]*eventMetricNewPrefixState
	FixedIPv4Coverage   *prefixCoverage
	FixedIPv6Coverage   *prefixCoverage
	NewIPv4Current      *prefixCoverage
	NewIPv6Current      *prefixCoverage
	NewIPv4Cumulative   *prefixCoverage
	NewIPv6Cumulative   *prefixCoverage
	NormalPrefixes      int
	PartialPrefixes     int
	CompletePrefixes    int
	UnknownPrefixes     int
	VisibleDirections   int
	InvisibleDirections int
	NormalASNs          int
	AffectedASNs        int
	InterruptedASNs     int
	UnknownASNs         int
	NewVisibleIPv4      int
	NewVisibleIPv6      int
	NewCumulativeIPv4   int
	NewCumulativeIPv6   int
	Started             bool
}

type eventMetricProjector struct {
	Mapping              *GlobalCountryMapping
	Events               []*eventMetricEventState
	DirectionReferences  map[eventMetricDirectionKey][]eventMetricDirectionReference
	DirectionRouteCounts map[eventMetricDirectionKey]int
	Routes               map[RouteStateKey]eventMetricRouteValue
	EventsByCountry      map[uint16][]int
	CountryPrefixCounts  map[uint16]map[eventMetricPrefixKey]int
	CurrentSlot          int
}

type eventMetricDefinition struct {
	EventMetricID string
	Binding       EventCohortBinding
	Cohort        EventCohortManifest
	Members       []EventCohortMember
}

type EventMetricSeriesValues struct {
	FixedPrefixCount                 int    `json:"fixed_prefix_count"`
	NormalPrefixCount                int    `json:"normal_prefix_count"`
	InterruptedPrefixCount           int    `json:"interrupted_prefix_count"`
	PartiallyInterruptedPrefixCount  int    `json:"partially_interrupted_prefix_count"`
	CompletelyInterruptedPrefixCount int    `json:"completely_interrupted_prefix_count"`
	UnknownPrefixCount               int    `json:"unknown_prefix_count"`
	ExpectedDirectionCount           int    `json:"expected_direction_count"`
	VisibleDirectionCount            int    `json:"visible_direction_count"`
	InvisibleDirectionCount          int    `json:"invisible_direction_count"`
	UnknownDirectionCount            int    `json:"unknown_direction_count"`
	FixedASNCount                    int    `json:"fixed_asn_count"`
	NormalASNCount                   int    `json:"normal_asn_count"`
	AffectedASNCount                 int    `json:"affected_asn_count"`
	RouteInterruptedASNCount         int    `json:"route_interrupted_asn_count"`
	UnknownASNCount                  int    `json:"unknown_asn_count"`
	FixedVisibleIPv4AddressCount     uint64 `json:"fixed_visible_ipv4_address_count"`
	FixedVisibleIPv6Slash48Count     uint64 `json:"fixed_visible_ipv6_slash48_count"`
	NewVisibleIPv4PrefixCount        int    `json:"new_visible_ipv4_prefix_count"`
	NewVisibleIPv6PrefixCount        int    `json:"new_visible_ipv6_prefix_count"`
	NewVisibleIPv4AddressCount       uint64 `json:"new_visible_ipv4_address_count"`
	NewVisibleIPv6Slash48Count       uint64 `json:"new_visible_ipv6_slash48_count"`
	NewCumulativeIPv4PrefixCount     int    `json:"new_cumulative_ipv4_prefix_count"`
	NewCumulativeIPv6PrefixCount     int    `json:"new_cumulative_ipv6_prefix_count"`
	NewCumulativeIPv4AddressCount    uint64 `json:"new_cumulative_ipv4_address_count"`
	NewCumulativeIPv6Slash48Count    uint64 `json:"new_cumulative_ipv6_slash48_count"`
}

type EventMetricSeriesRow struct {
	SchemaVersion string                   `json:"schema_version"`
	EventMetricID string                   `json:"event_metric_id"`
	CohortID      string                   `json:"cohort_id"`
	CountryCode   string                   `json:"country_code"`
	StateSlot     int                      `json:"state_slot"`
	StatePointUTC string                   `json:"state_point_utc"`
	ValueState    string                   `json:"value_state"`
	MissingReason *string                  `json:"missing_reason"`
	Values        *EventMetricSeriesValues `json:"values"`
}

type EventMetricPrefixStateRow struct {
	SchemaVersion           string `json:"schema_version"`
	RecordKind              string `json:"record_kind"`
	EventMetricID           string `json:"event_metric_id"`
	CohortID                string `json:"cohort_id"`
	CohortMemberID          string `json:"cohort_member_id"`
	StateSlot               int    `json:"state_slot"`
	StatePointUTC           string `json:"state_point_utc"`
	Prefix                  string `json:"prefix"`
	AddressFamily           string `json:"address_family"`
	Classification          string `json:"classification"`
	ExpectedDirectionCount  int    `json:"expected_direction_count"`
	VisibleDirectionCount   int    `json:"visible_direction_count"`
	InvisibleDirectionCount int    `json:"invisible_direction_count"`
	UnknownDirectionCount   int    `json:"unknown_direction_count"`
}

type EventMetricASNStateRow struct {
	SchemaVersion       string `json:"schema_version"`
	RecordKind          string `json:"record_kind"`
	EventMetricID       string `json:"event_metric_id"`
	CohortID            string `json:"cohort_id"`
	StateSlot           int    `json:"state_slot"`
	StatePointUTC       string `json:"state_point_utc"`
	ASN                 uint32 `json:"asn"`
	Classification      string `json:"classification"`
	FixedPrefixCount    int    `json:"fixed_prefix_count"`
	NormalPrefixCount   int    `json:"normal_prefix_count"`
	PartialPrefixCount  int    `json:"partial_prefix_count"`
	CompletePrefixCount int    `json:"complete_prefix_count"`
	UnknownPrefixCount  int    `json:"unknown_prefix_count"`
}

type EventMetricNewPrefixStateRow struct {
	SchemaVersion     string `json:"schema_version"`
	RecordKind        string `json:"record_kind"`
	EventMetricID     string `json:"event_metric_id"`
	CohortID          string `json:"cohort_id"`
	StateSlot         int    `json:"state_slot"`
	StatePointUTC     string `json:"state_point_utc"`
	FirstObservedSlot int    `json:"first_observed_slot"`
	Prefix            string `json:"prefix"`
	AddressFamily     string `json:"address_family"`
	VisibilityState   string `json:"visibility_state"`
}

func eventMetricPrefixFromRoute(key RouteStateKey) eventMetricPrefixKey {
	return eventMetricPrefixKey{AFI: key.Route.AFI, Prefix: key.Route.Prefix.Masked()}
}

func eventMetricDirectionFromRoute(key RouteStateKey) eventMetricDirectionKey {
	return eventMetricDirectionKey{
		eventMetricPrefixKey: eventMetricPrefixFromRoute(key),
		PeerASN:              key.Route.PeerASN,
	}
}

func eventMetricAFIName(afi uint8) string {
	if afi == 4 {
		return "ipv4"
	}
	return "ipv6"
}

func eventMetricStatePoint(slot int) string {
	start, _ := time.Parse(time.RFC3339, RouteEventWindowStartUTC)
	return start.Add(time.Duration(slot) * 5 * time.Minute).Format(time.RFC3339)
}

func eventMetricSlot(value string, allowEnd bool) (int, error) {
	parsed, err := time.Parse(time.RFC3339, value)
	if err != nil || parsed.Format(time.RFC3339) != value {
		return 0, fmt.Errorf("invalid event metric state point %q", value)
	}
	start, _ := time.Parse(time.RFC3339, RouteEventWindowStartUTC)
	delta := parsed.Sub(start)
	if delta < 0 || delta%(5*time.Minute) != 0 {
		return 0, fmt.Errorf("event metric state point is outside aligned 224-310 points")
	}
	slot := int(delta / (5 * time.Minute))
	maximum := RouteStateFinalSlot - 1
	if allowEnd {
		maximum = RouteStateFinalSlot
	}
	if slot < 0 || slot > maximum {
		return 0, fmt.Errorf("event metric slot is outside 224-310")
	}
	return slot, nil
}

func eventMetricPrefixClass(visible, expected int) (string, error) {
	if expected < 1 || visible < 0 || visible > expected {
		return "", fmt.Errorf("invalid event prefix direction population")
	}
	if visible == expected {
		return eventPrefixNormal, nil
	}
	if visible == 0 {
		return eventPrefixComplete, nil
	}
	return eventPrefixPartial, nil
}

func eventMetricASNClass(state *eventMetricASNState) (string, error) {
	if state == nil || state.TotalPrefixes < 1 || state.NormalPrefixes < 0 ||
		state.PartialPrefixes < 0 || state.CompletePrefixes < 0 || state.UnknownPrefixes < 0 ||
		state.NormalPrefixes+state.PartialPrefixes+state.CompletePrefixes+state.UnknownPrefixes != state.TotalPrefixes {
		return "", fmt.Errorf("invalid event ASN prefix population")
	}
	if state.UnknownPrefixes > 0 {
		return eventASNUnknown, nil
	}
	if state.CompletePrefixes == state.TotalPrefixes {
		return eventASNRouteInterrupted, nil
	}
	if state.PartialPrefixes+state.CompletePrefixes > 0 {
		return eventASNAffected, nil
	}
	return eventASNNormal, nil
}

func newEventMetricProjector(
	mapping *GlobalCountryMapping,
	definitions []eventMetricDefinition,
) (*eventMetricProjector, error) {
	if mapping == nil || len(definitions) == 0 {
		return nil, fmt.Errorf("event metric mapping and definitions are required")
	}
	projector := &eventMetricProjector{
		Mapping: mapping, Events: make([]*eventMetricEventState, 0, len(definitions)),
		DirectionReferences:  make(map[eventMetricDirectionKey][]eventMetricDirectionReference),
		DirectionRouteCounts: make(map[eventMetricDirectionKey]int),
		Routes:               make(map[RouteStateKey]eventMetricRouteValue),
		EventsByCountry:      make(map[uint16][]int),
		CountryPrefixCounts:  make(map[uint16]map[eventMetricPrefixKey]int),
	}
	seen := make(map[string]struct{}, len(definitions))
	for _, definition := range definitions {
		if definition.EventMetricID == "" || definition.Binding.CohortID != definition.Cohort.CohortID ||
			definition.Binding.CountryCode != definition.Cohort.CountryCode || len(definition.Members) == 0 {
			return nil, fmt.Errorf("event metric definition identity mismatch")
		}
		if _, exists := seen[definition.EventMetricID]; exists {
			return nil, fmt.Errorf("duplicate event metric definition")
		}
		seen[definition.EventMetricID] = struct{}{}
		countryID, exists := mapping.IDForCode(definition.Binding.CountryCode)
		if !exists || definition.Binding.CountryCode == UnknownCountryCode {
			return nil, fmt.Errorf("event metric country mapping is unavailable")
		}
		windowStart, err := eventMetricSlot(definition.Binding.WindowStartUTC, true)
		if err != nil {
			return nil, err
		}
		projectionEnd, err := eventMetricSlot(definition.Binding.ProjectionEndStatePointUTC, true)
		if err != nil || projectionEnd < windowStart {
			return nil, fmt.Errorf("event metric projection window is invalid")
		}
		fixed4, _ := newPrefixCoverage(4)
		fixed6, _ := newPrefixCoverage(6)
		new4, _ := newPrefixCoverage(4)
		new6, _ := newPrefixCoverage(6)
		cumulative4, _ := newPrefixCoverage(4)
		cumulative6, _ := newPrefixCoverage(6)
		event := &eventMetricEventState{
			EventMetricID: definition.EventMetricID, Binding: definition.Binding,
			CountryID: countryID, Cohort: definition.Cohort,
			WindowStartSlot: windowStart, ProjectionEndSlot: projectionEnd,
			Members:           make([]eventMetricMemberState, 0, len(definition.Members)),
			MemberByPrefix:    make(map[eventMetricPrefixKey]int, len(definition.Members)),
			ASNs:              make(map[uint32]*eventMetricASNState),
			NewPrefixes:       make(map[eventMetricPrefixKey]*eventMetricNewPrefixState),
			FixedIPv4Coverage: fixed4, FixedIPv6Coverage: fixed6,
			NewIPv4Current: new4, NewIPv6Current: new6,
			NewIPv4Cumulative: cumulative4, NewIPv6Cumulative: cumulative6,
		}
		eventIndex := len(projector.Events)
		for _, source := range definition.Members {
			prefix, err := netip.ParsePrefix(source.Prefix)
			if err != nil || prefix.String() != source.Prefix ||
				(source.AddressFamily != "ipv4" && source.AddressFamily != "ipv6") ||
				len(source.CountryOriginASNs) == 0 || len(source.ExpectedDirections) == 0 ||
				source.ExpectedDirectionCount != len(source.ExpectedDirections) {
				return nil, fmt.Errorf("event metric cohort member is invalid")
			}
			afi := uint8(4)
			if source.AddressFamily == "ipv6" {
				afi = 6
			}
			key := eventMetricPrefixKey{AFI: afi, Prefix: prefix.Masked()}
			if _, exists := event.MemberByPrefix[key]; exists {
				return nil, fmt.Errorf("duplicate event metric cohort prefix")
			}
			member := eventMetricMemberState{
				CohortMemberID: source.CohortMemberID, Prefix: key,
				CountryOriginASNs:      append([]uint32(nil), source.CountryOriginASNs...),
				ExpectedDirectionCount: len(source.ExpectedDirections),
				Classification:         eventPrefixComplete,
			}
			memberIndex := len(event.Members)
			event.MemberByPrefix[key] = memberIndex
			event.Members = append(event.Members, member)
			event.CompletePrefixes++
			event.InvisibleDirections += len(source.ExpectedDirections)
			seenDirections := make(map[uint32]struct{}, len(source.ExpectedDirections))
			for _, direction := range source.ExpectedDirections {
				if _, exists := seenDirections[direction.PeerASN]; exists || direction.RouteObservationCount < 1 {
					return nil, fmt.Errorf("event metric direction population is invalid")
				}
				seenDirections[direction.PeerASN] = struct{}{}
				directionKey := eventMetricDirectionKey{eventMetricPrefixKey: key, PeerASN: direction.PeerASN}
				projector.DirectionReferences[directionKey] = append(
					projector.DirectionReferences[directionKey],
					eventMetricDirectionReference{EventIndex: eventIndex, MemberIndex: memberIndex},
				)
			}
			for _, asn := range member.CountryOriginASNs {
				state := event.ASNs[asn]
				if state == nil {
					state = &eventMetricASNState{ASN: asn}
					event.ASNs[asn] = state
				}
				state.TotalPrefixes++
				state.CompletePrefixes++
			}
		}
		if len(event.Members) != int(definition.Cohort.MemberCount) ||
			event.InvisibleDirections != int(definition.Cohort.ExpectedDirectionRelationCount) {
			return nil, fmt.Errorf("event metric cohort aggregate population mismatch")
		}
		for _, state := range event.ASNs {
			classification, err := eventMetricASNClass(state)
			if err != nil {
				return nil, err
			}
			state.Classification = classification
			event.InterruptedASNs++
		}
		projector.Events = append(projector.Events, event)
		projector.EventsByCountry[countryID] = append(projector.EventsByCountry[countryID], eventIndex)
		if projector.CountryPrefixCounts[countryID] == nil {
			projector.CountryPrefixCounts[countryID] = make(map[eventMetricPrefixKey]int)
		}
	}
	return projector, nil
}

func (projector *eventMetricProjector) setMemberDirectionVisible(
	eventIndex, memberIndex int,
	visible bool,
) error {
	event := projector.Events[eventIndex]
	member := &event.Members[memberIndex]
	oldClass := member.Classification
	oldVisible := member.VisibleDirectionCount > 0
	if visible {
		member.VisibleDirectionCount++
		event.VisibleDirections++
		event.InvisibleDirections--
	} else {
		member.VisibleDirectionCount--
		event.VisibleDirections--
		event.InvisibleDirections++
	}
	newClass, err := eventMetricPrefixClass(member.VisibleDirectionCount, member.ExpectedDirectionCount)
	if err != nil || event.InvisibleDirections < 0 {
		return fmt.Errorf("event metric direction adjustment underflow")
	}
	if oldClass == newClass {
		return nil
	}
	if err := event.adjustPrefixClass(oldClass, -1); err != nil {
		return err
	}
	if err := event.adjustPrefixClass(newClass, 1); err != nil {
		return err
	}
	member.Classification = newClass
	newVisible := member.VisibleDirectionCount > 0
	if oldVisible != newVisible {
		coverage := event.FixedIPv4Coverage
		if member.Prefix.AFI == 6 {
			coverage = event.FixedIPv6Coverage
		}
		if newVisible {
			err = coverage.Add(member.Prefix.Prefix)
		} else {
			err = coverage.Remove(member.Prefix.Prefix)
		}
		if err != nil {
			return err
		}
	}
	for _, asn := range member.CountryOriginASNs {
		state := event.ASNs[asn]
		if err := event.adjustASNCategory(state.Classification, -1); err != nil {
			return err
		}
		if err := adjustEventMetricASNPrefixClass(state, oldClass, -1); err != nil {
			return err
		}
		if err := adjustEventMetricASNPrefixClass(state, newClass, 1); err != nil {
			return err
		}
		state.Classification, err = eventMetricASNClass(state)
		if err != nil {
			return err
		}
		if err := event.adjustASNCategory(state.Classification, 1); err != nil {
			return err
		}
	}
	return nil
}

func (event *eventMetricEventState) adjustPrefixClass(classification string, delta int) error {
	target := (*int)(nil)
	switch classification {
	case eventPrefixNormal:
		target = &event.NormalPrefixes
	case eventPrefixPartial:
		target = &event.PartialPrefixes
	case eventPrefixComplete:
		target = &event.CompletePrefixes
	case eventPrefixUnknown:
		target = &event.UnknownPrefixes
	default:
		return fmt.Errorf("unknown event prefix classification")
	}
	*target += delta
	if *target < 0 {
		return fmt.Errorf("event prefix classification underflow")
	}
	return nil
}

func adjustEventMetricASNPrefixClass(state *eventMetricASNState, classification string, delta int) error {
	target := (*int)(nil)
	switch classification {
	case eventPrefixNormal:
		target = &state.NormalPrefixes
	case eventPrefixPartial:
		target = &state.PartialPrefixes
	case eventPrefixComplete:
		target = &state.CompletePrefixes
	case eventPrefixUnknown:
		target = &state.UnknownPrefixes
	default:
		return fmt.Errorf("unknown event ASN prefix classification")
	}
	*target += delta
	if *target < 0 {
		return fmt.Errorf("event ASN prefix population underflow")
	}
	return nil
}

func (event *eventMetricEventState) adjustASNCategory(classification string, delta int) error {
	target := (*int)(nil)
	switch classification {
	case eventASNNormal:
		target = &event.NormalASNs
	case eventASNAffected:
		target = &event.AffectedASNs
	case eventASNRouteInterrupted:
		target = &event.InterruptedASNs
	case eventASNUnknown:
		target = &event.UnknownASNs
	default:
		return fmt.Errorf("unknown event ASN classification")
	}
	*target += delta
	if *target < 0 {
		return fmt.Errorf("event ASN classification underflow")
	}
	return nil
}

func (projector *eventMetricProjector) adjustDirectionRoute(
	key eventMetricDirectionKey,
	delta int,
) error {
	current := projector.DirectionRouteCounts[key]
	if current+delta < 0 {
		return fmt.Errorf("event metric direction route population underflow")
	}
	before := current > 0
	current += delta
	if current == 0 {
		delete(projector.DirectionRouteCounts, key)
	} else {
		projector.DirectionRouteCounts[key] = current
	}
	after := current > 0
	if before == after {
		return nil
	}
	for _, reference := range projector.DirectionReferences[key] {
		if err := projector.setMemberDirectionVisible(reference.EventIndex, reference.MemberIndex, after); err != nil {
			return err
		}
	}
	return nil
}

func (projector *eventMetricProjector) adjustCountryPrefix(
	countryID uint16,
	key eventMetricPrefixKey,
	delta int,
) error {
	population := projector.CountryPrefixCounts[countryID]
	if population == nil {
		return nil
	}
	current := population[key]
	if current+delta < 0 {
		return fmt.Errorf("event metric country prefix population underflow")
	}
	before := current > 0
	current += delta
	if current == 0 {
		delete(population, key)
	} else {
		population[key] = current
	}
	after := current > 0
	if before == after {
		return nil
	}
	for _, eventIndex := range projector.EventsByCountry[countryID] {
		event := projector.Events[eventIndex]
		if projector.CurrentSlot <= event.Cohort.CohortStateSlot ||
			projector.CurrentSlot > event.ProjectionEndSlot {
			continue
		}
		if _, fixed := event.MemberByPrefix[key]; fixed {
			continue
		}
		state := event.NewPrefixes[key]
		if state == nil {
			if !after {
				continue
			}
			state = &eventMetricNewPrefixState{
				Prefix: key, FirstObservedSlot: projector.CurrentSlot, Visible: true,
			}
			event.NewPrefixes[key] = state
			if err := event.adjustNewPrefixCoverage(state, true, true); err != nil {
				return err
			}
			continue
		}
		if state.Visible != after {
			if err := event.adjustNewPrefixCoverage(state, after, false); err != nil {
				return err
			}
			state.Visible = after
		}
	}
	return nil
}

func (event *eventMetricEventState) adjustNewPrefixCoverage(
	state *eventMetricNewPrefixState,
	visible bool,
	first bool,
) error {
	current := event.NewIPv4Current
	cumulative := event.NewIPv4Cumulative
	currentCount := &event.NewVisibleIPv4
	cumulativeCount := &event.NewCumulativeIPv4
	if state.Prefix.AFI == 6 {
		current = event.NewIPv6Current
		cumulative = event.NewIPv6Cumulative
		currentCount = &event.NewVisibleIPv6
		cumulativeCount = &event.NewCumulativeIPv6
	}
	if first {
		if err := cumulative.Add(state.Prefix.Prefix); err != nil {
			return err
		}
		*cumulativeCount++
	}
	if visible {
		if err := current.Add(state.Prefix.Prefix); err != nil {
			return err
		}
		*currentCount++
	} else {
		if err := current.Remove(state.Prefix.Prefix); err != nil {
			return err
		}
		*currentCount--
	}
	if *currentCount < 0 || *cumulativeCount < *currentCount {
		return fmt.Errorf("event new prefix population underflow")
	}
	return nil
}

func (projector *eventMetricProjector) Apply(event routeStateEvent) error {
	if event.Key.Collector != RouteStateCollectorRRC25 {
		return fmt.Errorf("event metric route collector is not rrc25")
	}
	previous, existed := projector.Routes[event.Key]
	direction := eventMetricDirectionFromRoute(event.Key)
	_, directionSelected := projector.DirectionReferences[direction]
	prefix := direction.eventMetricPrefixKey
	if existed && previous.Visible {
		if directionSelected {
			if err := projector.adjustDirectionRoute(direction, -1); err != nil {
				return err
			}
		}
		if previous.OriginKnown {
			countryID := projector.Mapping.CountryID(previous.OriginASN)
			if err := projector.adjustCountryPrefix(countryID, prefix, -1); err != nil {
				return err
			}
		}
	}
	current := eventMetricRouteValue{
		Visible:     event.Action != actionWithdraw,
		OriginKnown: event.OriginKnown,
		OriginASN:   event.OriginASN,
	}
	selectedCountry := false
	if current.Visible && current.OriginKnown {
		countryID := projector.Mapping.CountryID(current.OriginASN)
		_, selectedCountry = projector.EventsByCountry[countryID]
		if selectedCountry {
			if err := projector.adjustCountryPrefix(countryID, prefix, 1); err != nil {
				return err
			}
		}
	}
	if current.Visible && directionSelected {
		if err := projector.adjustDirectionRoute(direction, 1); err != nil {
			return err
		}
	}
	if directionSelected || selectedCountry {
		projector.Routes[event.Key] = current
	} else {
		delete(projector.Routes, event.Key)
	}
	return nil
}

func (projector *eventMetricProjector) ValidateCohortsAtCurrentSlot() error {
	for _, event := range projector.Events {
		if event.Cohort.CohortStateSlot != projector.CurrentSlot {
			continue
		}
		population := projector.CountryPrefixCounts[event.CountryID]
		if len(population) != len(event.MemberByPrefix) {
			return fmt.Errorf("event metric country cohort prefix population diverged at slot %d", projector.CurrentSlot)
		}
		for prefix, count := range population {
			if count < 1 {
				return fmt.Errorf("event metric country prefix count is invalid")
			}
			if _, exists := event.MemberByPrefix[prefix]; !exists {
				return fmt.Errorf("event metric cohort member set diverged at slot %d", projector.CurrentSlot)
			}
		}
	}
	return nil
}

func (event *eventMetricEventState) seriesRow(slot int, observed bool, reason string) EventMetricSeriesRow {
	row := EventMetricSeriesRow{
		SchemaVersion: EventMetricSeriesVersion, EventMetricID: event.EventMetricID,
		CohortID: event.Cohort.CohortID, CountryCode: event.Binding.CountryCode,
		StateSlot: slot, StatePointUTC: eventMetricStatePoint(slot), ValueState: "observed",
	}
	if !observed {
		row.ValueState = "unknown"
		row.MissingReason = &reason
		return row
	}
	values := &EventMetricSeriesValues{
		FixedPrefixCount: len(event.Members), NormalPrefixCount: event.NormalPrefixes,
		InterruptedPrefixCount:           event.PartialPrefixes + event.CompletePrefixes,
		PartiallyInterruptedPrefixCount:  event.PartialPrefixes,
		CompletelyInterruptedPrefixCount: event.CompletePrefixes,
		UnknownPrefixCount:               event.UnknownPrefixes,
		ExpectedDirectionCount:           event.VisibleDirections + event.InvisibleDirections,
		VisibleDirectionCount:            event.VisibleDirections, InvisibleDirectionCount: event.InvisibleDirections,
		FixedASNCount: len(event.ASNs), NormalASNCount: event.NormalASNs,
		AffectedASNCount: event.AffectedASNs, RouteInterruptedASNCount: event.InterruptedASNs,
		UnknownASNCount:               event.UnknownASNs,
		FixedVisibleIPv4AddressCount:  event.FixedIPv4Coverage.Covered(),
		FixedVisibleIPv6Slash48Count:  event.FixedIPv6Coverage.Covered(),
		NewVisibleIPv4PrefixCount:     event.NewVisibleIPv4,
		NewVisibleIPv6PrefixCount:     event.NewVisibleIPv6,
		NewVisibleIPv4AddressCount:    event.NewIPv4Current.Covered(),
		NewVisibleIPv6Slash48Count:    event.NewIPv6Current.Covered(),
		NewCumulativeIPv4PrefixCount:  event.NewCumulativeIPv4,
		NewCumulativeIPv6PrefixCount:  event.NewCumulativeIPv6,
		NewCumulativeIPv4AddressCount: event.NewIPv4Cumulative.Covered(),
		NewCumulativeIPv6Slash48Count: event.NewIPv6Cumulative.Covered(),
	}
	row.Values = values
	return row
}

func (event *eventMetricEventState) prefixRow(member *eventMetricMemberState, slot int, kind string) EventMetricPrefixStateRow {
	return EventMetricPrefixStateRow{
		SchemaVersion: EventMetricPrefixVersion, RecordKind: kind,
		EventMetricID: event.EventMetricID, CohortID: event.Cohort.CohortID,
		CohortMemberID: member.CohortMemberID, StateSlot: slot,
		StatePointUTC: eventMetricStatePoint(slot), Prefix: member.Prefix.Prefix.String(),
		AddressFamily: eventMetricAFIName(member.Prefix.AFI), Classification: member.Classification,
		ExpectedDirectionCount:  member.ExpectedDirectionCount,
		VisibleDirectionCount:   member.VisibleDirectionCount,
		InvisibleDirectionCount: member.ExpectedDirectionCount - member.VisibleDirectionCount,
		UnknownDirectionCount:   0,
	}
}

func (event *eventMetricEventState) asnRow(state *eventMetricASNState, slot int, kind string) EventMetricASNStateRow {
	return EventMetricASNStateRow{
		SchemaVersion: EventMetricASNVersion, RecordKind: kind,
		EventMetricID: event.EventMetricID, CohortID: event.Cohort.CohortID,
		StateSlot: slot, StatePointUTC: eventMetricStatePoint(slot), ASN: state.ASN,
		Classification: state.Classification, FixedPrefixCount: state.TotalPrefixes,
		NormalPrefixCount: state.NormalPrefixes, PartialPrefixCount: state.PartialPrefixes,
		CompletePrefixCount: state.CompletePrefixes, UnknownPrefixCount: state.UnknownPrefixes,
	}
}

func (event *eventMetricEventState) newPrefixRow(state *eventMetricNewPrefixState, slot int, kind string) EventMetricNewPrefixStateRow {
	visibility := "not_visible"
	if state.Visible {
		visibility = "visible"
	}
	return EventMetricNewPrefixStateRow{
		SchemaVersion: EventMetricNewPrefixVersion, RecordKind: kind,
		EventMetricID: event.EventMetricID, CohortID: event.Cohort.CohortID,
		StateSlot: slot, StatePointUTC: eventMetricStatePoint(slot),
		FirstObservedSlot: state.FirstObservedSlot, Prefix: state.Prefix.Prefix.String(),
		AddressFamily: eventMetricAFIName(state.Prefix.AFI), VisibilityState: visibility,
	}
}

func equalEventMetricPrefixRow(left, right EventMetricPrefixStateRow) bool {
	left.RecordKind, right.RecordKind = "", ""
	left.StateSlot, right.StateSlot = 0, 0
	left.StatePointUTC, right.StatePointUTC = "", ""
	return left == right
}

func equalEventMetricASNRow(left, right EventMetricASNStateRow) bool {
	left.RecordKind, right.RecordKind = "", ""
	left.StateSlot, right.StateSlot = 0, 0
	left.StatePointUTC, right.StatePointUTC = "", ""
	return left == right
}

func equalEventMetricNewPrefixRow(left, right EventMetricNewPrefixStateRow) bool {
	left.RecordKind, right.RecordKind = "", ""
	left.StateSlot, right.StateSlot = 0, 0
	left.StatePointUTC, right.StatePointUTC = "", ""
	return left == right
}

type EventMetricEventManifest struct {
	SchemaVersion                  string                  `json:"schema_version"`
	Status                         string                  `json:"status"`
	Directory                      string                  `json:"directory"`
	EventMetricID                  string                  `json:"event_metric_id"`
	LegacyReference                string                  `json:"legacy_reference"`
	CountryCode                    string                  `json:"country_code"`
	CohortID                       string                  `json:"cohort_id"`
	CohortContentSHA256            string                  `json:"cohort_content_sha256"`
	WindowStartUTC                 string                  `json:"window_start_utc"`
	ProjectionEndStatePointUTC     string                  `json:"projection_end_state_point_utc"`
	StatePointCount                int64                   `json:"state_point_count"`
	FixedPrefixCount               int                     `json:"fixed_prefix_count"`
	FixedASNCount                  int                     `json:"fixed_asn_count"`
	ExpectedDirectionRelationCount int                     `json:"expected_direction_relation_count"`
	NewPrefixCount                 int                     `json:"new_prefix_count"`
	Series                         RouteEventStoreFile     `json:"series"`
	PrefixStates                   RouteEventStoreFile     `json:"prefix_states"`
	ASNStates                      RouteEventStoreFile     `json:"asn_states"`
	NewPrefixStates                RouteEventStoreFile     `json:"new_prefix_states"`
	FinalValues                    EventMetricSeriesValues `json:"final_values"`
	ContentSHA256                  string                  `json:"content_sha256"`
}

type EventMetricStoreManifest struct {
	SchemaVersion                   string                     `json:"schema_version"`
	Status                          string                     `json:"status"`
	RunID                           string                     `json:"run_id"`
	DatasetID                       string                     `json:"dataset_id"`
	CollectorID                     string                     `json:"collector_id"`
	WindowStartUTC                  string                     `json:"window_start_utc"`
	WindowEndExclusiveUTC           string                     `json:"window_end_exclusive_utc"`
	ImplementationID                string                     `json:"implementation_id"`
	ProjectorName                   string                     `json:"projector_name"`
	ProjectorVersion                string                     `json:"projector_version"`
	SourceRouteEventDatasetID       string                     `json:"source_route_event_dataset_id"`
	SourceRouteEventContentSHA256   string                     `json:"source_route_event_content_sha256"`
	SourceRouteEventManifestSHA256  string                     `json:"source_route_event_manifest_sha256"`
	SourceRouteStateDatasetID       string                     `json:"source_route_state_dataset_id"`
	SourceRouteStateContentSHA256   string                     `json:"source_route_state_content_sha256"`
	SourceRouteStateManifestSHA256  string                     `json:"source_route_state_manifest_sha256"`
	SourceEventCohortDatasetID      string                     `json:"source_event_cohort_dataset_id"`
	SourceEventCohortContentSHA256  string                     `json:"source_event_cohort_content_sha256"`
	SourceEventCohortManifestSHA256 string                     `json:"source_event_cohort_manifest_sha256"`
	SourcePeerSessionDatasetID      string                     `json:"source_peer_session_dataset_id"`
	SourcePeerSessionContentSHA256  string                     `json:"source_peer_session_content_sha256"`
	MappingVersion                  string                     `json:"mapping_version"`
	MappingCompatibleSHA256         string                     `json:"mapping_compatible_sha256"`
	MappingRevisedSHA256            string                     `json:"mapping_revised_sha256"`
	RouteStateAuthority             string                     `json:"route_state_authority"`
	DirectionDefinition             string                     `json:"direction_definition"`
	DirectionVisibilitySemantics    string                     `json:"direction_visibility_semantics"`
	SessionRouteBoundary            string                     `json:"session_route_boundary"`
	NewPrefixSemantics              string                     `json:"new_prefix_semantics"`
	MissingSemantics                string                     `json:"missing_semantics"`
	IPv4ResourceSemantics           string                     `json:"ipv4_resource_semantics"`
	IPv6ResourceSemantics           string                     `json:"ipv6_resource_semantics"`
	EventCount                      int                        `json:"event_count"`
	StatePointCount                 int64                      `json:"state_point_count"`
	FixedPrefixCount                int64                      `json:"fixed_prefix_count"`
	ExpectedDirectionRelationCount  int64                      `json:"expected_direction_relation_count"`
	SeriesRowCount                  int64                      `json:"series_row_count"`
	PrefixStateRowCount             int64                      `json:"prefix_state_row_count"`
	ASNStateRowCount                int64                      `json:"asn_state_row_count"`
	NewPrefixStateRowCount          int64                      `json:"new_prefix_state_row_count"`
	Events                          []EventMetricEventManifest `json:"events"`
	ContentSHA256                   string                     `json:"content_sha256"`
}

type eventMetricSources struct {
	RouteEvents           RouteEventStoreManifest
	RouteEventManifestSHA string
	RouteState            RouteStateStoreManifest
	RouteStateManifestSHA string
	Ledger                []RouteStateSlotRecord
	Cohorts               EventCohortStoreManifest
	CohortManifestSHA     string
	PeerSessions          PeerSessionStoreManifest
	Mapping               *GlobalCountryMapping
}

func eventMetricStoreContentSHA(value EventMetricStoreManifest) string {
	value.ContentSHA256 = ""
	raw, _ := json.Marshal(value)
	digest := sha256.Sum256(raw)
	return hex.EncodeToString(digest[:])
}

func eventMetricEventContentSHA(value EventMetricEventManifest) string {
	value.ContentSHA256 = ""
	raw, _ := json.Marshal(value)
	digest := sha256.Sum256(raw)
	return hex.EncodeToString(digest[:])
}

func eventMetricIdentity(cohorts EventCohortStoreManifest, implementationID string) (string, string) {
	runID := stableID("event_metric_run_v1_", map[string]any{
		"schema_version":                     EventMetricStoreVersion,
		"source_event_cohort_dataset_id":     cohorts.DatasetID,
		"source_event_cohort_content_sha256": cohorts.ContentSHA256,
		"implementation_id":                  implementationID,
		"projector_name":                     EventMetricProjectorName,
		"projector_version":                  EventMetricProjectorVersion,
	}, 32)
	datasetID := stableID("event_metric_dataset_v1_", map[string]any{
		"run_id": runID, "collector_id": "rrc25",
	}, 32)
	return runID, datasetID
}

func eventMetricID(datasetID string, binding EventCohortBinding) string {
	return stableID("country_event_metric_v1_", map[string]any{
		"dataset_id": datasetID, "cohort_id": binding.CohortID,
		"legacy_reference":               binding.LegacyReference,
		"window_start_utc":               binding.WindowStartUTC,
		"projection_end_state_point_utc": binding.ProjectionEndStatePointUTC,
	}, 32)
}

func loadEventMetricSources(config EventMetricStoreConfig) (eventMetricSources, error) {
	var sources eventMetricSources
	if config.RouteEventRoot == "" || config.RouteStateRoot == "" || config.EventCohortRoot == "" ||
		config.PeerSessionRoot == "" || config.CompatibleMappingPath == "" ||
		config.RevisedMappingPath == "" || config.LifecycleSnapshotPath == "" ||
		config.RouteEventImplementationID == "" || config.RouteStateImplementationID == "" ||
		config.EventCohortImplementationID == "" || config.PeerSessionImplementationID == "" ||
		config.ImplementationID == "" {
		return sources, fmt.Errorf("event metric paths and identities are required")
	}
	for label, value := range map[string]string{
		"RouteEvent":   config.RouteEventImplementationID,
		"RouteState":   config.RouteStateImplementationID,
		"peer session": config.PeerSessionImplementationID,
		"event cohort": config.EventCohortImplementationID,
		"event metric": config.ImplementationID,
	} {
		if err := validateRouteEventImplementationID(value); err != nil {
			return sources, fmt.Errorf("%s implementation %w", label, err)
		}
	}
	cohortConfig := EventCohortStoreConfig{
		RouteEventRoot: config.RouteEventRoot, RouteStateRoot: config.RouteStateRoot,
		RawRoot: config.RawRoot, SelectionPath: config.SelectionPath,
		PeerSessionRoot:             config.PeerSessionRoot,
		CompatibleMappingPath:       config.CompatibleMappingPath,
		RevisedMappingPath:          config.RevisedMappingPath,
		LifecycleSnapshotPath:       config.LifecycleSnapshotPath,
		Output:                      config.EventCohortRoot,
		RouteEventImplementationID:  config.RouteEventImplementationID,
		RouteStateImplementationID:  config.RouteStateImplementationID,
		PeerSessionImplementationID: config.PeerSessionImplementationID,
		ImplementationID:            config.EventCohortImplementationID, Resume: true,
	}
	cohorts, err := LoadEventCohortStore(config.EventCohortRoot, cohortConfig)
	if err != nil {
		return sources, err
	}
	sources.Cohorts = cohorts
	cohortSHA, _, err := sha256File(filepath.Join(config.EventCohortRoot, "manifest.json"))
	if err != nil {
		return sources, err
	}
	sources.CohortManifestSHA = cohortSHA
	routeEvents, routeEventSHA, err := quickRouteEventSource(RouteStateStoreConfig{
		RouteEventRoot:             config.RouteEventRoot,
		RouteEventImplementationID: config.RouteEventImplementationID,
		ImplementationID:           config.RouteStateImplementationID,
	})
	if err != nil {
		return sources, err
	}
	sources.RouteEvents, sources.RouteEventManifestSHA = routeEvents, routeEventSHA
	var routeState RouteStateStoreManifest
	routeStateRaw, err := readJSON(filepath.Join(config.RouteStateRoot, "manifest.json"), &routeState)
	if err != nil {
		return sources, err
	}
	var routeStateComplete RouteStateStoreManifest
	routeStateCompleteRaw, err := readJSON(filepath.Join(config.RouteStateRoot, "COMPLETE.json"), &routeStateComplete)
	if err != nil {
		return sources, err
	}
	routeStateSHA, _, err := sha256File(filepath.Join(config.RouteStateRoot, "manifest.json"))
	if err != nil {
		return sources, err
	}
	if !bytes.Equal(routeStateRaw, routeStateCompleteRaw) ||
		routeState.SchemaVersion != RouteStateStoreVersion || routeState.Status != "complete" ||
		routeState.DatasetID != cohorts.SourceRouteStateDatasetID ||
		routeState.ContentSHA256 != cohorts.SourceRouteStateContentSHA256 ||
		routeStateSHA != cohorts.SourceRouteStateManifestSHA256 ||
		routeState.SourceRouteEventDatasetID != routeEvents.DatasetID ||
		routeState.SourceRouteEventContentSHA != routeEvents.ContentSHA256 ||
		routeState.SourceRouteEventManifestSHA != routeEventSHA ||
		routeState.ImplementationID != config.RouteStateImplementationID ||
		routeState.RouteStateKey != "collector + VP/peer + prefix + address_family" ||
		routeState.ContentSHA256 != routeStateStoreContentSHA(routeState) {
		return sources, fmt.Errorf("event metric RouteState source identity mismatch")
	}
	sources.RouteState, sources.RouteStateManifestSHA = routeState, routeStateSHA
	first, firstManifest, err := LoadRouteStateSlotLedger(
		config.RouteStateRoot, routeState.DatasetID, 1, RouteStateMidpointSlot,
	)
	if err != nil {
		return sources, err
	}
	second, secondManifest, err := LoadRouteStateSlotLedger(
		config.RouteStateRoot, routeState.DatasetID, RouteStateMidpointSlot+1, RouteStateFinalSlot,
	)
	if err != nil {
		return sources, err
	}
	if len(routeState.SlotLedgers) != 2 || firstManifest != routeState.SlotLedgers[0] ||
		secondManifest != routeState.SlotLedgers[1] {
		return sources, fmt.Errorf("event metric RouteState ledger identity mismatch")
	}
	sources.Ledger = append(first, second...)
	var peer PeerSessionStoreManifest
	peerRaw, err := readJSON(filepath.Join(config.PeerSessionRoot, "manifest.json"), &peer)
	if err != nil {
		return sources, err
	}
	var peerComplete PeerSessionStoreManifest
	peerCompleteRaw, err := readJSON(filepath.Join(config.PeerSessionRoot, "COMPLETE.json"), &peerComplete)
	if err != nil {
		return sources, err
	}
	if !bytes.Equal(peerRaw, peerCompleteRaw) ||
		peer.DatasetID != cohorts.SourcePeerSessionDatasetID ||
		peer.ContentSHA256 != cohorts.SourcePeerSessionContentSHA256 ||
		peer.ImplementationID != config.PeerSessionImplementationID ||
		peer.ObservationSemantics != "single_peer_session_transition" ||
		peer.PrefixWithdrawalInference != "not_permitted" {
		return sources, fmt.Errorf("event metric peer-session boundary mismatch")
	}
	sources.PeerSessions = peer
	mapping, err := LoadGlobalCountryMapping(config.CompatibleMappingPath, config.RevisedMappingPath)
	if err != nil {
		return sources, err
	}
	if mapping.MappingVersion != cohorts.MappingVersion ||
		mapping.CompatibleSHA256 != cohorts.MappingCompatibleSHA256 ||
		mapping.RevisedSHA256 != cohorts.MappingRevisedSHA256 {
		return sources, fmt.Errorf("event metric mapping identity mismatch")
	}
	sources.Mapping = mapping
	return sources, nil
}

func readEventMetricBindings(root string, meta RouteEventStoreFile) ([]EventCohortBinding, error) {
	if err := verifyRouteEventStoreFile(root, meta); err != nil {
		return nil, err
	}
	file, err := os.Open(filepath.Join(root, filepath.FromSlash(meta.Path)))
	if err != nil {
		return nil, err
	}
	defer file.Close()
	decoded, err := gzip.NewReader(file)
	if err != nil {
		return nil, err
	}
	defer decoded.Close()
	scanner := bufio.NewScanner(decoded)
	scanner.Buffer(make([]byte, 64<<10), 4<<20)
	result := make([]EventCohortBinding, 0, meta.RowCount)
	for scanner.Scan() {
		var row EventCohortBinding
		if err := json.Unmarshal(scanner.Bytes(), &row); err != nil {
			return nil, err
		}
		result = append(result, row)
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}
	if int64(len(result)) != meta.RowCount {
		return nil, fmt.Errorf("event metric binding population mismatch")
	}
	return result, nil
}

func readEventMetricMembers(root string, cohort EventCohortManifest) ([]EventCohortMember, error) {
	if err := verifyRouteEventStoreFile(root, cohort.Members); err != nil {
		return nil, err
	}
	file, err := os.Open(filepath.Join(root, filepath.FromSlash(cohort.Members.Path)))
	if err != nil {
		return nil, err
	}
	defer file.Close()
	decoded, err := gzip.NewReader(file)
	if err != nil {
		return nil, err
	}
	defer decoded.Close()
	scanner := bufio.NewScanner(decoded)
	scanner.Buffer(make([]byte, 64<<10), 16<<20)
	result := make([]EventCohortMember, 0, cohort.MemberCount)
	for scanner.Scan() {
		var row EventCohortMember
		if err := json.Unmarshal(scanner.Bytes(), &row); err != nil {
			return nil, err
		}
		result = append(result, row)
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}
	if int64(len(result)) != cohort.MemberCount {
		return nil, fmt.Errorf("event metric member population mismatch")
	}
	return result, nil
}

func buildEventMetricDefinitions(
	config EventMetricStoreConfig,
	sources eventMetricSources,
	datasetID string,
) ([]eventMetricDefinition, error) {
	bindings, err := readEventMetricBindings(config.EventCohortRoot, sources.Cohorts.Events)
	if err != nil {
		return nil, err
	}
	cohortByID := make(map[string]EventCohortManifest, len(sources.Cohorts.Cohorts))
	for _, cohort := range sources.Cohorts.Cohorts {
		cohortByID[cohort.CohortID] = cohort
	}
	definitions := make([]eventMetricDefinition, 0, len(bindings))
	for _, binding := range bindings {
		cohort, exists := cohortByID[binding.CohortID]
		if !exists || cohort.CountryCode != binding.CountryCode {
			return nil, fmt.Errorf("event metric cohort binding is missing")
		}
		members, err := readEventMetricMembers(config.EventCohortRoot, cohort)
		if err != nil {
			return nil, err
		}
		definitions = append(definitions, eventMetricDefinition{
			EventMetricID: eventMetricID(datasetID, binding), Binding: binding,
			Cohort: cohort, Members: members,
		})
	}
	if len(definitions) != sources.Cohorts.EventCount {
		return nil, fmt.Errorf("event metric definition population mismatch")
	}
	return definitions, nil
}

type eventMetricEventWriters struct {
	root        string
	directory   string
	event       *eventMetricEventState
	series      *deterministicJSONLGzipWriter
	prefixes    *deterministicJSONLGzipWriter
	asns        *deterministicJSONLGzipWriter
	newPrefixes *deterministicJSONLGzipWriter
}

type eventMetricWriterManager struct {
	root      string
	active    map[int]*eventMetricEventWriters
	completed []EventMetricEventManifest
}

func newEventMetricWriterManager(root string) *eventMetricWriterManager {
	return &eventMetricWriterManager{root: root, active: make(map[int]*eventMetricEventWriters)}
}

func eventMetricEventDirectory(event *eventMetricEventState) string {
	suffix := event.EventMetricID
	if len(suffix) > 12 {
		suffix = suffix[len(suffix)-12:]
	}
	return filepath.ToSlash(filepath.Join(
		"events", event.Binding.CountryCode,
		fmt.Sprintf("slot-%04d-%s", event.Cohort.CohortStateSlot, suffix),
	))
}

func (manager *eventMetricWriterManager) start(eventIndex int, event *eventMetricEventState, slot int) error {
	if manager.active[eventIndex] != nil || slot != event.WindowStartSlot {
		return fmt.Errorf("event metric writer start coordinate mismatch")
	}
	directory := eventMetricEventDirectory(event)
	absolute := filepath.Join(manager.root, filepath.FromSlash(directory))
	if err := os.MkdirAll(absolute, 0o750); err != nil {
		return err
	}
	result := &eventMetricEventWriters{root: manager.root, directory: directory, event: event}
	var err error
	result.series, err = newDeterministicJSONLGzipWriter(filepath.Join(absolute, "series.jsonl.gz"))
	if err != nil {
		return err
	}
	result.prefixes, err = newDeterministicJSONLGzipWriter(filepath.Join(absolute, "prefix-states.jsonl.gz"))
	if err != nil {
		result.series.Abort()
		return err
	}
	result.asns, err = newDeterministicJSONLGzipWriter(filepath.Join(absolute, "asn-states.jsonl.gz"))
	if err != nil {
		result.series.Abort()
		result.prefixes.Abort()
		return err
	}
	result.newPrefixes, err = newDeterministicJSONLGzipWriter(filepath.Join(absolute, "new-prefix-states.jsonl.gz"))
	if err != nil {
		result.series.Abort()
		result.prefixes.Abort()
		result.asns.Abort()
		return err
	}
	for index := range event.Members {
		member := &event.Members[index]
		row := event.prefixRow(member, slot, "baseline")
		if err := result.prefixes.Write(row); err != nil {
			return err
		}
		member.LastEmitted = row
	}
	asns := make([]uint32, 0, len(event.ASNs))
	for asn := range event.ASNs {
		asns = append(asns, asn)
	}
	sort.Slice(asns, func(i, j int) bool { return asns[i] < asns[j] })
	for _, asn := range asns {
		state := event.ASNs[asn]
		row := event.asnRow(state, slot, "baseline")
		if err := result.asns.Write(row); err != nil {
			return err
		}
		state.LastEmitted = row
	}
	event.Started = true
	manager.active[eventIndex] = result
	return nil
}

func (writer *eventMetricEventWriters) writePoint(slot int) error {
	event := writer.event
	for index := range event.Members {
		member := &event.Members[index]
		row := event.prefixRow(member, slot, "change")
		if !equalEventMetricPrefixRow(row, member.LastEmitted) {
			if err := writer.prefixes.Write(row); err != nil {
				return err
			}
			member.LastEmitted = row
		}
	}
	asns := make([]uint32, 0, len(event.ASNs))
	for asn := range event.ASNs {
		asns = append(asns, asn)
	}
	sort.Slice(asns, func(i, j int) bool { return asns[i] < asns[j] })
	for _, asn := range asns {
		state := event.ASNs[asn]
		row := event.asnRow(state, slot, "change")
		if !equalEventMetricASNRow(row, state.LastEmitted) {
			if err := writer.asns.Write(row); err != nil {
				return err
			}
			state.LastEmitted = row
		}
	}
	newKeys := make([]eventMetricPrefixKey, 0, len(event.NewPrefixes))
	for key := range event.NewPrefixes {
		newKeys = append(newKeys, key)
	}
	sort.Slice(newKeys, func(i, j int) bool {
		if newKeys[i].AFI != newKeys[j].AFI {
			return newKeys[i].AFI < newKeys[j].AFI
		}
		return newKeys[i].Prefix.String() < newKeys[j].Prefix.String()
	})
	for _, key := range newKeys {
		state := event.NewPrefixes[key]
		kind := "change"
		if state.LastEmitted.SchemaVersion == "" {
			kind = "first_observed"
		}
		row := event.newPrefixRow(state, slot, kind)
		if state.LastEmitted.SchemaVersion == "" || !equalEventMetricNewPrefixRow(row, state.LastEmitted) {
			if err := writer.newPrefixes.Write(row); err != nil {
				return err
			}
			state.LastEmitted = row
		}
	}
	return writer.series.Write(event.seriesRow(slot, true, ""))
}

func (manager *eventMetricWriterManager) finish(eventIndex int, slot int) error {
	writer := manager.active[eventIndex]
	if writer == nil || slot != writer.event.ProjectionEndSlot {
		return fmt.Errorf("event metric writer finish coordinate mismatch")
	}
	relative := func(name string) string { return filepath.ToSlash(filepath.Join(writer.directory, name)) }
	series, err := writer.series.Close(relative("series.jsonl.gz"))
	if err != nil {
		return err
	}
	prefixes, err := writer.prefixes.Close(relative("prefix-states.jsonl.gz"))
	if err != nil {
		return err
	}
	asns, err := writer.asns.Close(relative("asn-states.jsonl.gz"))
	if err != nil {
		return err
	}
	newPrefixes, err := writer.newPrefixes.Close(relative("new-prefix-states.jsonl.gz"))
	if err != nil {
		return err
	}
	finalRow := writer.event.seriesRow(slot, true, "")
	if finalRow.Values == nil || series.RowCount != int64(writer.event.ProjectionEndSlot-writer.event.WindowStartSlot+1) {
		return fmt.Errorf("event metric series population mismatch")
	}
	manifest := EventMetricEventManifest{
		SchemaVersion: EventMetricVersion, Status: "complete", Directory: writer.directory,
		EventMetricID: writer.event.EventMetricID, LegacyReference: writer.event.Binding.LegacyReference,
		CountryCode: writer.event.Binding.CountryCode, CohortID: writer.event.Cohort.CohortID,
		CohortContentSHA256:        writer.event.Cohort.ContentSHA256,
		WindowStartUTC:             writer.event.Binding.WindowStartUTC,
		ProjectionEndStatePointUTC: writer.event.Binding.ProjectionEndStatePointUTC,
		StatePointCount:            series.RowCount, FixedPrefixCount: len(writer.event.Members),
		FixedASNCount:                  len(writer.event.ASNs),
		ExpectedDirectionRelationCount: writer.event.VisibleDirections + writer.event.InvisibleDirections,
		NewPrefixCount:                 len(writer.event.NewPrefixes), Series: series, PrefixStates: prefixes,
		ASNStates: asns, NewPrefixStates: newPrefixes, FinalValues: *finalRow.Values,
	}
	manifest.ContentSHA256 = eventMetricEventContentSHA(manifest)
	if _, err := writeJSONImmutable(
		filepath.Join(manager.root, filepath.FromSlash(writer.directory), "manifest.json"), manifest,
	); err != nil {
		return err
	}
	manager.completed = append(manager.completed, manifest)
	delete(manager.active, eventIndex)
	return nil
}

func (manager *eventMetricWriterManager) writeSlot(projector *eventMetricProjector, slot int) error {
	for eventIndex, event := range projector.Events {
		if slot < event.WindowStartSlot || slot > event.ProjectionEndSlot {
			continue
		}
		if slot == event.WindowStartSlot {
			if err := manager.start(eventIndex, event, slot); err != nil {
				return err
			}
		}
		writer := manager.active[eventIndex]
		if writer == nil {
			return fmt.Errorf("event metric active writer is missing")
		}
		if err := writer.writePoint(slot); err != nil {
			return err
		}
		if slot == event.ProjectionEndSlot {
			if err := manager.finish(eventIndex, slot); err != nil {
				return err
			}
		}
	}
	return nil
}

func compareEventMetricSourceSlot(
	partition RouteEventPartitionManifest,
	parsed parsedRouteStatePartition,
	formal RouteStateSlotRecord,
) error {
	if parsed.Index != partition.ArtifactIndex || formal.Slot != partition.ArtifactIndex ||
		formal.ArtifactID != partition.Artifact.ArtifactID ||
		formal.SourcePartitionContentSHA != partition.ContentSHA256 ||
		formal.SourceRouteEventFileSHA != partition.Events.SHA256 ||
		formal.RouteEventCount != parsed.RouteEventCount || formal.AnnounceCount != parsed.AnnounceCount ||
		formal.WithdrawCount != parsed.WithdrawCount || formal.TransitionSHA256 != parsed.TransitionSHA256 ||
		formal.QualityStatus != "complete" {
		return fmt.Errorf("event metric replay diverged from formal RouteState slot %d", formal.Slot)
	}
	return nil
}

func processEventMetricUpdates(
	config EventMetricStoreConfig,
	sources eventMetricSources,
	projector *eventMetricProjector,
	writers *eventMetricWriterManager,
) error {
	workers := config.Workers
	if workers < 1 {
		workers = runtime.GOMAXPROCS(0)
	}
	if workers > RouteStateFinalSlot {
		workers = RouteStateFinalSlot
	}
	type result struct {
		parsed parsedRouteStatePartition
		err    error
	}
	jobs := make(chan int)
	results := make(chan result, workers)
	var group sync.WaitGroup
	for worker := 0; worker < workers; worker++ {
		group.Add(1)
		go func() {
			defer group.Done()
			for slot := range jobs {
				parsed, err := parseRouteStatePartition(config.RouteEventRoot, sources.RouteEvents.Partitions[slot])
				results <- result{parsed: parsed, err: err}
			}
		}()
	}
	nextSchedule, inflight := 1, 0
	for inflight < workers && nextSchedule <= RouteStateFinalSlot {
		jobs <- nextSchedule
		nextSchedule++
		inflight++
	}
	pending := make(map[int]parsedRouteStatePartition, workers)
	nextApply := 1
	for nextApply <= RouteStateFinalSlot {
		item := <-results
		inflight--
		if item.err != nil {
			close(jobs)
			group.Wait()
			return item.err
		}
		pending[item.parsed.Index] = item.parsed
		for {
			parsed, exists := pending[nextApply]
			if !exists {
				break
			}
			delete(pending, nextApply)
			projector.CurrentSlot = nextApply
			for _, event := range parsed.Events {
				if err := projector.Apply(event); err != nil {
					close(jobs)
					group.Wait()
					return err
				}
			}
			parsed.Events = nil
			if err := compareEventMetricSourceSlot(
				sources.RouteEvents.Partitions[nextApply], parsed, sources.Ledger[nextApply-1],
			); err != nil {
				close(jobs)
				group.Wait()
				return err
			}
			if err := projector.ValidateCohortsAtCurrentSlot(); err != nil {
				close(jobs)
				group.Wait()
				return err
			}
			if err := writers.writeSlot(projector, nextApply); err != nil {
				close(jobs)
				group.Wait()
				return err
			}
			if nextApply%32 == 0 || nextApply == RouteStateFinalSlot {
				config.progress(fmt.Sprintf(
					"event metric replay slot=%04d/%04d selected_routes=%d",
					nextApply, RouteStateFinalSlot, len(projector.Routes),
				))
			}
			nextApply++
			if nextSchedule <= RouteStateFinalSlot {
				jobs <- nextSchedule
				nextSchedule++
				inflight++
			}
		}
	}
	close(jobs)
	group.Wait()
	if inflight != 0 || len(pending) != 0 || len(writers.active) != 0 {
		return fmt.Errorf("event metric replay pipeline did not close")
	}
	return nil
}

func RunEventMetricStore(config EventMetricStoreConfig) (EventMetricStoreManifest, error) {
	var empty EventMetricStoreManifest
	if config.Output == "" {
		return empty, fmt.Errorf("event metric output is required")
	}
	if _, err := os.Lstat(config.Output); err == nil {
		if !config.Resume {
			return empty, fmt.Errorf("event metric output already exists")
		}
		return LoadEventMetricStore(config.Output, config)
	} else if !os.IsNotExist(err) {
		return empty, err
	}
	sources, err := loadEventMetricSources(config)
	if err != nil {
		return empty, err
	}
	runID, datasetID := eventMetricIdentity(sources.Cohorts, config.ImplementationID)
	definitions, err := buildEventMetricDefinitions(config, sources, datasetID)
	if err != nil {
		return empty, err
	}
	projector, err := newEventMetricProjector(sources.Mapping, definitions)
	if err != nil {
		return empty, err
	}
	temporary := config.Output + ".tmp"
	if _, err := os.Lstat(temporary); err == nil {
		return empty, fmt.Errorf("unfinished event metric output exists")
	} else if !os.IsNotExist(err) {
		return empty, err
	}
	if err := os.MkdirAll(temporary, 0o750); err != nil {
		return empty, err
	}
	writers := newEventMetricWriterManager(temporary)
	projector.CurrentSlot = 0
	seed, err := parseRouteStatePartitionWithConsumer(
		config.RouteEventRoot, sources.RouteEvents.Partitions[0], false, projector.Apply,
	)
	if err != nil {
		return empty, err
	}
	if seed.RouteEventCount != sources.RouteEvents.Partitions[0].RouteEvents ||
		seed.RIBSnapshotCount != seed.RouteEventCount {
		return empty, fmt.Errorf("event metric Seed RIB population mismatch")
	}
	if err := projector.ValidateCohortsAtCurrentSlot(); err != nil {
		return empty, err
	}
	if err := writers.writeSlot(projector, 0); err != nil {
		return empty, err
	}
	if err := processEventMetricUpdates(config, sources, projector, writers); err != nil {
		return empty, err
	}
	if len(writers.completed) != len(definitions) {
		return empty, fmt.Errorf("event metric completed event population mismatch")
	}
	sort.Slice(writers.completed, func(i, j int) bool {
		if writers.completed[i].WindowStartUTC != writers.completed[j].WindowStartUTC {
			return writers.completed[i].WindowStartUTC < writers.completed[j].WindowStartUTC
		}
		return writers.completed[i].LegacyReference < writers.completed[j].LegacyReference
	})
	manifest := EventMetricStoreManifest{
		SchemaVersion: EventMetricStoreVersion, Status: "complete", RunID: runID, DatasetID: datasetID,
		CollectorID: "rrc25", WindowStartUTC: RouteEventWindowStartUTC,
		WindowEndExclusiveUTC: RouteEventWindowEndUTC,
		ImplementationID:      config.ImplementationID, ProjectorName: EventMetricProjectorName,
		ProjectorVersion:                EventMetricProjectorVersion,
		SourceRouteEventDatasetID:       sources.RouteEvents.DatasetID,
		SourceRouteEventContentSHA256:   sources.RouteEvents.ContentSHA256,
		SourceRouteEventManifestSHA256:  sources.RouteEventManifestSHA,
		SourceRouteStateDatasetID:       sources.RouteState.DatasetID,
		SourceRouteStateContentSHA256:   sources.RouteState.ContentSHA256,
		SourceRouteStateManifestSHA256:  sources.RouteStateManifestSHA,
		SourceEventCohortDatasetID:      sources.Cohorts.DatasetID,
		SourceEventCohortContentSHA256:  sources.Cohorts.ContentSHA256,
		SourceEventCohortManifestSHA256: sources.CohortManifestSHA,
		SourcePeerSessionDatasetID:      sources.PeerSessions.DatasetID,
		SourcePeerSessionContentSHA256:  sources.PeerSessions.ContentSHA256,
		MappingVersion:                  sources.Mapping.MappingVersion,
		MappingCompatibleSHA256:         sources.Mapping.CompatibleSHA256,
		MappingRevisedSHA256:            sources.Mapping.RevisedSHA256,
		RouteStateAuthority:             "the_existing_route_state_dataset_is_the_only_route_state_fact",
		DirectionDefinition:             "one_independent_direction_is_one_rrc25_peer_asn_and_multiple_bgp_sessions_do_not_expand_the_denominator",
		DirectionVisibilitySemantics:    "an_expected_direction_is_visible_when_current_route_state_has_any_visible_route_for_its_peer_asn_and_prefix_regardless_of_origin",
		SessionRouteBoundary:            "peer_session_observations_remain_separate_and_never_materialize_or_imply_route_withdrawals",
		NewPrefixSemantics:              "prefixes_first_observed_after_the_frozen_cohort_use_independent_current_and_cumulative_tracks_and_never_offset_fixed_cohort_interruptions",
		MissingSemantics:                "missing_or_incomplete_slots_are_unknown_with_null_values_and_are_never_coerced_to_zero",
		IPv4ResourceSemantics:           "deduplicated_unique_ipv4_address_union",
		IPv6ResourceSemantics:           "deduplicated_unique_ipv6_slash48_equivalent_union",
		EventCount:                      len(writers.completed), Events: writers.completed,
	}
	for _, event := range manifest.Events {
		manifest.StatePointCount += event.StatePointCount
		manifest.FixedPrefixCount += int64(event.FixedPrefixCount)
		manifest.ExpectedDirectionRelationCount += int64(event.ExpectedDirectionRelationCount)
		manifest.SeriesRowCount += event.Series.RowCount
		manifest.PrefixStateRowCount += event.PrefixStates.RowCount
		manifest.ASNStateRowCount += event.ASNStates.RowCount
		manifest.NewPrefixStateRowCount += event.NewPrefixStates.RowCount
	}
	if manifest.StatePointCount != manifest.SeriesRowCount ||
		manifest.FixedPrefixCount != sources.Cohorts.CohortMemberCount ||
		manifest.ExpectedDirectionRelationCount != sources.Cohorts.ExpectedDirectionRelationCount {
		return empty, fmt.Errorf("event metric root population mismatch")
	}
	manifest.ContentSHA256 = eventMetricStoreContentSHA(manifest)
	if _, err := writeJSONImmutable(filepath.Join(temporary, "manifest.json"), manifest); err != nil {
		return empty, err
	}
	if _, err := writeJSONImmutable(filepath.Join(temporary, "COMPLETE.json"), manifest); err != nil {
		return empty, err
	}
	if err := os.Rename(temporary, config.Output); err != nil {
		return empty, err
	}
	return LoadEventMetricStore(config.Output, config)
}

func LoadEventMetricStore(root string, config EventMetricStoreConfig) (EventMetricStoreManifest, error) {
	var manifest EventMetricStoreManifest
	sources, err := loadEventMetricSources(config)
	if err != nil {
		return manifest, err
	}
	runID, datasetID := eventMetricIdentity(sources.Cohorts, config.ImplementationID)
	manifestRaw, err := readJSON(filepath.Join(root, "manifest.json"), &manifest)
	if err != nil {
		return manifest, err
	}
	var complete EventMetricStoreManifest
	completeRaw, err := readJSON(filepath.Join(root, "COMPLETE.json"), &complete)
	if err != nil {
		return manifest, err
	}
	if !bytes.Equal(manifestRaw, completeRaw) || manifest.SchemaVersion != EventMetricStoreVersion ||
		manifest.Status != "complete" || manifest.RunID != runID || manifest.DatasetID != datasetID ||
		manifest.CollectorID != "rrc25" || manifest.ImplementationID != config.ImplementationID ||
		manifest.SourceRouteEventDatasetID != sources.RouteEvents.DatasetID ||
		manifest.SourceRouteEventContentSHA256 != sources.RouteEvents.ContentSHA256 ||
		manifest.SourceRouteEventManifestSHA256 != sources.RouteEventManifestSHA ||
		manifest.SourceRouteStateDatasetID != sources.RouteState.DatasetID ||
		manifest.SourceRouteStateContentSHA256 != sources.RouteState.ContentSHA256 ||
		manifest.SourceRouteStateManifestSHA256 != sources.RouteStateManifestSHA ||
		manifest.SourceEventCohortDatasetID != sources.Cohorts.DatasetID ||
		manifest.SourceEventCohortContentSHA256 != sources.Cohorts.ContentSHA256 ||
		manifest.SourceEventCohortManifestSHA256 != sources.CohortManifestSHA ||
		manifest.SourcePeerSessionDatasetID != sources.PeerSessions.DatasetID ||
		manifest.SourcePeerSessionContentSHA256 != sources.PeerSessions.ContentSHA256 ||
		manifest.EventCount != len(manifest.Events) || manifest.ContentSHA256 != eventMetricStoreContentSHA(manifest) {
		return manifest, fmt.Errorf("complete event metric store identity mismatch")
	}
	var points, prefixes, directions, seriesRows, prefixRows, asnRows, newRows int64
	seen := make(map[string]struct{}, len(manifest.Events))
	for _, event := range manifest.Events {
		if _, exists := seen[event.EventMetricID]; exists || event.SchemaVersion != EventMetricVersion ||
			event.Status != "complete" || event.ContentSHA256 != eventMetricEventContentSHA(event) {
			return manifest, fmt.Errorf("event metric event manifest identity mismatch")
		}
		seen[event.EventMetricID] = struct{}{}
		var disk EventMetricEventManifest
		if _, err := readJSON(filepath.Join(root, filepath.FromSlash(event.Directory), "manifest.json"), &disk); err != nil || disk != event {
			if err != nil {
				return manifest, err
			}
			return manifest, fmt.Errorf("event metric listed manifest mismatch")
		}
		for _, file := range []RouteEventStoreFile{event.Series, event.PrefixStates, event.ASNStates, event.NewPrefixStates} {
			if err := verifyRouteEventStoreFile(root, file); err != nil {
				return manifest, err
			}
		}
		points += event.StatePointCount
		prefixes += int64(event.FixedPrefixCount)
		directions += int64(event.ExpectedDirectionRelationCount)
		seriesRows += event.Series.RowCount
		prefixRows += event.PrefixStates.RowCount
		asnRows += event.ASNStates.RowCount
		newRows += event.NewPrefixStates.RowCount
	}
	if points != manifest.StatePointCount || prefixes != manifest.FixedPrefixCount ||
		directions != manifest.ExpectedDirectionRelationCount || seriesRows != manifest.SeriesRowCount ||
		prefixRows != manifest.PrefixStateRowCount || asnRows != manifest.ASNStateRowCount ||
		newRows != manifest.NewPrefixStateRowCount || points != seriesRows {
		return manifest, fmt.Errorf("event metric store aggregate population mismatch")
	}
	return manifest, nil
}
