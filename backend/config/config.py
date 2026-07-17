import os
from ast import literal_eval
'''
  - run.py 会先加载 .env 到环境变量
  - 然后 config.py 再从环境变量里读
  - 所以最终程序实际使用的是：
      - .env 里的值
      - 如果 .env 没配，就退回 config.py 默认值
'''

def _get_bool(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'y', 'on')


def _get_int(name, default):
    value = os.environ.get(name)
    if value is None or value == '':
        return default
    return int(value)


def _get_list(name, default):
    value = os.environ.get(name)
    if value is None or value.strip() == '':
        return default
    try:
        parsed = literal_eval(value)
        if isinstance(parsed, list):
            return parsed
    except (ValueError, SyntaxError):
        pass
    return [item.strip() for item in value.split(',') if item.strip()]

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# print(BASE_DIR)
# Flask 应用配置
SECRET_KEY = os.environ.get('SECRET_KEY', 'change-me-in-production')
PORT = _get_int('PORT', 19743)
DEBUG = _get_bool('DEBUG', True)

# 检测数据来源 SOURCE: r表示数据来源于ripe ris, c表示数据来源于中心内部
SOURCE = os.environ.get('SOURCE', 'r')

# 运行模式 MODE: 0为即时检测，1为检测历史数据 RIB_HISTORY_FILE为历史模式下的起始rib文件
MODE = _get_int('MODE', 1)
RIB_HISTORY_FILE = os.environ.get('RIB_HISTORY_FILE', 'bview.20260312.0000.gz')

# 数据文件路径
BASE_DATA_PATH = os.environ.get('BASE_DATA_PATH', '/home/bgpdata/data/ripe/rrc25/')
# BASE_DATA_PATH = r"/home/bgpdata/data/ripe/rrc25/"  # 测试路径

# 路由资源表名
ROUTING_RES_TABLE = 'bgp_routing_resource'
VP_RES_TABLE = os.environ.get('VP_RES_TABLE', 'bgp_vp_resource')
# 边界、连通表名
BOUNDARY_TABLE = 'bgp_boundary'
CONNECTION_TABLE = 'bgp_connection'

# ========== 国家内部拓扑（国家中断页面用）==========
# 无向边表：所有国家放在一张表，边按(min_asn,max_asn)规范化
COUNTRY_TOPOLOGY_EDGE_TABLE = 'country_topology_edge'
# 可选：国家拓扑快照（小国全量图快速返回）
COUNTRY_TOPOLOGY_SNAPSHOT_TABLE = 'country_topology_snapshot'
# 返回全量拓扑的边数阈值（大国默认走subgraph，避免前端卡死）
COUNTRY_TOPOLOGY_FULL_EDGE_THRESHOLD = 50000

# 信息文件路径
INFO_DIR = os.path.join(BASE_DIR, 'info')
AS_INFO_FILE = os.path.join(INFO_DIR, 'as_entity.csv')
AS_INFO_OLD_FILE = os.path.join(INFO_DIR, 'as_dict.txt')
TOP_NX_FILE = os.path.join(INFO_DIR, 'top_nx.csv')
TOP_IP_FILE = os.path.join(INFO_DIR, 'top_ip.txt')
IMPORTANT_AS_FILE = os.path.join(INFO_DIR, 'important_as.csv')
IPV4_ALL_PREFIX_FILE = os.path.join(INFO_DIR, 'ipv4_all_prefix.xls')
IPV6_ALL_PREFIX_FILE = os.path.join(INFO_DIR, 'ipv6_all_prefix.xls')
PFX2AS_DICT_FILE = os.path.join(INFO_DIR, 'pfx2as_dict.txt')
AS_REL_DICT_FILE = os.path.join(INFO_DIR, 'as_rel_dict.txt')
COUNTRY_INFO_FILE = os.path.join(INFO_DIR, 'country.xlsx')
PRIVATE_AS_FILE = os.path.join(INFO_DIR, 'private_as_dict_new.json')
PREFIX_INFO_FILE = os.path.join(INFO_DIR, 'ip_bgp_entity.csv')
DOMAIN_INFO_FILE = os.path.join(INFO_DIR, 'website_entity.csv')
TRIPLET_FILE = os.path.join(INFO_DIR, 'triplet_20days.csv')
AS_RANK_FILE = os.path.join(INFO_DIR, 'as_rank.json')
ORG_INFO_FILE = os.path.join(INFO_DIR, 'org_entity.csv')
DOMAIN_CN_INFO_FILE = os.path.join(INFO_DIR, 'domain_cn.csv')
IMPORTANT_DOMAIN = os.path.join(INFO_DIR, 'domain_cn_center.txt')

