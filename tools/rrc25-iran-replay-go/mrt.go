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
	"strconv"
	"strings"
	"time"
)

const (
	mrtHeaderBytes = 12
	mrtTableDumpV2 = 13
	mrtBGP4MP      = 16
	mrtBGP4MPET    = 17

	peerIndexTable = 1
	ribIPv4Unicast = 2
	ribIPv6Unicast = 4

	actionWithdraw    = uint8(1)
	actionAnnounce    = uint8(2)
	actionRIBSnapshot = uint8(3)
)

type hashingReader struct {
	reader io.Reader
	hash   hashState
	bytes  int64
}

type hashState struct {
	value [32]byte
	h     io.Writer
	sum   interface{ Sum([]byte) []byte }
}

func newHashingReader(reader io.Reader) *hashingReader {
	hash := sha256.New()
	return &hashingReader{
		reader: reader,
		hash:   hashState{h: hash, sum: hash},
	}
}

func (reader *hashingReader) Read(buffer []byte) (int, error) {
	n, err := reader.reader.Read(buffer)
	if n > 0 {
		_, _ = reader.hash.h.Write(buffer[:n])
		reader.bytes += int64(n)
	}
	return n, err
}

func (reader *hashingReader) digest() string {
	return hex.EncodeToString(reader.hash.sum.Sum(nil))
}

func withVerifiedGzip(
	rawRoot string,
	artifact Artifact,
	consume func(io.Reader) error,
) error {
	path := filepath.Join(rawRoot, filepath.FromSlash(artifact.RelativePath))
	before, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if !before.Mode().IsRegular() || before.Mode()&os.ModeSymlink != 0 ||
		before.Size() != artifact.SizeBytes {
		return fmt.Errorf("input identity changed before open: %s", path)
	}
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	hashing := newHashingReader(bufio.NewReaderSize(file, 1<<20))
	decoded, err := gzip.NewReader(hashing)
	if err != nil {
		return fmt.Errorf("open gzip %s: %w", path, err)
	}
	if err := consume(decoded); err != nil {
		_ = decoded.Close()
		return err
	}
	if err := decoded.Close(); err != nil {
		return fmt.Errorf("close gzip %s: %w", path, err)
	}
	if _, err := io.Copy(io.Discard, hashing); err != nil {
		return fmt.Errorf("drain compressed input %s: %w", path, err)
	}
	after, err := file.Stat()
	if err != nil {
		return err
	}
	if before.Size() != after.Size() || before.ModTime() != after.ModTime() {
		return fmt.Errorf("input changed while reading: %s", path)
	}
	if hashing.bytes != artifact.SizeBytes {
		return fmt.Errorf("compressed byte count mismatch for %s", path)
	}
	if hashing.digest() != artifact.FileSHA256 {
		return fmt.Errorf("sha256 mismatch for %s", path)
	}
	return nil
}

func readMRTRecord(reader io.Reader) (timestamp uint32, mrtType, subtype uint16, payload []byte, err error) {
	header := make([]byte, mrtHeaderBytes)
	_, err = io.ReadFull(reader, header)
	if err == io.EOF {
		return 0, 0, 0, nil, io.EOF
	}
	if err != nil {
		return 0, 0, 0, nil, fmt.Errorf("truncated MRT header: %w", err)
	}
	timestamp = binary.BigEndian.Uint32(header[0:4])
	mrtType = binary.BigEndian.Uint16(header[4:6])
	subtype = binary.BigEndian.Uint16(header[6:8])
	length := binary.BigEndian.Uint32(header[8:12])
	if length > 64<<20 {
		return 0, 0, 0, nil, fmt.Errorf("MRT record exceeds 64 MiB")
	}
	payload = make([]byte, int(length))
	if _, err := io.ReadFull(reader, payload); err != nil {
		return 0, 0, 0, nil, fmt.Errorf("truncated MRT payload: %w", err)
	}
	return timestamp, mrtType, subtype, payload, nil
}

type MRTRecordEvidence struct {
	Timestamp          uint32
	MRTType            uint16
	Subtype            uint16
	Payload            []byte
	UncompressedOffset int64
	RecordLength       uint32
	RecordSHA256       string
}

func readMRTRecordEvidence(
	reader io.Reader,
	uncompressedOffset int64,
) (MRTRecordEvidence, error) {
	header := make([]byte, mrtHeaderBytes)
	if _, err := io.ReadFull(reader, header); err != nil {
		if err == io.EOF {
			return MRTRecordEvidence{}, io.EOF
		}
		return MRTRecordEvidence{}, fmt.Errorf("truncated MRT header: %w", err)
	}
	length := binary.BigEndian.Uint32(header[8:12])
	if length > 64<<20 {
		return MRTRecordEvidence{}, fmt.Errorf("MRT record exceeds 64 MiB")
	}
	payload := make([]byte, int(length))
	if _, err := io.ReadFull(reader, payload); err != nil {
		return MRTRecordEvidence{}, fmt.Errorf("truncated MRT payload: %w", err)
	}
	digest := sha256.New()
	_, _ = digest.Write(header)
	_, _ = digest.Write(payload)
	return MRTRecordEvidence{
		Timestamp:          binary.BigEndian.Uint32(header[0:4]),
		MRTType:            binary.BigEndian.Uint16(header[4:6]),
		Subtype:            binary.BigEndian.Uint16(header[6:8]),
		Payload:            payload,
		UncompressedOffset: uncompressedOffset,
		RecordLength:       uint32(mrtHeaderBytes) + length,
		RecordSHA256:       hex.EncodeToString(digest.Sum(nil)),
	}, nil
}

