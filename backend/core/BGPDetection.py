import datetime
import os.path
import sys
import time
import traceback
import pyinotify


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

from database.utils import get_conn
from database.moas import create_moas_table
from database.hijack import create_hijack_table
from database.sub_hijack import create_sub_hijack_table
from database.leak_event import create_leak_event_table
from database.leak_phenomenon import create_leak_phenomenon_table
from database.event import create_event_table
from database.prefix_outage import create_prefix_outage_table
from database.as_outage import create_as_outage_table
from database.country_outage import create_country_outage_table
from database.utils import if_table_exist

from database.feature_asn import create_feature_asn_table
from database.feature_country import create_feature_country_table

from core.BGPInfo import BGPInfo
from core.BGPRib import BGPRib
from core.BGPOutage import BGPOutage
from core.BGPFeature import BGPFeature

from core.BGPLeak import BGPLeak
from core.BGPHijack import BGPHijack
from core.BGPSubHijack import BGPSubHijack



from config.logger import init_logger
logger = init_logger()
from config.database import DATABASE, USER, HOST, PORT, PASSWORD
from config.config import (MODE, SOURCE, BASE_DATA_PATH, RIB_HISTORY_FILE, 
                           DETECTION_DUMP_DIR, BIG_COUNTRY, 
                           FEATURE_COUNTRY_TABLE, FEATURE_OTHER_TABLE,
                           FEATURE_ASN_MONTHLY_ENABLED)


