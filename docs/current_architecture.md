# 个人记账 Agent 当前架构

更新时间：2026-07-22

## 1. 系统定位

这是一个本地优先、单用户、自用的记账 Agent。系统把职责分成两部分：

- LLM 负责理解自然语言、识别意图、拆分账单、生成分类和追问。
- 本地 Python + SQLite 负责校验、去重、确认、写入、查询、统计、审计、撤销和备份。

LLM 不是账本，也不能直接执行 SQL、付款、转账或投资操作。SQLite 是唯一账本事实来源。

## 2. 运行拓扑

```text
浏览器
  |
  | HTTP / JSON (127.0.0.1:8000)
  v
FastAPI: src/web_app.py
  |
  +-- 应用服务: src/services/chat.py
  |      +-- 请求幂等 / 进程内占用锁 / 中断恢复
  |      +-- services/transcription.py：短语音转文字（不持久化音频）
  |
  +-- Agent Runtime: src/agent/
  |      +-- ToolCall / ToolResult / Registry / Runner
  |      +-- 原生工具循环 / Checkpoint / Token usage
  |
  +-- 账本领域: src/ledger/
  |      +-- 交易 / 待还 / 订阅 / 账户 / 查询 / 可观测性
  |
  +-- 应用组装与兼容入口: src/financial_agent.py
  |      |
  |      +-- OpenAI-compatible SDK
  |      |      +-- 任意 OpenAI-compatible 服务
  |      |      +-- 火山方舟
  |      |
  |      +-- SQLite: .financial_agent/ledger.db
  |
  +-- 静态前端: web/index.html + app.js + styles.css
```

正式服务默认只监听 `127.0.0.1:8000`。启动、停止和重启分别由 `scripts/start_web.sh`、`stop_web.sh` 和
`restart_web.sh` 管理。`scripts/runtime.sh` 优先使用显式指定或当前激活的环境，再通过 mamba 环境表和常见
Miniforge 安装目录查找 `financial-agent`，因此不依赖固定用户名或 Python 路径。默认以 `setsid` 启动独立
后台 Uvicorn 进程，避免 WSL 的临时 user systemd 单元在桌面启动脚本退出后被关闭。只有显式设置
`LEDGER_AGENT_USE_SYSTEMD=1` 时才使用临时 `ledger-agent-web.service` 单元；项目外不会生成永久
service 文件。

Windows 双击入口由 `scripts/install_windows_launcher.ps1` 安装。安装器接收 WSL 发行版和 Linux 项目路径，
把通用启动脚本与本机 `launcher.json` 写入指定的 Windows 安装目录，并创建桌面快捷方式。
`open_ledger_agent.ps1` 只读取该配置，不包含开发者用户名或盘符。每日提醒安装器复用同一配置，并把计划任务
包装脚本和日志写入独立的 Windows 运行目录。

## 3. 代码模块

### `src/financial_agent.py`

当前应用组装模块和兼容入口。它保留原有公开导入路径，负责 SQLite 建表与兼容迁移、Provider/模型配置、
分类与商户记忆、CSV 导入导出、备份恢复、审计及 CLI；交易、待还、订阅、账户和 Agent 执行已迁出。

- SQLite 建表与兼容迁移。
- Provider、模型选择和 OpenAI-compatible 调用。
- 分类、支付方式、别名与商户分类记忆。
- CSV 导入、CSV 导出、SQLite 备份与恢复。
- 操作审计、整批撤销和 CLI 命令入口。

### `src/agent/`

框架无关的 Agent Runtime：

- `contracts.py`：`ToolCall`、`ToolResult`、`ToolStep`、风险等级和运行策略。
- `registry.py`：统一工具注册、重复检测和模型可读 Schema。
- `runner.py`：参数校验、工具白名单、确认门、步骤限制、运行时限和步骤 Hook。
- `ledger_tools.py`：十六个账本工具的 Pydantic 输入 Schema、描述、风险和确认元数据；也是模型能力与字段约束的唯一来源。
- `native.py`：Chat Completions 与 Responses 的原生多步工具循环。
- `checkpoints.py`：工具历史的可序列化 Checkpoint 协议。
- `usage.py`：两类 API 的 token usage 标准化和调用观察器。
- `models.py`、`action_mapping.py`、`prompts.py`：Agent 上下文/动作、工具映射和系统 Prompt。

这层不依赖 LangChain 或特定模型厂商，领域函数仍由本地 Python 和 SQLite 执行。

