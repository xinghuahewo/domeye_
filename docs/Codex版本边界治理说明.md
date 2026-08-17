# Codex 版本边界治理说明

## 目标

本机制把“不要混用旧版”从会话提醒改成可验证的工程边界。每个任务同时绑定：

- 唯一 Worktree；
- 唯一任务分支；
- 40 位基线提交；
- 目标版本；
- 允许修改路径；
- 禁止修改路径；
- 当前 Worktree 内的权威参考；
- 禁止依赖规则；
- 定向完成检查。

`AGENTS.md` 规定操作纪律，`.codex/TASK.json` 约束单个任务，
`config/codex-version-boundaries.json` 约束整个仓库，
`dev/codex_task_guard.py` 和 CI 负责失败关闭。

本文件解决“单个任务不能混用版本”；唯一生产主干、服务器端分支保护、不可变制品
晋级、发布身份归一和 Worktree 退役由
[主干开发与发布归一治理规范](主干开发与发布归一治理规范.md)统一约束。任务边界
通过不代表提交已进入生产主干，更不代表已经构建、认证、部署或完成生产验证。

## 一、为任务创建独立 Worktree

从明确的远端分支或完整提交创建任务分支，不在已有任务目录中切换版本：

```bash
git fetch origin --prune
git worktree add \
  -b codex/<任务名> \
  ../<独立任务目录> \
  <明确的基线引用>
```

进入新 Worktree 后先确认：

```bash
pwd
git rev-parse --show-toplevel
git branch --show-current
git rev-parse HEAD
git status --short
git worktree list --porcelain
```

为不同 Worktree 分配不同的端口、运行目录、日志目录、临时目录、构建输出目录、
数据库或 schema，以及容器项目名。代码目录隔离不等于运行时隔离；旧服务仍在
监听或多个版本共用状态目录时，页面和测试仍可能读取错误版本。

## 二、创建本地任务合同

复制模板：

```bash
cp .codex/TASK.example.json .codex/TASK.json
```

`.codex/TASK.json` 已被 Git 忽略，不进入正式代码。必须填写真实值，尤其是：

- `worktreeRoot`：当前 Worktree 的绝对路径；
- `targetBranch`：当前任务分支；
- `baseCommit`：开始任务时冻结的 40 位提交；
- `allowedPaths`：本任务可以修改的最小路径集合；
- `forbiddenPaths`：即使接口相似也不能触碰的旧版、冻结核心和生产路径；
- `authoritativeReferences`：只能引用当前 Worktree 内的合同、测试、代码或文档；
- `requiredChecks`：只列与本次影响范围相符的测试，不机械扩大为全量重跑。

路径模式使用仓库相对路径。目录可写成 `path/to/tree/**`，单文件写完整路径。
禁止使用绝对路径和 `..`。

`forbiddenContentRules` 可阻止当前版本直接导入旧模块。规则只扫描本任务改动的
UTF-8 文本文件，命中后报告文件与行号。不要用它替代类型检查或架构测试。

## 三、开始前检查

```bash
make codex-preflight
```

preflight 会失败关闭：

- 当前目录不是合同声明的 Worktree；
- 当前分支不匹配或处于 detached HEAD；
- 基线提交不存在；
- 当前 HEAD 不是基线后代；
- 权威参考不存在；
- 除本地任务合同外已经存在暂存、未暂存或未跟踪改动。

preflight 还会把任务合同 SHA-256、Worktree、分支和基线写入当前 Worktree
专属的 Git 状态目录。postflight 必须读取同一封存状态；任务中途修改合同、
替换分支或换到另一 Worktree 都会失败。需要扩大范围时，应停止当前任务并由
用户重新确认，随后创建新的任务 Worktree，不能删除封存状态继续执行。

通过后，在这个 Worktree 中启动一个新 Codex 会话。不得在旧版本会话中切换
目录后继续实现。

## 四、结束前检查

```bash
make codex-postflight
```

postflight 汇总相对固定基线的：

- 已提交改动；
- 已暂存改动；
- 未暂存改动；
- 未跟踪文件；
- 重命名的旧路径与新路径。

随后检查仓库级冻结路径、任务级禁止路径、任务允许路径、禁止依赖规则，并执行
任务合同内的定向检查。任何一项失败都不得宣告完成。

最后仍需人工审查：

```bash
git diff --stat <基线提交>
git diff --name-status <基线提交>
git diff <基线提交>
```

本地 postflight 通过只证明当前 Worktree 的边界和列出的检查通过，不证明改动
已经提交、推送、合并、发布或部署。

## 五、CI 仓库级门禁

Pull Request CI 执行：

```bash
python3 dev/codex_task_guard.py policy --base-ref <PR 基线提交>
```

CI 不读取被忽略的本地 `.codex/TASK.json`，因此只负责永久仓库边界。目前冻结：

- `backend/core/**`；
- `backend/core.sha256`。

上述两项同时内建在检查器中，不能仅通过修改 JSON 策略删除。仍应通过受保护
分支和评审权限保护检查器、策略与 CI 工作流本身，防止同一 Pull Request
同时削弱门禁并越界修改。

任务级允许路径必须由本地 postflight 证明。若未来需要让 CI 验证每个任务的
允许路径，应引入经过评审的 PR 清单或签名审批记录，不能让提交者在同一 PR
中任意扩大自己的允许范围。

## 六、合并或迁移任务

普通实现任务禁止切换分支、合并、rebase、cherry-pick 和 reset。确需跨版本
迁移时，创建独立迁移 Worktree 和新会话，并在任务合同中明确：

- 源版本和目标版本的完整提交；
- 只读源路径；
- 目标写入路径；
- 允许存在的显式适配器；
- 必须保持的兼容要求；
- 迁移完成后禁止残留的依赖模式；
- 对固定双基线的差异审查方式。

不得直接复制兄弟 Worktree 的未提交文件，也不得把旧生成物当成当前源码。

## 七、进入生产主干与 Worktree 退役

普通任务 postflight 通过后只进入评审状态。合入生产主干必须由独立整合或发布任务
执行，并确认：

1. 目标为永久生产主干 `main`；
2. 源提交已推送且工作树干净；
3. 任务合同、定向检查和人工差异审查均有证据；
4. 服务器端 Hook 所需的 root 持有评审与 CI 证明已经就绪；
5. 没有把未评审兄弟 Worktree、实验提交或现场文件带入主干；
6. 主干更新只能快进，合并只证明 `merged`；
7. 后续仍需正式 tag、一次构建、同制品晋级和生产归一验证。

任务 Worktree 不因提交进入主干而自动删除。退役前必须确认其 HEAD 已是
`main` 的祖先，或已经形成明确的 `excluded` / `abandoned` 决议；同时确认
无未提交业务文件、提交已推送、证据已保留，并且不被活动发布、回滚、锁、挂载或
进程引用。清理必须先输出 dry-run 清单，再逐个精确执行 `git worktree remove`。

带发布编号的临时 Worktree 不能承担长期主干职责。永久生产身份只有
`main`、其 annotated release tag 和从该提交生成的不可变运行制品。禁止通过
重写历史、强推、复制目录或修改证据文件伪造归一结果。
