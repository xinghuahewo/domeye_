package replay

import (
	"bufio"
	"compress/gzip"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"io"
	"net/netip"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"sync"
	"time"
)

// LoadSelectedGlobalRIBCheckpoint 校验完整 RIB checkpoint，但只把指定国家的
// 固定 cohort 装入内存。这样事件发布投影不需要再次持有五千余万条全球路由。
func LoadSelectedGlobalRIBCheckpoint(
	checkpointRoot string,
	mapping *GlobalCountryMapping,
	expectedArtifact Artifact,
	countryCodes map[string]struct{},
) (*GlobalReplayState, GlobalRIBCheckpointManifest, error) {
	root := filepath.Join(checkpointRoot, "rib")
	var manifest GlobalRIBCheckpointManifest
	if _, err := readJSON(filepath.Join(root, "manifest.json"), &manifest); err != nil {
		return nil, manifest, err
	}
	if manifest.SchemaVersion != "rrc25-global-rib-checkpoint/v1" ||
		manifest.EngineVersion != GlobalEngineVersion ||
		manifest.MappingVersion != mapping.MappingVersion ||
		manifest.InputArtifact.FileSHA256 != expectedArtifact.FileSHA256 ||
		manifest.InputArtifact.SizeBytes != expectedArtifact.SizeBytes ||
		manifest.ShardCount < 1 || len(manifest.Shards) != manifest.ShardCount ||
		len(countryCodes) == 0 {
		return nil, manifest, fmt.Errorf("selected global RIB checkpoint identity mismatch")
	}

	selectedIDs := make(map[uint16]struct{}, len(countryCodes))
	capacity := int64(0)
	manifestCountries := make(map[string]GlobalCheckpointCountry)
	for _, country := range manifest.Countries {
		manifestCountries[country.CountryCode] = country
	}
	for code := range countryCodes {
		id, exists := mapping.IDForCode(code)
		country, present := manifestCountries[code]
		if !exists || !present || country.BaselinePrefixVP <= 0 {
			return nil, manifest, fmt.Errorf("selected country %s is absent from RIB checkpoint", code)
		}
		selectedIDs[id] = struct{}{}
		capacity += country.BaselinePrefixVP
	}
	state, err := NewGlobalReplayState(mapping, int(capacity))
	if err != nil {
		return nil, manifest, err
	}
	state.SeedObservedAt = manifest.SeedObservedAt
	parsedSeed, err := time.Parse(time.RFC3339, manifest.SeedObservedAt)
	if err != nil {
		return nil, manifest, fmt.Errorf("global RIB seed time is invalid")
	}
	state.SeedEventMicros = parsedSeed.UnixMicro()

	shards := append([]GlobalCheckpointShard(nil), manifest.Shards...)
	sort.Slice(shards, func(i, j int) bool { return shards[i].Shard < shards[j].Shard })
	globalRecordCount := int64(0)
	for _, shard := range shards {
		if shard.Shard < 0 || shard.Shard >= manifest.ShardCount {
			return nil, manifest, fmt.Errorf("global RIB checkpoint shard coordinate mismatch")
		}
		expectedRelative := filepath.ToSlash(filepath.Join(
			"checkpoints", "rib", fmt.Sprintf("shard-%03d.bin.gz", shard.Shard),
		))
		if shard.Path != expectedRelative {
			return nil, manifest, fmt.Errorf("global RIB checkpoint shard path mismatch")
		}
		count, err := readSelectedGlobalRIBShard(
			filepath.Join(root, fmt.Sprintf("shard-%03d.bin.gz", shard.Shard)),
			shard, manifest.ShardCount, state, selectedIDs,
		)
		if err != nil {
			return nil, manifest, fmt.Errorf(
				"load selected global RIB checkpoint shard %d: %w", shard.Shard, err,
			)
		}
		globalRecordCount += count
	}
	if globalRecordCount != manifest.RecordCount {
		return nil, manifest, fmt.Errorf("global RIB checkpoint record population mismatch")
	}
	if _, err := state.ValidateConservation(); err != nil {
		return nil, manifest, err
	}
	for code := range countryCodes {
		id, _ := mapping.IDForCode(code)
		actual := state.country(id)
		expected := manifestCountries[code]
		if actual.BaselinePrefixVP != expected.BaselinePrefixVP ||
			actual.BaselineByAFI[0] != expected.BaselineIPv4 ||
			actual.BaselineByAFI[1] != expected.BaselineIPv6 ||
			actual.CohortDigest.Hex() != expected.MembershipDigest {
			return nil, manifest, fmt.Errorf("selected country %s RIB population mismatch", code)
		}
	}
	return state, manifest, nil
}

