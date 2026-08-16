package replay

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"
)

func keyInDifferentShard(
	t *testing.T,
	first RouteKey,
	shardCount int,
) RouteKey {
	t.Helper()
	for suffix := 2; suffix < 250; suffix++ {
		candidate := globalRouteKey(
			fmt.Sprintf("192.0.2.%d", suffix), 64510,
			"198.51.100.0/24",
		)
		if shardFor(candidate, shardCount) != shardFor(first, shardCount) {
			return candidate
		}
	}
	t.Fatal("could not find route key in another shard")
	return RouteKey{}
}

func TestApplyGlobalSpoolSlotUsesMergedRecordOrderAndClosesPopulation(
	t *testing.T,
) {
	mapping := newSyntheticGlobalCountryMapping(map[uint32]string{
		64500: "IR",
		64501: "US",
	})
	state, err := NewGlobalReplayState(mapping, 2)
	if err != nil {
		t.Fatal(err)
	}
	first := globalRouteKey("192.0.2.1", 64510, "203.0.113.0/24")
	second := keyInDifferentShard(t, first, 2)
	if err := state.Seed(first, true, 64500, 1, 1); err != nil {
		t.Fatal(err)
	}
	if err := state.Seed(second, true, 64500, 2, 1); err != nil {
		t.Fatal(err)
	}
	events := []ParsedEvent{
		{
			Key: first, Action: actionAnnounce,
			OriginKnown: true, OriginASN: 64500,
			RecordOrdinal: 10, ElementOrdinal: 1,
		},
		{
			Key: second, Action: actionAnnounce,
			OriginKnown: true, OriginASN: 64501,
			RecordOrdinal: 11, ElementOrdinal: 1,
		},
		{
			Key: first, Action: actionWithdraw,
			RecordOrdinal: 12, ElementOrdinal: 1,
		},
		{
			Key: second, Action: actionWithdraw,
			RecordOrdinal: 13, ElementOrdinal: 1,
		},
	}
	root := t.TempDir()
	meta := syntheticSlotSpool(t, root, 0, 2, events)
	meta.Stats.RouteEvents = int64(len(events))
	meta.Stats.Announces = 2
	meta.Stats.Withdraws = 2
	activity, err := ApplyGlobalSpoolSlot(state, root, meta)
	if err != nil {
		t.Fatal(err)
	}
	if activity.Global.Announce != 2 || activity.Global.Withdraw != 2 ||
		activity.DuplicateAnnounces != 1 ||
		activity.ReplacementAnnounces != 1 ||
		activity.CountryMigrations != 1 ||
		activity.WithdrawWithoutState != 0 {
		t.Fatalf("unexpected activity: %+v", activity)
	}
	conservation, err := state.ValidateConservation()
	if err != nil {
		t.Fatal(err)
	}
	if conservation.GlobalBaselinePrefixVP != 2 ||
		conservation.GlobalVisiblePrefixVP != 0 ||
		conservation.GlobalCurrentPrefixVP != 0 {
		t.Fatalf("unexpected conservation: %+v", conservation)
	}
}

func TestApplyGlobalSpoolSlotRejectsDuplicateGlobalCoordinates(
	t *testing.T,
) {
	mapping := newSyntheticGlobalCountryMapping(map[uint32]string{
		64500: "IR",
	})
	state, _ := NewGlobalReplayState(mapping, 2)
	first := globalRouteKey("192.0.2.1", 64510, "203.0.113.0/24")
	second := keyInDifferentShard(t, first, 2)
	events := []ParsedEvent{
		{
			Key: first, Action: actionAnnounce,
			OriginKnown: true, OriginASN: 64500,
			RecordOrdinal: 10, ElementOrdinal: 1,
		},
		{
			Key: second, Action: actionAnnounce,
			OriginKnown: true, OriginASN: 64500,
			RecordOrdinal: 10, ElementOrdinal: 1,
		},
	}
	root := t.TempDir()
	meta := syntheticSlotSpool(t, root, 0, 2, events)
	meta.Stats.RouteEvents = 2
	meta.Stats.Announces = 2
	if _, err := ApplyGlobalSpoolSlot(state, root, meta); err == nil {
		t.Fatal("duplicate global coordinates must be rejected")
	}
}

