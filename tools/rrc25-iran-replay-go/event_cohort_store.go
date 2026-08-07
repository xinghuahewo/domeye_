package replay

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/netip"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"time"
)

const (
	EventCohortStoreVersion     = "rrc25-event-cohort-store/v1"
	EventCohortVersion          = "rrc25-event-cohort/v1"
	EventCohortMemberVersion    = "rrc25-event-cohort-member/v1"
	EventLifecycleVersion       = "country_outage_event_lifecycle_snapshot/v1"
	EventCohortProjectorName    = "domeye_country_outage_event_cohort_projector"
	EventCohortProjectorVersion = "1.0.0"
)

type EventCohortStoreConfig struct {
	RouteEventRoot              string
	RouteStateRoot              string
	RawRoot                     string
	SelectionPath               string
	PeerSessionRoot             string
	CompatibleMappingPath       string
	RevisedMappingPath          string
	LifecycleSnapshotPath       string
	Output                      string
	RouteEventImplementationID  string
	RouteStateImplementationID  string
	PeerSessionImplementationID string
	ImplementationID            string
	Resume                      bool
	Progress                    func(string)
}

func (config EventCohortStoreConfig) progress(message string) {
	if config.Progress != nil {
		config.Progress(message)
	}
}

type EventLifecycle struct {
	IncidentID                   string  `json:"incident_id"`
	LegacyReference              string  `json:"legacy_reference"`
	CountryCode                  string  `json:"country_code"`
	SourceCode                   string  `json:"source_code"`
	CollectorID                  string  `json:"collector_id"`
	DetectedAtUTC                string  `json:"detected_at_utc"`
	CohortStatePointUTC          string  `json:"cohort_state_point_utc"`
	WindowStartUTC               string  `json:"window_start_utc"`
	RequestedWindowStartUTC      string  `json:"requested_window_start_utc"`
	LeftBoundaryMissingSlotCount int     `json:"left_boundary_missing_slot_count"`
	EventEndAtUTC                *string `json:"event_end_at_utc"`
	EventDurationSeconds         *int64  `json:"event_duration_seconds"`
	ProjectionEndStatePointUTC   string  `json:"projection_end_state_point_utc"`
	LifecycleState               string  `json:"lifecycle_state"`
	IsFinalInDataRange           bool    `json:"is_final_in_data_range"`
	LifecycleSource              string  `json:"lifecycle_source"`
}

type EventLifecycleSnapshot struct {
	SchemaVersion         string           `json:"schema_version"`
	Status                string           `json:"status"`
	CollectorID           string           `json:"collector_id"`
	WindowStartUTC        string           `json:"window_start_utc"`
	WindowEndExclusiveUTC string           `json:"window_end_exclusive_utc"`
	IntervalSeconds       int              `json:"interval_seconds"`
	EventCount            int              `json:"event_count"`
	IncidentInputSHA256   string           `json:"incident_input_sha256"`
	DetailSourceSemantics string           `json:"detail_source_semantics"`
	WindowSemantics       string           `json:"window_semantics"`
	LifecycleStateCounts  map[string]int   `json:"lifecycle_state_counts"`
	Events                []EventLifecycle `json:"events"`
	ContentSHA256         string           `json:"content_sha256"`
	SnapshotID            string           `json:"snapshot_id"`
}

type EventCohortRouteObservation struct {
	VPID               string `json:"vp_id"`
	PeerIP             string `json:"peer_ip"`
	OriginASN          uint32 `json:"origin_asn"`
	ASPathStatus       string `json:"as_path_status"`
	ASPathID           string `json:"as_path_id,omitempty"`
	RouteQualityStatus string `json:"route_quality_status"`
	LastRouteEventID   string `json:"last_route_event_id"`
	LastUpdatedUTC     string `json:"last_updated_utc"`
}

type EventCohortDirection struct {
	PeerASN               uint32                        `json:"peer_asn"`
	RouteObservationCount int                           `json:"route_observation_count"`
	RouteObservations     []EventCohortRouteObservation `json:"route_observations"`
}

type EventCohortMember struct {
	SchemaVersion          string                 `json:"schema_version"`
	CohortID               string                 `json:"cohort_id"`
	CohortMemberID         string                 `json:"cohort_member_id"`
	CountryCode            string                 `json:"country_code"`
	Prefix                 string                 `json:"prefix"`
	AddressFamily          string                 `json:"address_family"`
	OriginASNs             []uint32               `json:"origin_asns"`
	ExpectedDirectionCount int                    `json:"expected_direction_count"`
	ExpectedDirections     []EventCohortDirection `json:"expected_directions"`
}

type EventCohortManifest struct {
	SchemaVersion                  string              `json:"schema_version"`
	Status                         string              `json:"status"`
	CohortID                       string              `json:"cohort_id"`
	CountryCode                    string              `json:"country_code"`
	CohortStatePointUTC            string              `json:"cohort_state_point_utc"`
	CohortStateSlot                int                 `json:"cohort_state_slot"`
	SourceRouteStateDatasetID      string              `json:"source_route_state_dataset_id"`
	SourceRouteStateSlotSHA256     string              `json:"source_route_state_slot_sha256"`
	SourceRouteStateDigest         string              `json:"source_route_state_digest"`
	ReplayStartCheckpointID        string              `json:"replay_start_checkpoint_id"`
	ReplayStartCheckpointSlot      int                 `json:"replay_start_checkpoint_slot"`
	ReplayedUpdateSlotCount        int                 `json:"replayed_update_slot_count"`
	PopulationSemantics            string              `json:"population_semantics"`
	DirectionSemantics             string              `json:"direction_semantics"`
	MemberCount                    int64               `json:"member_count"`
	IPv4MemberCount                int64               `json:"ipv4_member_count"`
	IPv6MemberCount                int64               `json:"ipv6_member_count"`
	OriginASNCount                 int                 `json:"origin_asn_count"`
	ExpectedDirectionRelationCount int64               `json:"expected_direction_relation_count"`
	RouteObservationCount          int64               `json:"route_observation_count"`
	Members                        RouteEventStoreFile `json:"members"`
	ContentSHA256                  string              `json:"content_sha256"`
}

