package replay

import (
	"compress/gzip"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/netip"
	"os"
	"path/filepath"
	"sort"
	"time"
)

type asnFamilyKey struct {
	ASN uint32
	AFI uint8
}

type visibilityCounter struct {
	Total   int `json:"total"`
	Visible int `json:"visible"`
}

type ReplayState struct {
	Mapping            *CountryMapping
	Baseline           map[RouteKey]uint32
	Routes             map[RouteKey]StateEntry
	Counters           map[asnFamilyKey]visibilityCounter
	BaselineASNs       map[uint32]struct{}
	BaselinePrefixes   map[string]struct{}
	VisiblePrefixVP    int
	CohortID           string
	CatchUpMetrics     []Observation
	FormalObservations []Observation
}

func NewReplayState(mapping *CountryMapping, baseline map[RouteKey]uint32) (*ReplayState, error) {
	if mapping == nil || len(baseline) == 0 {
		return nil, fmt.Errorf("mapping and baseline are required")
	}
	state := &ReplayState{
		Mapping:          mapping,
		Baseline:         make(map[RouteKey]uint32, len(baseline)),
		Routes:           make(map[RouteKey]StateEntry, len(baseline)),
		Counters:         make(map[asnFamilyKey]visibilityCounter),
		BaselineASNs:     make(map[uint32]struct{}),
		BaselinePrefixes: make(map[string]struct{}),
		VisiblePrefixVP:  len(baseline),
	}
	members := make([]BaselineMember, 0, len(baseline))
	for key, origin := range baseline {
		state.Baseline[key] = origin
		state.BaselineASNs[origin] = struct{}{}
		state.BaselinePrefixes[key.Prefix.String()] = struct{}{}
		state.Routes[key] = StateEntry{
			Key: key,
			Value: RouteValue{
				Present: true, OriginKnown: true, OriginASN: origin,
				LastAction:      actionAnnounce,
				LastEventMicros: catchUpStart.UnixMicro(),
			},
		}
		counterKey := asnFamilyKey{ASN: origin, AFI: key.AFI}
		counter := state.Counters[counterKey]
		counter.Total++
		counter.Visible++
		state.Counters[counterKey] = counter
		members = append(members, BaselineMember{
			Key: key, KeyText: key.Canonical(), OriginASN: origin,
		})
	}
	sort.Slice(members, func(i, j int) bool {
		if members[i].KeyText == members[j].KeyText {
			return members[i].OriginASN < members[j].OriginASN
		}
		return members[i].KeyText < members[j].KeyText
	})
	state.CohortID = stableID("cohort_go_v1_", map[string]any{
		"collector_id":     "rrc25",
		"country_code":     "IR",
		"mapping_version":  mapping.MappingVersion,
		"seed_observed_at": CatchUpStartUTC,
		"members":          members,
	}, 32)
	return state, nil
}

func routeVisible(value RouteValue, expectedOrigin uint32) bool {
	return value.Present && value.OriginKnown && value.OriginASN == expectedOrigin
}

func valueFromEvent(event ParsedEvent) RouteValue {
	return RouteValue{
		Present:         event.Action == actionAnnounce,
		OriginKnown:     event.Action == actionAnnounce && event.OriginKnown,
		OriginASN:       event.OriginASN,
		LastAction:      event.Action,
		LastEventMicros: event.EventMicros,
		ArtifactIndex:   event.ArtifactIndex,
		RecordOrdinal:   event.RecordOrdinal,
		ElementOrdinal:  event.ElementOrdinal,
	}
}

func (state *ReplayState) Apply(event ParsedEvent) {
	if baselineOrigin, baseline := state.Baseline[event.Key]; baseline {
		current := state.Routes[event.Key]
		before := routeVisible(current.Value, baselineOrigin)
		current.Value = valueFromEvent(event)
		state.Routes[event.Key] = current
		after := routeVisible(current.Value, baselineOrigin)
		if before != after {
			counterKey := asnFamilyKey{ASN: baselineOrigin, AFI: event.Key.AFI}
			counter := state.Counters[counterKey]
			if after {
				counter.Visible++
				state.VisiblePrefixVP++
			} else {
				counter.Visible--
				state.VisiblePrefixVP--
			}
			state.Counters[counterKey] = counter
		}
		return
	}

	current, dynamic := state.Routes[event.Key]
	if dynamic && !current.Dynamic {
		return
	}
	if event.Action == actionWithdraw {
		if dynamic {
			delete(state.Routes, event.Key)
		}
		return
	}
	if event.OriginKnown &&
		state.Mapping.Membership(event.OriginASN) == MembershipIR {
		if _, belongsToBaselineASN := state.BaselineASNs[event.OriginASN]; !belongsToBaselineASN {
			state.Routes[event.Key] = StateEntry{
				Key: event.Key, Value: valueFromEvent(event), Dynamic: true,
			}
			return
		}
	}
	if dynamic {
		delete(state.Routes, event.Key)
	}
}