### Agent 性能策略

- 每次调用只发送紧凑上下文：近期对话摘要、相关商户记忆、与当前文本匹配的订阅/待还账户和资金账户；历史工具结果仍按需通过 SQLite 工具读取。
- 对表达明确的普通收支、信贷、还款、转账和常见查询，运行时从 Pydantic 工具注册表选择最小工具子集；表达模糊或图片输入仍保留完整工具集。
- 信贷消费在上下文中存在唯一匹配账户时，可直接提交带账户 ID 的草稿；没有唯一匹配时才读取待还清单。
- `agent_model_calls` 记录每次模型请求的输入/输出 token、模型调用耗时和工具步骤耗时，便于区分模型等待与本地执行时间。当前调用为非流式工具调用，因此不伪造“首 token”指标。

### `src/ledger/`

- `models.py`：账单草稿和重复账单错误模型。
- `queries.py`：月度范围、明细搜索、支出聚合和月度汇总，只读访问 SQLite。
- `observability.py`：工具步骤、模型调用、Checkpoint、token 和费用估算持久化。
- `transactions.py`：收入、支出、搜索、导入、编辑、删除、重复检测和批量撤销。
- `liabilities.py`：待还账单、信贷消费、实际还款、还款修改与历史结转。
- `subscriptions.py`：订阅、确认扣款、跳过一期和下次扣款日推进。
- `accounts.py`：账户余额、账户转账和月度对账。

### `src/agent/context.py`、`tools.py` 与 `runtime.py`

- `context.py`：按当前输入装载紧凑上下文，包括相关商户、分类、账户、订阅和待还账户。
- `tools.py`：把原生工具调用映射为本地账本领域函数，并在写入前创建可编辑草稿。
- `runtime.py`：工具选择、原生函数调用执行、重试、观察日志和恢复执行。

### `src/services/chat.py`

Web 聊天用例的应用服务。负责请求幂等、进程内防重复执行、上下文装载、结果持久化、恢复执行和运行日志；
`web_app.py` 只保留 HTTP 参数、状态码和响应适配。

### `src/services/transcription.py`

语音输入的独立适配层。浏览器只把短录音上传到本地 FastAPI，服务通过 Groq 的
OpenAI-compatible 转写接口返回文字，不在 SQLite、聊天记录或项目目录保存音频文件。
转写结果仍由用户确认后才会进入 Agent。

### `src/web_app.py`

FastAPI 适配层，负责：

- Pydantic 请求校验。
- 将 HTTP 请求映射到本地领域函数。
- 将重复账单转换为 `409 Conflict`。
- 记录 Web 发起的操作来源和 Agent 调用状态。
- 提供静态页面和 `/api/docs`。

### `web/`

无构建步骤的原生 Web UI：

- `index.html`：七个视图及对话框结构。
- `app.js`：API 调用、草稿编辑、页面状态和交互。
- `styles.css`：桌面和移动端响应式样式。

### `tests/`

- `test_financial_agent.py`：确定性领域逻辑和 SQLite 边界。
- `test_web_app.py`：FastAPI 接口契约。
- `test_llm_integration.py`：真实外部模型调用，默认不执行。

## 4. Agent 动作协议

当前模型只能选择十七种安全工具动作：

| Action | 用途 | 最终执行者 |
| --- | --- | --- |
| `clarify` | 金额、日期或拆分方式不明确时追问 | LLM 生成问题 |
| `record` | 生成一条或多条账单草稿 | LLM 解析，本地确认写入 |
| `summary` | 月度收支汇总 | SQLite |
| `plan` | 预算和储蓄建议 | SQLite 计算 + 本地规则 |
| `search` | 查询具体账单 | SQLite |
| `where` | 按分类、商户或支付方式聚合 | SQLite |
| `analyze` | 比较 2–12 个月的支出趋势和增长来源 | SQLite 计算 + LLM 解读 |
| `compare` | 比较任意两个明确日期区间的支出变化 | SQLite |
| `recurring` | 识别周期性支出候选 | SQLite |
| `subscriptions` | 查询订阅清单和当月预计扣款 | SQLite |
| `liabilities` | 查询信用卡、花呗、月付和分期的月度应还与未还金额 | SQLite |
| `accounts` | 查询真实资金账户余额与最近对账差异 | SQLite |
| `management` | 生成订阅建立/扣款/跳期、待还账单/还款、账户转账草稿 | 本地校验，用户确认后写入 |
| `report` | 月度复盘 | SQLite 计算，LLM 可组织文字 |

