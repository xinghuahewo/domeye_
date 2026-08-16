package replay

import (
	"net/netip"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestEventPathPartitionCandidates(t *testing.T) {
	cases := []struct {
		value string
		want  []int
	}{
		{"2026-02-24T00:00:00Z", []int{0, 1}},
		{"2026-02-24T00:00:01Z", []int{1}},
		{"2026-02-24T00:05:00Z", []int{2}},
		{"2026-03-10T23:59:59Z", []int{4320}},
	}
	for _, item := range cases {
		got, err := eventPathPartitionCandidates(item.value, 4321)
		if err != nil || len(got) != len(item.want) {
			t.Fatalf("partition candidates %s: got=%v err=%v", item.value, got, err)
		}
		for index := range got {
			if got[index] != item.want[index] {
				t.Fatalf("partition candidates %s: got=%v want=%v", item.value, got, item.want)
			}
		}
	}
	if _, err := eventPathPartitionCandidates("2026-03-11T00:00:00Z", 4321); err == nil {
		t.Fatal("exclusive window end was accepted")
	}
}

func TestPathAffectedParentsRequiresOrderedKnownOrigin(t *testing.T) {
	affected := map[uint32]struct{}{64500: {}, 64501: {}, 64510: {}}
	ordered := newASPathSnapshot([]ASPathSegment{{SegmentType: asSequenceSegment, ASNs: []uint32{64500, 64501, 64502}}})
	parents, ambiguous, err := pathAffectedParents(ordered, 64502, affected)
	if err != nil || ambiguous || len(parents) != 2 || parents[0] != 64500 || parents[1] != 64501 {
		t.Fatalf("ordered relationship mismatch: parents=%v ambiguous=%v err=%v", parents, ambiguous, err)
	}
	if related, uncertain := orderedPathRelationship(ordered, 64500, 64502); !related || uncertain {
		t.Fatalf("ordered path did not produce relationship: related=%v uncertain=%v", related, uncertain)
	}
	unordered := newASPathSnapshot([]ASPathSegment{
		{SegmentType: asSequenceSegment, ASNs: []uint32{64496}},
		{SegmentType: asSetSegment, ASNs: []uint32{64510, 64511}},
	})
	parents, ambiguous, err = pathAffectedParents(unordered, 64511, affected)
	if err != nil || !ambiguous || len(parents) != 0 {
		t.Fatalf("AS_SET created or hid an ordered relationship: parents=%v ambiguous=%v err=%v", parents, ambiguous, err)
	}
	if related, uncertain := orderedPathRelationship(unordered, 64510, 64511); related || !uncertain {
		t.Fatalf("AS_SET was treated as ordered: related=%v uncertain=%v", related, uncertain)
	}
	if _, _, err := pathAffectedParents(ordered, 64503, affected); err == nil {
		t.Fatal("path tail and observed origin mismatch was accepted")
	}
}

func TestRelationCoverageUsesAssociatedInterruptedPrefixesOnly(t *testing.T) {
	ipv4, _ := newPrefixCoverage(4)
	ipv6, _ := newPrefixCoverage(6)
	relation := &eventPathRelationState{CurrentIPv4: ipv4, CurrentIPv6: ipv6}
	v4 := eventPrefixDefinition{Prefix: mustEventPrefix(t, "192.0.2.0/24"), AddressFamily: "ipv4"}
	v6 := eventPrefixDefinition{Prefix: mustEventPrefix(t, "2001:db8::/32"), AddressFamily: "ipv6"}
	if err := adjustRelationPrefix(relation, v4, eventPrefixPartial, 1); err != nil {
		t.Fatal(err)
	}
	if err := adjustRelationPrefix(relation, v6, eventPrefixComplete, 1); err != nil {
		t.Fatal(err)
	}
	if relation.CurrentInterrupted != 2 || relation.CurrentIPv4.Covered() != 256 ||
		relation.CurrentIPv6.Covered() != 65536 {
		t.Fatalf("relation coverage mismatch: %+v", relation)
	}
	if err := adjustRelationPrefix(relation, v4, eventPrefixNormal, 1); err != nil {
		t.Fatal(err)
	}
	if relation.CurrentInterrupted != 2 || relation.CurrentIPv4.Covered() != 256 {
		t.Fatal("normal prefix changed interrupted coverage")
	}
	if err := adjustRelationPrefix(relation, v4, eventPrefixPartial, -1); err != nil {
		t.Fatal(err)
	}
	if relation.CurrentInterrupted != 1 || relation.CurrentIPv4.Covered() != 0 {
		t.Fatal("interrupted prefix removal did not close coverage")
	}
}

func TestASStaticSnapshotKeepsMissingUnknownAndFirstRow(t *testing.T) {
	path := filepath.Join(t.TempDir(), "as_entity.csv")
	content := strings.Join([]string{
		"asn,as_name,org_name,org_name_cn,type,type_cn",
		"64500,EXAMPLE,Example Org,示例组织,ISP,互联网服务提供商",
		"64500,CHANGED,Changed Org,变更组织,Changed,变更",
		"64501,,,,,",
	}, "\n") + "\n"
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	profiles, err := loadEventASStaticProfiles(path, map[uint32]struct{}{64500: {}, 64501: {}, 64502: {}})
	if err != nil {
		t.Fatal(err)
	}
	if profiles[64500].ASName == nil || *profiles[64500].ASName != "EXAMPLE" ||
		profiles[64500].Organization == nil || *profiles[64500].Organization != "示例组织" ||
		profiles[64500].Nature == nil || *profiles[64500].Nature != "互联网服务提供商" {
		t.Fatalf("first AS row or preferred fields changed: %+v", profiles[64500])
	}
	for _, asn := range []uint32{64501, 64502} {
		profile := profiles[asn]
		if profile.ASName != nil || profile.Organization != nil || profile.Nature != nil ||
			profile.NameState != "unknown" || profile.OrgState != "unknown" || profile.NatureState != "unknown" {
			t.Fatalf("missing AS attributes were fabricated: %+v", profile)
		}
	}
}

func mustEventPrefix(t *testing.T, value string) netip.Prefix {
	t.Helper()
	prefix, err := netip.ParsePrefix(value)
	if err != nil {
		t.Fatal(err)
	}
	return prefix
}
