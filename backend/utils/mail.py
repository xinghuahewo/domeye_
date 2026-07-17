"""
邮件告警工具模块
- SMTP SSL/TLS (465) 发送
- 异步线程发送（不阻塞主检测流程）
- 去重/冷却机制（防止刷屏）
- 失败时仅记录日志，不抛异常
"""
import os
import sys
import smtplib
import threading
import time
from email.mime.text import MIMEText
from email.utils import formataddr

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.config import (
    MAIL_SMTP_HOST,
    MAIL_SMTP_PORT,
    MAIL_USERNAME,
    MAIL_PASSWORD,
    MAIL_FROM,
    MAIL_TO,
    MAIL_ENABLED,
    MAIL_COOLDOWN_SECONDS,
    MAIL_SUBJECT_PREFIX,
)
from config.logger import outage_logger

# 内存级去重/冷却记录：{key: last_send_timestamp}
_send_cache = {}
_cache_lock = threading.Lock()


def _is_mail_configured():
    """检查邮件配置是否完整"""
    if not MAIL_ENABLED:
        return False
    if not MAIL_SMTP_HOST or not MAIL_USERNAME or not MAIL_PASSWORD:
        return False
    if not MAIL_TO or len(MAIL_TO) == 0:
        return False
    return True


def should_send(key, cooldown_seconds=None):
    """
    判断某个 key 是否应该发送（冷却去重）
    
    Args:
        key: 事件唯一标识，例如 "prefix_outage:1.2.3.0/24:high:r"
        cooldown_seconds: 冷却时间（秒），默认使用配置值
    
    Returns:
        bool: True 表示可以发送，False 表示在冷却期内
    """
    if cooldown_seconds is None:
        cooldown_seconds = MAIL_COOLDOWN_SECONDS
    
    now = time.time()
    with _cache_lock:
        last_time = _send_cache.get(key, 0)
        if now - last_time < cooldown_seconds:
            return False
        _send_cache[key] = now
        return True


def _do_send_email(subject, content, to_list):
    """
    实际发送邮件（同步，内部使用）
    
    Args:
        subject: 邮件主题
        content: 邮件正文（纯文本）
        to_list: 收件人列表
    """
    try:
        msg = MIMEText(content, 'plain', 'utf-8')
        msg['Subject'] = f"{MAIL_SUBJECT_PREFIX} {subject}"
        msg['From'] = formataddr(('Domeye告警', MAIL_FROM))
        msg['To'] = ', '.join(to_list)

        with smtplib.SMTP_SSL(MAIL_SMTP_HOST, MAIL_SMTP_PORT, timeout=30) as server:
            server.login(MAIL_USERNAME, MAIL_PASSWORD)
            server.sendmail(MAIL_FROM, to_list, msg.as_string())
        
        outage_logger.info(f"邮件发送成功: {subject}")
    except Exception as e:
        outage_logger.exception(f"邮件发送失败: {subject}, 错误: {e}")


def send_email(subject, content, to=None):
    """
    同步发送邮件（会阻塞当前线程）
    
    Args:
        subject: 邮件主题
        content: 邮件正文
        to: 收件人列表（可选，默认使用配置）
    """
    if not _is_mail_configured():
        outage_logger.warning("邮件配置不完整，跳过发送")
        return
    
    to_list = to if to else MAIL_TO
    _do_send_email(subject, content, to_list)


def send_email_async(subject, content, to=None, key=None, cooldown_seconds=None):
    """
    异步发送邮件（后台线程，不阻塞主流程）
    
    Args:
        subject: 邮件主题
        content: 邮件正文
        to: 收件人列表（可选，默认使用配置）
        key: 去重标识（可选，若提供则进行冷却去重）
        cooldown_seconds: 冷却时间（秒），默认使用配置值
    """
    if not _is_mail_configured():
        outage_logger.warning("邮件配置不完整，跳过发送")
        return
    
    # 冷却去重检查
    if key is not None:
        if not should_send(key, cooldown_seconds):
            outage_logger.info(f"邮件冷却中，跳过发送: key={key}")
            return
    
    to_list = to if to else MAIL_TO
    
    # 后台线程发送
    thread = threading.Thread(
        target=_do_send_email,
        args=(subject, content, to_list),
        daemon=True
    )
    thread.start()


def send_outage_alert(event_type, event_info, detail_url, level, source):
    """
    发送中断告警邮件（便捷封装）
    
    Args:
        event_type: 事件类型，如 "前缀中断"、"AS中断"、"国家中断"
        event_info: 事件描述信息
        detail_url: 详情页 URL
        level: 事件等级 (high/middle/low)
        source: 数据源标识
    """
    # 仅 high 级别发送
    if level != 'high':
        return
    
    # 构造去重 key
    key = f"{event_type}:{detail_url}:{source}"
    
    subject = f"{event_type} - {level.upper()}"
    content = f"""
=====================================
  Domeye BGP 中断告警
=====================================

【事件类型】{event_type}
【事件等级】{level.upper()}
【数据源】{source}

【事件详情】
{event_info}

【详情链接】
{detail_url}

=====================================
此邮件由 Domeye 系统自动发送，请勿直接回复。
"""
    
    send_email_async(subject, content.strip(), key=key)


# 用于测试的入口
if __name__ == '__main__':
    print("测试邮件发送...")
    print(f"MAIL_SMTP_HOST: {MAIL_SMTP_HOST}")
    print(f"MAIL_SMTP_PORT: {MAIL_SMTP_PORT}")
    print(f"MAIL_USERNAME: {MAIL_USERNAME}")
    print(f"MAIL_PASSWORD: {'*' * len(MAIL_PASSWORD) if MAIL_PASSWORD else '(未设置)'}")
    print(f"MAIL_FROM: {MAIL_FROM}")
    print(f"MAIL_TO: {MAIL_TO}")
    print(f"MAIL_ENABLED: {MAIL_ENABLED}")
    
    if _is_mail_configured():
        send_email("测试邮件", "这是一封来自 Domeye 系统的测试邮件。")
        print("邮件已发送，请检查收件箱。")
    else:
        print("邮件配置不完整，无法发送测试邮件。")