class BGPDetection:
    '''
    BGPDetection类是 BGP 检测的核心类，负责处理 BGP 数据流和检测事件。
    '''
    def __init__(self, rib_file, conn):
        self.conn = conn
        self.rib_file = rib_file

        self.country = {}
        self.country_file = ""

        
        self.bgp_info = BGPInfo()
        
        self.bgp_rib = BGPRib(self.bgp_info, self.rib_file)
        ### 测试完成
        # self.feature_detect = BGPFeature(self.conn, self.bgp_info, self.bgp_rib)
        self.outage_detect = BGPOutage(self.conn, self.bgp_info, self.bgp_rib)
        self.hijack_detect = BGPHijack(self.conn, self.bgp_info, self.bgp_rib)
        self.sub_hijack_detect = BGPSubHijack(self.conn, self.bgp_info, self.bgp_rib)
        self.leak_detect = BGPLeak(self.conn, self.bgp_info, self.bgp_rib)


    def init_detection(self):
        """初始化检测"""   
        self.outage_detect.init_outage(self.bgp_rib.prefix_dict)
        # self.feature_detect.init_feature()
        self.hijack_detect.init_hijack(self.bgp_rib.prefix_dict)
        self.sub_hijack_detect.init_sub_hijack(self.bgp_rib.prefix_dict)
        self.leak_detect.init_leak()

    def get_table_name(self, type_):
        
        if type_ == "hijack":
            return (
                self.hijack_detect.moas_table,
                self.hijack_detect.hijack_table,
                self.hijack_detect.event_table,
            )
        elif type_ == "leak":
            return (
                self.leak_detect.leak_phenomenon_table,
                self.leak_detect.leak_event_table,
                self.leak_detect.event_table,
            )
        elif type_ == "outage":
            return (
                self.outage_detect.prefix_table,
                self.outage_detect.as_table,
                self.outage_detect.country_table,
                self.outage_detect.event_table,
            )
        elif type_ == 'sub_hijack':
            return (
                self.sub_hijack_detect.sub_hijack_table,
                self.sub_hijack_detect.event_table,
            )
        return None

    def set_table_name(self, type_, *args):
        if type_ == "hijack":
            (
                self.hijack_detect.moas_table,
                self.hijack_detect.hijack_table,
                self.hijack_detect.event_table,
            ) = args
        elif type_ == "leak":
            (
                self.leak_detect.leak_phenomenon_table,
                self.leak_detect.leak_event_table,
                self.leak_detect.event_table,
            ) = args
        elif type_ == "outage":
            (
                self.outage_detect.prefix_table,
                self.outage_detect.as_table,
                self.outage_detect.country_table,
                self.outage_detect.event_table,
            ) = args
        elif type_ == 'sub_hijack':
            (
                self.sub_hijack_detect.sub_hijack_table,
                self.sub_hijack_detect.event_table,
            ) = args
        # elif type_ == 'feature':
        #     (
        #         self.outage_detect.feature_table,
        #     ) = args


    def update_checking(self, updates_file):
        """收到更新报文时，更新劫持信息.

        Args:
        ----
            updates_file (str): 更新报文路径。

        """
        t = ""
        # 注意一个问题 在中心 文件名字是北京时间 但是在ripe 文件名字是utc时间
        # file_time = file_to_time(updates_file) 
        # if SOURCE == 'r':
        #     file_time = file_time + datetime.timedelta(hours=8)
        try:
            logger.info(f"开始处理update文件: {updates_file}")
            with open(updates_file, 'r') as f:
                for update_message in f:
                    try:
                        fields = update_message.strip().split('|')
                        timestamp, flag, vp, prefix = int(fields[1]), fields[2], fields[4], fields[5]
                        if flag == 'STATE' or prefix == '0.0.0.0/0' or prefix == '::/0':
                            continue
                        if flag == 'A':  # 宣告路由加油as_path
                            as_path = fields[6]
                            # 不考虑AS集合的情况（路由聚合）
                            if '{' in as_path:
                                # 包含AS集合，跳过
                                continue
                        else:
                            as_path = ''

                        ### T时区问题  转成utc时间 + 8h
                        t = datetime.datetime.fromtimestamp(int(timestamp) + 28800, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

                        old_origin_set = self.bgp_rib.prefix_as.get(prefix, set()).copy()
                        old_vp_paths = self.bgp_rib.prefix_dict.get(prefix, {}).copy()
                        # TODO: 72s
                        # logger.info("update data struct for prefix {}".format(prefix))
                        self.bgp_rib.update_rib(flag, prefix, vp, as_path, old_origin_set)
                        # logger.info("update data struct for prefix {} done".format(prefix))
                        
                        origin_set = self.bgp_rib.prefix_as.get(prefix, set()).copy()
                        new_vp_paths = self.bgp_rib.prefix_dict.get(prefix, dict()).copy()
                        
                        # logger.info("start to detect hijacking for prefix {}".format(prefix))
                        self.hijack_detect.hijack_detect(t, prefix, old_origin_set, origin_set, new_vp_paths)
                        # logger.info("detect hijacking for prefix {} done".format(prefix))
                        self.sub_hijack_detect.sub_hijack_detect(t, prefix)
                        
                        # logger.info("start to detect leaking for prefix {}".format(prefix))
                        self.leak_detect.leak_detect(t, flag, prefix, vp, as_path)
                        # logger.info("detect leaking for prefix {} done".format(prefix))


                        # self.feature_detect.feature_detect(t, flag, prefix, old_origin_set)

                        # 如果在前缀树中存在父前缀 即认为是细路由 不进行中断判断
                        # parent = self.bgp_rib.has_parent_prefix(prefix)
                        # if not parent:
                        # logger.info("start to detect outage for prefix {}".format(prefix))
                        self.outage_detect.outage_detect(t, flag, prefix, vp, as_path, old_origin_set, old_vp_paths, origin_set, new_vp_paths)
                        # logger.info("detect outage for prefix {} done".format(prefix))
                    except Exception as e:
                        logger.error(f"Error in processing update message at time {t}: {e}")
                        logger.error(f"update_message: {update_message}")
                        logger.error(f"Error: {traceback.format_exc()}")
                        continue


            # 每次读取一个update文件后，更新中断事件表
            logger.info("start to update outage event table")
            self.outage_detect.update_outage_event_table()
            logger.info("update outage event table done")
            # 每次读取一个update文件后，将更新的特征表加入到数据库中
            # self.feature_detect.insert_to_db(file_time)
        except Exception as e:
            logger.error(f"Error in processing update file {updates_file} at time {t}: {e}")
            logger.error(f"Error: {traceback.format_exc()}")




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
                logger.info(f"检测到新文件 {full_file_path}, 添加到待处理队列。")


def __main_ripe():
    """detection Ripe data ."""
    global Data_Path, Data_Path_Last_month, Current_Updates_File, Last_File_Name, Updates_File_Queue

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
            logger.info(f"rib文件路径: {rib_file}")
            rib_file_path = os.path.dirname(rib_file)
            rib_file_basename = os.path.basename(rib_file)
            ### XXX: update的文件名对应的时间是这个update文件中第一个update报文的时间
            Current_Updates_File = rib_file_path + '/' + get_current_file_name_from_rib(rib_file_basename)
            logger.info("当前需要update文件为: {}".format(Current_Updates_File))
            update_list = get_updates_file_list(base_data_path=BASE_DATA_PATH, rib_file=rib_file_basename)
            Updates_File_Queue.extend(update_list)
            Updates_File_Queue.sort()
            logger.info(
                f"获取Updates文件列表成功，FIRST FILE：{update_list[0]}，LAST_FILE：{update_list[-1]}, number of files: {len(update_list)}"
            )
        else:
            rib_file_path = get_rib_path(BASE_DATA_PATH, RIB_HISTORY_FILE)
            logger.info(f"rib文件路径: {rib_file_path}")
            rib_file = os.path.join(rib_file_path, RIB_HISTORY_FILE)
            Current_Updates_File = rib_file_path + '/' + get_current_file_name_from_rib(rib_file)
            update_list = get_updates_file_list(base_data_path=BASE_DATA_PATH, rib_file=RIB_HISTORY_FILE)
            Updates_File_Queue.extend(update_list)
            Updates_File_Queue.sort()
            logger.info(
                f"获取Updates文件列表成功，FIRST FILE：{update_list[0]}，LAST_FILE：{update_list[-1]}, number of files: {len(update_list)}"
            )

        # 判断rib文件是否合法
        if rib_file == "":
            logger.error("没有找到合适的rib文件, 程序结束")
            sys.exit(-1)

        # 解压rib文件
        start = time.time()
        # XXX
        rib_file = dump_file(rib_file, DETECTION_DUMP_DIR)
        # rib_file = "/home/bgpdata/Domeye/data/detection/bview.20250612.0000.gz.data.simple"
        end = time.time()
        logger.info(f"rib文件解压耗时: {end - start}秒")

        # 获得数据库连接
        conn = get_conn(database=DATABASE, user=USER, password=PASSWORD, host=HOST, port=PORT)

        #### XXX
        create_tables(conn)  # 创建必要的表

        # 创建HijackDetect对象
        anomaly_detect = BGPDetection(
            rib_file=rib_file,
            conn=conn,
        )

        is_init = False
        # count = 0
        # is_handle_file = False

        while True:
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
                        logger.info("监测目录更新成功")
                    except:
                        logger.error("监测目录更新失败")

            # 如果文件队列不为空，且当前需要处理的文件为队列中的第一个文件，则处理该文件 否则等待
            while len(Updates_File_Queue) > 0:
                # is_handle_file = True
                updates_file = Updates_File_Queue.pop(0)
                # 根据updates文件的文件名确定当前数据表的名称
                # XXX
                set_tablename(conn, anomaly_detect, updates_file)
                if is_init is False:
                    anomaly_detect.init_detection()
                    is_init = True
                start = int(time.time())
                logger.info(f"Handling file: {updates_file}")
                # 解压一个update文件所需要的时间5s以内
                updates_file = dump_file(updates_file, DETECTION_DUMP_DIR)
                anomaly_detect.update_checking(updates_file=updates_file)
                remove_file(updates_file)
                end = int(time.time())
                logger.info(f"update文件{updates_file}处理完毕，耗时{end - start}秒")
                Last_File_Name = Current_Updates_File
                Current_Updates_File_basename = get_current_file_name_from_updates(Last_File_Name)
                Current_Updates_File = get_update_file_abspath(Current_Updates_File_basename)
                logger.info(f"当前需要updates文件为{Current_Updates_File}")


            logger.info("waiting new file, sleep 30s...")
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt  Ctrl+C 中断")
        sys.exit(-1)
    except Exception as e:
        logger.error(f"Error: {e}")
        logger.error(f"Error: {traceback.format_exc()}")
        sys.exit(-1)
    finally:
        try:
            if 'notifier' in locals():
                notifier.stop()
            if 'conn' in locals():
                conn.close()
        except:
            pass


def set_tablename(conn, anomaly_detect: BGPDetection, updates_file):
    """set table name for detection according to the time of updates file"""
    file_date = file_to_time(os.path.basename(updates_file))
    # 将时间转换为北京时间
    file_date += datetime.timedelta(hours=8)
    # 根据updates文件的文件名确定数据表名, 设置outage_detect对象的数据表, 若表不存在则创建表
    event_table = get_event_table_name(file_date)
    event_table = event_table


    moas_table = get_moas_table_name(file_date)
    hijack_table = get_hijack_table_name(file_date)
    if anomaly_detect.get_table_name("hijack") != (moas_table, hijack_table, event_table):
        anomaly_detect.set_table_name("hijack", moas_table, hijack_table, event_table)
        if not if_table_exist(conn, moas_table):
            create_moas_table(conn=conn, moas_table=moas_table)
        if not if_table_exist(conn, hijack_table):
            create_hijack_table(conn=conn, hijack_table=hijack_table)
        if not if_table_exist(conn, event_table):
            create_event_table(conn=conn, event_table=event_table)

    leak_phenomenon_table = get_leak_phenomenon_table_name(file_date)
    leak_event_table = get_leak_event_table_name(file_date)
    if anomaly_detect.get_table_name("leak") != (leak_phenomenon_table, leak_event_table, event_table):
        anomaly_detect.set_table_name("leak", leak_phenomenon_table, leak_event_table, event_table)
        if not if_table_exist(conn, leak_phenomenon_table):
            create_leak_phenomenon_table(conn=conn, leak_phenomenon_table=leak_phenomenon_table)
        if not if_table_exist(conn, leak_event_table):
            create_leak_event_table(conn=conn, leak_event_table=leak_event_table)

    ## 子劫持
    sub_hijack_table = get_sub_hijack_table_name(file_date)
    if anomaly_detect.get_table_name("sub_hijack") != (sub_hijack_table, event_table):
        anomaly_detect.set_table_name("sub_hijack", sub_hijack_table, event_table)
        if not if_table_exist(conn, sub_hijack_table):
            create_sub_hijack_table(conn=conn, sub_hijack_table=sub_hijack_table)
        if not if_table_exist(conn, event_table):
            create_event_table(conn=conn, event_table=event_table)


    

    ### 中断
    prefix_table, as_table, country_table = get_outage_table_name(file_date)
    prefix_table = prefix_table
    as_table = as_table
    country_table = country_table
    if anomaly_detect.get_table_name("outage") != (prefix_table, as_table, country_table, event_table):
        anomaly_detect.set_table_name(
            "outage",
            prefix_table,
            as_table,
            country_table,
            event_table,
        )
        logger.info(f"set table name: {prefix_table}, {as_table}, {country_table}, {event_table}")
        if not if_table_exist(conn, prefix_table):
            create_prefix_outage_table(conn, prefix_table)
        if not if_table_exist(conn, as_table):
            create_as_outage_table(conn, as_table)
        if not if_table_exist(conn, country_table):
            create_country_outage_table(conn, country_table)
        if not if_table_exist(conn, event_table):
            create_event_table(conn, event_table)


def create_tables(conn):
    """创建固定名称的表"""
    if not if_table_exist(conn, FEATURE_COUNTRY_TABLE):
        create_feature_country_table(conn, FEATURE_COUNTRY_TABLE)
    # ASN 特征表改为按月建表：{base}_{YYYYMM}
    if FEATURE_ASN_MONTHLY_ENABLED:
        month_suffix = datetime.datetime.utcnow().strftime('%Y%m')
        table_name = f"{FEATURE_OTHER_TABLE}_{month_suffix}"
        if not if_table_exist(conn, table_name):
            create_feature_asn_table(conn, table_name)
        for country in BIG_COUNTRY:
            country_en = BIG_COUNTRY.get(country)
            base = f'feature_{country_en}'
            table_name = f"{base}_{month_suffix}"
            if not if_table_exist(conn, table_name):
                create_feature_asn_table(conn, table_name)
    else:
        if not if_table_exist(conn, FEATURE_OTHER_TABLE):
            create_feature_asn_table(conn, FEATURE_OTHER_TABLE)
        for country in BIG_COUNTRY:
            country_en = BIG_COUNTRY.get(country)
            country_table = f'feature_{country_en}'
            if not if_table_exist(conn, country_table):
                create_feature_asn_table(conn, country_table)


if __name__ == "__main__":
    __main_ripe()