type EventCohortBinding struct {
	IncidentID                   string  `json:"incident_id"`
	LegacyReference              string  `json:"legacy_reference"`
	CountryCode                  string  `json:"country_code"`
	DetectedAtUTC                string  `json:"detected_at_utc"`
	CohortID                     string  `json:"cohort_id"`
	CohortStatePointUTC          string  `json:"cohort_state_point_utc"`
	WindowStartUTC               string  `json:"window_start_utc"`
	RequestedWindowStartUTC      string  `json:"requested_window_start_utc"`
	LeftBoundaryMissingSlotCount int     `json:"left_boundary_missing_slot_count"`
	EventEndAtUTC                *string `json:"event_end_at_utc"`
	EventDurationSeconds         *int64  `json:"event_duration_seconds"`
	ProjectionEndStatePointUTC   string  `json:"projection_end_state_point_utc"`
	LifecycleState               string  `json:"lifecycle_state"`
	IsFinalInDataRange           bool    `json:"is_final_in_data_range"`
}

type EventCohortStoreManifest struct {
	SchemaVersion                  string                `json:"schema_version"`
	Status                         string                `json:"status"`
	RunID                          string                `json:"run_id"`
	DatasetID                      string                `json:"dataset_id"`
	CollectorID                    string                `json:"collector_id"`
	WindowStartUTC                 string                `json:"window_start_utc"`
	WindowEndExclusiveUTC          string                `json:"window_end_exclusive_utc"`
	ImplementationID               string                `json:"implementation_id"`
	ProjectorName                  string                `json:"projector_name"`
	ProjectorVersion               string                `json:"projector_version"`
	SourceRouteEventDatasetID      string                `json:"source_route_event_dataset_id"`
	SourceRouteEventContentSHA256  string                `json:"source_route_event_content_sha256"`
	SourceRouteStateDatasetID      string                `json:"source_route_state_dataset_id"`
	SourceRouteStateContentSHA256  string                `json:"source_route_state_content_sha256"`
	SourceRouteStateManifestSHA256 string                `json:"source_route_state_manifest_sha256"`
	SourcePeerSessionDatasetID     string                `json:"source_peer_session_dataset_id"`
	SourcePeerSessionContentSHA256 string                `json:"source_peer_session_content_sha256"`
	LifecycleSnapshotID            string                `json:"lifecycle_snapshot_id"`
	LifecycleSnapshotContentSHA256 string                `json:"lifecycle_snapshot_content_sha256"`
	LifecycleSnapshotFileSHA256    string                `json:"lifecycle_snapshot_file_sha256"`
	MappingVersion                 string                `json:"mapping_version"`
	MappingCompatibleSHA256        string                `json:"mapping_compatible_sha256"`
	MappingRevisedSHA256           string                `json:"mapping_revised_sha256"`
	RouteStateAuthority            string                `json:"route_state_authority"`
	DirectionDefinition            string                `json:"direction_definition"`
	NewPopulationPolicy            string                `json:"new_population_policy"`
	SessionRouteBoundary           string                `json:"session_route_boundary"`
	EventCount                     int                   `json:"event_count"`
	UniqueCohortCount              int                   `json:"unique_cohort_count"`
	CohortMemberCount              int64                 `json:"cohort_member_count"`
	ExpectedDirectionRelationCount int64                 `json:"expected_direction_relation_count"`
	RouteObservationCount          int64                 `json:"route_observation_count"`
	Events                         RouteEventStoreFile   `json:"events"`
	Cohorts                        []EventCohortManifest `json:"cohorts"`
	ContentSHA256                  string                `json:"content_sha256"`
}

type eventCohortStoreMarker struct {
	SchemaVersion                  string `json:"schema_version"`
	RunID                          string `json:"run_id"`
	DatasetID                      string `json:"dataset_id"`
	ImplementationID               string `json:"implementation_id"`
	SourceRouteStateDatasetID      string `json:"source_route_state_dataset_id"`
	SourceRouteStateContentSHA256  string `json:"source_route_state_content_sha256"`
	SourcePeerSessionDatasetID     string `json:"source_peer_session_dataset_id"`
	SourcePeerSessionContentSHA256 string `json:"source_peer_session_content_sha256"`
	LifecycleSnapshotID            string `json:"lifecycle_snapshot_id"`
	LifecycleSnapshotContentSHA256 string `json:"lifecycle_snapshot_content_sha256"`
}

type eventCohortTarget struct {
	CountryCode string
	CountryID   uint16
	StatePoint  string
	StateSlot   int
	CohortID    string
	Events      []EventLifecycle
}

func eventCohortContentSHA(value EventCohortManifest) string {
	value.ContentSHA256 = ""
	raw, _ := json.Marshal(value)
	digest := sha256.Sum256(raw)
	return hex.EncodeToString(digest[:])
}

func eventCohortStoreContentSHA(value EventCohortStoreManifest) string {
	value.ContentSHA256 = ""
	raw, _ := json.Marshal(value)
	digest := sha256.Sum256(raw)
	return hex.EncodeToString(digest[:])
}

