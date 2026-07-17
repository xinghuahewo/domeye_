#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
性能测试脚本：测试三种 deal_outage 方法的性能差异
测试前缀/AS中断时序图性能问题
"""

import pandas as pd
import numpy as np
import time
import sys
import os
from collections import defaultdict

# 添加路径以便导入项目代码
sys.path.append('/home/bgpdata/Domeye/backend')
from utils.get_event import deal_outage_origin, deal_outage, deal_outage_vectorized

if __name__ == '__main__':
    import pandas as pd
    import time
    import psycopg2
    import traceback
    from config.database import DATABASE, PORT, USER, PASSWORD, HOST

    df = pd.read_csv("/home/bgpdata/Domeye/backend/info/ip_bgp_entity.csv", keep_default_na=False, usecols=['prefix', 'route', 'bgp', 'name', 'domain_num', 'domain_auth_num', 'domain', 'domain_auth'])
    df.drop_duplicates(subset=['prefix'], keep='first', inplace=True)
    df_new = df.set_index('prefix', drop=True, append=False, inplace=False, verify_integrity=False)
    prefix_info = df_new.to_dict(orient='index')

    prefixes = prefix_info.keys()

    conn = psycopg2.connect(
        dbname=DATABASE,
        user=USER,
        password=PASSWORD,
        host=HOST,
        port=PORT
    )
    def get_prefix_outage_by_interval(conn, start_time, end_time, country, asn, table_name):
        """
        获取与指定时间范围重叠的前缀中断记录。
        """
        if country:
            sql = f"""
            SELECT DISTINCT prefix, s_time, e_time
                FROM {table_name}
                WHERE country = '{country}'
                AND (
                    s_time <= '{end_time}'::timestamp
                    AND (e_time >= '{start_time}'::timestamp OR e_time IS NULL)
                )
            """
        else:
            sql = f"""
            SELECT DISTINCT prefix, s_time, e_time
                FROM {table_name}
                WHERE asn = '{asn}'
                AND (
                    s_time <= '{end_time}'::timestamp
                    AND (e_time >= '{start_time}'::timestamp OR e_time IS NULL)
                )
            """
        try:
            # df = pd.read_sql(sql, conn)
            cursor = conn.cursor()
            cursor.execute(sql)
            data = cursor.fetchall()
            df = pd.DataFrame(data, columns=['prefix', 's_time', 'e_time'])
        except Exception as e:
            conn.rollback()
            print(f"获取前缀中断记录失败: {e}")
            print(traceback.format_exc())
            return []
        finally:
            cursor.close()
        return df

    start = time.time()
    result = get_prefix_outage_by_interval(
        conn=conn,
        start_time='2025-06-12 00:00:00',
        end_time='2025-06-30 23:59:59',
        country='中国',
        asn=None,
        table_name='prefix_outage_202506'
    )
    end = time.time()
    print("查询耗时:", end - start)
    print(len(result))

    # 处理结果
    # start = time.time()
    # result1 = deal_outage_origin(
    #     df=result,
    #     type='prefix',
    #     start_time='2025-06-12 00:00:00',
    #     end_time='2025-06-30 23:59:59',
    #     prefixes=prefixes,
    #     interval_minutes=3
    # )
    # end = time.time()
    # print("deal_outage_origin处理耗时:", end - start)
    
    start = time.time()
    result2 = deal_outage(
        df=result,
        type='prefix',
        start_time='2025-06-12 00:00:00',
        end_time='2025-06-30 23:59:59',
        prefixes=prefixes,
        interval_minutes=3
    )
    end = time.time()
    print("deal_outage处理耗时:", end - start)

    result1 = result2.copy()  # 使用优化方法的结果作为基准
    start = time.time()
    result3 = deal_outage_vectorized(
        df=result,
        type='prefix',
        start_time='2025-06-12 00:00:00',
        end_time='2025-06-30 23:59:59',
        prefixes=prefixes,
        interval_minutes=3
    )
    end = time.time()

    print("deal_outage_vectorized处理耗时:", end - start)

    ### 验证结果是否相同
    def compare_json_results(baseline_result, optimized_result, vectorized_result):
        """
        详细比较优化方法和向量化方法与基准结果的一致性
        """
        print("\n" + "="*50)
        print("JSON结果一致性验证（以原始方法为基准）")
        print("="*50)
        
        # 基本信息检查
        print(f"原始方法(基准) 长度: {len(baseline_result)}")
        print(f"优化方法 长度: {len(optimized_result)}")
        print(f"向量化方法 长度: {len(vectorized_result)}")
        
        results = [
            ("优化方法", optimized_result),
            ("向量化方法", vectorized_result)
        ]
        
        all_consistent = True
        
        for method_name, test_result in results:
            print(f"\n--- {method_name} vs 基准结果 ---")
            
            # 1. 长度检查
            if len(baseline_result) != len(test_result):
                print(f"❌ {method_name}长度不一致: 基准{len(baseline_result)} vs {method_name}{len(test_result)}")
                all_consistent = False
                continue
            
            # 2. 逐项对比
            mismatches = []
            for i in range(len(baseline_result)):
                baseline_item = baseline_result[i]
                test_item = test_result[i]
                
                # 比较时间戳
                if baseline_item['time_slot'] != test_item['time_slot']:
                    mismatches.append(f"时间点 {i}: 时间戳不一致")
                    continue
                
                # 比较中断数量
                baseline_count = baseline_item['outage_count']
                test_count = test_item['outage_count']
                
                if baseline_count != test_count:
                    mismatches.append(f"时间点 {i} ({baseline_item['time_slot']}): 中断数不一致 基准{baseline_count} vs {method_name}{test_count}")
            
            # 3. 输出结果
            if not mismatches:
                print(f"✅ {method_name}与基准结果完全一致")
                print(f"   - 总时间点数: {len(test_result)}")
                print(f"   - 总中断数统计: {sum(item['outage_count'] for item in test_result)}")
            else:
                print(f"❌ {method_name}发现 {len(mismatches)} 个不匹配项")
                all_consistent = False
                # 显示前5个不匹配的详情
                for mismatch in mismatches[:5]:
                    print(f"   - {mismatch}")
                if len(mismatches) > 5:
                    print(f"   - ... 还有 {len(mismatches)-5} 个不匹配项")
        
        return all_consistent
    
    # 4. 统计信息对比
    def print_statistics_comparison(baseline_result, optimized_result, vectorized_result):
        """打印统计信息对比"""
        print("\n统计信息对比:")
        
        def get_stats(result):
            counts = [item['outage_count'] for item in result]
            return {
                'total': sum(counts),
                'max': max(counts),
                'min': min(counts),
                'avg': sum(counts) / len(counts) if counts else 0,
                'non_zero': sum(1 for c in counts if c > 0)
            }
        
        baseline_stats = get_stats(baseline_result)
        optimized_stats = get_stats(optimized_result)
        vectorized_stats = get_stats(vectorized_result)
        
        print(f"{'指标':<15} {'原始方法(基准)':<15} {'优化方法':<12} {'向量化方法':<12}")
        print("-" * 60)
        print(f"{'总中断数':<15} {baseline_stats['total']:<15} {optimized_stats['total']:<12} {vectorized_stats['total']:<12}")
        print(f"{'最大值':<15} {baseline_stats['max']:<15} {optimized_stats['max']:<12} {vectorized_stats['max']:<12}")
        print(f"{'最小值':<15} {baseline_stats['min']:<15} {optimized_stats['min']:<12} {vectorized_stats['min']:<12}")
        print(f"{'平均值':<15} {baseline_stats['avg']:<15.2f} {optimized_stats['avg']:<12.2f} {vectorized_stats['avg']:<12.2f}")
        print(f"{'非零时间点':<15} {baseline_stats['non_zero']:<15} {optimized_stats['non_zero']:<12} {vectorized_stats['non_zero']:<12}")
        
        # 检查统计数据是否一致
        stats_consistent = True
        if baseline_stats['total'] != optimized_stats['total']:
            print(f"⚠️  优化方法总中断数不一致: {baseline_stats['total']} vs {optimized_stats['total']}")
            stats_consistent = False
        if baseline_stats['total'] != vectorized_stats['total']:
            print(f"⚠️  向量化方法总中断数不一致: {baseline_stats['total']} vs {vectorized_stats['total']}")
            stats_consistent = False
        
        if stats_consistent:
            print("✅ 统计数据完全一致")
        
        return stats_consistent
    
    # 执行比较
    is_consistent = compare_json_results(result1, result2, result3)
    stats_consistent = print_statistics_comparison(result1, result2, result3)
    
    # 5. 抽样检查 - 显示部分结果
    print(f"\n抽样检查 (前5个和后5个时间点):")
    print("-" * 85)
    print(f"{'时间':<20} {'原始方法(基准)':<15} {'优化方法':<12} {'向量化方法':<12} {'差异标记':<10}")
    print("-" * 85)
    
    # 前5个
    for i in range(min(5, len(result1))):
        time_str = result1[i]['time_slot'][:19]  # 截取到秒
        baseline_count = result1[i]['outage_count']
        optimized_count = result2[i]['outage_count']
        vectorized_count = result3[i]['outage_count']
        
        # 标记差异
        diff_marker = ""
        if baseline_count != optimized_count:
            diff_marker += "O"  # Optimized different
        if baseline_count != vectorized_count:
            diff_marker += "V"  # Vectorized different
        if not diff_marker:
            diff_marker = "✓"  # All same
            
        print(f"{time_str:<20} {baseline_count:<15} {optimized_count:<12} {vectorized_count:<12} {diff_marker:<10}")
    
    if len(result1) > 10:
        print("...")
        # 后5个
        for i in range(max(0, len(result1)-5), len(result1)):
            time_str = result1[i]['time_slot'][:19]
            baseline_count = result1[i]['outage_count']
            optimized_count = result2[i]['outage_count']
            vectorized_count = result3[i]['outage_count']
            
            diff_marker = ""
            if baseline_count != optimized_count:
                diff_marker += "O"
            if baseline_count != vectorized_count:
                diff_marker += "V"
            if not diff_marker:
                diff_marker = "✓"
                
            print(f"{time_str:<20} {baseline_count:<15} {optimized_count:<12} {vectorized_count:<12} {diff_marker:<10}")
    
    print(f"\n差异标记说明: ✓=一致, O=优化方法与基准不同, V=向量化方法与基准不同")
    print(f"\n🎯 最终结论: {'结果一致 ✅' if is_consistent and stats_consistent else '结果不一致 ❌'}")


    # print(result1)
    # print(result2)
    # print(result3)
