import time
import sys
import os
import ipaddress
import pyinotify
import traceback
import datetime


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

from core.BGPInfo import BGPInfo
from core.BGPRib import BGPRib, normalize_bgp_prefix, get_feature_prefix_skip_reason

from config.config import BIG_COUNTRY, SOURCE, FEATURE_COUNTRY_TABLE, FEATURE_OTHER_TABLE
from config.config import FEATURE_ASN_MONTHLY_ENABLED, FEATURE_ASN_OLD_SUFFIX
from config.logger import feature_logger

from utils.get_as_info import get_as_country_cn, get_as_country
from utils.prefix_quantity import calculate_c_segments_count, calculate_v6_48_segments_count
from utils.utilitys import (
    get_data_path,
    get_rib_file,
    get_rib_path,
    get_updates_file_list,
    dump_file,
    get_moas_table_name,
    get_hijack_table_name,
    get_event_table_name,
    get_sub_hijack_table_name,
    remove_file,
    file_to_time,
    get_leak_phenomenon_table_name,
    get_leak_event_table_name,
    get_outage_table_name,
    get_current_file_name_from_rib,
    get_current_file_name_from_updates,
    get_update_file_abspath,
)

from database.utils import if_table_exist, get_conn
from database.feature_asn import (
    insert_feature_list,
    create_feature_asn_table,
)
from database.feature_country import insert_feature_country, create_feature_country_table

from config.logger import feature_logger
from config.database import DATABASE, USER, HOST, PORT, PASSWORD
from config.config import (MODE, SOURCE, BASE_DATA_PATH, RIB_HISTORY_FILE, 
                           FEATURE_DUMP_DIR, BIG_COUNTRY, 
                           FEATURE_COUNTRY_TABLE, FEATURE_OTHER_TABLE)

# 断点续传：进度文件路径
PROGRESS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '../logs/feature_progress.txt')
FULL_IPV4_C_SEGMENTS = 1 << 24
FULL_IPV4_ADDRESS_COUNT = 1 << 32
SUSPICIOUS_V4_IP_THRESHOLD = 1_000_000_000
ANOMALY_PREFIX_SAMPLE_LIMIT = 10