func loadEventLifecycleSnapshot(path string) (EventLifecycleSnapshot, string, error) {
	var snapshot EventLifecycleSnapshot
	raw, err := os.ReadFile(path)
	if err != nil {
		return snapshot, "", err
	}
	if err := json.Unmarshal(raw, &snapshot); err != nil {
		return snapshot, "", err
	}
	var canonical map[string]any
	if err := json.Unmarshal(raw, &canonical); err != nil {
		return snapshot, "", err
	}
	delete(canonical, "content_sha256")
	delete(canonical, "snapshot_id")
	canonicalRaw, err := json.Marshal(canonical)
	if err != nil {
		return snapshot, "", err
	}
	contentDigest := sha256.Sum256(canonicalRaw)
	contentSHA := hex.EncodeToString(contentDigest[:])
	fileDigest := sha256.Sum256(raw)
	fileSHA := hex.EncodeToString(fileDigest[:])
	if snapshot.SchemaVersion != EventLifecycleVersion || snapshot.Status != "complete" ||
		snapshot.CollectorID != "rrc25" || snapshot.WindowStartUTC != RouteEventWindowStartUTC ||
		snapshot.WindowEndExclusiveUTC != RouteEventWindowEndUTC || snapshot.IntervalSeconds != 300 ||
		snapshot.EventCount != len(snapshot.Events) || snapshot.EventCount == 0 ||
		snapshot.ContentSHA256 != contentSHA ||
		snapshot.SnapshotID != "event_lifecycle_snapshot_v1_"+contentSHA[:32] {
		return snapshot, fileSHA, fmt.Errorf("event lifecycle snapshot identity mismatch")
	}
	stateCounts := make(map[string]int)
	seen := make(map[string]struct{}, len(snapshot.Events))
	for _, event := range snapshot.Events {
		if _, exists := seen[event.LegacyReference]; exists || event.IncidentID == "" ||
			event.CollectorID != "rrc25" || event.SourceCode == "" ||
			(event.LifecycleState != "event_end_recorded" &&
				event.LifecycleState != "event_end_outside_data_range" &&
				event.LifecycleState != "event_end_unknown") {
			return snapshot, fileSHA, fmt.Errorf("event lifecycle population mismatch")
		}
		seen[event.LegacyReference] = struct{}{}
		stateCounts[event.LifecycleState]++
	}
	if !mapsEqualStringInt(snapshot.LifecycleStateCounts, stateCounts) {
		return snapshot, fileSHA, fmt.Errorf("event lifecycle state count mismatch")
	}
	return snapshot, fileSHA, nil
}

func mapsEqualStringInt(left, right map[string]int) bool {
	if len(left) != len(right) {
		return false
	}
	for key, value := range left {
		if right[key] != value {
			return false
		}
	}
	return true
}

func eventCohortSlot(statePoint string) (int, error) {
	parsed, err := time.Parse(time.RFC3339, statePoint)
	if err != nil || parsed.Format(time.RFC3339) != statePoint {
		return 0, fmt.Errorf("invalid cohort state point %q", statePoint)
	}
	start, _ := time.Parse(time.RFC3339, RouteEventWindowStartUTC)
	delta := parsed.Sub(start)
	if delta < 0 || delta%(5*time.Minute) != 0 {
		return 0, fmt.Errorf("cohort state point is outside aligned 224-310 points")
	}
	slot := int(delta / (5 * time.Minute))
	if slot < 0 || slot >= RouteStateFinalSlot {
		return 0, fmt.Errorf("cohort state slot is outside 224-310")
	}
	return slot, nil
}

func eventCohortIdentity(
	routeState RouteStateStoreManifest,
	peerSessions PeerSessionStoreManifest,
	lifecycle EventLifecycleSnapshot,
	implementationID string,
) (string, string) {
	runID := stableID("event_cohort_run_v1_", map[string]any{
		"schema_version":                     EventCohortStoreVersion,
		"source_route_state_dataset_id":      routeState.DatasetID,
		"source_route_state_content_sha256":  routeState.ContentSHA256,
		"source_peer_session_dataset_id":     peerSessions.DatasetID,
		"source_peer_session_content_sha256": peerSessions.ContentSHA256,
		"lifecycle_snapshot_id":              lifecycle.SnapshotID,
		"lifecycle_snapshot_content_sha256":  lifecycle.ContentSHA256,
		"implementation_id":                  implementationID,
		"projector_name":                     EventCohortProjectorName,
		"projector_version":                  EventCohortProjectorVersion,
	}, 32)
	datasetID := stableID("event_cohort_dataset_v1_", map[string]any{
		"run_id": runID, "collector_id": "rrc25",
	}, 32)
	return runID, datasetID
}

func eventCohortID(datasetID, country, statePoint, stateDigest string) string {
	return stableID("country_event_cohort_v1_", map[string]any{
		"dataset_id":                datasetID,
		"country_code":              country,
		"cohort_state_point_utc":    statePoint,
		"source_route_state_digest": stateDigest,
		"population_semantics":      "visible_country_origin_routes_grouped_by_unique_prefix_and_peer_asn",
	}, 32)
}

func buildEventCohortTargets(
	snapshot EventLifecycleSnapshot,
	mapping *GlobalCountryMapping,
	datasetID string,
) ([]eventCohortTarget, map[string]int, error) {
	targets := make([]eventCohortTarget, 0, len(snapshot.Events))
	byKey := make(map[string]int)
	for _, event := range snapshot.Events {
		countryID, exists := mapping.IDForCode(event.CountryCode)
		if !exists || event.CountryCode == UnknownCountryCode {
			return nil, nil, fmt.Errorf("event country absent from frozen mapping: %s", event.CountryCode)
		}
		slot, err := eventCohortSlot(event.CohortStatePointUTC)
		if err != nil {
			return nil, nil, fmt.Errorf("%s: %w", event.LegacyReference, err)
		}
		key := fmt.Sprintf("%s|%04d", event.CountryCode, slot)
		index, exists := byKey[key]
		if !exists {
			index = len(targets)
			byKey[key] = index
			targets = append(targets, eventCohortTarget{
				CountryCode: event.CountryCode,
				CountryID:   countryID,
				StatePoint:  event.CohortStatePointUTC,
				StateSlot:   slot,
				CohortID: stableID("pending_event_cohort_v1_", map[string]any{
					"dataset_id": datasetID, "country_code": event.CountryCode,
					"cohort_state_point_utc": event.CohortStatePointUTC,
				}, 32),
			})
		}
		targets[index].Events = append(targets[index].Events, event)
	}
	sort.Slice(targets, func(i, j int) bool {
		if targets[i].StateSlot != targets[j].StateSlot {
			return targets[i].StateSlot < targets[j].StateSlot
		}
		return targets[i].CountryCode < targets[j].CountryCode
	})
	byReference := make(map[string]int, len(snapshot.Events))
	for index := range targets {
		for _, event := range targets[index].Events {
			byReference[event.LegacyReference] = index
		}
	}
	return targets, byReference, nil
}

