from flask import Flask
from flask_cors import CORS

from .api.route import api_v1_bp

# 1. 创建 Flask 应用实例
flask_app = Flask(__name__)

# 2. 初始化 CORS 扩展以允许跨域请求
#    - origins: 明确指定允许访问的源列表
CORS(
    flask_app,
    origins=[
        "*"
        # "http://localhost:8899", 
        # "http://127.0.0.1:8899",
        # "http://your-frontend-host:8899"
        # "10.38.104.193:19740"
    ],
    supports_credentials=True,
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"]
)

# 3. 注册 API 蓝图并添加版本前缀
flask_app.register_blueprint(api_v1_bp, url_prefix='/api/v1')

# 注意：这里不加载配置，也不注册路由。
# 这是一个纯粹的应用实例，将在更高层级的文件中被配置和使用。
