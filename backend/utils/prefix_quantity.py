import ipaddress
from typing import Iterable


def calculate_c_segments_count(prefixes: Iterable[str]) -> int:
    """Count unique IPv4 /24 blocks covered by the given prefixes."""
    unique_c_segments_ints = set()

    for prefix in prefixes:
        try:
            parts = prefix.split('/')
            if len(parts) != 2:
                continue
            ip_str, prefix_len_str = parts
            prefixlen = int(prefix_len_str)
            if prefixlen == 0:
                continue

            octets = ip_str.split('.')
            ip_int = (int(octets[0]) << 24) | (int(octets[1]) << 16) | (int(octets[2]) << 8) | int(octets[3])

            mask = (0xFFFFFFFF << (32 - prefixlen)) & 0xFFFFFFFF
            start_ip = ip_int & mask
            num_addresses = 1 << (32 - prefixlen)
            end_ip = start_ip + num_addresses - 1

            first_c_start = start_ip & 0xFFFFFF00
            last_c_start = end_ip & 0xFFFFFF00
            unique_c_segments_ints.update(range(first_c_start, last_c_start + 1, 256))
        except (ValueError, TypeError):
            continue

    return len(unique_c_segments_ints)


def calculate_v6_48_segments_count(prefixes: Iterable[str]) -> int:
    """Count unique IPv6 /48 blocks covered by the given prefixes."""
    intervals = []
    for prefix in prefixes:
        try:
            net = ipaddress.ip_network(prefix, strict=False)
            if net.version != 6:
                continue
            prefixlen = int(net.prefixlen)
            if prefixlen == 0:
                continue
            base_48 = int(net.network_address) >> 80
            if prefixlen >= 48:
                intervals.append((base_48, base_48))
            else:
                blocks = 1 << (48 - prefixlen)
                intervals.append((base_48, base_48 + blocks - 1))
        except Exception:
            continue

    if not intervals:
        return 0

    intervals.sort(key=lambda item: item[0])
    merged_total = 0
    current_start, current_end = intervals[0]
    for start, end in intervals[1:]:
        if start <= current_end + 1:
            if end > current_end:
                current_end = end
            continue
        merged_total += current_end - current_start + 1
        current_start, current_end = start, end
    merged_total += current_end - current_start + 1
    return merged_total
