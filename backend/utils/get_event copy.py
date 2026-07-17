import os
import re
import sys
import time
import math
import datetime
import psycopg2  # 与postgreSQL数据库进行交互
import traceback
from dateutil.relativedelta import relativedelta
from psycopg2 import extras
import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import *
from database.feature_asn import get_as_feature_db
# 当前: 显示近一周的信息
"""
  查询数据库获取具体信息
"""


def get_event_info(detail_url, conn):
    # detail_url: <event_type>/<start_time>/<problem>/<int:event_id>
    detail_url_fields = detail_url.split('/')
    event_type, start_time, problem, event_id, source = detail_url_fields[0], detail_url_fields[1], detail_url_fields[2], detail_url_fields[3], detail_url_fields[4]
    year, month = start_time[0:4], start_time[5:7]
    if event_type == 'hijack':
        table = event_type + '_' + '{}{}'.format(year, month)
        prefix = problem.replace('-', '/')
        row = get_hijack_de(conn=conn, table=table, prefix=prefix, hijack_id=event_id, source=source)
        d = dict()
        hijacked_as = ''
        t = ''
        for r in row:
            d['event_id'] = event_id
            d['hijacked_prefix'] = prefix
            d['hijacked_as'] = r['hijacked_as']
            d['hijacked_as_name'] = r['hijacked_as_name']
            d['hijacked_as_org'] = r['hijacked_as_org']
            d['hijacked_as_country'] = r['hijacked_as_country']
            d['hijacked_as_descr'] = r['hijacked_as_descr']
            d['hijacked_as_admin'] = eval(r['hijacked_as_admin']) if r['hijacked_as_admin'] else None
            d['hijacker_as'] = r['hijacker_as']
            d['hijacker_as_name'] = r['hijacker_as_name']
            d['hijacker_as_org'] = r['hijacker_as_org']
            d['hijacker_as_country'] = r['hijacker_as_country']
            d['hijacker_as_descr'] = r['hijacker_as_descr']
            d['hijacker_as_admin'] = eval(r['hijacker_as_admin']) if r['hijacker_as_admin'] else None
            d['start_time'] = str(r['s_time'].strftime("%Y年%m月%d日 %H时%M分%S秒"))
            if r['e_time'] != None:
                d['end_time'] = str(r['e_time'].strftime("%Y年%m月%d日 %H时%M分%S秒"))
            else:
                d['end_time'] = ''
            
            if r['duration'] != None:
                duration_fields = str(r['duration']).split(':')
                hours, minutes, seconds = duration_fields[0].replace('days', '天').replace('day', '天'), duration_fields[1], duration_fields[2]
                d['duration'] = ''
                if hours not in ['0', '00']:
                    d['duration'] = d['duration'] + '{}小时'.format(hours)
                if minutes not in ['0', '00']:
                    d['duration'] = d['duration'] + '{}分钟'.format(minutes)
                if seconds not in ['0', '00']:
                    d['duration'] = d['duration'] + '{}秒'.format(seconds)
            else:
                d['duration'] = ''

            d['event_descr'] = str(r['hijack_level_info'])
            d['event_level'] = r['hijack_level']
            d['pre_vp_paths'] = r['pre_vp_paths']
            d['eve_vp_paths'] = r['eve_vp_paths']
            hijacked_as = r['hijacked_as']
            t = r['s_time']
        
        return event_type, d
    
    elif event_type == 'sub_hijack':
        table = event_type + '_' + '{}{}'.format(year, month)
        prefix = problem.replace('-', '/')
        row = get_sub_hijack_de(conn=conn, table=table, prefix=prefix, sub_hijack_id=event_id, source=source)
        d = dict()
        for r in row:
            d['event_id'] = event_id
            d['hijacker_prefix'] = prefix
            d['hijacked_prefix'] = r['hijacked_prefix']
            d['hijacked_as'] = eval(r['hijacked_as'])[0]
            d['hijacked_as_name'] = r['hijacked_as_name']
            d['hijacked_as_org'] = r['hijacked_as_org']
            d['hijacked_as_country'] = r['hijacked_as_country']
            d['hijacked_as_descr'] = r['hijacked_as_descr']
            d['hijacked_as_admin'] = eval(r['hijacked_as_admin']) if r['hijacked_as_admin'] else None
            d['hijacker_as'] = eval(r['hijacker_as'])[0]
            d['hijacker_as_name'] = r['hijacker_as_name']
            d['hijacker_as_org'] = r['hijacker_as_org']
            d['hijacker_as_country'] = r['hijacker_as_country']
            d['hijacker_as_descr'] = r['hijacker_as_descr'] 
            d['hijacker_as_admin'] = eval(r['hijacker_as_admin']) if r['hijacker_as_admin'] else None
            d['start_time'] = str(r['s_time'].strftime("%Y年%m月%d日 %H时%M分%S秒"))
            if r['e_time'] != None:
                d['end_time'] = str(r['e_time'].strftime("%Y年%m月%d日 %H时%M分%S秒"))
            else:
                d['end_time'] = ''
            
            if r['duration'] != None:
                duration_fields = str(r['duration']).split(':')
                hours, minutes, seconds = duration_fields[0].replace('days', '天').replace('day', '天'), duration_fields[1], duration_fields[2]
                d['duration'] = ''
                if hours not in ['0', '00']:
                    d['duration'] = d['duration'] + '{}小时'.format(hours)
                if minutes not in ['0', '00']:
                    d['duration'] = d['duration'] + '{}分钟'.format(minutes)
                if seconds not in ['0', '00']:
                    d['duration'] = d['duration'] + '{}秒'.format(seconds)
            else:
                d['duration'] = ''

            d['event_level'] = r['sub_hijack_level']
            d['event_descr'] = r['level_info']
        return event_type, d

    elif event_type == 'prefix_outage':
        table = event_type + '_' + '{}{}'.format(year, month)
        prefix = problem.replace('-', '/')
        row = get_pre_outage_de(conn=conn, table=table, prefix=prefix, outage_id=event_id, source=source)
        d = dict()
        for r in row:
            d['event_id'] = event_id
            d['prefix'] = prefix
            d['asn'] = r['asn']
            d['as_name'] = r['as_name']
            d['org_name'] = r['org_name']
            d['country'] = r['country']
            d['as_descr'] = r['as_descr']
            d['as_admin'] = eval(r['as_admin']) if r['as_admin'] else None
            d['start_time'] = str(r['s_time'].strftime("%Y年%m月%d日 %H时%M分%S秒"))
            d['event_level'] = r['outage_level']
            if r['e_time'] != None: 
                d['end_time'] = str(r['e_time'].strftime("%Y年%m月%d日 %H时%M分%S秒"))
            else:
                d['end_time'] = ''
            
            if r['duration'] != None:
                duration_fields = str(r['duration']).split(':')
                hours, minutes, seconds = duration_fields[0].replace('days', '天').replace('day', '天'), duration_fields[1], duration_fields[2]
                d['duration'] = ''
                if hours not in ['0', '00']:
                    d['duration'] = d['duration'] + '{}小时'.format(hours)
                if minutes not in ['0', '00']:
                    d['duration'] = d['duration'] + '{}分钟'.format(minutes)
                if seconds not in ['0', '00']:
                    d['duration'] = d['duration'] + '{}秒'.format(seconds)
            else:
                d['duration'] = ''     
        return event_type, d  

    elif event_type == 'as_outage':
        table = event_type + '_' + '{}{}'.format(year, month)
        asn = problem
        row = get_as_outage_de(conn=conn, table=table, asn=asn, outage_id=event_id, source=source)
        d = dict()
        t = ''
        for r in row:
            d['event_id'] = event_id
            d['asn'] = asn
            d['as_name'] = r['as_name']
            d['country'] = r['country']
            d['org_name'] = r['org_name']
            d['as_descr'] = r['as_descr']
            d['as_admin'] = eval(r['as_admin']) if r['as_admin'] else None
            d['event_level'] = r['outage_level']
            d['as_type'] = r['as_type']
            d['end_descr'] = r['outage_level_descr']
            d['as_prefix_num'] = r['total_prefix_num']
            d['outage_prefix_num'] = r['max_outage_prefix_num']
            d['outage_prefixes'] = r['outage_prefixes']
            d['start_time'] = str(r['s_time'].strftime("%Y年%m月%d日 %H时%M分%S秒"))
            if r['e_time'] != None:
                d['end_time'] = str(r['e_time'].strftime("%Y年%m月%d日 %H时%M分%S秒"))
            else:
                d['end_time'] = ''
            
            if r['duration'] != None:
                duration_fields = str(r['duration']).split(':')
                hours, minutes, seconds = duration_fields[0].replace('days', '天').replace('day', '天'), duration_fields[1], duration_fields[2]
                d['duration'] = ''
                if hours not in ['0', '00']:
                    d['duration'] = d['duration'] + '{}小时'.format(hours)
                if minutes not in ['0', '00']:
                    d['duration'] = d['duration'] + '{}分钟'.format(minutes)
                if seconds not in ['0', '00']:
                    d['duration'] = d['duration'] + '{}秒'.format(seconds)
            else:
                d['duration'] = ''
        
        return event_type, d

    elif event_type == 'country_outage':
        table = event_type + '_' + '{}{}'.format(year, month)
        country = problem
        row = get_country_outage_de(conn=conn, table=table, country=country, outage_id=event_id, source=source)
        d = dict()
        for r in row:
            d['country_code'] = country
            d['event_id'] = event_id
            d['country'] = r['country_chinese_name']
            d['total_as_num'] = r['total_as_num']
            d['event_level'] = r['outage_level']
            d['outage_as_num'] = r['max_outage_as_num']
            d['event_descr'] = r['outage_level_descr']
            d['outage_ases'] = r['outage_ases']
            d['start_time'] = str(r['s_time'].strftime("%Y年%m月%d日 %H时%M分%S秒"))
            if r['e_time'] != None:
                d['end_time'] = str(r['e_time'].strftime("%Y年%m月%d日 %H时%M分%S秒"))
            else:
                d['end_time'] = ''
            
            if r['duration'] != None:
                duration_fields = str(r['duration']).split(':')
                hours, minutes, seconds = duration_fields[0].replace('days', '天').replace('day', '天'), duration_fields[1], duration_fields[2]
                d['duration'] = ''
                if hours not in ['0', '00']:
                    d['duration'] = d['duration'] + '{}小时'.format(hours)
                if minutes not in ['0', '00']:
                    d['duration'] = d['duration'] + '{}分钟'.format(minutes)
                if seconds not in ['0', '00']:
                    d['duration'] = d['duration'] + '{}秒'.format(seconds)
            else:
                d['duration'] = ''
        
        return event_type, d

    elif event_type == 'leak':
        table = 'leak_event_' + '{}{}'.format(year, month)
        prefix = problem.replace('-', '/')
        row = get_leak_de(conn=conn, table=table, prefix=prefix, leak_id=event_id, source=source)
        d = dict()
        asn = ''
        t = ''
        for r in row:
            d['event_id'] = event_id
            d['leak_prefix'] = prefix
            d['event_descr'] = r['leak_level_info']
            d['event_level'] = r['leak_level']
            d['leak_by'] = r['leak_by']
            d['leak_by_name'] = r['leak_by_name']
            d['leak_by_org'] = r['leak_by_org']
            d['leak_by_country'] = r['leak_by_country']
            d['leak_by_descr'] = r['leak_by_descr']
            d['leak_by_admin'] = eval(r['leak_by_admin']) if r['leak_by_admin'] else None
            d['leak_to'] = r['leak_to']
            d['leak_to_name'] = r['leak_to_name']
            d['leak_to_org'] = r['leak_to_org']
            d['leak_to_country'] = r['leak_to_country']
            d['leak_to_descr'] = r['leak_to_descr']
            d['leak_to_admin'] = eval(r['leak_to_admin']) if r['leak_to_admin'] else None

            d['prefix_origin_as'] = r['prefix_ori_as']
            d['ori_as_country'] = r['ori_as_country']
            d['ori_as_org'] = r['ori_as_org']
            d['ori_as_name'] = r['ori_as_name']
            d['ori_as_descr'] = r['ori_as_descr']
            d['ori_as_admin'] = eval(r['ori_as_admin']) if r['ori_as_admin'] else None
            d['start_time'] = str(r['s_time'].strftime("%Y年%m月%d日 %H时%M分%S秒"))

            d["as_path"] = r['as_path']
        return event_type, d


def if_table_exist(conn, table_name) -> bool:
    """
    判断数据表是否存在
    :param conn: 数据库连接
    :param table_name: 数据表名
    :return: 存在则返回True, 不存在则返回False
    """
    cursor = conn.cursor()
    sql = """
        select count(*) from pg_class where relname = '{}';
    """.format(table_name)
    cursor.execute(sql)
    result = cursor.fetchall()
    cursor.close()
    return result[0][0] == 1


def get_pre_outage_de(conn, table, prefix, outage_id, source) -> list:
    """
    返回前缀中断的详情信息
    :param conn: 数据库连接
    :param table: 数据表名
    :param prefix: 中断前缀
    :param outage_id: 前缀中断id
    :return: 前缀中断的详情信息
    """
    row = list()
    if if_table_exist(conn, table):
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        sql = """
            select prefix, asn, country, s_time, e_time, outage_level_descr, outage_level, 
                   as_name, org_name, as_type, duration, pre_vp_paths, eve_vp_paths, event_info, 
                   as_descr, as_admin, next_vp_paths
            from {}
            where prefix = '{}' and outage_id = '{}' and source = '{}';
        """.format(table, prefix, outage_id, source)
        cursor.execute(sql)
        row = cursor.fetchall()
        cursor.close()
    return row


def get_as_outage_de(conn, table, asn, outage_id, source) -> list:    
    """
    返回AS中断的详情信息
    :param conn: 数据库连接
    :param table: 数据表名
    :param asn: AS编号
    :param outage_id: AS中断id
    :return: AS中断的详情信息
    """
    row = list()
    if if_table_exist(conn, table):
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        sql = """
            select asn, as_name, country, org_name, s_time, duration, outage_level, pre_vp_paths, eve_vp_paths,
                   outage_prefixes, total_prefix_num, max_outage_prefix_num, as_type, e_time, 
                   outage_level_descr, event_info, as_descr, as_admin, next_vp_paths
            from {}
            where asn = '{}' and outage_id = '{}' and source = '{}';
        """.format(table, asn, outage_id, source)
        cursor.execute(sql)
        row = cursor.fetchall()
        cursor.close()
    return row


def get_boundary_outage_de(conn, table, export_as, peer_as, outage_id, source) -> list:
    """
    返回AS中断的详情信息
    :param conn: 数据库连接
    :param table: 数据表名
    :param export_as, peer_as: AS编号
    :param outage_id: AS中断id
    :return: AS中断的详情信息
    """
    row = list()
    if if_table_exist(conn, table):
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        sql = """
            select export_as, peer_as, s_time, e_time, duration, outage_level, 
                   outage_level_descr, max_used_count, export_as_country, peer_as_country,
                   export_as_name, peer_as_name, export_as_org, peer_as_org,
                   export_as_type, peer_as_type, event_info
            from {}
            where export_as = '{}' and peer_as = '{}' and outage_id = '{}' and source = '{}';
        """.format(table, export_as, peer_as, outage_id, source)
        cursor.execute(sql)
        row = cursor.fetchall()
        cursor.close()
    return row


def get_country_outage_de(conn, table, country, outage_id, source) -> list:
    """
    返回国家中断的详情信息
    :param conn: 数据库连接
    :param table: 数据表名
    :param country: 国家两字母表示
    :param outage_id: 国家中断id
    :return: 国家中断的详情信息
    """
    row = list()
    if if_table_exist(conn, table):
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        sql = """
            select country_chinese_name, total_as_num, s_time, e_time, duration, outage_level, max_outage_as_num,
                   outage_level_descr, outage_ases, event_info
            from {}
            where country = '{}' and outage_id = '{}' and source = '{}';
        """.format(table, country, outage_id, source)
        cursor.execute(sql)
        row = cursor.fetchall()
        cursor.close()
    return row


