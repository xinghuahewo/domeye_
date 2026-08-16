package replay

import (
	"bytes"
	"net/netip"
	"path/filepath"
	"strings"
	"testing"
)

func TestGlobalRIBAttributesDecodeCompactMPReachWithoutWeakeningUpdates(
	t *testing.T,
) {
	raw := []byte{
		0x80, 14, 17,
		0x10,
		0x2a, 0x01, 0x0d, 0xb8, 0, 0, 0, 0,
		0, 0, 0, 0, 0, 0, 0, 1,
		0x40, 2, 6,
		2, 1, 0, 0, 0xfc, 0x00,
	}
	if _, err := parseAttributes(raw, 4); err == nil ||
		!strings.Contains(err.Error(), "unsupported MP_REACH AFI 4138") {
		t.Fatalf("strict UPDATE parsing must reject unsupported AFI: %v", err)
	}
	parsed, err := parseRIBAttributes(raw, 4)
	if err != nil {
		t.Fatal(err)
	}
	if !parsed.OriginSeen || !parsed.Origin.Known ||
		parsed.Origin.ASN != 64512 ||
		parsed.RIBCompactMPReach != 1 {
		t.Fatalf("unexpected compact RIB attributes: %+v", parsed)
	}
}

func TestGlobalRIBQualityMigratesLegacyCompactMPReachCounts(t *testing.T) {
	quality := GlobalRIBQuality{
		RIBCompactMPReach: 3,
		LegacyUnsupportedMP: map[string]int64{
			"legacy-a": 4,
			"legacy-b": 5,
		},
	}
	if !quality.normalizeCompactMPReach() ||
		quality.RIBCompactMPReach != 12 ||
		quality.LegacyUnsupportedMP != nil {
		t.Fatalf("legacy quality was not normalized: %+v", quality)
	}
	if quality.normalizeCompactMPReach() {
		t.Fatal("normalized quality must be idempotent")
	}
}

func TestSeedGlobalRIBRetainsAllCountriesAndUnknownBucket(t *testing.T) {
	rawRoot := t.TempDir()
	stream := bytes.Join([][]byte{
		peerIndexFixture(),
		ribFixture(netip.MustParsePrefix("203.0.113.0/24"), 64500),
		ribFixture(netip.MustParsePrefix("198.51.100.0/24"), 64501),
		ribFixture(netip.MustParsePrefix("192.0.2.0/24"), 65000),
	}, nil)
	artifact := writeGzipArtifact(
		t, rawRoot, "rrc25/test-global-rib.gz",
		stream, CatchUpStartUTC, "rib",
	)
	mapping := newSyntheticGlobalCountryMapping(map[uint32]string{
		64500: "IR",
		64501: "US",
	})
	checkpointRoot := filepath.Join(t.TempDir(), "checkpoints")
	state, manifest, quality, err := SeedGlobalRIB(
		rawRoot, artifact, mapping, checkpointRoot, 4, 3, nil,
	)
	if err != nil {
		t.Fatal(err)
	}
	if quality.RIBEntries != 3 || quality.RIBMappedCountry != 2 ||
		quality.RIBMappingUnknown != 1 || quality.RIBUniqueRouteKeys != 3 {
		t.Fatalf("unexpected global RIB quality: %+v", quality)
	}
	if len(manifest.Countries) != 3 ||
		manifest.Conservation.UnknownBaselinePrefixVP != 1 {
		t.Fatalf("unknown bucket was not retained: %+v", manifest)
	}
	restored, restoredManifest, err := LoadGlobalRIBCheckpoint(
		checkpointRoot, mapping, artifact,
	)
	if err != nil {
		t.Fatal(err)
	}
	if restoredManifest.StateDigest != manifest.StateDigest ||
		restored.StateDigest.Hex() != state.StateDigest.Hex() ||
		len(restored.Routes) != 3 {
		t.Fatal("real parser global RIB checkpoint did not reconcile")
	}
}