# 解压后的数据集文件暂存路径
DETECTION_DUMP_DIR = os.path.join(os.path.dirname(BASE_DIR), 'data', 'detection')
FEATURE_DUMP_DIR = os.path.join(os.path.dirname(BASE_DIR), 'data', 'feature')
PREFIX_COUNT_DUMP_DIR = os.path.join(os.path.dirname(BASE_DIR), 'data', 'prefix_count')

# 中断和恢复阈值
PREFIX_OUTAGE_THRESHOLD = float(os.environ.get('PREFIX_OUTAGE_THRESHOLD', 1))
AS_OUTAGE_THRESHOLD = float(os.environ.get('AS_OUTAGE_THRESHOLD', 0.2))
COUNTRY_OUTAGE_THRESHOLD = float(os.environ.get('COUNTRY_OUTAGE_THRESHOLD', 0.03))
PREFIX_RESTORE_THRESHOLD = float(os.environ.get('PREFIX_RESTORE_THRESHOLD', 0.4))
AS_RESTORE_THRESHOLD = float(os.environ.get('AS_RESTORE_THRESHOLD', 0.85))
COUNTRY_RESTORE_THRESHOLD = float(os.environ.get('COUNTRY_RESTORE_THRESHOLD', 0.98))

# ========== 旧邮件配置（已弃用）==========
# MAIL_FROM = ""  # 发送方
# MAIL_TO = []  # 接收方
# MAIL_AUTH_CODE = "JMMHDYYRKYAENAXV"  # 授权码
# MAIL_SERVER_HOST = 'smtp.163.com'  # 邮件服务器
# MAIL_SERVER_PORT = 25  # 邮件服务器端口

# ========== 新邮件配置（SSL/TLS 465）==========
MAIL_SMTP_HOST = os.environ.get('MAIL_SMTP_HOST', '')  # SMTP 服务器
MAIL_SMTP_PORT = _get_int('MAIL_SMTP_PORT', 465)  # SSL/TLS 端口
MAIL_USERNAME = os.environ.get('MAIL_USERNAME', '')  # 登录用户名
MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')  # SMTP 授权码
MAIL_FROM = os.environ.get('MAIL_FROM', MAIL_USERNAME)  # 发件人显示地址
MAIL_TO = _get_list('MAIL_TO', [])  # 收件人列表
MAIL_ENABLED = _get_bool('MAIL_ENABLED', False)  # 是否启用邮件告警
MAIL_COOLDOWN_SECONDS = _get_int('MAIL_COOLDOWN_SECONDS', 1800)  # 同一事件冷却时间（秒），防止刷屏
MAIL_SUBJECT_PREFIX = os.environ.get('MAIL_SUBJECT_PREFIX', '[Domeye告警]')  # 邮件主题前缀

def init_runtime_directories():
    for path in (DETECTION_DUMP_DIR, PREFIX_COUNT_DUMP_DIR, FEATURE_DUMP_DIR):
        os.makedirs(path, exist_ok=True)


FEATURE_COUNTRY_TABLE = os.environ.get('FEATURE_COUNTRY_TABLE', 'feature_country')  # 国家特征表名

FEATURE_OTHER_TABLE = os.environ.get('FEATURE_OTHER_TABLE', 'feature_other')  # 其他特征表名

# ========== Feature ASN 月表（避免大表 ALTER）==========
# 仅影响 AS 特征表：feature_other、feature_{US/CN/...}。
# 写入与查询会按时间路由到对应月份表：{base}_{YYYYMM}，例如 feature_other_202601。
# 旧表重命名为：{base}_old（一次性迁移脚本完成）。
FEATURE_ASN_MONTHLY_ENABLED = _get_bool('FEATURE_ASN_MONTHLY_ENABLED', True)
FEATURE_ASN_OLD_SUFFIX = os.environ.get('FEATURE_ASN_OLD_SUFFIX', '_old')
# AS拥有量大于1000的国家
BIG_COUNTRY = {
        '美国': 'US',
        '巴西': 'BR',
        '中国': 'CN',
        '俄罗斯': 'RU',
        '印度': 'IN',
        '英国': 'GB',
        '印度尼西亚': 'ID',
        '德国': 'DE',
        '澳大利亚': 'AU',
        '波兰': 'PL'
}