def get_hijack_de(conn, table, prefix, hijack_id, source) -> list:
    """
    返回劫持事件的详细信息
    :param conn: 数据库连接
    :param table: 数据表名
    :param prefix: 被劫持的前缀
    :param hijack_id: 前缀劫持id
    :return: 劫持事件的详细信息
    """
    row = list()
    if if_table_exist(conn, table):
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        sql = """
            select prefix, hijack_eventid, hijacked_as, hijacked_as_name, hijacked_as_org, hijacked_as_country,
                   hijacker_as, hijacker_as_name, hijacker_as_org, hijacker_as_country, s_time, e_time, duration,
                   hijack_level, hijack_level_info, pre_vp_paths, eve_vp_paths, event_info, 
                   hijacker_as_descr, hijacker_as_admin, hijacked_as_descr, hijacked_as_admin, next_vp_paths
            from {}
            where prefix = '{}' and hijack_eventid = '{}' and source = '{}';
        """.format(table, prefix, hijack_id, source)
        cursor.execute(sql)
        row = cursor.fetchall()
        cursor.close()
    return row


def get_sub_hijack_de(conn, table, prefix, sub_hijack_id, source) -> list:
    """
    返回子前缀劫持事件的详细信息
    :param conn: 数据库连接
    :param table: 数据表名
    :param prefix: 发动劫持的前缀
    :param sub_hijack_id: 子前缀劫持id
    :return: 子前缀劫持的详细信息
    """
    row = list()
    if if_table_exist(conn, table):
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        sql = """
            select prefix, sub_hijack_eventid, hijacked_prefix, hijacked_as, hijacked_as_name, hijacked_as_org, hijacked_as_country,
                   hijacker_as, hijacker_as_name, hijacker_as_org, hijacker_as_country, s_time, e_time, duration,
                   sub_hijack_level, level_info, event_info, hijacker_as_descr, hijacker_as_admin, hijacked_as_descr, hijacked_as_admin
            from {}
            where prefix = '{}' and sub_hijack_eventid = '{}' and source = '{}';
        """.format(table, prefix, sub_hijack_id, source)
        cursor.execute(sql)
        row = cursor.fetchall()
        cursor.close()
    return row


def get_leak_de(conn, table, prefix, leak_id, source) -> list:
    """
    返回劫持事件的详细信息
    :param conn: 数据库连接
    :param table: 数据表名
    :param prefix: 泄露的前缀
    :param leak_id: 前缀泄露id
    :return: 泄露事件的详细信息
    """
    row = list()
    if if_table_exist(conn, table):
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        sql = """
            select prefix, leak_event_id, leak_by, leak_by_name, leak_by_org, leak_by_country,
                   leak_to, leak_to_name, leak_to_org, leak_to_country, s_time, leak_level, 
                   leak_level_info, as_path, prefix_ori_as, ori_as_country, ori_as_org, ori_as_name, 
                   event_info, leak_to_descr, leak_to_admin, leak_by_descr, leak_by_admin, 
                   ori_as_descr, ori_as_admin
            from {}
            where prefix = '{}' and leak_event_id = '{}' and source = '{}';
        """.format(table, prefix, leak_id, source)
        cursor.execute(sql)
        row = cursor.fetchall()
        cursor.close()
    return row


def get_event_judgeinfo(conn, event_table, detail_url):
    """
    获取某一个事件的研判相关信息
    :param conn: 数据库连接
    :param event_table: 事件总表
    :param detail_url: 详情url(事件总表的键)
    :return: 事件的rows
    """         
    row = list()
    if if_table_exist(conn, event_table):
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        sql = """
            select state, judge_reason, judge_userid, judge_time
            from {}
            where detail_url = '{}';
        """.format(event_table, detail_url)
        cursor.execute(sql)
        row = cursor.fetchall()
        cursor.close()
    return row


def __get_as_feature(conn, feature_table, asn, t: datetime.datetime) -> list:
    """
    获取asn的特征，事件发生前6小时 - 事件发生后6小时
    :param conn: 数据库连接
    :param feature_table: 特征表名
    :param asn: AS编号
    :param t: 事件发生的时间
    :return:
    """

    _6hour_ago = t - datetime.timedelta(hours=6)
    _6hour_later = t + datetime.timedelta(hours=6)
    # 统一走 database/feature_asn.py（支持按月表 + *_old 回退）
    return get_as_feature_db(conn=conn, feature_table=feature_table, asn=asn, _6hour_ago=_6hour_ago, _6hour_later=_6hour_later)


def __deal_as_feature(feature_rows, t: datetime.datetime) -> tuple:
    """
    补全可能缺失的特征, 拆分为时间列表，announ列表，withdraw列表
    :param feature_rows: 从数据库取出的特征列表
    :param t: 事件的开始时间
    :return: 时间列表，announ列表，withdraw列表
    """
    res_list = list()
    asn = ''
    for row in feature_rows:
        asn = row['asn']
        announ_num = row['announ_num']
        withdraw_num = row['withdraw_num']

        d = dict()
        d['t'] = str(row['t'])
        d['asn'] = asn
        d['announ_num'] = announ_num
        d['withdraw_num'] = withdraw_num
        res_list.append(d)
    # 对特征按时间排序
    res_list.sort(key=lambda item: item['t'])
    # 补全缺失的特征
    _6hour_ago = t - datetime.timedelta(hours=6)
    _6hour_later = t + datetime.timedelta(hours=6)
    temp_time = _6hour_ago
    while temp_time <= _6hour_later:
        time_str = str(temp_time)
        exist = False
        for item in res_list:
            if item['t'] == time_str:
                exist = True
                break
        if exist is False:
            d = dict()
            d['asn'] = asn
            d['t'] = time_str
            d['announ_num'], d['withdraw_num'] = 0, 0
            res_list.append(d)
        temp_time = temp_time + datetime.timedelta(minutes=5)
    res_list.sort(key=lambda item: item['t'])
    # 拆分为3个列表
    time_list, announ_list, withdraw_list = list(), list(), list()
    for item in res_list:
        time_list.append(item['t'])
        announ_list.append(item['announ_num'])
        withdraw_list.append(item['withdraw_num'])
    return time_list, announ_list, withdraw_list


def get_as_feature(conn, feature_table, asn, t: datetime.datetime) -> tuple:
    if 0 <= t.minute <= 4:
        minute = 0
    else:
        minute = 5
    date = datetime.datetime(year=t.year, month=t.month, day=t.day, hour=t.hour, minute=minute, second=0)
    start = time.time()
    feature_raw = __get_as_feature(conn=conn, feature_table=FEATURE_TABLE, asn=asn, t=date)
    end = time.time()
    print("查询feature表事件: {}s".format(end - start))
    # print(feature_raw 
    return __deal_as_feature(feature_rows=feature_raw, t=date)



def get_user_list(conn, page_num, page_size, userid, username, role, 
                  creatorid, creatorname, create_time, sort_mode):
    """
    在用户管理模块获取用户列表。
    :param conn: 数据库连接
    :param page_num: 页数
    :param page_size: 分页长度
    :param userid: 用户账号（用户唯一id）
    :param username: 用户名
    :param role: 用户角色
    :param creatorid: 创建人账号
    :param creatorname: 创建人用户名
    :param create_time: 创建时间
    :param sort_mode: 排序方式
    :return: 用户列表
    """    
    # 筛选条件
    offset = (page_num - 1) * page_size
    userid = "'%%'" if userid in [None, ''] else "'%{}%'".format(userid)
    username = "'%%'" if username in [None, ''] else "'%{}%'".format(username)
    creatorid = "'%%'" if creatorid in [None, ''] else "'%{}%'".format(creatorid)
    creatorname = "'%%'" if creatorname in [None, ''] else "'%{}%'".format(creatorname)
    role = "'{}'".format(role) if role in ['guest', 'admin', 'operator'] else 'role'

    pattern = re.compile('^\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}')
    if create_time == None:
        create_time_start = create_time_end = 'create_time'
    elif pattern.match(create_time):
        create_time_start = "'{}'".format(create_time.split('_')[0] + ' 00:00:00')
        create_time_end = "'{}'".format(create_time.split('_')[1] + ' 24:00:00')
    else:
        create_time_start = create_time_end = 'create_time'
    if sort_mode in ['useridA', 'useridB', 'usernameA', 'usernameB',  
                    'creatoridA', 'creatoridB', 'creatornameA', 'creatornameB', 'create_timeA', 'create_timeB']:
        sort_mode = sort_mode.replace('A', ' asc, userid asc').replace('B', ' desc, userid asc')
    elif sort_mode == 'roleA':
        sort_mode = "case role when 'guest' then 1 when 'operator' then 2 when 'admin' then 3 end, userid"
    elif sort_mode == 'roleB':
        sort_mode = "case role when 'admin' then 1 when 'operator' then 2 when 'guest' then 3 end, userid"
    else:
        sort_mode = 'create_time asc, userid asc'

    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    users_table = 'users'
    user_rows = list()
    if if_table_exist(conn, users_table):
        sql = """
            select userid, username, role, password, creatorid, creatorname, create_time
            from {}
            where userid like {} and username like {} and creatorid like {} and creatorname like {} 
            and create_time >= {} and create_time <= {} and role = {}
            order by {}
            limit {} offset {};
        """.format(users_table, userid, username, creatorid, creatorname, create_time_start, create_time_end, 
                   role, sort_mode, page_size, offset)
        cursor.execute(sql)
        user_rows = cursor.fetchall()
    return user_rows


def deal_user_list(user_rows) -> list:
    """
        在拿到users表中的数据之后 每一行用字典来表示 并加入至列表
    """
    res_list = list()
    for row in user_rows:
        userid = row['userid']
        username = row['username']
        password = row['password']
        role = row['role']
        creatorid = row['creatorid']
        creatorname = row['creatorname']
        create_time = str(row['create_time'])
        d = dict()
        d['userid'] = userid
        d['username'] = username
        d['role'] = role
        d['password'] = password
        d['creatorid'] = creatorid
        d['creatorname'] = creatorname
        d['create_time'] = create_time
        res_list.append(d)
    return res_list


def get_user_total_page(conn, page_size, userid, username, role, 
                        creatorid, creatorname, create_time):
    """
    获取用户列表总页数
    :param conn: 数据库连接
    :param page_size: 分页长度
    :param userid: 用户账号
    :param username: 用户名
    :param role: 用户角色
    :param creatorid: 创建人账号
    :param creatorname: 创建人用户名
    :param create_time: 创建时间
    :return: 用户列表总页数、总条目数
    """    
    # 筛选条件
    userid = "'%%'" if userid in [None, ''] else "'%{}%'".format(userid)
    username = "'%%'" if username in [None, ''] else "'%{}%'".format(username)
    creatorid = "'%%'" if creatorid in [None, ''] else "'%{}%'".format(creatorid)
    creatorname = "'%%'" if creatorname in [None, ''] else "'%{}%'".format(creatorname)
    role = "'{}'".format(role) if role in ['guest', 'admin', 'operator'] else 'role'

    pattern = re.compile('^\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}')
    if create_time == None:
        create_time_start = create_time_end = 'create_time'
    elif pattern.match(create_time):
        create_time_start = "'{}'".format(create_time.split('_')[0] + ' 00:00:00')
        create_time_end = "'{}'".format(create_time.split('_')[1] + ' 24:00:00')
    else:
        create_time_start = create_time_end = 'create_time'
    
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    users_table = 'users'

    if if_table_exist(conn, users_table):
        sql = """
            select count(*)
            from {}
            where userid like {} and username like {} and creatorid like {} and creatorname like {} 
            and create_time >= {} and create_time <= {} and role = {};
            """.format(users_table, userid, username, creatorid, creatorname, create_time_start, 
                       create_time_end, role)
        cursor.execute(sql)
        record_count = cursor.fetchone()[0]
        total_page = math.ceil(record_count / page_size)
    else:
        total_page, record_count = 0, 0
    cursor.close()
    return total_page, record_count


def get_boundary(conn, boundary_table, page_num, page_size, export_as_country, peer_as_country, sort_mode) -> list:
    """_summary_

    :param conn: _description_
    :param boundary_table: _description_
    :param page_num: _description_
    :param page_size: _description_
    :param export_as_country: _description_
    :param peer_as_country: _description_
    :param sort_mode: _description_
    :return: _description_
    """    
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    boundary_rows = list()
    offset = (page_num - 1) * page_size
    if export_as_country in [None, '']:
        export_as_country = "'%%'"
    else:
        export_as_country = "'%{}%'".format(export_as_country)
    if peer_as_country in [None, '']:
        peer_as_country = "'%%'"
    else:
        peer_as_country = "'%{}%'".format(peer_as_country)
    if sort_mode in [None, '']:
        sort_mode =  'export_as asc, peer_as asc'
    elif ('export_as' in sort_mode) or ('export_as_country' in sort_mode) or ('export_as_name' in sort_mode) \
        or ('export_as_org' in sort_mode) or ('export_as_type' in sort_mode) or ('peer_as' in sort_mode) \
        or ('peer_as_country' in sort_mode) or ('peer_as_name' in sort_mode) or ('peer_as_org' in sort_mode ) \
        or ('peer_as_type' in sort_mode):
        sort_mode =  sort_mode.replace('A', ' asc, export_as asc, peer_as asc').replace('B', ' desc, export_as asc, peer_as asc')
    else:
        sort_mode =  'export_as asc, peer_as asc'

    sql = 'select time from {} order by time desc limit 1;'.format(boundary_table)
    cursor.execute(sql)
    time = cursor.fetchone()[0]
    if if_table_exist(conn, boundary_table):
        sql_boundary = """
                select export_as, export_as_country, export_as_name, export_as_org, export_as_type,
                peer_as, peer_as_country, peer_as_name, peer_as_org, peer_as_type
                from {}
                where COALESCE(export_as_country, '') like {} and COALESCE(peer_as_country, '') like {}
                and time='{}'
                order by {} 
                limit {} offset {};
            """.format(boundary_table, export_as_country, peer_as_country, time, sort_mode, page_size, offset)
        cursor.execute(sql_boundary)
        boundary_rows = cursor.fetchall()
    cursor.close()
    return boundary_rows


def get_connection(conn, connection_table, page_num, page_size, vp_country_chinese_name, 
                   dst_country_chinese_name, sort_mode) -> list:
    """_summary_

    :param conn: _description_
    :param connection_table: _description_
    :param page_num: _description_
    :param page_size: _description_
    :param vp_country_chinese_name: _description_
    :param dst_country_chinese_name: _description_
    :param sort_mode: _description_
    :return: _description_
    """    
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    connection_rows = list()
    offset = (page_num - 1) * page_size
    if vp_country_chinese_name in [None, '']:
        vp_country_chinese_name = "'%%'"
    else:
        vp_country_chinese_name = "'%{}%'".format(vp_country_chinese_name)
    if dst_country_chinese_name in [None, '']:
        dst_country_chinese_name = "'%%'"
    else:
        dst_country_chinese_name = "'%{}%'".format(dst_country_chinese_name)
    if sort_mode in [None, '']:
        sort_mode =  'vp_country asc, dst_country asc'
    elif ('vp_country' in sort_mode) or ('vp_country_chinese_name' in sort_mode) or ('dst_country' in sort_mode) \
        or ('dst_country_chinese_name' in sort_mode) or ('export_as_count' in sort_mode) or ('key_path_count' in sort_mode) \
        or ('key_country_path_count' in sort_mode) or ('key_org_path_count' in sort_mode) or ('entrance_as_count' in sort_mode ) \
        or ('path_count' in sort_mode):
        sort_mode =  sort_mode.replace('A', ' asc, vp_country asc, dst_country asc').replace('B', ' desc, vp_country asc, dst_country asc')
    else:
        sort_mode =  'vp_country asc, dst_country asc'

    sql = 'select time from {} order by time desc limit 1;'.format(connection_table)
    cursor.execute(sql)
    time = cursor.fetchone()[0]
    if if_table_exist(conn, connection_table):
        sql_connection = """
                select vp_country, vp_country_chinese_name, dst_country, dst_country_chinese_name,
                export_as_count, key_path_count, key_country_path_count, key_org_path_count, 
                entrance_as_count, path_count
                from {}
                where COALESCE(vp_country_chinese_name, '') like {} and 
                COALESCE(dst_country_chinese_name, '') like {} and time='{}'
                order by {}
                limit {} offset {};
            """.format(connection_table, vp_country_chinese_name, dst_country_chinese_name, time, 
                        sort_mode, page_size, offset)
        cursor.execute(sql_connection)
        connection_rows = cursor.fetchall()
    cursor.close()
    return connection_rows