type eventCountryRouteIndex map[uint16]map[RouteStateKey]struct{}

func newEventCountryRouteIndex(
	state *RouteState,
	mapping *GlobalCountryMapping,
	wanted map[uint16]struct{},
) eventCountryRouteIndex {
	result := make(eventCountryRouteIndex, len(wanted))
	for id := range wanted {
		result[id] = make(map[RouteStateKey]struct{})
	}
	for key, value := range state.Routes {
		if !value.Visible || !value.OriginKnown {
			continue
		}
		countryID := mapping.CountryID(value.OriginASN)
		if routes, exists := result[countryID]; exists {
			routes[key] = struct{}{}
		}
	}
	return result
}

func applyEventCohortRouteState(
	state *RouteState,
	index eventCountryRouteIndex,
	mapping *GlobalCountryMapping,
	event routeStateEvent,
) error {
	previous, existed := state.Routes[event.Key]
	if existed && previous.Visible && previous.OriginKnown {
		if routes, wanted := index[mapping.CountryID(previous.OriginASN)]; wanted {
			delete(routes, event.Key)
		}
	}
	transition, err := state.ApplyWithTransition(event)
	if err != nil {
		return err
	}
	if transition.Current.Visible && transition.Current.OriginKnown {
		if routes, wanted := index[mapping.CountryID(transition.Current.OriginASN)]; wanted {
			routes[event.Key] = struct{}{}
		}
	}
	return nil
}

func compareEventCohortStateSlot(
	state *RouteState,
	parsed parsedRouteStatePartition,
	formal RouteStateSlotRecord,
) error {
	if parsed.Index != formal.Slot || parsed.RouteEventCount != formal.RouteEventCount ||
		parsed.AnnounceCount != formal.AnnounceCount || parsed.WithdrawCount != formal.WithdrawCount ||
		parsed.TransitionSHA256 != formal.TransitionSHA256 ||
		int64(len(state.Routes)) != formal.RouteStateRecordCount ||
		state.VisibleRouteCount != formal.VisibleRouteCount || state.StateDigest.Hex() != formal.StateDigest {
		return fmt.Errorf("event cohort replay diverged from formal RouteState slot %d", formal.Slot)
	}
	return nil
}

func eventCohortKeyLess(left, right RouteStateKey) bool {
	if left.Route.AFI != right.Route.AFI {
		return left.Route.AFI < right.Route.AFI
	}
	leftPrefix, rightPrefix := left.Route.Prefix.String(), right.Route.Prefix.String()
	if leftPrefix != rightPrefix {
		return leftPrefix < rightPrefix
	}
	if left.Route.PeerASN != right.Route.PeerASN {
		return left.Route.PeerASN < right.Route.PeerASN
	}
	return left.Route.PeerIP.Compare(right.Route.PeerIP) < 0
}

func eventCohortObservation(key RouteStateKey, value RouteStateValue) EventCohortRouteObservation {
	pathStatus := "unknown"
	pathID := ""
	if value.ASPathKnown {
		pathStatus = "known"
		pathID = "asp_v1_" + hex.EncodeToString(value.ASPathDigest[:])
	}
	return EventCohortRouteObservation{
		VPID:   VPIdentifier(key.Route.PeerIP, key.Route.PeerASN),
		PeerIP: key.Route.PeerIP.String(), OriginASN: value.OriginASN,
		ASPathStatus: pathStatus, ASPathID: pathID,
		RouteQualityStatus: routeStateQualityName(value.QualityStatus),
		LastRouteEventID:   "rte_v1_" + hex.EncodeToString(value.LastRouteEventID[:]),
		LastUpdatedUTC:     canonicalTimeFromMicros(value.LastUpdatedMicros),
	}
}

func eventCohortMemberID(cohortID string, prefix netip.Prefix, afi uint8) string {
	return stableID("event_cohort_member_v1_", map[string]any{
		"cohort_id": cohortID, "prefix": prefix.String(), "address_family": afi,
	}, 32)
}

