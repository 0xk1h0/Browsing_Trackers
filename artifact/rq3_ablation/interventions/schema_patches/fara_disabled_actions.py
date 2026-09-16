"""Fara-7B action-space ablation hook (RQ3).

This module is dropped into the Fara prompt-construction module so that
`FARA_DISABLED_ACTIONS` (a comma-separated list of action names) removes the
listed actions from:

  1. the model-visible action enum (`parameters["properties"]["action"]["enum"]`),
  2. the prompt's bulleted action description, and
  3. any orphaned per-action argument fields (e.g. `url` for `visit_url`,
     `query` for `web_search`).

Recognized actions (matching the names Fara emits):

    * ``web_search``    -- discrete search-engine query primitive
    * ``visit_url``     -- direct-URL navigation primitive
    * ``history_back``  -- browser history back action (C5 negative control)
    * ``pause_and_memorize_fact``

A second hook in the Fara agent dispatch path rejects an attempted dispatch
of a disabled action and records ``action="policy_violation"`` in the agent
log. Without that second hook, the model can still emit a disabled action by
ignoring the schema; the dispatch refusal closes that loophole.

Installation
------------
Patch ``models/fara/src/fara/_prompts.py`` by adding (at the top of the file):

    from .rq3_schema_patch import (
        _DISABLED_ACTION_ARG_FIELDS,
        get_disabled_actions,
        apply_action_ablation,
    )

and inside ``FaraComputerUse.__init__``, after the existing parameter setup:

    apply_action_ablation(self.parameters)

Patch ``models/fara/src/fara/fara_agent.py`` so the dispatch loop rejects
disabled actions:

    _disabled_raw = os.environ.get("FARA_DISABLED_ACTIONS", "").strip()
    if _disabled_raw:
        disabled_actions = {a.strip() for a in _disabled_raw.split(",") if a.strip()}
        if args.get("action") in disabled_actions:
            log.info(f"[policy_violation] disabled action {args['action']!r}")
            self._record(action="policy_violation", reason="disabled", args=args)
            return ToolResult(... terminated=False ...)
"""
from __future__ import annotations

import os
import re
from typing import Any


_DISABLED_ACTION_ARG_FIELDS: dict[str, list[str]] = {
    "visit_url": ["url"],
    "web_search": ["query"],
    "history_back": [],
    "pause_and_memorize_fact": ["fact"],
}


def get_disabled_actions() -> set[str]:
    raw = os.environ.get("FARA_DISABLED_ACTIONS", "").strip()
    if not raw:
        return set()
    return {a.strip() for a in raw.split(",") if a.strip()}


def apply_action_ablation(parameters: dict[str, Any]) -> set[str]:
    """Mutate `parameters` in place to strip disabled actions.

    Returns the set of actions that were stripped (empty if nothing to do).
    Raises ValueError if disabling would empty the enum.

    `parameters` is the JSONSchema-style dict that lives at
    ``FaraComputerUse.parameters`` — i.e. the value passed to the model as
    the tool's input schema.
    """
    disabled = get_disabled_actions()
    if not disabled:
        return set()

    action_schema = parameters["properties"]["action"]

    new_enum = [a for a in action_schema["enum"] if a not in disabled]
    if not new_enum:
        raise ValueError(
            "FARA_DISABLED_ACTIONS would disable every action; refusing."
        )
    action_schema["enum"] = new_enum

    kept_lines = []
    for line in action_schema["description"].splitlines():
        m = re.match(r"\*\s+`([a-z_]+)`\s*:", line)
        if m and m.group(1) in disabled:
            continue
        kept_lines.append(line)
    action_schema["description"] = "\n".join(kept_lines)

    for action in disabled:
        for field in _DISABLED_ACTION_ARG_FIELDS.get(action, []):
            parameters["properties"].pop(field, None)

    return set(disabled)


def is_action_disabled(action_name: str) -> bool:
    return action_name in get_disabled_actions()
