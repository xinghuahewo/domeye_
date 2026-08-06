package replay

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"math"
	"math/bits"
	"net/netip"
	"sort"
)

const (
	GlobalEngineVersion      = "rrc25-global-go-replay/1.0.0"
	GlobalObservationVersion = "rrc25-global-country-observation/v1"
	GlobalCohortVersion      = "rrc25-global-country-cohort/v1"
)

type multisetDigest [4]uint64

func (digest *multisetDigest) Add(value [32]byte) {
	carry := uint64(0)
	for index := 3; index >= 0; index-- {
		word := binary.BigEndian.Uint64(value[index*8 : (index+1)*8])
		digest[index], carry = bits.Add64(digest[index], word, carry)
	}
}

func (digest *multisetDigest) Sub(value [32]byte) {
	borrow := uint64(0)
	for index := 3; index >= 0; index-- {
		word := binary.BigEndian.Uint64(value[index*8 : (index+1)*8])
		digest[index], borrow = bits.Sub64(digest[index], word, borrow)
	}
}

func (digest multisetDigest) Hex() string {
	raw := make([]byte, 32)
	for index, word := range digest {
		binary.BigEndian.PutUint64(raw[index*8:(index+1)*8], word)
	}
	return hex.EncodeToString(raw)
}

type globalRoute struct {
	BaselineOriginKnown bool
	BaselineOriginASN   uint32
	BaselineCountryID   uint16
	CurrentPresent      bool
	CurrentOriginKnown  bool
	CurrentOriginASN    uint32
	CurrentCountryID    uint16
	Dynamic             bool
	RIBRecordOrdinal    uint32
	RIBElementOrdinal   uint32
	LastArtifactIndex   uint16
	LastRecordOrdinal   uint32
	LastElementOrdinal  uint32
	LastEventMicros     int64
}

func routeIdentityDigest(key RouteKey, route globalRoute) [32]byte {
	var raw [96]byte
	at := 0
	peer := key.PeerIP.As16()
	copy(raw[at:at+16], peer[:])
	at += 16
	prefix := key.Prefix.Addr().As16()
	copy(raw[at:at+16], prefix[:])
	at += 16
	binary.BigEndian.PutUint32(raw[at:at+4], key.PeerASN)
	at += 4
	raw[at] = key.AFI
	at++
	raw[at] = uint8(key.Prefix.Bits())
	at++
	flags := uint8(0)
	if route.BaselineOriginKnown {
		flags |= 1
	}
	if route.CurrentPresent {
		flags |= 2
	}
	if route.CurrentOriginKnown {
		flags |= 4
	}
	if route.Dynamic {
		flags |= 8
	}
	raw[at] = flags
	at++
	binary.BigEndian.PutUint32(raw[at:at+4], route.BaselineOriginASN)
	at += 4
	binary.BigEndian.PutUint16(raw[at:at+2], route.BaselineCountryID)
	at += 2
	binary.BigEndian.PutUint32(raw[at:at+4], route.CurrentOriginASN)
	at += 4
	binary.BigEndian.PutUint16(raw[at:at+2], route.CurrentCountryID)
	at += 2
	binary.BigEndian.PutUint32(raw[at:at+4], route.RIBRecordOrdinal)
	at += 4
	binary.BigEndian.PutUint32(raw[at:at+4], route.RIBElementOrdinal)
	at += 4
	binary.BigEndian.PutUint16(raw[at:at+2], route.LastArtifactIndex)
	at += 2
	binary.BigEndian.PutUint32(raw[at:at+4], route.LastRecordOrdinal)
	at += 4
	binary.BigEndian.PutUint32(raw[at:at+4], route.LastElementOrdinal)
	at += 4
	binary.BigEndian.PutUint64(raw[at:at+8], uint64(route.LastEventMicros))
	at += 8
	return sha256.Sum256(raw[:at])
}

func cohortMemberDigest(key RouteKey, route globalRoute) [32]byte {
	var raw [64]byte
	at := 0
	peer := key.PeerIP.As16()
	copy(raw[at:at+16], peer[:])
	at += 16
	prefix := key.Prefix.Addr().As16()
	copy(raw[at:at+16], prefix[:])
	at += 16
	binary.BigEndian.PutUint32(raw[at:at+4], key.PeerASN)
	at += 4
	raw[at] = key.AFI
	at++
	raw[at] = uint8(key.Prefix.Bits())
	at++
	if route.BaselineOriginKnown {
		raw[at] = 1
	}
	at++
	binary.BigEndian.PutUint32(raw[at:at+4], route.BaselineOriginASN)
	at += 4
	binary.BigEndian.PutUint16(raw[at:at+2], route.BaselineCountryID)
	at += 2
	return sha256.Sum256(raw[:at])
}