func readSelectedGlobalRIBShard(
	path string,
	meta GlobalCheckpointShard,
	shardCount int,
	state *GlobalReplayState,
	selectedIDs map[uint16]struct{},
) (int64, error) {
	file, err := os.Open(path)
	if err != nil {
		return 0, err
	}
	defer file.Close()
	hash := sha256.New()
	counting := &sha256Writer{hash: hash}
	tee := io.TeeReader(file, counting)
	compressed, err := gzip.NewReader(tee)
	if err != nil {
		return 0, err
	}
	reader := bufio.NewReaderSize(compressed, 1<<20)
	magic := make([]byte, len(globalRIBMagic))
	if _, err := io.ReadFull(reader, magic); err != nil {
		_ = compressed.Close()
		return 0, err
	}
	if string(magic) != string(globalRIBMagic[:]) {
		_ = compressed.Close()
		return 0, fmt.Errorf("invalid global RIB checkpoint magic")
	}
	count := int64(0)
	for {
		var header [20]byte
		if _, err := io.ReadFull(reader, header[:]); err != nil {
			if err == io.EOF {
				break
			}
			_ = compressed.Close()
			return 0, err
		}
		originKnown := header[0]&1 != 0
		afi := header[1]
		prefixBits := header[2]
		peerIPLength := int(header[3])
		addressLength := 4
		if afi == 6 {
			addressLength = 16
		} else if afi != 4 {
			_ = compressed.Close()
			return 0, fmt.Errorf("invalid checkpoint AFI")
		}
		if peerIPLength != 4 && peerIPLength != 16 {
			_ = compressed.Close()
			return 0, fmt.Errorf("invalid checkpoint peer IP length")
		}
		peerRaw := make([]byte, peerIPLength)
		prefixRaw := make([]byte, addressLength)
		if _, err := io.ReadFull(reader, peerRaw); err != nil {
			_ = compressed.Close()
			return 0, err
		}
		if _, err := io.ReadFull(reader, prefixRaw); err != nil {
			_ = compressed.Close()
			return 0, err
		}
		peerIP, err := parseAddress(peerRaw)
		if err != nil {
			_ = compressed.Close()
			return 0, err
		}
		prefixAddress, err := parsePrefixAddress(prefixRaw, afi)
		if err != nil {
			_ = compressed.Close()
			return 0, err
		}
		key := RouteKey{
			PeerIP:  peerIP,
			PeerASN: binary.BigEndian.Uint32(header[4:8]),
			AFI:     afi,
			Prefix:  netip.PrefixFrom(prefixAddress, int(prefixBits)).Masked(),
		}
		if shardFor(key, shardCount) != meta.Shard {
			_ = compressed.Close()
			return 0, fmt.Errorf("global RIB checkpoint shard coordinate mismatch")
		}
		originASN := binary.BigEndian.Uint32(header[8:12])
		countryID := uint16(0)
		if originKnown {
			countryID = state.Mapping.CountryID(originASN)
		}
		if _, selected := selectedIDs[countryID]; selected {
			if err := state.Seed(
				key, originKnown, originASN,
				binary.BigEndian.Uint32(header[12:16]),
				binary.BigEndian.Uint32(header[16:20]),
			); err != nil {
				_ = compressed.Close()
				return 0, err
			}
		}
		count++
	}
	if err := compressed.Close(); err != nil {
		return 0, err
	}
	if _, err := io.Copy(io.Discard, tee); err != nil {
		return 0, err
	}
	if count != meta.RecordCount || counting.bytes != meta.SizeBytes ||
		hex.EncodeToString(hash.Sum(nil)) != meta.SHA256 {
		return 0, fmt.Errorf("global RIB checkpoint shard identity mismatch")
	}
	return count, nil
}