type cursor struct {
	raw   []byte
	at    int
	field string
}

func (cursor *cursor) take(length int, field string) ([]byte, error) {
	if length < 0 || cursor.at+length > len(cursor.raw) {
		return nil, fmt.Errorf("%s.%s out of bounds", cursor.field, field)
	}
	result := cursor.raw[cursor.at : cursor.at+length]
	cursor.at += length
	return result, nil
}

func (cursor *cursor) u8(field string) (uint8, error) {
	raw, err := cursor.take(1, field)
	if err != nil {
		return 0, err
	}
	return raw[0], nil
}

func (cursor *cursor) u16(field string) (uint16, error) {
	raw, err := cursor.take(2, field)
	if err != nil {
		return 0, err
	}
	return binary.BigEndian.Uint16(raw), nil
}

func (cursor *cursor) u32(field string) (uint32, error) {
	raw, err := cursor.take(4, field)
	if err != nil {
		return 0, err
	}
	return binary.BigEndian.Uint32(raw), nil
}

func (cursor *cursor) finish() error {
	if cursor.at != len(cursor.raw) {
		return fmt.Errorf("%s has %d unconsumed bytes", cursor.field, len(cursor.raw)-cursor.at)
	}
	return nil
}

type peer struct {
	IP  netip.Addr
	ASN uint32
}

func parseAddress(raw []byte) (netip.Addr, error) {
	address, ok := netip.AddrFromSlice(raw)
	if !ok {
		return netip.Addr{}, fmt.Errorf("invalid IP address")
	}
	return address.Unmap(), nil
}

func parsePrefixAddress(raw []byte, afi uint8) (netip.Addr, error) {
	switch afi {
	case 4:
		if len(raw) != 4 {
			return netip.Addr{}, fmt.Errorf("invalid IPv4 prefix address")
		}
		var value [4]byte
		copy(value[:], raw)
		return netip.AddrFrom4(value), nil
	case 6:
		if len(raw) != 16 {
			return netip.Addr{}, fmt.Errorf("invalid IPv6 prefix address")
		}
		var value [16]byte
		copy(value[:], raw)
		return netip.AddrFrom16(value), nil
	default:
		return netip.Addr{}, fmt.Errorf("unsupported prefix AFI %d", afi)
	}
}

func parsePeerIndex(payload []byte) ([]peer, error) {
	cursor := cursor{raw: payload, field: "peer-index"}
	if _, err := cursor.take(4, "collector-bgp-id"); err != nil {
		return nil, err
	}
	viewLength, err := cursor.u16("view-length")
	if err != nil {
		return nil, err
	}
	if _, err := cursor.take(int(viewLength), "view-name"); err != nil {
		return nil, err
	}
	count, err := cursor.u16("peer-count")
	if err != nil {
		return nil, err
	}
	result := make([]peer, 0, count)
	for index := 0; index < int(count); index++ {
		peerType, err := cursor.u8("peer-type")
		if err != nil {
			return nil, err
		}
		if peerType&^0x03 != 0 {
			return nil, fmt.Errorf("peer type has reserved bits")
		}
		if _, err := cursor.take(4, "peer-bgp-id"); err != nil {
			return nil, err
		}
		addressBytes := 4
		if peerType&0x01 != 0 {
			addressBytes = 16
		}
		rawIP, err := cursor.take(addressBytes, "peer-ip")
		if err != nil {
			return nil, err
		}
		ip, err := parseAddress(rawIP)
		if err != nil {
			return nil, err
		}
		asnBytes := 2
		if peerType&0x02 != 0 {
			asnBytes = 4
		}
		rawASN, err := cursor.take(asnBytes, "peer-asn")
		if err != nil {
			return nil, err
		}
		var asn uint32
		if asnBytes == 2 {
			asn = uint32(binary.BigEndian.Uint16(rawASN))
		} else {
			asn = binary.BigEndian.Uint32(rawASN)
		}
		result = append(result, peer{IP: ip, ASN: asn})
	}
	if err := cursor.finish(); err != nil {
		return nil, err
	}
	return result, nil
}