def get_boundary_total_page(conn, boundary_table, page_size, export_as_country, peer_as_country):
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    if export_as_country in [None, '']:
        export_as_country = "'%%'"
    else:
        export_as_country = "'%{}%'".format(export_as_country)
    if peer_as_country in [None, '']:
        peer_as_country = "'%%'"
    else:
        peer_as_country = "'%{}%'".format(peer_as_country)

    sql = 'select time from {} order by time desc limit 1;'.format(boundary_table)
    cursor.execute(sql)
    time = cursor.fetchone()[0]
    if if_table_exist(conn, boundary_table):
        sql_boundary = """
                select count(*)
                from {}
                where COALESCE(export_as_country, '') like {} and COALESCE(peer_as_country, '') like {}
                and time='{}';
            """.format(boundary_table, export_as_country, peer_as_country, time)
        cursor.execute(sql_boundary)
        record_count = cursor.fetchone()[0]
        total_page = math.ceil(record_count / page_size)
    else:
        total_page, record_count = 0, 0
    cursor.close()
    return total_page, record_count


def get_connection_total_page(conn, connection_table, page_size, vp_country_chinese_name, dst_country_chinese_name):
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    if vp_country_chinese_name in [None, '']:
        vp_country_chinese_name = "'%%'"
    else:
        vp_country_chinese_name = "'%{}%'".format(vp_country_chinese_name)
    if dst_country_chinese_name in [None, '']:
        dst_country_chinese_name = "'%%'"
    else:
        dst_country_chinese_name = "'%{}%'".format(dst_country_chinese_name)

    sql = 'select time from {} order by time desc limit 1;'.format(connection_table)
    cursor.execute(sql)
    time = cursor.fetchone()[0]
    if if_table_exist(conn, connection_table):
        sql_connection = """
                select count(*)
                from {}
                where COALESCE(vp_country_chinese_name, '') like {} and 
                COALESCE(dst_country_chinese_name, '') like {} and time='{}';
            """.format(connection_table, vp_country_chinese_name, dst_country_chinese_name, time)
        cursor.execute(sql_connection)
        record_count = cursor.fetchone()[0]
        total_page = math.ceil(record_count / page_size)
    else:
        total_page, record_count = 0, 0
    cursor.close()
    return total_page, record_count


def deal_boundary(boundary_rows) -> list:
    res_list = list()
    for row in boundary_rows:
        export_as = row['export_as']
        export_as_country = row['export_as_country']
        export_as_name = row['export_as_name']
        export_as_org = row['export_as_org']
        export_as_type = row['export_as_type']
        peer_as = row['peer_as']
        peer_as_country = row['peer_as_country']
        peer_as_name = row['peer_as_name']
        peer_as_org = row['peer_as_org']
        peer_as_type = row['peer_as_type']
        d = dict()
        d['export_as'] = export_as
        d['export_as_country'] = export_as_country
        d['export_as_name'] = export_as_name
        d['export_as_org'] = export_as_org
        d['export_as_type'] = export_as_type
        d['peer_as'] = peer_as
        d['peer_as_country'] = peer_as_country
        d['peer_as_name'] = peer_as_name
        d['peer_as_org'] = peer_as_org
        d['peer_as_type'] = peer_as_type
        res_list.append(d)
    return res_list


def deal_connection(connection_rows) -> list:
    res_list = list()
    for row in connection_rows:
        vp_country = row['vp_country']
        vp_country_chinese_name = row['vp_country_chinese_name']
        dst_country = row['dst_country']
        dst_country_chinese_name = row['dst_country_chinese_name']
        export_as_count = row['export_as_count']
        key_path_count = row['key_path_count']
        key_country_path_count = row['key_country_path_count']
        key_org_path_count = row['key_org_path_count']
        entrance_as_count = row['entrance_as_count']
        path_count = row['path_count']
        d = dict()
        d['vp_country'] = vp_country
        d['vp_country_chinese_name'] = vp_country_chinese_name
        d['dst_country'] = dst_country
        d['dst_country_chinese_name'] = dst_country_chinese_name
        d['export_as_count'] = export_as_count
        d['key_path_count'] = key_path_count
        d['key_country_path_count'] = key_country_path_count
        d['key_org_path_count'] = key_org_path_count
        d['entrance_as_count'] = entrance_as_count
        d['path_count'] = path_count
        res_list.append(d)
    return res_list


def get_top_event(conn, last_month_table, event_table, country, page_size, event_type):
    """
    返回近一周的高危事件
    :param conn: 数据库连接
    :param last_month_table: 上一个月的数据表名
    :param event_table: 事件表
    :event_type: 选取的事件类型
    :return: 总事件列表
    """
    # 一周可能横跨两个月
    # 获取当前的UTC时间
    bj_now = datetime.datetime.now()

    # 一周之前的UTC时间
    bj_week_ago = bj_now - datetime.timedelta(days=10)
    # bj_week_ago = bj_now - datetime.timedelta(days=7) g
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    # top_event_rows_lm = list()
    top_event_rows = list()
    
    country = 'True' if country == 'domestic' else 'is_domestic'

    if 1 <= bj_now.day <= 7:
        if if_table_exist(conn, last_month_table) and if_table_exist(conn, event_table):
            sql_event_lm = """
            select * from (
                select event_type, level, s_time, e_time, attacker_as, attacked_as, 
                event_info, detail_url, affected_prefix, attacker_org, attacked_org, 
                attacker_country, attacked_country, state, judge_reason,
                judge_userid, judge_username, judge_time, notify_userid, notify_username, notify_time
                from {}
                where is_domestic={} and level='high' and s_time > '{}'
                and event_type in {}
                and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                and (duration >= '00:03:00' or duration is null)
                UNION ALL
                select event_type, level, s_time, e_time, attacker_as, attacked_as, 
                event_info, detail_url, affected_prefix, attacker_org, attacked_org, 
                attacker_country, attacked_country, state, judge_reason,
                judge_userid, judge_username, judge_time, notify_userid, notify_username, notify_time
                from {}
                where is_domestic={} and level='high' and s_time > '{}'
                and event_type in {}
                and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                and (duration >= '00:03:00' or duration is null)
                ) as t
            order by 
                case 
                    when attacker_country = '中国' then 0
                    else 1
                end,
                s_time desc
                """.format(last_month_table, country, bj_week_ago, event_type, 
                    event_table, country, bj_week_ago, event_type, page_size)
            print(sql_event_lm)
        elif if_table_exist(conn, last_month_table):
                sql_event_lm = """
                    select event_type, level, s_time, e_time, attacker_as, attacked_as, 
                    event_info, detail_url, affected_prefix, attacker_org, attacked_org, 
                    attacker_country, attacked_country, state, judge_reason,
                    judge_userid, judge_username, judge_time, notify_userid, notify_username, notify_time
                    from {}
                    where is_domestic={} and level='high' and s_time > '{}'
                    and event_type in {}
                    and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                    and (duration >= '00:03:00' or duration is null)
                    order by 
                        case 
                            when attacker_country = '中国' then 0
                            else 1
                        end,
                        s_time desc
                    """.format(last_month_table, country, bj_week_ago, event_type, page_size)
                print(sql_event_lm)
        else:
            sql_event_lm = """
                select event_type, level, s_time, e_time, attacker_as, attacked_as, 
                event_info, detail_url, affected_prefix, attacker_org, attacked_org, 
                attacker_country, attacked_country, state, judge_reason,
                judge_userid, judge_username, judge_time, notify_userid, notify_username, notify_time
                from {}
                where is_domestic={} and level='high' and s_time > '{}'
                and event_type in {}
                and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                and (duration >= '00:03:00' or duration is null)
                order by 
                    case 
                        when attacker_country = '中国' then 0
                        else 1
                    end,
                    s_time desc
                """.format(event_table, country, bj_week_ago, event_type, page_size)
        try:
            cursor.execute(sql_event_lm)
            top_event_rows = cursor.fetchall()
        except Exception as e:
            conn.rollback()
            cursor.close()
    else:
        if if_table_exist(conn, event_table):
            sql_event_lm = """
                select event_type, level, s_time, e_time, attacker_as, attacked_as, 
                event_info, detail_url, affected_prefix, attacker_org, attacked_org, 
                attacker_country, attacked_country, state, judge_reason,
                judge_userid, judge_username, judge_time, notify_userid, notify_username, notify_time
                from {}
                where is_domestic={} and level='high' and s_time > '{}'
                and event_type in {}
                and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                and (duration >= '00:03:00' or duration is null)
                order by 
                    case 
                        when attacker_country = '中国' then 0
                        else 1
                    end,
                    s_time desc
                """.format(event_table, country, bj_week_ago, event_type, page_size)
            print(sql_event_lm)
            try:
                cursor.execute(sql_event_lm)
                top_event_rows = cursor.fetchall()
            except Exception as e:
                conn.rollback()
                cursor.close()

    ### 如果不为空 去除event_type中重复的行 每个event_type只保留最近的一条
    # event_type 去重
    if len(top_event_rows) > 0:
        top_event_rows =  pd.DataFrame(top_event_rows, columns=top_event_rows[0].keys())
        top_event_rows = top_event_rows.drop_duplicates(subset=['event_type'], keep="first")
        # top_event_rows = top_event_rows.sort_values(by='s_time', ascending=False)
        top_event_rows = top_event_rows.head(page_size)
    else:
        top_event_rows = pd.DataFrame()
        
    print(top_event_rows)
    return top_event_rows


# 不同类型的事件都可以使用deal_top_event处理 都是从event_table中读取
def deal_top_event(top_event_rows) -> list:
    """
    返回的是首页重大路由事件数据。
    :param top_event_rows: _description_
    :return: _description_
    """
    res_list = list()
    for index, row in top_event_rows.iterrows():
        event_type = row['event_type']
        level = row['level']
        s_time = str(row['s_time']).replace(' ', '\n')
        e_time = str(row['e_time'])
        attacker_as = row['attacker_as']
        attacked_as = row['attacked_as']
        event_info = row['event_info']
        detail_url = row['detail_url']
        affected_prefix = row['affected_prefix']
        attacker_org = row['attacker_org']
        attacked_org = row['attacked_org']
        attacker_country = row['attacker_country']
        attacked_country = row['attacked_country']
        state = row['state']
        judge_reason = row['judge_reason']
        judge_userid = row['judge_userid']
        judge_username = row['judge_username']
        judge_time = str(row['judge_time'])
        notify_userid = row['notify_userid']
        notify_username = row['notify_username']
        notify_time = str(row['notify_time'])
        d = dict()
        d['event_type'] = event_type
        d['level'] = level
        d['start_time'], d['end_time'] = s_time, e_time
        if e_time in ['None', None, 'NaT', pd.NaT]:
            d['end_time'] = '-'
        d['attacker_as'], d['attacked_as'] = attacker_as, attacked_as
        d['event_info'] = event_info
        d['detail_url'] = detail_url
        d['affected_prefix'] = affected_prefix
        d['attacker_org'] = attacker_org
        d['attacked_org'] = attacked_org
        d['attacker_country'] = attacker_country
        d['attacked_country'] = attacked_country
        d['state'] = state
        d['judge_reason'] = judge_reason
        d['judge_userid'] = judge_userid 
        d['judge_username'] = judge_username 
        d['judge_time'] = judge_time
        d['notify_userid'] = notify_userid 
        d['notify_username'] = notify_username 
        d['notify_time'] = notify_time
        res_list.append(d)
    return res_list





def get_security_screen_event(conn, last_month_table, event_table, country, page_size):
    """
    返回近一周的高危事件
    :param conn: 数据库连接
    :param last_month_table: 上一个月的数据表名
    :param leak_table: 泄露事件表
    :return: 泄露事件列表
    """
    # 获取当前的UTC时间
    bj_now = datetime.datetime.now()

    # 一周之前的UTC时间
    bj_week_ago = bj_now - datetime.timedelta(days=60) 
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    top_event_rows_lm = list()
    top_event_rows = list()
    country = 'True' if country == 'domestic' else 'is_domestic'

    if 1 <= bj_now.day <= 3:
        if if_table_exist(conn, last_month_table) and if_table_exist(conn, event_table):
            sql_event_lm = """
                    select event_type, level, state, s_time, e_time, attacker_as, attacked_as, 
                    attacker_org, attacked_org, attacker_country, attacked_country
                    from {}
                    where is_domestic={} and s_time > '{}'
                    and (state='misreport' or state='notify' or state='notified')
                    and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                    and (duration >= '00:03:00' or duration is null)
                    UNION ALL
                    select event_type, level, state, s_time, e_time, attacker_as, attacked_as, 
                    attacker_org, attacked_org, attacker_country, attacked_country
                    from {}
                    where is_domestic={} and s_time > '{}'
                    and (state='misreport' or state='notify' or state='notified')
                    and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                    and (duration >= '00:03:00' or duration is null)
                    order by case level when 'high' then 1 when 'middle' then 2 when 'low' then 3 end, s_time desc, s_time desc  
                    limit {} offset 0;
                """.format(last_month_table, country, bj_week_ago, 
                           event_table, country, bj_week_ago, page_size)
            cursor.execute(sql_event_lm)
            top_event_rows_lm = cursor.fetchall()
        cursor.close()
        return top_event_rows_lm

    if if_table_exist(conn, last_month_table):
        sql_event = """
                select event_type, level, state, s_time, e_time, attacker_as, attacked_as, 
                attacker_org, attacked_org, attacker_country, attacked_country
                from {}
                where is_domestic={} 
                and (state='misreport' or state='notify' or state='notified')
                and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                and (duration >= '00:03:00' or duration is null)
                order by case level when 'high' then 1 when 'middle' then 2 when 'low' then 3 end, s_time desc, s_time desc 
                limit {} offset 0;
            """.format(last_month_table, country, page_size)
        cursor.execute(sql_event)
        top_event_rows = cursor.fetchall()
    cursor.close()
    return top_event_rows

def deal_security_screen_event(top_event_rows, country_info) -> list:
    """
    返回的是安全态势大屏中间靠下重要数据。
    :param top_event_rows: _description_
    :return: _description_
    """    
    res_list = list()
    for row in top_event_rows:
        event_type = row['event_type']
        state = row['state']
        level = row['level']
        s_time, e_time = str(row['s_time']), str(row['e_time'])
        attacker = str(row['attacker_as']).replace('[', '').replace(']', '').replace('\'', '')
        attacked = str(row['attacked_as']).replace('[', '').replace(']', '').replace('\'', '')
        attacker_country, attacked_country = row['attacker_country'], row['attacked_country']
        attacker_org, attacked_org = row['attacker_org'], row['attacked_org']
        d = dict()
        attacked_country_code = get_country_two_letter_code(country_info, attacked_country).lower()
        d['attackedFlag'] = 'flag-icon-' + attacked_country_code
        d['attackedCountry'], d['attackedOrg'], d['attackedAS'] = attacked_country, attacked_org, attacked
        if get_country_two_letter_code(country_info, attacker_country) != None:
            attacker_country_code = get_country_two_letter_code(country_info, attacker_country).lower()
            d['attackerFlag'] = 'flag-icon-' + attacker_country_code
        else:
            d['attackerFlag'] = None
        d['attackerCountry'], d['attackerOrg'], d['attackerAS'] = attacker_country, attacker_org, attacker
        d['startTime'], d['endTime'] = s_time, e_time
        d['eventType'] = event_type
        d['eventJudge'] = False if state == 'misreport' else True
        d['eventLevel'] = level
        res_list.append(d)
    return res_list


