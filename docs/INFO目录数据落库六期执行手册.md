# INFO 目录数据落库六期执行手册

## 一、阶段目标

S6 将普通运行入口固定为仓库外原子状态指定的数据库 release，并用系统调用追踪证明：

- API 精确查询、静态快照、六类检测和普通后台查询只连接隔离候选库；
- 每个运行进程只消费一个 `content_id` 和一个 `release_sk`；
- 普通运行入口没有隐式文件回退；
- 对旧 INFO 来源和当前四文件制品目录的直接读取次数为 0；
- IPv4/IPv6 数据库连接次数为 0，只使用候选库 Unix Socket；
- 至少 12 次运行覆盖四类进程且每类不少于 3 次；
- 一个隔离验收观察周期不少于 60 秒；
- 当前数据库 release、文件回滚后端、状态日志、失败证据和原始来源均保持完整；
- 不执行删除、分区清理或垃圾回收。

该观察周期是隔离候选的最终效果验收周期，不是生产服务观察，也不授予生产激活。

## 二、普通运行入口

`backend.info_pipeline.runtime.open_pinned_database_runtime` 是收口后的数据库运行入口：

1. 只接受普通文件形式的 v1 原子状态；
2. 状态必须明确为 `backend=database`；
3. 状态的 `content_id`、manifest、`release_sk` 必须与数据库 `core` 活动指针一致；
4. 每次打开都得到一个独占连接和 release-pinned 快照；
5. 状态为文件模式时失败关闭，不在普通运行路径自动读取文件。

文件回滚仍保留，但只能由 S5 已验收的独立控制边界显式启用。

## 三、追踪口径

每个普通运行进程由 `strace -f -e trace=%file,connect` 记录：

- 追踪行包含旧 INFO 来源路径或当前四文件制品路径，计为一次直接文件访问；
- 出现 `AF_INET` 或 `AF_INET6` 的 `connect`，计为一次非候选 Unix Socket
  数据库连接；
- `AF_UNIX` 连接只用于当前隔离候选库；
- 维护态来源哈希复核不计作普通运行读取，必须在证据中单独标识。

全部原始 `.strace` 文件保留在 S6 证据目录，并由嵌套 SHA256 清单覆盖。

## 四、执行

在隔离迁移仓库根目录执行：

```bash
./deploy/database/accept-static-info-s6.sh \
  /home/bgpdata/Domeye/backend/info \
  /home/bgpdata/Domeye-Core/backend/info \
  domeye_info_s1_v5_20260725T131422Z \
  postgres \
  bgp_project \
  /home/bgpdata/Domeye-Core/backend \
  /home/bgpdata/Domeye-Info-Migration/20260725T131422Z-s1-v5/evidence/S5 \
  /home/bgpdata/Domeye-Info-Migration/20260725T131422Z-s1-v5/runtime/static-info \
  /home/bgpdata/Domeye-Info-Migration/20260725T131422Z-s1-v5/evidence/S6
```

当前四文件制品目录可以不存在；它仍会作为禁止访问路径纳入系统调用追踪。可用的文件
回滚后端以完整只读来源目录及其 24 文件 manifest 为准。

## 五、最终出场

S6 只有同时满足以下结果才能运行最终 Hook：

- `final_acceptance_status=pass`；
- `passed_requirement_count=12`；
- `runtime_direct_info_file_read_count=0`；
- `legacy_database_connection_count=0`；
- 当前 release、文件后端和回滚制品均可用；
- 观察周期完成、四类运行进程覆盖完整；
- 引用中的内容和失败证据保持完整；
- `cleanup_performed=false`；
- S6 Hook 中 FA-01 至 FA-12 全部为 `pass`。

最终通过只代表同一内容身份的隔离候选已达到设计效果。生产激活仍需独立发布授权。