func parsePrefix(raw []byte, bitLength uint8, afi uint8) (netip.Prefix, error) {
	maxBits := 32
	addressBytes := 4
	if afi == 6 {
		maxBits = 128
		addressBytes = 16
	}
	if int(bitLength) > maxBits || len(raw) != (int(bitLength)+7)/8 {
		return netip.Prefix{}, fmt.Errorf("invalid prefix framing")
	}
	full := make([]byte, addressBytes)
	copy(full, raw)
	address, ok := netip.AddrFromSlice(full)
	if !ok {
		return netip.Prefix{}, fmt.Errorf("invalid prefix address")
	}
	if afi == 4 {
		address = address.Unmap()
	}
	prefix := netip.PrefixFrom(address, int(bitLength))
	if prefix.Masked() != prefix {
		return netip.Prefix{}, fmt.Errorf("non-canonical prefix host bits")
	}
	return prefix, nil
}

type originResult struct {
	Known bool
	ASN   uint32
}

const (
	asSetSegment           = "as_set"
	asSequenceSegment      = "as_sequence"
	confedSequenceSegment  = "confederation_sequence"
	confedSetSegment       = "confederation_set"
	asPathSnapshotSemantic = "route_observation_path_snapshot"
)

type ASPathSegment struct {
	SegmentType string   `json:"segment_type"`
	ASNs        []uint32 `json:"asns"`
}

type ASPathSnapshot struct {
	Semantics        string          `json:"semantics"`
	CausalConclusion *string         `json:"causal_conclusion"`
	Canonical        string          `json:"canonical"`
	Segments         []ASPathSegment `json:"segments"`
}

func segmentName(value uint8) (string, error) {
	switch value {
	case 1:
		return asSetSegment, nil
	case 2:
		return asSequenceSegment, nil
	case 3:
		return confedSequenceSegment, nil
	case 4:
		return confedSetSegment, nil
	default:
		return "", fmt.Errorf("unsupported AS_PATH segment type %d", value)
	}
}

func canonicalASPath(segments []ASPathSegment) string {
	parts := make([]string, 0, len(segments))
	for _, segment := range segments {
		asns := make([]string, 0, len(segment.ASNs))
		for _, asn := range segment.ASNs {
			asns = append(asns, strconv.FormatUint(uint64(asn), 10))
		}
		var part string
		switch segment.SegmentType {
		case asSetSegment:
			part = "{" + strings.Join(asns, ",") + "}"
		case asSequenceSegment:
			part = strings.Join(asns, " ")
		case confedSequenceSegment:
			part = "(" + strings.Join(asns, " ") + ")"
		case confedSetSegment:
			part = "[" + strings.Join(asns, ",") + "]"
		}
		parts = append(parts, part)
	}
	return strings.Join(parts, " ")
}

func newASPathSnapshot(segments []ASPathSegment) ASPathSnapshot {
	cloned := make([]ASPathSegment, 0, len(segments))
	for _, segment := range segments {
		cloned = append(cloned, ASPathSegment{
			SegmentType: segment.SegmentType,
			ASNs:        append([]uint32(nil), segment.ASNs...),
		})
	}
	return ASPathSnapshot{
		Semantics: asPathSnapshotSemantic,
		Segments:  cloned,
		Canonical: canonicalASPath(cloned),
	}
}

func (path ASPathSnapshot) Origin() originResult {
	if len(path.Segments) == 0 {
		return originResult{}
	}
	last := path.Segments[len(path.Segments)-1]
	if last.SegmentType != asSequenceSegment || len(last.ASNs) == 0 {
		return originResult{}
	}
	return originResult{Known: true, ASN: last.ASNs[len(last.ASNs)-1]}
}

// PathLength 使用 RFC 4271/5065 路由选择口径：AS_SEQUENCE 按 ASN 数计，
// AS_SET 计 1，confederation segment 不计入。该口径也是 RFC 6793
// 重建 AS_PATH + AS4_PATH 时的必要前提。
func (path ASPathSnapshot) PathLength() int {
	length := 0
	for _, segment := range path.Segments {
		switch segment.SegmentType {
		case asSequenceSegment:
			length += len(segment.ASNs)
		case asSetSegment:
			length++
		}
	}
	return length
}

func parseASPath(value []byte, asnWidth int) (ASPathSnapshot, error) {
	if asnWidth != 2 && asnWidth != 4 {
		return ASPathSnapshot{}, fmt.Errorf("invalid AS_PATH ASN width %d", asnWidth)
	}
	at := 0
	segments := make([]ASPathSegment, 0)
	for at < len(value) {
		if at+2 > len(value) {
			return ASPathSnapshot{}, fmt.Errorf("AS_PATH segment header truncated")
		}
		segmentType := value[at]
		count := int(value[at+1])
		at += 2
		if segmentType < 1 || segmentType > 4 || count == 0 ||
			at+count*asnWidth > len(value) {
			return ASPathSnapshot{}, fmt.Errorf("invalid AS_PATH segment")
		}
		name, err := segmentName(segmentType)
		if err != nil {
			return ASPathSnapshot{}, err
		}
		segment := ASPathSegment{SegmentType: name, ASNs: make([]uint32, 0, count)}
		for index := 0; index < count; index++ {
			raw := value[at+index*asnWidth : at+(index+1)*asnWidth]
			if asnWidth == 2 {
				segment.ASNs = append(segment.ASNs, uint32(binary.BigEndian.Uint16(raw)))
			} else {
				segment.ASNs = append(segment.ASNs, binary.BigEndian.Uint32(raw))
			}
		}
		segments = append(segments, segment)
		at += count * asnWidth
	}
	return newASPathSnapshot(segments), nil
}

