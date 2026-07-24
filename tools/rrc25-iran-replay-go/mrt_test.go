package replay

import (
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"net/netip"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func mrtRecord(timestamp uint32, mrtType, subtype uint16, payload []byte) []byte {
	result := make([]byte, 12+len(payload))
	binary.BigEndian.PutUint32(result[0:4], timestamp)
	binary.BigEndian.PutUint16(result[4:6], mrtType)
	binary.BigEndian.PutUint16(result[6:8], subtype)
	binary.BigEndian.PutUint32(result[8:12], uint32(len(payload)))
	copy(result[12:], payload)
	return result
}

func attribute(flags, attributeType uint8, value []byte) []byte {
	result := []byte{flags, attributeType, uint8(len(value))}
	return append(result, value...)
}

func asPath(origin uint32, width int) []byte {
	result := []byte{2, 1}
	if width == 2 {
		raw := make([]byte, 2)
		binary.BigEndian.PutUint16(raw, uint16(origin))
		return append(result, raw...)
	}
	raw := make([]byte, 4)
	binary.BigEndian.PutUint32(raw, origin)
	return append(result, raw...)
}

func peerIndexFixture() []byte {
	payload := bytes.NewBuffer(nil)
	_ = binary.Write(payload, binary.BigEndian, uint32(0x01020304))
	_ = binary.Write(payload, binary.BigEndian, uint16(0))
	_ = binary.Write(payload, binary.BigEndian, uint16(1))
	payload.WriteByte(0x02)
	_ = binary.Write(payload, binary.BigEndian, uint32(0x05060708))
	payload.Write(netip.MustParseAddr("192.0.2.10").AsSlice())
	_ = binary.Write(payload, binary.BigEndian, uint32(64500))
	return mrtRecord(uint32(catchUpStart.Unix()), mrtTableDumpV2, peerIndexTable, payload.Bytes())
}

func ribFixtureWithExtra(prefix netip.Prefix, origin uint32, extra []byte) []byte {
	attributes := bytes.Join([][]byte{
		attribute(0x40, 2, asPath(origin, 4)),
		extra,
	}, nil)
	payload := bytes.NewBuffer(nil)
	_ = binary.Write(payload, binary.BigEndian, uint32(1))
	payload.WriteByte(uint8(prefix.Bits()))
	address := prefix.Addr().AsSlice()
	payload.Write(address[:(prefix.Bits()+7)/8])
	_ = binary.Write(payload, binary.BigEndian, uint16(1))
	_ = binary.Write(payload, binary.BigEndian, uint16(0))
	_ = binary.Write(payload, binary.BigEndian, uint32(catchUpStart.Unix()))
	_ = binary.Write(payload, binary.BigEndian, uint16(len(attributes)))
	payload.Write(attributes)
	subtype := uint16(ribIPv4Unicast)
	if prefix.Addr().Is6() {
		subtype = ribIPv6Unicast
	}
	return mrtRecord(uint32(catchUpStart.Unix()), mrtTableDumpV2, subtype, payload.Bytes())
}

func ribFixture(prefix netip.Prefix, origin uint32) []byte {
	return ribFixtureWithExtra(prefix, origin, nil)
}

func writeGzipArtifact(t *testing.T, rawRoot, relative string, content []byte, artifactTime, artifactType string) Artifact {
	t.Helper()
	path := filepath.Join(rawRoot, filepath.FromSlash(relative))
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		t.Fatal(err)
	}
	file, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	compressed := gzip.NewWriter(file)
	if _, err := compressed.Write(content); err != nil {
		t.Fatal(err)
	}
	if err := compressed.Close(); err != nil {
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(raw)
	hash := hex.EncodeToString(digest[:])
	return Artifact{
		ArtifactID: "art_v1_" + hash[:32], ArtifactTimeUTC: artifactTime,
		ArtifactType: artifactType, CollectorID: "rrc25", Compression: "gz",
		FileSHA256: hash, RelativePath: relative, SizeBytes: int64(len(raw)),
	}
}

func TestSeedRIBRetainsOnlyDefiniteIRState(t *testing.T) {
	rawRoot := t.TempDir()
	stream := bytes.Join([][]byte{
		peerIndexFixture(),
		ribFixtureWithExtra(
			netip.MustParsePrefix("203.0.113.0/24"),
			64500,
			attribute(0x80, 14, []byte{0x10, 0x2a}),
		),
		ribFixture(netip.MustParsePrefix("198.51.100.0/24"), 64496),
		ribFixture(netip.MustParsePrefix("192.0.2.0/24"), 64497),
	}, nil)
	artifact := writeGzipArtifact(
		t, rawRoot, "rrc25/test-rib.gz", stream, CatchUpStartUTC, "rib",
	)
	mapping := &CountryMapping{
		byASN: map[uint32]Membership{
			64500: MembershipIR, 64496: MembershipOther,
		},
		MappingVersion: "test",
	}
	baseline, quality, err := SeedRIB(rawRoot, artifact, mapping)
	if err != nil {
		t.Fatal(err)
	}
	if len(baseline) != 1 {
		t.Fatalf("expected one retained member, got %d", len(baseline))
	}
	for key, origin := range baseline {
		if key.Prefix.String() != "203.0.113.0/24" || origin != 64500 {
			t.Fatalf("unexpected retained route: %+v -> %d", key, origin)
		}
	}
	if quality.RIBEntries != 3 || quality.RIBExplicitNonIR != 1 ||
		quality.RIBMappingUnknown != 1 || quality.RIBRetainedMembers != 1 {
		t.Fatalf("unexpected quality: %+v", quality)
	}
}

func updateFixture(timestamp time.Time, subtype uint16, origin uint32, extra []byte) []byte {
	width := 4
	if subtype == 1 {
		width = 2
	}
	attributes := bytes.Join([][]byte{
		attribute(0x40, 1, []byte{0}),
		attribute(0x40, 2, asPath(origin, width)),
		extra,
	}, nil)
	nlri := []byte{24, 203, 0, 113}
	body := bytes.NewBuffer(nil)
	_ = binary.Write(body, binary.BigEndian, uint16(0))
	_ = binary.Write(body, binary.BigEndian, uint16(len(attributes)))
	body.Write(attributes)
	body.Write(nlri)
	message := bytes.NewBuffer(nil)
	message.Write(bytes.Repeat([]byte{0xff}, 16))
	_ = binary.Write(message, binary.BigEndian, uint16(19+body.Len()))
	message.WriteByte(2)
	message.Write(body.Bytes())
	payload := bytes.NewBuffer(nil)
	if width == 2 {
		_ = binary.Write(payload, binary.BigEndian, uint16(64500))
		_ = binary.Write(payload, binary.BigEndian, uint16(64496))
	} else {
		_ = binary.Write(payload, binary.BigEndian, uint32(64500))
		_ = binary.Write(payload, binary.BigEndian, uint32(64496))
	}
	_ = binary.Write(payload, binary.BigEndian, uint16(0))
	_ = binary.Write(payload, binary.BigEndian, uint16(1))
	payload.Write(netip.MustParseAddr("192.0.2.10").AsSlice())
	payload.Write(netip.MustParseAddr("192.0.2.1").AsSlice())
	payload.Write(message.Bytes())
	return mrtRecord(uint32(timestamp.Unix()), mrtBGP4MP, subtype, payload.Bytes())
}

func TestUpdateAcceptsStrictASPathLimitAndBothASNWidths(t *testing.T) {
	for _, subtype := range []uint16{1, 4} {
		t.Run(string(rune('0'+subtype)), func(t *testing.T) {
			rawRoot := t.TempDir()
			slot := mustUTC("2026-02-28T09:25:00Z")
			pathLimit := attribute(0xe0, 21, []byte{20, 0, 0, 0xfd, 0x63})
			artifact := writeGzipArtifact(
				t, rawRoot, "rrc25/test-update.gz",
				updateFixture(slot.Add(time.Second), subtype, 64500, pathLimit),
				slot.Format(time.RFC3339), "update",
			)
			var events []ParsedEvent
			stats, err := ParseUpdate(rawRoot, artifact, 17, func(event ParsedEvent) error {
				events = append(events, event)
				return nil
			})
			if err != nil {
				t.Fatal(err)
			}
			if len(events) != 1 || events[0].OriginASN != 64500 ||
				events[0].Key.Prefix.String() != "203.0.113.0/24" {
				t.Fatalf("unexpected events: %+v", events)
			}
			if stats.RouteEvents != 1 || stats.Announces != 1 {
				t.Fatalf("unexpected stats: %+v", stats)
			}
		})
	}
}

func TestUpdateRejectsMalformedASPathLimit(t *testing.T) {
	rawRoot := t.TempDir()
	slot := mustUTC("2026-02-28T09:25:00Z")
	bad := attribute(0xe0, 21, []byte{20, 0, 0, 0xfd})
	artifact := writeGzipArtifact(
		t, rawRoot, "rrc25/test-update.gz",
		updateFixture(slot.Add(time.Second), 4, 64500, bad),
		slot.Format(time.RFC3339), "update",
	)
	if _, err := ParseUpdate(rawRoot, artifact, 0, func(ParsedEvent) error { return nil }); err == nil {
		t.Fatal("expected malformed AS_PATHLIMIT failure")
	}
}
