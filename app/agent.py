import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic_ai import Agent, ModelRetry, RunContext

from app.knowledge import KnowledgeService
from app.prompting import SAFETY_BASELINE


SYSTEM_INSTRUCTIONS = SAFETY_BASELINE


@dataclass
class AgentDeps:
    knowledge: KnowledgeService
    citations: list[dict] = field(default_factory=list)
    show_contact: bool = False
    knowledge_searched: bool = False
    validation_error: str | None = None


agent = Agent(deps_type=AgentDeps, tool_timeout=15, retries=1)


@agent.tool
def search_knowledge(ctx: RunContext[AgentDeps], query: str) -> str:
    """搜索公司的本地知识资料。"""
    ctx.deps.knowledge_searched = True
    results = ctx.deps.knowledge.search(query)
    ctx.deps.citations = [
        {"document_id": item["document_id"], "document": item["display_name"], "section": item["section"], "location": item["location"], "excerpt": item["content"][:260], "images": item.get("images", [])}
        for item in results[:4]
    ]
    if not results:
        return "知识库没有找到相关资料。"
    return "\n\n".join(
        f"[资料 {index + 1}] {item['display_name']} / {item['section']}\n{item['content']}"
        for index, item in enumerate(results[:4])
    )


@agent.tool_plain
def get_business_time() -> dict:
    """查询上海时区的当前时间、星期和示例营业状态。"""
    try:
        shanghai_timezone = ZoneInfo("Asia/Shanghai")
    except ZoneInfoNotFoundError:
        # Windows 通常不自带 IANA 时区数据库。tzdata 是首选来源；这个固定
        # UTC+8 回退保证依赖安装遗漏时，查询当前上海时间也不会中断整次对话。
        shanghai_timezone = timezone(timedelta(hours=8), name="Asia/Shanghai")
    now = datetime.now(shanghai_timezone)
    return {"datetime": now.isoformat(), "weekday": now.strftime("%A"), "is_open": 9 <= now.hour < 18, "business_hours": "09:00-18:00"}


ORDERS = {
    "A10001": {"phone_suffix": "1234", "status": "已发货", "product": "云小智 Pro", "paid_at": "2026-08-12", "signed": False},
    "A10002": {"phone_suffix": "5678", "status": "已签收", "product": "企业服务套餐", "paid_at": "2026-07-20", "signed": True},
    "A10003": {"phone_suffix": "9012", "status": "已退款", "product": "标准套餐", "paid_at": "2026-08-01", "signed": True},
}


@agent.tool_plain
def lookup_order(order_id: str, phone_suffix: str) -> dict:
    """用订单号和手机号后四位查询模拟订单。"""
    order = ORDERS.get(order_id.upper())
    if not order:
        return {"found": False, "message": "未找到该订单"}
    if order["phone_suffix"] != phone_suffix:
        return {"found": False, "message": "手机号后四位不匹配"}
    return {"found": True, "order_id": order_id.upper(), **{key: value for key, value in order.items() if key != "phone_suffix"}}


@agent.tool_plain
def lookup_shipping(order_id: str) -> dict:
    """查询模拟物流轨迹。"""
    order = ORDERS.get(order_id.upper())
    if not order:
        return {"found": False, "message": "未找到该订单"}
    if order["status"] == "已发货":
        return {"found": True, "carrier": "顺丰速运", "tracking_no": "SF-DEMO-001", "latest": "上海转运中心运输中"}
    return {"found": True, "latest": f"订单当前状态：{order['status']}"}


@agent.tool_plain
def check_refund_eligibility(order_id: str) -> dict:
    """根据模拟规则判断订单退款资格，不执行退款。"""
    order = ORDERS.get(order_id.upper())
    if not order:
        return {"eligible": False, "reason": "未找到该订单"}
    if order["status"] == "已退款":
        return {"eligible": False, "reason": "订单已经退款"}
    if order["signed"]:
        return {"eligible": True, "reason": "已签收订单可在七天内申请售后，最终以人工审核为准"}
    return {"eligible": True, "reason": "尚未签收，可登记退款诉求，最终以人工审核为准"}


@agent.tool
def show_contact_form(ctx: RunContext[AgentDeps], reason: str, summary: str) -> str:
    """当问题无法解决或用户要求人工时展示联系方式登记表。"""
    ctx.deps.show_contact = True
    return f"已准备联系方式登记表。原因：{reason}；摘要：{summary}"


_TRANSITION_ONLY = re.compile(r"(让我|我先|请稍等|稍等一下).{0,20}(查询|搜索|查找|确认|处理)")


@agent.output_validator
def validate_final_answer(ctx: RunContext[AgentDeps], output: str) -> str:
    """拒绝空回答和把工具调用过渡语当作最终回答的结果。"""
    answer = output.strip()
    if not answer:
        ctx.deps.validation_error = "模型返回了空回答"
        raise ModelRetry("最终回答不能为空，请直接回答用户的问题。")
    if len(answer) <= 80 and _TRANSITION_ONLY.search(answer):
        ctx.deps.validation_error = "模型只返回了工具调用前的过渡语"
        if ctx.deps.knowledge_searched:
            raise ModelRetry("不要只说明将要查询；请根据已检索到的知识资料给出完整最终回答。")
        raise ModelRetry("不要只说明将要查询；请根据已有信息给出完整最终回答。")
    ctx.deps.validation_error = None
    return answer