func (state *ReplayState) ApplySlot(
	outputRoot string,
	meta SlotSpoolMeta,
) error {
	shards := append([]ShardSpoolMeta(nil), meta.Shards...)
	sort.Slice(shards, func(i, j int) bool { return shards[i].Shard < shards[j].Shard })
	for _, shard := range shards {
		path := filepath.Join(outputRoot, filepath.FromSlash(shard.Path))
		reader, err := openSpool(path)
		if err != nil {
			return err
		}
		count := int64(0)
		for {
			event, err := reader.Next()
			if err == io.EOF {
				break
			}
			if err != nil {
				reader.Close()
				return err
			}
			if int(event.ArtifactIndex) != meta.ArtifactIndex ||
				shardFor(event.Key, len(shards)) != shard.Shard {
				reader.Close()
				return fmt.Errorf("spool event coordinate mismatch")
			}
			state.Apply(event)
			count++
		}
		if err := reader.Close(); err != nil {
			return err
		}
		if count != shard.RecordCount {
			return fmt.Errorf("spool record count mismatch at slot %d shard %d",
				meta.ArtifactIndex, shard.Shard)
		}
	}
	return nil
}

type familyClass struct {
	Total   int
	Visible int
	Class   string
}

func classify(total, visible int) string {
	switch {
	case total <= 0:
		return "not_applicable"
	case visible == total:
		return "fully_visible"
	case visible == 0:
		return "fully_invisible"
	default:
		return "partially_visible"
	}
}

