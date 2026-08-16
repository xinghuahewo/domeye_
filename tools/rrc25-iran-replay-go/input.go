package replay

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

type selectionDocument struct {
	SchemaVersion string `json:"schema_version"`
	CollectorID   string `json:"collector_id"`
	CountryCode   string `json:"country_code"`
	Roles         struct {
		AnalysisRIBs    []Artifact `json:"analysis_ribs"`
		AnalysisUpdates []Artifact `json:"analysis_updates"`
	} `json:"roles"`
}

func readJSON(path string, target any) ([]byte, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	if err := json.Unmarshal(raw, target); err != nil {
		return nil, fmt.Errorf("decode %s: %w", path, err)
	}
	return raw, nil
}

func ValidateAndSelectInputs(path string) (FixedInputs, error) {
	var document selectionDocument
	if _, err := readJSON(path, &document); err != nil {
		return FixedInputs{}, err
	}
	if document.SchemaVersion != "rrc25-country-outage-input-selection/v1" ||
		document.CollectorID != "rrc25" || document.CountryCode != "IR" {
		return FixedInputs{}, fmt.Errorf("selection identity is not frozen RRC25/IR")
	}
	var rib *Artifact
	for index := range document.Roles.AnalysisRIBs {
		row := &document.Roles.AnalysisRIBs[index]
		if row.RelativePath == ExpectedRIBPath {
			if rib != nil {
				return FixedInputs{}, fmt.Errorf("duplicate frozen 08:00 RIB")
			}
			copy := *row
			rib = &copy
		}
	}
	if rib == nil || rib.FileSHA256 != ExpectedRIBSHA256 ||
		rib.SizeBytes != ExpectedRIBBytes || rib.ArtifactTimeUTC != CatchUpStartUTC {
		return FixedInputs{}, fmt.Errorf("frozen 08:00 RIB identity mismatch")
	}
	if err := validateArtifact(*rib, "rib"); err != nil {
		return FixedInputs{}, err
	}
	updates := make([]Artifact, 0, 84)
	for _, row := range document.Roles.AnalysisUpdates {
		parsed, err := time.Parse(time.RFC3339, row.ArtifactTimeUTC)
		if err != nil {
			return FixedInputs{}, fmt.Errorf("bad update time: %w", err)
		}
		if !parsed.Before(catchUpStart) && parsed.Before(windowEnd) {
			updates = append(updates, row)
		}
	}
	sort.Slice(updates, func(i, j int) bool {
		return updates[i].ArtifactTimeUTC < updates[j].ArtifactTimeUTC
	})
	if len(updates) != 84 {
		return FixedInputs{}, fmt.Errorf("expected 84 updates, got %d", len(updates))
	}
	var updateBytes int64
	for index, row := range updates {
		if err := validateArtifact(row, "update"); err != nil {
			return FixedInputs{}, err
		}
		expected := catchUpStart.Add(time.Duration(index) * 5 * time.Minute)
		if row.ArtifactTimeUTC != expected.Format(time.RFC3339) {
			return FixedInputs{}, fmt.Errorf("update slot %d is not continuous", index)
		}
		updateBytes += row.SizeBytes
	}
	if updateBytes != ExpectedUpdateBytes {
		return FixedInputs{}, fmt.Errorf(
			"update compressed bytes mismatch: %d", updateBytes,
		)
	}
	return FixedInputs{
		RIB:       *rib,
		CatchUp:   append([]Artifact(nil), updates[:25]...),
		Formal:    append([]Artifact(nil), updates[25:]...),
		AllUpdate: updates,
	}, nil
}

func validateArtifact(row Artifact, role string) error {
	if row.ArtifactType != role || row.CollectorID != "rrc25" ||
		row.Compression != "gz" || row.SizeBytes <= 0 {
		return fmt.Errorf("invalid %s artifact fields", role)
	}
	if len(row.FileSHA256) != 64 || len(row.ArtifactID) != len("art_v1_")+32 {
		return fmt.Errorf("invalid artifact identity")
	}
	if strings.HasPrefix(row.RelativePath, "/") ||
		strings.Contains(row.RelativePath, "..") ||
		!strings.HasPrefix(row.RelativePath, "rrc25/") {
		return fmt.Errorf("artifact path escapes collector")
	}
	return nil
}

