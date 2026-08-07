package replay

import (
	"bufio"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"hash"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

type RouteMetricStoreFile struct {
	Path          string `json:"path"`
	Role          string `json:"role"`
	DateUTC       string `json:"date_utc,omitempty"`
	RowCount      int64  `json:"row_count"`
	SizeBytes     int64  `json:"size_bytes"`
	SHA256        string `json:"sha256"`
	ContentSHA256 string `json:"content_sha256"`
}

type routeMetricTSVWriter struct {
	relative   string
	role       string
	dateUTC    string
	temporary  string
	file       *os.File
	compressed *gzip.Writer
	buffer     *bufio.Writer
	content    hash.Hash
	rows       int64
}

func validateRouteMetricTSVField(value string) error {
	if strings.ContainsAny(value, "\t\r\n") {
		return fmt.Errorf("route metric TSV field contains a delimiter")
	}
	return nil
}

func newRouteMetricTSVWriter(
	root string,
	relative string,
	role string,
	dateUTC string,
	header []string,
) (*routeMetricTSVWriter, error) {
	if relative == "" || role == "" || len(header) == 0 {
		return nil, fmt.Errorf("route metric TSV identity is required")
	}
	for _, value := range header {
		if err := validateRouteMetricTSVField(value); err != nil {
			return nil, err
		}
	}
	finalPath := filepath.Join(root, filepath.FromSlash(relative))
	if err := os.MkdirAll(filepath.Dir(finalPath), 0o750); err != nil {
		return nil, err
	}
	if _, err := os.Lstat(finalPath); err == nil {
		return nil, fmt.Errorf("immutable route metric TSV already exists")
	} else if !os.IsNotExist(err) {
		return nil, err
	}
	temporary := finalPath + ".tmp"
	if _, err := os.Lstat(temporary); err == nil {
		return nil, fmt.Errorf("unfinished route metric TSV exists")
	} else if !os.IsNotExist(err) {
		return nil, err
	}
	file, err := os.OpenFile(temporary, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o640)
	if err != nil {
		return nil, err
	}
	compressed, err := gzip.NewWriterLevel(file, gzip.BestSpeed)
	if err != nil {
		_ = file.Close()
		return nil, err
	}
	compressed.Header.ModTime = time.Unix(0, 0).UTC()
	compressed.Header.OS = 255
	writer := &routeMetricTSVWriter{
		relative: relative, role: role, dateUTC: dateUTC, temporary: temporary,
		file: file, compressed: compressed, buffer: bufio.NewWriterSize(compressed, 1<<20),
		content: sha256.New(),
	}
	if err := writer.writeRaw(header, false); err != nil {
		writer.Abort()
		return nil, err
	}
	return writer, nil
}

func (writer *routeMetricTSVWriter) writeRaw(fields []string, count bool) error {
	for _, value := range fields {
		if err := validateRouteMetricTSVField(value); err != nil {
			return err
		}
	}
	line := strings.Join(fields, "\t") + "\n"
	if _, err := writer.buffer.WriteString(line); err != nil {
		return err
	}
	if _, err := writer.content.Write([]byte(line)); err != nil {
		return err
	}
	if count {
		writer.rows++
	}
	return nil
}

func (writer *routeMetricTSVWriter) Write(fields []string) error {
	return writer.writeRaw(fields, true)
}

func (writer *routeMetricTSVWriter) Abort() {
	if writer == nil {
		return
	}
	if writer.buffer != nil {
		_ = writer.buffer.Flush()
	}
	if writer.compressed != nil {
		_ = writer.compressed.Close()
	}
	if writer.file != nil {
		_ = writer.file.Close()
	}
}

func (writer *routeMetricTSVWriter) Close(root string) (RouteMetricStoreFile, error) {
	result := RouteMetricStoreFile{
		Path: writer.relative, Role: writer.role, DateUTC: writer.dateUTC,
		RowCount: writer.rows, ContentSHA256: hex.EncodeToString(writer.content.Sum(nil)),
	}
	if err := writer.buffer.Flush(); err != nil {
		return result, err
	}
	if err := writer.compressed.Close(); err != nil {
		return result, err
	}
	if err := writer.file.Sync(); err != nil {
		return result, err
	}
	if err := writer.file.Close(); err != nil {
		return result, err
	}
	sha, size, err := sha256File(writer.temporary)
	if err != nil {
		return result, err
	}
	result.SHA256, result.SizeBytes = sha, size
	finalPath := filepath.Join(root, filepath.FromSlash(writer.relative))
	if err := os.Rename(writer.temporary, finalPath); err != nil {
		return result, err
	}
	return result, nil
}

func metricInt(value int64) string {
	return strconv.FormatInt(value, 10)
}

func routeMetricFields(
	candidateID string,
	datasetID string,
	projectionID string,
	row RouteMetricRow,
) []string {
	countryCode := row.CountryCode
	if countryCode == "" {
		countryCode = `\N`
	}
	return []string{
		candidateID, datasetID, projectionID, row.StatePointUTC,
		row.SubjectType, row.SubjectID, countryCode, row.SampleEncoding,
		metricInt(row.BaselineRouteStateCountV4), metricInt(row.BaselineRouteStateCountV6),
		metricInt(row.CohortVisibleRouteStateCountV4), metricInt(row.CohortVisibleRouteStateCountV6),
		metricInt(row.CurrentVisibleRouteStateCountV4), metricInt(row.CurrentVisibleRouteStateCountV6),
		metricInt(row.AnnouncementCountV4), metricInt(row.AnnouncementCountV6),
		metricInt(row.WithdrawalCountV4), metricInt(row.WithdrawalCountV6),
		row.CohortVisibilityStateV4, row.CohortVisibilityStateV6,
	}
}

var routeMetricHeader = []string{
	"candidate_id", "metric_dataset_id", "projection_id", "state_point_utc",
	"subject_type", "subject_id", "country_code", "sample_encoding",
	"baseline_route_state_count_v4", "baseline_route_state_count_v6",
	"cohort_visible_route_state_count_v4", "cohort_visible_route_state_count_v6",
	"current_visible_route_state_count_v4", "current_visible_route_state_count_v6",
	"announcement_count_v4", "announcement_count_v6",
	"withdrawal_count_v4", "withdrawal_count_v6",
	"cohort_visibility_state_v4", "cohort_visibility_state_v6",
}

func verifyRouteMetricStoreFile(root string, expected RouteMetricStoreFile) error {
	path := filepath.Join(root, filepath.FromSlash(expected.Path))
	sha, size, err := sha256File(path)
	if err != nil {
		return err
	}
	if sha != expected.SHA256 || size != expected.SizeBytes {
		return fmt.Errorf("route metric file identity mismatch: %s", expected.Path)
	}
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	decoded, err := gzip.NewReader(file)
	if err != nil {
		return err
	}
	content := sha256.New()
	reader := bufio.NewReader(io.TeeReader(decoded, content))
	lines := int64(0)
	for {
		_, err := reader.ReadString('\n')
		if err == io.EOF {
			break
		}
		if err != nil {
			return err
		}
		lines++
	}
	if err := decoded.Close(); err != nil {
		return err
	}
	if lines != expected.RowCount+1 || hex.EncodeToString(content.Sum(nil)) != expected.ContentSHA256 {
		return fmt.Errorf("route metric TSV population or content mismatch: %s", expected.Path)
	}
	return nil
}
