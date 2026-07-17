import itertools
import sys
import os
import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from database.leak_event import get_leak_id, leak_event_record
from database.leak_phenomenon import get_leak_phenomenon_id
from database.event import event_start, event_end

from utils.get_as_info import (get_as_name,
                               get_as_org_name, 
                               get_as_org_name_en,
                               get_as_country_cn,
                               get_as_descr,
                               get_as_admin,
                               )
from utils.get_other_info import get_leak_triplet
from utils.utilitys import get_as_info, get_duration

from config.config import SOURCE
from config.logger import leak_logger

class BGPLeak:
 
    """BGP Leak Class

        prefix_dict = {
            prefix: {
                phenomenon_id,
                event_id,
                last_leak_time
            },
            ...
        }_
        
        phenomenon_dict = {
            prefix: {
                leak_phenomenon_id: {
                    s_timestamp,
                    s_time,
                    leak_by,
                    leak_by_country,
                    leak_to,
                    leak_to_country,
                    leak_vp,
                    as_path,
                    prefix_ori_as,
                    ori_as_country,
                    leak_level,
                    leak_level_info,
                },
            },
            ...
        }

        prefix_path_dict = {
            prefix: as_path_set(),
            ...
        }
        
    """
    def __init__(self, conn, bgp_info, bgp_rib):
        self.conn = conn
        self.bgp_info = bgp_info
        self.bgp_rib = bgp_rib

        self.leak_phenomenon_table = None
        self.leak_event_table = None
        self.event_table = None

        self.prefix_event = dict()
        self.prefix_path_dict = dict()

        self.phenomenon_dict = dict()
        self.phenomenon_init_id = dict()
        self.event_init_id = dict()


    def init_id(self):
        """
        如果数据表中已经存在数据，重新启动程序时应读取并记录数据表中的事件id，防止主键重复
        :return:
        """
        leak_phenomenon_rows = get_leak_phenomenon_id(self.conn, self.leak_phenomenon_table)
        leak_event_rows = get_leak_id(self.conn, self.leak_event_table)

        for row in leak_phenomenon_rows:
            if row['source'] == SOURCE:
                self.phenomenon_init_id.setdefault(row['prefix'], row['max'])
        for row in leak_event_rows:
            if row['source'] == SOURCE:
                self.event_init_id.setdefault(row['prefix'], row['max'])

    def init_leak(self):
        self.init_id()

    def get_table(self):
        return self.leak_phenomenon_table, self.leak_event_table, self.event_table

    def set_table(self, leak_phenomenon_table, leak_event_table, event_table):
        self.leak_phenomenon_table = leak_phenomenon_table
        self.leak_event_table = leak_event_table
        self.event_table = event_table
        
    def get_as_rel(self, as1, as2):
        # return: -1 as1 - as2为单一的provider-custmoer
        ##### 首先排除对等关系
        if self.bgp_info.as_rel_dict.get(as1) is not None:
            if self.bgp_info.as_rel_dict[as1].get('peers') is not None:
                if as2 in self.bgp_info.as_rel_dict[as1]['peers']:
                    return 0
        if self.bgp_info.as_rel_dict.get(as2) is not None:
            if self.bgp_info.as_rel_dict[as2].get('peers') is not None:
                if as1 in self.bgp_info.as_rel_dict[as2]['peers']:
                    return 0

        #### 其次排除cp关系
        if self.bgp_info.as_rel_dict.get(as1) is not None:
            if self.bgp_info.as_rel_dict[as1].get('provider') is not None:
                if as2 in self.bgp_info.as_rel_dict[as1]['provider']:
                    return 1
        if self.bgp_info.as_rel_dict.get(as2) is not None:
            if self.bgp_info.as_rel_dict[as2].get('customer') is not None:
                if as1 in self.bgp_info.as_rel_dict[as2]['customer']:
                    return 1

        ### 最后确定是否为pc关系
        if self.bgp_info.as_rel_dict.get(as1) is not None:
            if self.bgp_info.as_rel_dict[as1].get('customer') is not None:
                if as2 in self.bgp_info.as_rel_dict[as1]['customer']:
                    return -1
        if self.bgp_info.as_rel_dict.get(as2) is not None:
            if self.bgp_info.as_rel_dict[as2].get('provider') is not None:
                if as1 in self.bgp_info.as_rel_dict[as2]['provider']:
                    return -1
        return -2

    def leak_level(self, prefix, asn):
        descr_info = '这仅仅是一个可能的路由泄漏事件'
        level = 'low'

        if self.bgp_info.prefix_info.get(prefix) is not None:
            num = self.bgp_info.prefix_info[prefix]['domain_num']
            auth_num = self.bgp_info.prefix_info[prefix]['domain_auth_num']
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
            if self.bgp_info.prefix_info[prefix]['domain'] not in [None, '']:
                domain.extend(eval(self.bgp_info.prefix_info[prefix]['domain']))
            if self.bgp_info.prefix_info[prefix]['domain_auth'] not in [None, '']:
                domain.extend(eval(self.bgp_info.prefix_info[prefix]['domain_auth']))

            for d in domain:
                if self.bgp_info.important_domain_dict.get(d) is not None:
                    level = 'high'
                    
                    try:
                        descr_info = descr_info + '在前缀中有' + self.bgp_info.important_domain_dict.get('name', '重要') + '的网站 。'
                    except:
                        descr_info = descr_info + '在前缀中有重要网站'
        try:
            if self.bgp_info.important_as_dict.get(int(asn)) is not None:
                level = 'high'
                if descr_info != '':
                    descr_info = descr_info + " 并且 " + asn + " 是 Cloud|IDC|CDN 或者 顶级内容提供商 (top content provider)。"

        except:
            pass
        return level, descr_info
    
    def is_leak_event(self, leak_by, leak_to):
        if leak_by == leak_to:
            return 0, 'by equal to'


        # 未知组织过滤
        leak_by_org = get_as_org_name(self.bgp_info.as_info, leak_by)
        leak_to_org = get_as_org_name(self.bgp_info.as_info, leak_to)
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
        if leak_by in self.bgp_info.as_info.keys():
            leak_by_import = self.bgp_info.as_info[leak_by]['import_as']
            leak_by_export = self.bgp_info.as_info[leak_by]['export_as']
            if leak_to_str in leak_by_import:
                return 0, leak_by + ' import filter ' + leak_to
            if leak_to_str in leak_by_export:
                return 0, leak_by + ' export filter ' + leak_to
        if leak_to in self.bgp_info.as_info.keys():
            leak_to_import = self.bgp_info.as_info[leak_to]['import_as']
            leak_to_export = self.bgp_info.as_info[leak_to]['export_as']
            if leak_by_str in leak_to_import:
                return 0, leak_to + ' import filter ' + leak_by
            if leak_by_str in leak_to_export:
                return 0, leak_to + ' export filter ' + leak_by  

        return 1, 'possible leak'

    def leak_detect(self, t, flag, prefix, vp, as_path):
        """泄露检测
        Args:
            t (str): 时间戳
            flag (bool): 是否为新pr efix
            prefix (str): 前缀
            vp (str): vp
            as_path (str): as_path
            old_origin_set (set): 旧的origin set
            old_vp_paths (set): 旧的vp paths
        """
        if flag == 'A':
            if prefix not in self.prefix_event.keys():
                self.prefix_event[prefix] = dict()
                self.prefix_event[prefix]['last_leak_time'] = ""
                if prefix in self.phenomenon_init_id:
                    self.prefix_event[prefix]['phenomenon_id'] = self.phenomenon_init_id[prefix]
                else:
                    self.prefix_event[prefix]['phenomenon_id'] = 0
                if prefix in self.event_init_id:
                    self.prefix_event[prefix]['event_id'] = self.event_init_id[prefix]
                else:
                    self.prefix_event[prefix]['event_id'] = 0

            self.check_leak(t, vp, prefix, as_path)   

    def check_leak(self, t, vp, prefix, vp_path):
        """检测一条as_path是否存在泄露
        Args:
            t(str): 时间
            vp(str): vp
            prefix(str): 前缀
            vp_path(str): as_path
        """
        if '{' in vp_path:
            return
        vp_path_fields_o = vp_path.split(' ')
        vp_path_fields = [k for k, g in itertools.groupby(vp_path_fields_o)]  # 去除相邻的重复AS

        # 如果 prefix 已经检测出了泄露则跳过
        if prefix in self.prefix_path_dict and self.prefix_path_dict[prefix] is True:
            return

        else:
            ori_asn = vp_path_fields[-1]
            length = len(vp_path_fields)

            if length > 3:
                for i in range(length - 1, -1, -1):
                    if i - 2 == -1:
                        break
                    if self.get_as_rel(vp_path_fields[i], vp_path_fields[i - 1]) == -1 and self.get_as_rel(vp_path_fields[i - 2], vp_path_fields[i - 1]) == -1:
                        if get_leak_triplet(self.bgp_info.triplet_info, vp_path_fields[i], vp_path_fields[i - 1], vp_path_fields[i - 2]) >= 0.2:
                            # leak_logger.info("检测出三元组:{}, {}, {}, 稳定度为：{}".format(vp_path_fields[i], vp_path_fields[i - 1], vp_path_fields[i - 2], get_leak_triplet(self.triplet_info, vp_path_fields[i], vp_path_fields[i - 1], vp_path_fields[i - 2])))
                            continue
                        # 出现provider -- customer -- provider的情况
                        # 根据该prefix上一次的泄露时间，判断是否需要重置id
                        if self.prefix_event[prefix]['last_leak_time'] != "":
                            last_leak_time = self.prefix_event[prefix]['last_leak_time']
                            last_leak_time = datetime.datetime.strptime(last_leak_time, "%Y-%m-%d %H:%M:%S")
                            start_time = datetime.datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
                            # 如果last_leak_time与start_time的月份不一致，则重置id
                            if last_leak_time.month != start_time.month:
                                self.prefix_event[prefix]['phenomenon_id'] = 0
                                self.prefix_event[prefix]['event_id'] = 0

                        # 记录此次泄露的时间
                        self.prefix_event[prefix]['last_leak_time'] = t
                        # 泄露事件评级
                        level, level_info = self.leak_level(prefix, ori_asn)

                        # 记录前缀泄露开始
                        self.prefix_event[prefix]['phenomenon_id'] += 1
                        if level == 'high' or level == 'middle':
                            self.prefix_event[prefix]['event_id'] += 1
                        phenomenon_id = self.prefix_event[prefix]['phenomenon_id']
                        self.phenomenon_dict.setdefault(prefix, dict()).setdefault(phenomenon_id, dict())
                        
                        self.phenomenon_dict[prefix][phenomenon_id]['s_time'] = datetime.datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
                         
                        leak_by = vp_path_fields[i - 1]
                        self.phenomenon_dict[prefix][phenomenon_id]['leak_by'] = leak_by
                        # leak_by_country = get_as_country(self.as_info, leak_by)
                        self.phenomenon_dict[prefix][phenomenon_id]['leak_by_country'] = get_as_country_cn(self.bgp_info.as_info, leak_by)
                        self.phenomenon_dict[prefix][phenomenon_id]['leak_by_name'] = get_as_name(self.bgp_info.as_info, leak_by)
                        self.phenomenon_dict[prefix][phenomenon_id]['leak_by_org'] = get_as_org_name(self.bgp_info.as_info, leak_by)
                        self.phenomenon_dict[prefix][phenomenon_id]['leak_by_descr'] = get_as_descr(self.bgp_info.as_info, leak_by)
                        self.phenomenon_dict[prefix][phenomenon_id]['leak_by_admin'] = get_as_admin(self.bgp_info.as_info, leak_by)

                        leak_to = vp_path_fields[i - 2]
                        self.phenomenon_dict[prefix][phenomenon_id]['leak_to'] = leak_to
                        # leak_to_country = get_as_country(self.as_info, leak_to)
                        self.phenomenon_dict[prefix][phenomenon_id]['leak_to_country'] = get_as_country_cn(self.bgp_info.as_info, leak_to)
                        self.phenomenon_dict[prefix][phenomenon_id]['leak_to_name'] = get_as_name(self.bgp_info.as_info, leak_to)
                        self.phenomenon_dict[prefix][phenomenon_id]['leak_to_org'] = get_as_org_name(self.bgp_info.as_info, leak_to)
                        self.phenomenon_dict[prefix][phenomenon_id]['leak_to_descr'] = get_as_descr(self.bgp_info.as_info, leak_to)
                        self.phenomenon_dict[prefix][phenomenon_id]['leak_to_admin'] = get_as_admin(self.bgp_info.as_info, leak_to)

                        self.phenomenon_dict[prefix][phenomenon_id]['leak_vp'] = vp
                        self.phenomenon_dict[prefix][phenomenon_id]['as_path'] = vp_path
                        self.phenomenon_dict[prefix][phenomenon_id]['prefix_ori_as'] = ori_asn
                        self.phenomenon_dict[prefix][phenomenon_id]['ori_as_org'] = get_as_org_name(self.bgp_info.as_info, ori_asn)
                        self.phenomenon_dict[prefix][phenomenon_id]['ori_as_country'] = get_as_country_cn(self.bgp_info.as_info, ori_asn)
                        self.phenomenon_dict[prefix][phenomenon_id]['ori_as_name'] = get_as_name(self.bgp_info.as_info, ori_asn)
                        self.phenomenon_dict[prefix][phenomenon_id]['ori_as_descr'] = get_as_descr(self.bgp_info.as_info, ori_asn)
                        self.phenomenon_dict[prefix][phenomenon_id]['ori_as_admin'] = get_as_admin(self.bgp_info.as_info, ori_asn)
                        self.phenomenon_dict[prefix][phenomenon_id]['leak_level'] = level
                        self.phenomenon_dict[prefix][phenomenon_id]['leak_level_info'] = level_info

                        leak_by = self.phenomenon_dict[prefix][phenomenon_id]['leak_by']
                        leak_by_name = self.phenomenon_dict[prefix][phenomenon_id]['leak_by_name']
                        leak_by_org = self.phenomenon_dict[prefix][phenomenon_id]['leak_by_org']
                        leak_by_country = self.phenomenon_dict[prefix][phenomenon_id]['leak_by_country']
                        leak_to = self.phenomenon_dict[prefix][phenomenon_id]['leak_to']
                        leak_to_name = self.phenomenon_dict[prefix][phenomenon_id]['leak_to_name']
                        leak_to_org = self.phenomenon_dict[prefix][phenomenon_id]['leak_to_org']
                        leak_to_country = self.phenomenon_dict[prefix][phenomenon_id]['leak_to_country']
                        s_time = self.phenomenon_dict[prefix][phenomenon_id]['s_time']
                        prefix_ori_as = self.phenomenon_dict[prefix][phenomenon_id]['prefix_ori_as']
                        ori_as_org = self.phenomenon_dict[prefix][phenomenon_id]['ori_as_org']
                        ori_as_name = self.phenomenon_dict[prefix][phenomenon_id]['ori_as_name']
                        ori_as_country = self.phenomenon_dict[prefix][phenomenon_id]['ori_as_country']
                        event_info = "北京时间 {} , 归属于 {} 的前缀 {} 被 {} 泄漏给 {}。".format(s_time, 
                                        get_as_info(prefix_ori_as, ori_as_name, ori_as_country), prefix, 
                                        get_as_info(leak_by, leak_by_name, leak_by_country),
                                        get_as_info(leak_to, leak_to_name, leak_to_country))
                        self.phenomenon_dict[prefix][phenomenon_id]['event_info'] = event_info

                        # 避免重复检测
                        self.prefix_path_dict[prefix] = True

                        # 事件入现象库
                        # leak_phenomenon_record(self.conn, self.phenomenon_dict, prefix, phenomenon_id, self.leak_phenomenon_table)

                        # 事件入事件库
                        if level == 'middle' or level == 'high':
                            is_leak, filter_reason = self.is_leak_event(leak_by=leak_by, leak_to=leak_to)
                            
                            if is_leak: 
                                # 事件计入路由泄漏记录表
                                event_id = self.prefix_event[prefix]['event_id']
                                leak_event_record(self.conn, self.phenomenon_dict, SOURCE, prefix, phenomenon_id, event_id, self.leak_event_table, 
                                            is_leak, filter_reason)
                                leak_logger.info("写入路由泄漏记录表成功")
                             

                                # 事件计入事件总表
                                detail_url = '{}/{}/{}/{}/{}'.format('leak', s_time, prefix.replace('/', '-'), event_id, SOURCE)
                                org_name = leak_by_org
                                if (leak_by_country in ['中国']) or (leak_to_country in ['中国']):
                                    state = 'judge'
                                    is_domestic = True
                                else:
                                    state = 'abroad'
                                    is_domestic = False
                                attacker_as = 'AS{}\n({})'.format(leak_by, leak_by_name) if leak_by_name else 'AS{}'.format(leak_by)
                                attacked_as = 'AS{}\n({})'.format(prefix_ori_as, ori_as_name) if ori_as_name else 'AS{}'.format(prefix_ori_as)
                                attacker_org = leak_by_org
                                attacked_org = ori_as_org
                                attacker_country = leak_by_country
                                attacked_country = ori_as_country
                                event_start(
                                            source=SOURCE,
                                            event_type='路由泄漏', 
                                            level=self.phenomenon_dict[prefix][phenomenon_id]['leak_level'], 
                                            s_time=s_time, 
                                            e_time=None, 
                                            duration=None,
                                            attacker_as=attacker_as, 
                                            attacked_as=attacked_as, 
                                            affected_prefix=prefix, 
                                            event_info=event_info, 
                                            detail_url=detail_url,
                                            attacker_org=attacker_org, 
                                            attacked_org=attacked_org,
                                            attacker_country=attacker_country,
                                            attacked_country=attacked_country, 
                                            state=state, 
                                            is_domestic=is_domestic,
                                            conn=self.conn, 
                                            table=self.event_table)
                                leak_logger.info("写入时间总表成功")


                        # 将此次记录从内存中删除
                        if prefix in self.prefix_path_dict and phenomenon_id in self.phenomenon_dict[prefix]:
                            del self.phenomenon_dict[prefix][phenomenon_id]
   