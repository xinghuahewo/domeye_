package replay

import (
	"bufio"
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/csv"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/netip"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	EventASPathStoreVersion     = "rrc25-event-as-path-store/v1"
	EventASPathEventVersion     = "rrc25-event-as-path/v1"
	EventAffectedASVersion      = "rrc25-event-affected-as/v1"
	EventPathDownstreamVersion  = "rrc25-event-path-downstream/v1"
	EventPathEvidenceVersion    = "rrc25-event-path-evidence/v1"
	EventASPathProjectorName    = "domeye_country_outage_event_as_path_projector"
	EventASPathProjectorVersion = "1.0.0"
)

type EventASPathStoreConfig struct {
	RouteEventRoot              string
	EventCohortRoot             string
	EventMetricRoot             string
	ASSnapshotPath              string
	Output                      string
	RouteEventImplementationID  string
	EventCohortImplementationID string
	EventMetricImplementationID string
	ImplementationID            string
	Workers                     int
	Resume                      bool
	Progress                    func(string)
}

func (config EventASPathStoreConfig) progress(message string) {
	if config.Progress != nil {
		config.Progress(message)
	}
}

type EventASStaticProfile struct {
	ASN          uint32  `json:"asn"`
	ASName       *string `json:"as_name"`
	Organization *string `json:"organization"`
	Nature       *string `json:"nature"`
	NameState    string  `json:"name_state"`
	OrgState     string  `json:"organization_state"`
	NatureState  string  `json:"nature_state"`
}

type EventAffectedASRow struct {
	SchemaVersion                 string  `json:"schema_version"`
	EventASPathID                 string  `json:"event_as_path_id"`
	EventMetricID                 string  `json:"event_metric_id"`
	CohortID                      string  `json:"cohort_id"`
	ASN                           uint32  `json:"asn"`
	ASName                        *string `json:"as_name"`
	Organization                  *string `json:"organization"`
	Nature                        *string `json:"nature"`
	NameState                     string  `json:"name_state"`
	OrganizationState             string  `json:"organization_state"`
	NatureState                   string  `json:"nature_state"`
	EventClassification           string  `json:"event_classification"`
	FixedPrefixCount              int     `json:"fixed_prefix_count"`
	PeakPartialPrefixCount        int     `json:"peak_partial_prefix_count"`
	PeakCompletePrefixCount       int     `json:"peak_complete_prefix_count"`
	PeakInvisibleDirectionCount   int     `json:"peak_invisible_direction_count"`
	PathDownstreamASNCount        int     `json:"path_downstream_asn_count"`
	ConcurrentDownstreamASNCount  int     `json:"concurrent_downstream_asn_count"`
	StaticAttributeSnapshotSHA256 string  `json:"static_attribute_snapshot_sha256"`
}

type EventPathDownstreamRow struct {
	SchemaVersion                        string  `json:"schema_version"`
	EventASPathID                        string  `json:"event_as_path_id"`
	EventMetricID                        string  `json:"event_metric_id"`
	CohortID                             string  `json:"cohort_id"`
	AffectedASN                          uint32  `json:"affected_asn"`
	DownstreamASN                        uint32  `json:"downstream_asn"`
	DownstreamASName                     *string `json:"downstream_as_name"`
	DownstreamOrganization               *string `json:"downstream_organization"`
	DownstreamNature                     *string `json:"downstream_nature"`
	DownstreamNameState                  string  `json:"downstream_name_state"`
	DownstreamOrganizationState          string  `json:"downstream_organization_state"`
	DownstreamNatureState                string  `json:"downstream_nature_state"`
	ObservedPathCount                    int     `json:"observed_path_count"`
	AssociatedFixedPrefixCount           int     `json:"associated_fixed_prefix_count"`
	IndependentDirectionCount            int     `json:"independent_direction_count"`
	RouteObservationCount                int     `json:"route_observation_count"`
	ConcurrentStatePointCount            int     `json:"concurrent_state_point_count"`
	FirstConcurrentStatePointUTC         *string `json:"first_concurrent_state_point_utc"`
	LastConcurrentStatePointUTC          *string `json:"last_concurrent_state_point_utc"`
	PeakConcurrentInterruptedPrefixCount int     `json:"peak_concurrent_interrupted_prefix_count"`
	PeakConcurrentIPv4AddressCount       uint64  `json:"peak_concurrent_ipv4_address_count"`
	PeakConcurrentIPv6Slash48Count       uint64  `json:"peak_concurrent_ipv6_slash48_count"`
	PathRelationshipSemantics            string  `json:"path_relationship_semantics"`
}

type EventPathEvidenceRow struct {
	SchemaVersion         string   `json:"schema_version"`
	EventASPathID         string   `json:"event_as_path_id"`
	CohortID              string   `json:"cohort_id"`
	AffectedASN           uint32   `json:"affected_asn"`
	DownstreamASN         uint32   `json:"downstream_asn"`
	CohortMemberID        string   `json:"cohort_member_id"`
	Prefix                string   `json:"prefix"`
	AddressFamily         string   `json:"address_family"`
	ASPathID              string   `json:"as_path_id"`
	ASPathCanonical       string   `json:"as_path_canonical"`
	IndependentPeerASNs   []uint32 `json:"independent_peer_asns"`
	RouteObservationCount int      `json:"route_observation_count"`
	RelationshipState     string   `json:"relationship_state"`
}

type EventASPathEventManifest struct {
	SchemaVersion                       string              `json:"schema_version"`
	Status                              string              `json:"status"`
	Directory                           string              `json:"directory"`
	EventASPathID                       string              `json:"event_as_path_id"`
	EventMetricID                       string              `json:"event_metric_id"`
	LegacyReference                     string              `json:"legacy_reference"`
	CountryCode                         string              `json:"country_code"`
	CohortID                            string              `json:"cohort_id"`
	WindowStartUTC                      string              `json:"window_start_utc"`
	ProjectionEndStatePointUTC          string              `json:"projection_end_state_point_utc"`
	AffectedASCount                     int                 `json:"affected_as_count"`
	RouteInterruptedASCount             int                 `json:"route_interrupted_as_count"`
	AffectedOnlyASCount                 int                 `json:"affected_only_as_count"`
	UnknownStaticNameCount              int                 `json:"unknown_static_name_count"`
	UnknownStaticOrganizationCount      int                 `json:"unknown_static_organization_count"`
	UnknownStaticNatureCount            int                 `json:"unknown_static_nature_count"`
	PathDownstreamRelationCount         int                 `json:"path_downstream_relation_count"`
	PathDownstreamASNCount              int                 `json:"path_downstream_asn_count"`
	ConcurrentDownstreamRelationCount   int                 `json:"concurrent_downstream_relation_count"`
	PathEvidenceCount                   int64               `json:"path_evidence_count"`
	KnownASPathObservationCount         int64               `json:"known_as_path_observation_count"`
	UnknownASPathObservationCount       int64               `json:"unknown_as_path_observation_count"`
	OrderedRelationshipObservationCount int64               `json:"ordered_relationship_observation_count"`
	AmbiguousPathObservationCount       int64               `json:"ambiguous_path_observation_count"`
	AffectedAS                          RouteEventStoreFile `json:"affected_as"`
	Downstreams                         RouteEventStoreFile `json:"path_downstreams"`
	PathEvidence                        RouteEventStoreFile `json:"path_evidence"`
	ContentSHA256                       string              `json:"content_sha256"`
}

