import datetime
import os.path
import sys
import time
import pyinotify
import ipaddress
import pandas as pd
import numpy as np
import traceback
import json
from collections import defaultdict


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
from config.config import RIB_HISTORY_FILE, MODE, \
     ROUTING_RES_TABLE, VP_RES_TABLE, AS_INFO_FILE, COUNTRY_INFO_FILE, BASE_DATA_PATH, PREFIX_COUNT_DUMP_DIR, \
     BASE_DIR, COUNTRY_TOPOLOGY_EDGE_TABLE, COUNTRY_TOPOLOGY_SNAPSHOT_TABLE, COUNTRY_TOPOLOGY_FULL_EDGE_THRESHOLD
from config.logger import prefix_count_logger
from config.database import DATABASE, USER, PASSWORD, HOST, PORT

from database.utils import get_conn, if_table_exist
from database.prefix_count import create_prefix_count_table, prefix_count_insert
from database.vp_resource import create_vp_resource_table, upsert_vp_resource_rows
from database.country_topology import (
    create_country_topology_edge_table,
    create_country_topology_snapshot_table,
    delete_country_edges,
    insert_country_edges,
    upsert_country_snapshot,
)
from utils.prefix_quantity import calculate_c_segments_count, calculate_v6_48_segments_count

from utils.utilitys import file_to_time, dump_file, remove_file, get_rib_file, get_rib_path, get_data_path


