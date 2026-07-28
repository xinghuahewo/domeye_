package replay

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestGlobalAppendSpoolManifestHasDeterministicIdentity(t *testing.T) {
	artifact := Artifact{
		ArtifactID:      "art-test",
		ArtifactTimeUTC: "2026-02-28T15:00:00Z",
	}
	meta := SlotSpoolMeta{
		ArtifactIndex: 84,
		Artifact:      artifact,
	}
	first, err := newGlobalAppendSpoolManifest(artifact, meta)
	if err != nil {
		t.Fatal(err)
	}
	second, err := newGlobalAppendSpoolManifest(artifact, meta)
	if err != nil {
		t.Fatal(err)
	}
	firstJSON, err := json.Marshal(first)
	if err != nil {
		t.Fatal(err)
	}
	secondJSON, err := json.Marshal(second)
	if err != nil {
		t.Fatal(err)
	}
	if string(firstJSON) != string(secondJSON) ||
		first.SchemaVersion != "rrc25-global-append-spool/v2" ||
		first.DataThrough != "2026-02-28T15:05:00Z" ||
		strings.Contains(string(firstJSON), "created_at") {
		t.Fatalf("append spool identity is not deterministic: %s", firstJSON)
	}
}

func TestGlobalContinuationCheckpointRoundTripPreservesMutableState(
	t *testing.T,
) {
	mapping := newSyntheticGlobalCountryMapping(map[uint32]string{
		64500: "IR",
		64501: "US",
	})
	state, err := NewGlobalReplayState(mapping, 4)
	if err != nil {
		t.Fatal(err)
	}
	iran := globalRouteKey("192.0.2.1", 64496, "203.0.113.0/24")
	us := globalRouteKey("2001:db8::1", 64497, "2001:db8:1::/48")
	unknown := globalRouteKey("192.0.2.2", 64498, "198.51.100.0/24")
	dynamic := globalRouteKey("192.0.2.3", 64499, "198.51.101.0/24")
	if err := state.Seed(iran, true, 64500, 10, 1); err != nil {
		t.Fatal(err)
	}
	if err := state.Seed(us, true, 64501, 11, 2); err != nil {
		t.Fatal(err)
	}
	if err := state.Seed(unknown, false, 0, 12, 3); err != nil {
		t.Fatal(err)
	}
	for index := 1; index <= 200; index++ {
		key := globalRouteKey(
			fmt.Sprintf("192.0.2.%d", index),
			64496,
			fmt.Sprintf("10.0.%d.0/24", index),
		)
		if err := state.Seed(
			key, true, 64500, uint32(100+index), 1,
		); err != nil {
			t.Fatal(err)
		}
	}
	events := []ParsedEvent{
		{
			Key: iran, Action: actionAnnounce, OriginKnown: true,
			OriginASN: 64501, ArtifactIndex: 83,
			RecordOrdinal: 20, ElementOrdinal: 1,
			EventMicros: 1_772_377_199_000_000,
		},
		{
			Key: us, Action: actionWithdraw, ArtifactIndex: 83,
			RecordOrdinal: 21, ElementOrdinal: 1,
			EventMicros: 1_772_377_200_000_000,
		},
		{
			Key: dynamic, Action: actionAnnounce, OriginKnown: true,
			OriginASN: 64500, ArtifactIndex: 83,
			RecordOrdinal: 22, ElementOrdinal: 1,
			EventMicros: 1_772_377_201_000_000,
		},
	}
	for _, event := range events {
		if err := state.Apply(event, NewGlobalSlotActivity()); err != nil {
			t.Fatal(err)
		}
	}
	before, err := state.ValidateConservation()
	if err != nil {
		t.Fatal(err)
	}
	directory := filepath.Join(t.TempDir(), "checkpoint")
	manifest, checkpointSHA, err := WriteGlobalContinuationCheckpoint(
		directory,
		state,
		GlobalContinuationCheckpointManifest{
			RunID: "run-test", DatasetID: "dataset-test",
			Revision: GlobalDatasetRevision, DataThrough: WindowEndUTC,
			ProductSequence: 85, ProcessedSlot: 83,
			ProcessedUpdateCount:   84,
			PreviousProductSHA256:  strings.Repeat("a", 64),
			SourceCheckpointSHA256: strings.Repeat("b", 64),
			ShardCount:             3,
		},
	)
	if err != nil {
		t.Fatal(err)
	}
	if manifest.RecordCount != int64(len(state.Routes)) ||
		len(checkpointSHA) != 64 ||
		manifest.RestoreRequiresRIB ||
		manifest.RestoreRequiresPriorUpdate {
		t.Fatalf("unexpected continuation manifest: %+v", manifest)
	}
	repeatDirectory := filepath.Join(t.TempDir(), "checkpoint")
	repeatManifest, repeatSHA, err := WriteGlobalContinuationCheckpoint(
		repeatDirectory,
		state,
		GlobalContinuationCheckpointManifest{
			RunID: "run-test", DatasetID: "dataset-test",
			Revision: GlobalDatasetRevision, DataThrough: WindowEndUTC,
			ProductSequence: 85, ProcessedSlot: 83,
			ProcessedUpdateCount:   84,
			PreviousProductSHA256:  strings.Repeat("a", 64),
			SourceCheckpointSHA256: strings.Repeat("b", 64),
			ShardCount:             3,
		},
	)
	if err != nil {
		t.Fatal(err)
	}
	if checkpointSHA != repeatSHA ||
		!reflect.DeepEqual(manifest.Shards, repeatManifest.Shards) ||
		manifest.IdentityTime != WindowEndUTC ||
		manifest.CreatedAt != "" {
		t.Fatal("continuation checkpoint identity is not deterministic")
	}
	restored, loaded, loadedSHA, err := LoadGlobalContinuationCheckpoint(
		directory, mapping,
	)
	if err != nil {
		t.Fatal(err)
	}
	after, err := restored.ValidateConservation()
	if err != nil {
		t.Fatal(err)
	}
	if checkpointSHA != loadedSHA ||
		loaded.StateDigest != state.StateDigest.Hex() ||
		before != after ||
		!reflect.DeepEqual(state.Routes, restored.Routes) ||
		!reflect.DeepEqual(state.Counters, restored.Counters) ||
		!reflect.DeepEqual(state.Countries, restored.Countries) {
		t.Fatal("continuation checkpoint did not preserve complete RouteState")
	}
	if state.CohortID(1) != restored.CohortID(1) {
		t.Fatal("continuation restore changed the fixed cohort identity")
	}
}

