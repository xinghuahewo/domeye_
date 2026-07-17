import ipaddress
import pandas as pd

df = pd.read_csv("/home/bgpdata/Domeye/backend/info/as_entity.csv", keep_default_na=False, usecols=['asn', 'as_name', 'as_country', \
            'as_country_cn', 'type', 'org_name', 'org_name_cn'])

df.drop_duplicates(subset=['asn'], keep='first', inplace=True)
df['asn'] = df['asn'].astype(str)
afg_as_set = set(df[df['as_country'] == 'AF']['asn'].tolist())
print("Total {} AFG ASes".format(len(afg_as_set)))

def cal_rib_afg_boundary_as(rib_file):

    def get_afg_asn(as_path_fields):
        for asn in as_path_fields:
            if asn in afg_as_set:
                return asn
        return None


    boundary_afg_as = set()
    afg_curr_as_set = set()

    with open(rib_file) as f:
        for line in f:
            fields = line.strip().split('|')
            if len(fields) < 7:
                continue
            prefix = fields[5]
            path = fields[6]

            try:
                as_path_fields = path.split(' ')
                vp = as_path_fields[0]
                origin_as = as_path_fields[-1]
                if origin_as not in afg_as_set:
                    continue
                
                afg_curr_as_set.add(origin_as)
                afg_asn = get_afg_asn(as_path_fields)
                boundary_afg_as.add(afg_asn)
            except:
                print("Error reading line {} of RIB file".format(line))
                continue

    print("Total {} AFG boundary ASes".format(len(boundary_afg_as)))
    print("Total {} AFG current ASes".format(len(afg_curr_as_set)))
    # with open("/home/bgpdata/Domeye/backend/tests/afg_boundary_as.txt", 'w') as f:
    #     for asn in boundary_afg_as:
    #         f.write(asn + '\n')


# 给定v6前缀集合 算一个/48的个数
SEGMENT_SHIFT = 128 - 48  # 右移80位获取/48段的编号


def calculate_v6_segments_count(v6_prefixes):
    c_segment_set = set()
    for prefix in v6_prefixes:
        try:
            network = ipaddress.IPv6Network(prefix, strict=False)
        except ValueError:
            print(f"Error processing v6 prefix: {prefix}")
            continue

        if network.prefixlen > 48:
            continue

        base_segment = int(network.network_address) >> SEGMENT_SHIFT
        segment_count = 1 << (48 - network.prefixlen)

        for offset in range(segment_count):
            c_segment_set.add(base_segment + offset)

    return len(c_segment_set)



import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.BGPRib import BGPRib
from core.BGPInfo import BGPInfo
from utils.utilitys import file_to_time, get_updates_file_list, dump_file, remove_file
from config.config import FEATURE_DUMP_DIR
import datetime
from config.config import BASE_DATA_PATH
from BGPFeature import calculate_c_segments_count

