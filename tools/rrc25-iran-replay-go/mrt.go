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

	actionWithdraw = uint8(1)
	actionAnnounce = uint8(2)
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

func parseASPath(value []byte, asnWidth int) (originResult, error) {
	at := 0
	var result originResult
	var lastType uint8
	for at < len(value) {
		if at+2 > len(value) {
			return originResult{}, fmt.Errorf("AS_PATH segment header truncated")
		}
		segmentType := value[at]
		count := int(value[at+1])
		at += 2
		if segmentType < 1 || segmentType > 4 || count == 0 ||
			at+count*asnWidth > len(value) {
			return originResult{}, fmt.Errorf("invalid AS_PATH segment")
		}
		lastType = segmentType
		if segmentType == 2 {
			raw := value[at+(count-1)*asnWidth : at+count*asnWidth]
			if asnWidth == 2 {
				result = originResult{Known: true, ASN: uint32(binary.BigEndian.Uint16(raw))}
			} else {
				result = originResult{Known: true, ASN: binary.BigEndian.Uint32(raw)}
			}
		} else {
			result = originResult{}
		}
		at += count * asnWidth
	}
	if lastType != 2 {
		return originResult{}, nil
	}
	return result, nil
}

type parsedAttributes struct {
	Origin          originResult
	OriginSeen      bool
	AS4Origin       originResult
	AS4Seen         bool
	MPAnnounces     map[uint8][]netip.Prefix
	MPWithdraws     map[uint8][]netip.Prefix
	UnknownOptional map[uint8]int64
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

func parseAttributes(raw []byte, asnWidth int) (parsedAttributes, error) {
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
			return result, fmt.Errorf("path attribute %d out of bounds", attributeType)
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
			origin, err := parseASPath(value, asnWidth)
			if err != nil {
				return result, err
			}
			result.Origin, result.OriginSeen = origin, true
		case 14:
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
			origin, err := parseASPath(value, 4)
			if err != nil {
				return result, err
			}
			result.AS4Origin, result.AS4Seen = origin, true
		case 21:
			if flags&0xe0 != 0xc0 && flags&0xe0 != 0xe0 {
				return result, fmt.Errorf("invalid AS_PATHLIMIT flags")
			}
			if len(value) != 5 {
				return result, fmt.Errorf("invalid AS_PATHLIMIT length")
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
			return result, fmt.Errorf("AS4_PATH in four-byte ASN message")
		}
		result.Origin = result.AS4Origin
		result.OriginSeen = true
	}
	return result, nil
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
		parsed, err := parseAttributes(attributes, 4)
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
	Key            RouteKey
	Action         uint8
	OriginKnown    bool
	OriginASN      uint32
	EventMicros    int64
	ArtifactIndex  uint16
	RecordOrdinal  uint32
	ElementOrdinal uint32
}

type UpdateParseStats struct {
	PhysicalRecords int64
	RouteEvents     int64
	Announces       int64
	Withdraws       int64
	UnknownOrigins  int64
	UnknownOptional map[uint8]int64
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
			ArtifactIndex: artifactIndex, RecordOrdinal: recordOrdinal,
			ElementOrdinal: element,
		})
		element++
		stats.Withdraws++
	}
	appendAnnounce := func(afi uint8, prefix netip.Prefix) {
		events = append(events, ParsedEvent{
			Key:    RouteKey{PeerIP: peer.IP, PeerASN: peer.ASN, AFI: afi, Prefix: prefix},
			Action: actionAnnounce, OriginKnown: attributes.Origin.Known,
			OriginASN: attributes.Origin.ASN, EventMicros: eventMicros,
			ArtifactIndex: artifactIndex, RecordOrdinal: recordOrdinal,
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
