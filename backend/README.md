# Domeye Backend

## 1. 项目定位

`backend/` 是 Domeye 的后端服务与检测任务目录，负责两类能力：

1. Flask API  
对前端提供事件、特征、地理数据、认证、看板等接口。

2. BGP 检测与数据加工任务  
从 RIB / update 数据中提取路由异常、资源统计、特征数据，并写入 PostgreSQL。

当前后端以 Flask + PostgreSQL 为核心，目录中同时包含：

- 在线 API 服务
- 批处理检测脚本
- 数据初始化与建表逻辑
- 测试脚本与部分测试样例

## 2. 目录结构

```text
backend/
├── config/                 运行时配置、数据库连接、日志配置
├── core/                   BGP 检测与离线/批处理任务
├── database/               数据库访问层
├── info/                   基础信息数据文件（AS、前缀、国家、域名等）
├── logs/                   运行日志
├── reports/                导出文件目录
├── screen_data/            屏幕展示/中间产物
├── tests/                  测试脚本与测试样例
├── utils/                  通用工具、全局数据初始化、报告辅助
├── web/                    Flask API、资源路由、接口测试
├── .env.example            环境变量示例
├── init_db.py              启动时建表/补表
├── requirements.txt        Python 依赖
└── run.py                  Flask 服务入口
```

重点子目录说明：

- `config/config.py`：业务配置、阈值、数据路径、邮件配置
- `config/database.py`：数据库与 SSH 配置，当前会在导入时直接建立连接
- `core/`：核心检测脚本，不同脚本负责不同任务
- `web/api/route.py`：统一路由注册入口
- `utils/data_loader.py`：启动时加载全局静态数据

## 3. 运行架构

后端大致分成三层：

1. 数据任务层  
`core/` 下脚本消费本地 BGP 数据文件，生成事件、特征和资源信息。

2. 数据访问层  
`database/` 负责 PostgreSQL 查询与写入。

3. API 层  
`web/` 负责封装 Flask + Flask-RESTful 接口，对前端提供查询能力。

启动 `run.py` 时会发生：

1. 自动读取 `backend/.env`（如果存在）
2. 导入 Flask app 和 API 路由
3. 导入数据库连接与配置
4. 执行 `init_db.auto_init_db()`
5. 执行 `utils.data_loader.init_global_data()`
6. 启动 Flask 服务

这意味着：

- 数据库不可达时，服务可能在导入阶段就失败
- `info/` 下基础数据文件缺失时，初始化也可能失败

## 4. 环境要求

建议环境：

- Python 3.10+
- PostgreSQL
- Linux

主要 Python 依赖见 [requirements.txt](/home/bgpdata/Domeye/backend/requirements.txt)：

- `Flask`
- `Flask-Cors`
- `Flask-RESTful`
- `psycopg2-binary`
- `pandas`
- `numpy`
- `networkx`
- `matplotlib`
- `requests`
- `pytest`

安装依赖：

```bash
cd /home/bgpdata/Domeye/backend
pip install -r requirements.txt
```

## 5. 配置方式

### 5.1 配置来源

运行配置主要来自两个文件：

- [config/config.py](/home/bgpdata/Domeye/backend/config/config.py)
- [config/database.py](/home/bgpdata/Domeye/backend/config/database.py)

现在代码已经改成“优先读取环境变量”，并提供了示例文件：

- [backend/.env.example](/home/bgpdata/Domeye/backend/.env.example)

本地开发可直接复制：

```bash
cd /home/bgpdata/Domeye/backend
cp .env.example .env
```

### 5.2 常用环境变量

基础运行：

- `PORT`
- `DEBUG`
- `SOURCE`
- `MODE`
- `RIB_HISTORY_FILE`
- `BASE_DATA_PATH`

数据库：

- `DB_HOST`
- `DB_PORT`
- `DB_NAME`
- `DB_USER`
- `DB_PASSWORD`

邮件：

- `MAIL_ENABLED`
- `MAIL_SMTP_HOST`
- `MAIL_SMTP_PORT`
- `MAIL_USERNAME`
- `MAIL_PASSWORD`
- `MAIL_FROM`
- `MAIL_TO`

SSH / 数据通道：

- `SSH_HOST`
- `SSH_USER`
- `SSH_PWD`
- `REMOTE_PATH`
- `SSH_HOST2`
- `SSH_USER2`
- `SSH_PWD2`
- `REMOTE_PATH2`

### 5.3 当前实现限制

[config/database.py](/home/bgpdata/Domeye/backend/config/database.py) 当前在模块导入时就执行：

- `conn_11`
- `conn_13`
- `conn_15`
- `conn_226`

也就是说，只要导入后端模块，就会尝试连数据库。  
如果数据库未启动、地址错误或密码不对，`python run.py` 会直接报 `psycopg2.OperationalError`。

## 6. 启动方式

### 6.1 启动 Flask API

```bash
cd /home/bgpdata/Domeye/backend
python3 run.py
```

默认监听：

- `0.0.0.0:${PORT}`

`run.py` 现在会自动加载：

- [backend/.env](/home/bgpdata/Domeye/backend/.env)

所以不需要每次手动 `source .env`。

