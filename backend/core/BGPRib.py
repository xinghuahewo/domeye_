import datetime
import sys
import time
import pytricia
import traceback
import ipaddress
from utils.get_as_info import get_as_country
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FEATURE_IPV4_MIN_PREFIXLEN = 8
FEATURE_IPV6_MIN_PREFIXLEN = 16
FEATURE_EXCLUDED_PREFIXES = {
    # 2026-04-06: AS34369 abnormal wide-origin announcements that distort Iran feature counts.
    "120.0.0.0/11",
    "173.0.0.0/11",
}


def normalize_bgp_prefix(prefix):
    try:
        network = ipaddress.ip_network(str(prefix).strip(), strict=False)
    except ValueError:
        return None, None, None
    return str(network), network.version, network.prefixlen


def get_feature_prefix_skip_reason(version, prefixlen, prefix=None):
    if prefix in FEATURE_EXCLUDED_PREFIXES:
        return 'manually_excluded_prefix'
    if version == 4 and prefixlen < FEATURE_IPV4_MIN_PREFIXLEN:
        return 'oversized_ipv4_prefix'
    if version == 6 and prefixlen < FEATURE_IPV6_MIN_PREFIXLEN:
        return 'oversized_ipv6_prefix'
    return None


class BGPRib:
    """数据结构：
        vp_set: 观测点结合  vp的结合

        prefix_dict: {prefix: {vp1: as_path1, vp2: as_path2}}  # 到达该前缀的路径

        prefix_as: {"prefix": {"pub_as1",}}

        as_prefix: {"asn": {"prefix1", "prefix2"}}

        country_dict: {"country": "edge"} # 国家拓扑 国内as的边

        ipv4_tree: {prefix: {"pub_as1",}}
        ipv6_tree: {prefix: {"pub_as1",}}
        
        构建ipv4与ipv6前缀树，即对prefix_dict中所有的前缀按v4与v6分别加入到前缀树中 前缀信息也会作为附加信息

    """
    def __init__(self, bgp_info, rib_file, country_filter=None, load_rib=True):
        """
        Args:
            bgp_info (_type_): 实体信息类对象
            rib_file (_type_): rib文件路径
        """
        
        self.bgp_info = bgp_info  # bgp_info 基础信息
        self.rib_file = rib_file  # rib_file 初始化所需的rib文件路径
        self.country_filter = country_filter

        self.vp_set = set()  # 存储所有的VP
        self.prefix_dict =  dict()  # Dict[str, Dict]
        self.prefix_as = dict() # Dict[str, set()]  # 判断moas现象
        self.as_prefix = dict() # Dict[str, set()]  # 判断子前缀劫持现象
        self.t = ''  # rib的时间戳

        
        self.ipv4_tree = pytricia.PyTricia(32)
        self.ipv6_tree = pytricia.PyTricia(128)

        # load_rib=False 用于从快照恢复，避免启动时重新全量读取 bview。
        if load_rib:
            self.init_rib()

    def _is_asn_in_country_filter(self, asn):
        if self.country_filter is None or asn is None or asn == '':
            return True
        as_country = get_as_country(self.bgp_info.as_info, asn)
        return as_country is not None and as_country != '' and as_country == self.country_filter



    def init_rib(self):
        print(f"开始初始化RIB文件: {self.rib_file}")
        start = time.time()
        line_count = 0
        t = ''
        t_flag = False
        with open(self.rib_file) as f:
            for line in f:
                line_count += 1
                fields = line.strip().split('|')
                if len(fields) < 7:
                    continue

                try:
                    timestamp, vp, prefix, path = fields[1], fields[4], fields[5], fields[6]

                    normalized_prefix, _, _ = normalize_bgp_prefix(prefix)
                    if normalized_prefix is None or normalized_prefix == '0.0.0.0/0' or normalized_prefix == '::/0':
                        continue
                    prefix = normalized_prefix

                    if t_flag is False:
                        # 先转换成utc时间 + 8小时
                        t = datetime.datetime.fromtimestamp(int(timestamp), datetime.timezone.utc) + datetime.timedelta(hours=8)
                        t = t.strftime("%Y-%m-%d %H:%M:%S")
                        self.t = t


                    t_flag = True
                    # vp= as_path_fields[0]
                    asn = self.__get_origin_asn(path.split(' '))
                    if asn is None or asn == '':
                        continue

                    if not self._is_asn_in_country_filter(asn):
                        continue

                except:
                    print(f"Error reading line {line_count} of RIB file: {self.rib_file}")
                    print(traceback.format_exc())
                    continue

                self.update_dict(prefix, vp, path, asn)


        self.rebuild_trees()

        end = time.time()
        print(f"RIB文件初始化完成: 总行数 {line_count}, 耗时 {end - start:.2f}秒, ")

    def rebuild_trees(self):
        # pytricia 本身不直接参与序列化，恢复后统一由 prefix_as 重建前缀树。
        self.ipv4_tree = pytricia.PyTricia(32)
        self.ipv6_tree = pytricia.PyTricia(128)

        tree_start = int(time.time())
        for prefix, as_set in self.prefix_as.items():
            normalized_prefix, version, _ = normalize_bgp_prefix(prefix)
            if normalized_prefix is None:
                continue
            if version == 4:
                self.ipv4_tree.insert(normalized_prefix, as_set.copy())
            elif version == 6:
                self.ipv6_tree.insert(normalized_prefix, as_set.copy())
        tree_end = int(time.time())
        print(f"前缀树构建完成, 共花费了{tree_end - tree_start}秒")

    def dump_state(self):
        # 快照只保存可恢复的核心状态，启动后再重建派生结构。
        # 这里的“路由内存态”包含：
        # - vp_set: 当前见过的观测点集合
        # - prefix_dict: prefix -> {vp: as_path}，表示每个前缀当前被哪些 VP 看到、路径是什么
        # - prefix_as: prefix -> {origin_as}，表示当前前缀的 origin 集合
        # - as_prefix: asn -> {prefix}，表示当前 ASN 持有的前缀集合
        # - t: 当前 RIB/状态时间
        # 不直接落盘的对象包括 pytricia 前缀树；它是 prefix_as 的派生索引，恢复时重建。
        return {
            'vp_set': self.vp_set.copy(),
            'prefix_dict': {
                prefix: vp_path_dict.copy()
                for prefix, vp_path_dict in self.prefix_dict.items()
            },
            'prefix_as': {
                prefix: as_set.copy()
                for prefix, as_set in self.prefix_as.items()
            },
            'as_prefix': {
                asn: prefix_set.copy()
                for asn, prefix_set in self.as_prefix.items()
            },
            't': self.t,
        }

    def load_state(self, state):
        # 恢复顺序必须先回填字典/set，再重建 pytricia。
        # 也就是说 checkpoint 并不是完整 Python 对象 dump，而是“核心状态 + 派生索引重建”。
        self.vp_set = set(state.get('vp_set', set()))
        self.prefix_dict = {
            prefix: dict(vp_path_dict)
            for prefix, vp_path_dict in state.get('prefix_dict', {}).items()
        }
        self.prefix_as = {
            prefix: set(as_set)
            for prefix, as_set in state.get('prefix_as', {}).items()
        }
        self.as_prefix = {
            asn: set(prefix_set)
            for asn, prefix_set in state.get('as_prefix', {}).items()
        }
        self.t = state.get('t', '')
        self.rebuild_trees()


    def has_parent_prefix(self, prefix):
        """
        Check if the prefix has a parent prefix in the prefix_dict
        Returns:
            parent (str): Parent prefix if the prefix has a parent prefix, False otherwise
        """
        try:
            version = ipaddress.ip_network(prefix).version
            if version == 4:
                tree = self.ipv4_tree
            elif version == 6:
                tree = self.ipv6_tree
            if prefix in tree:
                # 如果prefix在树中 使用parent获取父前缀
                parent = tree.parent(prefix)
            else:
                # 如果prefix不在树中 使用get_key获取父前缀  不能使用parent 否在回报错
                parent = tree.get_key(prefix)
            if parent is not None and parent != prefix and parent != '0.0.0.0/0' and parent != '::/0':
                return parent
            else:
                return False
        except:
            return False
        
    
    def __check_private_as(self, asn):
        if '{' in asn:
            return False
        if '_' in asn:
            return True
        try:
            asn_int = int(asn)
            # 16位私有ASN: 64512-65535
            # 32位私有ASN: 4200000000-4294967294
            return (64512 <= asn_int <= 65535) or (4200000000 <= asn_int <= 4294967294)
        except ValueError:
            return False

    def __get_origin_asn(self, as_path_fields):
        # 不考虑私有AS直接找到最后的共有AS
        for index in range(len(as_path_fields)-1, -1, -1):
            asn = as_path_fields[index]
            if self.__check_private_as(asn):
                continue
            else:
                return asn
        # 如果as_path中全是私有AS，则返回最后一个私有AS
        return as_path_fields[-1]

    def update_rib(self, flag, prefix, vp, as_path, old_origin_set):
        """Update the data structure according to the routing messages
        Params:
            flag (str): The type of the message, e.g., 'A', 'W'
            prefix (str): The prefix being updated
            vp (str): The VP associated with the prefix
            as_path (str): The AS path associated with the prefix
        
        """
        normalized_prefix, version, _ = normalize_bgp_prefix(prefix)
        if normalized_prefix is None:
            return
        prefix = normalized_prefix

        if flag == 'W':
            # Withdrawn prefix
            if prefix in self.prefix_dict and vp in self.prefix_dict[prefix]:
                
                # 1. 先获取被删除路径的 origin_asn
                withdrawn_path = self.prefix_dict[prefix][vp]
                withdrawn_origin_asn = self.__get_origin_asn(withdrawn_path.split(' '))
                
                # 2. 删除路径
                # delete vp from prefix_dict
                del self.prefix_dict[prefix][vp]
                # if prefix_dict[prefix] is empty, delete prefix
                if not self.prefix_dict[prefix]:
                    del self.prefix_dict[prefix]

                ### update prefix_as
                # old_origin_asn = self.prefix_as.get(prefix, set())

                # 3. 检查是否还有其他路径宣告这个 withdrawn_origin_asn
                is_origin_still_present = False
                if prefix in self.prefix_dict:
                    for path in self.prefix_dict[prefix].values():
                        if self.__get_origin_asn(path.split(' ')) == withdrawn_origin_asn:
                            is_origin_still_present = True
                            break

                # 只有当这个 origin_asn 不再被任何路径宣告时，才从 prefix_as 中移除
                if not is_origin_still_present:
                    if withdrawn_origin_asn in self.prefix_as.get(prefix, set()):
                        self.prefix_as[prefix].discard(withdrawn_origin_asn)
                        if not self.prefix_as[prefix]:
                            del self.prefix_as[prefix]
                    
                    # 更新 as_prefix
                    if withdrawn_origin_asn in self.as_prefix and prefix in self.as_prefix[withdrawn_origin_asn]:
                        self.as_prefix[withdrawn_origin_asn].discard(prefix)
                        if not self.as_prefix[withdrawn_origin_asn]:
                            del self.as_prefix[withdrawn_origin_asn]

                # 4. 更新前缀树
                tree = self.ipv4_tree if version == 4 else self.ipv6_tree
                if prefix not in self.prefix_dict:
                    if prefix in tree:
                        tree.delete(prefix)
                else:
                    # 只有在 origin_set 确实改变时才更新树
                    if not is_origin_still_present:
                        tree.insert(prefix, self.prefix_as.get(prefix, set()).copy())
            
        elif flag == 'A':
            as_path_fields = as_path.split(' ')
            asn = self.__get_origin_asn(as_path_fields)

            if not self._is_asn_in_country_filter(asn):
                return

            self.update_dict(prefix, vp, as_path, asn)
            ### update pyt
            # 修改前缀树
            if version == 4:
                # cover the value of prefix in the tree, if exist
                self.ipv4_tree.insert(prefix, self.prefix_as.get(prefix, set()).copy())
            elif version == 6:
                self.ipv6_tree.insert(prefix, self.prefix_as.get(prefix, set()).copy())
        else:
            # flag is not 'A' or 'W'
            pass


    def update_dict(self, prefix, vp, path, asn):
        """Update the data structure according to the routing messages(A)"""
        normalized_prefix, _, _ = normalize_bgp_prefix(prefix)
        if normalized_prefix is None:
            return
        prefix = normalized_prefix

        ### update vp_set
        if vp not in self.vp_set:
            self.vp_set.add(vp)
        ### update prefix_dict
        if prefix not in self.prefix_dict:
            self.prefix_dict[prefix] = dict()
        self.prefix_dict[prefix][vp] = path
        ### update prefix_as
        self.prefix_as.setdefault(prefix, set()).add(asn)
        ### update as_prefix
        self.as_prefix.setdefault(asn, set()).add(prefix)


if __name__ == "__main__":
    from BGPInfo import BGPInfo
    bgp_info = BGPInfo()
    start = time.time()
    obj = BGPRib(bgp_info, rib_file='/home/bgpdata/data/test/0708/bview.20250708.0000.data')
    end = time.time()
    print(f"初始化完成, 耗时 {end - start:.2f}秒")
    obj.get_stat()
