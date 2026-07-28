package replay

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"regexp"
	"sort"
)

const UnknownCountryCode = "__UNKNOWN__"

var countryCodePattern = regexp.MustCompile(`^[A-Z]{2}$`)

// GlobalCountryMapping 把同一份冻结 ASN mapping 展开为全部国家及显式未知桶。
// Country ID 只用于单次引擎内部压缩；对外身份始终使用 CountryCode。
type GlobalCountryMapping struct {
	byASN            map[uint32]uint16
	codes            []string
	codeToID         map[string]uint16
	MappingVersion   string
	CompatibleSHA256 string
	RevisedSHA256    string
	ConflictCount    int
	UnknownRowCount  int
}

func LoadGlobalCountryMapping(
	compatiblePath, revisedPath string,
) (*GlobalCountryMapping, error) {
	var compatible compatibleMappingDocument
	compatibleRaw, err := readJSON(compatiblePath, &compatible)
	if err != nil {
		return nil, err
	}
	var revised revisedMappingDocument
	revisedRaw, err := readJSON(revisedPath, &revised)
	if err != nil {
		return nil, err
	}
	if compatible.SchemaVersion != "as-country-mapping-snapshot/v1" ||
		revised.SchemaVersion != "iran-revised-mapping-delta/v1" ||
		compatible.TargetCountry != "IR" || revised.TargetCountry != "IR" {
		return nil, fmt.Errorf("mapping documents are not the frozen global base plus IR delta")
	}

	conflicts := make(map[uint32]struct{}, len(compatible.Conflicts))
	for _, row := range compatible.Conflicts {
		conflicts[row.ASN] = struct{}{}
	}
	byASNCode := make(map[uint32]string, len(compatible.Rows))
	codes := make(map[string]struct{})
	unknownRows := 0
	for _, row := range compatible.Rows {
		if _, conflict := conflicts[row.ASN]; conflict || row.CountryCode == nil {
			unknownRows++
			continue
		}
		code := *row.CountryCode
		if !countryCodePattern.MatchString(code) {
			return nil, fmt.Errorf("mapping ASN %d has invalid country code %q", row.ASN, code)
		}
		byASNCode[row.ASN] = code
		codes[code] = struct{}{}
	}
	for _, row := range revised.Rows {
		if row.CountryCode != "IR" || row.DelegatedDate > "20260227" {
			return nil, fmt.Errorf("revised mapping row violates event cutoff")
		}
		if _, exists := byASNCode[row.ASN]; !exists {
			if _, conflict := conflicts[row.ASN]; !conflict {
				return nil, fmt.Errorf("revised ASN %d absent from compatible base", row.ASN)
			}
		}
		byASNCode[row.ASN] = "IR"
		codes["IR"] = struct{}{}
	}

	sortedCodes := make([]string, 0, len(codes)+1)
	sortedCodes = append(sortedCodes, UnknownCountryCode)
	for code := range codes {
		sortedCodes = append(sortedCodes, code)
	}
	sort.Strings(sortedCodes[1:])
	if len(sortedCodes) > int(^uint16(0)) {
		return nil, fmt.Errorf("country population exceeds uint16")
	}
	codeToID := make(map[string]uint16, len(sortedCodes))
	for index, code := range sortedCodes {
		codeToID[code] = uint16(index)
	}
	byASN := make(map[uint32]uint16, len(byASNCode))
	for asn, code := range byASNCode {
		byASN[asn] = codeToID[code]
	}

	digest := sha256.New()
	_, _ = digest.Write(compatibleRaw)
	_, _ = digest.Write([]byte{0})
	_, _ = digest.Write(revisedRaw)
	compatibleDigest := sha256.Sum256(compatibleRaw)
	revisedDigest := sha256.Sum256(revisedRaw)
	return &GlobalCountryMapping{
		byASN:            byASN,
		codes:            sortedCodes,
		codeToID:         codeToID,
		MappingVersion:   hex.EncodeToString(digest.Sum(nil)),
		CompatibleSHA256: hex.EncodeToString(compatibleDigest[:]),
		RevisedSHA256:    hex.EncodeToString(revisedDigest[:]),
		ConflictCount:    len(conflicts),
		UnknownRowCount:  unknownRows,
	}, nil
}

func newSyntheticGlobalCountryMapping(rows map[uint32]string) *GlobalCountryMapping {
	codes := map[string]struct{}{}
	for _, code := range rows {
		if code != "" {
			codes[code] = struct{}{}
		}
	}
	sortedCodes := []string{UnknownCountryCode}
	for code := range codes {
		sortedCodes = append(sortedCodes, code)
	}
	sort.Strings(sortedCodes[1:])
	codeToID := make(map[string]uint16, len(sortedCodes))
	for index, code := range sortedCodes {
		codeToID[code] = uint16(index)
	}
	byASN := make(map[uint32]uint16, len(rows))
	for asn, code := range rows {
		if code != "" {
			byASN[asn] = codeToID[code]
		}
	}
	return &GlobalCountryMapping{
		byASN: byASN, codes: sortedCodes, codeToID: codeToID,
		MappingVersion: "synthetic-global-mapping",
	}
}

func (mapping *GlobalCountryMapping) CountryID(asn uint32) uint16 {
	if mapping == nil {
		return 0
	}
	return mapping.byASN[asn]
}

func (mapping *GlobalCountryMapping) CountryCode(id uint16) string {
	if mapping == nil || int(id) >= len(mapping.codes) {
		return UnknownCountryCode
	}
	return mapping.codes[id]
}

func (mapping *GlobalCountryMapping) IDForCode(code string) (uint16, bool) {
	if mapping == nil {
		return 0, false
	}
	id, exists := mapping.codeToID[code]
	return id, exists
}

func (mapping *GlobalCountryMapping) CountryCodes() []string {
	if mapping == nil {
		return nil
	}
	return append([]string(nil), mapping.codes...)
}
