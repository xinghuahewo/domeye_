package replay

import (
	"fmt"
	"net/netip"
)

// prefixCoverage 维护唯一地址并集。IPv4 返回唯一地址数；IPv6 只投影
// 到前 48 bit，返回唯一 /48 等价块数。它不因重叠前缀、观察方向
// 或 BGP 会话重复计数。
type prefixCoverage struct {
	familyBits int
	root       *prefixCoverageNode
	members    map[netip.Prefix]int
}

type prefixCoverageNode struct {
	direct   int
	covered  uint64
	children [2]*prefixCoverageNode
}

func newPrefixCoverage(afi uint8) (*prefixCoverage, error) {
	bits := 0
	switch afi {
	case 4:
		bits = 32
	case 6:
		bits = 48
	default:
		return nil, fmt.Errorf("prefix coverage AFI must be 4 or 6")
	}
	return &prefixCoverage{
		familyBits: bits,
		root:       &prefixCoverageNode{},
		members:    make(map[netip.Prefix]int),
	}, nil
}

func (coverage *prefixCoverage) Add(prefix netip.Prefix) error {
	return coverage.adjust(prefix, 1)
}

func (coverage *prefixCoverage) Remove(prefix netip.Prefix) error {
	return coverage.adjust(prefix, -1)
}

func (coverage *prefixCoverage) Covered() uint64 {
	if coverage == nil || coverage.root == nil {
		return 0
	}
	return coverage.root.covered
}

func (coverage *prefixCoverage) adjust(prefix netip.Prefix, delta int) error {
	if coverage == nil || coverage.root == nil || (delta != 1 && delta != -1) {
		return fmt.Errorf("invalid prefix coverage adjustment")
	}
	prefix = prefix.Masked()
	if !prefix.IsValid() || (prefix.Addr().Is4() && coverage.familyBits != 32) ||
		(prefix.Addr().Is6() && coverage.familyBits != 48) {
		return fmt.Errorf("prefix coverage address family mismatch")
	}
	current := coverage.members[prefix]
	if current+delta < 0 {
		return fmt.Errorf("prefix coverage membership underflow")
	}
	if current+delta == 0 {
		delete(coverage.members, prefix)
	} else {
		coverage.members[prefix] = current + delta
	}
	depth := prefix.Bits()
	if depth > coverage.familyBits {
		depth = coverage.familyBits
	}
	path := make([]*prefixCoverageNode, 1, depth+1)
	path[0] = coverage.root
	node := coverage.root
	for at := 0; at < depth; at++ {
		branch := prefixCoverageBit(prefix.Addr(), at)
		if node.children[branch] == nil {
			if delta < 0 {
				return fmt.Errorf("prefix coverage path is missing")
			}
			node.children[branch] = &prefixCoverageNode{}
		}
		node = node.children[branch]
		path = append(path, node)
	}
	node.direct += delta
	if node.direct < 0 {
		return fmt.Errorf("prefix coverage node underflow")
	}
	for at := len(path) - 1; at >= 0; at-- {
		currentNode := path[at]
		if currentNode.direct > 0 {
			currentNode.covered = uint64(1) << uint(coverage.familyBits-at)
			continue
		}
		currentNode.covered = 0
		for _, child := range currentNode.children {
			if child != nil {
				currentNode.covered += child.covered
			}
		}
	}
	return nil
}

func prefixCoverageBit(address netip.Addr, at int) int {
	if address.Is4() {
		raw := address.As4()
		return int((raw[at/8] >> uint(7-at%8)) & 1)
	}
	raw := address.As16()
	return int((raw[at/8] >> uint(7-at%8)) & 1)
}
