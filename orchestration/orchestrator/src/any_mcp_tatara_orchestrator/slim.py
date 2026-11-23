"""Answer slimming (S-83 decisions 925, S1 and S3-S9): lower default limits and shaped reads.

Every read an agent makes costs its context, so a read answers only the fields agents use and a
list defaults to a short page. Two tables, no per-tool code in the plugin:

* ``READ_DEFAULTS``: the argument a plain (not merged) read tool gets when the call omits it. A
  merged tool sets the same through ``Route.fill`` on the op.
* ``SHAPERS``: ``(tool, op key)`` to a function ``(answer, arguments) -> answer`` run on the
  decoded JSON answer; ``arguments`` are the ones the agent sent.

A shaper never invents data: it drops fields, keeps the leading rows and says how many it left
out in ``omitted``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .fit import _cut, measure

__all__ = [
    "LIST_LIMIT",
    "MAP_DEPTH",
    "READ_DEFAULTS",
    "SHAPERS",
    "fit_records",
    "shaper",
]

LIST_LIMIT = 20  # default page of every list read below
MAP_DEPTH = 3  # default depth of mindmap_map op get
_COMMENT_CHARS = 400

Shaper = Callable[[Any, Mapping[str, Any]], Any]

# Plain read tools: the arguments they get when the call omits them.
READ_DEFAULTS: dict[str, dict[str, Any]] = {
    "activity": {"limit": LIST_LIMIT},
    "mindmap_search": {"limit": LIST_LIMIT},
    "spec_comments": {"open": True},
}

_SESSION_VIEWS = ("summary", "spec")
_WEBHOOK_EVENT = (
    "id",
    "received_at",
    "type",
    "kind",
    "action",
    "repo",
    "number",
    "title",
    "author",
    "status",
    "reason",
    "session_slug",
    "update_of",
    "duplicate_of",
    "url",
)
_SCHEDULE_RUN = (
    "id",
    "schedule_id",
    "trigger",
    "fire_time",
    "status",
    "reason",
    "session_slug",
    "started_at",
    "finished_at",
    "chain_status",
)
_ROOM = (
    "room_id",
    "name",
    "kind",
    "membership",
    "member_count",
    "access",
    "preset_id",
    "trigger",
    "effective_trigger",
    "agents_may_initiate",
    "anonymize_names",
    "agent_aliases",
    "agents_may_send_files",
    "session",
    "binding",
    "last_event_at",
    "queued",
    "pending",
)
_VAULT_ENTRY = (
    "id",
    "name",
    "description",
    "labels",
    "envs",
    "version",
    "attachments",
    "behind_count",
)
_VAULT_ENV = ("env", "protected", "value")
_VAULT_ATTACHMENT = ("preset_id", "preset_name", "env", "entry_env", "manageable", "privileged")


def _pick(row: Any, keep: Iterable[str]) -> Any:
    return {k: row[k] for k in keep if k in row} if isinstance(row, dict) else row


def _page(rows: list[Any], limit: int) -> tuple[list[Any], int]:
    return rows[:limit], max(len(rows) - limit, 0)


def _rows(keep: tuple[str, ...]) -> Shaper:
    """A list answer whose rows keep only ``keep``."""

    def shape(answer: Any, _args: Mapping[str, Any]) -> Any:
        if not isinstance(answer, list):
            return answer
        return [_pick(row, keep) for row in answer]

    return shape


def session_get(answer: Any, args: Mapping[str, Any]) -> Any:
    """``view`` summary (default) leaves the spec and the vault pins out; view spec is whole."""
    if not isinstance(answer, dict) or args.get("view", "summary") == "spec":
        return answer
    return {k: v for k, v in answer.items() if k not in ("spec", "vault")}


def proposal_latest(answer: Any, _args: Mapping[str, Any]) -> Any:
    """One proposal with its latest revision only (``all_revisions`` reads every one)."""
    if not isinstance(answer, dict) or not isinstance(answer.get("revisions"), list):
        return answer
    revisions = answer["revisions"]
    return {**answer, "revisions": revisions[-1:], "revisions_total": len(revisions)}


def spec_comments(answer: Any, _args: Mapping[str, Any]) -> Any:
    """Comment bodies and suggestions cut to a short head, the cut length marked."""
    if not isinstance(answer, dict) or not isinstance(answer.get("items"), list):
        return answer

    def cut(comment: Any) -> Any:
        if not isinstance(comment, dict):
            return comment
        out = dict(comment)
        for field in ("body", "suggestion"):
            text = out.get(field)
            if isinstance(text, str) and len(text) > _COMMENT_CHARS:
                out[field] = _cut(text, _COMMENT_CHARS)
        return out

    return {**answer, "items": [cut(c) for c in answer["items"]]}


def chat_rooms(answer: Any, args: Mapping[str, Any]) -> Any:
    """The room list of the projects: a summary row per room, the first ``limit`` of them."""
    if not isinstance(answer, list):
        return answer
    rooms, omitted = _page([_pick(r, _ROOM) for r in answer], args.get("limit", LIST_LIMIT))
    return {"rooms": rooms, **({"omitted": omitted} if omitted else {})}


def vault_list(answer: Any, args: Mapping[str, Any]) -> Any:
    """Vault entries without timestamps and counters, the first ``limit`` of them."""
    if not isinstance(answer, dict) or not isinstance(answer.get("entries"), list):
        return answer

    def entry(raw: Any) -> Any:
        row = _pick(raw, _VAULT_ENTRY)
        if not isinstance(row, dict):
            return row
        if isinstance(row.get("envs"), list):
            row["envs"] = [_pick(e, _VAULT_ENV) for e in row["envs"]]
        if isinstance(row.get("attachments"), list):
            row["attachments"] = [_pick(a, _VAULT_ATTACHMENT) for a in row["attachments"]]
        return row

    entries, omitted = _page([entry(e) for e in answer["entries"]], args.get("limit", LIST_LIMIT))
    return {**answer, "entries": entries, **({"omitted": omitted} if omitted else {})}


SHAPERS: dict[tuple[str, str], Shaper] = {
    ("session_get", ""): session_get,
    ("proposal_get", "id"): proposal_latest,
    ("spec_comments", ""): spec_comments,
    ("chat_rooms", "projects"): chat_rooms,
    ("vault_list", ""): vault_list,
    ("webhook", "events"): _rows(_WEBHOOK_EVENT),
    ("schedule", "runs"): _rows(_SCHEDULE_RUN),
    ("schedule", "runs_all"): _rows(_SCHEDULE_RUN),
}


def shaper(tool: str, key: str) -> Shaper | None:
    return SHAPERS.get((tool, key))


def fit_records(page: Any, budget: int) -> Any:
    """A history records page inside ``budget``: the leading records that fit, ``omitted`` the
    count left out and ``next_after_id`` the id to continue from (records page forward)."""
    if not isinstance(page, dict) or not isinstance(page.get("records"), list):
        return page
    records = page["records"]
    if measure(page) <= budget:
        return page

    def shaped(keep: int) -> dict[str, Any]:
        # The upstream continue id is past every record of the page: it is only valid whole.
        rest = {k: v for k, v in page.items() if k not in ("records", "next_after_id")}
        last = records[keep - 1].get("id") if keep and isinstance(records[keep - 1], dict) else None
        if keep == len(records):
            rest.update({k: v for k, v in page.items() if k == "next_after_id"})
        elif last is not None:
            rest["next_after_id"] = last
        return {"omitted": len(records) - keep, **rest, "records": records[:keep]}

    lo, hi = 0, len(records)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if measure(shaped(mid)) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return shaped(lo)