class BGPFeature:
    '''
    BGPFeature类是 BGP 特征的核心类，负责处理 BGP 特征数据。

    Args:
        conn: 数据库连接
        bgp_info: 实体信息类对象
        bgp_rib: BGPRib类对象

        
    struct:
        feature_dict: {
            'country': {   # 国家  因为feature表是根据国家进行分表的  包含未知国家的 ZZ
                'asn': {
                    v4Prefix_num:,  # C段数量
                    v6Prefix_num:,  # /48 数量
                    v4IP_num:,
                    announ_num:,
                    withdraw_num:,
                    is_change:   # 用来判断某个as是否发生了变化 在一个update中
                }
            }



                
    '''
    # def __init__(self, conn, bgp_info, bgp_rib):
    def __init__(self, rib_file, conn):   
        self.conn = conn
        # self.bgp_info = bgp_info
        # self.bgp_rib = bgp_rib

        self.rib_file = rib_file

        self.bgp_info = BGPInfo()
        
        self.bgp_rib = BGPRib(self.bgp_info, self.rib_file, country_filter='IR')


        self.feature_dict = {}   # 保存当前时间点的特征
      


        self.feature_collect_dict = {   # 只记录每一时刻的宣告和回撤数量
            'v4Prefix_num': 0,
            'v6Prefix_num': 0,
            'v4IP_num': 0,
            'announ_num': 0,
            'withdraw_num': 0
        }

        # 记录已确保创建的 ASN 特征表（避免每个 update 文件都去查/建表）
        self._ensured_feature_asn_tables = set()

    def _feature_asn_month_table(self, base_table: str, t: datetime.datetime) -> str:
        if not FEATURE_ASN_MONTHLY_ENABLED:
            return base_table
        return f"{base_table}_{t.strftime('%Y%m')}"

    def _ensure_feature_asn_table(self, table_name: str) -> None:
        if table_name in self._ensured_feature_asn_tables:
            return
        if not if_table_exist(self.conn, table_name):
            create_feature_asn_table(self.conn, table_name)
        self._ensured_feature_asn_tables.add(table_name)

    def _log_feature_anomaly(
        self,
        *,
        scope: str,
        identifier: str,
        file_time,
        v4_prefix_num: int,
        v4_ip_num: int,
        v6_prefix_num: int,
        announ_num: int,
        withdraw_num: int,
        prefixes,
    ) -> None:
        reasons = []
        if v4_prefix_num >= FULL_IPV4_C_SEGMENTS:
            reasons.append('v4prefix_num_reached_full_ipv4_c_segments')
        if v4_ip_num >= FULL_IPV4_ADDRESS_COUNT:
            reasons.append('v4ip_num_reached_full_ipv4_space')
        elif v4_ip_num >= SUSPICIOUS_V4_IP_THRESHOLD:
            reasons.append('v4ip_num_exceeds_suspicious_threshold')

        if not reasons:
            return

        sample_prefixes = sorted(prefixes)[:ANOMALY_PREFIX_SAMPLE_LIMIT] if prefixes else []
        feature_logger.warning(
            'feature anomaly detected: scope=%s identifier=%s file_time=%s '
            'v4prefix_num=%s v4ip_num=%s v6prefix_num=%s announ_num=%s withdraw_num=%s '
            'prefix_count=%s sample_prefixes=%s reasons=%s',
            scope,
            identifier,
            file_time,
            v4_prefix_num,
            v4_ip_num,
            v6_prefix_num,
            announ_num,
            withdraw_num,
            len(prefixes) if prefixes else 0,
            sample_prefixes,
            reasons,
        )
         
    def _log_skipped_prefix(
        self,
        *,
        scope: str,
        reason: str,
        prefix: str,
        normalized_prefix: str = '',
        updates_file: str = '',
        file_time='',
        vp: str = '',
        as_path: str = '',
        identifier: str = '',
    ) -> None:
        feature_logger.warning(
            'skip prefix during feature processing: scope=%s reason=%s prefix=%s normalized_prefix=%s '
            'updates_file=%s file_time=%s vp=%s as_path=%s identifier=%s',
            scope,
            reason,
            prefix,
            normalized_prefix,
            updates_file,
            file_time,
            vp,
            as_path,
            identifier,
        )
         
    def __time_cost(func):
        def wrapper(*args, **kwargs):
            start_time = time.time()
            result = func(*args, **kwargs)
            end_time = time.time()
            print(f"{func.__name__} time cost: {end_time - start_time} seconds")
            return result
        return wrapper



    @__time_cost
    def init_feature(self):
        """初始化特征

        Args:
            prefix_dict (dict): preifx-vp-as_path dict 
        """
        for asn, prefixes in self.bgp_rib.as_prefix.items():

            country = get_as_country_cn(self.bgp_info.as_info, asn)
            if country == '' or country is None or country == '未知':
                country = '未知'
            if country not in self.feature_dict:
                self.feature_dict[country] = {}

            if asn not in self.feature_dict[country]:
                self.feature_dict[country][asn] = {
                    'v4Prefix_num': 0,
                    'v6Prefix_num': 0,
                    'v4IP_num': 0,
                    'announ_num': 0,
                    'withdraw_num': 0,
                    'is_change': False,
                }

        feature_logger.info(f"init feature success!")

    def get_origin_public_as(self, as_path):
        """获取as_path的origin as

        Args:
            as_path (str): as_path字符串
        Returns:
            str: origin as
        """
        if as_path == '' or as_path is None:
            return None
        as_path_fields = as_path.split(' ')
        origin_as = as_path.split(' ')[-1]
        if '{' in origin_as:
            return None
        for index in range(len(as_path_fields)-1, -1, -1):
            asn = as_path_fields[index]
            if (int(asn) >= 64512 and int(asn) <= 65535) or int(asn) > 4294967295:
                continue
            else:
                return asn
        return None


    def feature_detect(self, t, flag, as_path):
        """特征提取

        Args:
            t (datetime): 当前update文件的时间
            flag (str): 宣告或回撤
            prefix (str): 前缀
            old_origin_set (set): 旧的origin集合
            origin_set (set): 当前前缀的origin集合
            new_vp_paths (dict): 当前前缀的vp-as_path
        """        
        self.t = t  # 当前update文件的时间

        if flag == 'W':
            self.feature_collect_dict['withdraw_num'] += 1

            # 回撤的origin的withdraw_num + 1
            origin_asn = self.get_origin_public_as(as_path)
            if origin_asn:
                country = get_as_country_cn(self.bgp_info.as_info, origin_asn)
                if country == '' or country is None or country == '未知':
                    country = '未知'
                if country not in self.feature_dict:
                    self.feature_dict[country] = {}
                if origin_asn not in self.feature_dict[country]:
                    self.feature_dict[country][origin_asn] = {
                        'v4Prefix_num': 0,
                        'v6Prefix_num': 0,
                        'v4IP_num': 0,
                        'announ_num': 0,
                        'withdraw_num': 0,
                        'is_change': False,
                    }
                self.feature_dict[country][origin_asn]['withdraw_num'] += 1
                self.feature_dict[country][origin_asn]['is_change'] = True

                # version = ipaddress.ip_network(prefix, strict=False).version
                # if self.feature_dict[country][origin_asn]['v4IP_num'] == 0 and version == 4:
                #     feature_logger.error(f"prefix {prefix} is withdraw, {origin_asn} but v4IP_num is 0")
                
        elif flag == 'A':
            self.feature_collect_dict['announ_num'] += 1

            public_as = self.get_origin_public_as(as_path)
            if public_as:
                country = get_as_country_cn(self.bgp_info.as_info, public_as)
                # if country != '伊拉克':
                #     return 
                if country == '' or country is None or country == '未知':
                    country = '未知'
                if country not in self.feature_dict:
                    self.feature_dict[country] = {}
                if public_as not in self.feature_dict[country]:
                    self.feature_dict[country][public_as] = {
                        'v4Prefix_num': 0,
                        'v6Prefix_num': 0,
                        'v4IP_num': 0,
                        'announ_num': 0,
                        'withdraw_num': 0,
                        'is_change': False,
                    }
                self.feature_dict[country][public_as]['announ_num'] += 1
                self.feature_dict[country][public_as]['is_change'] = True



    @__time_cost
    def insert_to_db(self, file_time):
        """在一个update文件读取完成之后，将特征插入到数据库中
        Args:
            file_time (datetime): 当前update文件的时间
        """
        # 计算总量 feature_collect_dict
        v4Prefix_collect = set()
        v6Prefix_collect = set()
        logged_skipped_prefixes = set()

        for country, asn_dict in self.feature_dict.items():

            start1 = time.time()
            # 记录当前国家的特征信息
            v4Prefix_country = set()
            v6Prefix_country = set()
            announ_num_country = 0
            withdraw_num_country = 0

            insert_list = []
            for asn, feature in asn_dict.items():
                
                v4Prefix_set = set()
                v6Prefix_set = set()
                # 生成对应as、country、collect的前缀集合
                for prefix in self.bgp_rib.as_prefix.get(asn, set()):
                    normalized_prefix, version, prefixlen = normalize_bgp_prefix(prefix)
                    if normalized_prefix is None:
                        warning_key = ('invalid_prefix', prefix, f'{country}:{asn}')
                        if warning_key not in logged_skipped_prefixes:
                            self._log_skipped_prefix(
                                scope='aggregate',
                                reason='invalid_prefix',
                                prefix=prefix,
                                file_time=file_time,
                                identifier=f'{country}:{asn}',
                            )
                            logged_skipped_prefixes.add(warning_key)
                        continue

                    skip_reason = get_feature_prefix_skip_reason(version, prefixlen)
                    if skip_reason is not None:
                        warning_key = (skip_reason, normalized_prefix, f'{country}:{asn}')
                        if warning_key not in logged_skipped_prefixes:
                            self._log_skipped_prefix(
                                scope='aggregate',
                                reason=skip_reason,
                                prefix=prefix,
                                normalized_prefix=normalized_prefix,
                                file_time=file_time,
                                identifier=f'{country}:{asn}',
                            )
                            logged_skipped_prefixes.add(warning_key)
                        continue

                    if version == 4:
                        v4Prefix_set.add(normalized_prefix)
                        v4Prefix_country.add(normalized_prefix)
                        v4Prefix_collect.add(normalized_prefix)
                    elif version == 6:
                        v6Prefix_set.add(normalized_prefix)
                        v6Prefix_country.add(normalized_prefix)
                        v6Prefix_collect.add(normalized_prefix)

                if feature['is_change']:
                    asn_v6Prefix_num = calculate_v6_48_segments_count(v6Prefix_set)
                    # asn_v6Prefix_num = len(v6Prefix_set)
                    asn_v4Prefix_num = calculate_c_segments_count(v4Prefix_set) # 计算C段数量
                    asn_v4IP_num = asn_v4Prefix_num * 256
                    self._log_feature_anomaly(
                        scope='asn',
                        identifier=f'{country}:{asn}',
                        file_time=file_time,
                        v4_prefix_num=asn_v4Prefix_num,
                        v4_ip_num=asn_v4IP_num,
                        v6_prefix_num=asn_v6Prefix_num,
                        announ_num=feature['announ_num'],
                        withdraw_num=feature['withdraw_num'],
                        prefixes=v4Prefix_set,
                    )

                    self.feature_dict[country][asn]['v4Prefix_num'] = asn_v4Prefix_num
                    self.feature_dict[country][asn]['v6Prefix_num'] = asn_v6Prefix_num
                    self.feature_dict[country][asn]['v4IP_num'] = asn_v4IP_num


                    insert_list.append({
                        't': file_time,
                        'source': SOURCE,
                        'asn': asn,
                        'country': country if country != '未知' else ' ',
                        'v4Prefix_num': asn_v4Prefix_num,
                        'v6Prefix_num': asn_v6Prefix_num,
                        'v4IP_num': asn_v4IP_num,
                        'announ_num': feature['announ_num'],
                        'withdraw_num': feature['withdraw_num'],
                    })


                    announ_num_country += feature['announ_num']
                    withdraw_num_country += feature['withdraw_num']

                    # 重置特征
                    self.feature_dict[country][asn]['is_change'] = False
                    self.feature_dict[country][asn]['announ_num'] = 0
                    self.feature_dict[country][asn]['withdraw_num'] = 0

            end1 = time.time()
            feature_logger.info(f"calculate {country} feature time cost: {end1 - start1} seconds")



            # # 根据国家分类进行批量插入
            # if len(insert_list) > 0:
            #     country_en = BIG_COUNTRY.get(country, False)
            #     if country_en:
            #         feature_table_base = f'feature_{country_en}'
            #     else:
            #         feature_table_base = FEATURE_OTHER_TABLE
            #     feature_table = self._feature_asn_month_table(feature_table_base, file_time)
            #     self._ensure_feature_asn_table(feature_table)
            #     # 插入到数据库
            #     insert_feature_list(self.conn, insert_list, feature_table)
            #     if feature_table_base != FEATURE_OTHER_TABLE:
            #         feature_logger.info(f"insert {len(insert_list)} rows to {feature_table} in {file_time}")

            insert_list.clear()

            end2 = time.time()
            feature_logger.info(f"insert {country} feature time cost: {end2 - end1} seconds")

            # 插入国家数据
            v4Prefix_num_country = calculate_c_segments_count(v4Prefix_country)
            v6Prefix_num_country = calculate_v6_48_segments_count(v6Prefix_country)
            # v6Prefix_num_country = len(v6Prefix_country)
            v4IP_num_country = v4Prefix_num_country * 256
            self._log_feature_anomaly(
                scope='country',
                identifier=country,
                file_time=file_time,
                v4_prefix_num=v4Prefix_num_country,
                v4_ip_num=v4IP_num_country,
                v6_prefix_num=v6Prefix_num_country,
                announ_num=announ_num_country,
                withdraw_num=withdraw_num_country,
                prefixes=v4Prefix_country,
            )
            
            # 插入到国家特征表                
            insert_feature_country(self.conn, file_time, SOURCE, country, v4Prefix_num_country, v6Prefix_num_country,
                                   v4IP_num_country, announ_num_country, withdraw_num_country, FEATURE_COUNTRY_TABLE)

            end3 = time.time()
            feature_logger.info(f"insert {country} feature_country time cost: {end3 - end2} seconds")
        
        # 插入collect总量
        start = time.time()
        v4Prefix_num = calculate_c_segments_count(v4Prefix_collect)
        v6Prefix_num = calculate_v6_48_segments_count(v6Prefix_collect)
        # v6Prefix_num = len(v6Prefix_collect)
        v4IP_num = v4Prefix_num * 256
        self._log_feature_anomaly(
            scope='collect',
            identifier='collect',
            file_time=file_time,
            v4_prefix_num=v4Prefix_num,
            v4_ip_num=v4IP_num,
            v6_prefix_num=v6Prefix_num,
            announ_num=self.feature_collect_dict['announ_num'],
            withdraw_num=self.feature_collect_dict['withdraw_num'],
            prefixes=v4Prefix_collect,
        )
        insert_feature_country(self.conn, file_time, SOURCE, "collect", v4Prefix_num, v6Prefix_num, v4IP_num,
                               self.feature_collect_dict['announ_num'], self.feature_collect_dict['withdraw_num'], FEATURE_COUNTRY_TABLE)
        
        feature_logger.info(f"insert {file_time} feature_collect to feature_collect, \
                            announ_num: {self.feature_collect_dict['announ_num']}, withdraw_num: {self.feature_collect_dict['withdraw_num']}")

        end = time.time()
        feature_logger.info(f"insert collect feature_country time cost: {end - start} seconds")
        # 重置collect特征
        self.feature_collect_dict['v4Prefix_num'] = v4Prefix_num
        self.feature_collect_dict['v6Prefix_num'] = v6Prefix_num
        self.feature_collect_dict['v4IP_num'] = v4IP_num
        self.feature_collect_dict['announ_num'] = 0
        self.feature_collect_dict['withdraw_num'] = 0

    @__time_cost
    def update_checking(self, updates_file):
        """处理update文件

        Args:
            updates_file (str): update文件路径
        """
        t = ""
        # 注意一个问题 在中心 文件名字是北京时间 但是在ripe 文件名字是utc时间
        file_time = file_to_time(updates_file) 
        ## XXX: 中心的文件名字不加8h
        file_time = file_time + datetime.timedelta(hours=8)
        try:

            with open(updates_file, 'r') as f:
                for update_message in f:
                    fields = update_message.strip().split('|')
                    
                    # 测试
                    try:
                        timestamp, flag, vp, prefix = int(fields[1]), fields[2], fields[4], fields[5]
                        if flag == 'STATE':
                            continue

                        normalized_prefix, version, prefixlen = normalize_bgp_prefix(prefix)
                        if normalized_prefix is None:
                            self._log_skipped_prefix(
                                scope='update',
                                reason='invalid_prefix',
                                prefix=prefix,
                                updates_file=updates_file,
                                vp=vp,
                                as_path=fields[6] if flag == 'A' and len(fields) > 6 else '',
                            )
                            continue

                        skip_reason = get_feature_prefix_skip_reason(version, prefixlen)
                        if skip_reason is not None:
                            self._log_skipped_prefix(
                                scope='update',
                                reason=skip_reason,
                                prefix=prefix,
                                normalized_prefix=normalized_prefix,
                                updates_file=updates_file,
                                vp=vp,
                                as_path=fields[6] if flag == 'A' and len(fields) > 6 else '',
                            )
                            continue

                        prefix = normalized_prefix
                        if flag == 'A':  # 宣告路由加油as_path
                            as_path = fields[6]
                            asn = self.get_origin_public_as(as_path)
                            # 不处理非伊朗的AS
                            if asn is None or asn == '' or get_as_country(self.bgp_info.as_info, asn) != 'IR':
                                continue
                        else:
                            as_path = self.bgp_rib.prefix_dict.get(prefix, {}).get(vp, '')
                    
                        ### T时区问题  转成utc时间 + 8h
                        t = datetime.datetime.fromtimestamp(int(timestamp) + 28800, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

                        old_origin_set = self.bgp_rib.prefix_as.get(prefix, set()).copy()
                        self.bgp_rib.update_rib(flag, prefix, vp, as_path, old_origin_set)
                        self.feature_detect(t, flag, as_path)
                    except Exception as e:
                        feature_logger.error(f"update_message: {update_message}")
                        feature_logger.error(f"Error: {traceback.format_exc()}")    
                        continue
            
            # 每次读取一个update文件后，将更新的特征表加入到数据库中
            self.insert_to_db(file_time)
        except Exception as e:
            feature_logger.error(f"Error in processing update file {updates_file} at time {t}: {e}")
            feature_logger.error(f"Error: {traceback.format_exc()}")


def create_tables(conn):
    """创建固定名称的表"""
    if not if_table_exist(conn, FEATURE_COUNTRY_TABLE):
        create_feature_country_table(conn, FEATURE_COUNTRY_TABLE)
    # ASN 特征表改为按月建表：{base}_{YYYYMM}
    # 旧表（{base}{FEATURE_ASN_OLD_SUFFIX}）的重命名请使用脚本完成，避免启动时锁表。
    # if FEATURE_ASN_MONTHLY_ENABLED:
    #     now = datetime.datetime.utcnow()
    #     month_suffix = now.strftime('%Y%m')
    #     # feature_other_YYYYMM
    #     table_name = f"{FEATURE_OTHER_TABLE}_{month_suffix}"
    #     if not if_table_exist(conn, table_name):
    #         create_feature_asn_table(conn, table_name)
    #     # feature_{US/CN/...}_YYYYMM
    #     for country in BIG_COUNTRY:
    #         country_en = BIG_COUNTRY.get(country)
    #         base = f'feature_{country_en}'
    #         table_name = f"{base}_{month_suffix}"
    #         if not if_table_exist(conn, table_name):
    #             create_feature_asn_table(conn, table_name)
    # else:
    #     if not if_table_exist(conn, FEATURE_OTHER_TABLE):
    #         create_feature_asn_table(conn, FEATURE_OTHER_TABLE)
    #     for country in BIG_COUNTRY:
    #         country_en = BIG_COUNTRY.get(country)
    #         country_table = f'feature_{country_en}'
    #         if not if_table_exist(conn, country_table):
    #             create_feature_asn_table(conn, country_table)

def save_progress(filename):
    """保存处理进度"""
    try:
        with open(PROGRESS_FILE, 'w') as f:
            f.write(filename)
    except Exception as e:
        feature_logger.error(f"保存进度失败: {e}")

def load_progress():
    """加载上次处理的进度"""
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r') as f:
                last_file = f.read().strip()
                if last_file:
                    feature_logger.info(f"检测到上次处理进度: {last_file}")
                    return last_file
        except Exception as e:
            feature_logger.error(f"读取进度文件失败: {e}")
    return None

# 以下代码请勿修改 ####
Updates_File_Queue = list()
Last_File_Name = ""     # 上一次读取的文件名
Current_Updates_File = ""  # 当前需要读取的文件名
Data_Path = ""
Data_Path_Last_month = ""


class FileEventHandler(pyinotify.ProcessEvent):
    def process_IN_CLOSE_WRITE(self, event):
        """可写文件CLOSE时触发此函数,进行更新报文的处理.

        Args:
        ----
            event (pyinotify.ProcessEvent): 可写文件CLOSE事件。

        """
        global Last_File_Name
        full_file_path = str(event.pathname)  # 监测到的文件的完整路径
        file_name = full_file_path.split(os.sep)[-1]  # 监测到的文件的文件名
        if file_name.startswith("update"):
            # 新的update文件
            if file_name not in Updates_File_Queue and file_name > Last_File_Name and file_name >= Current_Updates_File: 
                Last_File_Name = file_name
                Updates_File_Queue.append(full_file_path)
                Updates_File_Queue.sort()
                feature_logger.info(f"检测到新文件 {full_file_path}, 添加到待处理队列。")

def main():
    """detection Ripe data ."""
    global Data_Path, Data_Path_Last_month

    ### XXX： 监控目录代码 测试不需要
    # 获取当前UTC时间
    try:
        utc_now = datetime.datetime.now(datetime.timezone.utc)
        # 确定监测的文件目录
        data_path, _ = get_data_path(BASE_DATA_PATH, utc_now)
        Data_Path = data_path
        watch_manager = pyinotify.WatchManager()
        watch_manager.add_watch(data_path, pyinotify.ALL_EVENTS, rec=True)
        file_event_handler = FileEventHandler()
        notifier = pyinotify.ThreadedNotifier(watch_manager, file_event_handler)
        # 开启文件监控
        notifier.start()

        if MODE == 0:
            # 获取rib文件
            rib_file = get_rib_file(BASE_DATA_PATH)
            feature_logger.info(f"rib文件路径: {rib_file}")
            rib_file_path = os.path.dirname(rib_file)
            rib_file_basename = os.path.basename(rib_file)
            ### XXX: update的文件名对应的时间是这个update文件中第一个update报文的时间
            Current_Updates_File = rib_file_path + '/' + get_current_file_name_from_rib(rib_file_basename)
            feature_logger.info("当前需要update文件为: {}".format(Current_Updates_File))
            update_list = get_updates_file_list(base_data_path=BASE_DATA_PATH, rib_file=rib_file_basename)
            
            # 断点续传：检查上次处理进度
            last_processed = load_progress()
            if last_processed and os.path.exists(last_processed):
                # 过滤掉已处理的文件
                original_count = len(update_list)
                update_list = [f for f in update_list if f > last_processed]
                skipped_count = original_count - len(update_list)
                feature_logger.info(f"断点续传：跳过已处理的{skipped_count}个文件，从 {update_list[0] if update_list else 'N/A'} 继续")
            
            Updates_File_Queue.extend(update_list)
            Updates_File_Queue.sort()
            feature_logger.info(
                f"获取Updates文件列表成功，FIRST FILE：{update_list[0] if update_list else 'N/A'}，LAST_FILE：{update_list[-1] if update_list else 'N/A'}, number of files: {len(update_list)}"
            )
        else:
            rib_file_path = get_rib_path(BASE_DATA_PATH, RIB_HISTORY_FILE)
            feature_logger.info(f"rib文件路径: {rib_file_path}")
            rib_file = os.path.join(rib_file_path, RIB_HISTORY_FILE)
            Current_Updates_File = rib_file_path + '/' + get_current_file_name_from_rib(rib_file)
            update_list = get_updates_file_list(base_data_path=BASE_DATA_PATH, rib_file=RIB_HISTORY_FILE)
            Updates_File_Queue.extend(update_list)
            Updates_File_Queue.sort()
            feature_logger.info(
                f"获取Updates文件列表成功，FIRST FILE：{update_list[0]}，LAST_FILE：{update_list[-1]}, number of files: {len(update_list)}"
            )

        # 判断rib文件是否合法
        if rib_file == "":
            feature_logger.error("没有找到合适的rib文件, 程序结束")
            sys.exit(-1)

        # 解压rib文件
        start = time.time()
        # XXX TODO
        # rib_file = dump_file(rib_file, FEATURE_DUMP_DIR)
        rib_file = "/home/bgpdata/data/test/ribs/bview.20260227.1600.gz.data"
        end = time.time()
        feature_logger.info(f"rib文件解压耗时: {end - start}秒")

        # 获得数据库连接
        conn = get_conn(database=DATABASE, user=USER, password=PASSWORD, host=HOST, port=PORT)

        #### XXX
        create_tables(conn)  # 创建必要的表

        # 创建HijackDetect对象
        feature_detector = BGPFeature(
            rib_file=rib_file,
            conn=conn,
        )

        is_init = False
        # count = 0
        # is_handle_file = False

    # TODO: try catch exit
        while True:
            ### XXX: 监控代码 测试不需要
            # 获取当前UTC时间
            utc_now = datetime.datetime.now(datetime.timezone.utc)
            # 确定监测的文件目录
            data_path, data_path_last_month = get_data_path(BASE_DATA_PATH, utc_now)

            if os.path.exists(data_path):
                if data_path != Data_Path:
                    try:
                        notifier.stop()  # 停止对上一个文件夹的监控
                        Data_Path = data_path
                        # 监测文件目录
                        # 启动文件监视功能
                        watch_manager = pyinotify.WatchManager()
                        watch_manager.add_watch(data_path, pyinotify.ALL_EVENTS, rec=True)
                        file_event_handler = FileEventHandler()
                        notifier = pyinotify.ThreadedNotifier(watch_manager, file_event_handler)
                        notifier.start()
                        feature_logger.info("监测目录更新成功")
                    except:
                        feature_logger.error("监测目录更新失败")

            # 如果文件队列不为空，且当前需要处理的文件为队列中的第一个文件，则处理该文件 否则等待
            while len(Updates_File_Queue) > 0:
                # is_handle_file = True
                updates_file = Updates_File_Queue.pop(0)
                # 根据updates文件的文件名确定当前数据表的名称
                # XXX
                if is_init is False:
                    feature_detector.init_feature()
                    is_init = True
                start = int(time.time())
                feature_logger.info(f"Handling file: {updates_file}")
                updates_file_original = updates_file  # 保存原始路径用于记录进度
                updates_file = dump_file(updates_file, FEATURE_DUMP_DIR)
                feature_detector.update_checking(updates_file=updates_file)
                remove_file(updates_file)
                end = int(time.time())
                feature_logger.info(f"update文件{updates_file}处理完毕，耗时{end - start}秒")
                
                # 保存处理进度
                save_progress(updates_file_original)
                
                Last_File_Name = Current_Updates_File
                Current_Updates_File_basename = get_current_file_name_from_updates(Last_File_Name)
                Current_Updates_File = get_update_file_abspath(Current_Updates_File_basename)
                feature_logger.info(f"当前需要updates文件为{Current_Updates_File}")

            feature_logger.info("waiting new file, sleep 30s...")
            time.sleep(30)
    except KeyboardInterrupt:
        feature_logger.info("KeyboardInterrupt  Ctrl+C 中断")
        sys.exit(-1)
    except Exception as e:
        feature_logger.error(f"Error: {e}")
        feature_logger.error(f"Error: {traceback.format_exc()}")
        sys.exit(-1)
    finally:
        try:
            if 'notifier' in locals():
                notifier.stop()
            if 'conn' in locals():
                conn.close()
        except:
            pass


if __name__ == "__main__":
    main()
 
   
