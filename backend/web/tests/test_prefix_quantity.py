import os
import sys


current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, '..', '..'))
sys.path.append(backend_dir)

from utils.prefix_quantity import calculate_c_segments_count, calculate_v6_48_segments_count


def test_calculate_c_segments_count_deduplicates_overlap():
    prefixes = ['10.0.0.0/24', '10.0.0.0/23', '10.0.1.0/24']

    assert calculate_c_segments_count(prefixes) == 2


def test_calculate_v6_48_segments_count_handles_mixed_ranges():
    prefixes = ['2001:db8::/47', '2001:db8:1::/48', '2001:db8:2::/48']

    assert calculate_v6_48_segments_count(prefixes) == 3


def test_calculate_c_segments_count_ignores_default_route():
    prefixes = ['0.0.0.0/0', '10.0.0.0/24']

    assert calculate_c_segments_count(prefixes) == 1


def test_calculate_v6_48_segments_count_ignores_default_route():
    prefixes = ['::/0', '2001:db8::/48']

    assert calculate_v6_48_segments_count(prefixes) == 1