付款、投资、贷款和报税不属于可执行动作，只能转为规划建议。账户转账仅写入本地账本：用户明确给出双方账户、金额和日期后，仍须确认草稿；不会调用任何支付平台或实际划款。

待还采用“账户 + 月度账单”结构：`liabilities` 保存花呗、信用卡等长期账户元数据，
`liability_statements` 按 `liability_id + month` 保存每月原始应还、本月未还、还款日和最低还款，
`liability_payments` 通过 `statement_month` 归属到具体月份。登记还款只减少该月的本月未还，
不会改写原始应还，也不会新增支出流水。`statement_month` 与 `due_date` 相互独立，个人欠款可以只有
归属月份而没有固定还款日，此时不会进入逾期统计。月度概览展示当前本金、当前负债、本月收入、
本月支出、本月已还、本月待还和本月现金变化。
`本月现金变化 = 本月收入 - 本月支出 - 本月实际还款`，普通支出与实际还款都会按发生月份减少现金。
“本月已还”和“本月待还”按账单的 `statement_month` 归属；现金变化和本金则按还款的 `paid_at`
实际发生月份扣款。例如 7 月提前偿还 8 月账单时，7 月现金变化扣款，8 月“本月已还”显示该金额。
还款现金流不会写成消费支出，因此分类统计不会重复计算原消费；本月已还和本月待还也会在待还页面展开。
月度概览的“最近资金记录”按实际发生日期合并展示收入、支出、还款与转账；还款会额外标明归属账单月份，
也可以通过 Agent 的账本搜索查询，但不会因此进入消费支出或分类统计。
“资金明细”同样按实际发生日期展示收入、支出、还款与转账，并可按四种资金类型筛选。转账明确标记为不计入收支。还款记录可跳转到
对应待还账户及原账单月份；新增或调整欠债本身没有产生现金流，因此只在待还页面和操作日志展示。
还款记录可修改金额、实际日期、还款方式和备注，也可整笔撤销。每次修改都会根据该原账单的全部
还款记录重新计算已还与未还，并按实际还款日期更新本金和月度现金变化，避免增量修正产生漂移。

聊天上传的 PNG、JPEG 和 WebP 图片会作为 `message_attachments` BLOB 保存在本地 SQLite 中，
与用户消息和请求 ID 关联；单张上限为 6 MB、单次最多 3 张。聊天历史只返回附件元数据和不可猜测的
本地读取 URL，不在 JSON 中返回 Base64，因此刷新页面可以恢复缩略图，同时避免历史接口响应膨胀。
图片会随 SQLite 账本备份一并保存，不会写入 Web 静态目录。
聊天页可清空当前会话的消息、图片附件、聊天请求和 Agent 会话状态；账本、待还、订阅、分类、支付方式
以及操作和 Agent 日志不受影响。清空后会尝试压缩 SQLite 文件以回收附件占用的磁盘空间，正在运行的
请求必须先停止，避免执行结果写回已清除的会话。

本金是独立的可用资金，不属于待还账户。现在本金等于所有启用的真实资金账户余额之和：微信、支付宝、银行卡、现金及用户新增账户。
每个账户以最近一次实际余额对账为基准，再叠加该时点之后的收入、支出、实际还款和账户转账；补录对账日期之前的历史账单不会反向改写今天的余额。
转账只从转出账户减去、向转入账户增加，不计入收入、支出或分类统计。待还本身不会扣本金，只有实际登记还款且指定真实还款账户后才减少该账户余额。
旧版 `capital_anchors` 首次迁移为“待分配余额”账户，可通过对账和转账逐步分配到真实账户，总本金不会被静默改变。
月度概览中的“当前负债”汇总所有月份仍未偿还的账单，“本月待还”则只统计当前选择月份。

Web 录入月度账单时先选择已有待还账户；只有确实不存在时才新建账户。同名账户会被本地校验拒绝，
已发生还款的账单也不能把原始应还改到低于累计已还金额。
待还列表同时展示累计已还、还款次数，以及该账单最近一次实际还款的日期和金额。
没有固定还款日且尚未结清的历史账单，会在后续月份作为“历史结转待还”虚拟展示。结转不会创建
新的月度账单，也不会重复增加当前负债；其金额与笔数和当前月份账单分开汇总，还款仍写回原账单月份。
待还页面始终列出所有已有账单月份，月份之间可以直接切换；新增未来月份账单不会自动复制其他月份金额。

