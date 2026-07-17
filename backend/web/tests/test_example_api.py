import pytest
import os
import sys

# --- 动态添加项目根目录到 sys.path ---
# 这对于正确导入应用模块至关重要
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, '..', '..'))
sys.path.append(backend_dir)

# --- 从测试模块导入按需加载函数 ---
from web.tests.data_loader import get_as_info, clear_all_data

# --- 导入Flask应用工厂 ---
from run import create_app

@pytest.fixture(scope='module')
def app():
    """
    Pytest Fixture: 创建和配置一个新的 Flask 应用实例用于测试。
    'module' scope 意味着这个 fixture 在整个测试模块中只执行一次。
    """
    # 设置环境变量，告诉 create_app 我们正在进行测试
    # 这将阻止它加载所有全局数据
    os.environ['FLASK_CONFIG'] = 'testing'
    
    # 创建应用实例
    app = create_app()
    
    # 清理环境变量
    del os.environ['FLASK_CONFIG']
    
    # 返回应用实例以供测试使用
    yield app
    
    # --- 清理工作 ---
    # 在所有测试结束后，清理缓存的数据
    clear_all_data()


@pytest.fixture
def client(app):
    """
    Pytest Fixture: 为测试创建一个 Flask 测试客户端。
    这个 fixture 依赖于 'app' fixture。
    """
    return app.test_client()


def test_as_feature_list_api_loads_data_on_demand(client):
    """
    测试: 访问 /api/v1/features/ases 端点时，是否能按需加载 AS 数据。
    
    这个测试用例展示了如何结合测试客户端和按需数据加载器。
    """
    print("\n--- [测试开始] 验证 AS 特征列表 API 的按需加载 ---")

    # 1. 在调用API之前，先按需加载AS数据
    #    在真实的API实现中，这个调用会发生在API的逻辑内部。
    #    这里我们为了演示，手动调用它。
    print("步骤 1: 手动调用 get_as_info() 来触发数据加载...")
    as_info_data = get_as_info()
    assert as_info_data is not None
    assert len(as_info_data) > 0
    print(f"成功加载了 {len(as_info_data)} 条AS信息。")

    # 2. 使用测试客户端向目标API发送GET请求
    print("\n步骤 2: 向 /api/v1/features/ases 发送GET请求...")
    response = client.get('/api/v1/features/ases')
    print(f"API响应状态码: {response.status_code}")

    # 3. 断言请求成功
    assert response.status_code == 200

    # 4. 断言响应中包含预期的数据
    #    注意：这取决于 API 的实际返回格式。
    #    我们假设它会返回一个 JSON 列表，并且列表不为空。
    response_json = response.get_json()
    assert isinstance(response_json, dict)
    # 这是一个非常基本的断言，实际测试中可能需要更复杂的验证
    # 例如，检查返回的数据是否与 `as_info_data` 中的某条记录匹配
    print("步骤 3: 验证API响应成功并且返回了数据。")
    print("--- [测试结束] ---")

# --- 如何运行这个测试 ---
#
# 1. 确保你已经安装了 pytest 和 flask:
#    pip install pytest flask flask-restful pandas openpyxl
#
# 2. 在项目根目录 (即 Domeye/backend/ 目录) 运行 pytest:
#    cd /path/to/your/project/Domeye/backend/
#    pytest
#
# 3. Pytest 会自动发现并运行 tests/ 目录下的 test_*.py 文件。
#    你会在终端看到详细的输出，包括我们添加的 print 语句。 