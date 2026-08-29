"""Resolve LinkedIn's normalized response format into a nested tree.

LinkedIn returns ``{"data": ..., "included": [...]}`` when asked for
``application/vnd.linkedin.normalized+json+2.1``. Objects in ``included`` are keyed by
``entityUrn`` and reference each other through star-prefixed keys:

    "*company":   "urn:li:fsd_company:1"        single reference
    "**elements": ["urn:li:pos:1", "urn:li:pos:2"]  collection reference

The graph is genuinely cyclic, so resolution tracks the URNs on the current branch and
leaves a raw URN string in place rather than expanding a reference twice.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Truncation:
    """A collection LinkedIn returned only partially."""

    section: str
    returned: int
    total: int


@dataclass
class NormalizedResult:
    data: dict[str, Any]
    truncations: list[Truncation] = field(default_factory=list)


def _index(included: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        item["entityUrn"]: item
        for item in included
        if isinstance(item, dict) and isinstance(item.get("entityUrn"), str)
    }


def normalize(payload: dict[str, Any], max_depth: int = 12) -> NormalizedResult:
    """Return the payload with every URN reference resolved in place."""
    index = _index(payload.get("included") or [])
    truncations: list[Truncation] = []

    def resolve(node: Any, branch: frozenset[str], depth: int) -> Any:
        if depth > max_depth:
            return node
        if isinstance(node, list):
            return [resolve(item, branch, depth + 1) for item in node]
        if not isinstance(node, dict):
            return node

        _record_truncation(node, truncations)
        output: dict[str, Any] = {}

        for key, value in node.items():
            if key.startswith("**"):
                output[key[2:]] = _resolve_many(value, index, branch, depth, resolve)
            elif key.startswith("*"):
                output[key[1:]] = _resolve_one(value, index, branch, depth, resolve)
            else:
                output[key] = resolve(value, branch, depth + 1)
        return output

    def _resolve_one(
        urn: Any, idx: dict[str, dict[str, Any]], branch: frozenset[str], depth: int, rec: Any
    ) -> Any:
        if not isinstance(urn, str):
            return rec(urn, branch, depth + 1)
        if urn in branch:
            return urn  # cycle: leave the raw URN rather than expanding again
        target = idx.get(urn)
        if target is None:
            return None
        return rec(target, branch | {urn}, depth + 1)

    def _resolve_many(
        urns: Any, idx: dict[str, dict[str, Any]], branch: frozenset[str], depth: int, rec: Any
    ) -> Any:
        if not isinstance(urns, list):
            return rec(urns, branch, depth + 1)
        resolved: list[Any] = []
        for urn in urns:
            if not isinstance(urn, str):
                resolved.append(rec(urn, branch, depth + 1))
            elif urn in branch:
                resolved.append(urn)
            elif urn in idx:
                resolved.append(rec(idx[urn], branch | {urn}, depth + 1))
            # Unresolvable URNs are dropped rather than yielding a None hole.
        return resolved

    data = payload.get("data", payload)
    return NormalizedResult(data=resolve(data, frozenset(), 0), truncations=truncations)


def _record_truncation(node: dict[str, Any], sink: list[Truncation]) -> None:
    paging = node.get("paging")
    if not isinstance(paging, dict):
        return
    total = paging.get("total")
    count = paging.get("count")
    if not isinstance(total, int) or not isinstance(count, int):
        return
    if total > count:
        section = str(node.get("entityUrn") or node.get("$type") or "unknown")
        sink.append(Truncation(section=section, returned=count, total=total))