class PrefixCount:
    TRACKED_COLLECTORS = ('9808', '4837', '4134')
    GLOBAL_BUCKET = 'global'

    """
    prefix_count_dict = {
        collector: {
            time: {
                "ipv4_prefix": set()
                "ipv6_prefix": set()
                "private_as": set()
            }
        }
    }
    """

    def __init__(self, as_info_file, country_file, conn, prefix_count_table):
        self.as_info_file = as_info_file  # AS信息文件
        self.country_file = country_file
        self.currently_abnormal = dict()
        self.normal_range = dict()
        self.vp_normal_range = dict()
        self.as_info = dict()
        self.country = dict()
        self.prefix_count_dict = dict()
        self.vp_prefix_count_dict = dict()
        self.conn = conn  # 数据库连接
        self.prefix_count_table = prefix_count_table  # 路由资源统计表
        self.vp_table = VP_RES_TABLE
        self.rib_count = 0

    def init(self):
        self.__init_as_info()
        self.__init_info()

    def __init_info(self):
        collector_items = ['ipv4_prefix_count', 'ipv6_prefix_count', 'private_as_count', 'path_count', 'public_as_count']
        for collector in [*self.TRACKED_COLLECTORS, self.GLOBAL_BUCKET]:
            self._ensure_normal_range_bucket(self.normal_range, collector, collector_items)

        prefix_count_logger.info("init info success!")

    def _ensure_normal_range_bucket(self, range_store, bucket: str, items):
        if bucket not in range_store:
            range_store[bucket] = dict()
        for item in items:
            if item not in range_store[bucket]:
                range_store[bucket][item] = {'list_len': 0}

    def _ensure_bucket(self, bucket: str, time_key: str):
        self.currently_abnormal[bucket] = False
        if bucket not in self.prefix_count_dict:
            self.prefix_count_dict[bucket] = dict()
        if time_key not in self.prefix_count_dict[bucket]:
            self.prefix_count_dict[bucket][time_key] = dict()
            self.prefix_count_dict[bucket][time_key]['ipv4_prefix'] = set()
            self.prefix_count_dict[bucket][time_key]['ipv6_prefix'] = set()
            self.prefix_count_dict[bucket][time_key]['vp_set'] = set()
            self.prefix_count_dict[bucket][time_key]['private_as'] = set()
            self.prefix_count_dict[bucket][time_key]['path'] = set()
            self.prefix_count_dict[bucket][time_key]['public_as'] = set()
            self.prefix_count_dict[bucket][time_key]['ipv4_prefix_count'] = 0
            self.prefix_count_dict[bucket][time_key]['ipv6_prefix_count'] = 0
            self.prefix_count_dict[bucket][time_key]['ipv4_address_count'] = 0
            self.prefix_count_dict[bucket][time_key]['ipv6_48_count'] = 0
            self.prefix_count_dict[bucket][time_key]['vp_count'] = 0
            self.prefix_count_dict[bucket][time_key]['private_as_count'] = 0
            self.prefix_count_dict[bucket][time_key]['path_count'] = 0
            self.prefix_count_dict[bucket][time_key]['public_as_count'] = 0
            self.prefix_count_dict[bucket][time_key]['is_outlier'] = False

    def _ensure_vp_bucket(self, vp_asn: str, time_key: str):
        self._ensure_normal_range_bucket(self.vp_normal_range, vp_asn, ['ipv4_prefix_count', 'ipv6_prefix_count'])
        if vp_asn not in self.vp_prefix_count_dict:
            self.vp_prefix_count_dict[vp_asn] = dict()
        if time_key not in self.vp_prefix_count_dict[vp_asn]:
            self.vp_prefix_count_dict[vp_asn][time_key] = {
                'ipv4_prefix': set(),
                'ipv6_prefix': set(),
                'ipv4_prefix_count': 0,
                'ipv6_prefix_count': 0,
                'is_outlier': False,
            }

    def _safe_as_rank(self, asn: str):
        value = self.as_info.get(asn, {}).get('global_rank')
        if value in ['', None]:
            return None
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    def _build_vp_rows(self, time_key: str):
        rows = []
        active_vps = [vp for vp, vp_data in self.vp_prefix_count_dict.items() if time_key in vp_data]
        for vp in active_vps:
            self.vp_prefix_count_dict[vp][time_key]['ipv4_prefix_count'] = len(self.vp_prefix_count_dict[vp][time_key]['ipv4_prefix'])
            self.vp_prefix_count_dict[vp][time_key]['ipv6_prefix_count'] = calculate_v6_48_segments_count(self.vp_prefix_count_dict[vp][time_key]['ipv6_prefix'])
            self.__check_vp_outlier(vp=vp, time=time_key)
            rows.append({
                'time': time_key,
                'asn': vp,
                'as_name': self.as_info.get(vp, {}).get('as_name', ''),
                'as_rank': self._safe_as_rank(vp),
                'ipv4_prefix_count': self.vp_prefix_count_dict[vp][time_key]['ipv4_prefix_count'],
                'ipv6_prefix_count': self.vp_prefix_count_dict[vp][time_key]['ipv6_prefix_count'],
                'is_outlier': self.vp_prefix_count_dict[vp][time_key]['is_outlier'],
            })
        return rows

    def _cleanup_old_state(self, state_store, bucket: str, file_name: str, keep_days=3):
        cutoff = str(file_to_time(file_name) - datetime.timedelta(days=keep_days))
        for prev_time in list(state_store.get(bucket, {}).keys()):
            if prev_time < cutoff:
                del state_store[bucket][prev_time]

    def _get_target_buckets(self, observed_vp: str):
        buckets = [self.GLOBAL_BUCKET]
        if observed_vp in self.TRACKED_COLLECTORS:
            buckets.append(observed_vp)
        return buckets

    def update_rib(self, rib_file):
        """读取rib文件"""
        line_count = 0
        self.rib_count += 1
        file_name = os.path.basename(rib_file)
        try:
            file_size = os.path.getsize(rib_file)
        except Exception:
            file_size = -1
        time = str(file_to_time(file_name))
        prefix_count_logger.info(
            f"[RIB] update_rib start: file={rib_file}, size={file_size}, parsed_time={time}, rib_count={self.rib_count}"
        )
        self._ensure_bucket(self.GLOBAL_BUCKET, time)
        
        with open(rib_file) as f:
            for line in f:
                line_count += 1
                if line_count % 1_000_000 == 0:
                    prefix_count_logger.info(f"[RIB] parsing progress: file={file_name}, lines={line_count}")
                fields = line.strip().split('|')
                if len(fields) < 7:
                    continue
                flag, prefix, path = fields[2], fields[5], fields[6]
                if flag == 'STATE' or prefix == '0.0.0.0/0' or prefix == '::/0':
                    continue
                try:
                    as_path_fields = path.split(' ')
                    observed_vp = as_path_fields[0]
                    peer_asn = fields[4]
                    target_buckets = self._get_target_buckets(observed_vp)
                    for bucket in target_buckets:
                        self._ensure_bucket(bucket, time)
                    self._ensure_vp_bucket(peer_asn, time)
                    # BGP4MP|1740997582|STATE|194.50.19.65|48292|6|1
                    ip_version = ipaddress.ip_network(prefix).version
                    for bucket in target_buckets:
                        if ip_version == 4:
                            self.prefix_count_dict[bucket][time]['ipv4_prefix'].add(prefix)
                        else:
                            self.prefix_count_dict[bucket][time]['ipv6_prefix'].add(prefix)
                        self.prefix_count_dict[bucket][time]['vp_set'].add(observed_vp)
                    if ip_version == 4:
                        self.vp_prefix_count_dict[peer_asn][time]['ipv4_prefix'].add(prefix)
                    else:
                        self.vp_prefix_count_dict[peer_asn][time]['ipv6_prefix'].add(prefix)
                    # 监测点私有AS
                    last_asn = as_path_fields[-1]
                    if '{' in path:
                        continue
                    if (int (last_asn) >= 64512 and int(last_asn) <= 65535) or int(last_asn) > 4294967295:
                        private_as = last_asn
                        for bucket in target_buckets:
                            self.prefix_count_dict[bucket][time]['private_as'].add(private_as)
                    
                    # 监测点可达路径
                    for bucket in target_buckets:
                        self.prefix_count_dict[bucket][time]['path'].add(path)

                    # 监测点可达AS
                    public_as = None
                    last_asn = as_path_fields[-1]
                    if (int(last_asn) < 64512 or int(last_asn) > 65535) and int(last_asn) <= 4294967295:
                        public_as = last_asn
                    if public_as != None:
                        for bucket in target_buckets:
                            self.prefix_count_dict[bucket][time]['public_as'].add(public_as)
                except:
                    prefix_count_logger.info("Error processing AS path {} in RIB file: {}".format(path, rib_file))
                    continue


        try:
            active_buckets = [bucket for bucket, bucket_data in self.prefix_count_dict.items() if time in bucket_data]
            for collector in active_buckets:
                self.prefix_count_dict[collector][time]['ipv4_prefix_count'] = len(self.prefix_count_dict[collector][time]['ipv4_prefix'])
                ipv6_48_count = calculate_v6_48_segments_count(self.prefix_count_dict[collector][time]['ipv6_prefix'])
                # 路由资源表的 IPv6“前缀数”按 /48 口径统计，保持和首页展示一致。
                self.prefix_count_dict[collector][time]['ipv6_prefix_count'] = ipv6_48_count
                self.prefix_count_dict[collector][time]['ipv4_address_count'] = calculate_c_segments_count(self.prefix_count_dict[collector][time]['ipv4_prefix']) * 256
                self.prefix_count_dict[collector][time]['ipv6_48_count'] = ipv6_48_count
                self.prefix_count_dict[collector][time]['vp_count'] = len(self.prefix_count_dict[collector][time]['vp_set'])
                del self.prefix_count_dict[collector][time]['vp_set']
                self.prefix_count_dict[collector][time]['private_as_count'] = len(self.prefix_count_dict[collector][time]['private_as'])
                self.prefix_count_dict[collector][time]['path_count'] = len(self.prefix_count_dict[collector][time]['path'])
                del self.prefix_count_dict[collector][time]['path']
                self.prefix_count_dict[collector][time]['public_as_count'] = len(self.prefix_count_dict[collector][time]['public_as'])
                self.__check_outlier(vp=collector, time=time)
                prefix_count_logger.info(
                    f"[DB] prefix_count_insert begin: table={self.prefix_count_table}, collector={collector}, time={time}, "
                    f"ipv4={self.prefix_count_dict[collector][time]['ipv4_prefix_count']}, "
                    f"ipv6={self.prefix_count_dict[collector][time]['ipv6_prefix_count']}, "
                    f"ipv4_addr={self.prefix_count_dict[collector][time]['ipv4_address_count']}, "
                    f"ipv6_48={self.prefix_count_dict[collector][time]['ipv6_48_count']}, "
                    f"vp_count={self.prefix_count_dict[collector][time]['vp_count']}, "
                    f"private_as={self.prefix_count_dict[collector][time]['private_as_count']}, "
                    f"path={self.prefix_count_dict[collector][time]['path_count']}, "
                    f"public_as={self.prefix_count_dict[collector][time]['public_as_count']}, "
                    f"is_outlier={self.prefix_count_dict[collector][time]['is_outlier']}"
                )
                prefix_count_insert(prefix_count_dict=self.prefix_count_dict, normal_range=self.normal_range, 
                                    collector=collector, time=time, conn=self.conn, table=self.prefix_count_table)
                prefix_count_logger.info(f"[DB] prefix_count_insert end: table={self.prefix_count_table}, collector={collector}, time={time}")
                
                self._cleanup_old_state(self.prefix_count_dict, collector, file_name)

            vp_rows = self._build_vp_rows(time)
            if vp_rows:
                upsert_vp_resource_rows(self.conn, self.vp_table, vp_rows)
                for row in vp_rows:
                    self._cleanup_old_state(self.vp_prefix_count_dict, row['asn'], file_name)
            prefix_count_logger.info('update routing table success!')
            prefix_count_logger.info(f"[RIB] update_rib done: file={file_name}, lines={line_count}, time={time}")
        except Exception as e:
            prefix_count_logger.error(f"Error in updating routing table from RIB file {rib_file}: {e}")
            prefix_count_logger.error(f"Error: {traceback.format_exc()}")
    
    def _check_outlier_for_store(self, data_store, range_store, bucket, time_key, items, abnormal_store=None):
        self._ensure_normal_range_bucket(range_store, bucket, items)
        for item in items:
            normal_list = []
            for history_time in data_store[bucket]:
                if data_store[bucket][history_time]['is_outlier'] is False and history_time != time_key:
                    normal_list.append(data_store[bucket][history_time][item])

            if len(normal_list) > 5:
                self.__update_normal_range(range_store, bucket, item, normal_list)
            elif self.rib_count < 7:
                normal_list.append(data_store[bucket][time_key][item])
                self.__update_normal_range(range_store, bucket, item, normal_list)

            upper_bound = range_store[bucket][item].get('upper_bound')
            lower_bound = range_store[bucket][item].get('lower_bound')
            if upper_bound is None or lower_bound is None:
                continue
            if (data_store[bucket][time_key][item] > upper_bound) or (data_store[bucket][time_key][item] < lower_bound):
                data_store[bucket][time_key]['is_outlier'] = True
                if abnormal_store is not None:
                    abnormal_store[bucket] = True

    def __check_outlier(self, vp, time):
        self._check_outlier_for_store(
            self.prefix_count_dict,
            self.normal_range,
            vp,
            time,
            ['ipv4_prefix_count', 'ipv6_prefix_count', 'private_as_count', 'path_count', 'public_as_count'],
            abnormal_store=self.currently_abnormal,
        )

    def __check_vp_outlier(self, vp, time):
        self._check_outlier_for_store(
            self.vp_prefix_count_dict,
            self.vp_normal_range,
            vp,
            time,
            ['ipv4_prefix_count', 'ipv6_prefix_count'],
        )

    def __update_normal_range(self, range_store, vp, item, normal_list):
        if len(normal_list) >= range_store[vp][item]['list_len']:
            prefix_count_logger.info(range_store[vp][item]['list_len'])
            mean = np.mean(normal_list)
            std_dev = np.std(normal_list)
            upper_bound = (mean + 3 * std_dev) * 1.2
            lower_bound = (mean - 3 * std_dev) * 0.8
            prefix_count_logger.info("vp {}, item {}, normal_list {}".format(vp, item, normal_list))
            range_store[vp][item]['list_len'] = len(normal_list)
            range_store[vp][item]['upper_bound'] = int(upper_bound)
            range_store[vp][item]['lower_bound'] = int(lower_bound)

    def __init_country(self):
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
    
    def __init_as_info(self):
        df = pd.read_csv(self.as_info_file, keep_default_na=False, usecols=['asn', 'as_name', 'global_rank'])
        df.drop_duplicates(subset=['asn'], keep='first', inplace=True)
        df_new = df.set_index('asn', drop=True, append=False, inplace=False, verify_integrity=False)
        df_new.index = df_new.index.astype(str)
        self.as_info = df_new.to_dict(orient='index')
    
    def get_prefix_count_table(self):
        return self.prefix_count_table

    def set_prefix_count_table(self, prefix_count_table):
        self.prefix_count_table = prefix_count_table


