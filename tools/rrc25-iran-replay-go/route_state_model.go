package replay

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"net/netip"
	"strconv"
	"time"
)

const (
	RouteStateCollectorRRC25 = uint8(25)

	routeStateQualityClean          = uint8(0)
	routeStateQualityQualified      = uint8(1)
	routeStateQualityDegraded       = uint8(2)
	routeStateQualityOrphanWithdraw = uint8(3)
)

// RouteStateKey 是唯一状态事实的逻辑主键。Prefix×VP 是这个键的维度，
// 不是另一套平级事实。
type RouteStateKey struct {
	Collector uint8
	Route     RouteKey
}

type RouteStateValue struct {
	Visible            bool
	OriginKnown        bool
	OriginASN          uint32
	ASPathKnown        bool
	ASPathDigest       [32]byte
	AttributeKnown     bool
	AttributeDigest    [32]byte
	LastRouteEventID   [16]byte
	LastArtifactIndex  uint16
	LastRecordOrdinal  uint32
	LastElementOrdinal uint32
	LastUpdatedMicros  int64
	QualityStatus      uint8
}

type routeStateEvent struct {
	Key             RouteStateKey
	Action          uint8
	OriginKnown     bool
	OriginASN       uint32
	ASPathKnown     bool
	ASPathDigest    [32]byte
	AttributeKnown  bool
	AttributeDigest [32]byte
	RouteEventID    [16]byte
	ArtifactIndex   uint16
	RecordOrdinal   uint32
	ElementOrdinal  uint32
	EventMicros     int64
	QualityStatus   uint8
}

type RouteState struct {
	Routes              map[RouteStateKey]RouteStateValue
	StateDigest         multisetDigest
	VisibleRouteCount   int64
	ProcessedEventCount int64
}

// RouteStateTransition 只描述一次已经应用到唯一 RouteState 的前后值。
// S3 投影器消费这个描述维护指标；它不保存另一套可独立推进的路由状态。
type RouteStateTransition struct {
	Event          routeStateEvent
	Previous       RouteStateValue
	PreviousExists bool
	Current        RouteStateValue
}

func NewRouteState(capacity int) (*RouteState, error) {
	if capacity < 0 {
		return nil, fmt.Errorf("route-state capacity cannot be negative")
	}
	return &RouteState{Routes: make(map[RouteStateKey]RouteStateValue, capacity)}, nil
}

func routeStateAddressBytes(address netip.Addr) ([16]byte, uint8, error) {
	if !address.IsValid() {
		return [16]byte{}, 0, fmt.Errorf("invalid address")
	}
	if address.Is4() {
		var result [16]byte
		value := address.As4()
		copy(result[:4], value[:])
		return result, 4, nil
	}
	return address.As16(), 16, nil
}

func routeStateRecordDigest(key RouteStateKey, value RouteStateValue) [32]byte {
	var raw [192]byte
	at := 0
	raw[at] = key.Collector
	at++
	peer, peerLength, _ := routeStateAddressBytes(key.Route.PeerIP)
	raw[at] = peerLength
	at++
	copy(raw[at:at+16], peer[:])
	at += 16
	binary.BigEndian.PutUint32(raw[at:at+4], key.Route.PeerASN)
	at += 4
	raw[at] = key.Route.AFI
	at++
	raw[at] = uint8(key.Route.Prefix.Bits())
	at++
	prefix, prefixLength, _ := routeStateAddressBytes(key.Route.Prefix.Addr())
	raw[at] = prefixLength
	at++
	copy(raw[at:at+16], prefix[:])
	at += 16
	flags := uint8(0)
	if value.Visible {
		flags |= 1
	}
	if value.OriginKnown {
		flags |= 2
	}
	if value.ASPathKnown {
		flags |= 4
	}
	if value.AttributeKnown {
		flags |= 8
	}
	raw[at] = flags
	at++
	binary.BigEndian.PutUint32(raw[at:at+4], value.OriginASN)
	at += 4
	copy(raw[at:at+32], value.ASPathDigest[:])
	at += 32
	copy(raw[at:at+32], value.AttributeDigest[:])
	at += 32
	copy(raw[at:at+16], value.LastRouteEventID[:])
	at += 16
	binary.BigEndian.PutUint16(raw[at:at+2], value.LastArtifactIndex)
	at += 2
	binary.BigEndian.PutUint32(raw[at:at+4], value.LastRecordOrdinal)
	at += 4
	binary.BigEndian.PutUint32(raw[at:at+4], value.LastElementOrdinal)
	at += 4
	binary.BigEndian.PutUint64(raw[at:at+8], uint64(value.LastUpdatedMicros))
	at += 8
	raw[at] = value.QualityStatus
	at++
	return sha256.Sum256(raw[:at])
}