type EventASPathStoreManifest struct {
	SchemaVersion                     string                     `json:"schema_version"`
	Status                            string                     `json:"status"`
	RunID                             string                     `json:"run_id"`
	DatasetID                         string                     `json:"dataset_id"`
	CollectorID                       string                     `json:"collector_id"`
	WindowStartUTC                    string                     `json:"window_start_utc"`
	WindowEndExclusiveUTC             string                     `json:"window_end_exclusive_utc"`
	ImplementationID                  string                     `json:"implementation_id"`
	ProjectorName                     string                     `json:"projector_name"`
	ProjectorVersion                  string                     `json:"projector_version"`
	SourceRouteEventDatasetID         string                     `json:"source_route_event_dataset_id"`
	SourceRouteEventContentSHA256     string                     `json:"source_route_event_content_sha256"`
	SourceRouteEventManifestSHA256    string                     `json:"source_route_event_manifest_sha256"`
	SourceEventCohortDatasetID        string                     `json:"source_event_cohort_dataset_id"`
	SourceEventCohortContentSHA256    string                     `json:"source_event_cohort_content_sha256"`
	SourceEventCohortManifestSHA256   string                     `json:"source_event_cohort_manifest_sha256"`
	SourceEventMetricDatasetID        string                     `json:"source_event_metric_dataset_id"`
	SourceEventMetricContentSHA256    string                     `json:"source_event_metric_content_sha256"`
	SourceEventMetricManifestSHA256   string                     `json:"source_event_metric_manifest_sha256"`
	ASAttributeSnapshotName           string                     `json:"as_attribute_snapshot_name"`
	ASAttributeSnapshotSHA256         string                     `json:"as_attribute_snapshot_sha256"`
	ASAttributeSnapshotSizeBytes      int64                      `json:"as_attribute_snapshot_size_bytes"`
	ASAttributeDuplicatePolicy        string                     `json:"as_attribute_duplicate_policy"`
	ASAttributeFieldSemantics         string                     `json:"as_attribute_field_semantics"`
	PathRelationshipSemantics         string                     `json:"path_relationship_semantics"`
	UnorderedPathPolicy               string                     `json:"unordered_path_policy"`
	CausalBoundary                    string                     `json:"causal_boundary"`
	EventCount                        int                        `json:"event_count"`
	AffectedASCount                   int64                      `json:"affected_as_count"`
	PathDownstreamRelationCount       int64                      `json:"path_downstream_relation_count"`
	ConcurrentDownstreamRelationCount int64                      `json:"concurrent_downstream_relation_count"`
	PathEvidenceCount                 int64                      `json:"path_evidence_count"`
	KnownASPathObservationCount       int64                      `json:"known_as_path_observation_count"`
	UnknownASPathObservationCount     int64                      `json:"unknown_as_path_observation_count"`
	ResolvedASPathCount               int                        `json:"resolved_as_path_count"`
	ScannedASPathPartitionCount       int                        `json:"scanned_as_path_partition_count"`
	ScannedASPathRowCount             int64                      `json:"scanned_as_path_row_count"`
	Events                            []EventASPathEventManifest `json:"events"`
	ContentSHA256                     string                     `json:"content_sha256"`
}

type eventASPathSources struct {
	RouteEvents       RouteEventStoreManifest
	RouteManifestSHA  string
	Cohorts           EventCohortStoreManifest
	CohortManifestSHA string
	Metrics           EventMetricStoreManifest
	MetricManifestSHA string
	ASSnapshotSHA     string
	ASSnapshotSize    int64
}

func eventASPathStoreContentSHA(value EventASPathStoreManifest) string {
	value.ContentSHA256 = ""
	raw, _ := json.Marshal(value)
	digest := sha256.Sum256(raw)
	return hex.EncodeToString(digest[:])
}

func eventASPathEventContentSHA(value EventASPathEventManifest) string {
	value.ContentSHA256 = ""
	raw, _ := json.Marshal(value)
	digest := sha256.Sum256(raw)
	return hex.EncodeToString(digest[:])
}

func eventASPathIdentity(metrics EventMetricStoreManifest, snapshotSHA, implementationID string) (string, string) {
	runID := stableID("event_as_path_run_v1_", map[string]any{
		"schema_version":                     EventASPathStoreVersion,
		"source_event_metric_dataset_id":     metrics.DatasetID,
		"source_event_metric_content_sha256": metrics.ContentSHA256,
		"as_attribute_snapshot_sha256":       snapshotSHA,
		"implementation_id":                  implementationID,
		"projector_name":                     EventASPathProjectorName,
		"projector_version":                  EventASPathProjectorVersion,
	}, 32)
	datasetID := stableID("event_as_path_dataset_v1_", map[string]any{
		"run_id": runID, "collector_id": "rrc25",
	}, 32)
	return runID, datasetID
}

func eventASPathID(datasetID string, metric EventMetricEventManifest) string {
	return stableID("country_event_as_path_v1_", map[string]any{
		"dataset_id":       datasetID,
		"event_metric_id":  metric.EventMetricID,
		"cohort_id":        metric.CohortID,
		"legacy_reference": metric.LegacyReference,
	}, 32)
}

func loadEventASPathSources(config EventASPathStoreConfig) (eventASPathSources, error) {
	var sources eventASPathSources
	if config.RouteEventRoot == "" || config.EventCohortRoot == "" || config.EventMetricRoot == "" ||
		config.ASSnapshotPath == "" || config.ImplementationID == "" ||
		config.RouteEventImplementationID == "" || config.EventCohortImplementationID == "" ||
		config.EventMetricImplementationID == "" {
		return sources, fmt.Errorf("event AS path inputs and implementation identities are required")
	}
	for label, value := range map[string]string{
		"RouteEvent":    config.RouteEventImplementationID,
		"event cohort":  config.EventCohortImplementationID,
		"event metric":  config.EventMetricImplementationID,
		"event AS path": config.ImplementationID,
	} {
		if err := validateRouteEventImplementationID(value); err != nil {
			return sources, fmt.Errorf("%s implementation %w", label, err)
		}
	}
	loadTwin := func(root string, left, right any) ([]byte, error) {
		manifestRaw, err := readJSON(filepath.Join(root, "manifest.json"), left)
		if err != nil {
			return nil, err
		}
		completeRaw, err := readJSON(filepath.Join(root, "COMPLETE.json"), right)
		if err != nil {
			return nil, err
		}
		if !bytes.Equal(manifestRaw, completeRaw) {
			return nil, fmt.Errorf("immutable manifest and COMPLETE differ under %s", root)
		}
		return manifestRaw, nil
	}
	var routeComplete RouteEventStoreManifest
	routeRaw, err := loadTwin(config.RouteEventRoot, &sources.RouteEvents, &routeComplete)
	if err != nil {
		return sources, err
	}
	if sources.RouteEvents.SchemaVersion != RouteEventStoreVersion || sources.RouteEvents.Status != "complete" ||
		sources.RouteEvents.CollectorID != "rrc25" || sources.RouteEvents.ImplementationID != config.RouteEventImplementationID ||
		sources.RouteEvents.ContentSHA256 != routeEventStoreContentSHA(sources.RouteEvents) {
		return sources, fmt.Errorf("RouteEvent source identity mismatch")
	}
	digest := sha256.Sum256(routeRaw)
	sources.RouteManifestSHA = hex.EncodeToString(digest[:])
	var cohortComplete EventCohortStoreManifest
	cohortRaw, err := loadTwin(config.EventCohortRoot, &sources.Cohorts, &cohortComplete)
	if err != nil {
		return sources, err
	}
	if sources.Cohorts.SchemaVersion != EventCohortStoreVersion || sources.Cohorts.Status != "complete" ||
		sources.Cohorts.CollectorID != "rrc25" || sources.Cohorts.ImplementationID != config.EventCohortImplementationID ||
		sources.Cohorts.SourceRouteEventDatasetID != sources.RouteEvents.DatasetID ||
		sources.Cohorts.SourceRouteEventContentSHA256 != sources.RouteEvents.ContentSHA256 ||
		sources.Cohorts.ContentSHA256 != eventCohortStoreContentSHA(sources.Cohorts) {
		return sources, fmt.Errorf("event cohort source identity mismatch")
	}
	digest = sha256.Sum256(cohortRaw)
	sources.CohortManifestSHA = hex.EncodeToString(digest[:])
	var metricComplete EventMetricStoreManifest
	metricRaw, err := loadTwin(config.EventMetricRoot, &sources.Metrics, &metricComplete)
	if err != nil {
		return sources, err
	}
	if sources.Metrics.SchemaVersion != EventMetricStoreVersion || sources.Metrics.Status != "complete" ||
		sources.Metrics.CollectorID != "rrc25" || sources.Metrics.ImplementationID != config.EventMetricImplementationID ||
		sources.Metrics.SourceRouteEventDatasetID != sources.RouteEvents.DatasetID ||
		sources.Metrics.SourceRouteEventContentSHA256 != sources.RouteEvents.ContentSHA256 ||
		sources.Metrics.SourceRouteEventManifestSHA256 != sources.RouteManifestSHA ||
		sources.Metrics.SourceEventCohortDatasetID != sources.Cohorts.DatasetID ||
		sources.Metrics.SourceEventCohortContentSHA256 != sources.Cohorts.ContentSHA256 ||
		sources.Metrics.SourceEventCohortManifestSHA256 != sources.CohortManifestSHA ||
		sources.Metrics.EventCount != sources.Cohorts.EventCount ||
		sources.Metrics.ContentSHA256 != eventMetricStoreContentSHA(sources.Metrics) {
		return sources, fmt.Errorf("event metric source identity mismatch")
	}
	digest = sha256.Sum256(metricRaw)
	sources.MetricManifestSHA = hex.EncodeToString(digest[:])
	sources.ASSnapshotSHA, sources.ASSnapshotSize, err = sha256File(config.ASSnapshotPath)
	if err != nil {
		return sources, err
	}
	return sources, nil
}

