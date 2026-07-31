# Domeye 生产实时库存采集

`collect-production-runtime.py` 用于在发布前后或故障核验时，由受信操作者登录
目标服务器后执行一次只读采集。它把以下身份放在同一份机器可读证据中：

- `28473` 监听进程、进程 `cwd` 与实际 Python；
- Sidecar `28474`、金丝雀 `31631` 的监听状态，以及两个固定 Screen 名；
- 由进程 `cwd` 反推的当前 runtime release；
- release、state 目录中的清单、哈希、归档和回滚/管理脚本；
- Nginx `28471` 配置、实际 `root`、前端 `index.html` 与文件树摘要；
- 固定 Node、Python 和中文字体候选的内容摘要；
- 服务器仓库 `HEAD`、`main`、`origin/main` 与脱敏后的 `origin` 地址。

脚本不接受远程地址或任意路径参数，不运行 `ssh`、`git fetch`、`curl`、
`nginx -t`，不读取 `/proc/*/environ`、进程命令行、`.env`、Sidecar 运行配置、
认证 JSON、密钥或 token 文件，也不创建临时文件。除了标准输出外不进行写入。

在目标服务器仓库根目录执行：

```bash
python3 -B deploy/inventory/collect-production-runtime.py
```

需要归档时，由操作者在脚本外自行重定向到批准的证据目录；脚本本身不决定证据
落盘位置。输出是单个 JSON：

```text
schema_version
hash_contract
inventory_sha256
inventory
```

`inventory_sha256` 的输入是 `inventory` 对象按键排序、无多余空白、UTF-8 编码
后的紧凑 JSON，不包含外层 envelope。可用以下只读管道复核：

```bash
python3 -B deploy/inventory/collect-production-runtime.py |
  jq -cS '.inventory' |
  tr -d '\n' |
  sha256sum
```

注意：脚本只采集运行身份证据，不宣称目录不可篡改，也不以 HTTP 200、单个
manifest 或 Git 引用替代进程、端口、release 与 Nginx 的联合核对。

库存代表采集时刻，不会把历史验收、本地检查或候选状态升级为当前生产结论。组件
关系和生产证据边界见
[Domeye Core 前后端总览](../../docs/DomeyeCore前后端总览.md)。
