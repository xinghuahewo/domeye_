package replay

import (
	"net/netip"
	"testing"
)

func mustCoveragePrefix(value string) netip.Prefix {
	return netip.MustParsePrefix(value).Masked()
}

func TestPrefixCoverageIPv4UsesUniqueAddressUnion(t *testing.T) {
	coverage, err := newPrefixCoverage(4)
	if err != nil {
		t.Fatal(err)
	}
	if err := coverage.Add(mustCoveragePrefix("10.0.0.0/24")); err != nil {
		t.Fatal(err)
	}
	if err := coverage.Add(mustCoveragePrefix("10.0.0.0/25")); err != nil {
		t.Fatal(err)
	}
	if got := coverage.Covered(); got != 256 {
		t.Fatalf("overlapping IPv4 prefixes duplicated addresses: %d", got)
	}
	if err := coverage.Add(mustCoveragePrefix("10.0.1.0/24")); err != nil {
		t.Fatal(err)
	}
	if got := coverage.Covered(); got != 512 {
		t.Fatalf("unexpected IPv4 union: %d", got)
	}
	if err := coverage.Remove(mustCoveragePrefix("10.0.0.0/24")); err != nil {
		t.Fatal(err)
	}
	if got := coverage.Covered(); got != 384 {
		t.Fatalf("nested IPv4 member was not preserved: %d", got)
	}
}

func TestPrefixCoverageIPv6UsesUniqueSlash48Blocks(t *testing.T) {
	coverage, err := newPrefixCoverage(6)
	if err != nil {
		t.Fatal(err)
	}
	if err := coverage.Add(mustCoveragePrefix("2001:db8::/47")); err != nil {
		t.Fatal(err)
	}
	if err := coverage.Add(mustCoveragePrefix("2001:db8::/64")); err != nil {
		t.Fatal(err)
	}
	if got := coverage.Covered(); got != 2 {
		t.Fatalf("IPv6 /48 equivalents were duplicated: %d", got)
	}
	if err := coverage.Remove(mustCoveragePrefix("2001:db8::/47")); err != nil {
		t.Fatal(err)
	}
	if got := coverage.Covered(); got != 1 {
		t.Fatalf("more-specific IPv6 prefix must retain one /48 block: %d", got)
	}
}

func TestPrefixCoverageRejectsUnderflowAndFamilyDrift(t *testing.T) {
	coverage, _ := newPrefixCoverage(4)
	if err := coverage.Remove(mustCoveragePrefix("192.0.2.0/24")); err == nil {
		t.Fatal("expected membership underflow rejection")
	}
	if err := coverage.Add(mustCoveragePrefix("2001:db8::/48")); err == nil {
		t.Fatal("expected address-family rejection")
	}
}