func appendLeadingPath(
	result []ASPathSegment,
	base ASPathSnapshot,
	wantedLength int,
) []ASPathSegment {
	remaining := wantedLength
	leading := true
	adjacent := false
	for _, segment := range base.Segments {
		switch segment.SegmentType {
		case confedSequenceSegment, confedSetSegment:
			// RFC 6793 要求保留领先或紧邻已保留段的联盟段。
			if leading || adjacent {
				result = append(result, segment)
				adjacent = true
			}
		case asSequenceSegment:
			leading = false
			if remaining <= 0 {
				return result
			}
			count := len(segment.ASNs)
			if count > remaining {
				count = remaining
			}
			result = append(result, ASPathSegment{
				SegmentType: segment.SegmentType,
				ASNs:        append([]uint32(nil), segment.ASNs[:count]...),
			})
			adjacent = true
			remaining -= count
			if count < len(segment.ASNs) {
				return result
			}
		case asSetSegment:
			leading = false
			if remaining <= 0 {
				return result
			}
			result = append(result, segment)
			adjacent = true
			remaining--
		}
	}
	return result
}

func mergeAS4Path(base, as4 ASPathSnapshot) (ASPathSnapshot, bool) {
	baseLength := base.PathLength()
	as4Length := as4.PathLength()
	if as4Length == 0 || baseLength < as4Length {
		return base, false
	}
	segments := appendLeadingPath(nil, base, baseLength-as4Length)
	for _, segment := range as4.Segments {
		if segment.SegmentType == confedSequenceSegment ||
			segment.SegmentType == confedSetSegment {
			continue
		}
		segments = append(segments, segment)
	}
	return newASPathSnapshot(segments), true
}

type parsedAttributes struct {
	Origin            originResult
	OriginSeen        bool
	Path              ASPathSnapshot
	AS4Origin         originResult
	AS4Seen           bool
	AS4Path           ASPathSnapshot
	PathWarnings      []string
	MPAnnounces       map[uint8][]netip.Prefix
	MPWithdraws       map[uint8][]netip.Prefix
	UnknownOptional   map[uint8]int64
	RIBCompactMPReach int64
	MalformedOTC      int64
	TreatAsWithdraw   bool
}

func parseNLRIs(raw []byte, afi uint8) ([]netip.Prefix, error) {
	result := make([]netip.Prefix, 0)
	for at := 0; at < len(raw); {
		bits := raw[at]
		at++
		octets := (int(bits) + 7) / 8
		if at+octets > len(raw) {
			return nil, fmt.Errorf("NLRI truncated")
		}
		prefix, err := parsePrefix(raw[at:at+octets], bits, afi)
		if err != nil {
			return nil, err
		}
		result = append(result, prefix)
		at += octets
	}
	return result, nil
}

func parseMPReach(value []byte) (uint8, []netip.Prefix, error) {
	cursor := cursor{raw: value, field: "MP_REACH"}
	afiCode, err := cursor.u16("afi")
	if err != nil {
		return 0, nil, err
	}
	safi, err := cursor.u8("safi")
	if err != nil {
		return 0, nil, err
	}
	var afi uint8
	switch afiCode {
	case 1:
		afi = 4
	case 2:
		afi = 6
	default:
		return 0, nil, fmt.Errorf("unsupported MP_REACH AFI %d", afiCode)
	}
	if safi != 1 {
		return 0, nil, fmt.Errorf("unsupported MP_REACH SAFI %d", safi)
	}
	nextHopLength, err := cursor.u8("next-hop-length")
	if err != nil {
		return 0, nil, err
	}
	if _, err := cursor.take(int(nextHopLength), "next-hop"); err != nil {
		return 0, nil, err
	}
	reserved, err := cursor.u8("reserved")
	if err != nil || reserved != 0 {
		return 0, nil, fmt.Errorf("invalid MP_REACH reserved byte")
	}
	prefixes, err := parseNLRIs(value[cursor.at:], afi)
	if err != nil {
		return 0, nil, err
	}
	return afi, prefixes, nil
}