class Boundary:
    def __init__(self, rib_file):
        self.rib_file = rib_file
        self.bgp_info = BGPInfo()
        self.bgp_rib = BGPRib(self.bgp_info, rib_file)
        self.boundary_afg_as = set()
        self.afg_as = set()

        self.result = [] # t, len(as_set), as_set

        self.feature = []

    def get_boundary_asn(self, as_path_fields):
        for asn in as_path_fields:
            if asn in afg_as_set:
                return asn
        return None

    def init(self):
        # 从rib中读取该国所有的边界as
        for prefix, vp_dict in self.bgp_rib.prefix_dict.items():
            for vp, as_path in vp_dict.items():
                as_path_fields = as_path.split(' ')
                origin_as = as_path_fields[-1]
                if origin_as not in afg_as_set:
                    continue
                self.afg_as.add(origin_as)
                boundary_asn = self.get_boundary_asn(as_path_fields)
                if boundary_asn:
                    self.boundary_afg_as.add(boundary_asn)

        print("Init finish from RIB file {}, total {} AFG ASes".format(self.rib_file, len(self.afg_as)))

    def update_checking(self, updates_file):
        """处理update文件

        Args:
            updates_file (str): update文件路径
        """
        t = ""
        # 注意一个问题 在中心 文件名字是北京时间 但是在ripe 文件名字是utc时间
        file_time = file_to_time(updates_file) 
        file_time = file_time + datetime.timedelta(hours=8)

        with open(updates_file, 'r') as f:
            for update_message in f:
                fields = update_message.strip().split('|')
                
                # 测试
                try:
                    timestamp, flag, vp, prefix = int(fields[1]), fields[2], fields[4], fields[5]
                    if flag == 'STATE' or prefix == '0.0.0.0/0' or prefix == '::/0':
                        continue
                    if flag == 'A':  # 宣告路由加油as_path
                        as_path = fields[6]
                        if as_path.split(' ')[-1] not in self.afg_as:
                            continue
                    else:
                        as_path = self.bgp_rib.prefix_dict.get(prefix, {}).get(vp, '')
                except:
                    print(f"update_message: {update_message}")
                    continue



                ### T时区问题  转成utc时间 + 8h
                t = datetime.datetime.fromtimestamp(int(timestamp) + 28800, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

                old_origin_set = self.bgp_rib.prefix_as.get(prefix, set()).copy()
                self.bgp_rib.update_rib(flag, prefix, vp, as_path, old_origin_set)
        
        # 读完一个update 计算afg扔保留的边界as
        normal_afg_as = set()
        for asn in self.afg_as:
            if asn in self.bgp_rib.as_prefix.keys():
                normal_afg_as.add(asn)
        self.result.append((file_time, len(normal_afg_as), ','.join(normal_afg_as)))
        print(f"Finish update file {updates_file}, time {t}, total {len(normal_afg_as)} ASes")

        # 计算此时边界as的v4 C段个数 以及v6 前缀个数 以及总ip数
        v4_prefix_count = 0
        v6_prefix_count = 0

        is_first = True

        for asn in self.afg_as:
            prefixes = self.bgp_rib.as_prefix.get(asn, {})
            v4_prefixes = [p for p in prefixes if '.' in p]
            v6_prefixes = [p for p in prefixes if ':' in p]
            v4_prefix_count = calculate_c_segments_count(v4_prefixes)
            v6_prefix_count = calculate_v6_segments_count(v6_prefixes)
            v4_ip_count = v4_prefix_count * 256

            self.feature.append((file_time, asn, v4_prefix_count, v6_prefix_count, v4_ip_count))
            # if is_first:
            #     print(f"Sample AS {asn} at time {file_time}: v4_prefix_count={v4_prefix_count}, v6_prefix_count={v6_prefix_count}, v4_ip_count={v4_ip_count}")
            #     is_first = False


    def save_to_csv(self):
        df = pd.DataFrame(self.feature, columns=['time', 'asn', 'v4_prefix_count', 'v6_prefix_count', 'v4_ip_count'])
        df.to_csv('/home/bgpdata/Domeye/backend/tests/afg_as_feature_over_time.csv', index=False)

    # 保存结果
    def save_result(self):
        # 保存为csv
        df = pd.DataFrame(self.result, columns=['time', 'afg_as_count', 'afg_as_set'])
        df.to_csv('/home/bgpdata/Domeye/backend/tests/afg_as_over_time.csv', index=False)

        

def run(rib_file):
    update_list = get_updates_file_list(base_data_path=BASE_DATA_PATH, rib_file=os.path.basename(rib_file))
    update_list.sort()
    update_list = [p for p in update_list if os.path.basename(p) < "updates.20251002.0000.gz"]
    print(f"Total {len(update_list)} update files to process")

    rib_file = "/home/bgpdata/Domeye/data/test/bview.20250929.0800.data"
    detector = Boundary(rib_file)
    detector.init()
    print(f"Init finish, total {len(detector.boundary_afg_as)} AFG boundary ASes")
    
    while update_list:
        update_file = update_list.pop(0)
        update_file = dump_file(update_file, FEATURE_DUMP_DIR)
        detector.update_checking(update_file)
        remove_file(update_file)
    detector.save_result()
    detector.save_to_csv()

if __name__ == "__main__":
    rib_file = "/home/bgpdata/data/ripe/rrc00/2025.09/bview.20250929.0800.gz"
    run(rib_file)
    # cal_rib_afg_boundary_as("/home/bgpdata/Domeye/data/test/bview.20250929.0800.data")



