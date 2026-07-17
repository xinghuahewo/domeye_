import pytest
import os
import sys
import json
from unittest.mock import patch, MagicMock
from authlib.jose import jwt, JoseError

# --- 动态添加项目根目录到 sys.path ---
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, '..', '..'))
sys.path.append(backend_dir)

from web.tests._fake_database import install_fake_database_module

install_fake_database_module()

# --- 导入Flask应用工厂和Token生成函数 ---
# 我们需要设置测试环境，但对于auth接口，我们不依赖数据加载器
from run import create_app 
# 我们需要真实的token生成和验证逻辑
# 无法从 web.api.auth.api 导入 generate_token，因为它依赖于 jwt，所以我们在这里重新实现它用于测试

# --- 全局测试变量 ---
# 使用一个固定的密钥进行测试，以确保token的可预测性
TEST_SECRET_KEY = 'test-secret-key-for-auth-api'
ADMIN_USER_ID = 'admin01'
ADMIN_USERNAME = '测试管理员'

@pytest.fixture(scope='module')
def app():
    """ Pytest Fixture: 创建和配置用于测试的Flask应用实例 """
    os.environ['FLASK_CONFIG'] = 'testing'
    # 使用patch来覆盖配置文件中的SECRET_KEY
    with patch('web.api.auth.api.SECRET_KEY', TEST_SECRET_KEY):
        app = create_app()
        yield app
    del os.environ['FLASK_CONFIG']

@pytest.fixture
def client(app):
    """ Pytest Fixture: 创建一个Flask测试客户端 """
    return app.test_client()

@pytest.fixture
def mock_db_connection():
    """ 
    Pytest Fixture: 模拟数据库连接和游标.
    这将阻止任何真实的数据库操作.
    """
    with patch('web.api.auth.api.conn_11') as mock_conn:
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        yield mock_conn, mock_cursor

def generate_test_token(userid, role):
    """ 使用测试密钥生成一个Token """
    header = {'alg': 'HS256'}
    key = TEST_SECRET_KEY
    payload = {'userid': userid, 'role': role}
    return jwt.encode(header=header, payload=payload, key=key).decode('utf-8')

# --- Auth API Tests ---