type globalASNKey struct {
	CountryID uint16
	ASN       uint32
	AFI       uint8
}

type globalCountryPopulation struct {
	BaselinePrefixVP int64
	VisiblePrefixVP  int64
	CurrentPrefixVP  int64
	BaselineByAFI    [2]int64
	VisibleByAFI     [2]int64
	CurrentByAFI     [2]int64
	CohortDigest     multisetDigest
}

type GlobalReplayState struct {
	Mapping            *GlobalCountryMapping
	SeedObservedAt     string
	SeedEventMicros    int64
	Routes             map[RouteKey]globalRoute
	Counters           map[globalASNKey]visibilityCounter
	Countries          map[uint16]*globalCountryPopulation
	CohortIDs          map[uint16]string
	StateDigest        multisetDigest
	SeedRouteRows      int64
	BaselineRouteCount int64
	PresentRouteCount  int64
}

func NewGlobalReplayState(mapping *GlobalCountryMapping, capacity int) (*GlobalReplayState, error) {
	if mapping == nil {
		return nil, fmt.Errorf("global country mapping is required")
	}
	if capacity < 0 {
		return nil, fmt.Errorf("route capacity cannot be negative")
	}
	return &GlobalReplayState{
		Mapping:         mapping,
		SeedEventMicros: catchUpStart.UnixMicro(),
		Routes:          make(map[RouteKey]globalRoute, capacity),
		Counters:        make(map[globalASNKey]visibilityCounter),
		Countries:       make(map[uint16]*globalCountryPopulation),
		CohortIDs:       make(map[uint16]string),
	}, nil
}

func afiOffset(afi uint8) (int, error) {
	switch afi {
	case 4:
		return 0, nil
	case 6:
		return 1, nil
	default:
		return 0, fmt.Errorf("unsupported AFI %d", afi)
	}
}

func (state *GlobalReplayState) country(id uint16) *globalCountryPopulation {
	value := state.Countries[id]
	if value == nil {
		value = &globalCountryPopulation{}
		state.Countries[id] = value
	}
	return value
}

func (state *GlobalReplayState) removeSeedRoute(key RouteKey, route globalRoute) error {
	offset, err := afiOffset(key.AFI)
	if err != nil {
		return err
	}
	country := state.country(route.BaselineCountryID)
	country.BaselinePrefixVP--
	country.VisiblePrefixVP--
	country.CurrentPrefixVP--
	country.BaselineByAFI[offset]--
	country.VisibleByAFI[offset]--
	country.CurrentByAFI[offset]--
	country.CohortDigest.Sub(cohortMemberDigest(key, route))
	if route.BaselineOriginKnown {
		counterKey := globalASNKey{
			CountryID: route.BaselineCountryID,
			ASN:       route.BaselineOriginASN,
			AFI:       key.AFI,
		}
		counter := state.Counters[counterKey]
		counter.Total--
		counter.Visible--
		if counter.Total == 0 {
			delete(state.Counters, counterKey)
		} else {
			state.Counters[counterKey] = counter
		}
	}
	state.StateDigest.Sub(routeIdentityDigest(key, route))
	return nil
}

func (state *GlobalReplayState) Seed(
	key RouteKey,
	originKnown bool,
	originASN uint32,
	recordOrdinal uint32,
	elementOrdinal uint32,
) error {
	offset, err := afiOffset(key.AFI)
	if err != nil {
		return err
	}
	_, existed := state.Routes[key]
	if existing, exists := state.Routes[key]; exists {
		if existing.Dynamic {
			return fmt.Errorf("dynamic route exists during RIB seed")
		}
		if err := state.removeSeedRoute(key, existing); err != nil {
			return err
		}
	}
	countryID := uint16(0)
	if originKnown {
		countryID = state.Mapping.CountryID(originASN)
	}
	route := globalRoute{
		BaselineOriginKnown: originKnown,
		BaselineOriginASN:   originASN,
		BaselineCountryID:   countryID,
		CurrentPresent:      true,
		CurrentOriginKnown:  originKnown,
		CurrentOriginASN:    originASN,
		CurrentCountryID:    countryID,
		RIBRecordOrdinal:    recordOrdinal,
		RIBElementOrdinal:   elementOrdinal,
		LastEventMicros:     state.SeedEventMicros,
	}
	state.Routes[key] = route
	state.SeedRouteRows++
	if !existed {
		state.BaselineRouteCount++
		state.PresentRouteCount++
	}
	country := state.country(countryID)
	country.BaselinePrefixVP++
	country.VisiblePrefixVP++
	country.CurrentPrefixVP++
	country.BaselineByAFI[offset]++
	country.VisibleByAFI[offset]++
	country.CurrentByAFI[offset]++
	country.CohortDigest.Add(cohortMemberDigest(key, route))
	if originKnown {
		counterKey := globalASNKey{CountryID: countryID, ASN: originASN, AFI: key.AFI}
		counter := state.Counters[counterKey]
		counter.Total++
		counter.Visible++
		state.Counters[counterKey] = counter
	}
	state.StateDigest.Add(routeIdentityDigest(key, route))
	return nil
}

