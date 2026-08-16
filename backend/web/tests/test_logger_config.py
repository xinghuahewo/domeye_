import os

import pytest

from config.config import BASE_DIR
from config.logger import resolve_log_dir


def test_logger_keeps_production_default_when_unconfigured():
    assert resolve_log_dir({}) == os.path.join(BASE_DIR, 'logs')


def test_logger_accepts_isolated_absolute_directory():
    assert resolve_log_dir({
        'DOMEYE_LOG_DIR': '/home/bgpdata/Domeye-Core-dev-data/api/log/app/',
    }) == '/home/bgpdata/Domeye-Core-dev-data/api/log/app'


def test_logger_rejects_relative_override():
    with pytest.raises(RuntimeError, match='必须是绝对路径'):
        resolve_log_dir({'DOMEYE_LOG_DIR': 'shared/logs'})
