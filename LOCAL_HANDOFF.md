# Luna Medium：本机适配与只读验收交接

请对现成的 **CodexQuotaLogger** 做本机验收；不要重新设计整个项目。当前源码已在独立 Linux 环境完成单元测试和模拟 app-server 测试，但没有在这台 Mac 上执行真实读取。

## 范围

1. 将本包放到专用、稳定的项目目录。检查 Python 版本（要求 >=3.10）、`codex --version`、实际 Codex 可执行路径。不要读取或输出认证文件、环境凭证或用户会话正文。
2. 先阅读 README、`docs/PROTOCOL.md`、`docs/QA.md`，运行 `python3 -B -m unittest discover -s tests -v`。
3. 运行 `python3 quota_logger.py doctor`，必要时仅用 `--codex` 明确指定可执行文件。该命令会用本机 schema 作能力检查，并执行 **一次真实 `account/rateLimits/read`**，另有 initialize/initialized 和 `account/read`（`refreshToken:false`）。不要手动再跑第二次 snapshot 来增加样本。
4. 检查输出：本机 schema 状态、额度桶/窗口/重置时间、只读请求计数、`model_turn_methods_sent=0`；与用户已经打开的 Usage 页面做人工对照即可，不启动浏览器自动化或读浏览器凭证。
5. 必要适配只限本机路径、协议兼容性和真实发现的 bug。任何修改都补测试，保持固定只读白名单，不能用模型线程“触发额度更新”，不能消费 banked reset。
6. 到此停止并报告：`CODEX_QUOTA_LOGGER_LOCAL_DRY_RUN = PASS` 或 `BLOCKED`；列出版本、测试结果、实际只读读数、schema 状态、变更文件和剩余限制。没有真实读到额度就不能写 PASS。

## 不能做

- 不运行 `codex exec`、`thread/start`、`turn/start`、resume、任何模型请求或消费重置卡接口。
- 不安装/加载 LaunchAgent，除非用户在看到 dry-run 后明确批准。
- 不修改 `~/.codex` 配置/认证，不读取 token/cookie/key，不 sudo，不安装新客户端或抓 Usage 网页。
- 不启动其他项目，不抢 Bridge、不调 Android、不新建多 agent 调度系统。
- 不上传额度 CSV、原始日志、live dry-run 输出、salt 或真实账户数据到 GitHub。

## 需要保留的设计

- 通知触发完整只读快照；5 分钟轮询兜底其他客户端用量；变化才记录，1 小时强制心跳。
- CSV 长期保留，原始 JSONL 最近30个UTC日期，旧日期gzip。
- 管理目录总预算250,000,000字节；原始证据先淘汰，保护CSV，保护数据占满后停止新增记录。
- account-level 实测，不对项目/person/model自动归因。

## GitHub

源码已发布到 `SamZebrado/CodeXUsageLogger`。这是公开仓库；**不要**上传额度 CSV、原始日志、live dry-run 输出、salt、真实账户数据、认证信息或本机路径。

本地 dry-run 如果需要修改源码，只提交必要的兼容性修复和测试；push 前先跑完整离线测试并检查 staged diff。参考 `docs/GITHUB.md`。先完成本次 dry-run 报告，不把 LaunchAgent 安装、Pages 或其他发布动作混进来。