func globalBaselineVisible(route globalRoute) bool {
	if !route.CurrentPresent {
		return false
	}
	if !route.BaselineOriginKnown {
		return !route.CurrentOriginKnown
	}
	return route.CurrentOriginKnown &&
		route.CurrentOriginASN == route.BaselineOriginASN
}

type GlobalSlotActivity struct {
	ByCountry              map[uint16]UpdateCounts `json:"-"`
	Global                 UpdateCounts            `json:"global"`
	CountryMigrations      int64                   `json:"country_migrations"`
	ReplacementAnnounces   int64                   `json:"replacement_announces"`
	DuplicateAnnounces     int64                   `json:"duplicate_announces"`
	DuplicateWithdraws     int64                   `json:"duplicate_withdraws"`
	WithdrawWithoutState   int64                   `json:"withdraw_without_state"`
	AnnouncementsUnknown   int64                   `json:"announcements_unknown_country"`
	WithdrawsUnknown       int64                   `json:"withdraws_unknown_country"`
	DuplicateSeedRouteRows int64                   `json:"duplicate_seed_route_rows"`
}

func NewGlobalSlotActivity() *GlobalSlotActivity {
	return &GlobalSlotActivity{ByCountry: make(map[uint16]UpdateCounts)}
}

func (activity *GlobalSlotActivity) add(countryID uint16, action uint8) {
	counts := activity.ByCountry[countryID]
	switch action {
	case actionAnnounce:
		counts.Announce++
		activity.Global.Announce++
		if countryID == 0 {
			activity.AnnouncementsUnknown++
		}
	case actionWithdraw:
		counts.Withdraw++
		activity.Global.Withdraw++
		if countryID == 0 {
			activity.WithdrawsUnknown++
		}
	}
	activity.ByCountry[countryID] = counts
}

func (state *GlobalReplayState) removeCurrentPopulation(key RouteKey, route globalRoute) error {
	if !route.CurrentPresent {
		return nil
	}
	offset, err := afiOffset(key.AFI)
	if err != nil {
		return err
	}
	country := state.country(route.CurrentCountryID)
	country.CurrentPrefixVP--
	country.CurrentByAFI[offset]--
	return nil
}

func (state *GlobalReplayState) addCurrentPopulation(key RouteKey, route globalRoute) error {
	if !route.CurrentPresent {
		return nil
	}
	offset, err := afiOffset(key.AFI)
	if err != nil {
		return err
	}
	country := state.country(route.CurrentCountryID)
	country.CurrentPrefixVP++
	country.CurrentByAFI[offset]++
	return nil
}

func (state *GlobalReplayState) adjustBaselineVisibility(
	key RouteKey,
	route globalRoute,
	delta int,
) error {
	offset, err := afiOffset(key.AFI)
	if err != nil {
		return err
	}
	country := state.country(route.BaselineCountryID)
	country.VisiblePrefixVP += int64(delta)
	country.VisibleByAFI[offset] += int64(delta)
	if route.BaselineOriginKnown {
		counterKey := globalASNKey{
			CountryID: route.BaselineCountryID,
			ASN:       route.BaselineOriginASN,
			AFI:       key.AFI,
		}
		counter := state.Counters[counterKey]
		counter.Visible += delta
		state.Counters[counterKey] = counter
	}
	return nil
}