func scanVerifiedJSONLGzip(root string, meta RouteEventStoreFile, maximum int, consume func([]byte) error) (int64, error) {
	if err := verifyRouteEventStoreFile(root, meta); err != nil {
		return 0, err
	}
	file, err := os.Open(filepath.Join(root, filepath.FromSlash(meta.Path)))
	if err != nil {
		return 0, err
	}
	defer file.Close()
	decoded, err := gzip.NewReader(file)
	if err != nil {
		return 0, err
	}
	defer decoded.Close()
	scanner := bufio.NewScanner(decoded)
	scanner.Buffer(make([]byte, 64<<10), maximum)
	var rows int64
	for scanner.Scan() {
		rows++
		if err := consume(scanner.Bytes()); err != nil {
			return rows, err
		}
	}
	if err := scanner.Err(); err != nil {
		return rows, err
	}
	if rows != meta.RowCount {
		return rows, fmt.Errorf("verified JSONL row population mismatch for %s", meta.Path)
	}
	return rows, nil
}

type eventASNWindowSummary struct {
	ASN                     uint32
	FixedPrefixCount        int
	EverAffected            bool
	EverRouteInterrupted    bool
	PeakPartial             int
	PeakComplete            int
	PeakInvisibleDirections int
}

func readEventASNSummaries(root string, event EventMetricEventManifest) (map[uint32]*eventASNWindowSummary, error) {
	result := make(map[uint32]*eventASNWindowSummary)
	_, err := scanVerifiedJSONLGzip(root, event.ASNStates, 4<<20, func(raw []byte) error {
		var row EventMetricASNStateRow
		if err := json.Unmarshal(raw, &row); err != nil {
			return err
		}
		if row.EventMetricID != event.EventMetricID || row.CohortID != event.CohortID || row.FixedPrefixCount < 1 {
			return fmt.Errorf("event ASN state identity or population mismatch")
		}
		state := result[row.ASN]
		if state == nil {
			state = &eventASNWindowSummary{ASN: row.ASN, FixedPrefixCount: row.FixedPrefixCount}
			result[row.ASN] = state
		}
		if state.FixedPrefixCount != row.FixedPrefixCount {
			return fmt.Errorf("event ASN fixed prefix population changed")
		}
		if row.PartialPrefixCount > state.PeakPartial {
			state.PeakPartial = row.PartialPrefixCount
		}
		if row.CompletePrefixCount > state.PeakComplete {
			state.PeakComplete = row.CompletePrefixCount
		}
		if row.Classification == eventASNAffected {
			state.EverAffected = true
		}
		if row.Classification == eventASNRouteInterrupted {
			state.EverAffected, state.EverRouteInterrupted = true, true
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	if len(result) != event.FixedASNCount {
		return nil, fmt.Errorf("event ASN summary population mismatch")
	}
	return result, nil
}

type pathRequestSet map[int]map[string]struct{}

func eventPathPartitionCandidates(value string, count int) ([]int, error) {
	parsed, err := time.Parse(time.RFC3339, value)
	if err != nil || parsed.Format(time.RFC3339) != value {
		return nil, fmt.Errorf("invalid route observation update time %q", value)
	}
	start, _ := time.Parse(time.RFC3339, RouteEventWindowStartUTC)
	delta := parsed.Sub(start)
	if delta < 0 || delta >= time.Duration(RouteStateFinalSlot)*5*time.Minute {
		return nil, fmt.Errorf("route observation time outside 224-310")
	}
	if delta == 0 {
		candidates := []int{0}
		if count > 1 {
			candidates = append(candidates, 1)
		}
		return candidates, nil
	}
	index := int(delta/(5*time.Minute)) + 1
	if index < 1 || index >= count {
		return nil, fmt.Errorf("route observation partition outside manifest")
	}
	return []int{index}, nil
}

func collectEventPathRequests(config EventASPathStoreConfig, sources eventASPathSources) (pathRequestSet, map[string]struct{}, int64, int64, error) {
	requests := make(pathRequestSet)
	all := make(map[string]struct{})
	var known, unknown int64
	for index, cohort := range sources.Cohorts.Cohorts {
		if index%8 == 0 {
			config.progress(fmt.Sprintf("event AS path request scan event=%d/%d", index, len(sources.Cohorts.Cohorts)))
		}
		members, err := readEventMetricMembers(config.EventCohortRoot, cohort)
		if err != nil {
			return nil, nil, 0, 0, err
		}
		for _, member := range members {
			for _, direction := range member.ExpectedDirections {
				for _, observation := range direction.RouteObservations {
					if observation.ASPathStatus != "known" || observation.ASPathID == "" {
						unknown++
						continue
					}
					known++
					all[observation.ASPathID] = struct{}{}
					candidates, err := eventPathPartitionCandidates(observation.LastUpdatedUTC, len(sources.RouteEvents.Partitions))
					if err != nil {
						return nil, nil, 0, 0, err
					}
					for _, partition := range candidates {
						if requests[partition] == nil {
							requests[partition] = make(map[string]struct{})
						}
						requests[partition][observation.ASPathID] = struct{}{}
					}
				}
			}
		}
	}
	return requests, all, known, unknown, nil
}

type resolvedPathSet struct {
	Paths           map[string]ASPathSnapshot
	PartitionCount  int
	ScannedPathRows int64
}

func resolveEventPaths(config EventASPathStoreConfig, sources eventASPathSources, requests pathRequestSet, all map[string]struct{}) (resolvedPathSet, error) {
	result := resolvedPathSet{Paths: make(map[string]ASPathSnapshot, len(all))}
	partitions := make([]int, 0, len(requests))
	for partition := range requests {
		partitions = append(partitions, partition)
	}
	sort.Ints(partitions)
	workers := config.Workers
	if workers < 1 {
		workers = 1
	}
	if workers > 32 {
		workers = 32
	}
	tasks := make(chan int)
	type outcome struct {
		partition int
		rows      int64
		paths     map[string]ASPathSnapshot
		err       error
	}
	outcomes := make(chan outcome, workers)
	var group sync.WaitGroup
	for worker := 0; worker < workers; worker++ {
		group.Add(1)
		go func() {
			defer group.Done()
			for partition := range tasks {
				wanted := requests[partition]
				found := make(map[string]ASPathSnapshot)
				meta := sources.RouteEvents.Partitions[partition].Paths
				rows, err := scanVerifiedJSONLGzip(config.RouteEventRoot, meta, 4<<20, func(raw []byte) error {
					var row asPathRow
					if err := json.Unmarshal(raw, &row); err != nil {
						return err
					}
					if _, needed := wanted[row.ASPathID]; needed {
						found[row.ASPathID] = row.ASPath
					}
					return nil
				})
				outcomes <- outcome{partition: partition, rows: rows, paths: found, err: err}
			}
		}()
	}
	go func() {
		for _, partition := range partitions {
			tasks <- partition
		}
		close(tasks)
		group.Wait()
		close(outcomes)
	}()
	completed := 0
	for item := range outcomes {
		if item.err != nil {
			return result, fmt.Errorf("AS_PATH partition %04d: %w", item.partition, item.err)
		}
		completed++
		result.ScannedPathRows += item.rows
		for id, path := range item.paths {
			if existing, ok := result.Paths[id]; ok && existing.Canonical != path.Canonical {
				return result, fmt.Errorf("AS_PATH content identity collision")
			}
			result.Paths[id] = path
		}
		if completed%64 == 0 {
			config.progress(fmt.Sprintf("event AS path dictionary scan=%d/%d resolved=%d/%d", completed, len(partitions), len(result.Paths), len(all)))
		}
	}
	result.PartitionCount = len(partitions)
	if len(result.Paths) != len(all) {
		missing := 0
		for id := range all {
			if _, ok := result.Paths[id]; !ok {
				missing++
			}
		}
		return result, fmt.Errorf("AS_PATH resolution incomplete: %d unresolved", missing)
	}
	return result, nil
}

func orderedPathRelationship(path ASPathSnapshot, affected, origin uint32) (bool, bool) {
	if path.Semantics != asPathSnapshotSemantic || path.CausalConclusion != nil || len(path.Segments) == 0 {
		return false, true
	}
	sequence := make([]uint32, 0, 16)
	for _, segment := range path.Segments {
		if segment.SegmentType != asSequenceSegment {
			return false, true
		}
		sequence = append(sequence, segment.ASNs...)
	}
	if len(sequence) == 0 || sequence[len(sequence)-1] != origin || affected == origin {
		return false, false
	}
	for index := 0; index < len(sequence)-1; index++ {
		if sequence[index] == affected {
			return true, false
		}
	}
	return false, false
}

func nullableText(value string) *string {
	value = strings.TrimSpace(value)
	if value == "" || value == "未知" || strings.EqualFold(value, "unknown") {
		return nil
	}
	copy := value
	return &copy
}

func stateForText(value *string) string {
	if value == nil {
		return "unknown"
	}
	return "observed"
}

func loadEventASStaticProfiles(path string, wanted map[uint32]struct{}) (map[uint32]EventASStaticProfile, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	reader := csv.NewReader(bufio.NewReaderSize(file, 1<<20))
	reader.ReuseRecord = true
	reader.FieldsPerRecord = -1
	header, err := reader.Read()
	if err != nil {
		return nil, err
	}
	columns := make(map[string]int, len(header))
	for index, name := range header {
		columns[name] = index
	}
	required := []string{"asn", "as_name", "org_name", "org_name_cn", "type", "type_cn"}
	for _, name := range required {
		if _, ok := columns[name]; !ok {
			return nil, fmt.Errorf("AS attribute snapshot misses %s", name)
		}
	}
	result := make(map[uint32]EventASStaticProfile, len(wanted))
	for {
		record, err := reader.Read()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, err
		}
		value, err := strconv.ParseUint(strings.TrimSpace(record[columns["asn"]]), 10, 32)
		if err != nil {
			continue
		}
		asn := uint32(value)
		if _, needed := wanted[asn]; !needed {
			continue
		}
		if _, exists := result[asn]; exists {
			continue
		}
		organization := nullableText(record[columns["org_name_cn"]])
		if organization == nil {
			organization = nullableText(record[columns["org_name"]])
		}
		nature := nullableText(record[columns["type_cn"]])
		if nature == nil {
			nature = nullableText(record[columns["type"]])
		}
		profile := EventASStaticProfile{
			ASN: asn, ASName: nullableText(record[columns["as_name"]]),
			Organization: organization, Nature: nature,
		}
		profile.NameState = stateForText(profile.ASName)
		profile.OrgState = stateForText(profile.Organization)
		profile.NatureState = stateForText(profile.Nature)
		result[asn] = profile
	}
	for asn := range wanted {
		if _, exists := result[asn]; !exists {
			result[asn] = EventASStaticProfile{
				ASN: asn, NameState: "unknown", OrgState: "unknown", NatureState: "unknown",
			}
		}
	}
	return result, nil
}

type eventPathRelationKey struct {
	AffectedASN   uint32
	DownstreamASN uint32
}

type eventPathEvidenceKey struct {
	eventPathRelationKey
	CohortMemberID string
	ASPathID       string
}

type eventPathEvidenceState struct {
	Key              eventPathEvidenceKey
	Prefix           string
	AddressFamily    string
	ASPathCanonical  string
	PeerASNs         map[uint32]struct{}
	ObservationCount int
}

type eventPathRelationState struct {
	Key                     eventPathRelationKey
	PathIDs                 map[string]struct{}
	MemberIDs               map[string]struct{}
	PeerASNs                map[uint32]struct{}
	ObservationCount        int
	ConcurrentPoints        int
	FirstConcurrentUTC      *string
	LastConcurrentUTC       *string
	PeakInterruptedPrefixes int
	PeakIPv4Addresses       uint64
	PeakIPv6Slash48         uint64
	CurrentInterrupted      int
	CurrentIPv4             *prefixCoverage
	CurrentIPv6             *prefixCoverage
}

type eventPrefixDefinition struct {
	Prefix            netip.Prefix
	AddressFamily     string
	CountryOriginASNs []uint32
}

type eventPrefixRuntime struct {
	Classification      string
	InvisibleDirections int
}

type eventASRuntime struct {
	Partial             int
	Complete            int
	Unknown             int
	InvisibleDirections int
}

type eventASPathWork struct {
	Metric                  EventMetricEventManifest
	Cohort                  EventCohortManifest
	EventASPathID           string
	ASNSummaries            map[uint32]*eventASNWindowSummary
	Members                 map[string]eventPrefixDefinition
	Relations               map[eventPathRelationKey]*eventPathRelationState
	Evidence                map[eventPathEvidenceKey]*eventPathEvidenceState
	KnownPathObservations   int64
	UnknownPathObservations int64
	OrderedObservations     int64
	AmbiguousObservations   int64
	AffectedRows            []EventAffectedASRow
	DownstreamRows          []EventPathDownstreamRow
	EvidenceRows            []EventPathEvidenceRow
}

func affectedASSet(summaries map[uint32]*eventASNWindowSummary) map[uint32]struct{} {
	result := make(map[uint32]struct{})
	for asn, summary := range summaries {
		if summary.EverAffected {
			result[asn] = struct{}{}
		}
	}
	return result
}

func pathAffectedParents(path ASPathSnapshot, origin uint32, affected map[uint32]struct{}) ([]uint32, bool, error) {
	if path.Semantics != asPathSnapshotSemantic || path.CausalConclusion != nil || len(path.Segments) == 0 {
		return nil, true, nil
	}
	sequence := make([]uint32, 0, 16)
	hasUnordered := false
	affectedAnywhere := false
	for _, segment := range path.Segments {
		if segment.SegmentType != asSequenceSegment {
			hasUnordered = true
			for _, asn := range segment.ASNs {
				if _, ok := affected[asn]; ok && asn != origin {
					affectedAnywhere = true
				}
			}
			continue
		}
		for _, asn := range segment.ASNs {
			if _, ok := affected[asn]; ok && asn != origin {
				affectedAnywhere = true
			}
		}
		sequence = append(sequence, segment.ASNs...)
	}
	if hasUnordered {
		return nil, affectedAnywhere, nil
	}
	if len(sequence) == 0 || sequence[len(sequence)-1] != origin {
		return nil, false, fmt.Errorf("known observation origin does not match AS_PATH tail")
	}
	seen := make(map[uint32]struct{})
	parents := make([]uint32, 0)
	for index := 0; index < len(sequence)-1; index++ {
		asn := sequence[index]
		if asn == origin {
			continue
		}
		if _, ok := affected[asn]; !ok {
			continue
		}
		if _, duplicate := seen[asn]; duplicate {
			continue
		}
		seen[asn] = struct{}{}
		parents = append(parents, asn)
	}
	sort.Slice(parents, func(i, j int) bool { return parents[i] < parents[j] })
	return parents, false, nil
}

func buildEventRelations(
	config EventASPathStoreConfig,
	work *eventASPathWork,
	paths map[string]ASPathSnapshot,
) error {
	members, err := readEventMetricMembers(config.EventCohortRoot, work.Cohort)
	if err != nil {
		return err
	}
	work.Members = make(map[string]eventPrefixDefinition, len(members))
	work.Relations = make(map[eventPathRelationKey]*eventPathRelationState)
	work.Evidence = make(map[eventPathEvidenceKey]*eventPathEvidenceState)
	affected := affectedASSet(work.ASNSummaries)
	for _, member := range members {
		prefix, err := netip.ParsePrefix(member.Prefix)
		if err != nil || prefix.String() != member.Prefix {
			return fmt.Errorf("cohort member prefix is not canonical")
		}
		work.Members[member.CohortMemberID] = eventPrefixDefinition{
			Prefix: prefix, AddressFamily: member.AddressFamily,
			CountryOriginASNs: append([]uint32(nil), member.CountryOriginASNs...),
		}
		for _, direction := range member.ExpectedDirections {
			for _, observation := range direction.RouteObservations {
				if observation.ASPathStatus != "known" || observation.ASPathID == "" {
					work.UnknownPathObservations++
					continue
				}
				work.KnownPathObservations++
				path, ok := paths[observation.ASPathID]
				if !ok {
					return fmt.Errorf("resolved AS_PATH is missing")
				}
				if observation.OriginStatus != "known" || observation.OriginASN == nil {
					continue
				}
				parents, ambiguous, err := pathAffectedParents(path, *observation.OriginASN, affected)
				if err != nil {
					return err
				}
				if ambiguous {
					work.AmbiguousObservations++
					continue
				}
				for _, parent := range parents {
					work.OrderedObservations++
					key := eventPathRelationKey{AffectedASN: parent, DownstreamASN: *observation.OriginASN}
					relation := work.Relations[key]
					if relation == nil {
						ipv4, _ := newPrefixCoverage(4)
						ipv6, _ := newPrefixCoverage(6)
						relation = &eventPathRelationState{
							Key: key, PathIDs: make(map[string]struct{}), MemberIDs: make(map[string]struct{}),
							PeerASNs: make(map[uint32]struct{}), CurrentIPv4: ipv4, CurrentIPv6: ipv6,
						}
						work.Relations[key] = relation
					}
					relation.PathIDs[observation.ASPathID] = struct{}{}
					relation.MemberIDs[member.CohortMemberID] = struct{}{}
					relation.PeerASNs[direction.PeerASN] = struct{}{}
					relation.ObservationCount++
					evidenceKey := eventPathEvidenceKey{
						eventPathRelationKey: key, CohortMemberID: member.CohortMemberID,
						ASPathID: observation.ASPathID,
					}
					evidence := work.Evidence[evidenceKey]
					if evidence == nil {
						evidence = &eventPathEvidenceState{
							Key: evidenceKey, Prefix: member.Prefix, AddressFamily: member.AddressFamily,
							ASPathCanonical: path.Canonical, PeerASNs: make(map[uint32]struct{}),
						}
						work.Evidence[evidenceKey] = evidence
					}
					evidence.PeerASNs[direction.PeerASN] = struct{}{}
					evidence.ObservationCount++
				}
			}
		}
	}
	return nil
}

func classificationInterrupted(value string) bool {
	return value == eventPrefixPartial || value == eventPrefixComplete
}

func adjustClass(runtime *eventASRuntime, classification string, delta int) error {
	switch classification {
	case eventPrefixNormal:
	case eventPrefixPartial:
		runtime.Partial += delta
	case eventPrefixComplete:
		runtime.Complete += delta
	case eventPrefixUnknown:
		runtime.Unknown += delta
	default:
		return fmt.Errorf("unknown prefix classification")
	}
	if runtime.Partial < 0 || runtime.Complete < 0 || runtime.Unknown < 0 {
		return fmt.Errorf("AS prefix runtime underflow")
	}
	return nil
}

func adjustRelationPrefix(relation *eventPathRelationState, definition eventPrefixDefinition, classification string, delta int) error {
	if !classificationInterrupted(classification) {
		return nil
	}
	relation.CurrentInterrupted += delta
	if relation.CurrentInterrupted < 0 {
		return fmt.Errorf("path relation prefix population underflow")
	}
	if definition.Prefix.Addr().Is4() {
		if delta > 0 {
			return relation.CurrentIPv4.Add(definition.Prefix)
		}
		return relation.CurrentIPv4.Remove(definition.Prefix)
	}
	if delta > 0 {
		return relation.CurrentIPv6.Add(definition.Prefix)
	}
	return relation.CurrentIPv6.Remove(definition.Prefix)
}

func processEventConcurrentState(config EventASPathStoreConfig, work *eventASPathWork) error {
	prefixRows := make([]EventMetricPrefixStateRow, 0, work.Metric.PrefixStates.RowCount)
	_, err := scanVerifiedJSONLGzip(config.EventMetricRoot, work.Metric.PrefixStates, 4<<20, func(raw []byte) error {
		var row EventMetricPrefixStateRow
		if err := json.Unmarshal(raw, &row); err != nil {
			return err
		}
		prefixRows = append(prefixRows, row)
		return nil
	})
	if err != nil {
		return err
	}
	series := make([]EventMetricSeriesRow, 0, work.Metric.Series.RowCount)
	_, err = scanVerifiedJSONLGzip(config.EventMetricRoot, work.Metric.Series, 4<<20, func(raw []byte) error {
		var row EventMetricSeriesRow
		if err := json.Unmarshal(raw, &row); err != nil {
			return err
		}
		if row.EventMetricID != work.Metric.EventMetricID || row.CohortID != work.Metric.CohortID ||
			row.ValueState != "observed" || row.Values == nil || row.MissingReason != nil {
			return fmt.Errorf("event metric series is not complete observed input")
		}
		series = append(series, row)
		return nil
	})
	if err != nil {
		return err
	}
	if int64(len(series)) != work.Metric.StatePointCount {
		return fmt.Errorf("event metric series population mismatch")
	}
	reverseRelations := make(map[string][]*eventPathRelationState)
	for _, relation := range work.Relations {
		for memberID := range relation.MemberIDs {
			reverseRelations[memberID] = append(reverseRelations[memberID], relation)
		}
	}
	current := make(map[string]eventPrefixRuntime, len(work.Members))
	asRuntime := make(map[uint32]*eventASRuntime, len(work.ASNSummaries))
	for asn := range work.ASNSummaries {
		asRuntime[asn] = &eventASRuntime{}
	}
	apply := func(row EventMetricPrefixStateRow) error {
		definition, ok := work.Members[row.CohortMemberID]
		if !ok || row.EventMetricID != work.Metric.EventMetricID || row.CohortID != work.Metric.CohortID ||
			row.Prefix != definition.Prefix.String() || row.AddressFamily != definition.AddressFamily {
			return fmt.Errorf("event prefix state does not bind cohort member")
		}
		if old, exists := current[row.CohortMemberID]; exists {
			for _, asn := range definition.CountryOriginASNs {
				runtime := asRuntime[asn]
				if runtime == nil {
					return fmt.Errorf("event prefix references unknown fixed ASN")
				}
				if err := adjustClass(runtime, old.Classification, -1); err != nil {
					return err
				}
				runtime.InvisibleDirections -= old.InvisibleDirections
				if runtime.InvisibleDirections < 0 {
					return fmt.Errorf("AS invisible direction population underflow")
				}
			}
			for _, relation := range reverseRelations[row.CohortMemberID] {
				if err := adjustRelationPrefix(relation, definition, old.Classification, -1); err != nil {
					return err
				}
			}
		} else if row.RecordKind != "baseline" {
			return fmt.Errorf("first event prefix row is not baseline")
		}
		value := eventPrefixRuntime{Classification: row.Classification, InvisibleDirections: row.InvisibleDirectionCount}
		if row.ExpectedDirectionCount != row.VisibleDirectionCount+row.InvisibleDirectionCount+row.UnknownDirectionCount {
			return fmt.Errorf("event prefix direction population mismatch")
		}
		for _, asn := range definition.CountryOriginASNs {
			runtime := asRuntime[asn]
			if err := adjustClass(runtime, value.Classification, 1); err != nil {
				return err
			}
			runtime.InvisibleDirections += value.InvisibleDirections
		}
		for _, relation := range reverseRelations[row.CohortMemberID] {
			if err := adjustRelationPrefix(relation, definition, value.Classification, 1); err != nil {
				return err
			}
		}
		current[row.CohortMemberID] = value
		return nil
	}
	rowIndex := 0
	previousSlot := -1
	for _, point := range series {
		if previousSlot >= 0 && point.StateSlot != previousSlot+1 {
			return fmt.Errorf("event metric series slots are not contiguous")
		}
		previousSlot = point.StateSlot
		for rowIndex < len(prefixRows) && prefixRows[rowIndex].StateSlot == point.StateSlot {
			if err := apply(prefixRows[rowIndex]); err != nil {
				return err
			}
			rowIndex++
		}
		for asn, summary := range work.ASNSummaries {
			if !summary.EverAffected {
				continue
			}
			if asRuntime[asn].InvisibleDirections > summary.PeakInvisibleDirections {
				summary.PeakInvisibleDirections = asRuntime[asn].InvisibleDirections
			}
		}
		for _, relation := range work.Relations {
			parent := asRuntime[relation.Key.AffectedASN]
			parentActive := parent != nil && parent.Unknown == 0 && parent.Partial+parent.Complete > 0
			if !parentActive || relation.CurrentInterrupted == 0 {
				continue
			}
			relation.ConcurrentPoints++
			value := point.StatePointUTC
			if relation.FirstConcurrentUTC == nil {
				relation.FirstConcurrentUTC = &value
			}
			last := value
			relation.LastConcurrentUTC = &last
			if relation.CurrentInterrupted > relation.PeakInterruptedPrefixes {
				relation.PeakInterruptedPrefixes = relation.CurrentInterrupted
			}
			if relation.CurrentIPv4.Covered() > relation.PeakIPv4Addresses {
				relation.PeakIPv4Addresses = relation.CurrentIPv4.Covered()
			}
			if relation.CurrentIPv6.Covered() > relation.PeakIPv6Slash48 {
				relation.PeakIPv6Slash48 = relation.CurrentIPv6.Covered()
			}
		}
	}
	if rowIndex != len(prefixRows) || len(current) != len(work.Members) {
		return fmt.Errorf("event prefix change stream did not reconstruct full cohort")
	}
	return nil
}

func sortedUint32Set(values map[uint32]struct{}) []uint32 {
	result := make([]uint32, 0, len(values))
	for value := range values {
		result = append(result, value)
	}
	sort.Slice(result, func(i, j int) bool { return result[i] < result[j] })
	return result
}

func finalizeEventRows(work *eventASPathWork, snapshotSHA string, profiles map[uint32]EventASStaticProfile) {
	downstreamByAffected := make(map[uint32]int)
	concurrentByAffected := make(map[uint32]int)
	for _, relation := range work.Relations {
		downstreamByAffected[relation.Key.AffectedASN]++
		if relation.ConcurrentPoints > 0 {
			concurrentByAffected[relation.Key.AffectedASN]++
		}
		profile := profiles[relation.Key.DownstreamASN]
		work.DownstreamRows = append(work.DownstreamRows, EventPathDownstreamRow{
			SchemaVersion: EventPathDownstreamVersion, EventASPathID: work.EventASPathID,
			EventMetricID: work.Metric.EventMetricID, CohortID: work.Metric.CohortID,
			AffectedASN: relation.Key.AffectedASN, DownstreamASN: relation.Key.DownstreamASN,
			DownstreamASName: profile.ASName, DownstreamOrganization: profile.Organization,
			DownstreamNature: profile.Nature, DownstreamNameState: profile.NameState,
			DownstreamOrganizationState: profile.OrgState, DownstreamNatureState: profile.NatureState,
			ObservedPathCount: len(relation.PathIDs), AssociatedFixedPrefixCount: len(relation.MemberIDs),
			IndependentDirectionCount: len(relation.PeerASNs), RouteObservationCount: relation.ObservationCount,
			ConcurrentStatePointCount:            relation.ConcurrentPoints,
			FirstConcurrentStatePointUTC:         relation.FirstConcurrentUTC,
			LastConcurrentStatePointUTC:          relation.LastConcurrentUTC,
			PeakConcurrentInterruptedPrefixCount: relation.PeakInterruptedPrefixes,
			PeakConcurrentIPv4AddressCount:       relation.PeakIPv4Addresses,
			PeakConcurrentIPv6Slash48Count:       relation.PeakIPv6Slash48,
			PathRelationshipSemantics:            "ordered_rrc25_as_path_contains_affected_as_before_known_origin",
		})
	}
	for asn, summary := range work.ASNSummaries {
		if !summary.EverAffected {
			continue
		}
		classification := eventASNAffected
		if summary.EverRouteInterrupted {
			classification = eventASNRouteInterrupted
		}
		profile := profiles[asn]
		work.AffectedRows = append(work.AffectedRows, EventAffectedASRow{
			SchemaVersion: EventAffectedASVersion, EventASPathID: work.EventASPathID,
			EventMetricID: work.Metric.EventMetricID, CohortID: work.Metric.CohortID,
			ASN: asn, ASName: profile.ASName, Organization: profile.Organization, Nature: profile.Nature,
			NameState: profile.NameState, OrganizationState: profile.OrgState, NatureState: profile.NatureState,
			EventClassification: classification, FixedPrefixCount: summary.FixedPrefixCount,
			PeakPartialPrefixCount: summary.PeakPartial, PeakCompletePrefixCount: summary.PeakComplete,
			PeakInvisibleDirectionCount:   summary.PeakInvisibleDirections,
			PathDownstreamASNCount:        downstreamByAffected[asn],
			ConcurrentDownstreamASNCount:  concurrentByAffected[asn],
			StaticAttributeSnapshotSHA256: snapshotSHA,
		})
	}
	for _, evidence := range work.Evidence {
		work.EvidenceRows = append(work.EvidenceRows, EventPathEvidenceRow{
			SchemaVersion: EventPathEvidenceVersion, EventASPathID: work.EventASPathID,
			CohortID: work.Metric.CohortID, AffectedASN: evidence.Key.AffectedASN,
			DownstreamASN: evidence.Key.DownstreamASN, CohortMemberID: evidence.Key.CohortMemberID,
			Prefix: evidence.Prefix, AddressFamily: evidence.AddressFamily,
			ASPathID: evidence.Key.ASPathID, ASPathCanonical: evidence.ASPathCanonical,
			IndependentPeerASNs:   sortedUint32Set(evidence.PeerASNs),
			RouteObservationCount: evidence.ObservationCount,
			RelationshipState:     "observed_ordered_path_association",
		})
	}
	sort.Slice(work.AffectedRows, func(i, j int) bool {
		left, right := work.AffectedRows[i], work.AffectedRows[j]
		leftSeverity, rightSeverity := 1, 1
		if left.EventClassification == eventASNRouteInterrupted {
			leftSeverity = 0
		}
		if right.EventClassification == eventASNRouteInterrupted {
			rightSeverity = 0
		}
		if leftSeverity != rightSeverity {
			return leftSeverity < rightSeverity
		}
		if left.PeakCompletePrefixCount != right.PeakCompletePrefixCount {
			return left.PeakCompletePrefixCount > right.PeakCompletePrefixCount
		}
		if left.PathDownstreamASNCount != right.PathDownstreamASNCount {
			return left.PathDownstreamASNCount > right.PathDownstreamASNCount
		}
		return left.ASN < right.ASN
	})
	sort.Slice(work.DownstreamRows, func(i, j int) bool {
		left, right := work.DownstreamRows[i], work.DownstreamRows[j]
		if left.AffectedASN != right.AffectedASN {
			return left.AffectedASN < right.AffectedASN
		}
		return left.DownstreamASN < right.DownstreamASN
	})
	sort.Slice(work.EvidenceRows, func(i, j int) bool {
		left, right := work.EvidenceRows[i], work.EvidenceRows[j]
		if left.AffectedASN != right.AffectedASN {
			return left.AffectedASN < right.AffectedASN
		}
		if left.DownstreamASN != right.DownstreamASN {
			return left.DownstreamASN < right.DownstreamASN
		}
		if left.Prefix != right.Prefix {
			return left.Prefix < right.Prefix
		}
		return left.ASPathID < right.ASPathID
	})
}

func eventASPathDirectory(work *eventASPathWork) string {
	suffix := work.EventASPathID
	if len(suffix) > 12 {
		suffix = suffix[len(suffix)-12:]
	}
	return filepath.ToSlash(filepath.Join(
		"events", work.Metric.CountryCode,
		fmt.Sprintf("slot-%04d-%s", work.Cohort.CohortStateSlot, suffix),
	))
}

func writeEventASPathRows(root string, work *eventASPathWork) (EventASPathEventManifest, error) {
	var manifest EventASPathEventManifest
	directory := eventASPathDirectory(work)
	absolute := filepath.Join(root, filepath.FromSlash(directory))
	if err := os.MkdirAll(absolute, 0o750); err != nil {
		return manifest, err
	}
	writeRows := func(name string, rows []any) (RouteEventStoreFile, error) {
		writer, err := newDeterministicJSONLGzipWriter(filepath.Join(absolute, name))
		if err != nil {
			return RouteEventStoreFile{}, err
		}
		for _, row := range rows {
			if err := writer.Write(row); err != nil {
				writer.Abort()
				return RouteEventStoreFile{}, err
			}
		}
		return writer.Close(filepath.ToSlash(filepath.Join(directory, name)))
	}
	affectedValues := make([]any, len(work.AffectedRows))
	for index := range work.AffectedRows {
		affectedValues[index] = work.AffectedRows[index]
	}
	downstreamValues := make([]any, len(work.DownstreamRows))
	for index := range work.DownstreamRows {
		downstreamValues[index] = work.DownstreamRows[index]
	}
	evidenceValues := make([]any, len(work.EvidenceRows))
	for index := range work.EvidenceRows {
		evidenceValues[index] = work.EvidenceRows[index]
	}
	affectedFile, err := writeRows("affected-as.jsonl.gz", affectedValues)
	if err != nil {
		return manifest, err
	}
	downstreamFile, err := writeRows("path-downstreams.jsonl.gz", downstreamValues)
	if err != nil {
		return manifest, err
	}
	evidenceFile, err := writeRows("path-evidence.jsonl.gz", evidenceValues)
	if err != nil {
		return manifest, err
	}
	downstreamASNs := make(map[uint32]struct{})
	for _, row := range work.DownstreamRows {
		downstreamASNs[row.DownstreamASN] = struct{}{}
	}
	manifest = EventASPathEventManifest{
		SchemaVersion: EventASPathEventVersion, Status: "complete", Directory: directory,
		EventASPathID: work.EventASPathID, EventMetricID: work.Metric.EventMetricID,
		LegacyReference: work.Metric.LegacyReference, CountryCode: work.Metric.CountryCode,
		CohortID: work.Metric.CohortID, WindowStartUTC: work.Metric.WindowStartUTC,
		ProjectionEndStatePointUTC: work.Metric.ProjectionEndStatePointUTC,
		AffectedASCount:            len(work.AffectedRows), PathDownstreamRelationCount: len(work.DownstreamRows),
		PathDownstreamASNCount: len(downstreamASNs), PathEvidenceCount: int64(len(work.EvidenceRows)),
		KnownASPathObservationCount:         work.KnownPathObservations,
		UnknownASPathObservationCount:       work.UnknownPathObservations,
		OrderedRelationshipObservationCount: work.OrderedObservations,
		AmbiguousPathObservationCount:       work.AmbiguousObservations,
		AffectedAS:                          affectedFile, Downstreams: downstreamFile, PathEvidence: evidenceFile,
	}
	for _, row := range work.AffectedRows {
		if row.EventClassification == eventASNRouteInterrupted {
			manifest.RouteInterruptedASCount++
		} else {
			manifest.AffectedOnlyASCount++
		}
		if row.NameState == "unknown" {
			manifest.UnknownStaticNameCount++
		}
		if row.OrganizationState == "unknown" {
			manifest.UnknownStaticOrganizationCount++
		}
		if row.NatureState == "unknown" {
			manifest.UnknownStaticNatureCount++
		}
	}
	for _, row := range work.DownstreamRows {
		if row.ConcurrentStatePointCount > 0 {
			manifest.ConcurrentDownstreamRelationCount++
		}
	}
	manifest.ContentSHA256 = eventASPathEventContentSHA(manifest)
	if _, err := writeJSONImmutable(filepath.Join(absolute, "manifest.json"), manifest); err != nil {
		return manifest, err
	}
	return manifest, nil
}

func buildEventASPathWorks(config EventASPathStoreConfig, sources eventASPathSources, datasetID string, paths map[string]ASPathSnapshot) ([]*eventASPathWork, map[uint32]struct{}, error) {
	cohorts := make(map[string]EventCohortManifest, len(sources.Cohorts.Cohorts))
	for _, cohort := range sources.Cohorts.Cohorts {
		cohorts[cohort.CohortID] = cohort
	}
	works := make([]*eventASPathWork, 0, len(sources.Metrics.Events))
	wantedProfiles := make(map[uint32]struct{})
	for index, metric := range sources.Metrics.Events {
		if index%8 == 0 {
			config.progress(fmt.Sprintf("event AS path relation projection=%d/%d", index, len(sources.Metrics.Events)))
		}
		cohort, ok := cohorts[metric.CohortID]
		if !ok || cohort.ContentSHA256 != metric.CohortContentSHA256 {
			return nil, nil, fmt.Errorf("event metric cohort binding mismatch")
		}
		summaries, err := readEventASNSummaries(config.EventMetricRoot, metric)
		if err != nil {
			return nil, nil, err
		}
		work := &eventASPathWork{
			Metric: metric, Cohort: cohort, EventASPathID: eventASPathID(datasetID, metric),
			ASNSummaries: summaries,
		}
		if err := buildEventRelations(config, work, paths); err != nil {
			return nil, nil, err
		}
		if err := processEventConcurrentState(config, work); err != nil {
			return nil, nil, err
		}
		for asn, summary := range summaries {
			if summary.EverAffected {
				wantedProfiles[asn] = struct{}{}
			}
		}
		for key := range work.Relations {
			wantedProfiles[key.DownstreamASN] = struct{}{}
		}
		works = append(works, work)
	}
	return works, wantedProfiles, nil
}

func CreateEventASPathStore(config EventASPathStoreConfig) (EventASPathStoreManifest, error) {
	var empty EventASPathStoreManifest
	if config.Output == "" {
		return empty, fmt.Errorf("event AS path output is required")
	}
	sources, err := loadEventASPathSources(config)
	if err != nil {
		return empty, err
	}
	runID, datasetID := eventASPathIdentity(sources.Metrics, sources.ASSnapshotSHA, config.ImplementationID)
	if config.Resume {
		if _, err := os.Stat(config.Output); err == nil {
			return LoadEventASPathStore(config.Output, config)
		} else if !os.IsNotExist(err) {
			return empty, err
		}
	}
	if err := os.MkdirAll(filepath.Dir(config.Output), 0o750); err != nil {
		return empty, err
	}
	temporary := config.Output + ".tmp"
	if _, err := os.Stat(temporary); err == nil {
		return empty, fmt.Errorf("event AS path temporary output already exists")
	} else if !os.IsNotExist(err) {
		return empty, err
	}
	requests, allPaths, knownPaths, unknownPaths, err := collectEventPathRequests(config, sources)
	if err != nil {
		return empty, err
	}
	config.progress(fmt.Sprintf("event AS path requests partitions=%d unique_paths=%d", len(requests), len(allPaths)))
	resolved, err := resolveEventPaths(config, sources, requests, allPaths)
	if err != nil {
		return empty, err
	}
	works, wantedProfiles, err := buildEventASPathWorks(config, sources, datasetID, resolved.Paths)
	if err != nil {
		return empty, err
	}
	profiles, err := loadEventASStaticProfiles(config.ASSnapshotPath, wantedProfiles)
	if err != nil {
		return empty, err
	}
	for _, work := range works {
		finalizeEventRows(work, sources.ASSnapshotSHA, profiles)
	}
	if err := os.Mkdir(temporary, 0o750); err != nil {
		return empty, err
	}
	failed := true
	defer func() {
		if failed {
			_ = os.Rename(temporary, temporary+".failed")
		}
	}()
	events := make([]EventASPathEventManifest, 0, len(works))
	for index, work := range works {
		if index%8 == 0 {
			config.progress(fmt.Sprintf("event AS path write=%d/%d", index, len(works)))
		}
		event, err := writeEventASPathRows(temporary, work)
		if err != nil {
			return empty, err
		}
		events = append(events, event)
	}
	sort.Slice(events, func(i, j int) bool {
		if events[i].WindowStartUTC != events[j].WindowStartUTC {
			return events[i].WindowStartUTC < events[j].WindowStartUTC
		}
		return events[i].LegacyReference < events[j].LegacyReference
	})
	manifest := EventASPathStoreManifest{
		SchemaVersion: EventASPathStoreVersion, Status: "complete", RunID: runID, DatasetID: datasetID,
		CollectorID: "rrc25", WindowStartUTC: sources.Metrics.WindowStartUTC,
		WindowEndExclusiveUTC: sources.Metrics.WindowEndExclusiveUTC,
		ImplementationID:      config.ImplementationID, ProjectorName: EventASPathProjectorName,
		ProjectorVersion:                EventASPathProjectorVersion,
		SourceRouteEventDatasetID:       sources.RouteEvents.DatasetID,
		SourceRouteEventContentSHA256:   sources.RouteEvents.ContentSHA256,
		SourceRouteEventManifestSHA256:  sources.RouteManifestSHA,
		SourceEventCohortDatasetID:      sources.Cohorts.DatasetID,
		SourceEventCohortContentSHA256:  sources.Cohorts.ContentSHA256,
		SourceEventCohortManifestSHA256: sources.CohortManifestSHA,
		SourceEventMetricDatasetID:      sources.Metrics.DatasetID,
		SourceEventMetricContentSHA256:  sources.Metrics.ContentSHA256,
		SourceEventMetricManifestSHA256: sources.MetricManifestSHA,
		ASAttributeSnapshotName:         filepath.Base(config.ASSnapshotPath),
		ASAttributeSnapshotSHA256:       sources.ASSnapshotSHA,
		ASAttributeSnapshotSizeBytes:    sources.ASSnapshotSize,
		ASAttributeDuplicatePolicy:      "first_valid_row_wins_matching_existing_as_feature_loader",
		ASAttributeFieldSemantics:       "name_organization_and_nature_are_selected_from_the_frozen_existing_as_entity_snapshot_and_missing_values_remain_unknown",
		PathRelationshipSemantics:       "downstream_is_the_known_route_origin_when_an_ordered_rrc25_as_path_contains_the_affected_as_before_that_origin",
		UnorderedPathPolicy:             "as_set_confederation_or_missing_path_never_creates_an_ordered_downstream_relationship",
		CausalBoundary:                  "path_containment_is_observed_control_plane_association_not_dependency_propagation_impact_or_cause",
		EventCount:                      len(events), ResolvedASPathCount: len(resolved.Paths),
		ScannedASPathPartitionCount: resolved.PartitionCount, ScannedASPathRowCount: resolved.ScannedPathRows,
		KnownASPathObservationCount: knownPaths, UnknownASPathObservationCount: unknownPaths,
		Events: events,
	}
	for _, event := range events {
		manifest.AffectedASCount += int64(event.AffectedASCount)
		manifest.PathDownstreamRelationCount += int64(event.PathDownstreamRelationCount)
		manifest.ConcurrentDownstreamRelationCount += int64(event.ConcurrentDownstreamRelationCount)
		manifest.PathEvidenceCount += event.PathEvidenceCount
	}
	var eventKnown, eventUnknown int64
	for _, event := range events {
		eventKnown += event.KnownASPathObservationCount
		eventUnknown += event.UnknownASPathObservationCount
	}
	if eventKnown != knownPaths || eventUnknown != unknownPaths || manifest.EventCount != sources.Metrics.EventCount {
		return empty, fmt.Errorf("event AS path root population mismatch")
	}
	manifest.ContentSHA256 = eventASPathStoreContentSHA(manifest)
	if _, err := writeJSONImmutable(filepath.Join(temporary, "manifest.json"), manifest); err != nil {
		return empty, err
	}
	if _, err := writeJSONImmutable(filepath.Join(temporary, "COMPLETE.json"), manifest); err != nil {
		return empty, err
	}
	if err := os.Rename(temporary, config.Output); err != nil {
		return empty, err
	}
	failed = false
	return LoadEventASPathStore(config.Output, config)
}

func RunEventASPathStore(config EventASPathStoreConfig) (EventASPathStoreManifest, error) {
	var empty EventASPathStoreManifest
	if config.Output == "" {
		return empty, fmt.Errorf("event AS path output is required")
	}
	if _, err := os.Lstat(config.Output); err == nil {
		if !config.Resume {
			return empty, fmt.Errorf("event AS path output already exists")
		}
		return LoadEventASPathStore(config.Output, config)
	} else if !os.IsNotExist(err) {
		return empty, err
	}
	return CreateEventASPathStore(config)
}

func LoadEventASPathStore(root string, config EventASPathStoreConfig) (EventASPathStoreManifest, error) {
	var manifest EventASPathStoreManifest
	sources, err := loadEventASPathSources(config)
	if err != nil {
		return manifest, err
	}
	runID, datasetID := eventASPathIdentity(sources.Metrics, sources.ASSnapshotSHA, config.ImplementationID)
	manifestRaw, err := readJSON(filepath.Join(root, "manifest.json"), &manifest)
	if err != nil {
		return manifest, err
	}
	var complete EventASPathStoreManifest
	completeRaw, err := readJSON(filepath.Join(root, "COMPLETE.json"), &complete)
	if err != nil {
		return manifest, err
	}
	if !bytes.Equal(manifestRaw, completeRaw) || manifest.SchemaVersion != EventASPathStoreVersion ||
		manifest.Status != "complete" || manifest.RunID != runID || manifest.DatasetID != datasetID ||
		manifest.CollectorID != "rrc25" || manifest.ImplementationID != config.ImplementationID ||
		manifest.SourceRouteEventDatasetID != sources.RouteEvents.DatasetID ||
		manifest.SourceRouteEventContentSHA256 != sources.RouteEvents.ContentSHA256 ||
		manifest.SourceRouteEventManifestSHA256 != sources.RouteManifestSHA ||
		manifest.SourceEventCohortDatasetID != sources.Cohorts.DatasetID ||
		manifest.SourceEventCohortContentSHA256 != sources.Cohorts.ContentSHA256 ||
		manifest.SourceEventCohortManifestSHA256 != sources.CohortManifestSHA ||
		manifest.SourceEventMetricDatasetID != sources.Metrics.DatasetID ||
		manifest.SourceEventMetricContentSHA256 != sources.Metrics.ContentSHA256 ||
		manifest.SourceEventMetricManifestSHA256 != sources.MetricManifestSHA ||
		manifest.ASAttributeSnapshotSHA256 != sources.ASSnapshotSHA ||
		manifest.ASAttributeSnapshotSizeBytes != sources.ASSnapshotSize ||
		manifest.EventCount != len(manifest.Events) || manifest.EventCount != sources.Metrics.EventCount ||
		manifest.PathRelationshipSemantics != "downstream_is_the_known_route_origin_when_an_ordered_rrc25_as_path_contains_the_affected_as_before_that_origin" ||
		manifest.UnorderedPathPolicy != "as_set_confederation_or_missing_path_never_creates_an_ordered_downstream_relationship" ||
		manifest.CausalBoundary != "path_containment_is_observed_control_plane_association_not_dependency_propagation_impact_or_cause" ||
		manifest.ContentSHA256 != eventASPathStoreContentSHA(manifest) {
		return manifest, fmt.Errorf("complete event AS path store identity mismatch")
	}
	seen := make(map[string]struct{}, len(manifest.Events))
	var affected, relations, concurrent, evidence, known, unknown int64
	for _, event := range manifest.Events {
		if _, exists := seen[event.EventASPathID]; exists || event.SchemaVersion != EventASPathEventVersion ||
			event.Status != "complete" || event.ContentSHA256 != eventASPathEventContentSHA(event) {
			return manifest, fmt.Errorf("event AS path event identity mismatch")
		}
		seen[event.EventASPathID] = struct{}{}
		var disk EventASPathEventManifest
		if _, err := readJSON(filepath.Join(root, filepath.FromSlash(event.Directory), "manifest.json"), &disk); err != nil || disk != event {
			return manifest, fmt.Errorf("event AS path child manifest mismatch")
		}
		for _, meta := range []RouteEventStoreFile{event.AffectedAS, event.Downstreams, event.PathEvidence} {
			if err := verifyRouteEventStoreFile(root, meta); err != nil {
				return manifest, err
			}
		}
		if event.AffectedAS.RowCount != int64(event.AffectedASCount) ||
			event.Downstreams.RowCount != int64(event.PathDownstreamRelationCount) ||
			event.PathEvidence.RowCount != event.PathEvidenceCount ||
			event.RouteInterruptedASCount+event.AffectedOnlyASCount != event.AffectedASCount {
			return manifest, fmt.Errorf("event AS path child population mismatch")
		}
		affected += int64(event.AffectedASCount)
		relations += int64(event.PathDownstreamRelationCount)
		concurrent += int64(event.ConcurrentDownstreamRelationCount)
		evidence += event.PathEvidenceCount
		known += event.KnownASPathObservationCount
		unknown += event.UnknownASPathObservationCount
	}
	if affected != manifest.AffectedASCount || relations != manifest.PathDownstreamRelationCount ||
		concurrent != manifest.ConcurrentDownstreamRelationCount || evidence != manifest.PathEvidenceCount ||
		known != manifest.KnownASPathObservationCount || unknown != manifest.UnknownASPathObservationCount {
		return manifest, fmt.Errorf("event AS path aggregate population mismatch")
	}
	return manifest, nil
}
