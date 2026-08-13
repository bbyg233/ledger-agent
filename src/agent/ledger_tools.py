from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent.contracts import ToolHandler, ToolRisk, ToolSpec
from agent.registry import ToolRegistry


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClarificationInput(ToolInput):
    question: str = Field(
        min_length=1,
        max_length=300,
        description="只追问缺失或含糊的金额、日期、收支方向或多笔拆分金额",
    )


class TransactionToolInput(ToolInput):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="账单日期 YYYY-MM-DD")
    amount: float = Field(gt=0, description="明确的账单金额")
    direction: Literal["income", "expense"] = Field(description="收入或支出")
    category: str = Field(
        min_length=1,
        max_length=40,
        description="优先使用上下文中的标准分类；确无合适分类时填待分类",
    )
    account: str = Field(
        default="未指定",
        max_length=40,
        description="支付方式或资金账户，例如微信、支付宝、现金、银行卡；未知填未指定",
    )
    merchant: str = Field(
        default="",
        max_length=80,
        description="买了什么、商户或主要用途，是主要搜索字段，不能放进备注",
    )
    note: str = Field(
        default="",
        max_length=300,
        description="用户明确提供的补充细节；没有则为空，不能用于承载购买内容",
    )
    category_confidence: float = Field(
        default=1,
        ge=0,
        le=1,
        description="模型对分类判断的置信度，0 到 1",
    )
    category_reason: str = Field(
        default="",
        max_length=200,
        description="分类理由，不超过 30 个中文字",
    )
    proposed_category: str = Field(
        default="",
        max_length=40,
        description="仅当 category 为待分类时建议可复用的新分类，否则为空",
    )


class RecordTransactionsInput(ToolInput):
    transactions: list[TransactionToolInput] = Field(
        min_length=1,
        max_length=20,
        description=(
            "明确账单列表；单笔也使用长度为 1 的数组。只有各笔金额独立明确时才拆分，"
            "不能同时包含分项与总计"
        ),
    )


class MonthInput(ToolInput):
    month: str = Field(
        default="",
        pattern=r"^$|^\d{4}-\d{2}$",
        description="月份 YYYY-MM；为空时使用当前月份",
    )


class PlanInput(MonthInput):
    monthly_income: float | None = Field(default=None, ge=0, description="用户明确给出的月收入")
    saving_goal: float = Field(default=0, ge=0, description="用户明确给出的储蓄目标")


class SearchInput(MonthInput):
    query: str = Field(default="", max_length=1000, description="商户、用途、备注、还款对象或账单月份关键词")
    category: str = Field(default="", max_length=40, description="标准分类筛选")
    account: str = Field(default="", max_length=40, description="支付方式筛选")
    direction: Literal["", "income", "expense", "repayment", "transfer"] = Field(default="", description="收入、支出、还款或转账筛选")
    min_amount: float | None = Field(default=None, ge=0)
    max_amount: float | None = Field(default=None, ge=0)
    limit: int = Field(default=20, ge=1, le=200)


class AggregateInput(MonthInput):
    query: str = Field(default="", max_length=1000, description="可选的账单关键词范围")
    group_by: Literal["category", "merchant", "account"] = Field(
        default="category",
        description="按分类、商户/用途或支付方式聚合",
    )
    limit: int = Field(default=20, ge=1, le=100)


class SpendingTrendInput(ToolInput):
    end_month: str = Field(
        default="", pattern=r"^$|^\d{4}-\d{2}$", description="分析截止月份，空为当前月"
    )
    category: str = Field(default="", max_length=40, description="要比较的标准分类，空为全部支出")
    periods: int = Field(default=3, ge=2, le=12, description="连续比较月数")


class PeriodComparisonInput(ToolInput):
    current_start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="当前观察期起始日期 YYYY-MM-DD")
    current_end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="当前观察期结束日期 YYYY-MM-DD")
    baseline_start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="对比观察期起始日期 YYYY-MM-DD")
    baseline_end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="对比观察期结束日期 YYYY-MM-DD")
    category: str = Field(default="", max_length=40, description="可选的标准分类筛选，空为全部支出")