func (state *GlobalReplayState) Apply(
	event ParsedEvent,
	activity *GlobalSlotActivity,
) error {
	if activity == nil {
		return fmt.Errorf("slot activity is required")
	}
	if event.Action != actionAnnounce && event.Action != actionWithdraw {
		return fmt.Errorf("unsupported route action %d", event.Action)
	}
	current, exists := state.Routes[event.Key]
	activityCountry := uint16(0)
	if event.Action == actionAnnounce {
		if event.OriginKnown {
			activityCountry = state.Mapping.CountryID(event.OriginASN)
		}
		if exists && current.CurrentPresent {
			if current.CurrentOriginKnown == event.OriginKnown &&
				(!event.OriginKnown || current.CurrentOriginASN == event.OriginASN) {
				activity.DuplicateAnnounces++
			} else {
				activity.ReplacementAnnounces++
			}
		}
	} else if exists && current.CurrentPresent {
		activityCountry = current.CurrentCountryID
	} else {
		activity.WithdrawWithoutState++
		activity.DuplicateWithdraws++
	}
	activity.add(activityCountry, event.Action)

	if exists {
		state.StateDigest.Sub(routeIdentityDigest(event.Key, current))
		if err := state.removeCurrentPopulation(event.Key, current); err != nil {
			return err
		}
	}
	beforeVisible := exists && !current.Dynamic && globalBaselineVisible(current)
	oldPresent := exists && current.CurrentPresent
	oldCountry := current.CurrentCountryID

	if !exists {
		if event.Action == actionWithdraw {
			return nil
		}
		current = globalRoute{Dynamic: true}
	}
	if event.Action == actionWithdraw {
		if current.Dynamic {
			if oldPresent {
				state.PresentRouteCount--
			}
			delete(state.Routes, event.Key)
			return nil
		}
		current.CurrentPresent = false
		current.CurrentOriginKnown = false
		current.CurrentOriginASN = 0
		current.CurrentCountryID = 0
	} else {
		current.CurrentPresent = true
		current.CurrentOriginKnown = event.OriginKnown
		current.CurrentOriginASN = event.OriginASN
		if event.OriginKnown {
			current.CurrentCountryID = state.Mapping.CountryID(event.OriginASN)
		} else {
			current.CurrentCountryID = 0
		}
	}
	if oldPresent != current.CurrentPresent {
		if current.CurrentPresent {
			state.PresentRouteCount++
		} else {
			state.PresentRouteCount--
		}
	}
	current.LastArtifactIndex = event.ArtifactIndex
	current.LastRecordOrdinal = event.RecordOrdinal
	current.LastElementOrdinal = event.ElementOrdinal
	current.LastEventMicros = event.EventMicros

	if oldPresent && current.CurrentPresent && oldCountry != current.CurrentCountryID {
		activity.CountryMigrations++
	}
	afterVisible := !current.Dynamic && globalBaselineVisible(current)
	if beforeVisible != afterVisible {
		delta := -1
		if afterVisible {
			delta = 1
		}
		if err := state.adjustBaselineVisibility(event.Key, current, delta); err != nil {
			return err
		}
	}
	state.Routes[event.Key] = current
	if err := state.addCurrentPopulation(event.Key, current); err != nil {
		return err
	}
	state.StateDigest.Add(routeIdentityDigest(event.Key, current))
	return nil
}

type GlobalASNStateRow struct {
	SchemaVersion            string `json:"schema_version"`
	SnapshotID               string `json:"snapshot_id"`
	ObservedAt               string `json:"observed_at"`
	CountryCode              string `json:"country_code"`
	CohortID                 string `json:"cohort_id"`
	ASN                      uint32 `json:"asn"`
	Classification           string `json:"classification"`
	IPv4InvisibleIPv6Visible bool   `json:"ipv4_invisible_ipv6_visible"`
}

type GlobalCountryObservation struct {
	SchemaVersion         string              `json:"schema_version"`
	SnapshotID            string              `json:"snapshot_id"`
	ObservedAt            string              `json:"observed_at"`
	SlotStartUTC          string              `json:"slot_start_utc"`
	SlotEndUTC            string              `json:"slot_end_exclusive_utc"`
	SlotRole              string              `json:"slot_role"`
	CountryCode           string              `json:"country_code"`
	CohortID              string              `json:"cohort_id"`
	BaselineASNCount      int                 `json:"baseline_asn_count"`
	BaselinePrefixVPCount int                 `json:"baseline_prefix_vp_count"`
	VisiblePrefixVPCount  int                 `json:"visible_prefix_vp_count"`
	VisiblePrefixVPRatio  float64             `json:"visible_prefix_vp_ratio"`
	AffectedASNCount      int                 `json:"affected_asn_count"`
	AffectedASNRatio      *float64            `json:"affected_asn_ratio"`
	VisibleOriginASNCount int                 `json:"visible_origin_asn_count"`
	VisibleOriginASNRatio *float64            `json:"visible_origin_asn_ratio"`
	IPv4                  FamilyMetrics       `json:"ipv4"`
	IPv6                  FamilyMetrics       `json:"ipv6"`
	DualClassifications   map[string][]uint32 `json:"dual_stack_classifications"`
	UpdateCounts          UpdateCounts        `json:"update_counts"`
	CountryUpdateCounts   UpdateCounts        `json:"country_update_counts"`
	CurrentPrefixVPCount  int                 `json:"current_prefix_vp_count"`
	StateDigest           string              `json:"global_state_digest"`
}

