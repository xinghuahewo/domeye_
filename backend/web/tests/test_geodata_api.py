import pytest
import os
import sys
import json
from unittest.mock import patch, MagicMock, mock_open

# --- 动态添加项目根目录到 sys.path ---
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, '..', '..'))
sys.path.append(backend_dir)

# --- 导入Flask应用工厂 ---
from run import create_app

@pytest.fixture(scope='module')
def app():
    """ Pytest Fixture: 创建和配置用于测试的Flask应用实例 """
    os.environ['FLASK_CONFIG'] = 'testing'
    app = create_app()
    del os.environ['FLASK_CONFIG']
    yield app

@pytest.fixture
def client(app):
    """ Pytest Fixture: 创建一个Flask测试客户端 """
    return app.test_client()

# --- Geodata API Tests ---

class TestGeodataAPI:
    """
    为地理数据（Geodata）相关的API端点提供测试用例。
    这些测试使用了模拟（Mock）技术来隔离外部依赖，如数据库和文件系统。
    """

    @patch('web.api.geodata.api.get_boundary')
    @patch('web.api.geodata.api.get_boundary_total_page', return_value=(5, 50)) # (total_page, record_count)
    @patch('web.api.geodata.api.deal_boundary', return_value=[{"boundary": "mock_data"}])
    def test_boundary_list_api(self, mock_deal, mock_total, mock_get, client):
        """ 测试: /api/v1/geodata/boundaries 列表接口 """
        print("\n--- [测试] 边界列表接口 (BoundaryListResource) ---")

        # 准备
        query_params = {
            'page_size': 10,
            'page_num': 1,
            'export_as_country': 'China',
            'peer_as_country': 'USA',
            'sort_mode': 'desc'
        }
        
        # 执行
        response = client.get('/api/v1/geodata/boundaries', query_string=query_params)

        # 断言
        assert response.status_code == 200
        response_data = response.get_json()
        assert response_data['total_page'] == 5
        assert response_data['record_count'] == 50
        assert response_data['data'] == [{"boundary": "mock_data"}]

        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args.kwargs
        assert call_kwargs['page_size'] == query_params['page_size']
        assert call_kwargs['export_as_country'] == query_params['export_as_country']
        assert call_kwargs['peer_as_country'] == query_params['peer_as_country']
        assert call_kwargs['sort_mode'] == query_params['sort_mode']
        print("成功: API正确解析了查询参数并调用了底层获取函数。")


    @patch('web.api.geodata.api.get_connection')
    @patch('web.api.geodata.api.get_connection_total_page', return_value=(3, 30))
    @patch('web.api.geodata.api.deal_connection', return_value=[{"connection": "mock_data"}])
    def test_connection_list_api(self, mock_deal, mock_total, mock_get, client):
        """ 测试: /api/v1/geodata/connections 列表接口 """
        print("\n--- [测试] 连通性列表接口 (ConnectionListResource) ---")

        # 准备
        query_params = {
            'vp_country_chinese_name': '中国',
            'dst_country_chinese_name': '美国'
        }
        
        # 执行
        response = client.get('/api/v1/geodata/connections', query_string=query_params)

        # 断言
        assert response.status_code == 200
        response_data = response.get_json()
        assert response_data['total_page'] == 3
        assert response_data['record_count'] == 30
        assert response_data['data'] == [{"connection": "mock_data"}]
        
        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args.kwargs
        assert call_kwargs['vp_country_chinese_name'] == query_params['vp_country_chinese_name']
        assert call_kwargs['dst_country_chinese_name'] == query_params['dst_country_chinese_name']
        print("成功: API正确解析了查询参数并调用了底层获取函数。")

    def test_boundary_display_data_api(self, client):
        """ 测试: /api/v1/geodata/boundaries/display 大屏数据接口 """
        print("\n--- [测试] 边界大屏数据接口 (BoundaryDisplayDataResource) ---")
        
        # 准备: 模拟要从文件中读取的JSON数据
        mock_file_content = {
            "China": {"nodes": [], "links": []},
            "USA": {"nodes": [{"id": "AS123"}], "links": []}
        }
        # 使用 mock_open 来模拟 `open` 函数
        m = mock_open(read_data=json.dumps(mock_file_content))

        # 使用 patch 上下文管理器来替换内建的 `open` 函数
        with patch('builtins.open', m):
            # 执行
            response = client.get('/api/v1/geodata/boundaries/display', json={'country': 'USA'})

            # 断言
            assert response.status_code == 200
            response_data = response.get_json()
            assert response_data['nodes'][0]['id'] == 'AS123'
            
            # 验证文件是否以正确的方式被打开
            m.assert_called_once_with("screen_data/output_data/boundary.json", encoding="utf-8")
            print("成功: API能正确地从(模拟的)文件中读取并返回特定国家的数据。")


    @patch('web.api.geodata.api.os.path.exists', return_value=True)
    def test_boundary_screen_file_api_found(self, mock_exists, client):
        """ 测试: /api/v1/geodata/boundaries/screenfile 文件路径接口 (文件存在) """
        print("\n--- [测试] 边界大屏文件路径接口 - 文件存在 ---")
        
        # 执行
        response = client.get('/api/v1/geodata/boundaries/screenfile')
        
        # 断言
        assert response.status_code == 200
        # 验证返回的是一个拼接后的路径字符串
        assert 'screen_data-output_data-boundary.json' in response.get_data(as_text=True)
        print("成功: API在文件存在时返回了预期的路径字符串。")


    @patch('web.api.geodata.api.os.path.exists', return_value=False)
    def test_boundary_screen_file_api_not_found(self, mock_exists, client):
        """ 测试: /api/v1/geodata/boundaries/screenfile 文件路径接口 (文件不存在) """
        print("\n--- [测试] 边界大屏文件路径接口 - 文件不存在 ---")

        # 执行
        response = client.get('/api/v1/geodata/boundaries/screenfile')
        
        # 断言
        assert response.status_code == 404
        assert 'File not found' in response.get_data(as_text=True)
        print("成功: API在文件不存在时返回了404。") 