'''
Author: gbr
Date: 2025-07-11 12:14:53
LastEditors: gbr
LastEditTime: 2026-03-18 11:58:00
FilePath: /bgpdata/Domeye/backend/utils/data_loader.py
Description:

'''
import os
import sys
import traceback

import pandas as pd

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config.config import (
    IMPORTANT_AS_FILE,
    AS_INFO_FILE,
    PREFIX_INFO_FILE,
    DOMAIN_INFO_FILE,
    DOMAIN_CN_INFO_FILE,
    COUNTRY_INFO_FILE,
)


as_info = {}
prefix_info = {}
domain_info = {}
country_info = {}
important_as_dict = {}
country_list = []
ases_1000 = []

_core_data_loaded = False
_domain_data_loaded = False


def _load_core_data():
    global country_list, ases_1000, _core_data_loaded
    if _core_data_loaded:
        return

    df_important = pd.read_csv(IMPORTANT_AS_FILE)
    df_important.drop_duplicates(subset=['aut-num'], keep='first', inplace=True)
    df_important_new = df_important.set_index('aut-num', drop=True)
    important_as_dict.clear()
    important_as_dict.update(df_important_new.to_dict(orient='index'))
    print(f"重要AS信息加载完成: {len(important_as_dict)} 条")

    df_as = pd.read_csv(
        AS_INFO_FILE,
        keep_default_na=False,
        usecols=['asn', 'as_name', 'as_country_cn', 'org_name', 'org_name_cn', 'as_info',
                 'admin_info', 'tech_info', 'abuse_info', 'type', 'country_rank', 'global_rank'],
        engine='python',
        on_bad_lines='skip',
    )
    df_as.drop_duplicates(subset=['asn'], keep='first', inplace=True)

    ases_1000 = df_as[['asn', 'as_country_cn']].head(1000).copy()
    ases_1000['asn'] = ases_1000['asn'].astype(str)

    df_country = df_as['as_country_cn'].drop_duplicates(keep='first').dropna()
    country_list = df_country.tolist()
    if '未知' in country_list:
        country_list.remove('未知')
    print(f"国家列表加载完成: {len(country_list)} 条")

    df_as_new = df_as.set_index('asn', drop=True)
    df_as_new.index = df_as_new.index.astype(str)
    as_info.clear()
    as_info.update(df_as_new.to_dict(orient='index'))
    print(f"AS信息加载完成: {len(as_info)} 条")

    df_prefix = pd.read_csv(
        PREFIX_INFO_FILE,
        keep_default_na=False,
        usecols=['prefix', 'route', 'bgp', 'name', 'domain_num', 'domain_auth_num', 'domain', 'domain_auth'],
    )
    df_prefix.drop_duplicates(subset=['prefix'], keep='first', inplace=True)
    df_prefix_new = df_prefix.set_index('prefix', drop=True)
    prefix_info.clear()
    prefix_info.update(df_prefix_new.to_dict(orient='index'))
    print(f"前缀信息加载完成: {len(prefix_info)} 条")

    df_country = pd.read_excel(COUNTRY_INFO_FILE, keep_default_na=False)
    country_dict = {}
    for _, row in df_country.iterrows():
        chinese_name = row['chinese_short_name']
        country_dict.setdefault(chinese_name, {})
        country_dict[chinese_name]['two_letter_code'] = row['two_letter_code']
        country_dict[chinese_name]['longitude'] = row['longitude']
        country_dict[chinese_name]['latitude'] = row['latitude']
    country_info.clear()
    country_info.update(country_dict)
    print(f"国家地理信息加载完成: {len(country_info)} 条")

    _core_data_loaded = True


def load_domain_data():
    global _domain_data_loaded
    if _domain_data_loaded:
        return

    df_domain = pd.read_csv(
        DOMAIN_INFO_FILE,
        keep_default_na=False,
        sep=';',
        usecols=['url', 'title', 'industry', 'ip', 'ip_prefix', 'auth_ip'],
    )
    df_cn = pd.read_csv(
        DOMAIN_CN_INFO_FILE,
        keep_default_na=False,
        usecols=['url', 'title', 'industry', 'ip', 'ip_prefix', 'auth_ip'],
    )
    df_domain_full = pd.concat([df_domain, df_cn], axis=0, ignore_index=True)
    df_domain_full.drop_duplicates(subset=['url'], keep='first', inplace=True)
    df_domain_new = df_domain_full.set_index('url', drop=True)
    domain_info.clear()
    domain_info.update(df_domain_new.to_dict(orient='index'))
    print(f"域名信息加载完成: {len(domain_info)} 条")
    _domain_data_loaded = True


def ensure_domain_data_loaded():
    if not _domain_data_loaded:
        load_domain_data()


def ensure_core_data_loaded():
    """按需加载核心 AS、前缀与国家数据。"""
    if not _core_data_loaded:
        _load_core_data()


def init_global_data():
    """
    加载所有需要全局访问的数据字典
    """
    print("开始初始化全局数据字典...")

    try:
        _load_core_data()

        if os.environ.get('LOAD_DOMAIN_INFO_ON_STARTUP', '').strip().lower() in ('1', 'true', 'yes', 'on'):
            load_domain_data()
        else:
            print("域名信息延迟加载: 跳过启动期读取 website_entity.csv")

        print("全局数据字典初始化完成。")
    except FileNotFoundError as e:
        print(f"错误: 数据文件未找到 - {e}")
    except Exception as e:
        print(f"初始化全局数据时发生未知错误: {e}")
        print(traceback.format_exc())