订阅确认扣款会原子写入一笔 `source=subscription` 的支出并推进下次扣款日。此类支出不能通过普通
账单编辑、删除或通用撤销处理；资金明细提供“撤销订阅扣款”，一次性软删除支出并回退订阅日期。
若已有后续扣款或订阅计划在扣款后被修改，系统拒绝自动回退，避免覆盖后续状态。

`analyze` 是受控的多步只读链路。模型先识别截止月份、比较周期和目标分类；本地随后计算月度金额、
笔数、均笔金额、分类变化、商户变化和末月大额明细；最后由当前对话模型根据统计数据组织结论。
模型不能自行查询 SQL，也不能把账本相关性描述成现实因果。

### 原生工具协议

Agent 只支持原生工具调用。`ToolSpec` 中的 Pydantic 输入模型、工具描述、风险等级和确认要求是工具定义的
唯一来源；注册表分别将同一份定义转换为 Chat Completions 与 Responses API 所需的 `tools` 参数。

支持的两种原生 API 形态：

```text
Chat Completions: assistant.tool_calls -> role=tool
Responses API: function_call -> function_call_output + previous_response_id
```

模型不支持工具、没有调用工具或返回参数不符合 Pydantic Schema 时，请求会明确失败，不再回退到手写 JSON
路由协议。`record_transactions` 在 Web 中始终以预览方式执行，并在返回草稿后停止循环。

## 5. 对话记账链路

```text
用户输入
  -> POST /api/chat
  -> 从 SQLite 加载 AgentContext
  -> 立即保存用户消息和 pending 请求
  -> 关闭数据库连接
  -> LLM 根据 Pydantic Tool Schema 返回原生 ToolCall
  -> 重新打开数据库
  -> AgentRunner 用同一 Pydantic Schema 再次校验参数
  -> 本地验证日期、金额、方向、分类和字段长度
  -> 本地应用分类/支付方式归一化和商户记忆
  -> Web 展示可编辑草稿
  -> 用户确认
  -> 重复检测
  -> SQLite 事务整批写入
  -> audit_log 记录单笔和批次
```

`record` 永远先产生草稿。LLM 没有直接写账本的权限。

## 5.1 标准工具执行协议

模型直接返回标准工具调用，`AgentAction` 只作为现有 UI 状态的展示适配对象：

```text
Pydantic Tool Schema -> 原生 ToolCall(name, arguments, call_id)
  -> Pydantic Schema 校验
  -> Policy（白名单、风险、确认、步骤/时间上限）
  -> Tool Handler
  -> ToolResult
  -> ToolStep 持久化
  -> AgentAction（UI / 对话状态适配）
```

当前工具映射：

| Agent 动作 | 工具 | 风险 |
| --- | --- | --- |
| `clarify` | `ask_clarification` | 只读 |
| `record` | `record_transactions` | 写入，必须确认 |
| `summary` | `get_month_summary` | 只读 |
| `plan` | `create_budget_plan` | 只读 |
| `search` | `search_ledger` | 只读 |
| `where` | `aggregate_spending` | 只读 |
| `analyze` | `analyze_spending_trend` | 只读 |
| `compare` | `compare_spending_periods` | 只读 |
| `recurring` | `find_recurring_expenses` | 只读 |
| `subscriptions` | `get_subscriptions` | 只读 |
| `liabilities` | `get_liabilities` | 只读 |
| `accounts` | `get_account_balances` | 只读 |
| `management` | `propose_subscriptions`、`propose_subscription_charge`、`propose_subscription_skip`、`propose_liability_statement`、`propose_liability_payment`、`propose_account_transfer` | 写入，必须确认 |
| `report` | `generate_monthly_report` | 只读 |

Web 对话中的 `record_transactions` 固定以 dry-run 执行，只返回草稿。真正写入仍由用户点击确认后调用
账单确认 API。订阅和待还工具同样只返回草稿；确认时由管理草稿 API 在一个 SQLite 事务中重新校验后写入。非交互调用默认拒绝未经批准的写工具，只有 CLI 对话显式允许终端确认。

每次 Web 请求携带唯一 `request_id`。刷新页面后，前端通过聊天历史和请求状态接口恢复：

