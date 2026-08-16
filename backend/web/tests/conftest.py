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


@pytest.fixture()
def assert_contract():
    def check(value, schema):
        assert type(value) is dict
        assert set(value) == set(schema)
        for field, expected_types in schema.items():
            allowed_types = expected_types if isinstance(expected_types, tuple) else (expected_types,)
            assert type(value[field]) in allowed_types, (
                f'{field} 的类型应为 {[item.__name__ for item in allowed_types]}，'
                f'实际为 {type(value[field]).__name__}'
            )

    return check