func TestGlobalContinuationCheckpointRejectsCorruptShard(t *testing.T) {
	mapping := newSyntheticGlobalCountryMapping(map[uint32]string{64500: "IR"})
	state, _ := NewGlobalReplayState(mapping, 1)
	key := globalRouteKey("192.0.2.1", 64496, "203.0.113.0/24")
	if err := state.Seed(key, true, 64500, 1, 1); err != nil {
		t.Fatal(err)
	}
	directory := filepath.Join(t.TempDir(), "checkpoint")
	_, _, err := WriteGlobalContinuationCheckpoint(
		directory,
		state,
		GlobalContinuationCheckpointManifest{
			RunID: "run-test", DatasetID: "dataset-test",
			Revision: GlobalDatasetRevision, DataThrough: WindowEndUTC,
			ProductSequence: 85, ProcessedSlot: 83,
			ProcessedUpdateCount:   84,
			PreviousProductSHA256:  strings.Repeat("a", 64),
			SourceCheckpointSHA256: strings.Repeat("b", 64),
			ShardCount:             1,
		},
	)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(directory, "shard-000.bin.gz")
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_APPEND, 0)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := file.Write([]byte{0}); err != nil {
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	if _, _, _, err := LoadGlobalContinuationCheckpoint(
		directory, mapping,
	); err == nil {
		t.Fatal("corrupt continuation checkpoint was accepted")
	}
}