func writeEventCohort(
	config EventCohortStoreConfig,
	target eventCohortTarget,
	state *RouteState,
	routes map[RouteStateKey]struct{},
	routeState RouteStateStoreManifest,
	formalSlotSHA string,
	baseCheckpoint RouteStateCheckpointManifest,
) (EventCohortManifest, error) {
	cohortID := eventCohortID(routeState.DatasetID, target.CountryCode, target.StatePoint, state.StateDigest.Hex())
	directory := filepath.Join(config.Output, "cohorts", target.CountryCode, fmt.Sprintf("slot-%04d", target.StateSlot))
	if _, err := os.Lstat(directory); err == nil {
		if !config.Resume {
			return EventCohortManifest{}, fmt.Errorf("event cohort already exists: %s", directory)
		}
		return loadEventCohort(config.Output, directory, cohortID, routeState.DatasetID, target, formalSlotSHA)
	} else if !os.IsNotExist(err) {
		return EventCohortManifest{}, err
	}
	temporary := directory + ".tmp"
	if _, err := os.Lstat(temporary); err == nil {
		return EventCohortManifest{}, fmt.Errorf("unfinished event cohort exists: %s", temporary)
	} else if !os.IsNotExist(err) {
		return EventCohortManifest{}, err
	}
	if err := os.MkdirAll(temporary, 0o750); err != nil {
		return EventCohortManifest{}, err
	}
	writer, err := newDeterministicJSONLGzipWriter(filepath.Join(temporary, "members.jsonl.gz"))
	if err != nil {
		return EventCohortManifest{}, err
	}
	closed := false
	defer func() {
		if !closed {
			writer.Abort()
		}
	}()
	keys := make([]RouteStateKey, 0, len(routes))
	for key := range routes {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool { return eventCohortKeyLess(keys[i], keys[j]) })
	var memberCount, ipv4Count, ipv6Count, relationCount, observationCount int64
	originPopulation := make(map[uint32]struct{})
	for start := 0; start < len(keys); {
		end := start + 1
		for end < len(keys) && keys[end].Route.AFI == keys[start].Route.AFI &&
			keys[end].Route.Prefix == keys[start].Route.Prefix {
			end++
		}
		originSet := make(map[uint32]struct{})
		directions := make([]EventCohortDirection, 0)
		for at := start; at < end; {
			peerASN := keys[at].Route.PeerASN
			direction := EventCohortDirection{PeerASN: peerASN}
			for at < end && keys[at].Route.PeerASN == peerASN {
				value, exists := state.Routes[keys[at]]
				if !exists || !value.Visible || !value.OriginKnown {
					return EventCohortManifest{}, fmt.Errorf("event cohort index contains a non-visible route")
				}
				originSet[value.OriginASN] = struct{}{}
				originPopulation[value.OriginASN] = struct{}{}
				direction.RouteObservations = append(
					direction.RouteObservations, eventCohortObservation(keys[at], value),
				)
				at++
			}
			direction.RouteObservationCount = len(direction.RouteObservations)
			observationCount += int64(direction.RouteObservationCount)
			directions = append(directions, direction)
		}
		origins := sortedUint32(originSet)
		member := EventCohortMember{
			SchemaVersion: EventCohortMemberVersion, CohortID: cohortID,
			CohortMemberID: eventCohortMemberID(cohortID, keys[start].Route.Prefix, keys[start].Route.AFI),
			CountryCode:    target.CountryCode, Prefix: keys[start].Route.Prefix.String(),
			AddressFamily: map[uint8]string{4: "ipv4", 6: "ipv6"}[keys[start].Route.AFI],
			OriginASNs:    origins, ExpectedDirectionCount: len(directions), ExpectedDirections: directions,
		}
		if member.AddressFamily == "" || len(origins) == 0 || len(directions) == 0 {
			return EventCohortManifest{}, fmt.Errorf("invalid event cohort member population")
		}
		if err := writer.Write(member); err != nil {
			return EventCohortManifest{}, err
		}
		memberCount++
		relationCount += int64(len(directions))
		if keys[start].Route.AFI == 4 {
			ipv4Count++
		} else {
			ipv6Count++
		}
		start = end
	}
	relativeMembers := filepath.ToSlash(filepath.Join(
		"cohorts", target.CountryCode, fmt.Sprintf("slot-%04d", target.StateSlot), "members.jsonl.gz",
	))
	members, err := writer.Close(relativeMembers)
	if err != nil {
		return EventCohortManifest{}, err
	}
	closed = true
	manifest := EventCohortManifest{
		SchemaVersion: EventCohortVersion, Status: "complete", CohortID: cohortID,
		CountryCode: target.CountryCode, CohortStatePointUTC: target.StatePoint,
		CohortStateSlot: target.StateSlot, SourceRouteStateDatasetID: routeState.DatasetID,
		SourceRouteStateSlotSHA256: formalSlotSHA, SourceRouteStateDigest: state.StateDigest.Hex(),
		ReplayStartCheckpointID:   baseCheckpoint.CheckpointID,
		ReplayStartCheckpointSlot: baseCheckpoint.ProcessedSlot,
		ReplayedUpdateSlotCount:   target.StateSlot - baseCheckpoint.ProcessedSlot,
		PopulationSemantics:       "one_member_per_unique_prefix_and_address_family_visible_from_country_origin_at_last_complete_state_point_before_detection",
		DirectionSemantics:        "one_expected_direction_per_unique_rrc25_peer_asn_with_one_or_more_route_observation_sessions",
		MemberCount:               memberCount, IPv4MemberCount: ipv4Count, IPv6MemberCount: ipv6Count,
		OriginASNCount: len(originPopulation), ExpectedDirectionRelationCount: relationCount,
		RouteObservationCount: observationCount, Members: members,
	}
	manifest.ContentSHA256 = eventCohortContentSHA(manifest)
	if _, err := writeJSONImmutable(filepath.Join(temporary, "manifest.json"), manifest); err != nil {
		return EventCohortManifest{}, err
	}
	if err := os.Rename(temporary, directory); err != nil {
		return EventCohortManifest{}, err
	}
	return manifest, nil
}

func loadEventCohort(
	root, directory, cohortID, routeStateDatasetID string,
	target eventCohortTarget,
	formalSlotSHA string,
) (EventCohortManifest, error) {
	var manifest EventCohortManifest
	if _, err := readJSON(filepath.Join(directory, "manifest.json"), &manifest); err != nil {
		return manifest, err
	}
	if manifest.SchemaVersion != EventCohortVersion || manifest.Status != "complete" ||
		manifest.CohortID != cohortID || manifest.CountryCode != target.CountryCode ||
		manifest.CohortStatePointUTC != target.StatePoint || manifest.CohortStateSlot != target.StateSlot ||
		manifest.SourceRouteStateDatasetID != routeStateDatasetID ||
		manifest.SourceRouteStateSlotSHA256 != formalSlotSHA ||
		manifest.MemberCount != manifest.Members.RowCount ||
		manifest.MemberCount != manifest.IPv4MemberCount+manifest.IPv6MemberCount ||
		manifest.ContentSHA256 != eventCohortContentSHA(manifest) {
		return manifest, fmt.Errorf("event cohort identity mismatch: %s", directory)
	}
	if err := verifyRouteEventStoreFile(root, manifest.Members); err != nil {
		return manifest, err
	}
	return manifest, nil
}