- `pending`：重新显示响应中状态并轮询。
- `awaiting_confirmation`：恢复右侧待确认草稿。
- `completed`：历史消息正常展示。
- `error`：展示经过脱敏的错误消息。
- `dismissed`：草稿已由用户取消。

原生工具模式会在每次模型调用前、每批工具结果返回后保存 SQLite Checkpoint。进程中断后，页面轮询发现
请求不再由当前进程执行，会调用恢复接口并继续使用已保存的 Chat 消息，或 Responses 的
`previous_response_id + function_call_output`。已写入 Checkpoint 的工具结果不会再次执行。Web 写账工具始终只生成草稿，
因此恢复链路不会绕过人工确认写入真实账本。

## 6. 上下文与记忆

### 短期上下文

- `sessions`：会话标识和更新时间。
- `messages`：最近对话消息。
- `agent_state`：最近动作、月份、焦点和压缩结果。
- `preferences`：默认支付方式等长期偏好。

每轮默认读取最近 10 条消息。账本明细不会整体放入 prompt；查询仍通过 SQL 工具完成。

### 长期分类记忆

`merchant_category_rules` 保存结构化映射：

```text
标准化商户键 -> 显示名称 -> 分类 -> 确认次数
```

分类优先级为：

```text
用户手动修改/确认
  > 用户批准的新分类提案
  > 本地商户分类记忆
  > LLM 分类
  > 待分类
```

SQLite 保存全部商户规则，但只把当前输入或最近用户消息中实际出现的相关规则注入 LLM，最多 10 条。写入前仍会在本地执行精确商户规则匹配。

当前不使用向量数据库。商户记忆是结构化精确映射，SQLite 更容易检查、修改和撤销。

## 7. 智能分类链路

### 对话草稿

模型获得：

- 当前标准分类及别名。
- 相关的已确认商户规则。
- 当前用户输入和有限对话上下文。

模型返回：

- `category`
- `category_confidence`
- `category_reason`
- `classification_source`

模型返回不存在的分类时，本地将其转为“待分类”。低于默认阈值 `0.85` 时，草稿标记为需要确认。

现有分类确实不合适时，模型可以返回 `proposed_category`。本地只把它作为草稿提案展示，不修改分类表。用户点击“新增并应用”后，Web 才调用分类管理 API 创建分类并把草稿改为人工确认来源。

### 批量处理待分类账单

“账本设置 -> 智能分类”执行：

1. 先匹配本地商户规则。
2. 只把仍未匹配的商户/用途和备注发给当前 LLM。
3. 高置信度结果自动写回。
4. 低置信度结果保留“待分类”和建议分类。

该批处理不发送金额、日期、支付方式或完整原始账单，也不会自动联网搜索。

## 8. SQLite 数据结构

| 表 | 职责 |
| --- | --- |
| `transactions` | 账单事实、来源、重复指纹和分类元数据 |
| `budgets` | 月份 + 分类预算 |
| `categories` | 标准分类、别名、常用状态和顺序 |
| `payment_methods` | 标准支付方式、别名、常用状态和顺序 |
| `accounts` | 真实资金账户及类型；当前本金是其余额合计 |
| `account_reconciliations` | 各账户的实际余额快照、账面余额和对账差异 |
| `transfers` | 账户间内部转账；不属于收入或支出 |
| `merchant_category_rules` | 用户确认的商户长期分类记忆 |
| `audit_log` | 账单、预算、分类、设置、备份等操作记录 |
| `agent_runs` | Provider、模型、工具模式、动作、状态、耗时和错误摘要 |
| `agent_steps` | 每轮 Agent 的工具名、风险、状态、耗时和脱敏错误 |
| `agent_model_calls` | 每次模型请求的 API 类型、token、响应 ID 和可选费用估算 |
| `agent_checkpoints` | 多步工具循环的恢复状态；结束后清空具体状态，只保留完成/错误标记 |
| `sessions` | 对话会话 |
| `messages` | 对话消息 |
| `preferences` | 长期用户偏好 |
| `agent_state` | 每个会话的最近状态 |
| `chat_requests` | 可恢复的聊天请求状态、结果和错误摘要 |
| `inbox_items` | 稍后处理的自然语言待办，状态为待处理、处理中或已归档 |

`agent_runs` 同时保存单轮聚合后的模型调用次数、输入/输出/缓存/推理 token 与估算费用。API 自带费用时优先
使用；否则只有配置 `LEDGER_AGENT_MODEL_PRICING_JSON` 后才估算，不会硬编码可能变化的供应商价格。