func (state *RouteState) Apply(event routeStateEvent) error {
	_, err := state.ApplyWithTransition(event)
	return err
}

// ApplyWithTransition 与 Apply 使用同一条状态更新路径，并返回投影器所需的
// 前后值。返回成功时，Current 就是唯一 RouteState 中已经提交的当前值。
func (state *RouteState) ApplyWithTransition(
	event routeStateEvent,
) (RouteStateTransition, error) {
	if event.Key.Collector != RouteStateCollectorRRC25 {
		return RouteStateTransition{}, fmt.Errorf("route-state collector is not rrc25")
	}
	previous, existed := state.Routes[event.Key]
	if existed {
		state.StateDigest.Sub(routeStateRecordDigest(event.Key, previous))
		if previous.Visible {
			state.VisibleRouteCount--
		}
	}
	quality := event.QualityStatus
	if event.Action == actionWithdraw && !existed {
		quality = routeStateQualityOrphanWithdraw
	}
	value := RouteStateValue{
		Visible:            event.Action != actionWithdraw,
		OriginKnown:        event.OriginKnown && event.Action != actionWithdraw,
		OriginASN:          event.OriginASN,
		ASPathKnown:        event.ASPathKnown && event.Action != actionWithdraw,
		ASPathDigest:       event.ASPathDigest,
		AttributeKnown:     event.AttributeKnown,
		AttributeDigest:    event.AttributeDigest,
		LastRouteEventID:   event.RouteEventID,
		LastArtifactIndex:  event.ArtifactIndex,
		LastRecordOrdinal:  event.RecordOrdinal,
		LastElementOrdinal: event.ElementOrdinal,
		LastUpdatedMicros:  event.EventMicros,
		QualityStatus:      quality,
	}
	if !value.OriginKnown {
		value.OriginASN = 0
	}
	if !value.ASPathKnown {
		value.ASPathDigest = [32]byte{}
	}
	if !value.AttributeKnown {
		value.AttributeDigest = [32]byte{}
	}
	if value.Visible {
		state.VisibleRouteCount++
	}
	state.Routes[event.Key] = value
	state.StateDigest.Add(routeStateRecordDigest(event.Key, value))
	state.ProcessedEventCount++
	if state.VisibleRouteCount < 0 || state.VisibleRouteCount > int64(len(state.Routes)) {
		return RouteStateTransition{}, fmt.Errorf("route-state visible population is invalid")
	}
	return RouteStateTransition{
		Event: event, Previous: previous, PreviousExists: existed, Current: value,
	}, nil
}

func routeStateDecodeID(value, prefix string, bytes int) ([]byte, error) {
	if len(value) != len(prefix)+bytes*2 || value[:len(prefix)] != prefix {
		return nil, fmt.Errorf("invalid %s identity", prefix)
	}
	decoded, err := hex.DecodeString(value[len(prefix):])
	if err != nil || len(decoded) != bytes {
		return nil, fmt.Errorf("invalid %s identity", prefix)
	}
	return decoded, nil
}

func routeStateParseEventTime(value string) (int64, error) {
	if len(value) < len("2006-01-02T15:04:05Z") || value[len(value)-1] != 'Z' {
		return 0, fmt.Errorf("invalid RouteEvent timestamp %q", value)
	}
	parsed, err := time.Parse("2006-01-02T15:04:05.999999Z", value)
	if err != nil {
		parsed, err = time.Parse(time.RFC3339, value)
	}
	if err != nil {
		return 0, fmt.Errorf("invalid RouteEvent timestamp %q: %w", value, err)
	}
	return parsed.UnixMicro(), nil
}