def get_event(conn, last_month_table, event_table, page_num, page_size, level, event_type, 
              country, attacker_as, attacked_as, attacker_org, attacked_org, attacker_country, attacked_country, event_info, start_time, sort_mode, state, judge_reason, 
              judge_userid, judge_username, judge_time, notify_userid, notify_username, 
              notify_time):
    """
    得到符合要求的一页事件列表数据。
    :param conn: 数据库连接。
    :param last_month_table: 上个月的事件表。
    :param event_table: 本月事件表。
    :param page_num: 页数
    :param page_size: 分页条数(10条/页、50条/页等)
    :param level: 事件等级
    :param event_type: 事件类型
    :param country: 国内/国外
    :param attacker_org: 肇事方组织机构
    :param attacked_org: 受害方组织机构
    :param event_info: 事件信息
    :param start_time: 开始时间
    :param sort_mode: 事件列表排序方式
    :param state: 事件状态(待研判、待通报、已通报、疑似、误报、国外)
    :param judge_reason: 研判原因/研判依据
    :param judge_userid: 研判人账号
    :param judge_username: 研判人用户名
    :param judge_time: 研判时间
    :param notify_userid: 通报人账号
    :param notify_username: 通报人用户名
    :param notify_time: 通报时间
    :return: event_rows 事件列表
    """    
    # 获取当前的UTC时间
    bj_now = datetime.datetime.now()

    # 一周之前的UTC时间
    # 最早能够查询到的事件比现在早20天
    bj_week_ago = bj_now - datetime.timedelta(days=20)
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    event_rows_lm = list()
    event_rows = list()

    # 筛选条件
    offset = (page_num - 1) * page_size
    level = "'{}'".format(level) if level in ['high', 'middle', 'low'] else 'level'

    if event_type in ['前缀劫持', '子前缀劫持', '路由泄漏', '前缀中断', 'AS中断', '国家中断', '边界中断', 'RPKI证书异常']:
        event_type = "'{}'".format(event_type)      
    else:
        event_type = 'event_type'
    if country == 'all':
        is_domestic = 'is_domestic'
    elif country == 'foreign':
        is_domestic = False
    else:
        is_domestic = True
    attacker_as = "'%{}%'".format(attacker_as) if attacker_as else "'%%'"
    attacked_as = "'%{}%'".format(attacked_as) if attacked_as else "'%%'"
    attacker_org = "'%{}%'".format(attacker_org) if attacker_org else "'%%'"
    attacked_org = "'%{}%'".format(attacked_org) if attacked_org else "'%%'"
    attacker_country = "'%{}%'".format(attacker_country) if attacker_country else "'%%'"
    attacked_country = "'%{}%'".format(attacked_country) if attacked_country else "'%%'"
    event_info = "'%%'" if event_info == None else "'%{}%'".format(event_info)

    pattern = re.compile('^\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}')
    if start_time == None:
        s_time_start = s_time_end = 's_time'
    elif pattern.match(start_time):
        s_time_start = "'{}'".format(start_time.split('_')[0] + ' 00:00:00')
        s_time_end = "'{}'".format(start_time.split('_')[1] + ' 24:00:00')
    else:
        s_time_start = s_time_end = 's_time'
    if judge_time == None:
        judge_time_start = judge_time_end = "COALESCE(judge_time, DATE '0001-01-01')"
    elif pattern.match(judge_time):
        judge_time_start = "'{}'".format(judge_time.split('_')[0] + ' 00:00:00')
        judge_time_end = "'{}'".format(judge_time.split('_')[1] + ' 24:00:00')
    else:
        judge_time_start = judge_time_end = "COALESCE(judge_time, DATE '0001-01-01')"
    if notify_time == None:
        notify_time_start = notify_time_end = "COALESCE(notify_time, DATE '0001-01-01')"
    elif pattern.match(notify_time):
        notify_time_start = "'{}'".format(notify_time.split('_')[0] + ' 00:00:00')
        notify_time_end = "'{}'".format(notify_time.split('_')[1] + ' 24:00:00')
    else:
        notify_time_start = notify_time_end = "COALESCE(notify_time, DATE '0001-01-01')"

    judge_reason = "'%{}%'".format(judge_reason) if judge_reason else "'%%'"
    judge_userid = "'%{}%'".format(judge_userid) if judge_userid else "'%%'"
    judge_username = "'%{}%'".format(judge_username) if judge_username else "'%%'"
    notify_userid = "'%{}%'".format(notify_userid) if notify_userid else "'%%'"
    notify_username = "'%{}%'".format(notify_username) if notify_username else "'%%'"
    state = "'{}'".format(state) if state in ['judge', 'notify', 'misreport', 'suspected', 'notified'] else 'state'

    if sort_mode in ['event_typeA', 'event_typeB', 'event_infoA', 'event_infoB',
    'judge_reasonA', 'judge_reasonA', 'judge_useridA', 'judge_useridB', 'judge_usernameA',
    'judge_usernameB', 'judge_timeA', 'judge_timeB', 'notify_useridA', 'notify_useridB', 
    'notify_usernameA', 'notify_usernameB', 'notify_timeA', 'notify_timeB']:
        sort_mode = sort_mode.replace('A', ' asc, detail_url desc').replace('B', ' desc, detail_url desc')
    elif sort_mode == 'levelB':
        sort_mode = "case level when 'high' then 1 when 'middle' then 2 when 'low' then 3 end, detail_url"
    elif sort_mode == 'levelA':
        sort_mode = "case level when 'low' then 1 when 'middle' then 2 when 'high' then 3 end, detail_url"
    elif sort_mode in ['start_timeA', 'start_timeB']:
        sort_mode = sort_mode.replace('tart', '').replace('A', ' asc, detail_url desc').replace('B', ' desc, detail_url desc')
    elif sort_mode in ['end_timeA', 'end_timeB']:
        sort_mode = sort_mode.replace('nd', '').replace('A', ' asc, detail_url desc').replace('B', ' desc, detail_url desc')
    elif sort_mode in ['attacker_orgA', 'attacker_orgB']:
        sort_mode = sort_mode.replace('A', ' asc, detail_url desc').replace('B', ' desc, detail_url desc')
    elif sort_mode in ['attacked_orgA', 'attacked_orgB']:
        sort_mode = sort_mode.replace('A', ' asc, detail_url desc').replace('B', ' desc, detail_url desc')
    else:
        sort_mode = "case level when 'high' then 1 when 'middle' then 2 when 'low' then 3 end, s_time desc, detail_url"
    if event_type == "'边界中断'":
        is_boundary_outage = '='
    else:
        is_boundary_outage = '<>'
    
    if event_type == "'RPKI证书异常'":
        if if_table_exist(conn, event_table):
            sql_event = """
                    select event_type, level, s_time, e_time, attacker_as, attacked_as, 
                    event_info, detail_url, affected_prefix, attacker_org, attacked_org, 
                    attacker_country, attacked_country, state, judge_reason,
                    judge_userid, judge_username, judge_time, notify_userid, notify_username, notify_time
                    from {}
                    where level={} and event_type={} and COALESCE(is_domestic, True)={} 
                    and COALESCE(attacker_as, '') like {} and COALESCE(attacked_as, '') like {}
                    and COALESCE(attacker_org, '') like {} and COALESCE(attacked_org, '') like {}
                    and COALESCE(attacker_country, '') like {} and COALESCE(attacked_country, '') like {}
                    and s_time >= {} and s_time <= {} and COALESCE(judge_reason, '') like {}
                    and COALESCE(judge_userid, '') like {} and COALESCE(judge_username, '') like {}
                    and COALESCE(notify_userid, '') like {} and COALESCE(notify_username, '') like {}
                    and COALESCE(state, 'judge') = {} and COALESCE(judge_time, DATE '0001-01-01') >= {} and COALESCE(judge_time, DATE '0001-01-01') <= {}
                    and COALESCE(notify_time, DATE '0001-01-01') >= {} and COALESCE(notify_time, DATE '0001-01-01') <= {}
                    order by {} 
                    limit {} offset {};
                """.format(event_table, level, event_type, is_domestic, 
                        attacker_as, attacked_as, attacker_org, attacked_org, attacker_country, attacked_country,
                        s_time_start, s_time_end, judge_reason, judge_userid, judge_username, 
                        notify_userid, notify_username, state,
                        judge_time_start, judge_time_end, notify_time_start, notify_time_end,
                        sort_mode, page_size, offset)
            print(sql_event)
            try:
                cursor.execute(sql_event)
                event_rows = cursor.fetchall()
            except:
                traceback.print_exc()
                conn.rollback()
        cursor.close()
        return event_rows


    if 1 <= bj_now.day <= 7:
        if if_table_exist(conn, last_month_table):
            if if_table_exist(conn, event_table):
                sql_event_lm = """
                select * from (
                        select event_type, level, s_time, e_time, attacker_as, attacked_as, 
                        event_info, detail_url, affected_prefix, attacker_org, attacked_org, 
                        attacker_country, attacked_country, state, judge_reason,
                        judge_userid, judge_username, judge_time, notify_userid, notify_username, notify_time
                        from {}
                        where level={} and event_type={} and is_domestic={} 
                        and COALESCE(attacker_as, '') like {} and COALESCE(attacked_as, '') like {}
                        and COALESCE(attacker_org, '') like {} and COALESCE(attacked_org, '') like {}
                        and COALESCE(attacker_country, '') like {} and COALESCE(attacked_country, '') like {}
                        and event_info like {} and s_time >= {} and s_time <= {} and COALESCE(judge_reason, '') like {}
                        and COALESCE(judge_userid, '') like {} and COALESCE(judge_username, '') like {}
                        and COALESCE(notify_userid, '') like {} and COALESCE(notify_username, '') like {}
                        and state = {} and s_time > '{}' 
                        and COALESCE(judge_time, DATE '0001-01-01') >= {} 
                        and COALESCE(judge_time, DATE '0001-01-01') <= {}
                        and COALESCE(notify_time, DATE '0001-01-01') >= {} 
                        and COALESCE(notify_time, DATE '0001-01-01') <= {}
                        and event_type {} '边界中断'
                        and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                        UNION ALL
                        select event_type, level, s_time, e_time, attacker_as, attacked_as, 
                        event_info, detail_url, affected_prefix, attacker_org, attacked_org, 
                        attacker_country, attacked_country, state, judge_reason,
                        judge_userid, judge_username, judge_time, notify_userid, notify_username, notify_time
                        from {}
                        where level={} and event_type={} and is_domestic={}
                        and COALESCE(attacker_as, '') like {} and COALESCE(attacked_as, '') like {}
                        and COALESCE(attacker_org, '') like {} and COALESCE(attacked_org, '') like {}
                        and COALESCE(attacker_country, '') like {} and COALESCE(attacked_country, '') like {}
                        and event_info like {} and s_time >= {} and s_time <= {} and COALESCE(judge_reason, '') like {}
                        and COALESCE(judge_userid, '') like {} and COALESCE(judge_username, '') like {}
                        and COALESCE(notify_userid, '') like {} and COALESCE(notify_username, '') like {}
                        and state = {} and s_time > '{}' 
                        and COALESCE(judge_time, DATE '0001-01-01') >= {} 
                        and COALESCE(judge_time, DATE '0001-01-01') <= {}
                        and COALESCE(notify_time, DATE '0001-01-01') >= {} 
                        and COALESCE(notify_time, DATE '0001-01-01') <= {}
                        and event_type {} '边界中断'
                        and (duration >= '00:03:00' or duration is null)
                        and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10 ) t
                        order by {} 
                        limit {} offset {};
                    """.format(last_month_table, level, event_type, is_domestic, 
                            attacker_as, attacked_as, attacker_org, attacked_org, attacker_country, attacked_country, event_info, 
                            s_time_start, s_time_end, judge_reason, judge_userid, judge_username, 
                            notify_userid, notify_username, state, bj_week_ago, 
                            judge_time_start, judge_time_end, notify_time_start, notify_time_end,
                            is_boundary_outage,
                            event_table, level, event_type, is_domestic, 
                            attacker_as, attacked_as, attacker_org, attacked_org, attacker_country, attacked_country, event_info, 
                            s_time_start, s_time_end, judge_reason, judge_userid, judge_username, 
                            notify_userid, notify_username, state, bj_week_ago, 
                            judge_time_start, judge_time_end, notify_time_start, notify_time_end,
                            is_boundary_outage,
                            sort_mode, page_size, offset)
            else:
                sql_event_lm = """
                    select event_type, level, s_time, e_time, attacker_as, attacked_as, 
                        event_info, detail_url, affected_prefix, attacker_org, attacked_org, 
                        attacker_country, attacked_country, state, judge_reason,
                        judge_userid, judge_username, judge_time, notify_userid, notify_username, notify_time
                        from {}
                        where level={} and event_type={} and is_domestic={} 
                        and COALESCE(attacker_as, '') like {} and COALESCE(attacked_as, '') like {}
                        and COALESCE(attacker_org, '') like {} and COALESCE(attacked_org, '') like {} 
                        and COALESCE(attacker_country, '') like {} and COALESCE(attacked_country, '') like {}
                        and event_info like {} and s_time >= {} and s_time <= {} and COALESCE(judge_reason, '') like {}
                        and COALESCE(judge_userid, '') like {} and COALESCE(judge_username, '') like {}
                        and COALESCE(notify_userid, '') like {} and COALESCE(notify_username, '') like {}
                        and state = {} and s_time > '{}' 
                        and COALESCE(judge_time, DATE '0001-01-01') >= {} 
                        and COALESCE(judge_time, DATE '0001-01-01') <= {}
                        and COALESCE(notify_time, DATE '0001-01-01') >= {} 
                        and COALESCE(notify_time, DATE '0001-01-01') <= {}
                        and event_type {} '边界中断'
                        and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                        order by {} 
                        limit {} offset {};
                """.format(last_month_table, level, event_type, is_domestic, 
                            attacker_as, attacked_as, attacker_org, attacked_org, attacker_country, attacked_country, event_info, 
                            s_time_start, s_time_end, judge_reason, judge_userid, judge_username, 
                            notify_userid, notify_username, state, bj_week_ago, 
                            judge_time_start, judge_time_end, notify_time_start, notify_time_end,
                            is_boundary_outage,
                            sort_mode, page_size, offset)
            print(sql_event_lm)
            cursor.execute(sql_event_lm)
            event_rows_lm = cursor.fetchall()
        cursor.close()
        try:
            return event_rows_lm
        except:
            return []
    if if_table_exist(conn, event_table):
        sql_event = """
                select event_type, level, s_time, e_time, attacker_as, attacked_as, 
                event_info, detail_url, affected_prefix, attacker_org, attacked_org, 
                attacker_country, attacked_country, state, judge_reason,
                judge_userid, judge_username, judge_time, notify_userid, notify_username, notify_time
                from {}
                where level={} and event_type={} and is_domestic={} 
                and COALESCE(attacker_as, '') like {} and COALESCE(attacked_as, '') like {}
                and COALESCE(attacker_org, '') like {} and COALESCE(attacked_org, '') like {}
                and COALESCE(attacker_country, '') like {} and COALESCE(attacked_country, '') like {}
                and event_info like {} and s_time >= {} and s_time <= {} and COALESCE(judge_reason, '') like {}
                and COALESCE(judge_userid, '') like {} and COALESCE(judge_username, '') like {}
                and COALESCE(notify_userid, '') like {} and COALESCE(notify_username, '') like {}
                and state = {} and COALESCE(judge_time, DATE '0001-01-01') >= {} and COALESCE(judge_time, DATE '0001-01-01') <= {}
                and COALESCE(notify_time, DATE '0001-01-01') >= {} and COALESCE(notify_time, DATE '0001-01-01') <= {}
                and event_type {} '边界中断'
                and (duration >= '00:03:00' or duration is null)
                and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                order by {} 
                limit {} offset {};
            """.format(event_table, level, event_type, is_domestic, 
                    attacker_as, attacked_as, attacker_org, attacked_org, attacker_country, attacked_country, event_info, 
                    s_time_start, s_time_end, judge_reason, judge_userid, judge_username, 
                    notify_userid, notify_username, state,
                    judge_time_start, judge_time_end, notify_time_start, notify_time_end,
                    is_boundary_outage, 
                    sort_mode, page_size, offset)
        print(sql_event)
        try:
            cursor.execute(sql_event)
            event_rows = cursor.fetchall()
        except:
            traceback.print_exc()
            conn.rollback()
    try:
        return event_rows
    except:
        return []

