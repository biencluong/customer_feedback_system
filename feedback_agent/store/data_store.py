"""Read-only access to the mock data sources (stand-ins for a CRM, a policy wiki, and an SOP library)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from ..config import DATA_DIR

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set:
    return set(_WORD.findall(text.lower()))


class DataStore:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.customers: List[dict] = json.loads((data_dir / "customers.json").read_text())
        self.policies: List[dict] = json.loads((data_dir / "policies.json").read_text())
        self.guidelines: List[dict] = json.loads((data_dir / "guidelines.json").read_text())

    def find_customer(self, customer_id: Optional[str] = None, email: Optional[str] = None) -> Optional[dict]:
        for c in self.customers:
            if customer_id and c["customer_id"].lower() == customer_id.strip().lower():
                return c
            if email and c["email"].lower() == email.strip().lower():
                return c
        return None

    def get_guideline(self, category: str) -> Optional[dict]:
        return next((g for g in self.guidelines if g["category"] == category), None)

    def get_policy(self, policy_id: str) -> Optional[dict]:
        return next((p for p in self.policies if p["policy_id"].lower() == policy_id.strip().lower()), None)

    def search_policies(self, query: str, limit: int = 3) -> List[dict]:
        """Keyword search: tag matches weigh 3x, title 2x, body 1x. Small corpus, so no vector store needed."""
        q = _tokens(query)
        scored: List[tuple] = []
        for p in self.policies:
            score = (
                3 * len(q & set(p["tags"]))
                + 2 * len(q & _tokens(p["title"]))
                + len(q & _tokens(p["text"]))
            )
            if score >= 2:
                scored.append((score, p))
        scored.sort(key=lambda s: s[0], reverse=True)
        return [p for _, p in scored[:limit]]

    def valid_categories(self) -> List[str]:
        return [g["category"] for g in self.guidelines]

    def source_index(self) -> Dict[str, dict]:
        index = {p["policy_id"]: p for p in self.policies}
        index.update({g["guideline_id"]: g for g in self.guidelines})
        return index
