# Ledger Agent

本地优先的个人记账 Agent。用自然语言、图片或语音整理账单草稿；金额计算、账户余额、去重、确认写入、备份和审计全部由本地 Python + SQLite 完成。

> 仍处于早期开发阶段，适合个人自用和技术体验。它不是银行接入、自动付款、投资建议或税务软件。

## 为什么是 Agent

LLM 只负责理解你的意图、选择受控工具和生成草稿。它不能执行 SQL、付款或直接改写账本。每一笔会影响账本的数据，都要经过本地校验和你的确认。

```text
自然语言 / 图片 / 语音
  -> LLM 原生 Tool Calling
  -> Pydantic 参数校验与本地工具
  -> 待确认草稿
  -> 用户确认
  -> SQLite 事务、审计日志与备份
```

## 功能

- 对话、拖放图片、语音转文字记账；一句话可拆成多笔草稿。
- 本地搜索、月度汇总、时间段比较、消费趋势和周期性支出分析。
- 分类、商户记忆、重复账单检测、编辑、删除与撤销。
- 微信、支付宝、现金、银行卡等真实账户余额、转账和对账。
- 订阅、花呗/月付/信用卡等待还账单、信贷消费和实际还款。
- CSV 导入、完整 SQLite 备份恢复、操作日志和 Agent 调用日志。

## 五分钟开始

### 1. 创建环境

```bash
mamba env create -f environment.yml
mamba activate financial-agent
```

### 2. 配置模型（可选，但 Agent 对话需要）

```bash
cp .env.example .env
```

编辑 `.env`，选择一种 Provider：

- 火山方舟：填写 `ARK_API_KEY`，保留 `LEDGER_AGENT_PROVIDER=volcengine`。
- 任意 OpenAI-compatible 服务：填写 `LEDGER_AGENT_API_KEY`、`LEDGER_AGENT_BASE_URL`、`LEDGER_AGENT_MODEL`，并设为 `LEDGER_AGENT_PROVIDER=relay`。

`.env` 仅保存在本机，绝不能提交。

### 3. 启动 Web

```bash
bash scripts/start_web.sh
```

打开 http://127.0.0.1:8000 ，在聊天框输入：

```text
今天午饭 28 元微信
```

检查右侧草稿，确认后才会写入本地账本。停止服务：

```bash
bash scripts/stop_web.sh
```

## 核心概念

| 概念 | 含义 | 是否影响真实账户余额 |
| --- | --- | --- |
| 直接支付 | 微信、支付宝、现金、银行卡实际支付的消费或收入 | 是 |
| 信贷消费 | 花呗、月付、白条、信用卡发生的消费 | 否，增加待还 |
| 待还账单 | 某个信贷账户在某一账单月份的应还金额 | 否 |
| 实际还款 | 用真实账户偿还某张待还账单 | 是 |
| 账户转账 | 例如银行卡转微信 | 仅改变账户分布，不计收入或支出 |

信贷消费的**消费日期**和**账单月份**不同。例如 7 月消费、8 月 3 日还款，应记录为“消费日期 7 月、归属 8 月账单”。系统会根据已知还款日校验明显错误的月份归属。

## 常用方式

### 对话

```text
昨天午饭 28 元微信
美团月付买烤肉 54.9
搜索最近三个月的餐饮消费
分析我最近三个月为什么餐饮开支变多
微信转支付宝 100 元
```

图片可以拖进聊天输入框；语音按钮需要在 `.env` 中设置可选的 `GROQ_API_KEY`。

### 命令行

```bash
python src/financial_agent.py init
python src/financial_agent.py summary --month 2026-07
python src/financial_agent.py search 咖啡 --month 2026-07
python src/financial_agent.py where --month 2026-07 --group-by category
python src/financial_agent.py import ~/Downloads/wechat.csv --source wechat --dry-run
```

## 数据与安全

- 默认账本：`.financial_agent/ledger.db`。
- 备份：`.financial_agent/backups/`；可在界面中创建、下载和恢复。
- 聊天图片会保存到本地 SQLite，随备份一起保存；语音原文件不保存。
- 服务默认绑定 `127.0.0.1`。不要在没有身份认证和 HTTPS 的情况下改为 `0.0.0.0` 或公开部署。
- LLM 只收到完成当前任务所需的紧凑上下文；本地账本仍是唯一事实来源。

更多安全说明见 [SECURITY.md](SECURITY.md)。

## Windows / WSL

在 WSL 项目根目录运行以下命令创建 Windows 桌面入口：

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass \
  -File "$(wslpath -w "$PWD/scripts/install_windows_launcher.ps1")" \
  -Distro "Ubuntu-22.04" \
  -ProjectPath "$PWD"
```

创建每日 22:00 提醒：

```powershell
& "$env:LOCALAPPDATA\LedgerAgent\install_windows_reminder.ps1" -Time "22:00"
```

Windows 入口配置和提醒日志位于 `%LOCALAPPDATA%\LedgerAgent`，不属于项目文件，也不要上传。

## 开发

```bash
mamba env create -f environment.yml
mamba activate financial-agent
PYTHONPATH=src pytest -q
ruff check src tests
node --check web/app.js
```

默认测试不会调用真实模型。带 `integration` 标记的测试会使用外部 API，只在明确配置密钥后手动运行：

```bash
PYTHONPATH=src pytest -q -m integration
```

项目结构和 Agent 边界见 [docs/current_architecture.md](docs/current_architecture.md)，贡献方式见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## License

[MIT](LICENSE)
