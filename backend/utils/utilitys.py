"""
Utility functions
"""
import os
import datetime
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from config.config import BASE_DATA_PATH

def get_rib_file(base_data_path) -> str:
    # 获取当前的UTC时间
    utc_now = datetime.datetime.utcnow()

    # 获取本月数据路径和上月数据路路径
    data_path, data_path_last_month = get_data_path(base_data_path, utc_now)

    # 如果本月和上月的数据路径都是空，程序终止
    if data_path == "" and data_path_last_month == "":
        return ""

    rib_file = __get_rib_file(data_path, data_path_last_month, utc_now)
    return rib_file


def get_current_file_name_from_rib(rib_file: str) -> str:
    """从rib文件名中获取当前所需的updates文件名
    :param rib_file: rib文件名  ripe：bview.20250101.1600.gz  center: rib.20250101-1600.gz
    :return: 当前所需的updates文件名    ripe: updates.20250101.1600.gz  center: update.20250101-1600.gz
    """
    rib_file_name = os.path.basename(rib_file)
    # 替换 bview 为 updates rib 为 update
    update_file_name = rib_file_name.replace("bview", "updates")
    return update_file_name


def get_current_file_name_from_updates(last_updates_file: str) -> str:
    """从上一个updates文件名中获取当前所需的updates文件 basename
        :param updates_file: updates文件名  ripe: updates.20250101.1600.gz  center: update.20250101-1600
        :return: 当前所需的updates文件名  ripe: updates.20250101.1605.gz  center: update.20250101-1605
    """
    last_updates_file_name = os.path.basename(last_updates_file)
    # print(last_updates_file_name)
    date, time = last_updates_file_name.split(".")[1], last_updates_file_name.split(".")[2]
    last_file_time = datetime.datetime.strptime(f"{date} {time}", "%Y%m%d %H%M")
    next_file_time = last_file_time + datetime.timedelta(minutes=5)
    next_file_time = next_file_time.strftime("%Y%m%d %H%M")
    next_date, next_time = next_file_time.split(" ")[0], next_file_time.split(" ")[1]
    return f'updates.{next_date}.{next_time}.gz'


def get_latest_update_file(data_path: str) -> str:
    """
        data_path: 文件目录
    """
    files = os.listdir(data_path)
    files.sort()
    update_file = os.path.join(data_path, files[-1])

    return update_file


def get_data_path(base_data_path: str, date: datetime) -> tuple:
    """
    根据根数据路径和当前时间，获取本月数据路径和上月数据路径
    如果数据路径不存在，返回空字符串
    """
    year, month = date.year, date.month
    if month == 1:
        data_path = os.path.join(base_data_path, "{}.01".format(year))
        data_path_last_month = os.path.join(base_data_path, "{}.12".format(year - 1))
    elif 1 < month < 10:
        data_path = os.path.join(base_data_path, "{}.{}".format(year, "0" + str(month)))
        data_path_last_month = os.path.join(base_data_path, "{}.{}".format(year, "0" + str(month - 1)))
    elif month == 10:
        data_path = os.path.join(base_data_path, "{}.{}".format(year, str(month)))
        data_path_last_month = os.path.join(base_data_path, "{}.{}".format(year, "09"))
    else:
        data_path = os.path.join(base_data_path, "{}.{}".format(year, str(month)))
        data_path_last_month = os.path.join(base_data_path, "{}.{}".format(year, str(month - 1)))
    if not os.path.exists(data_path):
        data_path = ""
    if not os.path.exists(data_path_last_month):
        data_path_last_month = ""
    return data_path, data_path_last_month


def get_rib_path(base_data_path: str, rib_file_name: str) -> str:
    date = file_to_time(rib_file_name)
    year, month = date.year, date.month
    if 0 < month < 10:
        data_path = os.path.join(base_data_path, "{}.{}".format(year, "0" + str(month)))
    else:
        data_path = os.path.join(base_data_path, "{}.{}".format(year, str(month)))
    if not os.path.exists(data_path):
        data_path = ""
    return data_path