func prepareEventCohortSources(
	config EventCohortStoreConfig,
) (
	RouteStateStoreManifest,
	[]RouteStateSlotRecord,
	string,
	RouteEventStoreManifest,
	PeerSessionStoreManifest,
	*GlobalCountryMapping,
	EventLifecycleSnapshot,
	string,
	error,
) {
	if config.Output == "" || config.RouteEventRoot == "" || config.RouteStateRoot == "" ||
		config.PeerSessionRoot == "" || config.LifecycleSnapshotPath == "" ||
		config.CompatibleMappingPath == "" || config.RevisedMappingPath == "" {
		return RouteStateStoreManifest{}, nil, "", RouteEventStoreManifest{}, PeerSessionStoreManifest{}, nil, EventLifecycleSnapshot{}, "", fmt.Errorf("event cohort paths are required")
	}
	for label, value := range map[string]string{
		"RouteEvent":   config.RouteEventImplementationID,
		"RouteState":   config.RouteStateImplementationID,
		"peer session": config.PeerSessionImplementationID,
		"event cohort": config.ImplementationID,
	} {
		if err := validateRouteEventImplementationID(value); err != nil {
			return RouteStateStoreManifest{}, nil, "", RouteEventStoreManifest{}, PeerSessionStoreManifest{}, nil, EventLifecycleSnapshot{}, "", fmt.Errorf("%s implementation %w", label, err)
		}
	}
	sourceConfig := RouteMetricStoreConfig{
		RouteEventRoot: config.RouteEventRoot, RouteStateRoot: config.RouteStateRoot,
		CompatibleMappingPath: config.CompatibleMappingPath, RevisedMappingPath: config.RevisedMappingPath,
		RouteEventImplementationID: config.RouteEventImplementationID,
		RouteStateImplementationID: config.RouteStateImplementationID,
		ImplementationID:           config.ImplementationID,
	}
	routeState, _, ledger, routeStateManifestSHA, err := loadRouteMetricSource(sourceConfig, false)
	if err != nil {
		return routeState, nil, "", RouteEventStoreManifest{}, PeerSessionStoreManifest{}, nil, EventLifecycleSnapshot{}, "", err
	}
	routeEvents, _, err := quickRouteEventSource(RouteStateStoreConfig{
		RouteEventRoot:             config.RouteEventRoot,
		RouteEventImplementationID: config.RouteEventImplementationID,
		ImplementationID:           config.RouteStateImplementationID,
	})
	if err != nil {
		return routeState, nil, "", routeEvents, PeerSessionStoreManifest{}, nil, EventLifecycleSnapshot{}, "", err
	}
	peerSessions, err := LoadPeerSessionStore(config.PeerSessionRoot, PeerSessionStoreConfig{
		RawRoot: config.RawRoot, SelectionPath: config.SelectionPath,
		RouteEventRoot:             config.RouteEventRoot,
		RouteEventImplementationID: config.RouteEventImplementationID,
		Output:                     config.PeerSessionRoot, ImplementationID: config.PeerSessionImplementationID,
		Resume: true,
	})
	if err != nil {
		return routeState, nil, "", routeEvents, peerSessions, nil, EventLifecycleSnapshot{}, "", err
	}
	if peerSessions.PrefixWithdrawalInference != "not_permitted" {
		return routeState, nil, "", routeEvents, peerSessions, nil, EventLifecycleSnapshot{}, "", fmt.Errorf("peer session source permits prefix withdrawal inference")
	}
	mapping, err := LoadGlobalCountryMapping(config.CompatibleMappingPath, config.RevisedMappingPath)
	if err != nil {
		return routeState, nil, "", routeEvents, peerSessions, nil, EventLifecycleSnapshot{}, "", err
	}
	if mapping.MappingVersion != routeState.MappingVersion ||
		mapping.CompatibleSHA256 != routeState.MappingCompatibleSHA256 ||
		mapping.RevisedSHA256 != routeState.MappingRevisedSHA256 {
		return routeState, nil, "", routeEvents, peerSessions, mapping, EventLifecycleSnapshot{}, "", fmt.Errorf("event cohort mapping identity mismatch")
	}
	lifecycle, lifecycleFileSHA, err := loadEventLifecycleSnapshot(config.LifecycleSnapshotPath)
	return routeState, ledger, routeStateManifestSHA, routeEvents, peerSessions, mapping, lifecycle, lifecycleFileSHA, err
}