def get_total_page(conn, last_month_table, event_table, page_size, level, event_type, country, attacker_as, attacked_as,
                   attacker_org, attacked_org, attacker_country, attacked_country, event_info, start_time, state, judge_reason, judge_userid, 
                   judge_username, judge_time, notify_userid, notify_username, notify_time):
    """
    获取特定条件下的事件总数和相应页数
    :param conn: _description_
    :param last_month_table: _description_
    :param event_table: _description_
    :param page_size: _description_
    :param level: _description_
    :param event_type: _description_
    :param country: _description_
    :param attacker_org: _description_
    :param attacked_org: _description_
    :param attacker_country: _description_
    :param attacked_country: _description_
    :param event_info: _description_
    :param start_time: _description_
    :param state: _description_
    :param judge_reason: _description_
    :param judge_userid: _description_
    :param judge_username: _description_
    :param judge_time: _description_
    :param notify_userid: _description_
    :param notify_username: _description_
    :param notify_time: _description_
    :return: _description_
    """    
    # 获取当前的UTC时间
    bj_now = datetime.datetime.now()

    # 一周之前的UTC时间
    bj_week_ago = bj_now - datetime.timedelta(days=20)
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # 筛选条件
    level = "'{}'".format(level) if level in ['high', 'middle', 'low'] else 'level'
    if event_type in ['前缀劫持', '子前缀劫持', '路由泄漏', '前缀中断', 'AS中断', '国家中断', '边界中断', 'RPKI证书异常']:
        event_type = "'{}'".format(event_type)      
    else:
        event_type = 'event_type'
    if country == 'all':
        is_domestic = 'is_domestic'
    elif country == 'foreign':
        is_domestic = False
    else:
        is_domestic = True
    

    attacked_as = "'%%'" if attacked_as == None else "'%{}%'".format(attacked_as)
    attacker_as = "'%%'" if attacker_as == None else "'%{}%'".format(attacker_as)
    attacker_org = "'%%'" if attacker_org == None else "'%{}%'".format(attacker_org)
    attacked_org = "'%%'" if attacked_org == None else "'%{}%'".format(attacked_org)
    attacker_country = "'%%'" if attacker_country == None else "'%{}%'".format(attacker_country)
    attacked_country = "'%%'" if attacked_country == None else "'%{}%'".format(attacked_country)
    event_info = "'%%'" if event_info == None else "'%{}%'".format(event_info)

    pattern = re.compile('^\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}')
    if start_time == None:
        s_time_start = s_time_end = 's_time'
    elif pattern.match(start_time):
        s_time_start = "'{}'".format(start_time.split('_')[0] + ' 00:00:00')
        s_time_end = "'{}'".format(start_time.split('_')[1] + ' 24:00:00')
    else:
        s_time_start = s_time_end = 's_time'
    if judge_time == None:
        judge_time_start = judge_time_end = "COALESCE(judge_time, DATE '0001-01-01')"
    elif pattern.match(judge_time):
        judge_time_start = "'{}'".format(judge_time.split('_')[0] + ' 00:00:00')
        judge_time_end = "'{}'".format(judge_time.split('_')[1] + ' 24:00:00')
    else:
        judge_time_start = judge_time_end = "COALESCE(judge_time, DATE '0001-01-01')"
    if notify_time == None:
        notify_time_start = notify_time_end = "COALESCE(notify_time, DATE '0001-01-01')"
    elif pattern.match(notify_time):
        notify_time_start = "'{}'".format(notify_time.split('_')[0] + ' 00:00:00')
        notify_time_end = "'{}'".format(notify_time.split('_')[1] + ' 24:00:00')
    else:
        notify_time_start = notify_time_end = "COALESCE(notify_time, DATE '0001-01-01')"
    
    judge_reason = "'%%'" if judge_reason == None else "'%{}%'".format(judge_reason)
    judge_userid = "'%%'" if judge_userid == None else "'%{}%'".format(judge_userid)
    judge_username = "'%%'" if judge_username == None else "'%{}%'".format(judge_username)
    notify_userid = "'%%'" if notify_userid == None else "'%{}%'".format(notify_userid)
    notify_username = "'%%'" if notify_username == None else "'%{}%'".format(notify_username)

    if state in ['judge', 'notify', 'misreport', 'suspected', 'notified']:
        state = "'{}'".format(state) 
    else:
        state = 'state'
    
    if event_type == "'边界中断'":
        is_boundary_outage = '='
    else:
        is_boundary_outage = '<>'
    
    if event_type == "'RPKI证书异常'":
        if if_table_exist(conn, event_table):
            sql_event = """
                    select count(*)
                    from {}
                    where level={} and event_type={} and COALESCE(is_domestic, True)={} 
                    and COALESCE(attacker_as, '') like {} and COALESCE(attacked_as, '') like {}
                    and COALESCE(attacker_org, '') like {} and COALESCE(attacked_org, '') like {}
                    and COALESCE(attacker_country, '') like {} and COALESCE(attacked_country, '') like {}
                    and s_time >= {} and s_time <= {} and COALESCE(judge_reason, '') like {}
                    and COALESCE(judge_userid, '') like {} and COALESCE(judge_username, '') like {}
                    and COALESCE(notify_userid, '') like {} and COALESCE(notify_username, '') like {}
                    and COALESCE(state, 'judge') = {} and COALESCE(judge_time, DATE '0001-01-01') >= {} and COALESCE(judge_time, DATE '0001-01-01') <= {}
                    and COALESCE(notify_time, DATE '0001-01-01') >= {} and COALESCE(notify_time, DATE '0001-01-01') <= {};
                """.format(event_table, level, event_type, is_domestic, attacker_as, attacked_as, attacker_org, attacked_org, attacker_country, attacked_country,
                        s_time_start, s_time_end, judge_reason, judge_userid, judge_username, 
                        notify_userid, notify_username, state,
                        judge_time_start, judge_time_end, notify_time_start, notify_time_end)
            print(sql_event)
            try:
                cursor.execute(sql_event)
                record_count = cursor.fetchone()[0]
                total_page = math.ceil(record_count / page_size)
            except:
                traceback.print_exc()
                conn.rollback()
                record_count, total_page = 0, 0
        cursor.close()
        return total_page, record_count

    if 1 <= bj_now.day <= 7:
        if if_table_exist(conn, last_month_table):
            if if_table_exist(conn, event_table):
                sql_event_lm = """
                    select count(*) from (
                        select event_type, level, s_time, e_time, attacker_as, attacked_as, 
                        event_info, detail_url, affected_prefix, attacker_org, attacked_org, 
                        attacker_country, attacked_country, state, judge_reason,
                        judge_userid, judge_username, judge_time, notify_userid, notify_username, notify_time
                        from {}
                        where level={} and event_type={} and is_domestic={} 
                        and COALESCE(attacker_as, '') like {} and COALESCE(attacked_as, '') like {}
                        and COALESCE(attacker_org, '') like {} and COALESCE(attacked_org, '') like {}
                        and COALESCE(attacker_country, '') like {} and COALESCE(attacked_country, '') like {}
                        and event_info like {} and s_time >= {} and s_time <= {} and COALESCE(judge_reason, '') like {}
                        and COALESCE(judge_userid, '') like {} and COALESCE(judge_username, '') like {}
                        and COALESCE(notify_userid, '') like {} and COALESCE(notify_username, '') like {}
                        and state = {} and s_time > '{}' 
                        and COALESCE(judge_time, DATE '0001-01-01') >= {} 
                        and COALESCE(judge_time, DATE '0001-01-01') <= {}
                        and COALESCE(notify_time, DATE '0001-01-01') >= {} 
                        and COALESCE(notify_time, DATE '0001-01-01') <= {}
                        and event_type {} '边界中断'
                        and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                        UNION ALL
                        select event_type, level, s_time, e_time, attacker_as, attacked_as, 
                        event_info, detail_url, affected_prefix, attacker_org, attacked_org, 
                        attacker_country, attacked_country, state, judge_reason,
                        judge_userid, judge_username, judge_time, notify_userid, notify_username, notify_time
                        from {}
                        where level={} and event_type={} and is_domestic={}
                        and COALESCE(attacker_as, '') like {} and COALESCE(attacked_as, '') like {}
                        and COALESCE(attacker_org, '') like {} and COALESCE(attacked_org, '') like {}
                        and COALESCE(attacker_country, '') like {} and COALESCE(attacked_country, '') like {}
                        and event_info like {} and s_time >= {} and s_time <= {} and COALESCE(judge_reason, '') like {}
                        and COALESCE(judge_userid, '') like {} and COALESCE(judge_username, '') like {}
                        and COALESCE(notify_userid, '') like {} and COALESCE(notify_username, '') like {}
                        and state = {} and s_time > '{}' 
                        and COALESCE(judge_time, DATE '0001-01-01') >= {} 
                        and COALESCE(judge_time, DATE '0001-01-01') <= {}
                        and COALESCE(notify_time, DATE '0001-01-01') >= {} 
                        and COALESCE(notify_time, DATE '0001-01-01') <= {}
                        and event_type {} '边界中断'
                        and (duration >= '00:03:00' or duration is null)
                        and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10 ) t;
                    """.format(last_month_table, level, event_type, is_domestic, 
                            attacker_as, attacked_as, attacker_org, attacked_org, attacker_country, attacked_country, event_info, 
                            s_time_start, s_time_end, judge_reason, judge_userid, judge_username, 
                            notify_userid, notify_username, state, bj_week_ago, 
                            judge_time_start, judge_time_end, notify_time_start, notify_time_end,
                            is_boundary_outage,
                            event_table, level, event_type, is_domestic, 
                            attacker_as, attacked_as, attacker_org, attacked_org, attacker_country, attacked_country, event_info, 
                            s_time_start, s_time_end, judge_reason, judge_userid, judge_username, 
                            notify_userid, notify_username, state, bj_week_ago, 
                            judge_time_start, judge_time_end, notify_time_start, notify_time_end,
                            is_boundary_outage)
            else:
                sql_event_lm = """
                        select count(*)
                        from {}
                        where level={} and event_type={} and is_domestic={} 
                        and COALESCE(attacker_as, '') like {} and COALESCE(attacked_as, '') like {}
                        and COALESCE(attacker_org, '') like {} and COALESCE(attacked_org, '') like {}
                        and COALESCE(attacker_country, '') like {} and COALESCE(attacked_country, '') like {}
                        and event_info like {} and s_time >= {} and s_time <= {} and state = {}
                        and s_time > '{}' and COALESCE(judge_reason, '') like {}
                        and COALESCE(judge_userid, '') like {} and COALESCE(judge_username, '') like {}
                        and COALESCE(notify_userid, '') like {} and COALESCE(notify_username, '') like {}
                        and COALESCE(judge_time, DATE '0001-01-01') >= {} 
                        and COALESCE(judge_time, DATE '0001-01-01') <= {}
                        and COALESCE(notify_time, DATE '0001-01-01') >= {} 
                        and COALESCE(notify_time, DATE '0001-01-01') <= {}
                        and event_type {} '边界中断'
                        and (duration >= '00:03:00' or duration is null)
                        and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                """.format(last_month_table, level, event_type, is_domestic, 
                            attacker_as, attacked_as, attacker_org, attacked_org, attacker_country, attacked_country, event_info, 
                            s_time_start, s_time_end, state, bj_week_ago,
                            judge_reason, judge_userid, judge_username, notify_userid, notify_username, 
                            judge_time_start, judge_time_end, notify_time_start, notify_time_end,
                            is_boundary_outage)
            cursor.execute(sql_event_lm)
            record_count = cursor.fetchone()[0]
            total_page = math.ceil(record_count / page_size)
        else:
            total_page, record_count = 0, 0
    else:
        total_page, record_count = 0, 0
    if if_table_exist(conn, event_table):
        sql_event = """
                    select count(*)
                    from {}
                    where level={} and event_type={} and is_domestic={} 
                    and COALESCE(attacker_as, '') like {} and COALESCE(attacked_as, '') like {}
                    and COALESCE(attacker_org, '') like {} and COALESCE(attacked_org, '') like {} 
                    and COALESCE(attacker_country, '') like {} and COALESCE(attacked_country, '') like {}
                    and event_info like {} and s_time >= {} and s_time <= {} and state = {}
                    and COALESCE(judge_reason, '') like {}
                    and COALESCE(judge_userid, '') like {} and COALESCE(judge_username, '') like {}
                    and COALESCE(notify_userid, '') like {} and COALESCE(notify_username, '') like {}
                    and COALESCE(judge_time, DATE '0001-01-01') >= {} 
                    and COALESCE(judge_time, DATE '0001-01-01') <= {}
                    and COALESCE(notify_time, DATE '0001-01-01') >= {} 
                    and COALESCE(notify_time, DATE '0001-01-01') <= {}
                    and event_type {} '边界中断'
                    and (duration >= '00:03:00' or duration is null)
                    and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                """.format(event_table, level, event_type, is_domestic, 
                            attacker_as, attacked_as, attacker_org, attacked_org, attacker_country, attacked_country, event_info, 
                            s_time_start, s_time_end, state, 
                            judge_reason, judge_userid, judge_username, notify_userid, notify_username, 
                            judge_time_start, judge_time_end, notify_time_start, notify_time_end, is_boundary_outage)
        cursor.execute(sql_event)
        record_count2 = cursor.fetchone()[0]
        total_page2 = math.ceil(record_count2 / page_size)
    else:
        total_page2, record_count2 = 0, 0
    cursor.close()
    total_page = total_page + total_page2
    record_count = record_count + record_count2
    return total_page, record_count


def deal_event(event_rows) -> list:
    res_list = list()
    for row in event_rows:
        event_type = row['event_type']
        level = row['level']
        s_time = str(row['s_time']).replace(' ', '\n')
        e_time = str(row['e_time'])
        attacker_as = row['attacker_as']
        attacked_as = row['attacked_as']
        event_info = row['event_info']
        detail_url = row['detail_url']
        affected_prefix = row['affected_prefix']
        attacker_org = row['attacker_org']
        attacked_org = row['attacked_org']
        attacker_country = row['attacker_country']
        attacked_country = row['attacked_country']
        state = row['state']
        judge_reason = row['judge_reason']
        judge_userid = row['judge_userid']
        judge_username = row['judge_username']
        judge_time = str(row['judge_time'])
        notify_userid = row['notify_userid']
        notify_username = row['notify_username']
        notify_time = str(row['notify_time'])
        d = dict()
        d['event_type'] = event_type
        d['level'] = level
        d['start_time'], d['end_time'] = s_time, e_time
        if e_time in ['None', None]:
            d['end_time'] = '-'
        d['attacker_as'], d['attacked_as'] = attacker_as, attacked_as
        d['event_info'] = event_info
        d['detail_url'] = detail_url
        d['affected_prefix'] = affected_prefix
        d['attacker_org'] = attacker_org
        d['attacked_org'] = attacked_org
        d['attacker_country'] = attacker_country
        d['attacked_country'] = attacked_country
        d['state'] = state
        d['judge_reason'] = judge_reason
        d['judge_userid'] = judge_userid 
        d['judge_username'] = judge_username 
        d['judge_time'] = judge_time
        d['notify_userid'] = notify_userid 
        d['notify_username'] = notify_username 
        d['notify_time'] = notify_time
        res_list.append(d)
    return res_list

