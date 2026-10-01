"""Retrieval tools exposed to the LLM via function calling.

Every tool returns a JSON-serialisable dict. "Not found" is a normal result ({"found": false, ...}) rather than an
exception, so the model can reason about missing data instead of the run crashing.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from ..store.data_store import DataStore


class SimulatedToolError(RuntimeError):
    pass


class LookupCustomerInput(BaseModel):
    customer_id: Optional[str] = Field(default=None, description="Customer ID, e.g. C001.")
    email: Optional[str] = Field(default=None, description="Customer email address.")


class OrderHistoryInput(BaseModel):
    customer_id: str = Field(description="Customer ID returned by lookup_customer.")
    limit: int = Field(default=5, ge=1, le=20)


class GuidelineInput(BaseModel):
    category: str = Field(description="Feedback category, e.g. billing_issue, bug_report, churn_risk.")


class PolicySearchInput(BaseModel):
    query: str = Field(description="Keywords describing the situation, e.g. 'duplicate charge refund'.")


def _months_between(start: str, end: date) -> int:
    s = date.fromisoformat(start)
    return (end.year - s.year) * 12 + (end.month - s.month)


def build_tools(store: DataStore, failing_tool: Optional[str] = None) -> List[BaseTool]:
    """Build the tool set. `failing_tool` makes that tool raise, to demonstrate tool-failure handling."""

    def _maybe_fail(name: str) -> None:
        if failing_tool == name:
            raise SimulatedToolError(f"{name}: upstream data source unavailable (simulated outage)")

    def lookup_customer(customer_id: Optional[str] = None, email: Optional[str] = None) -> dict:
        _maybe_fail("lookup_customer")
        if not customer_id and not email:
            return {"found": False, "message": "Provide customer_id or email."}
        c = store.find_customer(customer_id, email)
        if c is None:
            return {
                "found": False,
                "message": f"No customer record matches customer_id={customer_id!r}, email={email!r}. "
                "Do not assume any customer details.",
            }
        profile = {k: v for k, v in c.items() if k != "orders"}
        profile["tenure_months"] = _months_between(c["customer_since"], date.today())
        profile["open_ticket_count"] = sum(1 for t in c["past_tickets"] if t["status"] == "open")
        return {"found": True, "customer": profile}

    def get_order_history(customer_id: str, limit: int = 5) -> dict:
        _maybe_fail("get_order_history")
        c = store.find_customer(customer_id=customer_id)
        if c is None:
            return {"found": False, "message": f"No customer {customer_id!r}."}
        orders = sorted(c["orders"], key=lambda o: o["date"], reverse=True)[:limit]
        return {"found": True, "customer_id": c["customer_id"], "orders": orders}

    def get_cs_guideline(category: str) -> dict:
        _maybe_fail("get_cs_guideline")
        g = store.get_guideline(category)
        if g is None:
            return {
                "found": False,
                "message": f"No guideline for category {category!r}.",
                "valid_categories": store.valid_categories(),
            }
        return {"found": True, "guideline": g}

    def search_company_policy(query: str) -> dict:
        _maybe_fail("search_company_policy")
        hits = store.search_policies(query)
        if not hits:
            return {"found": False, "message": f"No policy matched {query!r}. Try different keywords."}
        return {"found": True, "policies": hits}

    return [
        StructuredTool.from_function(
            lookup_customer,
            name="lookup_customer",
            description="Look up a customer's profile: tier, tenure, spend, health score, account manager, past tickets.",
            args_schema=LookupCustomerInput,
        ),
        StructuredTool.from_function(
            get_order_history,
            name="get_order_history",
            description="Get a customer's recent orders/charges. Use for billing, refund, or pricing questions.",
            args_schema=OrderHistoryInput,
        ),
        StructuredTool.from_function(
            get_cs_guideline,
            name="get_cs_guideline",
            description="Get the standard CS operating procedure (SOP) for a feedback category.",
            args_schema=GuidelineInput,
        ),
        StructuredTool.from_function(
            search_company_policy,
            name="search_company_policy",
            description="Keyword search over company policies (refunds, SLA, escalation, abuse, retention, credits, "
            "privacy, feature requests). Returns full policy text with IDs.",
            args_schema=PolicySearchInput,
        ),
    ]