func RunEventCohortStore(config EventCohortStoreConfig) (EventCohortStoreManifest, error) {
	routeState, ledger, routeStateManifestSHA, routeEvents, peerSessions, mapping, lifecycle, lifecycleFileSHA, err := prepareEventCohortSources(config)
	if err != nil {
		return EventCohortStoreManifest{}, err
	}
	runID, datasetID := eventCohortIdentity(routeState, peerSessions, lifecycle, config.ImplementationID)
	targets, _, err := buildEventCohortTargets(lifecycle, mapping, datasetID)
	if err != nil {
		return EventCohortStoreManifest{}, err
	}
	marker := eventCohortStoreMarker{
		SchemaVersion: EventCohortStoreVersion, RunID: runID, DatasetID: datasetID,
		ImplementationID:               config.ImplementationID,
		SourceRouteStateDatasetID:      routeState.DatasetID,
		SourceRouteStateContentSHA256:  routeState.ContentSHA256,
		SourcePeerSessionDatasetID:     peerSessions.DatasetID,
		SourcePeerSessionContentSHA256: peerSessions.ContentSHA256,
		LifecycleSnapshotID:            lifecycle.SnapshotID,
		LifecycleSnapshotContentSHA256: lifecycle.ContentSHA256,
	}
	if err := os.MkdirAll(config.Output, 0o750); err != nil {
		return EventCohortStoreManifest{}, err
	}
	if _, err := writeJSONImmutable(filepath.Join(config.Output, "RUNNING.json"), marker); err != nil {
		return EventCohortStoreManifest{}, err
	}
	if raw, err := os.ReadFile(config.LifecycleSnapshotPath); err != nil {
		return EventCohortStoreManifest{}, err
	} else if err := writeBytesImmutable(filepath.Join(config.Output, "event-lifecycle-snapshot.json"), raw); err != nil {
		return EventCohortStoreManifest{}, err
	}

	cohortManifests := make([]EventCohortManifest, 0, len(targets))
	cohortByKey := make(map[string]EventCohortManifest, len(targets))
	for _, baseSlot := range []int{0, RouteStateMidpointSlot} {
		groupTargets := make([]eventCohortTarget, 0)
		wanted := make(map[uint16]struct{})
		for _, target := range targets {
			belongs := (baseSlot == 0 && target.StateSlot < RouteStateMidpointSlot) ||
				(baseSlot == RouteStateMidpointSlot && target.StateSlot >= RouteStateMidpointSlot)
			if belongs {
				groupTargets = append(groupTargets, target)
				wanted[target.CountryID] = struct{}{}
			}
		}
		if len(groupTargets) == 0 {
			continue
		}
		checkpointDirectory := filepath.Join(config.RouteStateRoot, "checkpoints", fmt.Sprintf("slot-%04d", baseSlot))
		config.progress(fmt.Sprintf("加载 RouteState checkpoint slot=%d", baseSlot))
		state, checkpoint, err := LoadRouteStateCheckpoint(checkpointDirectory, routeStateIdentityFromManifest(routeState))
		if err != nil {
			return EventCohortStoreManifest{}, err
		}
		index := newEventCountryRouteIndex(state, mapping, wanted)
		targetAt := 0
		materialize := func(target eventCohortTarget) error {
			formalSlotSHA := checkpoint.ContentSHA256
			if target.StateSlot > 0 {
				formalSlotSHA = ledger[target.StateSlot-1].ContentSHA256
			}
			manifest, err := writeEventCohort(
				config, target, state, index[target.CountryID], routeState, formalSlotSHA, checkpoint,
			)
			if err != nil {
				return err
			}
			key := fmt.Sprintf("%s|%04d", target.CountryCode, target.StateSlot)
			cohortByKey[key] = manifest
			cohortManifests = append(cohortManifests, manifest)
			config.progress(fmt.Sprintf("event cohort %s slot=%04d members=%d", target.CountryCode, target.StateSlot, manifest.MemberCount))
			return nil
		}
		for targetAt < len(groupTargets) && groupTargets[targetAt].StateSlot == baseSlot {
			if err := materialize(groupTargets[targetAt]); err != nil {
				return EventCohortStoreManifest{}, err
			}
			targetAt++
		}
		if targetAt < len(groupTargets) {
			maximumSlot := groupTargets[len(groupTargets)-1].StateSlot
			for slot := baseSlot + 1; slot <= maximumSlot; slot++ {
				parsed, err := parseRouteStatePartitionWithConsumer(
					config.RouteEventRoot, routeEvents.Partitions[slot], false,
					func(event routeStateEvent) error {
						return applyEventCohortRouteState(state, index, mapping, event)
					},
				)
				if err != nil {
					return EventCohortStoreManifest{}, err
				}
				if err := compareEventCohortStateSlot(state, parsed, ledger[slot-1]); err != nil {
					return EventCohortStoreManifest{}, err
				}
				for targetAt < len(groupTargets) && groupTargets[targetAt].StateSlot == slot {
					if err := materialize(groupTargets[targetAt]); err != nil {
						return EventCohortStoreManifest{}, err
					}
					targetAt++
				}
				if slot%96 == 0 {
					config.progress(fmt.Sprintf("event cohort replay slot=%04d/%04d", slot, maximumSlot))
				}
			}
		}
		state = nil
		index = nil
		runtime.GC()
	}
	if len(cohortManifests) != len(targets) {
		return EventCohortStoreManifest{}, fmt.Errorf("event cohort target population mismatch")
	}
	sort.Slice(cohortManifests, func(i, j int) bool {
		if cohortManifests[i].CohortStateSlot != cohortManifests[j].CohortStateSlot {
			return cohortManifests[i].CohortStateSlot < cohortManifests[j].CohortStateSlot
		}
		return cohortManifests[i].CountryCode < cohortManifests[j].CountryCode
	})
	eventWriter, err := newDeterministicJSONLGzipWriter(filepath.Join(config.Output, "events.jsonl.gz"))
	if err != nil {
		return EventCohortStoreManifest{}, err
	}
	eventClosed := false
	defer func() {
		if !eventClosed {
			eventWriter.Abort()
		}
	}()
	for _, event := range lifecycle.Events {
		slot, _ := eventCohortSlot(event.CohortStatePointUTC)
		cohort, exists := cohortByKey[fmt.Sprintf("%s|%04d", event.CountryCode, slot)]
		if !exists {
			return EventCohortStoreManifest{}, fmt.Errorf("event cohort binding missing: %s", event.LegacyReference)
		}
		binding := EventCohortBinding{
			IncidentID: event.IncidentID, LegacyReference: event.LegacyReference,
			CountryCode: event.CountryCode, DetectedAtUTC: event.DetectedAtUTC,
			CohortID: cohort.CohortID, CohortStatePointUTC: event.CohortStatePointUTC,
			WindowStartUTC: event.WindowStartUTC, RequestedWindowStartUTC: event.RequestedWindowStartUTC,
			LeftBoundaryMissingSlotCount: event.LeftBoundaryMissingSlotCount,
			EventEndAtUTC:                event.EventEndAtUTC, EventDurationSeconds: event.EventDurationSeconds,
			ProjectionEndStatePointUTC: event.ProjectionEndStatePointUTC,
			LifecycleState:             event.LifecycleState, IsFinalInDataRange: event.IsFinalInDataRange,
		}
		if err := eventWriter.Write(binding); err != nil {
			return EventCohortStoreManifest{}, err
		}
	}
	eventsMeta, err := eventWriter.Close("events.jsonl.gz")
	if err != nil {
		return EventCohortStoreManifest{}, err
	}
	eventClosed = true
	manifest := EventCohortStoreManifest{
		SchemaVersion: EventCohortStoreVersion, Status: "complete", RunID: runID, DatasetID: datasetID,
		CollectorID: "rrc25", WindowStartUTC: RouteEventWindowStartUTC,
		WindowEndExclusiveUTC: RouteEventWindowEndUTC,
		ImplementationID:      config.ImplementationID, ProjectorName: EventCohortProjectorName,
		ProjectorVersion:               EventCohortProjectorVersion,
		SourceRouteEventDatasetID:      routeEvents.DatasetID,
		SourceRouteEventContentSHA256:  routeEvents.ContentSHA256,
		SourceRouteStateDatasetID:      routeState.DatasetID,
		SourceRouteStateContentSHA256:  routeState.ContentSHA256,
		SourceRouteStateManifestSHA256: routeStateManifestSHA,
		SourcePeerSessionDatasetID:     peerSessions.DatasetID,
		SourcePeerSessionContentSHA256: peerSessions.ContentSHA256,
		LifecycleSnapshotID:            lifecycle.SnapshotID,
		LifecycleSnapshotContentSHA256: lifecycle.ContentSHA256,
		LifecycleSnapshotFileSHA256:    lifecycleFileSHA,
		MappingVersion:                 mapping.MappingVersion, MappingCompatibleSHA256: mapping.CompatibleSHA256,
		MappingRevisedSHA256: mapping.RevisedSHA256,
		RouteStateAuthority:  "the_existing_route_state_dataset_is_the_only_route_state_fact",
		DirectionDefinition:  "one_independent_direction_is_one_rrc25_peer_asn_and_multiple_bgp_sessions_do_not_expand_the_denominator",
		NewPopulationPolicy:  "new_prefixes_or_directions_after_detection_do_not_change_the_frozen_cohort_denominator",
		SessionRouteBoundary: "peer_session_down_never_materializes_or_implies_a_route_withdrawal",
		EventCount:           len(lifecycle.Events), UniqueCohortCount: len(cohortManifests), Events: eventsMeta,
		Cohorts: cohortManifests,
	}
	for _, cohort := range cohortManifests {
		manifest.CohortMemberCount += cohort.MemberCount
		manifest.ExpectedDirectionRelationCount += cohort.ExpectedDirectionRelationCount
		manifest.RouteObservationCount += cohort.RouteObservationCount
	}
	manifest.ContentSHA256 = eventCohortStoreContentSHA(manifest)
	if _, err := writeJSONImmutable(filepath.Join(config.Output, "manifest.json"), manifest); err != nil {
		return EventCohortStoreManifest{}, err
	}
	if _, err := writeJSONImmutable(filepath.Join(config.Output, "COMPLETE.json"), manifest); err != nil {
		return EventCohortStoreManifest{}, err
	}
	return LoadEventCohortStore(config.Output, config)
}