func parseMPUnreach(value []byte) (uint8, []netip.Prefix, error) {
	if len(value) < 3 {
		return 0, nil, fmt.Errorf("MP_UNREACH truncated")
	}
	afiCode := binary.BigEndian.Uint16(value[:2])
	safi := value[2]
	var afi uint8
	switch afiCode {
	case 1:
		afi = 4
	case 2:
		afi = 6
	default:
		return 0, nil, fmt.Errorf("unsupported MP_UNREACH AFI %d", afiCode)
	}
	if safi != 1 {
		return 0, nil, fmt.Errorf("unsupported MP_UNREACH SAFI %d", safi)
	}
	prefixes, err := parseNLRIs(value[3:], afi)
	return afi, prefixes, err
}

func parseRIBCompactMPReach(value []byte) error {
	if len(value) < 1 {
		return fmt.Errorf("compact RIB MP_REACH truncated")
	}
	nextHopLength := int(value[0])
	if nextHopLength != 4 && nextHopLength != 16 && nextHopLength != 32 {
		return fmt.Errorf(
			"unsupported compact RIB MP_REACH next-hop length %d",
			nextHopLength,
		)
	}
	if len(value) != nextHopLength+1 {
		return fmt.Errorf("compact RIB MP_REACH length mismatch")
	}
	for at := 1; at < len(value); {
		width := 16
		if nextHopLength == 4 {
			width = 4
		}
		if _, err := parseAddress(value[at : at+width]); err != nil {
			return fmt.Errorf("compact RIB MP_REACH next-hop: %w", err)
		}
		at += width
	}
	return nil
}

func parseAttributesWithRIBMode(
	raw []byte,
	asnWidth int,
	compactRIBMPReach bool,
) (parsedAttributes, error) {
	result := parsedAttributes{
		MPAnnounces:     make(map[uint8][]netip.Prefix),
		MPWithdraws:     make(map[uint8][]netip.Prefix),
		UnknownOptional: make(map[uint8]int64),
	}
	seen := make(map[uint8]struct{})
	for at := 0; at < len(raw); {
		if at+3 > len(raw) {
			return result, fmt.Errorf("path attribute header truncated")
		}
		flags := raw[at]
		attributeType := raw[at+1]
		at += 2
		if flags&0x0f != 0 {
			return result, fmt.Errorf("path attribute reserved flags set")
		}
		if _, duplicate := seen[attributeType]; duplicate {
			return result, fmt.Errorf("duplicate path attribute %d", attributeType)
		}
		seen[attributeType] = struct{}{}
		var length int
		lengthAt := at
		if flags&0x10 != 0 {
			if at+2 > len(raw) {
				return result, fmt.Errorf("extended attribute length truncated")
			}
			length = int(binary.BigEndian.Uint16(raw[at : at+2]))
			at += 2
		} else {
			length = int(raw[at])
			at++
		}
		if at+length > len(raw) {
			// RRC25 的一个真实 UPDATE 将 OTC(35) 设置了 Extended
			// Length 位，却仍使用一字节长度 4。RFC 9234 规定 OTC
			// 必须为四字节，畸形 OTC 应按 treat-as-withdraw 处理。
			// 这里只接受“最后一个属性、单字节长度正好为 4”的窄特征，
			// 以便保留可识别 NLRI，同时拒绝其他越界属性。
			if attributeType == 35 && flags&0xe0 == 0xe0 &&
				raw[lengthAt] == 4 && lengthAt+1+4 == len(raw) {
				length = 4
				at = lengthAt + 1
				result.MalformedOTC++
				result.TreatAsWithdraw = true
			} else {
				return result, fmt.Errorf(
					"path attribute %d out of bounds", attributeType,
				)
			}
		}
		value := raw[at : at+length]
		at += length
		switch attributeType {
		case 1:
			if flags&0xe0 != 0x40 || len(value) != 1 || value[0] > 2 {
				return result, fmt.Errorf("invalid ORIGIN attribute")
			}
		case 2:
			if flags&0xe0 != 0x40 {
				return result, fmt.Errorf("invalid AS_PATH flags")
			}
			path, err := parseASPath(value, asnWidth)
			if err != nil {
				return result, err
			}
			result.Path = path
			result.Origin, result.OriginSeen = path.Origin(), true
		case 14:
			if compactRIBMPReach {
				if err := parseRIBCompactMPReach(value); err != nil {
					return result, err
				}
				result.RIBCompactMPReach++
				continue
			}
			afi, prefixes, err := parseMPReach(value)
			if err != nil {
				return result, err
			}
			result.MPAnnounces[afi] = prefixes
		case 15:
			afi, prefixes, err := parseMPUnreach(value)
			if err != nil {
				return result, err
			}
			result.MPWithdraws[afi] = prefixes
		case 17:
			path, err := parseASPath(value, 4)
			if err != nil {
				result.PathWarnings = append(
					result.PathWarnings, "malformed_as4_path_discarded",
				)
				continue
			}
			cleaned := make([]ASPathSegment, 0, len(path.Segments))
			for _, segment := range path.Segments {
				if segment.SegmentType == confedSequenceSegment ||
					segment.SegmentType == confedSetSegment {
					result.PathWarnings = append(
						result.PathWarnings, "as4_confederation_segment_discarded",
					)
					continue
				}
				cleaned = append(cleaned, segment)
			}
			result.AS4Path = newASPathSnapshot(cleaned)
			result.AS4Origin, result.AS4Seen = result.AS4Path.Origin(), true
		case 21:
			if flags&0xe0 != 0xc0 && flags&0xe0 != 0xe0 {
				return result, fmt.Errorf("invalid AS_PATHLIMIT flags")
			}
			if len(value) != 5 {
				return result, fmt.Errorf("invalid AS_PATHLIMIT length")
			}
		case 35:
			if flags&0xe0 != 0xc0 && flags&0xe0 != 0xe0 {
				return result, fmt.Errorf("invalid OTC flags")
			}
			if len(value) != 4 {
				return result, fmt.Errorf("invalid OTC length")
			}
		default:
			if flags&0x80 == 0 {
				if attributeType != 3 && attributeType != 5 && attributeType != 6 {
					return result, fmt.Errorf("unknown well-known attribute %d", attributeType)
				}
			} else if attributeType > 42 && attributeType != 128 && attributeType != 255 {
				result.UnknownOptional[attributeType]++
			}
		}
	}
	if result.AS4Seen {
		if asnWidth == 4 {
			result.PathWarnings = append(
				result.PathWarnings, "as4_path_in_four_byte_message_discarded",
			)
		} else if merged, used := mergeAS4Path(result.Path, result.AS4Path); used {
			result.Path = merged
			result.Origin = merged.Origin()
		} else {
			result.PathWarnings = append(
				result.PathWarnings, "as4_path_longer_than_as_path_discarded",
			)
		}
	}
	return result, nil
}

