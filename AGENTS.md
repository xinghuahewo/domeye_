# 项目协作约定

- 生成和维护的项目文档统一使用中文。
- `backend/core/` 是从原项目等字节迁移的核心检测实现，迁移阶段不得修改其业务逻辑。
- 修改外围代码后，应在 `backend/` 目录执行 `sha256sum -c core.sha256`，确认核心文件未发生变化。
- `/home/bgpdata/Domeye` 仅作为原始参考和只读数据来源；新功能、配置与部署均在 `/home/bgpdata/Domeye-Core` 中进行。
