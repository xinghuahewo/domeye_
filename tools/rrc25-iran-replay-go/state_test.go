package replay

import (
	"net/netip"
	"os"
	"path/filepath"
	"testing"
)

func testKey(peer string, asn uint32, prefix string) RouteKey {
	return RouteKey{
		PeerIP: netip.MustParseAddr(peer), PeerASN: asn, AFI: 4,
		Prefix: netip.MustParsePrefix(prefix),
	}
}

func TestIncrementalVisibilityAndDynamicState(t *testing.T) {
	first := testKey("192.0.2.1", 64510, "203.0.113.0/24")
	second := testKey("192.0.2.2", 64511, "203.0.113.0/24")
	mapping := &CountryMapping{
		byASN: map[uint32]Membership{
			64500: MembershipIR, 65000: MembershipIR, 64496: MembershipOther,
		},
		MappingVersion: "mapping-test",
	}
	state, err := NewReplayState(mapping, map[RouteKey]uint32{
		first: 64500, second: 64500,
	})
	if err != nil {
		t.Fatal(err)
	}
	state.Apply(ParsedEvent{Key: first, Action: actionWithdraw})
	partial := state.Snapshot(
		WindowStartUTC, WindowStartUTC, WindowStartUTC, "window_start", UpdateCounts{},
	)
	if partial.VisiblePrefixVPCount != 1 || partial.AffectedASNCount != 1 ||
		len(partial.DualClassifications["partially_visible"]) != 1 {
		t.Fatalf("unexpected partial snapshot: %+v", partial)
	}
	state.Apply(ParsedEvent{Key: second, Action: actionWithdraw})
	invisible := state.Snapshot(
		WindowStartUTC, WindowStartUTC, WindowStartUTC, "window_start", UpdateCounts{},
	)
	if invisible.VisiblePrefixVPCount != 0 ||
		len(invisible.DualClassifications["fully_invisible"]) != 1 {
		t.Fatalf("unexpected invisible snapshot: %+v", invisible)
	}
	state.Apply(ParsedEvent{
		Key: first, Action: actionAnnounce, OriginKnown: true, OriginASN: 64500,
	})
	dynamicKey := testKey("192.0.2.3", 64512, "198.51.100.0/24")
	state.Apply(ParsedEvent{
		Key: dynamicKey, Action: actionAnnounce, OriginKnown: true, OriginASN: 65000,
	})
	if len(state.Snapshot(
		WindowStartUTC, WindowStartUTC, WindowStartUTC, "window_start", UpdateCounts{},
	).DynamicIRASNs) != 1 {
		t.Fatal("dynamic IR ASN not reported")
	}
	state.Apply(ParsedEvent{
		Key: dynamicKey, Action: actionAnnounce, OriginKnown: true, OriginASN: 64496,
	})
	if _, exists := state.Routes[dynamicKey]; exists {
		t.Fatal("dynamic key must be removed after non-IR replacement")
	}
}

func TestCheckpointRestoresAndReconcilesIncrementalCounters(t *testing.T) {
	key := testKey("192.0.2.1", 64510, "203.0.113.0/24")
	mapping := &CountryMapping{
		byASN:          map[uint32]Membership{64500: MembershipIR},
		MappingVersion: "mapping-checkpoint",
	}
	state, err := NewReplayState(mapping, map[RouteKey]uint32{key: 64500})
	if err != nil {
		t.Fatal(err)
	}
	state.Apply(ParsedEvent{Key: key, Action: actionWithdraw})
	state.CatchUpMetrics = append(state.CatchUpMetrics, state.Snapshot(
		"2026-02-28T08:05:00Z", "2026-02-28T08:00:00Z",
		"2026-02-28T08:05:00Z", "catch_up_slot_end", UpdateCounts{},
	))
	path := filepath.Join(t.TempDir(), "checkpoint.json.gz")
	if err := WriteCheckpoint(path, state, "catch-up", 0); err != nil {
		t.Fatal(err)
	}
	restored, checkpoint, err := LoadCheckpoint(path, mapping)
	if err != nil {
		t.Fatal(err)
	}
	if checkpoint.ProcessedSlot != 0 || restored.VisiblePrefixVP != 0 ||
		len(restored.CatchUpMetrics) != 1 {
		t.Fatalf("checkpoint did not round trip: %+v", checkpoint)
	}
}

func TestShardSpoolAppliesInDeterministicShardOrder(t *testing.T) {
	key := testKey("192.0.2.1", 64510, "203.0.113.0/24")
	mapping := &CountryMapping{
		byASN:          map[uint32]Membership{64500: MembershipIR},
		MappingVersion: "mapping-shard",
	}
	state, _ := NewReplayState(mapping, map[RouteKey]uint32{key: 64500})
	root := t.TempDir()
	shardCount := 2
	shards := make([]ShardSpoolMeta, 0, shardCount)
	for shard := 0; shard < shardCount; shard++ {
		relative := filepath.ToSlash(filepath.Join("spool", "000", "shard.bin."+string(rune('0'+shard))))
		path := filepath.Join(root, filepath.FromSlash(relative))
		if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
			t.Fatal(err)
		}
		writer, err := newSpoolWriter(path)
		if err != nil {
			t.Fatal(err)
		}
		if shardFor(key, shardCount) == shard {
			if err := writer.Write(ParsedEvent{Key: key, Action: actionWithdraw}); err != nil {
				t.Fatal(err)
			}
			if err := writer.Write(ParsedEvent{
				Key: key, Action: actionAnnounce, OriginKnown: true, OriginASN: 64500,
			}); err != nil {
				t.Fatal(err)
			}
		}
		meta, err := writer.Close(shard, relative)
		if err != nil {
			t.Fatal(err)
		}
		shards = append(shards, meta)
	}
	meta := SlotSpoolMeta{ArtifactIndex: 0, Shards: shards}
	if err := state.ApplySlot(root, meta); err != nil {
		t.Fatal(err)
	}
	if state.VisiblePrefixVP != 1 {
		t.Fatal("same-key order was not preserved inside shard")
	}
}
