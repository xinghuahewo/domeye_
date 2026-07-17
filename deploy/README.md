# Domeye Core 部署说明

本目录用于在 `/home/bgpdata/Domeye-Core` 部署核心精简版。部署保持现网的运行拓扑，但使用独立目录、独立 Screen 会话和不常用端口，不会替换或停止原项目。

## 固定运行参数

| 项目 | 配置 |
| --- | --- |
| 项目目录 | `/home/bgpdata/Domeye-Core` |
| 前端入口 | `0.0.0.0:28471`，由 nginx 提供静态文件 |
| 后端入口 | `127.0.0.1:28473`，仅供本机 nginx 代理 |
| 后端 Screen 会话 | `domeye_core_app` |
| uv 路径 | `/home/bgpdata/.local/bin/uv` |
| Python 环境 | `/home/bgpdata/Domeye-Core/backend/venv` |
| 基础信息只读来源 | `/home/bgpdata/Domeye/backend/info` |
| 后端日志 | `/home/bgpdata/Domeye-Core/var/log/backend-screen.log` |

脚本只会匹配完整名称为 `PID.domeye_core_app` 的会话。即使原项目仍在 `PID.app` 中运行，也不会被启停脚本选中。

## 首次部署

以下命令均在新项目中执行：

```bash
cd /home/bgpdata/Domeye-Core

# 后端依赖严格按 uv.lock 同步到项目自己的环境。
cd backend
UV_PROJECT_ENVIRONMENT=venv /home/bgpdata/.local/bin/uv sync --locked

# .env 应由受控来源准备，不要把真实凭据提交到 Git。
test -f .env
chmod 600 .env

# 构建 nginx 要提供的前端静态文件。
cd ../frontend
npm ci
npm run build
```

当前服务器的 nginx 会加载 `/etc/nginx/conf.d/*.conf`。安装配置前先检查目标位置是否已有同名文件：

```bash
sudo install -m 0644 \
  /home/bgpdata/Domeye-Core/deploy/nginx/domeye-core.conf \
  /etc/nginx/conf.d/domeye-core.conf

sudo nginx -t
sudo systemctl reload nginx
```

若迁移到其他服务器，应先确认 `nginx.conf` 实际包含的目录；仍须先执行 `nginx -t`，通过后才能重新加载。

## 启停与检查

```bash
cd /home/bgpdata/Domeye-Core

# 启动；重复执行不会创建第二个同名会话。
./deploy/start-backend.sh

# 同时检查 Screen、后端健康接口和 nginx 前端入口。
./deploy/status.sh

# 仅停止 domeye_core_app；重复执行也是安全的。
./deploy/stop-backend.sh
```

启动脚本会显式固定生产模式、监听地址和端口，并关闭自动建库、启动时加载核心数据等行为。基础信息暂时从原项目 `info/` 目录只读复用。因此，即使迁移来的 `.env` 仍含旧端口，也不会与原服务发生冲突。

排查后端问题时可查看：

```bash
tail -n 100 /home/bgpdata/Domeye-Core/var/log/backend-screen.log
screen -ls
curl --fail http://127.0.0.1:28473/api/v1/healthz
curl --fail http://127.0.0.1:28471/
```

## 更新部署

更新前端时重新执行 `npm ci && npm run build`，nginx 会直接读取新的 `frontend/dist`。更新后端代码或锁文件时，先停止新项目后端，同步锁定依赖，再启动并检查状态：

```bash
cd /home/bgpdata/Domeye-Core
./deploy/stop-backend.sh

cd backend
UV_PROJECT_ENVIRONMENT=venv /home/bgpdata/.local/bin/uv sync --locked

cd ..
./deploy/start-backend.sh
./deploy/status.sh
```

所有操作目标都必须保持在 `/home/bgpdata/Domeye-Core`。不要把这些脚本复制到 `/home/bgpdata/Domeye` 后执行，也不要复用原项目的 Screen 会话名或端口。
