package replay

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/netip"
	"sort"
	"time"
)

const (
	EngineVersion       = "rrc25-iran-go-replay/1.0.0"
	CheckpointVersion   = "rrc25-iran-go-checkpoint/v1"
	ObservationVersion  = "rrc25-country-observation-go/v1"
	WindowStartUTC      = "2026-02-28T10:05:00Z"
	WindowEndUTC        = "2026-02-28T15:00:00Z"
	CatchUpStartUTC     = "2026-02-28T08:00:00Z"
	ExpectedRIBPath     = "rrc25/2026.02/bview.20260228.0800.gz"
	ExpectedRIBSHA256   = "036e1a5b4d1554eae083d8b4d9de648f0ed95bfcd0ea781c4d001df68a23159c"
	ExpectedRIBBytes    = int64(426_297_361)
	ExpectedUpdateBytes = int64(401_865_192)
	ExpectedTotalBytes  = int64(828_162_553)
)

var (
	catchUpStart = mustUTC(CatchUpStartUTC)
	windowStart  = mustUTC(WindowStartUTC)
	windowEnd    = mustUTC(WindowEndUTC)
)

func mustUTC(value string) time.Time {
	parsed, err := time.Parse(time.RFC3339, value)
	if err != nil || parsed.Format(time.RFC3339) != value {
		panic("invalid frozen UTC time: " + value)
	}
	return parsed
}

type Artifact struct {
	ArtifactID      string `json:"artifact_id"`
	ArtifactTimeUTC string `json:"artifact_time_utc"`
	ArtifactType    string `json:"artifact_type"`
	CollectorID     string `json:"collector_id"`
	Compression     string `json:"compression"`
	FileSHA256      string `json:"file_sha256"`
	RelativePath    string `json:"relative_path"`
	SizeBytes       int64  `json:"size_bytes"`
}

type FixedInputs struct {
	RIB       Artifact   `json:"rib"`
	CatchUp   []Artifact `json:"catch_up_updates"`
	Formal    []Artifact `json:"formal_updates"`
	AllUpdate []Artifact `json:"-"`
}

type RouteKey struct {
	PeerIP  netip.Addr
	PeerASN uint32
	AFI     uint8
	Prefix  netip.Prefix
}

func (key RouteKey) Canonical() string {
	return fmt.Sprintf("%s|%d|%d|%s", key.PeerIP, key.PeerASN, key.AFI, key.Prefix)
}

func (key RouteKey) Less(other RouteKey) bool {
	return key.Canonical() < other.Canonical()
}

func VPIdentifier(peerIP netip.Addr, peerASN uint32) string {
	identity := fmt.Sprintf(
		`{"collector_id":"rrc25","peer_asn":%d,"peer_ip":%q,"schema":"vp_id_v1"}`,
		peerASN,
		peerIP.String(),
	)
	digest := sha256.Sum256([]byte(identity))
	return "vp_v1_" + hex.EncodeToString(digest[:])[:32]
}

type RouteValue struct {
	Present         bool   `json:"present"`
	OriginKnown     bool   `json:"origin_known"`
	OriginASN       uint32 `json:"origin_asn,omitempty"`
	LastAction      uint8  `json:"last_action"`
	LastEventMicros int64  `json:"last_event_micros"`
	ArtifactIndex   uint16 `json:"artifact_index"`
	RecordOrdinal   uint32 `json:"record_ordinal"`
	ElementOrdinal  uint32 `json:"element_ordinal"`
}

type BaselineMember struct {
	Key       RouteKey `json:"-"`
	KeyText   string   `json:"key"`
	OriginASN uint32   `json:"origin_asn"`
}

type StateEntry struct {
	Key     RouteKey
	Value   RouteValue
	Dynamic bool
}

type FamilyMetrics struct {
	BaselineASNCount      int      `json:"baseline_origin_asn_count"`
	VisibleASNCount       int      `json:"visible_origin_asn_count"`
	FullyVisibleASNs      []uint32 `json:"fully_visible_asns"`
	PartiallyVisibleASNs  []uint32 `json:"partially_visible_asns"`
	FullyInvisibleASNs    []uint32 `json:"fully_invisible_asns"`
	BaselinePrefixVPCount int      `json:"baseline_prefix_vp_count"`
	VisiblePrefixVPCount  int      `json:"visible_prefix_vp_count"`
}

type UpdateCounts struct {
	Announce int64 `json:"announce"`
	Withdraw int64 `json:"withdraw"`
}

type Observation struct {
	SchemaVersion         string              `json:"schema_version"`
	SnapshotID            string              `json:"snapshot_id"`
	ObservedAt            string              `json:"observed_at"`
	SlotStartUTC          string              `json:"slot_start_utc"`
	SlotEndUTC            string              `json:"slot_end_exclusive_utc"`
	SlotRole              string              `json:"slot_role"`
	CohortID              string              `json:"cohort_id"`
	BaselineASNCount      int                 `json:"baseline_asn_count"`
	BaselinePrefixVPCount int                 `json:"baseline_prefix_vp_count"`
	VisiblePrefixVPCount  int                 `json:"visible_prefix_vp_count"`
	VisiblePrefixVPRatio  float64             `json:"visible_prefix_vp_ratio"`
	AffectedASNCount      int                 `json:"affected_asn_count"`
	AffectedASNRatio      float64             `json:"affected_asn_ratio"`
	VisibleOriginASNCount int                 `json:"visible_origin_asn_count"`
	VisibleOriginASNRatio float64             `json:"visible_origin_asn_ratio"`
	IPv4                  FamilyMetrics       `json:"ipv4"`
	IPv6                  FamilyMetrics       `json:"ipv6"`
	DualClassifications   map[string][]uint32 `json:"dual_stack_classifications"`
	DynamicIRASNs         []uint32            `json:"dynamic_ir_origin_asns"`
	UpdateCounts          UpdateCounts        `json:"update_counts"`
}

func stableID(prefix string, value any, length int) string {
	raw, err := json.Marshal(value)
	if err != nil {
		panic(err)
	}
	digest := sha256.Sum256(raw)
	return prefix + hex.EncodeToString(digest[:])[:length]
}

func sortedUint32(values map[uint32]struct{}) []uint32 {
	result := make([]uint32, 0, len(values))
	for value := range values {
		result = append(result, value)
	}
	sort.Slice(result, func(i, j int) bool { return result[i] < result[j] })
	return result
}

type Quality struct {
	SchemaVersion              string          `json:"schema_version"`
	Status                     string          `json:"status"`
	EngineVersion              string          `json:"engine_version"`
	RIBPhysicalRecords         int64           `json:"rib_physical_records"`
	RIBEntries                 int64           `json:"rib_entries"`
	RIBRetainedMembers         int64           `json:"rib_retained_members"`
	RIBUnknownOrigins          int64           `json:"rib_unknown_origins"`
	RIBMappingUnknown          int64           `json:"rib_mapping_unknown"`
	RIBExplicitNonIR           int64           `json:"rib_explicit_non_ir"`
	UpdatePhysicalRecords      int64           `json:"update_physical_records"`
	UpdateRouteEvents          int64           `json:"update_route_events"`
	UpdateUnknownOrigins       int64           `json:"update_unknown_origins"`
	UpdateOptionalUnknownAttrs map[uint8]int64 `json:"update_optional_unknown_attributes"`
	InputCompressedBytes       int64           `json:"input_compressed_bytes"`
	ObservationCount           int             `json:"observation_count"`
	LastObservationAt          string          `json:"last_observation_at"`
	CheckpointResumeCount      int             `json:"checkpoint_resume_count"`
	Failures                   []string        `json:"failures"`
}