def get_sort_event_count(conn, last_month_table, event_table, obj, country):
    """
    返回近一周的AS/机构拥有事件数量排名
    :param conn: 数据库连接
    :param last_month_table: 上一个月的数据表名
    :param event_table: 事件表
    :return: 事件列表
    """
    # 获取当前的UTC时间
    bj_now = datetime.datetime.now()

    # 一周之前的UTC时间
    bj_week_ago = bj_now - datetime.timedelta(days=15)
    # bj_week_ago = '2024-07-29 20:07:09.640001' # test
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    event_rows_lm = list()
    event_rows = list()

    # 筛选条件
    obj = 'attacked_org' if obj == 'org' else 'attacked_as' 
    attacked_country = "'中国'" if country == 'domestic' else 'attacked_country'

    if 1 <= bj_now.day <= 7: # and (duration >= '00:03:00' or duration is null)
        if if_table_exist(conn, last_month_table) and if_table_exist(conn, event_table):
            sql_event_lm = """
                    SELECT {}, attacked_country,  SUM(count) as count FROM
                    (select count(*), {}, attacked_country from {}
                    where event_type in('前缀劫持', '子前缀劫持', '路由泄漏', 'AS中断') 
                    and attacked_country={} and {} is not null and s_time > '{}' 
                    and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                    group by {}, attacked_country
                    UNION ALL
                    select count(*), {}, attacked_country from {}
                    where event_type in('前缀劫持', '子前缀劫持', '路由泄漏', 'AS中断') 
                    and attacked_country={} and {} is not null and s_time > '{}' 
                    and (duration >= '00:03:00' or duration is null)
                    and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                    group by {}, attacked_country) AS subquery GROUP BY {}, attacked_country
                    order by count desc, {} asc
                    limit 10;
                """.format(obj, 
                           obj, last_month_table, attacked_country, obj, bj_week_ago, obj,
                           obj, event_table, attacked_country, obj, bj_week_ago, obj, 
                           obj, obj)
            cursor.execute(sql_event_lm)
            event_rows_lm = cursor.fetchall()
        cursor.close()
        return event_rows_lm
    else:
        if if_table_exist(conn, event_table):
            sql_event = """
                        select count(*), {}, attacked_country from {}
                        where event_type in('前缀劫持', '子前缀劫持', '路由泄漏', 'AS中断') 
                        and attacked_country={} and {} is not null and s_time > '{}' 
                        and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                        and (duration >= '00:03:00' or duration is null)
                        group by {}, attacked_country
                        order by count desc, {} asc
                        limit 6;
                    """.format(obj, event_table, attacked_country, obj, bj_week_ago, obj, obj)
            try:
                cursor.execute(sql_event)
                print(sql_event)
                event_rows = cursor.fetchall()
            except:
                traceback.print_exc()
                conn.rollback()
                
        cursor.close()
        return event_rows

def get_country_two_letter_code(country_info_dict: dict, chinese_name: str):
    """
    返回国家的中文简称
    :param country_info_dict: 国家信息字典
    :param two_letter_code: 国家两字母简称
    :return: 国家的中文简称
    """
    if chinese_name is None:
        return None
    if chinese_name in country_info_dict:
        if country_info_dict[chinese_name].get('two_letter_code') != '':
            return country_info_dict[chinese_name].get('two_letter_code')
    if chinese_name == '欧盟':
        return 'EU'
    return chinese_name

def get_things_list(conn, last_month_table, event_table, attacked, obj, country):
    """
    返回近一周的AS/机构拥有事件数量排名
    :param conn: 数据库连接
    :param last_month_table: 上一个月的数据表名
    :param event_table: 事件表
    :return: 事件列表
    """
    # 获取当前的UTC时间
    bj_now = datetime.datetime.now()

    # 一周之前的UTC时间
    bj_week_ago = bj_now - datetime.timedelta(days=15)
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    event_rows_lm = list()
    event_rows = list()

    # 筛选条件
    obj = 'attacked_org' if obj == 'org' else 'attacked_as' 
    attacked_country = "'中国'" if country == 'domestic' else 'attacked_country'

    # TODO: if 1 <= bj_now.day <= 7:
    if 1 <= bj_now.day <= 7: # and (duration >= '00:03:00' or duration is null)
        if if_table_exist(conn, last_month_table) and if_table_exist(conn, event_table):
            sql_event_lm = """
                    SELECT event_type, SUM(count) as count FROM
                    (select count(*), event_type from {}
                        where event_type in('前缀劫持', '子前缀劫持', '路由泄漏', 'AS中断') 
                        and {}='{}'
                        and attacked_country={} and {} is not null and s_time > '{}' 
                        and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                        and (duration >= '00:03:00' or duration is null)
                        group by event_type
                    UNION ALL
                    select count(*), event_type from {}
                        where event_type in('前缀劫持', '子前缀劫持', '路由泄漏', 'AS中断') 
                        and {}='{}'
                        and attacked_country={} and {} is not null and s_time > '{}' 
                        and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                        and (duration >= '00:03:00' or duration is null)
                        group by event_type) AS subquery GROUP BY event_type
                    order by event_type;
                """.format(last_month_table, obj, attacked, attacked_country, obj, bj_week_ago, 
                           event_table, obj, attacked, attacked_country, obj, bj_week_ago)
            cursor.execute(sql_event_lm)
            event_rows_lm = cursor.fetchall()
        cursor.close()
        return event_rows_lm
    
    else:
        if if_table_exist(conn, event_table):
            sql_event = """
                        select count(*), event_type from {}
                        where event_type in('前缀劫持', '子前缀劫持', '路由泄漏', 'AS中断') 
                        and {}='{}'
                        and attacked_country={} and {} is not null and s_time > '{}' 
                        and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                        and (duration >= '00:03:00' or duration is null)
                        group by event_type
                        order by event_type;
                    """.format(event_table, obj, attacked, attacked_country, obj, bj_week_ago)
            try:
                cursor.execute(sql_event)
                print(sql_event)
                event_rows = cursor.fetchall()
            except:
                traceback.print_exc()
                conn.rollback()
                
        cursor.close()
        return event_rows

def deal_sort_event_count(conn, last_month_table, event_table, event_rows, obj, country, country_info) -> list:
    res_list = list()
    for row in event_rows:
        event_count = int(row['count'])
        if obj == 'org':
            attacked = row['attacked_org']
        else:
            attacked = row['attacked_as']
        attacked_country = row['attacked_country']
        d = dict()
        d['num'] = event_count
        d['attacked'] = attacked
        d['attacked_country'] = attacked_country
        two_letter_code = get_country_two_letter_code(country_info, attacked_country).lower()
        d['flag'] = 'flag-icon-' + two_letter_code
        d['thingsList'] = list()
        things_list = get_things_list(conn, last_month_table, event_table, attacked, obj, country)
        for r in things_list:
            di = dict()
            di['value'] = r['count']
            di['name'] = r['event_type']
            d['thingsList'].append(di)
        res_list.append(d)
    return res_list

def get_event_count(conn, last_month_table, event_table, country):
    """
    返回近30天的各等级国内/全球事件数量
    :param conn: 数据库连接
    :param last_month_table: 上一个月的数据表名
    :param event_table: 事件表
    :return: 事件列表
    """
    # 获取当前的UTC时间
    bj_now = datetime.datetime.now()

    # 一个月之前的UTC时间
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    event_rows_lm = list()
    event_rows = list()

    # 筛选条件
    is_domestic = True if country == 'domestic' else 'is_domestic'
    
    bj_month_ago = bj_now - datetime.timedelta(days=30)

    if 1 <= bj_now.day <= 30:
        if if_table_exist(conn, last_month_table) and if_table_exist(conn, event_table):
            sql_event_lm = """
                    select count(*), to_date(cast(s_time as TEXT), 'yyyy-MM-dd') as days 
                    from {}
                    where event_type in('前缀劫持', '子前缀劫持', '路由泄漏', 'AS中断', '边界中断') 
                    and is_domestic={} and s_time > '{}'
                    and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                    group by days
                    UNION ALL
                    select count(*), to_date(cast(s_time as TEXT), 'yyyy-MM-dd') as days 
                    from {}
                    where event_type in('前缀劫持', '子前缀劫持', '路由泄漏', 'AS中断', '边界中断') 
                    and is_domestic={} and s_time > '{}'
                    and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                    and (duration >= '00:03:00' or duration is null)
                    group by days
                    order by days desc;
                """.format(last_month_table, is_domestic, bj_month_ago, 
                            event_table, is_domestic, bj_month_ago)
            cursor.execute(sql_event_lm)
            event_rows_lm = cursor.fetchall()
            return event_rows_lm

    if if_table_exist(conn, event_table):
        sql_event = """
                    select count(*), to_date(cast(s_time as TEXT), 'yyyy-MM-dd') as days 
                    from {}
                    where event_type in('前缀劫持', '子前缀劫持', '路由泄漏', 'AS中断', '边界中断') 
                    and is_domestic={} and s_time > '{}'
                    and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                    and (duration >= '00:03:00' or duration is null)
                    group by days
                    order by days desc;
                """.format(event_table, is_domestic, bj_month_ago)         
        cursor.execute(sql_event)
        event_rows = cursor.fetchall()

    cursor.close()
    return event_rows

def deal_event_count(event_rows) -> list:
    res_list = list()
    if event_rows == []:
        return [] 
    for row in event_rows:
        event_count = row['count']
        time = str(row['days'])
        d = dict()
        d['num'] = event_count
        d['time'] = time
        res_list.append(d)
    return res_list

def get_geo_event_count(conn, last_month_table, event_table):
    """
    返回全球各国异常事件数量
    :param conn: 数据库连接
    :param last_month_table: 上一个月的数据表名
    :param event_table: 事件表
    :return: 事件列表
    """
    # 获取当前的UTC时间
    bj_now = datetime.datetime.now()

    # 一个月之前的UTC时间
    bj_week_ago = bj_now - datetime.timedelta(days=7)
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    event_rows_lm = list()
    event_rows = list()

    if 1 <= bj_now.day <= 7: # and (duration >= '00:03:00' or duration is null)
        if if_table_exist(conn, last_month_table) and if_table_exist(conn, event_table):
            sql_event_lm = """
                    SELECT attacked_country, SUM(count) as count FROM
                    (select count(*), attacked_country from {}
                    where event_type in('前缀劫持', '子前缀劫持', '路由泄漏', 'AS中断') 
                    and s_time > '{}' 
                    and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                    and (duration >= '00:03:00' or duration is null)
                    group by attacked_country
                    UNION ALL
                    select count(*), attacked_country from {}
                    where event_type in('前缀劫持', '子前缀劫持', '路由泄漏', 'AS中断') 
                    and s_time > '{}' 
                    and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                    and (duration >= '00:03:00' or duration is null)
                    group by attacked_country) AS subquery GROUP BY attacked_country
                    order by count desc, attacked_country asc;
                """.format(last_month_table, bj_week_ago, event_table, bj_week_ago)
            cursor.execute(sql_event_lm)
            event_rows_lm = cursor.fetchall()
        cursor.close()
        return event_rows_lm
    else:
        if if_table_exist(conn, event_table):
            sql_event = """
                        select count(*), attacked_country from {}
                        where event_type in('前缀劫持', '子前缀劫持', '路由泄漏', 'AS中断') 
                        and s_time > '{}' 
                        and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                        and (duration >= '00:03:00' or duration is null)
                        group by attacked_country
                        order by count desc, attacked_country asc;
                    """.format(event_table, bj_week_ago)
            try:
                cursor.execute(sql_event)
                event_rows = cursor.fetchall()
            except:
                traceback.print_exc()
                conn.rollback()
        cursor.close()
        return event_rows

def get_country_longitude(country_info_dict: dict, chinese_name: str):
    """
    返回国家的经度
    :param country_info_dict: 国家信息字典
    :param chinese_name: 国家两字母简称
    :return: 国家的经度
    """
    if chinese_name is None:
        return None
    if chinese_name in country_info_dict:
        if country_info_dict[chinese_name].get('longitude') != '':
            return country_info_dict[chinese_name].get('longitude')
    return None


def get_country_latitude(country_info_dict: dict, chinese_name: str):
    """
    返回国家的纬度
    :param country_info_dict: 国家信息字典
    :param chinese_name: 国家两字母简称
    :return: 国家的纬度
    """
    if chinese_name is None:
        return None
    if chinese_name in country_info_dict:
        if country_info_dict[chinese_name].get('latitude') != '':
            return country_info_dict[chinese_name].get('latitude')
    return None

def deal_geo_event_count(event_rows, country_info) -> list:
    res_list = list()
    for row in event_rows:
        event_count = int(row['count'])
        attacked_country = row['attacked_country']
        d = dict()
        d['name'] = attacked_country
        d['value'] = event_count
        d['lng'] = get_country_longitude(country_info, attacked_country)
        d['lat'] = get_country_latitude(country_info, attacked_country)
        res_list.append(d)
    return res_list


def get_type_event_count(conn, last_month_table, event_table, country, event_type):
    """
    返回今天和昨天的AS/机构拥有事件数量排名
    :param conn: 数据库连接
    :param last_month_table: 上一个月的数据表名
    :param event_table: 事件表
    :param event_type: 事件类型
    :return: 事件列表
    """
    # 获取当前的UTC时间
    bj_now = datetime.datetime.now()

    # 一天前、两天前的UTC时间
    bj_day_ago = bj_now - datetime.timedelta(days=1)
    two_days_ago = bj_now - datetime.timedelta(days=2)
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    event_rows_lm = list()
    event_rows = list()

    # 筛选条件
    is_domestic = 'True' if country == 'domestic' else 'is_domestic'

    if bj_now.day in [1, 2]: 
        if if_table_exist(conn, last_month_table) and if_table_exist(conn, event_table):
            sql_event_td = """
                    SELECT event_type, SUM(count) as count FROM
                    (select count(*), event_type from {}
                    where s_time > '{}' and is_domestic={} and event_type='{}'
                    and (duration >= '00:03:00' or duration is null)
                    and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                    group by event_type
                    UNION ALL
                    select count(*), event_type from {}
                    where s_time > '{}' and is_domestic={} and event_type='{}'
                    and (duration >= '00:03:00' or duration is null)
                    and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                    group by event_type) AS subquery GROUP BY event_type;
                """.format(last_month_table, bj_day_ago, is_domestic, event_type, 
                            event_table, bj_day_ago, is_domestic, event_type)
            sql_event_yd = """
                    SELECT event_type, SUM(count) as count FROM
                    (select count(*), event_type from {}
                    where s_time < '{}' and s_time > '{}' and is_domestic={} and event_type='{}'
                    and (duration >= '00:03:00' or duration is null)
                    and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                    group by event_type
                    UNION ALL
                    select count(*), event_type from {}
                    where s_time < '{}' and s_time > '{}' and is_domestic={} and event_type='{}'
                    and (duration >= '00:03:00' or duration is null)
                    and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                    group by event_type) AS subquery GROUP BY event_type;
                """.format(last_month_table, bj_day_ago, two_days_ago, is_domestic, event_type, 
                            event_table, bj_day_ago, two_days_ago, is_domestic, event_type)
            cursor.execute(sql_event_td)
            event_rows_td = cursor.fetchall()
            cursor.execute(sql_event_yd)
            event_rows_yd = cursor.fetchall()
        cursor.close()
        return event_rows_td, event_rows_yd
    else:
        if if_table_exist(conn, event_table):
            sql_event_td = """
                        select count(*), event_type from {}
                        where s_time > '{}' and is_domestic={} and event_type='{}'
                        and (duration >= '00:03:00' or duration is null)
                        and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                        group by event_type;
                    """.format(event_table, bj_day_ago, is_domestic, event_type)
            sql_event_yd = """
                        select count(*), event_type from {}
                        where s_time < '{}' and s_time > '{}' and is_domestic={} and event_type='{}'
                        and (duration >= '00:03:00' or duration is null)
                        and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                        group by event_type;
                    """.format(event_table, bj_day_ago, two_days_ago, is_domestic, event_type)
            try:
                cursor.execute(sql_event_td)
                event_rows_td = cursor.fetchall()
                cursor.execute(sql_event_yd)
                print(sql_event_td, sql_event_yd)
                event_rows_yd = cursor.fetchall()
            except:
                traceback.print_exc()
                conn.rollback()
        cursor.close()
        return event_rows_td, event_rows_yd