class TestAuthAPI:
    """
    将认证相关的 API 测试组合在一起。
    包含了独立的失败场景测试和一个完整的端到端工作流测试。
    """

    def test_login_failure_wrong_password(self, client, mock_db_connection):
        """ 测试: 用户因密码错误登录失败 """
        print("\n--- [独立测试] 密码错误导致登录失败 ---")
        mock_conn, mock_cursor = mock_db_connection

        # 准备: 模拟数据库返回一个用户，但密码不匹配
        mock_cursor.fetchone.return_value = {
            'userid': 'testuser',
            'username': '测试用户',
            'password': 'correct_password',
            'role': 'guest'
        }

        # 执行: 使用错误的密码发送请求
        response = client.post('/api/v1/login', json={
            'userid': 'testuser',
            'password': 'wrong_password'
        })

        # 断言: 验证响应
        assert response.status_code == 200
        response_data = json.loads(response.data)
        assert response_data['status'] is False
        assert response_data['msg'] == '密码错误！'
        print("成功: API正确地返回了'密码错误！'。")

    def test_register_failure_user_exists(self, client, mock_db_connection):
        """ 测试: 因用户已存在而注册失败 """
        print("\n--- [独立测试] 因用户已存在而注册失败 ---")
        mock_conn, mock_cursor = mock_db_connection

        # 准备: 模拟数据库返回一个已存在的用户
        mock_cursor.fetchone.return_value = {'userid': 'existinguser'}
        admin_token = generate_test_token(ADMIN_USER_ID, 'admin')

        # 执行: 尝试注册一个已存在的用户
        response = client.post('/api/v1/register',
                               json={'userid': 'existinguser', 'username': 'user',
                                     'password': 'pw', 'role': 'guest'},
                               headers={'Authorization': admin_token})

        # 断言: 验证响应
        assert response.status_code == 200
        response_data = json.loads(response.data)
        assert response_data['status'] is False
        assert response_data['msg'] == '账号已存在！'
        # 断言: 数据库不应该提交任何更改
        mock_conn.commit.assert_not_called()
        print("成功: API在用户已存在时返回了正确错误，并且数据库未执行commit。")

    def test_authorization_required(self, client):
        """ 测试: 当未提供Token时，受保护的接口应返回401 """
        print("\n--- [独立测试] 验证未授权访问 ---")
        
        # 1. 测试需要登录的接口 (UserProfileResource)
        response_profile = client.get('/api/v1/profile')
        assert response_profile.status_code == 401
        print("成功: GET /profile 在没有Token时返回 401。")

        # 2. 测试需要管理员权限的接口 (UserListResource)
        response_users = client.get('/api/v1/users')
        assert response_users.status_code == 401
        print("成功: GET /users 在没有Token时返回 401。")

    def test_admin_permission_required(self, client):
        """ 测试: 当使用非管理员Token时，需要管理员权限的接口应返回403 """
        print("\n--- [独立测试] 验证非管理员权限 ---")
        
        # 准备: 创建一个普通用户的Token
        guest_token = generate_test_token('guestuser', 'guest')
        
        # 执行: 使用普通用户Token访问管理员接口
        response = client.get('/api/v1/users', headers={'Authorization': f'Bearer {guest_token}'})
        
        # 断言
        assert response.status_code == 403 # Forbidden
        response_data = json.loads(response.data)
        assert '您不具备该操作权限' in response_data['msg']
        print("成功: GET /users 在使用非管理员Token时返回 403。")

    def test_full_user_lifecycle_workflow(self, client, mock_db_connection):
        """
        测试: 一个完整的用户管理端到端工作流.
        这个测试会按顺序执行多个操作来验证集成的行为.
        """
        print("\n--- [工作流测试开始] ---")
        mock_conn, mock_cursor = mock_db_connection

        admin_token = None
        test_user_token = None

        # --- 1. 管理员登录 ---
        print("\n步骤 1: 管理员登录")
        admin_password = 'admin_password'
        mock_cursor.fetchone.return_value = {'userid': ADMIN_USER_ID, 'username': ADMIN_USERNAME,
                                             'password': admin_password, 'role': 'admin'}

        response = client.post(
            '/api/v1/login', json={'userid': ADMIN_USER_ID, 'password': admin_password})
        assert response.status_code == 200
        login_data = json.loads(response.data)
        assert login_data['status'] is True
        admin_token = login_data['token']
        assert admin_token is not None
        print("成功: 管理员登录并获取Token。")

        # --- 2. 获取管理员自己的个人资料 ---
        print("\n步骤 2: 管理员获取自己的个人资料")
        # 模拟数据库返回管理员信息
        mock_cursor.fetchone.return_value = {
            'username': ADMIN_USERNAME, 'password': admin_password}
        response = client.get('/api/v1/profile', headers={'Authorization': f'Bearer {admin_token}'})
        assert response.status_code == 200
        profile_data = json.loads(response.data)
        assert profile_data['status'] is True
        assert profile_data['userid'] == ADMIN_USER_ID
        assert profile_data['username'] == ADMIN_USERNAME
        print("成功: 管理员成功获取了自己的资料。")

        # --- 3. 获取用户列表 (需要模拟 get_user_list 和 get_user_total_page 的数据库调用) ---
        print("\n步骤 3: 管理员获取用户列表")
        # 实际项目中可能需要对这两个函数进行更详细的模拟
        with patch('web.api.auth.api.get_user_list') as mock_get_list, patch('web.api.auth.api.get_user_total_page') as mock_get_total:
            mock_get_list.return_value = []
            mock_get_total.return_value = (1, 1)  # (total_page, record_count)
            response = client.get(
                '/api/v1/users', headers={'Authorization': f'Bearer {admin_token}'})
            assert response.status_code == 200
            list_data = json.loads(response.data)
            assert 'data' in list_data
            print("成功: 管理员成功调用了用户列表接口。")

        # --- 4. 管理员注册一个新用户 ---
        print("\n步骤 4: 管理员注册新用户 'testuser'")
        testuser_id = 'testuser'
        testuser_pw = 'testpassword'
        # 模拟 check-if-exists (None) 和 get-creator-name
        mock_cursor.fetchone.side_effect = [None, {'username': ADMIN_USERNAME}]

        response = client.post('/api/v1/register',
                               json={'userid': testuser_id, 'username': '测试普通用户',
                                     'password': testuser_pw, 'role': 'guest'},
                               headers={'Authorization': f'Bearer {admin_token}'})
        assert response.status_code == 200
        register_data = json.loads(response.data)
        assert register_data['status'] is True
        mock_conn.commit.assert_called()
        print("成功: 新用户 'testuser' 注册成功。")

        # --- 5. 新用户登录 ---
        print("\n步骤 5: 新注册的 'testuser' 登录")
        mock_cursor.fetchone.side_effect = None  # 清除 side_effect
        mock_cursor.fetchone.return_value = {'userid': testuser_id,
                                             'username': '测试普通用户', 'password': testuser_pw, 'role': 'guest'}

        response = client.post(
            '/api/v1/login', json={'userid': testuser_id, 'password': testuser_pw})
        assert response.status_code == 200
        login_data = json.loads(response.data)
        assert login_data['status'] is True
        test_user_token = login_data['token']
        assert test_user_token is not None
        print("成功: 'testuser' 登录并获取Token。")

        # --- 6. 新用户更新自己的个人资料 (改密码) ---
        print("\n步骤 6: 'testuser' 更新自己的密码")
        new_password = 'new_password_123'
        # 模拟用户存在检查, 然后模拟获取角色
        mock_cursor.fetchone.side_effect = [
            {'userid': testuser_id}, {'role': 'guest'}]

        response = client.post('/api/v1/profile',
                               json={'username': '测试普通用户', 'password': new_password},
                               headers={'Authorization': f'Bearer {test_user_token}'})
        assert response.status_code == 200
        update_data = json.loads(response.data)
        assert update_data['status'] is True
        assert '更改成功' in update_data['msg']
        mock_conn.commit.assert_called()
        print("成功: 'testuser' 成功更新了自己的密码。")

        # --- 7. 管理员使用 body.userid 修改用户角色，保持前端兼容 ---
        print("\n步骤 7: 管理员使用 body.userid 将 'testuser' 的角色修改为 'operator'")
        mock_conn.commit.reset_mock()
        mock_cursor.fetchone.side_effect = None  # 重置
        mock_cursor.fetchone.side_effect = [
            {'userid': testuser_id}, {'role': 'operator'}]

        response = client.put('/api/v1/admin_edit',
                              json={'userid': testuser_id, 'role': 'operator'},
                              headers={'Authorization': f'Bearer {admin_token}'})
        assert response.status_code == 200
        update_data = json.loads(response.data)
        assert update_data['status'] is True
        mock_conn.commit.assert_called()
        print("成功: 管理员继续可以通过 body.userid 更新用户。")

        # --- 8. 管理员使用 body.userid 删除用户，保持前端兼容 ---
        print("\n步骤 8: 管理员使用 body.userid 删除 'testuser'")
        mock_conn.commit.reset_mock()
        mock_cursor.fetchone.side_effect = None
        mock_cursor.fetchone.return_value = {'userid': testuser_id}
        response = client.delete('/api/v1/admin_edit',
                                 json={'userid': testuser_id},
                                 headers={'Authorization': f'Bearer {admin_token}'})
        assert response.status_code == 200
        delete_data = json.loads(response.data)
        assert delete_data['status'] is True
        mock_conn.commit.assert_called_once()
        print("成功: 管理员继续可以通过 body.userid 删除用户。")

        # --- 9. 验证用户已被删除 (尝试再次登录) ---
        print("\n步骤 9: 验证 'testuser' 已被删除")
        # 模拟用户不存在
        mock_cursor.fetchone.return_value = None
        response = client.post(
            '/api/v1/login', json={'userid': testuser_id, 'password': new_password})
        assert response.status_code == 200
        login_data = json.loads(response.data)
        assert login_data['status'] is False
        assert login_data['msg'] == '账号不存在！'
        print("成功: 登录已删除的 'testuser' 失败，验证了删除操作。")

        print("\n--- [工作流测试结束] ---") 

    def test_admin_edit_accepts_userid_from_body(self, client, mock_db_connection):
        """测试: admin_edit 的 PUT/DELETE 继续兼容 body 里的 userid。"""
        mock_conn, mock_cursor = mock_db_connection
        admin_token = generate_test_token(ADMIN_USER_ID, 'admin')

        mock_cursor.fetchone.side_effect = [{'userid': 'body-user'}, {'role': 'guest'}]
        put_response = client.put(
            '/api/v1/admin_edit',
            json={'userid': 'body-user', 'username': '新名字'},
            headers={'Authorization': f'Bearer {admin_token}'},
        )
        assert put_response.status_code == 200
        assert json.loads(put_response.data)['status'] is True

        mock_conn.commit.reset_mock()
        mock_cursor.fetchone.side_effect = None
        mock_cursor.fetchone.return_value = {'userid': 'body-user'}
        delete_response = client.delete(
            '/api/v1/admin_edit',
            json={'userid': 'body-user'},
            headers={'Authorization': f'Bearer {admin_token}'},
        )
        assert delete_response.status_code == 200
        assert json.loads(delete_response.data)['status'] is True
        mock_conn.commit.assert_called_once()
