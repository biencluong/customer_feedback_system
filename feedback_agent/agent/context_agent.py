"""Context gathering: a tool-calling loop where the LLM decides which retrieval tools to call.

The loop is written directly against LangChain's `bind_tools` rather than a prebuilt executor so each decision,
tool call, and result is explicit and traceable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from ..models import Classification, FeedbackSubmission
from ..observability.tracing import Tracer
from .prompts import CONTEXT_AGENT_SYSTEM, render_feedback

# Used by the deterministic fallback when the agent LLM is unavailable.
CATEGORY_POLICY_QUERY = {
    "bug_report": "bug escalation engineering outage credit",
    "billing_issue": "refund charge billing duplicate",
    "feature_request": "feature request roadmap",
    "account_access": "account security escalation",
    "churn_risk": "churn cancel retention discount",
    "abuse": "abuse threat harassment conduct",
    "praise": "",
    "other": "sla response time",
}


@dataclass
class GatheredContext:
    customer: Optional[dict] = None
    orders: List[dict] = field(default_factory=list)
    guidelines: Dict[str, dict] = field(default_factory=dict)
    policies: Dict[str, dict] = field(default_factory=dict)
    tools_called: List[str] = field(default_factory=list)
    tool_errors: List[str] = field(default_factory=list)
    agent_notes: str = ""
    mode: str = "agent"  # "agent" or "fallback"
    hit_step_limit: bool = False

    def record(self, name: str, result: dict) -> None:
        self.tools_called.append(name)
        if "error" in result or not result.get("found"):
            return
        if name == "lookup_customer":
            self.customer = result["customer"]
        elif name == "get_order_history":
            self.orders = result["orders"]
        elif name == "get_cs_guideline":
            g = result["guideline"]
            self.guidelines[g["guideline_id"]] = g
        elif name == "search_company_policy":
            for p in result["policies"]:
                self.policies[p["policy_id"]] = p

    def source_ids(self) -> List[str]:
        return sorted(self.guidelines) + sorted(self.policies)

    def missing_required(self, submission: FeedbackSubmission, classification: Classification) -> List[str]:
        """Minimum coverage the report needs. Checks that each source was *consulted*, not that it had a match."""
        missing = []
        if (submission.customer_id or submission.customer_email) and "lookup_customer" not in self.tools_called:
            missing.append("lookup_customer")
        if "get_cs_guideline" not in self.tools_called:
            missing.append("get_cs_guideline")
        if classification.category != "praise" and "search_company_policy" not in self.tools_called:
            missing.append("search_company_policy")
        return missing

    def to_prompt_json(self) -> str:
        return json.dumps(
            {
                "customer": self.customer or "NOT FOUND",
                "recent_orders": self.orders,
                "guidelines": list(self.guidelines.values()),
                "policies": list(self.policies.values()),
                "tool_errors": self.tool_errors,
                "agent_findings": self.agent_notes,
            },
            indent=2,
            default=str,
        )


def _run_tool(tool: Optional[BaseTool], name: str, args: dict, ctx: GatheredContext, tracer: Tracer) -> dict:
    tracer.event("tool.call", tool=name, args=args)
    if tool is None:
        result = {"error": f"Unknown tool {name!r}."}
    else:
        try:
            result = tool.invoke(args)
        except Exception as e:  # tool/data-source failure: report it to the model instead of crashing
            result = {"error": f"{type(e).__name__}: {e}"}
    if "error" in result:
        ctx.tool_errors.append(f"{name}: {result['error']}")
    ctx.record(name, result)
    tracer.event("tool.result", tool=name, result=result)
    return result


def gather_context(
    llm,
    tools: List[BaseTool],
    submission: FeedbackSubmission,
    classification: Classification,
    ambiguous: bool,
    tracer: Tracer,
    max_steps: int,
) -> GatheredContext:
    ctx = GatheredContext()
    tool_map = {t.name: t for t in tools}
    llm_with_tools = llm.bind_tools(tools)

    case = (
        f"{render_feedback(submission)}\n\n"
        f"CLASSIFICATION: {classification.model_dump_json()}\n"
        f"AMBIGUOUS: {ambiguous}"
    )
    messages = [SystemMessage(CONTEXT_AGENT_SYSTEM), HumanMessage(case)]
    reminded = False

    for step in range(1, max_steps + 1):
        ai = llm_with_tools.invoke(messages)
        messages.append(ai)
        tracer.event(
            "agent.decision",
            step=step,
            tool_calls=[{"name": c["name"], "args": c["args"]} for c in ai.tool_calls],
            content=ai.content or None,
        )

        if not ai.tool_calls:
            missing = ctx.missing_required(submission, classification)
            if missing and not reminded:
                reminded = True
                tracer.event("agent.coverage_reminder", missing=missing)
                messages.append(
                    HumanMessage(f"Before finishing, you have not consulted: {', '.join(missing)}. Call them now.")
                )
                continue
            ctx.agent_notes = ai.content if isinstance(ai.content, str) else str(ai.content)
            return ctx

        for call in ai.tool_calls:
            result = _run_tool(tool_map.get(call["name"]), call["name"], call["args"], ctx, tracer)
            messages.append(ToolMessage(content=json.dumps(result, default=str), tool_call_id=call["id"]))

    ctx.hit_step_limit = True
    tracer.event("agent.step_limit_reached", max_steps=max_steps)
    return ctx


def fallback_gather(
    tools: List[BaseTool], submission: FeedbackSubmission, classification: Classification, tracer: Tracer
) -> GatheredContext:
    """Fixed retrieval plan used when the agent LLM fails. Less adaptive, but keeps the report grounded."""
    ctx = GatheredContext(mode="fallback")
    tool_map = {t.name: t for t in tools}

    if submission.customer_id or submission.customer_email:
        _run_tool(
            tool_map["lookup_customer"],
            "lookup_customer",
            {"customer_id": submission.customer_id, "email": submission.customer_email},
            ctx,
            tracer,
        )
    if ctx.customer and classification.category in ("billing_issue", "churn_risk"):
        _run_tool(
            tool_map["get_order_history"],
            "get_order_history",
            {"customer_id": ctx.customer["customer_id"]},
            ctx,
            tracer,
        )
    for category in filter(None, [classification.category, classification.secondary_category]):
        _run_tool(tool_map["get_cs_guideline"], "get_cs_guideline", {"category": category}, ctx, tracer)
    query = CATEGORY_POLICY_QUERY.get(classification.category, "")
    if query:
        _run_tool(tool_map["search_company_policy"], "search_company_policy", {"query": query}, ctx, tracer)
    return ctx