func parseAttributes(raw []byte, asnWidth int) (parsedAttributes, error) {
	return parseAttributesWithRIBMode(raw, asnWidth, false)
}

func parseRIBAttributes(raw []byte, asnWidth int) (parsedAttributes, error) {
	return parseAttributesWithRIBMode(raw, asnWidth, true)
}

func parseRIBRecord(
	payload []byte,
	subtype uint16,
	peers []peer,
	mapping *CountryMapping,
	baseline map[RouteKey]uint32,
	quality *Quality,
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
	cursor := cursor{raw: payload, field: "RIB"}
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
			return fmt.Errorf("RIB peer index out of range")
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
		if !parsed.OriginSeen || !parsed.Origin.Known {
			quality.RIBUnknownOrigins++
			continue
		}
		switch mapping.Membership(parsed.Origin.ASN) {
		case MembershipIR:
			key := RouteKey{
				PeerIP:  peers[peerIndex].IP,
				PeerASN: peers[peerIndex].ASN,
				AFI:     afi,
				Prefix:  prefix,
			}
			baseline[key] = parsed.Origin.ASN
			quality.RIBRetainedMembers++
		case MembershipOther:
			quality.RIBExplicitNonIR++
		default:
			quality.RIBMappingUnknown++
		}
	}
	return cursor.finish()
}

func SeedRIB(
	rawRoot string,
	artifact Artifact,
	mapping *CountryMapping,
) (map[RouteKey]uint32, Quality, error) {
	baseline := make(map[RouteKey]uint32)
	quality := Quality{
		SchemaVersion:              "rrc25-iran-go-quality/v1",
		Status:                     "running",
		EngineVersion:              EngineVersion,
		UpdateOptionalUnknownAttrs: make(map[uint8]int64),
	}
	var peers []peer
	err := withVerifiedGzip(rawRoot, artifact, func(reader io.Reader) error {
		for {
			_, mrtType, subtype, payload, err := readMRTRecord(reader)
			if err == io.EOF {
				break
			}
			if err != nil {
				return err
			}
			quality.RIBPhysicalRecords++
			if mrtType != mrtTableDumpV2 {
				return fmt.Errorf("unsupported RIB MRT type %d", mrtType)
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
				return fmt.Errorf("RIB record before peer index table")
			}
			if err := parseRIBRecord(payload, subtype, peers, mapping, baseline, &quality); err != nil {
				return err
			}
		}
		return nil
	})
	if err != nil {
		return nil, quality, err
	}
	if len(baseline) == 0 {
		return nil, quality, fmt.Errorf("RIB produced empty IR cohort")
	}
	quality.InputCompressedBytes = artifact.SizeBytes
	return baseline, quality, nil
}

type ParsedEvent struct {
	Key             RouteKey
	Action          uint8
	OriginKnown     bool
	OriginASN       uint32
	ASPath          *ASPathSnapshot
	AttributeSHA256 string
	PathWarnings    []string
	EventMicros     int64
	ArtifactIndex   uint16
	RecordOrdinal   uint32
	ElementOrdinal  uint32
}