class RecurringExpenseInput(ToolInput):
    end_month: str = Field(default="", pattern=r"^$|^\d{4}-\d{2}$", description="分析截止月份，空为当前月")
    months: int = Field(default=6, ge=3, le=24, description="回看月数")
    min_occurrences: int = Field(default=3, ge=2, le=12, description="至少在多少个不同月份出现")
    min_amount: float = Field(default=0, ge=0, description="单笔最低金额过滤")


class SubscriptionQueryInput(MonthInput):
    include_inactive: bool = Field(default=False, description="是否一并读取已停用的订阅")


class LiabilityQueryInput(MonthInput):
    include_inactive: bool = Field(default=False, description="是否一并读取已停用的待还项目")


class SubscriptionProposalItem(ToolInput):
    name: str = Field(min_length=1, max_length=80, description="订阅名称，例如视频会员")
    amount: float = Field(gt=0, description="每次实际扣款金额")
    cycle_months: Literal[1, 3, 6, 12] = Field(default=1, description="1 每月、3 每季、6 每半年、12 每年")
    next_charge_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="下一次确定扣款日期")
    category: str = Field(default="其他", max_length=40, description="标准分类")
    account: str = Field(default="未指定", max_length=40, description="预计扣款支付方式")
    note: str = Field(default="", max_length=300, description="用户明确说明的补充信息")


class SubscriptionProposalInput(ToolInput):
    subscriptions: list[SubscriptionProposalItem] = Field(min_length=1, max_length=10)


class SubscriptionChargeProposalInput(ToolInput):
    subscription_id: str = Field(min_length=1, max_length=80, description="上下文中已有订阅的 ID")


class SubscriptionSkipProposalInput(ToolInput):
    subscription_id: str = Field(min_length=1, max_length=80, description="上下文中已有订阅的 ID")


class LiabilityStatementProposalInput(ToolInput):
    liability_id: str = Field(default="", max_length=80, description="已有待还账户的 ID；省略时系统会按同名活跃账户自动匹配")
    name: str = Field(min_length=1, max_length=80, description="项目名称，例如花呗或招商银行信用卡")
    provider: str = Field(default="", max_length=80, description="平台或发卡行")
    kind: Literal["credit_card", "consumer_credit", "installment", "other"] = Field(default="other")
    statement_day: int = Field(default=0, ge=0, le=31, description="每月出账日；未知留 0。系统按下一次出账日推算其后的还款账单月")
    statement_month_offset: int = Field(default=1, ge=0, le=1, description="出账对应账单月：0 为出账当月，1 为出账后下月")
    statement_month: str = Field(
        pattern=r"^\d{4}-\d{2}$",
        description="这笔待还归属的月份；没有固定还款日时仍必须填写",
    )
    due_amount: float = Field(ge=0, description="这个账单月的原始应还金额")
    amount_mode: Literal["add", "set"] = Field(
        default="add",
        description="add 表示把新出现的一笔月付并入已有本月账单；仅在用户明确说应还总额改为某值时使用 set",
    )
    due_date: str = Field(
        default="",
        pattern=r"^$|^\d{4}-\d{2}-\d{2}$",
        description="可选还款截止日期；欠个人且没有约定日期时留空",
    )
    minimum_payment: float = Field(default=0, ge=0, description="最低还款金额，未知填 0")
    repayment_account: str = Field(default="未指定", max_length=40, description="计划使用的还款方式")
    credit_limit: float | None = Field(default=None, ge=0, description="可选信用额度")
    note: str = Field(default="", max_length=300, description="用户明确说明的补充信息")


class LiabilityPaymentProposalInput(ToolInput):
    liability_id: str = Field(min_length=1, max_length=80, description="上下文中已有待还项目的 ID")
    statement_month: str = Field(
        default="",
        pattern=r"^$|^\d{4}-\d{2}$",
        description="还款所属账单月；优先使用上下文中的 statement_month，未说明时可留空",
    )
    amount: float = Field(gt=0, description="已经实际偿还的金额")
    paid_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="实际还款日期")
    account: str = Field(default="未指定", max_length=40, description="实际使用的还款方式")
    note: str = Field(default="", max_length=300, description="用户明确说明的补充信息")