### 6.2 初始化数据库结构

启动 API 时会自动调用：

- [init_db.py](/home/bgpdata/Domeye/backend/init_db.py)

如果你只想单独执行初始化，也可以直接调用相关脚本或导入 `auto_init_db()`。

## 7. 核心任务脚本

`core/` 目录里的主要脚本：

- [BGPDetection.py](/home/bgpdata/Domeye/backend/core/BGPDetection.py)  
  检测主任务入口，负责异常检测流程。

- [BGPResource.py](/home/bgpdata/Domeye/backend/core/BGPResource.py)  
  资源统计与部分基础数据生成；国家内部拓扑构建也和它相关。

- [BGPFeature.py](/home/bgpdata/Domeye/backend/core/BGPFeature.py)  
  特征提取任务。

- [BGPFeature_ir.py](/home/bgpdata/Domeye/backend/core/BGPFeature_ir.py)  
  与特征计算相关的另一套实现/实验版本。

- [BGPHijack.py](/home/bgpdata/Domeye/backend/core/BGPHijack.py)  
  前缀劫持检测。

- [BGPSubHijack.py](/home/bgpdata/Domeye/backend/core/BGPSubHijack.py)  
  子前缀劫持检测。

- [BGPLeak.py](/home/bgpdata/Domeye/backend/core/BGPLeak.py)  
  路由泄露检测。

- [BGPOutage.py](/home/bgpdata/Domeye/backend/core/BGPOutage.py)  
  路由中断检测。

常见运行方式：

```bash
cd /home/bgpdata/Domeye/backend
python3 core/BGPDetection.py
python3 core/BGPResource.py
python3 core/BGPFeature.py
```

这些任务通常不是通过 Flask 触发，而是单独跑在后台。

## 8. API 模块

API 路由集中注册在：

- [web/api/route.py](/home/bgpdata/Domeye/backend/web/api/route.py)

当前包含的模块：

- `auth`：登录、注册、用户信息
- `events`：事件列表、详情、状态、研判、通知
- `features`：国家/AS 特征与中断特征
- `geodata`：边界、连通性、展示屏数据
- `dashboard`：看板统计
- `reports`：当前仓库里是占位实现，接口会返回 `501`

常见接口前缀：

- `/api/v1/login`
- `/api/v1/register`
- `/api/v1/events`
- `/api/v1/features/*`
- `/api/v1/geodata/*`
- `/api/v1/dashboard/*`
- `/api/v1/reports/*`

## 9. 数据文件依赖

后端严重依赖 `info/` 与外部 BGP 数据目录。

### 9.1 `info/` 目录

`config/config.py` 中会读取很多基础文件，例如：

- `as_entity.csv`
- `ip_bgp_entity.csv`
- `country.xlsx`
- `as_rank.json`
- `pfx2as_dict.txt`

如果这些文件缺失，很多查询与初始化会失败。

### 9.2 BGP 原始数据目录

由以下变量控制：

- `BASE_DATA_PATH`
- `MODE`
- `RIB_HISTORY_FILE`

默认数据路径不是仓库内目录，而是外部路径，例如：

```text
/home/bgpdata/data/ripe/rrc25/
```

所以迁移部署时必须同步准备外部数据目录。

## 10. 测试

测试主要分两类：

1. API / 单元测试  
位于：
- [backend/web/tests](/home/bgpdata/Domeye/backend/web/tests)

2. 分析脚本 / 样例验证  
位于：
- [backend/tests](/home/bgpdata/Domeye/backend/tests)

运行测试：

```bash
cd /home/bgpdata/Domeye/backend
pytest -q
```

如果只跑 API 相关：

```bash
cd /home/bgpdata/Domeye/backend
pytest -q web/tests
```

注意：

- 某些测试依赖真实数据文件
- 某些测试依赖数据库
- `tests/temp/` 下有测试生成图

## 11. 常见问题

### 11.1 `psycopg2.OperationalError`

说明数据库连接失败。优先检查：

- `backend/.env` 中的 `DB_*`
- 目标 PostgreSQL 是否可访问
- 用户名/密码是否正确

### 11.2 `ModuleNotFoundError: web.api.reports.api`

当前仓库已经补了一个占位版 `reports` 模块。  
如果你需要真正的导出报表实现，需要再恢复原始业务代码。

### 11.3 启动时报基础数据缺失

说明：

- `info/` 内基础文件不完整
- 或 `utils/data_loader.py` 在启动时读取失败

### 11.4 路径相关错误

这个项目不是“纯仓库内自给自足”结构，很多数据路径依赖外部目录。  
迁移到新机器后，最容易出错的是：

- `BASE_DATA_PATH`
- `screen_data/`
- PostgreSQL 连接地址

## 12. 建议的后续整理方向

如果后续继续维护这个后端，建议优先做这几件事：

1. 把 `config/database.py` 从“导入即连接”改成延迟连接
2. 把 `run.py`、`core/*.py` 的配置加载方式统一
3. 把生成产物（`logs/`、`screen_data/`、`tests/temp/`）和源码进一步分离
4. 把部署说明单独拆到 `docs/`，不要和后端总览混在一起
