package replay

import (
	"bufio"
	"compress/gzip"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func syntheticSlotSpool(
	t *testing.T,
	root string,
	index, shardCount int,
	events []ParsedEvent,
) SlotSpoolMeta {
	t.Helper()
	shards := make([]ShardSpoolMeta, 0, shardCount)
	for shard := 0; shard < shardCount; shard++ {
		relative := filepath.ToSlash(filepath.Join(
			"spool", fmt.Sprintf("%03d", index), fmt.Sprintf("shard-%03d.bin", shard),
		))
		path := filepath.Join(root, filepath.FromSlash(relative))
		if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
			t.Fatal(err)
		}
		writer, err := newSpoolWriter(path)
		if err != nil {
			t.Fatal(err)
		}
		for _, event := range events {
			event.ArtifactIndex = uint16(index)
			if shardFor(event.Key, shardCount) == shard {
				if err := writer.Write(event); err != nil {
					t.Fatal(err)
				}
			}
		}
		meta, err := writer.Close(shard, relative)
		if err != nil {
			t.Fatal(err)
		}
		shards = append(shards, meta)
	}
	return SlotSpoolMeta{
		SchemaVersion: "rrc25-update-shard-spool/v1",
		EngineVersion: EngineVersion, ArtifactIndex: index,
		Stats:  UpdateParseStats{UnknownOptional: make(map[uint8]int64)},
		Shards: shards,
	}
}

func countGzipLines(t *testing.T, path string) int {
	t.Helper()
	file, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	decoded, err := gzip.NewReader(file)
	if err != nil {
		t.Fatal(err)
	}
	defer decoded.Close()
	scanner := bufio.NewScanner(decoded)
	count := 0
	for scanner.Scan() {
		count++
	}
	if err := scanner.Err(); err != nil {
		t.Fatal(err)
	}
	return count
}

