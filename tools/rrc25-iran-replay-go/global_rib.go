package replay

import (
	"fmt"
	"io"
	"time"
)

type GlobalRIBQuality struct {
	SchemaVersion         string           `json:"schema_version"`
	Status                string           `json:"status"`
	EngineVersion         string           `json:"engine_version"`
	RIBPhysicalRecords    int64            `json:"rib_physical_records"`
	RIBEntries            int64            `json:"rib_entries"`
	RIBUniqueRouteKeys    int64            `json:"rib_unique_route_keys"`
	RIBDuplicateRouteRows int64            `json:"rib_duplicate_route_rows"`
	RIBKnownOrigins       int64            `json:"rib_known_origins"`
	RIBUnknownOrigins     int64            `json:"rib_unknown_origins"`
	RIBMappedCountry      int64            `json:"rib_mapped_country"`
	RIBMappingUnknown     int64            `json:"rib_mapping_unknown"`
	RIBCompactMPReach     int64            `json:"rib_compact_mp_reach_attributes"`
	LegacyUnsupportedMP   map[string]int64 `json:"rib_unsupported_multiprotocol_attributes,omitempty"`
	InputCompressedBytes  int64            `json:"input_compressed_bytes"`
	CountryBucketCount    int              `json:"country_bucket_count"`
	StateDigest           string           `json:"state_digest"`
	CheckpointCreatedAt   string           `json:"checkpoint_created_at"`
}

func (quality *GlobalRIBQuality) normalizeCompactMPReach() bool {
	if len(quality.LegacyUnsupportedMP) == 0 {
		return false
	}
	for _, count := range quality.LegacyUnsupportedMP {
		quality.RIBCompactMPReach += count
	}
	quality.LegacyUnsupportedMP = nil
	return true
}

func parseGlobalRIBRecord(
	payload []byte,
	subtype uint16,
	peers []peer,
	recordOrdinal uint32,
	state *GlobalReplayState,
	checkpoint *GlobalRIBCheckpointWriter,
	quality *GlobalRIBQuality,
) error {
	var afi uint8
	switch subtype {
	case ribIPv4Unicast:
		afi = 4
	case ribIPv6Unicast:
		afi = 6
	default:
		return fmt.Errorf("unsupported TABLE_DUMP_V2 subtype %d", subtype)
	}
	cursor := cursor{raw: payload, field: "global-RIB"}
	if _, err := cursor.u32("sequence"); err != nil {
		return err
	}
	bits, err := cursor.u8("prefix-length")
	if err != nil {
		return err
	}
	rawPrefix, err := cursor.take((int(bits)+7)/8, "prefix")
	if err != nil {
		return err
	}
	prefix, err := parsePrefix(rawPrefix, bits, afi)
	if err != nil {
		return err
	}
	entryCount, err := cursor.u16("entry-count")
	if err != nil {
		return err
	}
	for index := 0; index < int(entryCount); index++ {
		peerIndex, err := cursor.u16("peer-index")
		if err != nil {
			return err
		}
		if int(peerIndex) >= len(peers) {
			return fmt.Errorf("global RIB peer index out of range")
		}
		if _, err := cursor.u32("originated-time"); err != nil {
			return err
		}
		length, err := cursor.u16("attribute-length")
		if err != nil {
			return err
		}
		attributes, err := cursor.take(int(length), "attributes")
		if err != nil {
			return err
		}
		quality.RIBEntries++
		parsed, err := parseRIBAttributes(attributes, 4)
		if err != nil {
			return err
		}
		quality.RIBCompactMPReach += parsed.RIBCompactMPReach
		originKnown := parsed.OriginSeen && parsed.Origin.Known
		originASN := parsed.Origin.ASN
		if originKnown {
			quality.RIBKnownOrigins++
			if state.Mapping.CountryID(originASN) == 0 {
				quality.RIBMappingUnknown++
			} else {
				quality.RIBMappedCountry++
			}
		} else {
			quality.RIBUnknownOrigins++
		}
		key := RouteKey{
			PeerIP: peers[peerIndex].IP, PeerASN: peers[peerIndex].ASN,
			AFI: afi, Prefix: prefix,
		}
		elementOrdinal := uint32(index)
		if err := state.Seed(
			key, originKnown, originASN, recordOrdinal, elementOrdinal,
		); err != nil {
			return err
		}
		if err := checkpoint.Write(
			key, originKnown, originASN, recordOrdinal, elementOrdinal,
		); err != nil {
			return err
		}
	}
	return cursor.finish()
}