class LiabilityChargeItem(ToolInput):
    liability_id: str = Field(min_length=1, max_length=80, description="上下文中已有月付、花呗、信用卡等待还账户 ID")
    statement_month: str = Field(pattern=r"^\d{4}-\d{2}$", description="模型推测的账单月份；系统会按账户出账日最终校正")
    amount: float = Field(gt=0, description="本次实际消费金额")
    charged_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="实际消费日期")
    category: str = Field(min_length=1, max_length=40, description="消费分类")
    merchant: str = Field(min_length=1, max_length=80, description="商户或主要用途")
    note: str = Field(default="", max_length=300, description="用户明确说明的补充信息")


class LiabilityChargeProposalInput(ToolInput):
    charges: list[LiabilityChargeItem] = Field(
        min_length=1,
        max_length=20,
        description="独立的信贷消费列表；一句话含多笔明确金额时必须逐笔列出，不能合并金额或遗漏分项",
    )


class AccountQueryInput(ToolInput):
    include_inactive: bool = Field(default=False, description="是否包含已经停用的资金账户")


class AccountTransferProposalInput(ToolInput):
    source_account: str = Field(min_length=1, max_length=40, description="转出真实资金账户，必须来自上下文账户目录")
    target_account: str = Field(min_length=1, max_length=40, description="转入真实资金账户，必须来自上下文账户目录")
    amount: float = Field(gt=0, description="确定的实际转账金额")
    transferred_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="实际转账日期 YYYY-MM-DD")
    note: str = Field(default="", max_length=300, description="用户明确提供的补充说明")


class DailyReminderInput(ToolInput):
    enabled: bool | None = Field(
        default=None,
        description="是否启用每日记账提醒；用户未提及开关时留空",
    )
    time: str = Field(
        default="",
        pattern=r"^$|^(?:[01]\d|2[0-3]):[0-5]\d$",
        description="提醒时间 HH:MM，例如 21:00；用户未指定时间时留空",
    )
    skip_today: bool | None = Field(
        default=None,
        description="今天不需要提醒时为 true，恢复今天的提醒时为 false；未提及今天时留空",
    )


class RememberPersonalPreferenceInput(ToolInput):
    title: str = Field(
        min_length=1,
        max_length=60,
        description="一句话概括偏好，例如默认支付账户、记账确认规则或分类习惯",
    )
    content: str = Field(
        min_length=1,
        max_length=500,
        description="只记录用户明确要求长期记住的规则，不得自行推断或记录敏感凭证",
    )


