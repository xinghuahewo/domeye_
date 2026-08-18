# S4 可恢复隔离执行器：实现证据

状态：Implemented / 未部署 / 未执行服务器隔离。

## 边界

- 只处理策略中三个 Domeye-Core Runtime release 根的精确候选；
- 永久排除旧 `/home/bgpdata/Domeye`、生产数据、开发数据、服务、Screen、活动指针和
  GitHub 凭证；
- 不提供删除能力；隔离后的观察期和删除仍需独立 Gate。

## 执行合同

`quarantine-runtime-releases.py` 的默认行为为只读预检。`--apply` 必须同时获得：

1. `domeye.runtime-release-quarantine-batch/v1` 精确批次；
2. 每个对象的 inventory SHA-256、固定策略 SHA-256 和受支持审计 schema；
3. 本批真实的 `userAuthorization` 原文；
4. 当前审计再次确认候选状态、进程/挂载/锁扫描完整及回滚覆盖完整；
5. 空目标和同文件系统移动条件。

成功移动后，回执记为 `quarantined` 并保留原路径、隔离路径、清单摘要与预计释放空间。
移动或读回失败时，执行器按逆序恢复已移动目录并以 `rollback_complete` 或
`rollback_failed` 回执结束。任何回执均声明 `deleteAuthorized=false`。

## 本地验证

`python3 -m unittest dev.tests.test_runtime_release_quarantine` 覆盖：

- 默认只读预检不创建隔离目录；
- 精确候选移动、活动指针保持和移动后读回；
- inventory 漂移失败关闭；
- 活动 release 不能生成批次；
- 多对象后续移动失败时自动恢复已移动对象；
- 缺少精确用户授权时拒绝批次。

这只是代码级验证；不构成服务器安装、隔离、删除、发布或业务验证证据。
