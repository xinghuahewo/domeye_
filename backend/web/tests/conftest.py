import os

import pytest


os.environ.setdefault('FLASK_CONFIG', 'testing')


@pytest.fixture()
def app():
    from run import create_app

    return create_app('testing')


@pytest.fixture()
def client(app):
    return app.test_client()