type GlobalConservation struct {
	GlobalBaselinePrefixVP  int64  `json:"global_baseline_prefix_vp"`
	CountryBaselineSum      int64  `json:"country_baseline_sum"`
	GlobalVisiblePrefixVP   int64  `json:"global_visible_prefix_vp"`
	CountryVisibleSum       int64  `json:"country_visible_sum"`
	GlobalCurrentPrefixVP   int64  `json:"global_current_prefix_vp"`
	CountryCurrentSum       int64  `json:"country_current_sum"`
	RouteStateRows          int64  `json:"route_state_rows"`
	UnknownBaselinePrefixVP int64  `json:"unknown_baseline_prefix_vp"`
	UnknownCurrentPrefixVP  int64  `json:"unknown_current_prefix_vp"`
	StateDigest             string `json:"state_digest"`
	Status                  string `json:"status"`
}

func (state *GlobalReplayState) CohortID(countryID uint16) string {
	if existing := state.CohortIDs[countryID]; existing != "" {
		return existing
	}
	population := state.country(countryID)
	code := state.Mapping.CountryCode(countryID)
	seedObservedAt := state.SeedObservedAt
	if seedObservedAt == "" {
		seedObservedAt = CatchUpStartUTC
	}
	var cohortID string
	if code == "IR" {
		members := make([]BaselineMember, 0, int(population.BaselinePrefixVP))
		for key, route := range state.Routes {
			if route.Dynamic || route.BaselineCountryID != countryID ||
				!route.BaselineOriginKnown {
				continue
			}
			members = append(members, BaselineMember{
				Key: key, KeyText: key.Canonical(),
				OriginASN: route.BaselineOriginASN,
			})
		}
		sort.Slice(members, func(i, j int) bool {
			if members[i].KeyText == members[j].KeyText {
				return members[i].OriginASN < members[j].OriginASN
			}
			return members[i].KeyText < members[j].KeyText
		})
		cohortID = stableID("cohort_go_v1_", map[string]any{
			"collector_id":     "rrc25",
			"country_code":     "IR",
			"mapping_version":  state.Mapping.MappingVersion,
			"seed_observed_at": seedObservedAt,
			"members":          members,
		}, 32)
	} else {
		cohortID = stableID("global_cohort_v1_", map[string]any{
			"schema_version":     GlobalCohortVersion,
			"collector_id":       "rrc25",
			"country_code":       code,
			"mapping_version":    state.Mapping.MappingVersion,
			"seed_observed_at":   seedObservedAt,
			"baseline_prefix_vp": population.BaselinePrefixVP,
			"membership_digest":  population.CohortDigest.Hex(),
		}, 32)
	}
	state.CohortIDs[countryID] = cohortID
	return cohortID
}

type snapshotFamily struct {
	total   int
	visible int
	class   string
}

type snapshotASN struct {
	families map[uint8]snapshotFamily
}

func (state *GlobalReplayState) SnapshotAll(
	observedAt, slotStart, slotEnd, role string,
	activity *GlobalSlotActivity,
) ([]GlobalCountryObservation, []GlobalASNStateRow, GlobalConservation, error) {
	return state.snapshotAll(
		observedAt, slotStart, slotEnd, role, activity, true, false,
	)
}

// SnapshotAllFast 生成与 SnapshotAll 相同的国家和 ASN 状态，但使用已经由
// Apply 维护的增量人口计数做快速守恒校验。它用于从已验真的长窗 spool 定向
// 投影少量国家，避免在每个五分钟槽重复扫描全部固定路由。
func (state *GlobalReplayState) SnapshotAllFast(
	observedAt, slotStart, slotEnd, role string,
	activity *GlobalSlotActivity,
) ([]GlobalCountryObservation, []GlobalASNStateRow, GlobalConservation, error) {
	return state.snapshotAll(
		observedAt, slotStart, slotEnd, role, activity, true, true,
	)
}

