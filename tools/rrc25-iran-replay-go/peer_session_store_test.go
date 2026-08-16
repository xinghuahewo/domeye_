package replay

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"net/netip"
	"strings"
	"testing"
	"time"
)

func syntheticStateChangeRecord(t *testing.T, mrtType, subtype uint16, peerASN, localASN uint32, afi uint16, oldState, newState uint16, tail []byte) (MRTRecordEvidence, Artifact) {
	t.Helper()
	timestamp := uint32(time.Date(2026, 2, 24, 0, 0, 15, 0, time.UTC).Unix())
	payload := make([]byte, 0)
	if mrtType == mrtBGP4MPET {
		raw := make([]byte, 4)
		binary.BigEndian.PutUint32(raw, 123456)
		payload = append(payload, raw...)
	}
	asnWidth := 2
	if subtype == 5 {
		asnWidth = 4
	}
	appendASN := func(value uint32) {
		raw := make([]byte, asnWidth)
		if asnWidth == 2 {
			binary.BigEndian.PutUint16(raw, uint16(value))
		} else {
			binary.BigEndian.PutUint32(raw, value)
		}
		payload = append(payload, raw...)
	}
	appendASN(peerASN)
	appendASN(localASN)
	raw16 := make([]byte, 2)
	binary.BigEndian.PutUint16(raw16, 7)
	payload = append(payload, raw16...)
	binary.BigEndian.PutUint16(raw16, afi)
	payload = append(payload, raw16...)
	if afi == 1 {
		payload = append(payload, []byte{192, 0, 2, 1}...)
		payload = append(payload, []byte{192, 0, 2, 2}...)
	} else {
		payload = append(payload, []byte{0x20, 0x01, 0x0d, 0xb8, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1}...)
		payload = append(payload, []byte{0x20, 0x01, 0x0d, 0xb8, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2}...)
	}
	binary.BigEndian.PutUint16(raw16, oldState)
	payload = append(payload, raw16...)
	binary.BigEndian.PutUint16(raw16, newState)
	payload = append(payload, raw16...)
	payload = append(payload, tail...)
	digest := sha256.Sum256(append([]byte("record"), payload...))
	fileSHA := strings.Repeat("a", 64)
	return MRTRecordEvidence{
			Timestamp: timestamp, MRTType: mrtType, Subtype: subtype, Payload: payload,
			UncompressedOffset: 123, RecordLength: uint32(12 + len(payload)),
			RecordSHA256: hex.EncodeToString(digest[:]),
		}, Artifact{
			ArtifactID: "art_v1_example", ArtifactTimeUTC: "2026-02-24T00:00:00Z",
			ArtifactType: "update", CollectorID: "rrc25", Compression: "gz",
			FileSHA256: fileSHA, RelativePath: "rrc25/2026.02/updates.20260224.0000.gz", SizeBytes: 1,
		}
}

func TestPeerSessionStateChangePreservesFullSessionIdentity(t *testing.T) {
	record, artifact := syntheticStateChangeRecord(t, mrtBGP4MPET, 5, 4_200_000_001, 4_200_000_002, 2, 6, 1, nil)
	row, selected, err := parsePeerSessionStateChange(record, artifact, 9)
	if err != nil || !selected {
		t.Fatalf("parse failed: selected=%v err=%v", selected, err)
	}
	if row.PeerASN != 4_200_000_001 || row.LocalASN != 4_200_000_002 || row.AFI != 2 ||
		row.PeerIP != "2001:db8::1" || row.LocalIP != "2001:db8::2" ||
		row.OldStateName != "established" || row.NewStateName != "idle" {
		t.Fatalf("unexpected row: %+v", row)
	}
	if row.PrefixWithdrawalInference != "not_permitted" || row.Semantics != "single_peer_session_transition" {
		t.Fatalf("semantic boundary drift: %+v", row)
	}
	if row.EventTimeUTC != "2026-02-24T00:00:15.123456Z" || row.RecordOrdinal != 9 || row.UncompressedOffset != 123 {
		t.Fatalf("raw/time coordinates lost: %+v", row)
	}
}

func TestPeerSessionStateChangeRejectsTailAndUnknownState(t *testing.T) {
	record, artifact := syntheticStateChangeRecord(t, mrtBGP4MP, 0, 64500, 64501, 1, 6, 1, []byte{0})
	if _, _, err := parsePeerSessionStateChange(record, artifact, 0); err == nil {
		t.Fatal("expected tail rejection")
	}
	record, artifact = syntheticStateChangeRecord(t, mrtBGP4MP, 0, 64500, 64501, 1, 6, 7, nil)
	if _, _, err := parsePeerSessionStateChange(record, artifact, 0); err == nil {
		t.Fatal("expected FSM rejection")
	}
}

func TestPeerSessionNonStateRecordIsNotMaterialized(t *testing.T) {
	record, artifact := syntheticStateChangeRecord(t, mrtBGP4MP, 1, 64500, 64501, 1, 6, 1, nil)
	row, selected, err := parsePeerSessionStateChange(record, artifact, 0)
	if err != nil || selected || row.ObservationID != "" {
		t.Fatalf("non-state record materialized: selected=%v row=%+v err=%v", selected, row, err)
	}
}

func TestPeerSessionIdentityDistinguishesSessionsButDirectionUsesPeerASN(t *testing.T) {
	first := peerSessionID(mustAddr("192.0.2.1"), 64500, 64496, mustAddr("192.0.2.2"), 0, 1)
	second := peerSessionID(mustAddr("192.0.2.3"), 64500, 64496, mustAddr("192.0.2.2"), 0, 1)
	if first != "session_v1_ad750e8c3d8a6c74cde1273a20282604" {
		t.Fatalf("session identity canonicalization drifted: %s", first)
	}
	if first == second {
		t.Fatal("two BGP sessions collapsed to one session identity")
	}
	if peerSessionObservationID(strings.Repeat("a", 64), 9) != "pso_v1_9adea27fb94f287db542af51aa02015d" {
		t.Fatal("peer-session observation identity canonicalization drifted")
	}
	if peerSessionObservationID(strings.Repeat("a", 64), 1) == peerSessionObservationID(strings.Repeat("a", 64), 2) {
		t.Fatal("raw record coordinates did not affect observation identity")
	}
}

func mustAddr(value string) netip.Addr {
	return netip.MustParseAddr(value)
}