class CountryTopologyBuilder:
    """
    从RIB原始AS_PATH抽取“国家内部无向AS邻接图”，并落库：
    - 无向边：按 (min_asn, max_asn) 规范化
    - 只保留同一国家内部边（两端 country_cn 相同）
    - baseline：每次重建覆盖该国旧数据
    """

    def __init__(self, conn, edge_table: str, snapshot_table: str, as_dict_path: str):
        self.conn = conn
        self.edge_table = edge_table
        self.snapshot_table = snapshot_table
        self.as_dict_path = as_dict_path
        self.as_dict = {}

    def init(self):
        create_country_topology_edge_table(self.conn, self.edge_table)
        create_country_topology_snapshot_table(self.conn, self.snapshot_table)
        self._load_as_dict()

    def _load_as_dict(self):
        with open(self.as_dict_path, encoding="utf-8") as f:
            self.as_dict = json.load(f)

    def _is_private_asn(self, asn: str) -> bool:
        """
        与BGPOutage中的判定保持一致的核心逻辑（简化版）：过滤私有ASN与异常格式。
        """
        if asn is None or asn == "":
            return True
        if "{" in asn:
            return True
        if "_" in asn:
            return True
        try:
            asn_int = int(asn)
            return (64512 <= asn_int <= 65535) or (4200000000 <= asn_int <= 4294967294)
        except ValueError:
            return True

    def _get_country_cn(self, asn: str):
        """
        返回ASN对应中文国家名。若缺失则返回None。
        """
        try:
            return self.as_dict.get(str(asn), {}).get("country_cn")
        except Exception:
            return None

    def build_and_store_from_rib(self, rib_file: str, build_time):
        """
        从解压后的RIB文件构建并落库。
        """
        try:
            file_size = os.path.getsize(rib_file)
        except Exception:
            file_size = -1
        prefix_count_logger.info(
            f"[TOPO] build start: file={rib_file}, size={file_size}, build_time={build_time}, "
            f"full_edge_threshold={COUNTRY_TOPOLOGY_FULL_EDGE_THRESHOLD}"
        )
        country_edges = defaultdict(set)  # country_cn -> set[(a_asn,b_asn)]
        line_count = 0
        edge_count_total = 0

        with open(rib_file) as f:
            for line in f:
                line_count += 1
                if line_count % 2_000_000 == 0:
                    prefix_count_logger.info(f"[TOPO] parsing progress: lines={line_count}")
                fields = line.strip().split("|")
                if len(fields) < 7:
                    continue
                as_path = fields[6]
                if not as_path or "{" in as_path:
                    continue
                path_list = as_path.split(" ")
                if len(path_list) < 2:
                    continue

                prev = None
                for raw_asn in path_list:
                    if self._is_private_asn(raw_asn):
                        continue
                    if prev is None:
                        prev = raw_asn
                        continue
                    if prev == raw_asn:
                        continue

                    c1 = self._get_country_cn(prev)
                    c2 = self._get_country_cn(raw_asn)
                    if not c1 or not c2 or c1 != c2:
                        prev = raw_asn
                        continue

                    try:
                        a = int(prev)
                        b = int(raw_asn)
                    except Exception:
                        prev = raw_asn
                        continue
                    if a == b:
                        prev = raw_asn
                        continue
                    if a > b:
                        a, b = b, a
                    country_edges[c1].add((a, b))
                    prev = raw_asn

        # 落库：baseline replace（按国家覆盖）
        for country_cn, edges in country_edges.items():
            prefix_count_logger.info(f"[TOPO][DB] write begin: country={country_cn}, edges={len(edges)}, build_time={build_time}")
            delete_country_edges(self.conn, self.edge_table, country_cn, build_time=None)
            inserted = insert_country_edges(self.conn, self.edge_table, country_cn, build_time, edges)
            edge_count_total += inserted
            prefix_count_logger.info(
                f"[TOPO][DB] write end: country={country_cn}, edges={len(edges)}, inserted={inserted}, build_time={build_time}"
            )

            # 小国生成快照，接口可直接取全量图
            try:
                if len(edges) <= COUNTRY_TOPOLOGY_FULL_EDGE_THRESHOLD:
                    node_set = set()
                    links = []
                    for a, b in edges:
                        node_set.add(str(a))
                        node_set.add(str(b))
                        links.append({
                            "source": str(a),
                            "target": str(b),
                            "value": 1,
                            "lineStyle": {"color": "#000"},
                        })
                    nodes = [{"name": n, "itemStyle": {"color": "#777"}} for n in node_set]
                    graph_json = {
                        "country_cn": country_cn,
                        "build_time": str(build_time),
                        "node_count": len(nodes),
                        "edge_count": len(links),
                        "nodes": nodes,
                        "links": links,
                    }
                    upsert_country_snapshot(self.conn, self.snapshot_table, country_cn, build_time, graph_json)
                    prefix_count_logger.info(
                        f"[TOPO][DB] snapshot upserted: country={country_cn}, nodes={len(nodes)}, edges={len(links)}, build_time={build_time}"
                    )
            except Exception:
                # 快照失败不影响边表
                prefix_count_logger.info(f"country snapshot build failed for {country_cn}")

        prefix_count_logger.info(
            f"Country topology build done. rib_lines={line_count}, countries={len(country_edges)}, edges_inserted={edge_count_total}"
        )