type UpdateParseStats struct {
	PhysicalRecords int64
	RouteEvents     int64
	Announces       int64
	Withdraws       int64
	UnknownOrigins  int64
	UnknownOptional map[uint8]int64
	MalformedOTC    int64
	TreatAsWithdraw int64
}

func parseBGP4MP(
	timestamp uint32,
	mrtType, subtype uint16,
	payload []byte,
) (peer, int64, int, byte, []byte, error) {
	cursor := cursor{raw: payload, field: "BGP4MP"}
	micros := int64(timestamp) * 1_000_000
	if mrtType == mrtBGP4MPET {
		value, err := cursor.u32("microseconds")
		if err != nil {
			return peer{}, 0, 0, 0, nil, err
		}
		if value >= 1_000_000 {
			return peer{}, 0, 0, 0, nil, fmt.Errorf("invalid extended timestamp")
		}
		micros += int64(value)
	}
	if subtype == 0 || subtype == 5 {
		return peer{}, micros, 0, 0, nil, nil
	}
	var asnWidth int
	switch subtype {
	case 1:
		asnWidth = 2
	case 4:
		asnWidth = 4
	default:
		return peer{}, 0, 0, 0, nil, fmt.Errorf("unsupported BGP4MP subtype %d", subtype)
	}
	rawPeerASN, err := cursor.take(asnWidth, "peer-asn")
	if err != nil {
		return peer{}, 0, 0, 0, nil, err
	}
	if _, err := cursor.take(asnWidth, "local-asn"); err != nil {
		return peer{}, 0, 0, 0, nil, err
	}
	if _, err := cursor.take(2, "interface-index"); err != nil {
		return peer{}, 0, 0, 0, nil, err
	}
	afiCode, err := cursor.u16("afi")
	if err != nil {
		return peer{}, 0, 0, 0, nil, err
	}
	addressBytes := 0
	switch afiCode {
	case 1:
		addressBytes = 4
	case 2:
		addressBytes = 16
	default:
		return peer{}, 0, 0, 0, nil, fmt.Errorf("unsupported peer AFI %d", afiCode)
	}
	rawPeerIP, err := cursor.take(addressBytes, "peer-ip")
	if err != nil {
		return peer{}, 0, 0, 0, nil, err
	}
	if _, err := cursor.take(addressBytes, "local-ip"); err != nil {
		return peer{}, 0, 0, 0, nil, err
	}
	peerIP, err := parseAddress(rawPeerIP)
	if err != nil {
		return peer{}, 0, 0, 0, nil, err
	}
	var peerASN uint32
	if asnWidth == 2 {
		peerASN = uint32(binary.BigEndian.Uint16(rawPeerASN))
	} else {
		peerASN = binary.BigEndian.Uint32(rawPeerASN)
	}
	message := payload[cursor.at:]
	if len(message) < 19 {
		return peer{}, 0, 0, 0, nil, fmt.Errorf("BGP message truncated")
	}
	for _, value := range message[:16] {
		if value != 0xff {
			return peer{}, 0, 0, 0, nil, fmt.Errorf("invalid BGP marker")
		}
	}
	messageLength := int(binary.BigEndian.Uint16(message[16:18]))
	if messageLength != len(message) || messageLength < 19 || messageLength > 65535 {
		return peer{}, 0, 0, 0, nil, fmt.Errorf("invalid BGP message length")
	}
	return peer{IP: peerIP, ASN: peerASN}, micros, asnWidth, message[18], message[19:], nil
}