func TestGlobalSpoolPreservesIPv4MappedIPv6PrefixFamily(t *testing.T) {
	mapping := newSyntheticGlobalCountryMapping(map[uint32]string{
		64500: "IR",
	})
	state, _ := NewGlobalReplayState(mapping, 1)
	key := globalRouteKey(
		"2001:db8::1", 64510, "::ffff:5c2f:90c0/127",
	)
	event := ParsedEvent{
		Key: key, Action: actionAnnounce,
		OriginKnown: true, OriginASN: 64500,
		RecordOrdinal: 1, ElementOrdinal: 1,
	}
	root := t.TempDir()
	meta := syntheticSlotSpool(t, root, 0, 1, []ParsedEvent{event})
	meta.Stats.RouteEvents = 1
	meta.Stats.Announces = 1
	if _, err := ApplyGlobalSpoolSlot(state, root, meta); err != nil {
		t.Fatal(err)
	}
	if _, exists := state.Routes[key]; !exists {
		t.Fatal("IPv4-mapped IPv6 prefix changed family during spool round trip")
	}
}

func TestLoadGlobalSpoolManifestBindsArtifactsAndHash(t *testing.T) {
	root := t.TempDir()
	artifact := Artifact{
		ArtifactID:      "art_v1_0123456789abcdef0123456789abcdef",
		ArtifactTimeUTC: "2026-02-28T08:00:00Z",
		ArtifactType:    "update", CollectorID: "rrc25",
		Compression: "gz", FileSHA256: string(make([]byte, 64)),
		RelativePath: "rrc25/test.gz", SizeBytes: 1,
	}
	meta := syntheticSlotSpool(t, root, 0, 2, nil)
	meta.Artifact = artifact
	manifest := GlobalSpoolManifest{
		SchemaVersion: "rrc25-update-spool-manifest/v1",
		EngineVersion: EngineVersion,
		WorkerCount:   1,
		ShardCount:    2,
		Slots:         []SlotSpoolMeta{meta},
	}
	raw, err := json.MarshalIndent(manifest, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	raw = append(raw, '\n')
	path := filepath.Join(root, "spool", "manifest.json")
	if err := os.WriteFile(path, raw, 0o640); err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(raw)
	expected := hex.EncodeToString(digest[:])
	inputs := FixedInputs{AllUpdate: []Artifact{artifact}}
	loaded, actual, err := LoadGlobalSpoolManifest(
		root, inputs, expected,
	)
	if err != nil {
		t.Fatal(err)
	}
	if actual != expected || len(loaded.Slots) != 1 {
		t.Fatal("spool manifest identity was not retained")
	}
	if _, _, err := LoadGlobalSpoolManifest(
		root, inputs, string(make([]byte, 64)),
	); err == nil {
		t.Fatal("wrong spool manifest hash must be rejected")
	}
}

func TestGlobalProductIsImmutableAndDeterministic(t *testing.T) {
	path := filepath.Join(t.TempDir(), "formal-000.json.gz")
	product := GlobalSlotProduct{
		SchemaVersion: "rrc25-global-slot-product/v1",
		EngineVersion: GlobalEngineVersion,
		RunID:         "run", DatasetID: "dataset",
		Revision: GlobalDatasetRevision, CollectorID: "rrc25",
		Phase: "formal", ProductSequence: 26,
		ObservedAt: WindowStartUTC, DataThrough: WindowStartUTC,
		SlotStartUTC: WindowStartUTC, SlotEndExclusiveUTC: WindowStartUTC,
		SlotRole: "window_start", ASNStatesIncluded: true,
		Activity: GlobalActivityReport{
			ByCountry: map[string]UpdateCounts{},
		},
		Countries: []GlobalCountryObservation{},
		ASNStates: []GlobalASNStateRow{},
	}
	first, err := writeGlobalProductImmutable(path, product)
	if err != nil {
		t.Fatal(err)
	}
	second, err := writeGlobalProductImmutable(path, product)
	if err != nil {
		t.Fatal(err)
	}
	if first != second {
		t.Fatal("same product did not preserve the same compressed hash")
	}
	product.RunID = "different"
	if _, err := writeGlobalProductImmutable(path, product); err == nil {
		t.Fatal("immutable product overwrite must be rejected")
	}
}