LEDGER_TOOL_DEFINITIONS = (
    (
        "ask_clarification",
        "用户想记账但金额、日期、收支方向或多笔拆分金额不明确时追问；支付方式、分类、商户或备注缺失时不要追问。",
        ClarificationInput,
        ToolRisk.READ_ONLY,
        False,
    ),
    (
        "record_transactions",
        "金额、日期、收支方向和拆分均明确时生成一条或多条待确认账单草稿；该工具不会绕过用户确认直接写入。",
        RecordTransactionsInput,
        ToolRisk.WRITE,
        True,
    ),
    ("get_month_summary", "读取指定月份的收入、支出、结余和分类汇总。", MonthInput, ToolRisk.READ_ONLY, False),
    ("create_budget_plan", "根据本地月度统计生成预算和储蓄规划。", PlanInput, ToolRisk.READ_ONLY, False),
    ("search_ledger", "查找收入、支出和还款资金明细；按月份、分类、支付方式、金额或关键词过滤。", SearchInput, ToolRisk.READ_ONLY, False),
    ("aggregate_spending", "回答钱花在哪里或哪类最多；按分类、商户或支付方式聚合支出。", AggregateInput, ToolRisk.READ_ONLY, False),
    ("analyze_spending_trend", "回答支出为何增加、下降或如何变化；比较 2 至 12 个月及主要增长来源。", SpendingTrendInput, ToolRisk.READ_ONLY, False),
    (
        "compare_spending_periods",
        "比较任意两个明确日期区间的支出、笔数、分类和商户变化；只能依据本地统计，不把相关性说成现实因果。",
        PeriodComparisonInput,
        ToolRisk.READ_ONLY,
        False,
    ),
    (
        "find_recurring_expenses",
        "识别固定或近似固定的周期性支出，例如订阅、房租和话费；返回候选规律供用户核对，不自动创建扣款或提醒。",
        RecurringExpenseInput,
        ToolRisk.READ_ONLY,
        False,
    ),
    (
        "get_subscriptions",
        "读取订阅清单和指定月份预计扣款金额；订阅是计划项目，不等于已经写入的账单。",
        SubscriptionQueryInput,
        ToolRisk.READ_ONLY,
        False,
    ),
    (
        "get_liabilities",
        "读取信用卡、花呗、月付、分期和个人欠款的指定月份账单，回答本月应还、本月未还和逾期金额；不能替用户还款或改变债务。",
        LiabilityQueryInput,
        ToolRisk.READ_ONLY,
        False,
    ),
    (
        "get_account_balances",
        "读取真实资金账户的当前余额和最近对账差异；本金等于这些账户余额之和，不包含待还负债。",
        AccountQueryInput,
        ToolRisk.READ_ONLY,
        False,
    ),
    (
        "propose_subscriptions",
        "用户明确要建立周期订阅且金额、周期、下次扣款日明确时，生成待确认订阅草稿；不要直接创建。",
        SubscriptionProposalInput,
        ToolRisk.WRITE,
        True,
    ),
    (
        "propose_subscription_charge",
        "用户明确已有订阅已经实际扣款时，为上下文中对应订阅生成待确认扣款草稿；不要用于尚未扣款的预计支出。",
        SubscriptionChargeProposalInput,
        ToolRisk.WRITE,
        True,
    ),
    (
        "propose_subscription_skip",
        "用户明确表示某个已有订阅本期不扣、跳过或暂停一期时，生成待确认的跳期草稿；不会写入支出。",
        SubscriptionSkipProposalInput,
        ToolRisk.WRITE,
        True,
    ),
    (
        "propose_liability_statement",
        "用户明确提供某个月的应还金额时，生成待确认的月度账单新建或更新草稿；还款日可选，已有项目必须使用上下文 ID。",
        LiabilityStatementProposalInput,
        ToolRisk.WRITE,
        True,
    ),
    (
        "propose_liability_payment",
        "用户明确已经还款时，为已有待还项目生成待确认还款草稿；不会生成新的消费支出账单。",
        LiabilityPaymentProposalInput,
        ToolRisk.WRITE,
        True,
    ),
    (
        "propose_liability_charge",
        "用户明确说明使用已有月付、花呗、白条或信用卡发生消费时，生成一条或多条待确认信用消费草稿。多个独立金额必须一次通过 charges 数组逐笔生成，绝不能合并或遗漏。草稿会增加对应账单的待还和消费分析，但绝不扣减真实资金账户；不能用普通支出草稿替代。",
        LiabilityChargeProposalInput,
        ToolRisk.WRITE,
        True,
    ),
    (
        "propose_account_transfer",
        "用户明确说明一个真实账户转入另一个真实账户且金额、日期都明确时，生成待确认转账草稿；转账不属于收入或支出。",
        AccountTransferProposalInput,
        ToolRisk.WRITE,
        True,
    ),
    (
        "manage_daily_reminder",
        "读取或更新本地每日记账提醒。用于“今晚九点提醒我”“每天十点提醒”“今天已经记完不用提醒”“恢复今天提醒”等请求；只修改提醒设置，不触碰账本金额。",
        DailyReminderInput,
        ToolRisk.WRITE,
        False,
    ),
    (
        "remember_personal_preference",
        "仅在用户明确说“记住”“以后默认”“以后按这个规则”时，新增一条本地个人偏好记忆。不能从普通账单、闲聊或模型猜测中自动写入；不会修改账本金额。",
        RememberPersonalPreferenceInput,
        ToolRisk.WRITE,
        False,
    ),
    ("generate_monthly_report", "读取确定性统计并生成指定月份的复盘。", MonthInput, ToolRisk.READ_ONLY, False),
)


def build_ledger_tool_registry(handlers: Mapping[str, ToolHandler]) -> ToolRegistry:
    expected = {definition[0] for definition in LEDGER_TOOL_DEFINITIONS}
    missing = expected - set(handlers)
    extra = set(handlers) - expected
    if missing or extra:
        raise ValueError(
            f"账本工具处理器不匹配: missing={sorted(missing)}, extra={sorted(extra)}"
        )
    return ToolRegistry(
        ToolSpec(
            name=name,
            description=description,
            input_model=input_model,
            handler=handlers[name],
            risk=risk,
            requires_confirmation=requires_confirmation,
        )
        for name, description, input_model, risk, requires_confirmation in LEDGER_TOOL_DEFINITIONS
    )