func routeStateEventFromRow(
	row routeEventRow,
	artifact Artifact,
	artifactIndex int,
) (routeStateEvent, error) {
	if artifactIndex < 0 || artifactIndex > RouteEventUpdateCount {
		return routeStateEvent{}, fmt.Errorf("invalid RouteEvent artifact index")
	}
	peer, err := netip.ParseAddr(row.VPPeerIP)
	if err != nil {
		return routeStateEvent{}, fmt.Errorf("invalid VP peer IP: %w", err)
	}
	prefix, err := netip.ParsePrefix(row.Prefix)
	if err != nil {
		return routeStateEvent{}, fmt.Errorf("invalid RouteEvent prefix: %w", err)
	}
	maskedPrefix := prefix.Masked()
	if prefix != maskedPrefix {
		return routeStateEvent{}, fmt.Errorf("RouteEvent prefix is not canonical")
	}
	prefix = maskedPrefix
	afi := uint8(0)
	switch row.AFISAFI {
	case "ipv4_unicast":
		afi = 4
	case "ipv6_unicast":
		afi = 6
	default:
		return routeStateEvent{}, fmt.Errorf("unsupported RouteEvent AFI/SAFI %q", row.AFISAFI)
	}
	if (afi == 4) != prefix.Addr().Is4() {
		return routeStateEvent{}, fmt.Errorf("RouteEvent AFI/prefix mismatch")
	}
	if VPIdentifier(peer, row.VPASN) != row.VPID {
		return routeStateEvent{}, fmt.Errorf("RouteEvent VP identity mismatch")
	}
	action := uint8(0)
	switch row.Action {
	case "rib_snapshot":
		action = actionRIBSnapshot
	case "announce":
		action = actionAnnounce
	case "withdraw":
		action = actionWithdraw
	default:
		return routeStateEvent{}, fmt.Errorf("unsupported RouteEvent action %q", row.Action)
	}
	eventID, err := routeStateDecodeID(row.RouteEventID, "rte_v1_", 16)
	if err != nil {
		return routeStateEvent{}, err
	}
	eventMicros, err := routeStateParseEventTime(row.EventTimeUTC)
	if err != nil {
		return routeStateEvent{}, err
	}
	result := routeStateEvent{
		Key: RouteStateKey{
			Collector: RouteStateCollectorRRC25,
			Route:     RouteKey{PeerIP: peer, PeerASN: row.VPASN, AFI: afi, Prefix: prefix},
		},
		Action: action, ArtifactIndex: uint16(artifactIndex),
		RecordOrdinal: row.RecordOrdinal, ElementOrdinal: row.ElementOrdinal,
		EventMicros: eventMicros,
	}
	copy(result.RouteEventID[:], eventID)
	if row.OriginASN != nil {
		result.OriginKnown = true
		result.OriginASN = *row.OriginASN
	}
	if row.ASPathID != nil {
		decoded, err := routeStateDecodeID(*row.ASPathID, "asp_v1_", 32)
		if err != nil {
			return routeStateEvent{}, err
		}
		result.ASPathKnown = true
		copy(result.ASPathDigest[:], decoded)
	}
	if row.AttributeSHA256 != nil {
		decoded, err := hex.DecodeString(*row.AttributeSHA256)
		if err != nil || len(decoded) != 32 {
			return routeStateEvent{}, fmt.Errorf("invalid RouteEvent attribute identity")
		}
		result.AttributeKnown = true
		copy(result.AttributeDigest[:], decoded)
	}
	if len(row.ParserWarnings) > 0 {
		result.QualityStatus = routeStateQualityDegraded
	} else if len(row.QualityFlags) > 0 {
		result.QualityStatus = routeStateQualityQualified
	}
	if action == actionWithdraw {
		if row.OriginASN != nil || row.ASPathID != nil {
			return routeStateEvent{}, fmt.Errorf("withdraw RouteEvent contains current path state")
		}
	} else if !result.OriginKnown || !result.ASPathKnown {
		if result.QualityStatus < routeStateQualityQualified {
			result.QualityStatus = routeStateQualityQualified
		}
	}
	if artifact.CollectorID != "rrc25" {
		return routeStateEvent{}, fmt.Errorf("RouteEvent artifact collector mismatch")
	}
	expectedID := routeEventID(artifact.FileSHA256, row.RecordOrdinal, row.ElementOrdinal)
	if expectedID != row.RouteEventID {
		return routeStateEvent{}, fmt.Errorf("RouteEvent identity/source coordinate mismatch")
	}
	return result, nil
}

func routeStateQualityName(value uint8) string {
	switch value {
	case routeStateQualityClean:
		return "clean"
	case routeStateQualityQualified:
		return "qualified"
	case routeStateQualityDegraded:
		return "degraded"
	case routeStateQualityOrphanWithdraw:
		return "orphan_withdraw"
	default:
		return "unknown_" + strconv.Itoa(int(value))
	}
}