func (state *GlobalReplayState) SnapshotCountries(
	observedAt, slotStart, slotEnd, role string,
	activity *GlobalSlotActivity,
) ([]GlobalCountryObservation, GlobalConservation, error) {
	observations, _, conservation, err := state.snapshotAll(
		observedAt, slotStart, slotEnd, role, activity, false, false,
	)
	return observations, conservation, err
}

func (state *GlobalReplayState) snapshotAll(
	observedAt, slotStart, slotEnd, role string,
	activity *GlobalSlotActivity,
	includeASNRows bool,
	fastConservation bool,
) ([]GlobalCountryObservation, []GlobalASNStateRow, GlobalConservation, error) {
	if activity == nil {
		activity = NewGlobalSlotActivity()
	}
	byCountry := make(map[uint16]map[uint32]*snapshotASN)
	familyMetrics := make(map[uint16]map[uint8]*FamilyMetrics)
	for key, counter := range state.Counters {
		if counter.Total <= 0 || counter.Visible < 0 || counter.Visible > counter.Total {
			return nil, nil, GlobalConservation{}, fmt.Errorf(
				"invalid ASN counter country=%d asn=%d afi=%d total=%d visible=%d",
				key.CountryID, key.ASN, key.AFI, counter.Total, counter.Visible,
			)
		}
		if byCountry[key.CountryID] == nil {
			byCountry[key.CountryID] = make(map[uint32]*snapshotASN)
		}
		if byCountry[key.CountryID][key.ASN] == nil {
			byCountry[key.CountryID][key.ASN] = &snapshotASN{
				families: make(map[uint8]snapshotFamily),
			}
		}
		class := classify(counter.Total, counter.Visible)
		byCountry[key.CountryID][key.ASN].families[key.AFI] = snapshotFamily{
			total: counter.Total, visible: counter.Visible, class: class,
		}
		if familyMetrics[key.CountryID] == nil {
			familyMetrics[key.CountryID] = map[uint8]*FamilyMetrics{
				4: {
					FullyVisibleASNs:     []uint32{},
					PartiallyVisibleASNs: []uint32{},
					FullyInvisibleASNs:   []uint32{},
				},
				6: {
					FullyVisibleASNs:     []uint32{},
					PartiallyVisibleASNs: []uint32{},
					FullyInvisibleASNs:   []uint32{},
				},
			}
		}
		family := familyMetrics[key.CountryID][key.AFI]
		family.BaselinePrefixVPCount += counter.Total
		family.VisiblePrefixVPCount += counter.Visible
		switch class {
		case "fully_visible":
			family.FullyVisibleASNs = append(family.FullyVisibleASNs, key.ASN)
		case "partially_visible":
			family.PartiallyVisibleASNs = append(family.PartiallyVisibleASNs, key.ASN)
		case "fully_invisible":
			family.FullyInvisibleASNs = append(family.FullyInvisibleASNs, key.ASN)
		}
	}

	countryIDs := make([]int, 0, len(state.Countries))
	for countryID, population := range state.Countries {
		if population.BaselinePrefixVP > 0 {
			countryIDs = append(countryIDs, int(countryID))
		}
	}
	sort.Ints(countryIDs)
	observations := make([]GlobalCountryObservation, 0, len(countryIDs))
	asnRows := make([]GlobalASNStateRow, 0, len(state.Counters))
	for _, rawID := range countryIDs {
		countryID := uint16(rawID)
		code := state.Mapping.CountryCode(countryID)
		population := state.country(countryID)
		asns := byCountry[countryID]
		if asns == nil {
			asns = map[uint32]*snapshotASN{}
		}
		families := familyMetrics[countryID]
		if families == nil {
			families = map[uint8]*FamilyMetrics{
				4: {FullyVisibleASNs: []uint32{}, PartiallyVisibleASNs: []uint32{}, FullyInvisibleASNs: []uint32{}},
				6: {FullyVisibleASNs: []uint32{}, PartiallyVisibleASNs: []uint32{}, FullyInvisibleASNs: []uint32{}},
			}
		}
		// Prefix×VP 的地址族人口来自完整固定 cohort，而不是只来自具有
		// 可解析 origin ASN 的计数器。显式未知桶包含 origin unknown 路由；
		// 这些路由没有 ASN 分类，但仍必须进入 IPv4/IPv6 人口守恒。
		families[4].BaselinePrefixVPCount = int(population.BaselineByAFI[0])
		families[4].VisiblePrefixVPCount = int(population.VisibleByAFI[0])
		families[6].BaselinePrefixVPCount = int(population.BaselineByAFI[1])
		families[6].VisiblePrefixVPCount = int(population.VisibleByAFI[1])
		for _, family := range families {
			sort.Slice(family.FullyVisibleASNs, func(i, j int) bool {
				return family.FullyVisibleASNs[i] < family.FullyVisibleASNs[j]
			})
			sort.Slice(family.PartiallyVisibleASNs, func(i, j int) bool {
				return family.PartiallyVisibleASNs[i] < family.PartiallyVisibleASNs[j]
			})
			sort.Slice(family.FullyInvisibleASNs, func(i, j int) bool {
				return family.FullyInvisibleASNs[i] < family.FullyInvisibleASNs[j]
			})
			family.BaselineASNCount = len(family.FullyVisibleASNs) +
				len(family.PartiallyVisibleASNs) + len(family.FullyInvisibleASNs)
			family.VisibleASNCount = len(family.FullyVisibleASNs) +
				len(family.PartiallyVisibleASNs)
		}
		dual := map[string][]uint32{
			"fully_visible": {}, "partially_visible": {},
			"fully_invisible": {}, "ipv4_invisible_ipv6_visible": {},
		}
		cohortID := state.CohortID(countryID)
		asnValues := make([]uint32, 0, len(asns))
		for asn := range asns {
			asnValues = append(asnValues, asn)
		}
		sort.Slice(asnValues, func(i, j int) bool { return asnValues[i] < asnValues[j] })
		visibleASNs := 0
		affectedASNs := 0
		for _, asn := range asnValues {
			current := asns[asn]
			class := "fully_visible"
			allInvisible := len(current.families) > 0
			for _, family := range current.families {
				if family.class != "fully_visible" {
					class = "partially_visible"
				}
				if family.class != "fully_invisible" {
					allInvisible = false
				}
			}
			if allInvisible {
				class = "fully_invisible"
			}
			dual[class] = append(dual[class], asn)
			if class == "fully_visible" {
				visibleASNs++
			} else {
				affectedASNs++
				if class == "partially_visible" {
					visibleASNs++
				}
			}
			v4, has4 := current.families[4]
			v6, has6 := current.families[6]
			mismatch := has4 && has6 && v4.class == "fully_invisible" &&
				(v6.class == "fully_visible" || v6.class == "partially_visible")
			if mismatch {
				dual["ipv4_invisible_ipv6_visible"] = append(
					dual["ipv4_invisible_ipv6_visible"], asn,
				)
			}
			if includeASNRows {
				asnRows = append(asnRows, GlobalASNStateRow{
					SchemaVersion: "rrc25-global-country-asn-state/v1",
					ObservedAt:    observedAt, CountryCode: code,
					CohortID: cohortID, ASN: asn,
					Classification:           class,
					IPv4InvisibleIPv6Visible: mismatch,
				})
			}
		}
		var affectedRatio *float64
		var visibleRatio *float64
		if len(asnValues) > 0 {
			affected := float64(affectedASNs) / float64(len(asnValues))
			visible := float64(visibleASNs) / float64(len(asnValues))
			affectedRatio = &affected
			visibleRatio = &visible
		}
		prefixRatio := float64(population.VisiblePrefixVP) /
			float64(population.BaselinePrefixVP)
		snapshotID := stableID("global_snapshot_v1_", map[string]any{
			"country_code": code, "cohort_id": cohortID,
			"observed_at":             observedAt,
			"visible_prefix_vp_count": population.VisiblePrefixVP,
			"state_digest":            state.StateDigest.Hex(),
		}, 32)
		if includeASNRows {
			for index := len(asnRows) - len(asnValues); index < len(asnRows); index++ {
				asnRows[index].SnapshotID = snapshotID
			}
		}
		counts := activity.ByCountry[countryID]
		observations = append(observations, GlobalCountryObservation{
			SchemaVersion: GlobalObservationVersion,
			SnapshotID:    snapshotID, ObservedAt: observedAt,
			SlotStartUTC: slotStart, SlotEndUTC: slotEnd, SlotRole: role,
			CountryCode: code, CohortID: cohortID,
			BaselineASNCount:      len(asnValues),
			BaselinePrefixVPCount: int(population.BaselinePrefixVP),
			VisiblePrefixVPCount:  int(population.VisiblePrefixVP),
			VisiblePrefixVPRatio:  prefixRatio,
			AffectedASNCount:      affectedASNs, AffectedASNRatio: affectedRatio,
			VisibleOriginASNCount: visibleASNs, VisibleOriginASNRatio: visibleRatio,
			IPv4: *families[4], IPv6: *families[6],
			DualClassifications:  dual,
			UpdateCounts:         activity.Global,
			CountryUpdateCounts:  counts,
			CurrentPrefixVPCount: int(population.CurrentPrefixVP),
			StateDigest:          state.StateDigest.Hex(),
		})
	}
	var conservation GlobalConservation
	var err error
	if fastConservation {
		conservation, err = state.ValidateConservationFast()
	} else {
		conservation, err = state.ValidateConservation()
	}
	if err != nil {
		return nil, nil, conservation, err
	}
	return observations, asnRows, conservation, nil
}

