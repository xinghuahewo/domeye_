### 项目简介
**代码目录：** /home/bgpdata/Domeye/frontend
BGP检测系统的前端界面
### 安装运行
```bash
# 安装依赖
npm run install

# 运行项目
npm run dev

# 打包发布
npm run build
```
### 环境配置
```bash
# 在 .env.development / .env.production 中配置 VITE_API_URL
VITE_API_URL=http://127.0.0.1:19743/api/v1/
```
### 主要目录结构
```Planintext 
├── public/                     #   静态资源
├── src/
│   ├── api/index.ts            # 请求后端接口定义
│   ├── assets/                 # 图片、样式文件
│   ├── components/             # 公共组件    主要看feature 以及home目录
│   ├── newviews/               # 页面组件 事件展示页面
│   ├── router/route.ts         # 路由配置文件
│   └── App.vue                 # 根组件
├── .env.production             # 生产环境配置
└── package.json                # 依赖与脚本
```
### 部署说明
```bash
# 修改后端api  baseurl

# 生成dist文件夹
npm run bulid

# 进入容器放入 /home/code目录

# 启动nginx服务器即可
