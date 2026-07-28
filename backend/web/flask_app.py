"""Flask 应用构造器。"""

import os

from flask import Flask
from flask_cors import CORS

from config.data_window import validate_data_window_config
from .data_window_guard import enforce_request_data_window


def _cors_origins():
    raw_value = os.environ.get('CORS_ORIGINS', '')
    if not raw_value.strip():
        return []
    return [item.strip() for item in raw_value.split(',') if item.strip()]


def create_flask_app():
    validate_data_window_config()
    app = Flask(__name__)
    origins = _cors_origins()
    if origins:
        CORS(
            app,
            origins=origins,
            supports_credentials=False,
            methods=['GET', 'OPTIONS'],
            allow_headers=['Content-Type', 'If-None-Match'],
            expose_headers=['ETag'],
        )

    from .api.route import api_v1_bp
    from .api.v2.route import api_v2_bp

    app.before_request(enforce_request_data_window)
    app.register_blueprint(api_v1_bp, url_prefix='/api/v1')
    app.register_blueprint(api_v2_bp, url_prefix='/api/v2')
    return app