func TestSynthetic84SlotReplayCheckpointResumeAndPackageClosure(t *testing.T) {
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "checkpoints"), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := writeJSONAtomic(filepath.Join(root, "RUNNING.json"), map[string]any{
		"status": "running",
	}); err != nil {
		t.Fatal(err)
	}
	key := testKey("192.0.2.1", 64510, "203.0.113.0/24")
	mapping := &CountryMapping{
		byASN:          map[uint32]Membership{64500: MembershipIR},
		MappingVersion: "synthetic-mapping",
	}
	state, err := NewReplayState(mapping, map[RouteKey]uint32{key: 64500})
	if err != nil {
		t.Fatal(err)
	}
	metas := make([]SlotSpoolMeta, 84)
	for index := range metas {
		var events []ParsedEvent
		switch index {
		case 25:
			events = []ParsedEvent{{Key: key, Action: actionWithdraw}}
		case 35:
			events = []ParsedEvent{{
				Key: key, Action: actionAnnounce, OriginKnown: true, OriginASN: 64500,
			}}
		}
		metas[index] = syntheticSlotSpool(t, root, index, 4, events)
	}
	if err := WriteCheckpoint(
		filepath.Join(root, "checkpoints", "rib.json.gz"), state, "rib", -1,
	); err != nil {
		t.Fatal(err)
	}
	for index := 0; index < 25; index++ {
		if err := state.ApplySlot(root, metas[index]); err != nil {
			t.Fatal(err)
		}
		slot := catchUpStart.Add(time.Duration(index) * 5 * time.Minute)
		state.CatchUpMetrics = append(state.CatchUpMetrics, state.Snapshot(
			slot.Add(5*time.Minute).Format(time.RFC3339),
			slot.Format(time.RFC3339), slot.Add(5*time.Minute).Format(time.RFC3339),
			"catch_up_slot_end", UpdateCounts{},
		))
		if err := WriteCheckpoint(
			filepath.Join(root, "checkpoints", fmt.Sprintf("catch-up-%03d.json.gz", index+1)),
			state, "catch-up", index,
		); err != nil {
			t.Fatal(err)
		}
	}
	state.FormalObservations = append(state.FormalObservations, state.Snapshot(
		WindowStartUTC, WindowStartUTC, WindowStartUTC, "window_start", UpdateCounts{},
	))
	if err := WriteCheckpoint(
		filepath.Join(root, "checkpoints", "formal-000.json.gz"), state, "formal", -1,
	); err != nil {
		t.Fatal(err)
	}
	for index := 0; index < 59; index++ {
		if err := state.ApplySlot(root, metas[index+25]); err != nil {
			t.Fatal(err)
		}
		slot := windowStart.Add(time.Duration(index) * 5 * time.Minute)
		state.FormalObservations = append(state.FormalObservations, state.Snapshot(
			slot.Add(5*time.Minute).Format(time.RFC3339),
			slot.Format(time.RFC3339), slot.Add(5*time.Minute).Format(time.RFC3339),
			"slot_end", UpdateCounts{},
		))
		checkpointPath := filepath.Join(
			root, "checkpoints", fmt.Sprintf("formal-%03d.json.gz", index+1),
		)
		if err := WriteCheckpoint(checkpointPath, state, "formal", index); err != nil {
			t.Fatal(err)
		}
		if index == 20 {
			restored, checkpoint, err := LoadCheckpoint(checkpointPath, mapping)
			if err != nil {
				t.Fatal(err)
			}
			if checkpoint.ProcessedSlot != 20 {
				t.Fatal("resume coordinate mismatch")
			}
			state = restored
		}
	}
	if len(state.CatchUpMetrics) != 25 || len(state.FormalObservations) != 60 ||
		state.FormalObservations[59].ObservedAt != WindowEndUTC {
		t.Fatal("25+60 observation closure failed")
	}
	formalCheckpoints, err := filepath.Glob(filepath.Join(root, "checkpoints", "formal-*.json.gz"))
	if err != nil || len(formalCheckpoints) != 60 {
		t.Fatalf("expected 60 formal checkpoints, got %d", len(formalCheckpoints))
	}
	incident := DeriveIncident(state.FormalObservations, BuildNormalBand(state.CatchUpMetrics))
	inputs := FixedInputs{}
	quality := Quality{
		SchemaVersion:              "rrc25-iran-go-quality/v1",
		EngineVersion:              EngineVersion,
		UpdateOptionalUnknownAttrs: make(map[uint8]int64),
	}
	if err := finalizePackage(
		Config{Output: root, Workers: 4, Shards: 4},
		inputs, mapping, metas, state, quality, incident,
	); err != nil {
		t.Fatal(err)
	}
	if count := countGzipLines(t, filepath.Join(root, "country-snapshots.jsonl.gz")); count != 60 {
		t.Fatalf("expected 60 country rows, got %d", count)
	}
	var complete map[string]any
	raw, err := os.ReadFile(filepath.Join(root, "COMPLETE.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &complete); err != nil {
		t.Fatal(err)
	}
	if complete["status"] != "complete" {
		t.Fatalf("package did not close: %+v", complete)
	}
	if _, err := os.Stat(filepath.Join(root, "RUNNING.json")); !os.IsNotExist(err) {
		t.Fatal("RUNNING marker must be removed only after completion")
	}
}

func TestCancelledParallelParseFailsWithoutPublishingManifest(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	root := t.TempDir()
	_, err := ParseAllUpdates(ctx, root, root, []Artifact{{
		ArtifactID:      "art_v1_00000000000000000000000000000000",
		ArtifactTimeUTC: "2026-02-28T08:00:00Z",
		ArtifactType:    "update", CollectorID: "rrc25", Compression: "gz",
		FileSHA256: string(make([]byte, 64)), RelativePath: "rrc25/absent.gz",
		SizeBytes: 1,
	}}, 1, 1, nil)
	if err == nil {
		t.Fatal("cancelled parse must fail")
	}
	if _, statErr := os.Stat(filepath.Join(root, "spool", "manifest.json")); !os.IsNotExist(statErr) {
		t.Fatal("failed parse must not publish manifest")
	}
}