func decodeUpdateEvents(
	peer peer,
	eventMicros int64,
	body []byte,
	asnWidth int,
	artifactIndex uint16,
	recordOrdinal uint32,
	stats *UpdateParseStats,
) ([]ParsedEvent, error) {
	if len(body) < 4 {
		return nil, fmt.Errorf("BGP UPDATE body truncated")
	}
	withdrawLength := int(binary.BigEndian.Uint16(body[:2]))
	if 2+withdrawLength+2 > len(body) {
		return nil, fmt.Errorf("withdraw section out of bounds")
	}
	withdrawPrefixes, err := parseNLRIs(body[2:2+withdrawLength], 4)
	if err != nil {
		return nil, err
	}
	attributeLengthAt := 2 + withdrawLength
	attributeLength := int(binary.BigEndian.Uint16(body[attributeLengthAt : attributeLengthAt+2]))
	attributeStart := attributeLengthAt + 2
	attributeEnd := attributeStart + attributeLength
	if attributeEnd > len(body) {
		return nil, fmt.Errorf("attribute section out of bounds")
	}
	attributes, err := parseAttributes(body[attributeStart:attributeEnd], asnWidth)
	if err != nil {
		return nil, err
	}
	for key, count := range attributes.UnknownOptional {
		stats.UnknownOptional[key] += count
	}
	stats.MalformedOTC += attributes.MalformedOTC
	attributeDigest := sha256.Sum256(body[attributeStart:attributeEnd])
	attributeSHA256 := hex.EncodeToString(attributeDigest[:])
	announcePrefixes, err := parseNLRIs(body[attributeEnd:], 4)
	if err != nil {
		return nil, err
	}
	if (len(announcePrefixes) > 0 || len(attributes.MPAnnounces) > 0) &&
		(!attributes.OriginSeen) {
		return nil, fmt.Errorf("announcement missing AS_PATH")
	}
	events := make([]ParsedEvent, 0, len(withdrawPrefixes)+len(announcePrefixes))
	element := uint32(0)
	appendWithdraw := func(afi uint8, prefix netip.Prefix) {
		events = append(events, ParsedEvent{
			Key:    RouteKey{PeerIP: peer.IP, PeerASN: peer.ASN, AFI: afi, Prefix: prefix},
			Action: actionWithdraw, EventMicros: eventMicros,
			AttributeSHA256: attributeSHA256,
			PathWarnings:    append([]string(nil), attributes.PathWarnings...),
			ArtifactIndex:   artifactIndex, RecordOrdinal: recordOrdinal,
			ElementOrdinal: element,
		})
		element++
		stats.Withdraws++
	}
	appendAnnounce := func(afi uint8, prefix netip.Prefix) {
		if attributes.TreatAsWithdraw {
			appendWithdraw(afi, prefix)
			stats.TreatAsWithdraw++
			return
		}
		path := attributes.Path
		events = append(events, ParsedEvent{
			Key:    RouteKey{PeerIP: peer.IP, PeerASN: peer.ASN, AFI: afi, Prefix: prefix},
			Action: actionAnnounce, OriginKnown: attributes.Origin.Known,
			OriginASN: attributes.Origin.ASN, ASPath: &path,
			AttributeSHA256: attributeSHA256,
			PathWarnings:    append([]string(nil), attributes.PathWarnings...),
			EventMicros:     eventMicros,
			ArtifactIndex:   artifactIndex, RecordOrdinal: recordOrdinal,
			ElementOrdinal: element,
		})
		element++
		stats.Announces++
		if !attributes.Origin.Known {
			stats.UnknownOrigins++
		}
	}
	for _, prefix := range withdrawPrefixes {
		appendWithdraw(4, prefix)
	}
	for _, afi := range []uint8{4, 6} {
		for _, prefix := range attributes.MPWithdraws[afi] {
			appendWithdraw(afi, prefix)
		}
	}
	for _, prefix := range announcePrefixes {
		appendAnnounce(4, prefix)
	}
	for _, afi := range []uint8{4, 6} {
		for _, prefix := range attributes.MPAnnounces[afi] {
			appendAnnounce(afi, prefix)
		}
	}
	return events, nil
}

func ParseUpdate(
	rawRoot string,
	artifact Artifact,
	artifactIndex uint16,
	emit func(ParsedEvent) error,
) (UpdateParseStats, error) {
	stats := UpdateParseStats{UnknownOptional: make(map[uint8]int64)}
	slot, err := time.Parse(time.RFC3339, artifact.ArtifactTimeUTC)
	if err != nil {
		return stats, err
	}
	slotStartMicros := slot.UnixMicro()
	slotEndMicros := slot.Add(5 * time.Minute).UnixMicro()
	err = withVerifiedGzip(rawRoot, artifact, func(reader io.Reader) error {
		recordOrdinal := uint32(0)
		for {
			timestamp, mrtType, subtype, payload, err := readMRTRecord(reader)
			if err == io.EOF {
				break
			}
			if err != nil {
				return err
			}
			stats.PhysicalRecords++
			if mrtType != mrtBGP4MP && mrtType != mrtBGP4MPET {
				return fmt.Errorf("unsupported UPDATE MRT type %d", mrtType)
			}
			peer, eventMicros, asnWidth, messageType, body, err := parseBGP4MP(
				timestamp, mrtType, subtype, payload,
			)
			if err != nil {
				return fmt.Errorf("record %d: %w", recordOrdinal, err)
			}
			if messageType == 0 {
				recordOrdinal++
				continue
			}
			switch messageType {
			case 1, 3, 4:
				recordOrdinal++
				continue
			case 2:
			default:
				return fmt.Errorf("unsupported BGP message type %d", messageType)
			}
			events, err := decodeUpdateEvents(
				peer, eventMicros, body, asnWidth, artifactIndex, recordOrdinal, &stats,
			)
			if err != nil {
				return fmt.Errorf("record %d: %w", recordOrdinal, err)
			}
			for _, event := range events {
				if event.EventMicros < slotStartMicros || event.EventMicros >= slotEndMicros {
					return fmt.Errorf("record %d event outside artifact slot", recordOrdinal)
				}
				if err := emit(event); err != nil {
					return err
				}
				stats.RouteEvents++
			}
			recordOrdinal++
		}
		return nil
	})
	return stats, err
}