# 以下代码请勿修改 ####
Rib_File_Queue = list()
Last_File_Name = ""
Data_Path = ""
Data_Path_Last_month = ""
RESOURCE_TASK_LOCK_KEY = 482501


def acquire_resource_task_lock(conn):
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT pg_try_advisory_lock(%s);", (RESOURCE_TASK_LOCK_KEY,))
        locked = bool(cursor.fetchone()[0])
        if not locked:
            prefix_count_logger.error("another BGPResource instance is already running, exit")
        return locked
    except Exception as error:
        conn.rollback()
        prefix_count_logger.error(f"failed to acquire BGPResource advisory lock: {error}")
        return False
    finally:
        cursor.close()


def release_resource_task_lock(conn):
    if conn is None or getattr(conn, 'closed', 1) != 0:
        return

    cursor = conn.cursor()
    try:
        cursor.execute("SELECT pg_advisory_unlock(%s);", (RESOURCE_TASK_LOCK_KEY,))
        conn.commit()
    except Exception as error:
        conn.rollback()
        prefix_count_logger.warning(f"failed to release BGPResource advisory lock: {error}")
    finally:
        cursor.close()


class FileEventHandler(pyinotify.ProcessEvent):
    def process_IN_CLOSE_WRITE(self, event):
        """
        可写文件CLOSE时触发此函数
        :param event:
        :return:
        """
        global Last_File_Name
        full_file_path = str(event.pathname)  # 监测到的文件的完整路径
        file_name = full_file_path.split(os.sep)[-1]  # 监测到的文件的文件名
        if file_name.startswith("rib") or file_name.startswith("bview"):
            # 新的rib文件
            if Last_File_Name == "" or file_name > Last_File_Name:
                Last_File_Name = file_name
                Rib_File_Queue.append(full_file_path)
                prefix_count_logger.info("检测到新文件 {}, 添加到待处理队列。".format(full_file_path))


