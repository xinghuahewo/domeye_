"""
日志配置文件
"""
import os
import logging

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.config import BASE_DIR
from datetime import datetime
import time


LOG_DIR_ENV = 'DOMEYE_LOG_DIR'


def resolve_log_dir(environ=None):
    """允许隔离开发实例覆盖日志目录；生产未配置时保持原路径。"""

    active_environ = os.environ if environ is None else environ
    configured = active_environ.get(LOG_DIR_ENV, '').strip()
    if not configured:
        return os.path.join(BASE_DIR, 'logs')
    if not os.path.isabs(configured):
        raise RuntimeError(f'{LOG_DIR_ENV} 必须是绝对路径')
    return os.path.normpath(configured)


# 日志文件路径
LOG_DIR = resolve_log_dir()
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, 'bgp_log')
OUTAGE_LOG_PATH = os.path.join(LOG_DIR, 'outage_log')
PRIVATE_OUTAGE_LOG_PATH = os.path.join(LOG_DIR, 'private_outage_log')
HIJACK_LOG_PATH = os.path.join(LOG_DIR, 'hijack_log')
SUB_HIJACK_LOG_PATH = os.path.join(LOG_DIR, 'sub_hijack_log')
LEAK_LOG_PATH = os.path.join(LOG_DIR, 'leak_log')
FEATURE_LOG_PATH = os.path.join(LOG_DIR, 'feature_log')
BOUNDARY_OUTAGE_LOG_PATH = os.path.join(LOG_DIR, 'boundary_outage_log')
BOUNDARY_LOG_PATH = os.path.join(LOG_DIR, 'boundary_log')
CONNECTION_LOG_PATH = os.path.join(LOG_DIR, 'connection_log')
PREFIX_COUNT_LOG_PATH = os.path.join(LOG_DIR, 'prefix_count_log')
SECURITY_SCREEN_LOG_PATH = os.path.join(LOG_DIR, 'security_screen_log')
DATABASE_LOG_PATH = os.path.join(LOG_DIR, 'database_log')


def log_set(log_path, style):
    logger = logging.getLogger(style)
    logger.setLevel(level=logging.INFO)
    handler = logging.FileHandler(log_path, encoding='UTF-8')
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        '%(asctime)s - %(filename)s[line:%(lineno)d] - <%(threadName)s %(thread)d>' +
        '- <Process %(process)d> - %(levelname)s: %(message)s'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


outage_logger = log_set(OUTAGE_LOG_PATH, 'outage')
private_outage_logger = log_set(PRIVATE_OUTAGE_LOG_PATH, 'private_outage')
hijack_logger = log_set(HIJACK_LOG_PATH, 'hijack')
sub_hijack_logger = log_set(SUB_HIJACK_LOG_PATH, 'sub_hijack')
leak_logger = log_set(LEAK_LOG_PATH, 'leak')
feature_logger = log_set(FEATURE_LOG_PATH, 'feature')
boundary_outage_logger = log_set(BOUNDARY_OUTAGE_LOG_PATH, 'boundary_outage')
boundary_logger = log_set(BOUNDARY_LOG_PATH, 'boundary')
connection_logger = log_set(CONNECTION_LOG_PATH, 'connection')
prefix_count_logger = log_set(PREFIX_COUNT_LOG_PATH, 'prefix_count')
security_screen_logger = log_set(SECURITY_SCREEN_LOG_PATH, 'security_screen')
database_logger = log_set(DATABASE_LOG_PATH, 'database')



def init_logger(logger_name='BGP_System_Log', log_file_head='bgp', console_level=logging.INFO, file_level=logging.INFO, default_level=logging.DEBUG):
    '''
    初始化日志模块

    Parameters:

        - logger_name: logger的名字，用于区分不同的 logger

        - log_file_head: 日志文件名的前缀

        - console_level: 控制台日志的级别，一般为 WARNING

        - file_level: 日志文件的级别，一般为 INFO

        - default_level: 默认日志级别，一般为 DEBUG

    Returns:

        - logger: logger实例

    '''

    # 返回一个logger实例，如果没有指定name，返回root logger。
    # 只要name相同，返回的logger实例都是同一个而且只有一个，即name和logger实例是一一对应的。
    # 这意味着，无需把logger实例在各个模块中传递。只要知道name，就能得到同一个logger实例。
    logger = logging.getLogger(logger_name)
    # 
    if logger.handlers:
        logger.handlers.clear()
    
    logger.propagate = False
    # 设置总日志级别, 也可以给不同的handler设置不同的日志级别
    # 设置logger的level， level有以下几个级别：
    # 级别高低顺序：NOTSET < DEBUG < INFO < WARNING < ERROR < CRITICAL
    # 如果把looger的级别设置为INFO， 那么小于INFO级别的日志都不输出， 大于等于INFO级别的日志都输出　
    logger.setLevel(default_level)

    # 控制台日志和日志文件使用同一个formatter,formatter用于描述日志的格式
    formatter = logging.Formatter(
        '%(asctime)s - %(filename)s[line:%(lineno)d] - <%(threadName)s %(thread)d>' +
        '- <Process %(process)d> - %(levelname)s: %(message)s'
    )
    # asctime:日志产生的时间；filename:产生日志的脚本文件名；lineno:该脚本文件哪一行代码产生了日志
    # threadName: 当前线程名；thread: 当前进程名；Process进程同thread线程
    # levelname: logger的级别；meesage: 具体的日志信息

    cur_time = time.strftime('%Y-%m-%d', time.localtime(time.time()))
    filename = f'{log_file_head}-{cur_time}-{os.getpid()}.log'  # 日志文件名，以当前时间命名

    # 创建 Handler, 输出日志到控制台和文件
    # 日志文件 FileHandler
    file_handler = logging.FileHandler(os.path.join(LOG_DIR, filename))  # 创建日志文件 handler
    file_handler.setFormatter(formatter)  # 设置 Formatter
    file_handler.setLevel(file_level)  # 单独设置日志文件的日志级别

    # 控制台日志 StreamHandler
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(console_level)  # 单独设置控制台日志的日志级别，注释掉则使用总日志级别

    # 将 handler 添加到 logger 中

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

    return logger