def deal_type_event_count(event_rows_td, event_rows_yd, event_type) -> list:
    count_td = int(event_rows_td[0]['count']) if len(event_rows_td) > 0 else 0
    count_yd = int(event_rows_yd[0]['count']) if len(event_rows_yd) > 0 else 0

    if count_yd == 0:
        AmplitudeType = True
        Amplitude = "0%"
    elif count_td >= count_yd:
        AmplitudeType = True  
        Amplitude = "%.2f%%" % ((count_td - count_yd) / count_yd * 100)
    else:
        AmplitudeType = False  
        Amplitude = "%.2f%%" % ((count_yd - count_td) / count_yd * 100)
    d = dict()
    d['event_type'] = event_type
    d['num'] = count_td
    d['amplitude_type'] = AmplitudeType
    d['amplitude'] = str(Amplitude)
    if event_type in ['前缀中断', 'AS中断']:
        d['icon'] = 'iconfont icon-zaosheng'
    elif event_type in ['国家中断', '前缀劫持', '子前缀劫持']:
        d['icon'] = 'icon-dongtai'
    else:
        d['icon'] = 'iconfont icon-ditu'
    return d

def get_level_event_count(conn, last_month_table, event_table, country, level):
    """
    返回今天和昨天的AS/机构拥有事件数量排名
    :param conn: 数据库连接
    :param last_month_table: 上一个月的数据表名
    :param event_table: 事件表
    :param level: 事件等级
    :return: 事件列表
    """
    # 获取当前的UTC时间
    bj_now = datetime.datetime.now()

    # 一天前、两天前的UTC时间
    bj_day_ago = bj_now - datetime.timedelta(days=1)
    two_days_ago = bj_now - datetime.timedelta(days=2)
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    event_rows_lm = list()
    event_rows = list()

    # 筛选条件
    is_domestic = 'True' if country == 'domestic' else 'is_domestic'

    if bj_now.day in [1, 2]: 
        if if_table_exist(conn, last_month_table) and if_table_exist(conn, event_table):
            sql_event_td = """
                    SELECT level, SUM(count) as count FROM
                    (select count(*), level from {}
                    where s_time > '{}' and is_domestic={} and level='{}'
                    and (duration >= '00:03:00' or duration is null)
                    and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                    group by level
                    UNION ALL
                    select count(*), level from {}
                    where s_time > '{}' and is_domestic={} and level='{}'
                    and (duration >= '00:03:00' or duration is null)
                    and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                    group by level) AS subquery GROUP BY level;
                """.format(last_month_table, bj_day_ago, is_domestic, level, 
                            event_table, bj_day_ago, is_domestic, level)
            sql_event_yd = """
                    SELECT level, SUM(count) as count FROM
                    (select count(*), level from {}
                    where s_time < '{}' and s_time > '{}' and is_domestic={} and level='{}'
                    and (duration >= '00:03:00' or duration is null)
                    and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                    group by level
                    UNION ALL
                    select count(*), level from {}
                    where s_time < '{}' and s_time > '{}' and is_domestic={} and level='{}'
                    and (duration >= '00:03:00' or duration is null)
                    and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                    group by level) AS subquery GROUP BY level;
                """.format(last_month_table, bj_day_ago, two_days_ago, is_domestic, level, 
                            event_table, bj_day_ago, two_days_ago, is_domestic, level)
            cursor.execute(sql_event_td)
            event_rows_td = cursor.fetchall()
            cursor.execute(sql_event_yd)
            event_rows_yd = cursor.fetchall()
        cursor.close()
        return event_rows_td, event_rows_yd
    else:
        if if_table_exist(conn, event_table):
            sql_event_td = """
                        select count(*), level from {}
                        where s_time > '{}' and is_domestic={} and level='{}'
                        and event_type <> '前缀中断' and event_type <> '边界中断'
                        and (duration >= '00:03:00' or duration is null)
                        and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                        group by level;
                    """.format(event_table, bj_day_ago, is_domestic, level)
            sql_event_yd = """
                        select count(*), level from {}
                        where s_time < '{}' and s_time > '{}' and is_domestic={} and level='{}'
                        and event_type <> '前缀中断' and event_type <> '边界中断'
                        and (duration >= '00:03:00' or duration is null)
                        and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                        group by level;
                    """.format(event_table, bj_day_ago, two_days_ago, is_domestic, level)
            try:
                cursor.execute(sql_event_td)
                event_rows_td = cursor.fetchall()
                cursor.execute(sql_event_yd)
                print(sql_event_td, sql_event_yd)
                event_rows_yd = cursor.fetchall()
            except:
                traceback.print_exc()
                conn.rollback()
        cursor.close()
        return event_rows_td, event_rows_yd

def deal_level_event_count(event_rows_td, event_rows_yd, level) -> list:
    count_td = int(event_rows_td[0]['count']) if len(event_rows_td) > 0 else 0
    count_yd = int(event_rows_yd[0]['count']) if len(event_rows_yd) > 0 else 0

    if count_yd == 0:
        AmplitudeType = True
        Amplitude = "0%"
    elif count_td >= count_yd:
        AmplitudeType = True  
        Amplitude = "%.2f%%" % ((count_td - count_yd) / count_yd * 100)
    else:
        AmplitudeType = False  
        Amplitude = "%.2f%%" % ((count_yd - count_td) / count_yd * 100)
    d = dict()
    d['level'] = level
    d['num'] = count_td
    d['amplitude_type'] = AmplitudeType
    d['amplitude'] = Amplitude
    return d

def get_vp_state(conn, prefix_count_table, vp):
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    vp_rows = list()

    if if_table_exist(conn, prefix_count_table):
        sql_vp = """
                select * 
                from {}
                where vp = '{}'
                order by time desc
                limit 1;
            """.format(prefix_count_table, vp)
        # sql_vp = """
        #         select * 
        #         from {}
        #         where vp = '{}'
        #         order by time desc
        #         limit 2;
        #     """.format(prefix_count_table, vp)        
        print(sql_vp)
        try:
            cursor.execute(sql_vp)

            vp_rows = cursor.fetchall()
        except:
            conn.rollback()
        cursor.close()
    return vp_rows

def deal_vp_state(vp_rows) -> list:
    if len(vp_rows) == 0:
        return []
    for row in vp_rows:
        d = dict()
        d['vp'] = row['vp']
        d['time'] = str(row['time'])
        d['ipv4_prefix_count'] = row['ipv4_prefix_count']
        d['ipv6_prefix_count'] = row['ipv6_prefix_count']
        d['private_as_count'] = row['private_as_count']
        d['path_count'] = row['path_count']
        d['public_as_count'] = row['public_as_count']
        d['is_outlier'] = row['is_outlier']
        d['ipv4_prefix_normal_upper'] = row['ipv4_prefix_normal_upper']
        d['ipv4_prefix_normal_lower'] = row['ipv4_prefix_normal_lower']
        d['ipv6_prefix_normal_upper'] = row['ipv6_prefix_normal_upper']
        d['ipv6_prefix_normal_lower'] = row['ipv6_prefix_normal_lower']
        d['private_as_normal_upper'] = row['private_as_normal_upper']
        d['private_as_normal_lower'] = row['private_as_normal_lower']
        d['path_normal_upper'] = row['path_normal_upper']
        d['path_normal_lower'] = row['path_normal_lower']
        d['public_as_normal_upper'] = row['public_as_normal_upper']
        d['public_as_normal_lower'] = row['public_as_normal_lower']
    return d

def get_china_city_outage(conn, last_month_table, event_table):
    # 获取当前的UTC时间
    bj_now = datetime.datetime.now()

    # 一个月之前的UTC时间
    bj_week_ago = bj_now - datetime.timedelta(days=70)
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    event_rows_lm = list()
    event_rows = list()

    if 1 <= bj_now.day <= 7: 
        if if_table_exist(conn, last_month_table) and if_table_exist(conn, event_table):
            sql_event_lm = """
                    SELECT private_as_city, SUM(count) as count FROM
                    (select count(*), private_as_city 
                    from {} 
                    where is_private_as = true and s_time > '{}'
                    and private_as_city <> 'not found'
                    and outage_id < 10
                    and (duration >= '00:03:00' or duration is null)
                    group by private_as_city
                    UNION ALL
                    select count(*), private_as_city 
                    from {} 
                    where is_private_as = true and s_time > '{}'
                    and private_as_city <> 'not found'
                    and outage_id < 10
                    and (duration >= '00:03:00' or duration is null)
                    group by private_as_city) AS subquery GROUP BY private_as_city
                    order by count desc, private_as_city asc;
                """.format(last_month_table, bj_week_ago, event_table, bj_week_ago)
            cursor.execute(sql_event_lm)
            event_rows_lm = cursor.fetchall()
        cursor.close()
        return event_rows_lm
    else:
        if if_table_exist(conn, event_table):
            sql_event = """
                        select count(*), private_as_city 
                        from {} 
                        where is_private_as = true and s_time > '{}'
                        and private_as_city <> 'not found'
                        and outage_id < 10
                        
                        group by private_as_city
                        order by count desc, private_as_city asc;
                    """.format(event_table, bj_week_ago)
            try:
                cursor.execute(sql_event)
                event_rows = cursor.fetchall()
            except:
                traceback.print_exc()
                conn.rollback()
        cursor.close()
        return event_rows

def deal_china_city_outage(event_rows) -> list:
    res_list = list()
    for row in event_rows:
        event_count = int(row['count'])
        attacked_country = row['private_as_city']
        d = dict()
        d['city'] = attacked_country
        d['value'] = event_count
        res_list.append(d)
    return res_list

def get_state_event_count(conn, last_month_table, event_table, country, state):
    """
    返回今天和昨天的AS/机构拥有事件数量排名
    :param conn: 数据库连接
    :param last_month_table: 上一个月的数据表名
    :param event_table: 事件表
    :param event_type: 事件类型
    :return: 事件列表
    """
    # 获取当前的UTC时间
    bj_now = datetime.datetime.now()

    # 一天前、两天前的UTC时间
    bj_week_ago = bj_now - datetime.timedelta(days=60)
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    event_rows_lm = list()
    event_rows = list()

    # 筛选条件
    is_domestic = 'True' if country == 'domestic' else 'is_domestic'

    if 1 <= bj_now.day <= 7:
        if if_table_exist(conn, last_month_table):
            sql_event = """
                    select count(*) from {}
                    where s_time > '{}' and is_domestic={} and state='{}'
                    and (duration >= '00:03:00' or duration is null)
                    and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10;
                """.format(last_month_table, bj_week_ago, is_domestic, state)
            cursor.execute(sql_event)
            count_lm = cursor.fetchone()[0]
        else:
            count_lm = 0
        if if_table_exist(conn, event_table):
            sql_event = """
                    select count(*) from {}
                    where s_time > '{}' and is_domestic={} and state='{}'
                    and (duration >= '00:03:00' or duration is null)
                    and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10;
                """.format(event_table, bj_week_ago, is_domestic, state)
            cursor.execute(sql_event)
            count = cursor.fetchone()[0]
        else:
            count = 0
        cursor.close()
        count = count + count_lm
        return count
    else:
        if if_table_exist(conn, event_table):
            sql_event = """
                        select count(*) from {}
                        where s_time > '{}' and is_domestic={} and state='{}'
                        and (duration >= '00:03:00' or duration is null)
                        and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10;
                    """.format(event_table, bj_week_ago, is_domestic, state)
            print(sql_event)
            try:
                cursor.execute(sql_event)
                count = cursor.fetchone()[0]
            except:
                traceback.print_exc()
                conn.rollback()
        else:
            count = 0
        cursor.close()
        return count

def deal_state_event_count(count, state):
    d = dict()
    d['name'] = state
    d['value'] = int(count)
    d['itemStyle'] = dict()
    if state == 'judge':
        d['name'] = '待研判事件'
        d['itemStyle']['color'] = 'rgb(153,169,191)'
    elif state == 'notify':
        d['name'] = '待通报事件'
        d['itemStyle']['color'] = '#d41a1a'
    elif state == 'notified':
        d['name'] = '已通报事件'
        d['itemStyle']['color'] = '#FFD700'
    elif state == 'suspected':
        d['name'] = '疑似事件'
        d['itemStyle']['color'] = '#d4831a'
    elif state == 'misreport':
        d['name'] = '误报事件'
        d['itemStyle']['color'] = '#1ad45b'
    else:
        d['itemStyle']['color'] = 'rgb(153,169,191)'
    return d 

##### 绘制feature图所需数据
def get_as_features(conn, target, start_time, end_time, table_name="bgp_feature"):
    """从数据库中获取数据Features数据"""
    cursor = conn.cursor()
    try:
        sql = f"""
            SELECT t, asn, announ_num as announce, withdraw_num as withdraw
            FROM {table_name}
            WHERE asn = %s and t >= %s and t <= %s
            order by t asc
        """
        cursor.execute(sql, (target, start_time, end_time))
        data = cursor.fetchall()
        df = pd.DataFrame(data, columns=['t', 'asn', 'announce', 'withdraw'])
    except Exception as e:
        conn.rollback()
        print(f"获取Features数据失败: {e}")
        print(traceback.format_exc())
        return None
    finally:
        cursor.close()
    return df


def get_collect_features(conn, target, start_time, end_time, table_name="bgp_feature"):
    """获取某个时间段内收集点的Features数据
    Returns:
        list: 包含Features数据的列表 asn为collect 代表所有as的总和
    """
    cursor = conn.cursor()
    try:
        sql = f"""
            SELECT t, sum(announ_num) as announce, sum(withdraw_num) as withdraw
            FROM {table_name}
            WHERE t >= %s and t <= %s
            group by t
        """
        cursor.execute(sql, (start_time, end_time))
        data = cursor.fetchall()
        df = pd.DataFrame(data, columns=['t', 'announce', 'withdraw'])
        df["asn"] = "路由采集点"
        return df
    except Exception as e:
        conn.rollback()
        print(f"获取Features数据失败: {e}")
        print(traceback.format_exc())
        return None
    

# def get_domestic_features(conn, target, start_time, end_time, table_name="bgp_feature"):
#     """获取国内路由采集点的Features数据"""
#     cursor = conn.cursor()
#     try:
#         sql = f"""
#             SELECT t, sum(announ_num) as announce, sum(withdraw_num) as withdraw
#             FROM {table_name}
#             WHERE asn in ('4837', '4134', '9808') and t >= %s and t <= %s
#             group by t
#             order by t asc
#         """
#         cursor.execute(sql, (target, start_time, end_time))
#         data = cursor.fetchall()
#         df = pd.DataFrame(data, columns=['t', 'announce', 'withdraw'])
#         df["asn"] = "国内路由采集点"
#         return df
#     except Exception as e:
#         conn.rollback()
#         print(f"获取Features数据失败: {e}")
#         print(traceback.format_exc())


def get_country_features(conn, target, start_time, end_time, table_name="bgp_feature"):
    """获取某个国家在某个时间段内的Features数据
    """
    sql = f"""
        SELECT bf.t, 
                sum(bf.announ_num) as announce, 
                sum(bf.withdraw_num) as withdraw
        FROM {table_name} bf INNER JOIN as_info af ON bf.asn = af.asn
        WHERE af.as_country_cn = %s AND bf.t >= %s AND bf.t <= %s
        GROUP BY bf.t
        ORDER BY bf.t ASC
    """
    cursor = conn.cursor()
    try:
        cursor.execute(sql, (target, start_time, end_time))
        data = cursor.fetchall()
        df = pd.DataFrame(data, columns=['t', 'announce', 'withdraw'])
        df["asn"] = target
    except Exception as e:
        conn.rollback()
        print(f"获取Features数据失败: {e}")
        print(traceback.format_exc())
        return None
    finally:
        cursor.close()

    return df

def deal_features(d):
    # 返回给前端 t, announce, withdraw
    # 转换为字典
    if len(d) > 0:
        d = d.to_dict(orient='records')
        for item in d:
            item['t'] = str(item['t'])
    else:
        d = []
    return d
    
def get_country_list(conn, country, table_name="as_info"):
    """获取国家列表
    Args:
        conn: 数据库连接
        country: 国家名称，支持模糊搜索
    Returns:
        list: 包含国家名称的列表
    """
    sql = f"""
        SELECT distinct as_country_cn FROM {table_name} WHERE as_country_cn LIKE '%{country}%' and as_country_cn != '未知'
    """
    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        data = cursor.fetchall()
        country_list = [item[0] for item in data]
    except Exception as e:
        conn.rollback()
        print(f"获取国家列表失败: {e}")
        print(traceback.format_exc())
        return []
    finally:
        cursor.close()
    return country_list


