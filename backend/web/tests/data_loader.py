import pandas as pd
import sys
import os

# 动态地将 'Domeye/backend' 添加到 sys.path
# 这样我们就可以使用绝对路径从 config 和其他 utils 导入
# from config.config import ...
# from utils.other_util import ...
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, '..', '..'))
sys.path.append(backend_dir)

from config.config import (
    IMPORTANT_AS_FILE,
    AS_INFO_FILE,
    PREFIX_INFO_FILE,
    DOMAIN_INFO_FILE,
    DOMAIN_CN_INFO_FILE,
    COUNTRY_INFO_FILE
)

# --- 缓存字典 ---
_as_info = None
_prefix_info = None
_domain_info = None
_country_info = None
_important_as_dict = None

# --- 按需加载数据的函数 ---

def get_important_as_dict():
    """ 按需加载重要AS信息 """
    global _important_as_dict
    if _important_as_dict is None:
        print("首次加载: 重要AS信息...")
        df_important = pd.read_csv(IMPORTANT_AS_FILE)
        df_important.drop_duplicates(subset=['aut-num'], keep='first', inplace=True)
        df_important_new = df_important.set_index('aut-num', drop=True)
        _important_as_dict = df_important_new.to_dict(orient='index')
    return _important_as_dict

def get_as_info():
    """ 按需加载AS信息 """
    global _as_info
    if _as_info is None:
        print("首次加载: AS信息...")
        df_as = pd.read_csv(AS_INFO_FILE, keep_default_na=False, usecols=['asn', 'as_name', 'as_country_cn', 'org_name', 'org_name_cn', 'as_info', 'admin_info', 'tech_info', 'abuse_info', 'type', 'country_rank', 'global_rank'])
        df_as.drop_duplicates(subset=['asn'], keep='first', inplace=True)
        df_as_new = df_as.set_index('asn', drop=True)
        df_as_new.index = df_as_new.index.astype(str)
        _as_info = df_as_new.to_dict(orient='index')
    return _as_info

def get_prefix_info():
    """ 按需加载前缀信息 """
    global _prefix_info
    if _prefix_info is None:
        print("首次加载: 前缀信息...")
        df_prefix = pd.read_csv(PREFIX_INFO_FILE, keep_default_na=False, usecols=['prefix', 'route', 'bgp', 'name', 'domain_num', 'domain_auth_num', 'domain', 'domain_auth'])
        df_prefix.drop_duplicates(subset=['prefix'], keep='first', inplace=True)
        df_prefix_new = df_prefix.set_index('prefix', drop=True)
        _prefix_info = df_prefix_new.to_dict(orient='index')
    return _prefix_info

def get_domain_info():
    """ 按需加载域名信息 """
    global _domain_info
    if _domain_info is None:
        print("首次加载: 域名信息...")
        df_domain = pd.read_csv(DOMAIN_INFO_FILE, keep_default_na=False, sep=';', usecols=['url', 'title', 'industry', 'ip', 'ip_prefix', 'auth_ip'])
        df_cn = pd.read_csv(DOMAIN_CN_INFO_FILE, keep_default_na=False, usecols=['url', 'title', 'industry', 'ip', 'ip_prefix', 'auth_ip'])
        df_domain_full = pd.concat([df_domain, df_cn], axis=0, ignore_index=True)
        df_domain_full.drop_duplicates(subset=['url'], keep='first', inplace=True)
        df_domain_new = df_domain_full.set_index('url', drop=True)
        _domain_info = df_domain_new.to_dict(orient='index')
    return _domain_info

def get_country_info():
    """ 按需加载国家地理信息 """
    global _country_info
    if _country_info is None:
        print("首次加载: 国家地理信息...")
        df_country = pd.read_excel(COUNTRY_INFO_FILE, keep_default_na=False)
        country_dict = {}
        for index, row in df_country.iterrows():
            chinese_name = row['chinese_short_name']
            country_dict.setdefault(chinese_name, {})
            country_dict[chinese_name]['two_letter_code'] = row['two_letter_code']
            country_dict[chinese_name]['longitude'] = row['longitude']
            country_dict[chinese_name]['latitude'] = row['latitude']
        _country_info = country_dict
    return _country_info

def clear_all_data():
    """ 清除所有已加载的数据，用于测试 """
    global _as_info, _prefix_info, _domain_info, _country_info, _important_as_dict
    _as_info = None
    _prefix_info = None
    _domain_info = None
    _country_info = None
    _important_as_dict = None
    print("所有缓存的数据已被清除。")

if __name__ == '__main__':
    # --- 这是一个简单的测试，展示如何使用这些函数 ---
    print("--- 开始测试按需加载数据 ---")

    # 1. 首次获取 AS 信息
    print("\n[Test 1] 第一次获取AS信息...")
    as_data = get_as_info()
    print(f"AS信息加载完成，获取到 {len(as_data)} 条记录。")

    # 2. 再次获取 AS 信息 (这次应该从缓存中读取)
    print("\n[Test 2] 第二次获取AS信息...")
    as_data_cached = get_as_info()
    print("第二次获取完成，没有看到 '首次加载' 提示，说明缓存生效。")

    # 3. 获取国家信息
    print("\n[Test 3] 获取国家地理信息...")
    country_data = get_country_info()
    print(f"国家信息加载完成，获取到 {len(country_data)} 条记录。")

    # 4. 清除缓存并重新加载
    print("\n[Test 4] 清除所有缓存...")
    clear_all_data()
    print("\n[Test 5] 清除后再次获取AS信息...")
    as_data_reloaded = get_as_info()
    print(f"AS信息被重新加载，获取到 {len(as_data_reloaded)} 条记录。")

    print("\n--- 测试结束 ---") 