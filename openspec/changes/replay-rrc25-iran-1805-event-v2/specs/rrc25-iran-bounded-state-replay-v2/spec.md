## ADDED Requirements

### Requirement: 固定 18:05–23:00 输入
系统 MUST 只使用 RRC25 `bview.20260228.0800.gz`、UTC `[08:00,10:05)` 的
25 个 UPDATE 和 `[10:05,15:00)` 的 59 个 UPDATE。输入 MUST 连续、角色正确、
gzip 可读且 size/SHA256 与冻结清单一致。

#### Scenario: UPDATE 缺少一个槽
- **WHEN** 84 个预期 UPDATE 中任一槽缺失、重复或错位
- **THEN** 系统在解析前失败关闭，不发布部分结果

### Requirement: 60 个逐槽状态
系统 MUST 输出 18:05 窗口起点状态和 59 个槽末状态，最后一个 observed_at 为
23:00。状态键 MUST 为 collector + VP + AFI/SAFI + canonical prefix。

#### Scenario: 边界时刻事件
- **WHEN** RouteEvent 时间恰等于五分钟槽终点
- **THEN** 事件进入下一半开槽，MUST NOT 改写前一槽状态

### Requirement: 固定 cohort 与双栈分类
系统 MUST 从 seed bview 冻结明确 IR origin 的 IPv4/IPv6 Prefix×VP 人口，并按
地址族计算 fully_visible、partially_visible、fully_invisible、unknown，再计算
双栈联合分类。动态 IR origin MUST 单独报告，不得改变分母。

#### Scenario: IPv4 不可见而 IPv6 可见
- **WHEN** ASN 的 IPv4 基线状态全部丢失但 IPv6 仍有可见状态
- **THEN** 系统将其联合分类为 partially_visible，并保留
  ipv4_invisible_ipv6_visible 标签

### Requirement: 原子文件包
系统 MUST create-only 原子发布 input summary、cohort、国家快照、ASN 状态、
route 状态、Incident/Episode/Wave、QUALITY 和中文报告。

#### Scenario: 第 40 个正式槽解析失败
- **WHEN** 已产生内存状态但尚未完成全部 59 槽
- **THEN** 系统删除自身 staging，不创建正式输出目录