def get_country_feature_list(conn, start_time, end_time, country, page_num, page_size, table_name="bgp_feature"):
    """获取国家时序特征列表
    Args:
        conn: 数据库连接
        start_time: 开始时间
        end_time: 结束时间
        country: 国家列表，支持模糊搜索
        page_num: 页码
        page_size: 每页大小
    Returns:
        dict: {
            'total_page': total_page,
            'record_count': total_countries,
            'current_page': page_num,
            'page_size': page_size,
            'data': [
                {
                    'country': '国家名称',
                    'time_series_data': [
                        {'time': '时间', 'announce': int, 'withdraw': int}
                    ]
                }
            ]
        }
    """

    # 第一步：获取国家列表
    country_list = get_country_list(conn, country)
    total_countries = len(country_list)

    print(total_countries)

    # 第二步：分页处理，获取当前页的国家列表
    # 计算总页数
    total_page = total_countries // page_size + 1 if total_countries > 0 else 1
    # 分页处理，获取当前页的国家列表
    start_index = (page_num - 1) * page_size
    end_index = start_index + page_size
    current_page_countries = country_list[start_index:end_index]
    if not current_page_countries:
        return {
            'total_page': total_page,
            'record_count': total_countries,
            'current_page': page_num,
            'page_size': page_size,
            'data': []
        }

    # 第三步：获取当前页国家的所有时序数据
    placeholders = ','.join(['%s'] * len(current_page_countries))
    sql = f"""
        SELECT af.as_country_cn, bf.t, sum(bf.announ_num) as announce, sum(bf.withdraw_num) as withdraw
        FROM {table_name} bf INNER JOIN as_info af ON bf.asn = af.asn
        WHERE af.as_country_cn IN ({placeholders}) AND bf.t >= '{start_time}' AND bf.t <= '{end_time}'
        GROUP BY af.as_country_cn, bf.t
        ORDER BY af.as_country_cn, bf.t
    """

    cursor = conn.cursor()
    try:
        cursor.execute(sql, current_page_countries)
        data = cursor.fetchall()
        df = pd.DataFrame(data, columns=['country', 't', 'announce', 'withdraw'])

        result_data = []
        for country_name in current_page_countries:
            country_df = df[df['country'] == country_name]
            
            if not country_df.empty:
                time_series_data = []
                for _, row in country_df.iterrows():
                    time_series_data.append({
                        "time": str(row['t']),
                        "announce": int(row['announce']),
                        "withdraw": int(row['withdraw'])
                    })
            else:
                time_series_data = []
            
            result_data.append({
                "country": country_name,
                "time_series_data": time_series_data
            })
        print("result_data", result_data)
        return {
            'total_page': total_page,
            'record_count': total_countries,
            'current_page': page_num,
            'page_size': page_size,
            'data': result_data
        }
    except Exception as e:
        conn.rollback()
        print(f"获取时序数据失败: {e}")
        print(traceback.format_exc())
        return None
    finally:
        cursor.close()
        # return None



def get_as_outage_list(conn, start_time, end_time, country):
    """返回在end_time之前中断的as列表
    Args:
        conn: 数据库连接
        start_time: 开始时间
        end_time: 结束时间
        table_name: 表名
    Returns:
        list: as中断的列表
    """

    # 根据时间获取对应的as_outage表 as_outage_202506
    as_outage_table = 'as_outage_' + end_time[0:7].replace('-', '')


    print("country:", country)
    # 获取as_outage表的as列表
    if country == '' or country is None:
        sql = f"""
        SELECT distinct asn
        FROM {as_outage_table}
        WHERE (e_time is null or e_time >= '{end_time}') 
            and (country != '未知' or country is not null)
        """ 
    else:
        sql = f"""
            SELECT distinct asn
            FROM {as_outage_table}
            WHERE (e_time is null or e_time >= '{end_time}') 
                and country = '{country}'
                and (country != '未知' or country is not null)
        """
    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        data = cursor.fetchall()
        as_list = [item[0] for item in data]
    except Exception as e:
        conn.rollback()
        print(f"获取AS断网列表失败: {e}")
        print(traceback.format_exc())
        return []
    finally:
        cursor.close()
    return as_list

def get_as_features_list(conn, start_time, end_time, target_asn, country, page_num, page_size, table_name="bgp_feature"):
    """获取as的时序特征列表
    Args:
        conn: 数据库连接
        start_time: 开始时间
        end_time: 结束时间
        target_asn: ASN
        country: 国家
        page_num: 页码
        page_size: 每页大小
        table_name: 表名
    Returns:
        dict: {
            'total_page': total_page,
            'record_count': total_as_count,
            'current_page': page_num,
            'page_size': page_size,
            'data': [
                {
                    'asn': 'ASN',
                    'as_name': 'AS名称',
                    'country': '国家',
                    'org_name': '组织名称',
                    'time_series_data': [
                        {'time': '时间', 'announce': int, 'withdraw': int}
                    ]
                }
            ]
        }
    """

    if target_asn:
        as_list = [target_asn]
    else:
        as_list = get_as_outage_list(conn, start_time, end_time, country)

    if not as_list:
        return []
    total_as_count = len(as_list)
    total_page = total_as_count // page_size + 1 if total_as_count > 0 else 1
    start_index = (page_num - 1) * page_size
    end_index = start_index + page_size
    current_page_as_list = as_list[start_index:end_index]
    if not current_page_as_list:
        return {
            'total_page': total_page,
            'record_count': total_as_count,
            'current_page': page_num,
            'page_size': page_size,
            'data': []
        }
    placeholders = ','.join(['%s'] * len(current_page_as_list))
    sql = f"""
        SELECT asn, as_name, as_country_cn, org_name_cn
        FROM as_info
        WHERE asn IN ({placeholders})
            and (as_country_cn != '未知' or as_country_cn is not null)
    """
    # 获取as的时序特征
    as_features_sql = f"""
        SELECT bf.t, 
                bf.asn,
                sum(bf.announ_num) as announce, 
                sum(bf.withdraw_num) as withdraw
        FROM {table_name} bf INNER JOIN as_info af ON bf.asn = af.asn
        WHERE bf.asn IN ({placeholders}) AND bf.t >= '{start_time}' AND bf.t <= '{end_time}'
        GROUP BY bf.asn, bf.t
        ORDER BY bf.asn, bf.t
    """
    cursor = conn.cursor()
    try:
        cursor.execute(sql, current_page_as_list)
        data = cursor.fetchall()
        df = pd.DataFrame(data, columns=['asn', 'as_name', 'as_country_cn', 'org_name_cn'])

        cursor.execute(as_features_sql, current_page_as_list)
        as_features_data = cursor.fetchall()
        as_features_df = pd.DataFrame(as_features_data, columns=['t', 'asn', 'announce', 'withdraw'])

        result_data = []
        for asn in current_page_as_list:
            as_df = df[df['asn'] == asn]
            as_features_df_ = as_features_df[as_features_df['asn'] == asn]
            if not as_df.empty and not as_features_df_.empty:
                time_series_data = []
                for _, row in as_features_df_.iterrows():
                    time_series_data.append({
                        "time": str(row['t']),
                        "announce": int(row['announce']), 
                        "withdraw": int(row['withdraw'])
                    })
                result_data.append({
                    "asn": asn,
                    "as_name": as_df['as_name'].values[0],
                    "country": as_df['as_country_cn'].values[0],
                    "org_name": as_df['org_name_cn'].values[0],
                    "time_series_data": time_series_data
                })
            elif not as_df.empty:
                result_data.append({
                    "asn": asn,
                    "as_name": as_df['as_name'].values[0],
                    "country": as_df['as_country_cn'].values[0],
                    "org_name": as_df['org_name_cn'].values[0],
                    "time_series_data": []
                })
            else:
                result_data.append({
                    "asn": asn,
                    "as_name": "",
                    "country": "",
                    "org_name": "",
                    "time_series_data": []
                })

        return {
            'total_page': total_page,
            'record_count': total_as_count,
            'current_page': page_num,
            'page_size': page_size,
            'data': result_data
        }
    except Exception as e:
        conn.rollback()
        print(f"获取时序数据失败: {e}")
        print(traceback.format_exc())
        return None
    finally:
        cursor.close()

### 
def get_tables_by_time(table_type, start_time, end_time):
    """
    根据时间区间（一个月一张表）获取对应的表名列表
    Args:
        conn: 数据库连接
        table_type: 表类型，'prefix' 或 'asn'
        start_time: 开始时间
        end_time: 结束时间
    Returns:
        list: 表名列表
    """
    # 获取start_time的年月 以确定时间表
    start_time = datetime.datetime.strptime(start_time, '%Y-%m-%d %H:%M:%S')
    end_time = datetime.datetime.strptime(end_time, '%Y-%m-%d %H:%M:%S')
    start_time_year_month = start_time.strftime('%Y%m')
    end_time_year_month = end_time.strftime('%Y%m')
    table_name = table_type + '_outage_' + start_time_year_month
    table_name_list = []
    while start_time_year_month <= end_time_year_month:
        table_name_list.append(table_name)
        start_time = start_time + relativedelta(months=1)
        start_time_year_month = start_time.strftime('%Y%m')
        table_name = table_type + '_outage_' + start_time_year_month
    return table_name_list



## 获取as事件列表
#### TODO: 需要修改，现在只能获取一张表的结果，需要修改为获取多张表的结果
def get_as_outage_by_interval(conn, start_time, end_time, country, table_name):
    """
    获取与指定时间范围重叠的AS中断记录。
    """
    sql = f"""
    SELECT DISTINCT asn, s_time, e_time
        FROM {table_name}
        WHERE country = '{country}'
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
        df = pd.DataFrame(data, columns=['asn', 's_time', 'e_time'])
    except Exception as e:
        conn.rollback()
        print(f"获取AS中断记录失败: {e}")
        print(traceback.format_exc())
        return []
    finally:
        cursor.close()
    return df

def get_prefix_outage_by_interval(conn, start_time, end_time, country, asn, table_name):
    """
    安全地获取与指定时间范围重叠的前缀中断记录。
    """
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    # 使用列表来动态构建WHERE子句，防止SQL注入
    # 核心逻辑：检查事件的 [s_time, e_time] 区间是否与查询的 [start_time, end_time] 区间有重叠
    where_clauses = ["s_time <= %s", "(e_time >= %s OR e_time IS NULL)"]
    params = [end_time, start_time]
    
    if country:
        where_clauses.append("country = %s")
        params.append(country)
        
    if asn:
        where_clauses.append("asn = %s")
        params.append(asn)

    # 注意：在生产环境中，table_name 也应被验证以防止SQL注入
    sql_query = f"SELECT DISTINCT prefix, s_time, e_time FROM {table_name} WHERE {' AND '.join(where_clauses)}"

    try:
        cursor.execute(sql_query, tuple(params))
        outage_rows = cursor.fetchall()
        # 将结果转换为pandas DataFrame
        df = pd.DataFrame(outage_rows, columns=['prefix', 's_time', 'e_time'])
        return df
    except Exception as e:
        print(f"Error in get_prefix_outage_by_interval: {e}")
        traceback.print_exc()
        # 在出错时返回一个空的DataFrame
        return pd.DataFrame(columns=['prefix', 's_time', 'e_time'])
    finally:
        cursor.close()

def deal_outage(df, type, start_time, end_time, prefixes, interval_minutes=3):
    """
    根据中断事件的 DataFrame，计算每个时间间隔内的并发中断前缀数量。
    Args:
        df (pd.DataFrame): 包含 'prefix' 或 'asn', 's_time', 'e_time' 的 DataFrame。
        type (str): 中断类型，'prefix' 或 'asn'。
        start_time (str): 分析的开始时间。
        end_time (str): 分析的结束时间。
        prefixes (list): 前缀列表。
        interval_minutes (int): 时间间隔（分钟）。

    Returns:
        # pd.DataFrame: 包含 'time_slot' 和 'outage_count' 的结果 DataFrame。
        [
            {
                'time_slot': '时间',
                'outage_count': '中断数'
            }
        ]
    """
    if type == 'prefix':
        # 去除细路由，只保留粗路由
        df = df[df['prefix'].isin(prefixes)]
        df = df.drop_duplicates(subset=['prefix', 's_time']).copy()
    
    
    # 1. 生成时间序列
    time_slots = pd.date_range(start=start_time, end=end_time, freq=f'{interval_minutes}min')
    time_df = pd.DataFrame({'time_slot': time_slots})

    if df.empty:
        # 如果没有中断数据，直接返回带0计数的时间序列
        time_df['outage_count'] = 0
        # 修改成[]
        result = []
        for index, row in time_df.iterrows():
            result.append({
                'time_slot': str(row['time_slot']),
                'outage_count': 0
            })
        return result

    # 2. 准备中断数据
    df['s_time'] = pd.to_datetime(df['s_time'])
    df['e_time'] = pd.to_datetime(df['e_time'])
    df = df.drop_duplicates(subset=[type, 's_time', 'e_time']).copy()

    # 3. 使用交叉合并来创建所有 (时间点, 中断) 的组合
    merged_df = time_df.merge(df, how='cross')

    # 4. 筛选出在每个 time_slot 处于活动状态的中断
    is_active = (merged_df['s_time'] <= merged_df['time_slot']) & \
                ((merged_df['e_time'] > merged_df['time_slot']) | pd.isna(merged_df['e_time']))
    
    active_outages = merged_df[is_active]

    # 5. 按时间点分组并计算唯一前缀的数量
    if not active_outages.empty:
        outage_counts = active_outages.groupby('time_slot')[type].nunique().reset_index()
        outage_counts.rename(columns={type: 'outage_count'}, inplace=True)
    else:
        outage_counts = pd.DataFrame({'time_slot': [], 'outage_count': []})
        outage_counts['time_slot'] = pd.to_datetime(outage_counts['time_slot'])

    # 6. 将计数与完整的时间序列合并，以包含中断数为0的时间点
    final_df = time_df.merge(outage_counts, on='time_slot', how='left').fillna(0)
    final_df['outage_count'] = final_df['outage_count'].astype(int)

    # 修改成[]
    result = []
    for index, row in final_df.iterrows():
        result.append({
            'time_slot': str(row['time_slot']),
            'outage_count': int(row['outage_count'])
        })
    return result







if __name__ == '__main__':
    import pandas as pd
    global country_info
    print("初始化字典")
    country_info = dict()
    df = pd.read_excel(COUNTRY_INFO_FILE, keep_default_na=False)
    for index, row in df.iterrows():
        chinese_name = row['chinese_short_name']
        country_info.setdefault(chinese_name, dict())
        country_info[chinese_name]['two_letter_code'] = row['two_letter_code']
        country_info[chinese_name]['longitude'] = row['longitude']
        country_info[chinese_name]['latitude'] = row['latitude']
    from config.database import DATABASE, USER, PASSWORD, HOST, PORT
    conn_11 = psycopg2.connect(database=DATABASE, user=USER, password=PASSWORD, host=HOST, port=PORT)
    date = str(datetime.datetime.now())[0:7].replace('-', '')
    # 上个月的年月 202406 
    date_lm = str(datetime.datetime.now().date() - relativedelta(months=1)).replace('-', '')[0:6]
    # event_table = 'event_table_' + date
    event_table = 'event_table_202407'
    event_table_lm = 'event_table_202408'
    event_rows = get_sort_event_count(conn=conn_11, last_month_table=event_table_lm, 
                            event_table=event_table, obj='org', country='global')
    event_items = deal_sort_event_count(conn=conn_11, last_month_table=event_table_lm, 
                            event_table=event_table, event_rows=event_rows, obj='org', country='global',
                                        country_info=country_info)
    print(event_items)
    # event_rows = get_china_city_outage(conn=conn_11, last_month_table='private_as_outage_202310', 
    #                         event_table='private_as_outage_202309')
    # print(deal_china_city_outage(event_rows))




### 
