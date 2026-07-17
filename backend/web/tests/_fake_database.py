import sys
import types


def install_fake_database_module():
    """Install a lightweight config.database module before importing app code."""
    fake_database_module = types.ModuleType('config.database')
    fake_database_module.conn_11 = object()
    fake_database_module.conn_13 = object()
    fake_database_module.conn_15 = object()
    fake_database_module.conn_226 = object()
    fake_database_module.DATABASE = 'test'
    fake_database_module.USER = 'test'
    fake_database_module.PASSWORD = 'test'
    fake_database_module.HOST = '127.0.0.1'
    fake_database_module.PORT = 5432
    sys.modules['config.database'] = fake_database_module

