# CodexQuotaLogger：Codex 只读额度记录器

**安装前建议先看竞品：** 如果你的主要需求只是菜单栏/仪表盘查看额度，可优先考虑 [CodexMeter](https://github.com/raycalrui/CodexMeter)、[CodexBar](https://github.com/steipete/CodexBar) 或 [caut](https://github.com/Dicklesworthstone/coding_agent_usage_tracker)。本项目刻意只做更窄的用途：无界面后台运行、严格只读、跨 reset 安全的本地长期额度证据链，以及有上限的存储策略。详见 [竞品与定位](docs/COMPETITORS.md)。

**运行时只用 Python 标准库，不发模型消息，不调用 `codex exec`，不消费重置卡。** 这是账户总额度记录器；多人、多项目并发时，它不会自动猜测是谁消耗了额度。

## 已实现的方案

有额度更新通知时，重新读取完整状态；同时每 **5 分钟只读检查一次**，避免独立 app-server 收不到其他客户端通知。只在数值/窗口/计划等有意义的状态变化时写 CSV，每 **1 小时**仍写一条保底快照。

原始证据使用**去敏后的 JSONL**，保留最近 **30 个 UTC 日期**，旧日期可压缩为 gzip。程序自己管理的数据目录限制为 **250,000,000 字节**；优先删最旧的原始证据，长期 CSV 不自动删除。若长期 CSV 等不可删文件最终填满空间，则显示 `storage_full` 并暂停新增记录，保留已有数据。

“立即记录”不等于后台真实用量实时推送：未收到通知的变化，通常要等下一次 5 分钟检查；服务端也可能延迟更新。休眠、离线和安装前的历史无法补回。

## 第一步：只验证，不安装

需要已安装、已登录的 Codex CLI，以及 Python 3.10 或更新版本。

```sh
cd CodeXUsageLogger
python3 -B -m unittest discover -s tests -v
python3 quota_logger.py doctor
```

`doctor` 检查本机 Codex 版本与本机生成的协议 schema，然后进行**一次真实额度读取**。还会进行握手和 `refreshToken:false` 的账户元信息读取；不会发模型回合、不会保存额度历史，也不会安装/启动 LaunchAgent。schema 不可用时会如实报告，不假装已经核验。

Codex 不在 PATH 时：

```sh
python3 quota_logger.py doctor --codex /你的/Codex/可执行文件路径
```

**到此停下。先看 dry-run 结果，再决定安装。**

## 你批准后才执行

```sh
python3 quota_logger.py install --approve-install
python3 quota_logger.py start
python3 quota_logger.py status
```

安装步骤只写用户级 plist，不需要 sudo，也不自动加载；`start` 才加载服务。运行位置和 Python/Codex 路径会写入 plist，所以安装后请保持源码目录位置稳定。

其他操作：`snapshot` 单次只读查看；`stop` 停止并卸载当前服务；`restart` 重启；`uninstall` 移除 LaunchAgent、保留所有历史；`plist` 只预览；`run` 前台运行；`housekeeping` 只做本地原始日志整理。完整命令见英文 README。

## 文件与口径

默认目录：`~/Library/Application Support/CodexQuotaLogger/`

`quota_history.csv` 是需要长期保存的总额度历史。`raw/` 是保留 30 天的去敏原始证据；`health.json` 保存最后成功时间与状态；`operations*.jsonl` 是小型轮转日志。`identity.salt` 仅用于生成本地账户指纹，不是 API key 或登录凭证。

多额度桶全部保留，每次快照每桶一行，用 `sample_id` 关联；**不能把不同桶的百分比相加**。primary/secondary 按后台原名记录，用窗口长度判断含义，不写死“第一条=五小时、第二条=每周”。

重置时间、窗口、账户或计划变化会划分新区间；无解释的剩余额度回升会标为可能重置/校正。不会跨这些边界算消耗，不会把未知字段填成零。CSV 中 primary/secondary 各有独立区间 ID；日内窗口刷新不会自动切断仍连续的周窗口。

可选 `--usage` 每天额外读取一次后台 usage summary，单独保存；不假定后台日桶是北京时间，不把 token 统计当成周额度账单。

## 隐私与限制

程序使用固定只读 RPC 白名单。既不直接读写认证文件，也不保存邮箱、账户原始 ID、OAuth/API key、cookie、头部或任意错误原文；异常的服务端请求会让连接退出，不会自动批准。为了不把新字段中的隐私带入日志，“原始 JSON”只保留已知额度字段。

Codex 本身仍负责已有认证，可能正常刷新凭证或写自己的缓存；这些由 Codex 管理，不能宣称本工具能控制它的全部副作用或服务端计费。已验证的是**工具不请求任何模型回合**。

250 MB 是本工具目录中普通文件长度的总预算，包括 CSV、原始日志、状态和临时文件。文件系统元数据、APFS 快照、备份、导出副本、安装包、诊断用临时 schema，以及 Codex 在自己目录生成的缓存不属于这一上限。`status` 同时显示可取得的实际分配字节。

当前交付已通过 Linux 环境中的离线测试与模拟 app-server 测试；**你的 Mac、真实登录账户、LaunchAgent 加载尚待本机验证**。源码已发布到 `SamZebrado/CodeXUsageLogger`，但真实 dry-run 尚未替你执行。

后续计划：仅在有明确需要时增加独立的本地任务标记，继续保持账户额度与项目归因分开。