// ApplyGlobalSpoolSlotSelected 完整读取、排序和验真一个全球 spool 槽，但只把
// 属于已装入固定 cohort 的 route key 应用到定向状态机。
func ApplyGlobalSpoolSlotSelected(
	state *GlobalReplayState,
	sourceRoot string,
	meta SlotSpoolMeta,
) (int64, error) {
	if state == nil {
		return 0, fmt.Errorf("selected global replay state is required")
	}
	shards := append([]ShardSpoolMeta(nil), meta.Shards...)
	sort.Slice(shards, func(i, j int) bool { return shards[i].Shard < shards[j].Shard })
	if len(shards) == 0 {
		return 0, fmt.Errorf("global spool slot has no shards")
	}
	for shardIndex, shard := range shards {
		if shard.Shard != shardIndex {
			return 0, fmt.Errorf("global spool shard sequence mismatch")
		}
	}

	// 每个 route key 只会进入一个 shard。先并行读取并验真全部 shard，只保留
	// 已选固定 cohort 的事件；再按原始 MRT 坐标统一排序并串行 Apply。这样不改变
	// 同一 route key 的事件顺序，也不让多个 goroutine 并发修改 RouteState。
	type shardResult struct {
		index     int
		processed int64
		events    []ParsedEvent
		err       error
	}
	workerCount := runtime.GOMAXPROCS(0)
	if workerCount > len(shards) {
		workerCount = len(shards)
	}
	jobs := make(chan int)
	results := make(chan shardResult, len(shards))
	var workers sync.WaitGroup
	workers.Add(workerCount)
	for worker := 0; worker < workerCount; worker++ {
		go func() {
			defer workers.Done()
			for index := range jobs {
				shard := shards[index]
				processed, events, err := readSelectedGlobalSpoolShard(
					state,
					filepath.Join(sourceRoot, filepath.FromSlash(shard.Path)),
					shard,
					meta.ArtifactIndex,
					len(shards),
				)
				results <- shardResult{
					index: index, processed: processed, events: events, err: err,
				}
			}
		}()
	}
	go func() {
		for index := range shards {
			jobs <- index
		}
		close(jobs)
		workers.Wait()
		close(results)
	}()

	processed := int64(0)
	selected := make([]ParsedEvent, 0)
	seenResults := make([]bool, len(shards))
	var firstErr error
	for result := range results {
		if result.index < 0 || result.index >= len(shards) || seenResults[result.index] {
			if firstErr == nil {
				firstErr = fmt.Errorf("global spool parallel result coordinate mismatch")
			}
			continue
		}
		seenResults[result.index] = true
		processed += result.processed
		selected = append(selected, result.events...)
		if result.err != nil && firstErr == nil {
			firstErr = result.err
		}
	}
	if firstErr != nil {
		return 0, firstErr
	}
	for _, seen := range seenResults {
		if !seen {
			return 0, fmt.Errorf("global spool parallel result is missing")
		}
	}
	if processed != meta.Stats.RouteEvents {
		return 0, fmt.Errorf("global spool slot %d population mismatch", meta.ArtifactIndex)
	}
	sort.Slice(selected, func(i, j int) bool {
		if selected[i].RecordOrdinal != selected[j].RecordOrdinal {
			return selected[i].RecordOrdinal < selected[j].RecordOrdinal
		}
		return selected[i].ElementOrdinal < selected[j].ElementOrdinal
	})
	activity := NewGlobalSlotActivity()
	for index, event := range selected {
		if index > 0 && event.RecordOrdinal == selected[index-1].RecordOrdinal &&
			event.ElementOrdinal == selected[index-1].ElementOrdinal {
			return 0, fmt.Errorf("global selected spool order is not strictly increasing")
		}
		if err := state.Apply(event, activity); err != nil {
			return 0, err
		}
	}
	return int64(len(selected)), nil
}

func readSelectedGlobalSpoolShard(
	state *GlobalReplayState,
	path string,
	meta ShardSpoolMeta,
	artifactIndex int,
	shardCount int,
) (int64, []ParsedEvent, error) {
	reader, err := openSpool(path)
	if err != nil {
		return 0, nil, err
	}
	closed := false
	defer func() {
		if !closed {
			_ = reader.Close()
		}
	}()
	selected := make([]ParsedEvent, 0)
	count := int64(0)
	haveLast := false
	var lastRecord uint32
	var lastElement uint32
	for {
		event, nextErr := reader.Next()
		if nextErr == io.EOF {
			break
		}
		if nextErr != nil {
			return 0, nil, nextErr
		}
		if int(event.ArtifactIndex) != artifactIndex ||
			shardFor(event.Key, shardCount) != meta.Shard {
			return 0, nil, fmt.Errorf("global spool event coordinate mismatch")
		}
		if haveLast && (event.RecordOrdinal < lastRecord ||
			(event.RecordOrdinal == lastRecord && event.ElementOrdinal <= lastElement)) {
			return 0, nil, fmt.Errorf("global spool shard order is not strictly increasing")
		}
		if route, exists := state.Routes[event.Key]; exists && !route.Dynamic {
			selected = append(selected, event)
		}
		count++
		haveLast = true
		lastRecord = event.RecordOrdinal
		lastElement = event.ElementOrdinal
	}
	if count != meta.RecordCount {
		return 0, nil, fmt.Errorf("global spool record count mismatch")
	}
	if err := reader.Verify(meta); err != nil {
		return 0, nil, err
	}
	if err := reader.Close(); err != nil {
		return 0, nil, err
	}
	closed = true
	return count, selected, nil
}