账单删除使用软删除字段 `deleted_at`。普通查询、统计和重复检测排除已删除账单。

## 9. 可靠性边界

- 金额必须大于 0，日期必须是 ISO 日期，收支方向只能是收入或支出。
- 同日期、金额、方向、分类、支付方式和商户形成重复指纹。
- 命中重复时默认拒绝，用户二次确认后才能保留真实重复消费。
- 一次最多确认 20 条草稿，使用单个 SQLite 事务和 `batch_id`。
- CSV 导入也使用整批事务，失败时整批回滚。
- 分类改名/合并同步账单、预算、建议分类和商户规则。
- 分类合并遇到同月份预算冲突时停止，不自行相加或覆盖。
- 撤销整批新增时整批软删除，并同步重建商户记忆。
- SQLite 备份使用原生 backup API，并执行 `PRAGMA integrity_check`。

## 10. HTTP API 分组

- 运行状态：`/api/health`、`/api/settings/model`、`/api/agent/tools`
- Agent：`/api/chat`、`/api/chat/image`
- 聊天恢复与实时状态：`/api/chat/history`、`/api/chat/requests/{request_id}`、`/api/chat/requests/{request_id}/events`、`/api/chat/requests/{request_id}/resume`
- 待处理：`/api/inbox`
- 账单：`/api/transactions*`、`/api/undo*`
- 汇总：`/api/dashboard`
- 账户与对账：`/api/accounts*`、`/api/transfers`
- 预算：`/api/budgets*`
- 分类和支付方式：`/api/references*`
- 智能分类：`/api/transactions/classify-pending`
- 备份：`/api/backups*`
- 日志：`/api/logs/operations`、`/api/logs/agent`

## 11. Web 视图

1. 对话记账：自然语言或账单截图输入、SSE 运行状态和草稿确认。
2. 账单明细：搜索、筛选、编辑、软删除和撤销；含还款与转账记录。
3. 账户与对账：真实余额、对账差异、账户转账和新增账户。
4. 预算规划：分类月预算和执行进度。
5. 订阅与待还：周期扣款、月度应还和实际还款。
6. 日志：操作记录与 Agent 调用日志。
7. 账本设置：智能分类、分类、支付方式和备份恢复。

## 12. 配置与本地文件

- `.env`：API Key、Provider、模型、数据库路径和分类阈值；不应提交。
- `LEDGER_AGENT_MODEL_PRICING_JSON`：可选的每百万 token 输入/输出/缓存输入单价，用于本地费用估算。
- `.env.example`：无密钥配置模板，应保留。
- `.financial_agent/ledger.db`：真实本地账本，不应提交或当作测试文件删除。
- `.financial_agent/settings.json`：Web 选择的 Provider 和模型。
- `.financial_agent/backups/`：应用创建的 SQLite 备份。
- `.financial_agent/web.log`、`web.pid`：正式本地服务运行文件。

## 13. 安全与隐私

- API Key 只由本地 Python 进程读取，不返回浏览器。
- Agent 日志不保存完整 prompt、完整输入或 API Key；运行中的 Checkpoint 只保存在本地 SQLite，结束后清空内容。
- Web 未实现鉴权，只允许绑定 `127.0.0.1`。
- 不保存银行、支付平台或券商登录凭证。
- 不允许 LLM 生成 SQL 或直接修改数据库。
- 搜索、统计、预算金额和月报数字均由本地代码计算。

## 14. 当前技术债与优化顺序

### P1

1. `financial_agent.py` 仍包含数据库迁移、分类、导入、备份和 CLI；下一阶段可按 `storage`、`classification`、`importers`、`cli` 继续拆分。
2. 当前兼容迁移依赖 `ensure_column`，应增加显式 `schema_version` 和逐版本迁移。
3. 为外部模型调用增加有限重试、错误分类和超时分层。
4. 外部模型集成测试默认关闭；Provider 协议或模型版本变化后需要显式运行 live integration 测试。

### P2

1. 商户规则当前主要是精确标准化匹配，可增加“原始商户 -> 标准商户”实体和用户可见合并界面。
2. 同一商户的收入和支出目前共享分类记忆，可按方向或交易类型细分。
3. Web 前端 `app.js` 已超过 2,000 行，可按聊天、草稿、账单、待还和账户视图拆成原生 ES Modules。
4. 增加本机访问口令后，才考虑局域网或消息平台入口。
