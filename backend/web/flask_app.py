"""Flask 应用构造器。"""

import os

from flask import Flask
from flask_cors import CORS


def _cors_origins():
    raw_value = os.environ.get('CORS_ORIGINS', '')
    if not raw_value.strip():
        return []
    return [item.strip() for item in raw_value.split(',') if item.strip()]


def create_flask_app():
    app = Flask(__name__)
    origins = _cors_origins()
    if origins:
        CORS(
            app,
            origins=origins,
            supports_credentials=False,
            methods=['GET', 'OPTIONS'],
            allow_headers=['Content-Type'],
        )

    from .api.route import api_v1_bp

    app.register_blueprint(api_v1_bp, url_prefix='/api/v1')
    return app
