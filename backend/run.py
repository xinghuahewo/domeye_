'''
Author: Botong Wu 2048400180@qq.com
Date: 2025-07-10 20:26:39
LastEditors: Botong Wu 2048400180@qq.com
LastEditTime: 2026-02-04 18:52:40
FilePath: /bgpdata/Domeye/backend/run.py
Description: 这是默认设置,请设置`customMade`, 打开koroFileHeader查看配置 进行设置: https://github.com/OBKoro1/koro1FileHeader/wiki/%E9%85%8D%E7%BD%AE
'''
import os


def load_local_env():
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
            value = value.strip()
            if not key or key in os.environ:
                continue
            os.environ[key] = value


load_local_env()

from web.flask_app import flask_app
from config.config import DEBUG, PORT, init_runtime_directories

# --- Application Factory Pattern ---

def create_app(config_name=None):
    """
    创建并配置 Flask 应用实例
    """
    if config_name is None:
        config_name = os.getenv('FLASK_CONFIG', 'default')
    
    # 1. 从 config/config.py 加载配置
    app = flask_app

    register_routes(app)
    initialize_runtime_services()
    
    return app


def register_routes(app):
    # 导入路由配置会触发 api.add_resource，仍然集中在启动流程中完成。
    with app.app_context():
        from web.api import route
    return app


def initialize_runtime_services():
    if os.getenv('FLASK_CONFIG') == 'testing':
        return

    init_runtime_directories()

    from init_db import auto_init_db
    auto_init_db()

    from utils.data_loader import init_global_data
    init_global_data()

# --- Main Entry Point ---

if __name__ == '__main__':
    # 创建应用实例
    app = create_app()
    
    
    # 启动 Flask 开发服务器
    # DEBUG=True 时启用自动重载，避免每次改代码都手动重启
    # app.run(host='0.0.0.0', port=PORT, debug=DEBUG, use_reloader=DEBUG)
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)