func (state *GlobalReplayState) validateConservation(scanRoutes bool) (GlobalConservation, error) {
	result := GlobalConservation{
		GlobalBaselinePrefixVP: int64(0),
		GlobalVisiblePrefixVP:  int64(0),
		GlobalCurrentPrefixVP:  int64(0),
		RouteStateRows:         int64(len(state.Routes)),
		StateDigest:            state.StateDigest.Hex(),
		Status:                 "pass",
	}
	for countryID, population := range state.Countries {
		result.CountryBaselineSum += population.BaselinePrefixVP
		result.CountryVisibleSum += population.VisiblePrefixVP
		result.CountryCurrentSum += population.CurrentPrefixVP
		if countryID == 0 {
			result.UnknownBaselinePrefixVP = population.BaselinePrefixVP
			result.UnknownCurrentPrefixVP = population.CurrentPrefixVP
		}
		for index := range population.BaselineByAFI {
			if population.BaselineByAFI[index] < 0 ||
				population.VisibleByAFI[index] < 0 ||
				population.VisibleByAFI[index] > population.BaselineByAFI[index] ||
				population.CurrentByAFI[index] < 0 {
				result.Status = "fail"
				return result, fmt.Errorf("country %d family population is not closed", countryID)
			}
		}
		if population.BaselinePrefixVP < 0 ||
			population.VisiblePrefixVP < 0 ||
			population.VisiblePrefixVP > population.BaselinePrefixVP ||
			population.CurrentPrefixVP < 0 {
			result.Status = "fail"
			return result, fmt.Errorf("country %d population is not closed", countryID)
		}
	}
	result.GlobalBaselinePrefixVP = result.CountryBaselineSum
	result.GlobalVisiblePrefixVP = result.CountryVisibleSum
	result.GlobalCurrentPrefixVP = result.CountryCurrentSum
	baselineRoutes := state.BaselineRouteCount
	presentRoutes := state.PresentRouteCount
	if scanRoutes {
		baselineRoutes = 0
		presentRoutes = 0
		for _, route := range state.Routes {
			if !route.Dynamic {
				baselineRoutes++
			}
			if route.CurrentPresent {
				presentRoutes++
			}
		}
	}
	if baselineRoutes != result.GlobalBaselinePrefixVP ||
		presentRoutes != result.GlobalCurrentPrefixVP {
		result.Status = "fail"
		return result, fmt.Errorf(
			"global route population mismatch baseline=%d/%d current=%d/%d",
			baselineRoutes, result.GlobalBaselinePrefixVP,
			presentRoutes, result.GlobalCurrentPrefixVP,
		)
	}
	for key, counter := range state.Counters {
		if counter.Total < 0 || counter.Visible < 0 || counter.Visible > counter.Total {
			result.Status = "fail"
			return result, fmt.Errorf(
				"ASN counter is not closed country=%d asn=%d afi=%d",
				key.CountryID, key.ASN, key.AFI,
			)
		}
	}
	return result, nil
}

// ValidateConservationFast 核对增量维护的人口与 ASN 计数，不逐槽扫描数千万条
// RouteState。完整 checkpoint 写出和最终验收仍调用 ValidateConservation。
func (state *GlobalReplayState) ValidateConservationFast() (GlobalConservation, error) {
	return state.validateConservation(false)
}

func (state *GlobalReplayState) ValidateConservation() (GlobalConservation, error) {
	return state.validateConservation(true)
}

func ratioEqual(left, right float64) bool {
	return math.Abs(left-right) <= 1e-12
}

func globalRouteKey(
	peer string,
	peerASN uint32,
	prefix string,
) RouteKey {
	parsedPrefix := netip.MustParsePrefix(prefix)
	afi := uint8(6)
	if parsedPrefix.Addr().Is4() {
		afi = 4
	}
	return RouteKey{
		PeerIP: netip.MustParseAddr(peer), PeerASN: peerASN,
		AFI: afi, Prefix: parsedPrefix,
	}
}