func SeedGlobalRIB(
	rawRoot string,
	artifact Artifact,
	mapping *GlobalCountryMapping,
	checkpointRoot string,
	checkpointShards int,
	routeCapacity int,
	progress func(string),
) (
	*GlobalReplayState,
	GlobalRIBCheckpointManifest,
	GlobalRIBQuality,
	error,
) {
	state, err := NewGlobalReplayState(mapping, routeCapacity)
	if err != nil {
		return nil, GlobalRIBCheckpointManifest{}, GlobalRIBQuality{}, err
	}
	checkpoint, err := NewGlobalRIBCheckpointWriter(
		checkpointRoot, checkpointShards,
	)
	if err != nil {
		return nil, GlobalRIBCheckpointManifest{}, GlobalRIBQuality{}, err
	}
	defer checkpoint.Abort()
	quality := GlobalRIBQuality{
		SchemaVersion: "rrc25-global-rib-quality/v1",
		Status:        "running",
		EngineVersion: GlobalEngineVersion,
	}
	var peers []peer
	recordOrdinal := uint32(0)
	err = withVerifiedGzip(rawRoot, artifact, func(reader io.Reader) error {
		for {
			_, mrtType, subtype, payload, err := readMRTRecord(reader)
			if err == io.EOF {
				break
			}
			if err != nil {
				return err
			}
			quality.RIBPhysicalRecords++
			currentOrdinal := recordOrdinal
			recordOrdinal++
			if mrtType != mrtTableDumpV2 {
				return fmt.Errorf("unsupported global RIB MRT type %d", mrtType)
			}
			if subtype == peerIndexTable {
				parsed, err := parsePeerIndex(payload)
				if err != nil {
					return err
				}
				peers = parsed
				continue
			}
			if peers == nil {
				return fmt.Errorf("global RIB record before peer index table")
			}
			if err := parseGlobalRIBRecord(
				payload, subtype, peers, currentOrdinal,
				state, checkpoint, &quality,
			); err != nil {
				return fmt.Errorf(
					"global RIB physical record %d subtype %d: %w",
					currentOrdinal, subtype, err,
				)
			}
			if progress != nil && quality.RIBPhysicalRecords%100_000 == 0 {
				progress(fmt.Sprintf(
					"全球 RIB 已处理 physical records=%d entries=%d unique_routes=%d",
					quality.RIBPhysicalRecords, quality.RIBEntries, len(state.Routes),
				))
			}
		}
		return nil
	})
	if err != nil {
		return nil, GlobalRIBCheckpointManifest{}, quality, err
	}
	if len(state.Routes) == 0 {
		return nil, GlobalRIBCheckpointManifest{}, quality, fmt.Errorf(
			"global RIB produced no route state",
		)
	}
	quality.RIBUniqueRouteKeys = int64(len(state.Routes))
	quality.RIBDuplicateRouteRows = state.SeedRouteRows - int64(len(state.Routes))
	quality.InputCompressedBytes = artifact.SizeBytes
	conservation, err := state.ValidateConservation()
	if err != nil {
		return nil, GlobalRIBCheckpointManifest{}, quality, err
	}
	manifest, err := checkpoint.Finalize(state, artifact)
	if err != nil {
		return nil, GlobalRIBCheckpointManifest{}, quality, err
	}
	quality.CountryBucketCount = len(manifest.Countries)
	quality.StateDigest = conservation.StateDigest
	quality.CheckpointCreatedAt = manifest.CreatedAt
	quality.Status = "pass"
	return state, manifest, quality, nil
}

func GlobalRIBRunIdentity(
	inputs FixedInputs,
	mapping *GlobalCountryMapping,
) string {
	return stableID("global_run_v1_", map[string]any{
		"engine_version":  GlobalEngineVersion,
		"rib_sha256":      inputs.RIB.FileSHA256,
		"update_count":    len(inputs.AllUpdate),
		"first_update":    inputs.AllUpdate[0].ArtifactTimeUTC,
		"last_update":     inputs.AllUpdate[len(inputs.AllUpdate)-1].ArtifactTimeUTC,
		"mapping_version": mapping.MappingVersion,
		"window_start":    WindowStartUTC,
		"window_end":      WindowEndUTC,
	}, 32)
}

func GlobalCheckpointAge(manifest GlobalRIBCheckpointManifest) (time.Duration, error) {
	created, err := time.Parse(time.RFC3339, manifest.CreatedAt)
	if err != nil {
		return 0, err
	}
	return time.Since(created), nil
}