func BuildSelectedGlobalCountryCohorts(
	state *GlobalReplayState,
	manifest GlobalRIBCheckpointManifest,
	countryCodes map[string]struct{},
) ([]GlobalCountryCohortDocument, error) {
	if state == nil || state.Mapping == nil || len(countryCodes) == 0 {
		return nil, fmt.Errorf("selected global RouteState is required")
	}
	documents := make(map[string]*GlobalCountryCohortDocument, len(countryCodes))
	asnSets := make(map[string]map[uint32]struct{}, len(countryCodes))
	manifestByCode := make(map[string]GlobalCheckpointCountry)
	for _, source := range manifest.Countries {
		manifestByCode[source.CountryCode] = source
	}
	for code := range countryCodes {
		source, exists := manifestByCode[code]
		if !exists {
			return nil, fmt.Errorf("selected cohort country %s absent from RIB manifest", code)
		}
		documents[code] = &GlobalCountryCohortDocument{
			SchemaVersion:             GlobalCohortVersion,
			CohortID:                  source.CohortID,
			CollectorID:               manifest.CollectorID,
			CountryCode:               code,
			MappingVersion:            manifest.MappingVersion,
			SeedObservedAt:            manifest.SeedObservedAt,
			BaselinePrefixVPCount:     source.BaselinePrefixVP,
			BaselineIPv4PrefixVPCount: source.BaselineIPv4,
			BaselineIPv6PrefixVPCount: source.BaselineIPv6,
			MembershipDigest:          source.MembershipDigest,
			Members:                   []GlobalCountryCohortMember{},
		}
		asnSets[code] = make(map[uint32]struct{})
	}
	accumulators := make(map[countryMemberKey]*countryMemberAccumulator)
	for key, route := range state.Routes {
		if route.Dynamic || !route.BaselineOriginKnown {
			continue
		}
		code := state.Mapping.CountryCode(route.BaselineCountryID)
		if _, selected := countryCodes[code]; !selected {
			continue
		}
		memberKey := countryMemberKey{
			CountryID: route.BaselineCountryID,
			ASN:       route.BaselineOriginASN,
			AFI:       key.AFI,
		}
		current := accumulators[memberKey]
		if current == nil {
			current = &countryMemberAccumulator{Prefixes: make(map[netip.Prefix]struct{})}
			accumulators[memberKey] = current
		}
		current.PrefixVPCount++
		current.Prefixes[key.Prefix] = struct{}{}
	}
	keys := make([]countryMemberKey, 0, len(accumulators))
	for key := range accumulators {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool {
		left := state.Mapping.CountryCode(keys[i].CountryID)
		right := state.Mapping.CountryCode(keys[j].CountryID)
		if left != right {
			return left < right
		}
		if keys[i].ASN != keys[j].ASN {
			return keys[i].ASN < keys[j].ASN
		}
		return keys[i].AFI < keys[j].AFI
	})
	for _, key := range keys {
		code := state.Mapping.CountryCode(key.CountryID)
		current := accumulators[key]
		prefixValues := make([]string, 0, len(current.Prefixes))
		for prefix := range current.Prefixes {
			prefixValues = append(prefixValues, prefix.String())
		}
		sort.Strings(prefixValues)
		documents[code].Members = append(documents[code].Members, GlobalCountryCohortMember{
			ASN:           key.ASN,
			AFI:           key.AFI,
			PrefixVPCount: current.PrefixVPCount,
			Prefixes:      prefixValues,
		})
		asnSets[code][key.ASN] = struct{}{}
	}
	codes := make([]string, 0, len(countryCodes))
	for code := range countryCodes {
		codes = append(codes, code)
	}
	sort.Strings(codes)
	result := make([]GlobalCountryCohortDocument, 0, len(codes))
	for _, code := range codes {
		document := documents[code]
		document.BaselineOriginASNs = sortedUint32(asnSets[code])
		document.BaselineOriginASNCount = len(document.BaselineOriginASNs)
		knownMembers := int64(0)
		for _, member := range document.Members {
			knownMembers += int64(member.PrefixVPCount)
		}
		if knownMembers != document.BaselinePrefixVPCount ||
			document.BaselinePrefixVPCount !=
				document.BaselineIPv4PrefixVPCount+document.BaselineIPv6PrefixVPCount {
			return nil, fmt.Errorf("selected country %s cohort population mismatch", code)
		}
		result = append(result, *document)
	}
	return result, nil
}