func ValidateRawFiles(rawRoot string, inputs FixedInputs) error {
	artifacts := make([]Artifact, 0, 85)
	artifacts = append(artifacts, inputs.RIB)
	artifacts = append(artifacts, inputs.AllUpdate...)
	for _, artifact := range artifacts {
		path := filepath.Join(rawRoot, filepath.FromSlash(artifact.RelativePath))
		info, err := os.Lstat(path)
		if err != nil {
			return fmt.Errorf("stat %s: %w", path, err)
		}
		if !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("input is not a regular non-symlink file: %s", path)
		}
		if info.Size() != artifact.SizeBytes {
			return fmt.Errorf("input size mismatch: %s", path)
		}
	}
	return nil
}

type compatibleMappingDocument struct {
	SchemaVersion             string `json:"schema_version"`
	TargetCountry             string `json:"target_country"`
	SourceFileSHA256          string `json:"source_file_sha256"`
	SemanticFingerprintSHA256 string `json:"semantic_fingerprint_sha256"`
	SnapshotID                string `json:"snapshot_id"`
	Rows                      []struct {
		ASN         uint32  `json:"asn"`
		CountryCode *string `json:"country_code"`
	} `json:"rows"`
	Conflicts []struct {
		ASN uint32 `json:"asn"`
	} `json:"conflicts"`
}

type revisedMappingDocument struct {
	SchemaVersion string `json:"schema_version"`
	TargetCountry string `json:"target_country"`
	Rows          []struct {
		ASN           uint32 `json:"asn"`
		CountryCode   string `json:"country_code"`
		DelegatedDate string `json:"delegated_date"`
	} `json:"rows"`
}

type Membership uint8

const (
	MembershipUnknown Membership = iota
	MembershipOther
	MembershipIR
)

type CountryMapping struct {
	byASN          map[uint32]Membership
	CompatibleIR   int
	RevisedIR      int
	MappingVersion string
}

func LoadCountryMapping(compatiblePath, revisedPath string) (*CountryMapping, error) {
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
		return nil, fmt.Errorf("mapping documents are not frozen IR views")
	}
	conflicts := make(map[uint32]struct{}, len(compatible.Conflicts))
	for _, row := range compatible.Conflicts {
		conflicts[row.ASN] = struct{}{}
	}
	result := &CountryMapping{byASN: make(map[uint32]Membership, len(compatible.Rows))}
	for _, row := range compatible.Rows {
		if _, conflict := conflicts[row.ASN]; conflict || row.CountryCode == nil {
			result.byASN[row.ASN] = MembershipUnknown
			continue
		}
		if *row.CountryCode == "IR" {
			result.byASN[row.ASN] = MembershipIR
			result.CompatibleIR++
		} else {
			result.byASN[row.ASN] = MembershipOther
		}
	}
	for _, row := range revised.Rows {
		if row.CountryCode != "IR" || row.DelegatedDate > "20260227" {
			return nil, fmt.Errorf("revised mapping row violates event cutoff")
		}
		if _, exists := result.byASN[row.ASN]; !exists {
			return nil, fmt.Errorf("revised ASN %d absent from compatible base", row.ASN)
		}
		if result.byASN[row.ASN] != MembershipIR {
			result.RevisedIR++
		}
		result.byASN[row.ASN] = MembershipIR
	}
	digest := sha256.New()
	_, _ = digest.Write(compatibleRaw)
	_, _ = digest.Write([]byte{0})
	_, _ = digest.Write(revisedRaw)
	result.MappingVersion = hex.EncodeToString(digest.Sum(nil))
	return result, nil
}

func (mapping *CountryMapping) Membership(asn uint32) Membership {
	if value, exists := mapping.byASN[asn]; exists {
		return value
	}
	return MembershipUnknown
}
