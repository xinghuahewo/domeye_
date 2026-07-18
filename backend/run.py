"""Domeye Core Web 服务入口。"""

import os


def load_local_env():
    if os.getenv('DOMEYE_CORE_SKIP_LOCAL_ENV', '').strip().lower() == 'true':
        return

    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if not os.path.exists(env_path):
        return

    with open(env_path, 'r', encoding='utf-8') as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip()


load_local_env()

from config.config import (  # noqa: E402
    AUTO_INIT_DB,
    DEBUG,
    HOST,
    LOAD_CORE_DATA_ON_STARTUP,
    PORT,
    init_runtime_directories,
)
from web.flask_app import create_flask_app  # noqa: E402


def create_app(config_name=None):
    app = create_flask_app()
    effective_config = config_name or os.getenv('FLASK_CONFIG', 'default')
    if effective_config != 'testing':
        initialize_runtime_services()
    return app


def initialize_runtime_services():
    if not (AUTO_INIT_DB or LOAD_CORE_DATA_ON_STARTUP):
        return

    init_runtime_directories()

    if AUTO_INIT_DB:
        from init_db import auto_init_db

        auto_init_db()

    if LOAD_CORE_DATA_ON_STARTUP:
        from utils.data_loader import init_global_data

        init_global_data()


if __name__ == '__main__':
    create_app().run(
        host=HOST,
        port=PORT,
        debug=DEBUG,
        use_reloader=DEBUG,
    )