def main():
    global Data_Path, Data_Path_Last_month

    if MODE == 0:
        # Get the latest RIB file
        rib_file = get_rib_file(BASE_DATA_PATH)
        Rib_File_Queue.append(rib_file)

    else:
        rib_file_path = get_rib_path(BASE_DATA_PATH, RIB_HISTORY_FILE)
        rib_file = os.path.join(rib_file_path, RIB_HISTORY_FILE)
        rib_history_date = file_to_time(os.path.basename(rib_file))
        while True:
            rib_list = os.listdir(rib_file_path)
            rib_list.sort()
            if(len(rib_list) == 0):
                print("没有找到合适的rib文件,等待600秒")
                time.sleep(600)
            else:
                for r_file in rib_list:
                    if r_file.startswith("bview"):
                        rib_date = file_to_time(r_file)
                        if rib_date > rib_history_date:
                            r_file = os.path.join(rib_file_path, r_file)
                            Rib_File_Queue.append(r_file)
                            prefix_count_logger.info("检测到新文件 {}, 添加到待处理队列。".format(r_file))
                break


    # 判断rib文件是否合法
    if rib_file == "":
        prefix_count_logger.error("没有找到合适的rib文件, 程序结束")
        exit(-1)
    # 获取当前UTC时间
    utc_now = datetime.datetime.utcnow()
    # 确定监测的文件目录
    data_path, data_path_last_month = get_data_path(BASE_DATA_PATH, utc_now)
    Data_Path = data_path
    watch_manager = pyinotify.WatchManager()
    watch_manager.add_watch(data_path, pyinotify.ALL_EVENTS, rec=True)
    file_event_handler = FileEventHandler()
    notifier = pyinotify.ThreadedNotifier(watch_manager, file_event_handler)
    # 开启文件监控
    notifier.start()

    # 获得数据库连接
    conn = get_conn(database=DATABASE, user=USER, password=PASSWORD, host=HOST, port=PORT)
    if not acquire_resource_task_lock(conn):
        conn.close()
        sys.exit(-1)

    # 资源任务热路径不做表结构变更，避免 DDL 锁住查询/写入。
    if not if_table_exist(conn, ROUTING_RES_TABLE):
        create_prefix_count_table(conn=conn, prefix_count_table=ROUTING_RES_TABLE)
    else:
        prefix_count_logger.info(f"routing resource table {ROUTING_RES_TABLE} already exists, skip runtime DDL")

    if not if_table_exist(conn, VP_RES_TABLE):
        create_vp_resource_table(conn=conn, table_name=VP_RES_TABLE)
    else:
        prefix_count_logger.info(f"vp resource table {VP_RES_TABLE} already exists, skip runtime DDL")

    # 创建PrefixCount对象
    prefix_count = PrefixCount(as_info_file=AS_INFO_FILE, country_file=COUNTRY_INFO_FILE, 
                                conn=conn, prefix_count_table=ROUTING_RES_TABLE)

    topo_builder = None
    build_country_topology = os.environ.get("BUILD_COUNTRY_TOPOLOGY", "1")
    prefix_count_logger.info(f"[TOPO] BUILD_COUNTRY_TOPOLOGY={build_country_topology}")
    if build_country_topology == "1":
        try:
            as_dict_path = os.path.join(BASE_DIR, "screen_data", "info", "as_dict.json")
            topo_builder = CountryTopologyBuilder(
                conn=conn,
                edge_table=COUNTRY_TOPOLOGY_EDGE_TABLE,
                snapshot_table=COUNTRY_TOPOLOGY_SNAPSHOT_TABLE,
                as_dict_path=as_dict_path,
            )
            topo_builder.init()
            prefix_count_logger.info("CountryTopologyBuilder init success!")
        except Exception as e:
            topo_builder = None
            prefix_count_logger.error(f"CountryTopologyBuilder init failed: {e}")
            prefix_count_logger.error(f"Error: {traceback.format_exc()}")

    is_init = False
    # Rib_File_Queue1别忘了改成Rib_File_Queue
    # Rib_File_Queue1 = ['/root/bgpdata/bgpc/data/prefix_count/rib.20230904-0000.data', 
    #                     '/root/bgpdata/bgpc/data/prefix_count/rib.20231114-0000.data']
    try:
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
                        prefix_count_logger.info("监测目录更新成功")
                    except:
                        prefix_count_logger.error("监测目录更新失败")
            while len(Rib_File_Queue) > 0:
                prefix_count_logger.info(f"[RIB] queue pop: size_before={len(Rib_File_Queue)}")
                rib_file = Rib_File_Queue.pop(0)
                prefix_count_logger.info(f"[RIB] queue pop: file={rib_file}, size_after={len(Rib_File_Queue)}")
                build_time = None
                try:
                    build_time = file_to_time(os.path.basename(rib_file))
                except Exception:
                    build_time = datetime.datetime.utcnow()

                # 根据rib文件的文件名确定数据路径
                # file_date = file_to_time(os.path.basename(rib_file))

                if is_init is False:
                    init_start = int(time.time())
                    prefix_count.init()
                    init_end = int(time.time())
                    prefix_count_logger.info("初始化完成，耗时{}秒".format(init_end - init_start))
                    is_init = True

                start = int(time.time())
                prefix_count_logger.info("Handling file: {}".format(rib_file))
                prefix_count_logger.info(f"[RIB] bgpdump start: src={rib_file}, dump_dir={PREFIX_COUNT_DUMP_DIR}")
                rib_file = dump_file(rib_file, PREFIX_COUNT_DUMP_DIR)
                try:
                    dump_size = os.path.getsize(rib_file)
                except Exception:
                    dump_size = -1
                prefix_count_logger.info(f"[RIB] bgpdump done: out={rib_file}, size={dump_size}")

                rib_parse_start = time.time()
                prefix_count.update_rib(rib_file=rib_file)
                prefix_count_logger.info(f"[RIB] update_rib finished: file={rib_file}, cost_s={time.time() - rib_parse_start:.2f}")

                # 国家内部拓扑（baseline）：默认关闭，设置环境变量 BUILD_COUNTRY_TOPOLOGY=1 开启
                if topo_builder is not None:
                    try:
                        topo_start = time.time()
                        topo_builder.build_and_store_from_rib(rib_file=rib_file, build_time=build_time)
                        prefix_count_logger.info(f"[TOPO] build_and_store done: cost_s={time.time() - topo_start:.2f}")
                    except Exception as e:
                        prefix_count_logger.error(f"Country topology build failed: {e}")
                        prefix_count_logger.error(f"Error: {traceback.format_exc()}")

                prefix_count_logger.info(f"[RIB] cleanup start: file={rib_file}")
                remove_file(rib_file)
                prefix_count_logger.info(f"[RIB] cleanup done: file={rib_file}")
                end = int(time.time())
                prefix_count_logger.info("rib文件{}处理完毕，耗时{}秒".format(rib_file, end - start))
            prefix_count_logger.info("waiting new file...")
            time.sleep(30)
    except KeyboardInterrupt:
        prefix_count_logger.info("KeyboardInterrupt  Ctrl+C 中断")
        sys.exit(-1)
    except Exception as e:
        prefix_count_logger.error(f"Error: {e}")
        prefix_count_logger.error(f"Error: {traceback.format_exc()}")
        sys.exit(-1)
    finally:
        try:
            if 'notifier' in locals():
                notifier.stop()
            if 'conn' in locals():
                release_resource_task_lock(conn)
                conn.close()
        except:
            pass



if __name__ == '__main__':
    main()
    