func (state *ReplayState) Snapshot(
	observedAt, slotStart, slotEnd, role string,
	counts UpdateCounts,
) Observation {
	byASN := make(map[uint32]map[uint8]familyClass)
	families := map[uint8]*FamilyMetrics{
		4: {FullyVisibleASNs: []uint32{}, PartiallyVisibleASNs: []uint32{}, FullyInvisibleASNs: []uint32{}},
		6: {FullyVisibleASNs: []uint32{}, PartiallyVisibleASNs: []uint32{}, FullyInvisibleASNs: []uint32{}},
	}
	for key, counter := range state.Counters {
		if byASN[key.ASN] == nil {
			byASN[key.ASN] = make(map[uint8]familyClass)
		}
		class := classify(counter.Total, counter.Visible)
		byASN[key.ASN][key.AFI] = familyClass{
			Total: counter.Total, Visible: counter.Visible, Class: class,
		}
		family := families[key.AFI]
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
	for _, family := range families {
		sort.Slice(family.FullyVisibleASNs, func(i, j int) bool { return family.FullyVisibleASNs[i] < family.FullyVisibleASNs[j] })
		sort.Slice(family.PartiallyVisibleASNs, func(i, j int) bool { return family.PartiallyVisibleASNs[i] < family.PartiallyVisibleASNs[j] })
		sort.Slice(family.FullyInvisibleASNs, func(i, j int) bool { return family.FullyInvisibleASNs[i] < family.FullyInvisibleASNs[j] })
		family.BaselineASNCount = len(family.FullyVisibleASNs) +
			len(family.PartiallyVisibleASNs) + len(family.FullyInvisibleASNs)
		family.VisibleASNCount = len(family.FullyVisibleASNs) + len(family.PartiallyVisibleASNs)
	}

	dual := map[string][]uint32{
		"fully_visible": {}, "partially_visible": {},
		"fully_invisible": {}, "ipv4_invisible_ipv6_visible": {},
	}
	asns := sortedUint32(state.BaselineASNs)
	visibleCount := 0
	affectedCount := 0
	for _, asn := range asns {
		applicable := make([]familyClass, 0, 2)
		for _, afi := range []uint8{4, 6} {
			if value, exists := byASN[asn][afi]; exists {
				applicable = append(applicable, value)
			}
		}
		class := "fully_visible"
		allInvisible := len(applicable) > 0
		for _, value := range applicable {
			if value.Class != "fully_visible" {
				class = "partially_visible"
			}
			if value.Class != "fully_invisible" {
				allInvisible = false
			}
		}
		if allInvisible {
			class = "fully_invisible"
		}
		dual[class] = append(dual[class], asn)
		if class == "fully_visible" {
			visibleCount++
		} else {
			affectedCount++
			if class == "partially_visible" {
				visibleCount++
			}
		}
		v4, has4 := byASN[asn][4]
		v6, has6 := byASN[asn][6]
		if has4 && has6 && v4.Class == "fully_invisible" &&
			(v6.Class == "fully_visible" || v6.Class == "partially_visible") {
			dual["ipv4_invisible_ipv6_visible"] = append(
				dual["ipv4_invisible_ipv6_visible"], asn,
			)
		}
	}
	dynamicASNs := make(map[uint32]struct{})
	for _, entry := range state.Routes {
		if entry.Dynamic && entry.Value.Present && entry.Value.OriginKnown {
			dynamicASNs[entry.Value.OriginASN] = struct{}{}
		}
	}
	prefixRatio := float64(state.VisiblePrefixVP) / float64(len(state.Baseline))
	affectedRatio := float64(affectedCount) / float64(len(asns))
	visibleRatio := float64(visibleCount) / float64(len(asns))
	identity := map[string]any{
		"cohort_id": state.CohortID, "observed_at": observedAt,
		"visible_prefix_vp_count": state.VisiblePrefixVP,
	}
	return Observation{
		SchemaVersion: ObservationVersion,
		SnapshotID:    stableID("snapshot_go_v1_", identity, 32),
		ObservedAt:    observedAt, SlotStartUTC: slotStart,
		SlotEndUTC: slotEnd, SlotRole: role, CohortID: state.CohortID,
		BaselineASNCount:      len(asns),
		BaselinePrefixVPCount: len(state.Baseline),
		VisiblePrefixVPCount:  state.VisiblePrefixVP,
		VisiblePrefixVPRatio:  prefixRatio,
		AffectedASNCount:      affectedCount,
		AffectedASNRatio:      affectedRatio,
		VisibleOriginASNCount: visibleCount,
		VisibleOriginASNRatio: visibleRatio,
		IPv4:                  *families[4], IPv6: *families[6],
		DualClassifications: dual,
		DynamicIRASNs:       sortedUint32(dynamicASNs),
		UpdateCounts:        counts,
	}
}

type checkpointEntry struct {
	PeerIP         string     `json:"peer_ip"`
	PeerASN        uint32     `json:"peer_asn"`
	AFI            uint8      `json:"afi"`
	Prefix         string     `json:"prefix"`
	BaselineOrigin *uint32    `json:"baseline_origin,omitempty"`
	Value          RouteValue `json:"value"`
	Dynamic        bool       `json:"dynamic"`
}

type Checkpoint struct {
	SchemaVersion      string            `json:"schema_version"`
	EngineVersion      string            `json:"engine_version"`
	Phase              string            `json:"phase"`
	ProcessedSlot      int               `json:"processed_slot"`
	CreatedAt          string            `json:"created_at"`
	MappingVersion     string            `json:"mapping_version"`
	CohortID           string            `json:"cohort_id"`
	VisiblePrefixVP    int               `json:"visible_prefix_vp"`
	Entries            []checkpointEntry `json:"entries"`
	CatchUpMetrics     []Observation     `json:"catch_up_metrics"`
	FormalObservations []Observation     `json:"formal_observations"`
}

func (state *ReplayState) checkpoint(phase string, processedSlot int) Checkpoint {
	keys := make([]RouteKey, 0, len(state.Routes))
	for key := range state.Routes {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool { return keys[i].Less(keys[j]) })
	entries := make([]checkpointEntry, 0, len(keys))
	for _, key := range keys {
		entry := state.Routes[key]
		var baselineOrigin *uint32
		if origin, exists := state.Baseline[key]; exists {
			copy := origin
			baselineOrigin = &copy
		}
		entries = append(entries, checkpointEntry{
			PeerIP: key.PeerIP.String(), PeerASN: key.PeerASN,
			AFI: key.AFI, Prefix: key.Prefix.String(),
			BaselineOrigin: baselineOrigin, Value: entry.Value,
			Dynamic: entry.Dynamic,
		})
	}
	return Checkpoint{
		SchemaVersion: CheckpointVersion, EngineVersion: EngineVersion,
		Phase: phase, ProcessedSlot: processedSlot,
		CreatedAt:      time.Now().UTC().Format(time.RFC3339),
		MappingVersion: state.Mapping.MappingVersion, CohortID: state.CohortID,
		VisiblePrefixVP:    state.VisiblePrefixVP,
		Entries:            entries,
		CatchUpMetrics:     append([]Observation(nil), state.CatchUpMetrics...),
		FormalObservations: append([]Observation(nil), state.FormalObservations...),
	}
}

func WriteCheckpoint(path string, state *ReplayState, phase string, processedSlot int) error {
	checkpoint := state.checkpoint(phase, processedSlot)
	temp := path + ".tmp"
	file, err := os.OpenFile(temp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o640)
	if err != nil {
		return err
	}
	compressed, err := gzip.NewWriterLevel(file, gzip.BestSpeed)
	if err != nil {
		file.Close()
		return err
	}
	encoder := json.NewEncoder(compressed)
	if err := encoder.Encode(checkpoint); err != nil {
		compressed.Close()
		file.Close()
		return err
	}
	if err := compressed.Close(); err != nil {
		file.Close()
		return err
	}
	if err := file.Sync(); err != nil {
		file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	return os.Rename(temp, path)
}

func LoadCheckpoint(path string, mapping *CountryMapping) (*ReplayState, Checkpoint, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, Checkpoint{}, err
	}
	defer file.Close()
	compressed, err := gzip.NewReader(file)
	if err != nil {
		return nil, Checkpoint{}, err
	}
	defer compressed.Close()
	var checkpoint Checkpoint
	if err := json.NewDecoder(compressed).Decode(&checkpoint); err != nil {
		return nil, Checkpoint{}, err
	}
	if checkpoint.SchemaVersion != CheckpointVersion ||
		checkpoint.EngineVersion != EngineVersion ||
		checkpoint.MappingVersion != mapping.MappingVersion {
		return nil, Checkpoint{}, fmt.Errorf("checkpoint identity mismatch")
	}
	baseline := make(map[RouteKey]uint32)
	routes := make(map[RouteKey]StateEntry)
	for _, entry := range checkpoint.Entries {
		ip, err := netip.ParseAddr(entry.PeerIP)
		if err != nil {
			return nil, Checkpoint{}, err
		}
		prefix, err := netip.ParsePrefix(entry.Prefix)
		if err != nil {
			return nil, Checkpoint{}, err
		}
		key := RouteKey{PeerIP: ip, PeerASN: entry.PeerASN, AFI: entry.AFI, Prefix: prefix}
		if entry.BaselineOrigin != nil {
			baseline[key] = *entry.BaselineOrigin
		}
		routes[key] = StateEntry{Key: key, Value: entry.Value, Dynamic: entry.Dynamic}
	}
	state, err := NewReplayState(mapping, baseline)
	if err != nil {
		return nil, Checkpoint{}, err
	}
	state.Routes = routes
	state.Counters = make(map[asnFamilyKey]visibilityCounter)
	state.VisiblePrefixVP = 0
	for key, origin := range state.Baseline {
		counterKey := asnFamilyKey{ASN: origin, AFI: key.AFI}
		counter := state.Counters[counterKey]
		counter.Total++
		if current, exists := state.Routes[key]; exists && routeVisible(current.Value, origin) {
			counter.Visible++
			state.VisiblePrefixVP++
		}
		state.Counters[counterKey] = counter
	}
	if state.VisiblePrefixVP != checkpoint.VisiblePrefixVP ||
		state.CohortID != checkpoint.CohortID {
		return nil, Checkpoint{}, fmt.Errorf("checkpoint state reconciliation failed")
	}
	state.CatchUpMetrics = checkpoint.CatchUpMetrics
	state.FormalObservations = checkpoint.FormalObservations
	return state, checkpoint, nil
}

func floatEqual(left, right float64) bool {
	return math.Abs(left-right) <= 1e-12
}