def __get_rib_file(data_path: str, data_path_last_month: str, time_now: datetime) -> str:
    """
    返回rib文件路径，该文件与time_now间隔超过15小时，且距离time_now最近
    如果没找到合适的rib文件，返回空字符串
    """
    if os.path.exists(data_path):
        rib_file_list = []
        for file_name in os.listdir(data_path):
            if file_name.startswith('bview') and file_name.endswith('gz'):
                rib_file_list.append(file_name)
        rib_file_list.sort(reverse=True)
        for file_name in rib_file_list:
            # 判断当前时间是否晚于文件时间 找的是当前存在的最后一个bview文件
            if int((time_now - (file_to_time(file_name))).total_seconds()) >= 0:
                # 此文件与当前时间相差超过15个小时
                print(os.path.getsize(os.path.join(data_path, file_name)))
                if os.path.getsize(os.path.join(data_path, file_name)) > 200 * 1024 * 1024:
                    # 文件大小 > 1G
                    return os.path.join(data_path, file_name)
    if os.path.exists(data_path_last_month):
        rib_file_list = []
        for file_name in os.listdir(data_path_last_month):
            if file_name.startswith('bview') and file_name.endswith('gz'):
                rib_file_list.append(file_name)
        rib_file_list.sort(reverse=True)
        for file_name in rib_file_list:
            if int((time_now - (file_to_time(file_name))).total_seconds()) >= 0:
                # 此文件与当前时间相差超过15个小时
                if os.path.getsize(os.path.join(data_path_last_month, file_name)) > 400 * 1024 * 1024:
                    # 文件大小 > 1G
                    return os.path.join(data_path_last_month, file_name)
    return ""
    



def get_outage_table_name(date: datetime) -> tuple:
    ### 测试
    year = str(date.year)
    month = str(date.month) if date.month > 9 else "0" + str(date.month)
    prefix_outage_table = 'prefix_outage_' + year + month
    as_outage_table = 'as_outage_' + year + month
    country_outage_table = 'country_outage_' + year + month
    return prefix_outage_table, as_outage_table, country_outage_table

def get_moas_table_name(date: datetime) -> str:
    year = str(date.year)
    month = str(date.month) if date.month > 9 else "0" + str(date.month)
    moas_table = 'moas_' + year + month
    return moas_table


def get_hijack_table_name(date: datetime) -> str:
    year = str(date.year)
    month = str(date.month) if date.month > 9 else "0" + str(date.month)
    hijack_table = 'hijack_' + year + month
    return hijack_table

def get_sub_hijack_table_name(date: datetime) -> str:
    year = str(date.year)
    month = str(date.month) if date.month > 9 else "0" + str(date.month)
    sub_hijack_table = 'sub_hijack_' + year + month
    return sub_hijack_table


def get_leak_phenomenon_table_name(date: datetime) -> str:
    year = str(date.year)
    month = str(date.month) if date.month > 9 else "0" + str(date.month)
    leak_phenomenon_table = 'leak_phenomenon_' + year + month
    return leak_phenomenon_table


def get_leak_event_table_name(date: datetime) -> str:
    year = str(date.year)
    month = str(date.month) if date.month > 9 else "0" + str(date.month)
    leak_event_table = 'leak_event_' + year + month
    return leak_event_table


def get_boundary_outage_table_name(date: datetime) -> str:
    year = str(date.year)
    month = str(date.month) if date.month > 9 else "0" + str(date.month)
    boundary_outage_table = 'boundary_outage_' + year + month
    return boundary_outage_table

def get_event_table_name(date: datetime) -> str:
    year = str(date.year)
    month = str(date.month) if date.month > 9 else "0" + str(date.month)
    event_table = 'event_table_' + year + month
    return event_table

def get_boundary_table_name(date: datetime) -> str:
    year = str(date.year)
    month = str(date.month) if date.month > 9 else "0" + str(date.month)
    boundary_table = 'boundary_table_' + year + month
    return boundary_table

def get_connection_table_name(date: datetime) -> str:
    year = str(date.year)
    month = str(date.month) if date.month > 9 else "0" + str(date.month)
    connection_table = 'connection_table_' + year + month
    return connection_table