func LoadEventCohortStore(root string, config EventCohortStoreConfig) (EventCohortStoreManifest, error) {
	var manifest EventCohortStoreManifest
	routeState, _, routeStateManifestSHA, routeEvents, peerSessions, mapping, lifecycle, lifecycleFileSHA, err := prepareEventCohortSources(config)
	if err != nil {
		return manifest, err
	}
	runID, datasetID := eventCohortIdentity(routeState, peerSessions, lifecycle, config.ImplementationID)
	manifestRaw, err := readJSON(filepath.Join(root, "manifest.json"), &manifest)
	if err != nil {
		return manifest, err
	}
	var complete EventCohortStoreManifest
	completeRaw, err := readJSON(filepath.Join(root, "COMPLETE.json"), &complete)
	if err != nil {
		return manifest, err
	}
	if !bytes.Equal(manifestRaw, completeRaw) || manifest.SchemaVersion != EventCohortStoreVersion ||
		manifest.Status != "complete" || manifest.RunID != runID || manifest.DatasetID != datasetID ||
		manifest.CollectorID != "rrc25" || manifest.WindowStartUTC != RouteEventWindowStartUTC ||
		manifest.WindowEndExclusiveUTC != RouteEventWindowEndUTC ||
		manifest.ImplementationID != config.ImplementationID ||
		manifest.SourceRouteEventDatasetID != routeEvents.DatasetID ||
		manifest.SourceRouteEventContentSHA256 != routeEvents.ContentSHA256 ||
		manifest.SourceRouteStateDatasetID != routeState.DatasetID ||
		manifest.SourceRouteStateContentSHA256 != routeState.ContentSHA256 ||
		manifest.SourceRouteStateManifestSHA256 != routeStateManifestSHA ||
		manifest.SourcePeerSessionDatasetID != peerSessions.DatasetID ||
		manifest.SourcePeerSessionContentSHA256 != peerSessions.ContentSHA256 ||
		manifest.LifecycleSnapshotID != lifecycle.SnapshotID ||
		manifest.LifecycleSnapshotContentSHA256 != lifecycle.ContentSHA256 ||
		manifest.LifecycleSnapshotFileSHA256 != lifecycleFileSHA ||
		manifest.MappingVersion != mapping.MappingVersion || manifest.EventCount != len(lifecycle.Events) ||
		manifest.Events.RowCount != int64(len(lifecycle.Events)) ||
		manifest.UniqueCohortCount != len(manifest.Cohorts) ||
		manifest.ContentSHA256 != eventCohortStoreContentSHA(manifest) {
		return manifest, fmt.Errorf("complete event cohort store identity mismatch")
	}
	if err := verifyRouteEventStoreFile(root, manifest.Events); err != nil {
		return manifest, err
	}
	var members, relations, observations int64
	for _, cohort := range manifest.Cohorts {
		target := eventCohortTarget{
			CountryCode: cohort.CountryCode, StatePoint: cohort.CohortStatePointUTC,
			StateSlot: cohort.CohortStateSlot,
		}
		directory := filepath.Join(root, "cohorts", cohort.CountryCode, fmt.Sprintf("slot-%04d", cohort.CohortStateSlot))
		loaded, err := loadEventCohort(root, directory, cohort.CohortID, routeState.DatasetID, target, cohort.SourceRouteStateSlotSHA256)
		if err != nil || loaded != cohort {
			if err != nil {
				return manifest, err
			}
			return manifest, fmt.Errorf("event cohort manifest list mismatch")
		}
		members += cohort.MemberCount
		relations += cohort.ExpectedDirectionRelationCount
		observations += cohort.RouteObservationCount
	}
	if members != manifest.CohortMemberCount || relations != manifest.ExpectedDirectionRelationCount ||
		observations != manifest.RouteObservationCount {
		return manifest, fmt.Errorf("event cohort aggregate population mismatch")
	}
	return manifest, nil
}
