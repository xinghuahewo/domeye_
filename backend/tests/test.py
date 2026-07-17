import pandas as pd
import json
import itertools


df = pd.read_csv("/home/bgpdata/Domeye/backend/info/ip_bgp_entity.csv", keep_default_na=False)
df.drop_duplicates(subset=['prefix'], keep='first', inplace=True)
df_new = df.set_index('prefix', drop=True, append=False, inplace=False, verify_integrity=False)
prefix_info = df_new.to_dict(orient='index')


df = pd.read_csv("/home/bgpdata/Domeye/backend/info/as_entity.csv", keep_default_na=False, usecols=['asn', 'as_name', 'as_country', \
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
as_info = df_new.to_dict(orient='index')

df = pd.read_csv("/home/bgpdata/Domeye/backend/info/important_as.csv")
df.drop_duplicates(subset=['aut-num'], keep='first', inplace=True)
df_new = df.set_index('aut-num', drop=True, append=False, inplace=False, verify_integrity=False)
important_as_dict = df_new.to_dict(orient='index')

with open("/home/bgpdata/Domeye/backend/info/domain_cn_center.txt", 'r') as f:
    important_domain_dict = json.load(f)

def leak_level(prefix, asn):
    descr_info = '这仅仅是一个可能的路由泄漏事件'
    level = 'low'

    if prefix_info.get(prefix) is not None:
        num = prefix_info[prefix]['domain_num']
        auth_num = prefix_info[prefix]['domain_auth_num']
        if num > 0 or auth_num > 0:
            descr_info = ''
            if num > 30:
                descr_info = "在前缀中有" + str(num) + " 个普通网站。"
                level = 'high'
            else:
                descr_info = "在前缀中有" + str(num) + " 个普通网站。"
                level = 'middle'
            if auth_num > 15:
                # descr_info = descr_info + "在前缀中有" + str(auth_num) + " 个重点网站。"
                descr_info = descr_info + "在前缀中有" + str(auth_num) + " 个权威解析服务器。"
                level = 'high'
            else:
                if level != 'high':
                    # descr_info = descr_info + "在前缀中有" + str(auth_num) + " 个重点网站。"
                    descr_info = descr_info + "在前缀中有" + str(auth_num) + " 个权威解析服务器。"
                    level = 'middle'
    
        domain = list()
        if prefix_info[prefix]['domain'] not in [None, '']:
            domain.extend(eval(prefix_info[prefix]['domain']))
        if prefix_info[prefix]['domain_auth'] not in [None, '']:
            domain.extend(eval(prefix_info[prefix]['domain_auth']))

        for d in domain:
            if important_domain_dict.get(d) is not None:
                level = 'high'
                
                try:
                    descr_info = descr_info + '在前缀中有' + important_domain_dict.get('name', '重要') + '的网站 。'
                except:
                    descr_info = descr_info + '在前缀中有重要网站'
    try:
        if important_as_dict.get(int(asn)) is not None:
            level = 'high'
            if descr_info != '':
                descr_info = descr_info + " 并且 " + asn + " 是 Cloud|IDC|CDN 或者 顶级内容提供商 (top content provider)。"

    except:
        pass
    return level, descr_info


def get_as_org_name(as_info: dict, asn: str):
    """
    Returns the name of the organization to which the autonomous system with number asn belongs
    If there is no such asn in the as_info, return None
    :param as_info: Autonomous System Information
    :param asn: Autonomous system number
    :return: A business name or None
    """
    if '_' in asn:
        public_as = asn.split('_')[0]
        if public_as in as_info and as_info[public_as].get('org_name_cn') not in ['', None]:
            return as_info[public_as].get('org_name_cn')
        if public_as in as_info and as_info[public_as].get('org_name') not in ['', None]:
            return as_info[public_as].get('org_name')
        return ''
    else:
        if asn in as_info and as_info[asn].get('org_name_cn') not in ['', None]:
            return as_info[asn].get('org_name_cn')
        if asn in as_info and as_info[asn].get('org_name') not in ['', None]:
            return as_info[asn].get('org_name')
        return ''


def is_leak_event(leak_by, leak_to):
    if leak_by == leak_to:
        return 0, 'by equal to'


    # 未知组织过滤
    leak_by_org = get_as_org_name(as_info, leak_by)
    leak_to_org = get_as_org_name(as_info, leak_to)
    if leak_to_org:
        if '个人' in leak_to_org:
            return 0, 'personal network'
    if leak_by_org:
        if '个人' in leak_by_org:
            return 0, 'personal network'
    if leak_by_org == '未知组织' or leak_to_org == '未知组织':
        return 0, 'unknown org'
    
    if leak_by_org == leak_to_org:
        return 0, 'org equal'

    as_set = [leak_to, leak_by]
    for asn in as_set:
        if '{' in asn:
            return 0, 'as set'
        asn_num = int(asn)
        if asn_num in range(64511, 65536) or asn_num > 4294967295:
            return 0, 'private as'

    leak_by_str, leak_to_str = 'AS' + leak_by, 'AS' + leak_to

    # import export 过滤
    if leak_by in as_info.keys():
        leak_by_import = as_info[leak_by]['import_as']
        leak_by_export = as_info[leak_by]['export_as']
        if leak_to_str in leak_by_import:
            return 0, leak_by + ' import filter ' + leak_to
        if leak_to_str in leak_by_export:
            return 0, leak_by + ' export filter ' + leak_to
    if leak_to in as_info.keys():
        leak_to_import = as_info[leak_to]['import_as']
        leak_to_export = as_info[leak_to]['export_as']
        if leak_by_str in leak_to_import:
            return 0, leak_to + ' import filter ' + leak_by
        if leak_by_str in leak_to_export:
            return 0, leak_to + ' export filter ' + leak_by  

    return 1, 'possible leak'


def get_leak_triplet(triplet_info: dict, first_as: str, second_as: str, third_as: str):
    """
    获取leak triplet稳定度信息
    """
    if first_as in triplet_info and second_as in triplet_info[first_as] and third_as in triplet_info[first_as][second_as]:
        return triplet_info[first_as][second_as][third_as]['stability']
    else:
        return 0
def get_as_rel(as1, as2):
    # return: -1 as1 - as2为单一的provider-custmoer
    ##### 首先排除对等关系
    if as_rel_dict.get(as1) is not None:
        if as_rel_dict[as1].get('peers') is not None:
            if as2 in as_rel_dict[as1]['peers']:
                return 0
    if as_rel_dict.get(as2) is not None:
        if as_rel_dict[as2].get('peers') is not None:
            if as1 in as_rel_dict[as2]['peers']:
                return 0

    #### 其次排除cp关系
    if as_rel_dict.get(as1) is not None:
        if as_rel_dict[as1].get('provider') is not None:
            if as2 in as_rel_dict[as1]['provider']:
                return 1
    if as_rel_dict.get(as2) is not None:
        if as_rel_dict[as2].get('customer') is not None:
            if as1 in as_rel_dict[as2]['customer']:
                return 1

    ### 最后确定是否为pc关系
    if as_rel_dict.get(as1) is not None:
        if as_rel_dict[as1].get('customer') is not None:
            if as2 in as_rel_dict[as1]['customer']:
                return -1
    if as_rel_dict.get(as2) is not None:
        if as_rel_dict[as2].get('provider') is not None:
            if as1 in as_rel_dict[as2]['provider']:
                return -1
    return -2

import json
import itertools
with open("/home/bgpdata/Domeye/backend/info/as_rel_dict.txt", 'r') as f:
    as_rel_dict = json.load(f)

triplet_info = dict()

df = pd.read_csv("/home/bgpdata/Domeye/backend/info/triplet_20days.csv", keep_default_na=False, low_memory=False)
def init_dict(row):
    first_as, second_as, third_as = str(row['first_as']), str(row['second_as']), str(row['third_as'])
    triplet_info.setdefault(first_as, dict()).setdefault(second_as, dict()).setdefault(third_as, dict())
    triplet_info[first_as][second_as][third_as]['stability'] = row['stability']
df.apply(init_dict, axis=1)

# vp_path = "263237 52320 8048 8048 8048 8048 8048 8048 8048 8048 8048 23520 174 269832 21980"
# vp_path = "24482 52320 8048 8048 8048 8048 8048 8048 8048 8048 8048 6762 1299 269832 21980"
vp_path = "24482 52320 8048 8048 8048 8048 8048 8048 8048 8048 8048 23520 1299 269832 21980"
vp_path_fields_o = vp_path.split(' ')
vp_path_fields = [k for k, g in itertools.groupby(vp_path_fields_o)]

print(vp_path_fields)

for i in range(len(vp_path_fields) - 1, -1, -1):
    if i - 2 == -1:
        break
    
    # print("检查三元组:{}, {}, {}".format(vp_path_fields[i], vp_path_fields[i - 1], vp_path_fields[i - 2]))
    if get_as_rel(vp_path_fields[i], vp_path_fields[i - 1]) == -1 and get_as_rel(vp_path_fields[i - 2], vp_path_fields[i - 1]) == -1:
        print("检测出疑似泄露三元组:{}, {}, {}".format(vp_path_fields[i], vp_path_fields[i - 1], vp_path_fields[i - 2]))
        if get_leak_triplet(triplet_info, vp_path_fields[i], vp_path_fields[i - 1], vp_path_fields[i - 2]) >= 0.2:
            print("检测出三元组:{}, {}, {}, 稳定度为：{}".format(vp_path_fields[i], vp_path_fields[i - 1], vp_path_fields[i - 2], get_leak_triplet(triplet_info, vp_path_fields[i], vp_path_fields[i - 1], vp_path_fields[i - 2])))
            continue    

        leak_by = vp_path_fields[i - 1]
        leak_to = vp_path_fields[i]
        is_leak, filter_reason = is_leak_event(leak_by=leak_by, leak_to=leak_to)
        print("leak_by:{}, leak_to:{}, is_leak_event:{}, filter_reason:{}".format(leak_by, leak_to, is_leak, filter_reason))
        
        
        
        level = leak_level(prefix='200.74.236.0/23', asn = '21980')

        print("leak_level:{}".format(level))












# df = pd.read_csv("/home/bgpdata/Domeye/backend/info/as_entity.csv", 
#                 usecols=['asn', 'as_name', 'as_country', 'org_name', 'org_name_cn', 'global_rank', 'country_rank'])

# # df2 = pd.read_csv("/home/bgpdata/back_data/data/format/2025/06/14/rib_data.csv", 
# #                 usecols=['asn'])

# # asns = df2['asn'].unique().astype(str).tolist()
# # print(len(asns))
# # df["asn"] = df["asn"].astype(str)
# # df = df[df['asn'].isin(asns)]
# df = df[df["as_country"] == "CN"]
# print(len(df))
# # 以组织名称分组 计算asn数量
# grouped = df.groupby('org_name_cn').agg({'asn': 'nunique'}).sort_values(by='asn', ascending=False).reset_index()

# print(grouped.head(20))
# count = 0   
# for index, row in grouped.iterrows():
#     org_name = row['org_name_cn']
#     asn_count = row['asn']
#     print(f"{org_name}: {asn_count}")
#     count += 1
#     if count >= 1000:
#         break