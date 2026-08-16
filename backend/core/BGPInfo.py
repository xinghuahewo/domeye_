import time
import pandas as pd
import json
import traceback
import os
import sys


def load_local_env():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(current_dir, '.env'),
        os.path.join(os.path.dirname(current_dir), '.env'),
        os.path.join(os.path.dirname(os.path.dirname(current_dir)), '.env'),
    ]

    for env_path in candidates:
        if not os.path.exists(env_path):
            continue
        with open(env_path, 'r', encoding='utf-8') as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()
                if not key or key in os.environ:
                    continue
                os.environ[key] = value
        break


load_local_env()

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.config import (
    AS_INFO_FILE,
    COUNTRY_INFO_FILE,
    IMPORTANT_AS_FILE,
    IPV4_ALL_PREFIX_FILE,
    IPV6_ALL_PREFIX_FILE,
    PFX2AS_DICT_FILE,
    AS_REL_DICT_FILE,
    PREFIX_INFO_FILE,
    IMPORTANT_DOMAIN,
    PRIVATE_AS_FILE,
    TRIPLET_FILE
)

class BGPInfo:
    """bgpinfo类
       读取bgp异常检测所需的实体信息
    """

    def __init__(self):
        
        self.as_info_file = AS_INFO_FILE  # AS信息文件
        self.country_file = COUNTRY_INFO_FILE
        self.import_as_file = IMPORTANT_AS_FILE  # 重要AS文件
        self.ipv4_all_prefix_file = IPV4_ALL_PREFIX_FILE
        self.ipv6_all_prefix_file = IPV6_ALL_PREFIX_FILE
        self.pfx2as_dict_file = PFX2AS_DICT_FILE
        self.as_rel_dict_file = AS_REL_DICT_FILE
        self.prefix_info_file = PREFIX_INFO_FILE
        self.important_domain_file = IMPORTANT_DOMAIN
        self.private_as_file = PRIVATE_AS_FILE
        self.triplet_file = TRIPLET_FILE


        self.as_info = dict()
        self.country = dict()
        self.import_as_dict = dict()
        self.import_prefix_dict = dict()
        self.as_prefix_dict = dict()
        self.as_rel_dict = dict()
        self.prefix_info = dict()
        self.important_domain_dict = dict()
        self.private_as_dict = dict()
        self.triplet_info = dict()
        self.load_info()



    
    def load_info(self):
        """_summary_

        Raises:
            e: _description_
        """        
        print("开始加载实体信息")
        start = time.time()
        try:
            df = pd.read_csv(self.as_info_file, keep_default_na=False, usecols=['asn', 'as_name', 'as_country', \
                'as_country_cn', 'type', 'org_name', 'org_name_cn', 'descr', 'descr_cn', 'admin_info', 'import_as', 'export_as', \
                'is_ddos_provider', 'v4Peer', 'v6Peer', 'sibling_as'])
            df.drop_duplicates(subset=['asn'], keep='first', inplace=True)
            df_new = df.set_index('asn', drop=True, append=False, inplace=False, verify_integrity=False)
            df_new.index = df_new.index.astype(str)
            df_new['import_as'] = df_new['import_as'].apply(lambda x: eval(x) if x else [])
            df_new['export_as'] = df_new['export_as'].apply(lambda x: eval(x) if x else [])
            df_new['v4Peer'] = df_new['v4Peer'].apply(lambda x: eval(x) if x else [])
            df_new['v6Peer'] = df_new['v6Peer'].apply(lambda x: eval(x) if x else [])
            df_new['sibling_as'] = df_new['sibling_as'].apply(lambda x: eval(x) if x else [])
            self.as_info = df_new.to_dict(orient='index')

            df = pd.read_excel(self.country_file, keep_default_na=False)
            for index, row in df.iterrows():
                two_letter_code = row['two_letter_code']
                self.country.setdefault(two_letter_code, dict())
                self.country[two_letter_code]['english_full_name'] = row['english_full_name']
                self.country[two_letter_code]['english_short_name'] = row['english_short_name']
                self.country[two_letter_code]['chinese_short_name'] = row['chinese_short_name']
                self.country[two_letter_code]['three_letter_code'] = row['three_letter_code']
                self.country[two_letter_code]['digital_code'] = row['digital_code']
                self.country[two_letter_code]['phone_code'] = row['phone_code']
                self.country[two_letter_code]['jet_lag'] = row['jet_lag']
                self.country[two_letter_code]['latitude'] = row['latitude']
                self.country[two_letter_code]['longitude'] = row['longitude']


            df = pd.read_csv(self.import_as_file)
            df.drop_duplicates(subset=['aut-num'], keep='first', inplace=True)
            df_new = df.set_index('aut-num', drop=True, append=False, inplace=False, verify_integrity=False)
            self.important_as_dict = df_new.to_dict(orient='index')


            df_v4 = pd.read_excel(self.ipv4_all_prefix_file)
            df_v6 = pd.read_excel(self.ipv6_all_prefix_file)
            df = pd.concat([df_v4, df_v6], axis=0, ignore_index=True)
            df.drop_duplicates(subset=['prefix'], keep='first', inplace=True)
            df_new = df.set_index('prefix', drop=True, append=False, inplace=False, verify_integrity=False)
            self.important_prefix_dict = df_new.to_dict(orient='index')

            with open(self.pfx2as_dict_file, 'r') as f:
                self.as_prefix_dict = json.load(f)

            with open(self.as_rel_dict_file, 'r') as f:
                self.as_rel_dict = json.load(f)

            df = pd.read_csv(self.prefix_info_file, keep_default_na=False)
            df.drop_duplicates(subset=['prefix'], keep='first', inplace=True)
            df_new = df.set_index('prefix', drop=True, append=False, inplace=False, verify_integrity=False)
            self.prefix_info = df_new.to_dict(orient='index')



            with open(IMPORTANT_DOMAIN, 'r') as f:
                self.important_domain_dict = json.load(f)


            with open(PRIVATE_AS_FILE, 'r') as f:
                self.private_as_dict = json.load(f)


            df = pd.read_csv(self.triplet_file, keep_default_na=False, low_memory=False)
            def init_dict(row):
                first_as, second_as, third_as = str(row['first_as']), str(row['second_as']), str(row['third_as'])
                self.triplet_info.setdefault(first_as, dict()).setdefault(second_as, dict()).setdefault(third_as, dict())
                self.triplet_info[first_as][second_as][third_as]['stability'] = row['stability']
            df.apply(init_dict, axis=1)
            
            end = time.time()
            print(f"实体信息加载完成, 耗时: {end - start:.2f}秒")
        except Exception as e:
            print(f"加载实体信息失败: {e}")
            print(traceback.format_exc())
            raise e
        



    def get_status(self):
        print("BGPInfo数量信息统计:")
        print(f"AS信息: {len(self.as_info)}")   ### 133117
        print(f"国家信息: {len(self.country)}")   ### 251
        print(f"重要AS信息: {len(self.important_as_dict)}")         ### 1420
        print(f"重要前缀信息: {len(self.important_prefix_dict)}")     ### 89788
        print(f"AS前缀信息: {len(self.as_prefix_dict)}")           ### 74417
        print(f"AS关系信息: {len(self.as_rel_dict)}")         ## 77787
        print(f"前缀信息: {len(self.prefix_info)}")      # 1357067
        print(f"重要域名信息: {len(self.important_domain_dict)}") # 434
        print(f"私有AS信息: {len(self.private_as_dict)}") # 1000000


if __name__ == "__main__":
    bgp_info = BGPInfo(
        AS_INFO_FILE,
        COUNTRY_INFO_FILE,
        IMPORTANT_AS_FILE,
        IPV4_ALL_PREFIX_FILE,
        IPV6_ALL_PREFIX_FILE,
        PFX2AS_DICT_FILE,
        AS_REL_DICT_FILE,
        PREFIX_INFO_FILE,
        IMPORTANT_DOMAIN,
        PRIVATE_AS_FILE
    )
    print(len(bgp_info.as_info))   ### 133117
    print(len(bgp_info.country))   ### 251
    print(len(bgp_info.important_as_dict))         ### 1420
    print(len(bgp_info.important_prefix_dict))     ### 89788
    print(len(bgp_info.as_prefix_dict))           ### 74417
    print(len(bgp_info.as_rel_dict))         ## 77787
    print(len(bgp_info.prefix_info))      # 1357067
    print(len(bgp_info.important_domain_dict)) # 434