def get_prefix_count_table_name(date: datetime) -> str:
    year = str(date.year)
    month = str(date.month) if date.month > 9 else "0" + str(date.month)
    prefix_count_table = 'prefix_count_' + year + month
    return prefix_count_table


def get_duration(start_time: str, end_time: str) -> str:
    """
    Return time difference
    :param start_time: start_time, shaped like 2022-04-26 15:14:37
    :param end_time: end_time, shaped like 2022-05-04 00:19:24
    :return: time difference
    """
    s_time_struct = datetime.datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
    e_time_struct = datetime.datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
    total_seconds = (e_time_struct - s_time_struct).total_seconds()
    m, s = divmod(total_seconds, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    d, h, m, s = int(d), int(h), int(m), int(s)
    return "{} days {} hours {} minutes {} seconds".format(d, h, m, s)


def dump_file(file_path: str, dump_dir) -> str:
    """
    Extract the file with bgpdump, returning the full path to the resulting file
    :param file_path: Path of the file to be decompressed
    :param dump_dir: Save the decompressed file directory
    :return: Path to the result file
    """
    """"""
    file_name = os.path.basename(file_path)
    new_file_name = file_name + '.data'
    new_file_path = os.path.join(dump_dir, new_file_name)
    command = "bgpdump -m {} > {}".format(file_path, new_file_path)
    print(command)
    os.system(command=command)
    return new_file_path


def remove_file(file_path: str):
    """
    Delete a file
    :param file_path: The full path of the file to be deleted
    :return: No return value
    """
    command = "rm -f {}".format(file_path)
    os.system(command=command)


def parse_file(file_name: str) -> tuple:
    """
    Get the year, month, day, hour and minute represented by the data file
    :param file_name: file name
    :return: year, month, day, hour, minute
    """

    date, t = file_name.split('.')[1], file_name.split('.')[2]

    year, month, day = date[0:4], date[4:6], date[6:8]
    hour, minute = t[0:2], t[2:4]
    return int(year), int(month), int(day), int(hour), int(minute)


def file_to_time(file_name: str) -> datetime:
    """
    According to the file name, get the time corresponding to the file
    :param file_name: file name
    :return: the time corresponding to the file name
    """
    year, month, day, hour, minute = parse_file(file_name=file_name)
    return datetime.datetime(year=year, month=month, day=day, hour=hour, minute=minute, second=0, tzinfo=None)


def get_update_file_abspath(file_name: str) -> str:
    """根据文件名获取路径"""
    year, month, day, _, _ = parse_file(file_name=file_name)
    if month < 10:
        return os.path.join(BASE_DATA_PATH, f'{year}.0{month}', file_name)
    else:
        return os.path.join(BASE_DATA_PATH, f'{year}.{month}', file_name)
        # return os.path.join(BASE_DATA_PATH, file_name)



def get_as_info(asn, as_name, as_country):
    as_info = "AS {}".format(asn)
    if as_name is None and as_country is None:
        return as_info
    as_info += '('
    if as_name is not None:
        as_info += "{} ".format(as_name)
    if as_country is not None:
        if as_name is not None:
            as_info += ", {} ".format(as_country)
        else:
            as_info += "{} ".format(as_country)
    as_info += ')'
    return as_info


def get_updates_file_list(base_data_path: str, rib_file: str) -> list:
    rib_date = file_to_time(rib_file)
    year, month = rib_date.year, rib_date.month
    if 0 < month < 10:
        rib_folder = "{}.{}".format(year, "0" + str(month))
    else:
        rib_folder = "{}.{}".format(year, str(month))

    folders = [os.path.join(base_data_path, f) for f in os.listdir(base_data_path) if os.path.isdir(os.path.join(base_data_path, f)) and f >= rib_folder]
    folders.sort()
    updates_file_list = list()
    for folder in folders:
        file_list = os.listdir(folder)
        file_list.sort()
        for updates_file in file_list:
            if updates_file.startswith("update"):
                update_date = file_to_time(updates_file)
                if update_date >= rib_date:
                    updates_file = os.path.join(folder, updates_file)
                    updates_file_list.append(updates_file)
    return updates_file_list

