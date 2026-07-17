'''
Author: fyx fuyanxu@mail.bupt.edu.com
Date: 2025-07-25 16:33:12
LastEditors: fyx fuyanxu@mail.bupt.edu.com
LastEditTime: 2025-07-25 16:43:07
FilePath: /bgpdata/Domeye/backend/tests/test_ip_sum.py
Description: 这是默认设置,请设置`customMade`, 打开koroFileHeader查看配置 进行设置: https://github.com/OBKoro1/koro1FileHeader/wiki/%E9%85%8D%E7%BD%AE
'''
from calendar import c
import os
import sys
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.BGPFeature import IPPrefixCalculator

prefixes = set()

with open("/home/bgpdata/Domeye/backend/us_prefix_count.txt", "r") as f:
    for prefix in f.readlines():
        prefix = prefix.strip()
        if prefix:
            prefixes.add(prefix)

print(f"Total prefixes: {len(prefixes)}")

calculator = IPPrefixCalculator()
start = time.time()
print(calculator.calculate_ip_count(prefixes))
end = time.time()
print(f"Calculation time: {end - start} seconds")
