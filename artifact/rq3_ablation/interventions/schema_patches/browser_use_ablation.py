"""Browser-Use action-space ablation hook (RQ3).

Browser-Use exposes navigation primitives as `@self.registry.action(...)`-decorated
functions inside `browser_use.tools.service.Tools.__init__`. To ablate a primitive,
we wrap `Tools.__init__` so that immediately after it finishes registering its
default actions, the matching entries are removed from `tools.registry.registry.actions`.

Activation: set the `BU_DISABLED_ACTIONS` env variable to a comma-separated list of
action names (matching the function names declared in service.py).  Recognized names:

    * ``search``    -- the discrete search-engine action (service.py:368)
    * ``navigate``  -- the direct-URL navigation action (service.py:412)

Other names are accepted but logged with a warning if they don't match a registered
action at init time.

The patch is applied lazily on the first import of `browser_use.tools` so the import
path inside the agent harness can simply do::

    from agentcloak.measurement import browser_use_ablation  # noqa: F401

before instantiating the Browser-Use agent.  Subsequent ``Tools(...)`` constructions
in the same process inherit the patched behavior.

If `BU_DISABLED_ACTIONS` is empty/unset the module is a no-op.
"""

from __future__ import annotations

import logging
import os
from typing import Iterable

logger = logging.getLogger(__name__)

_PATCH_APPLIED = False
_ORIGINAL_INIT = None  # captured exactly once, before any patching


def _get_disabled_actions() -> set[str]:
    raw = os.environ.get("BU_DISABLED_ACTIONS", "").strip()
    if not raw:
        return set()
    return {a.strip() for a in raw.split(",") if a.strip()}


def _strip_actions(tools_instance, disabled: Iterable[str]) -> dict[str, bool]:
    """Remove `disabled` entries from `tools.registry.registry.actions`.

    Returns a {action_name: was_removed} report for the caller.
    """
    report: dict[str, bool] = {}
    actions = tools_instance.registry.registry.actions
    for name in disabled:
        if name in actions:
            del actions[name]
            report[name] = True
            logger.info(
                "[BU-ablation] removed action %r from Tools registry "
                "(remaining: %d)",
                name,
                len(actions),
            )
        else:
            report[name] = False
            logger.warning(
                "[BU-ablation] disabled action %r not found in registry "
                "(known: %s)",
                name,
                sorted(actions.keys()),
            )
    return report


def apply_patch() -> None:
    """Monkeypatch `Tools.__init__` to strip disabled actions post-init.

    Special handling: `navigate` is referenced by Browser-Use's
    `_convert_initial_actions` machinery which auto-injects
    `{'navigate': {url}}` for the task's start URL. Removing `navigate` at
    Tools construction crashes the Agent with KeyError. To enable
    schema-level removal of `navigate` (C2S condition), we instead
    monkeypatch `Agent._execute_initial_actions` so that the `navigate`
    handler is stripped from the Tools registry *after* the initial start
    URL has been loaded but *before* the agent's main step loop begins.
    The agent then has no `navigate` available for subsequent steps.
    """
    global _PATCH_APPLIED, _ORIGINAL_INIT

    try:
        from browser_use.tools.service import Tools
        from browser_use.agent.service import Agent
    except Exception as e:  # pragma: no cover -- only reachable when bu missing
        logger.warning("[BU-ablation] browser_use not importable; skipping: %s", e)
        _PATCH_APPLIED = True
        return

    # Capture the un-patched __init__ exactly once.
    if _ORIGINAL_INIT is None:
        _ORIGINAL_INIT = Tools.__init__

    disabled = _get_disabled_actions()

    if not disabled:
        # No-op: restore the original init so prior wrappers don't linger.
        Tools.__init__ = _ORIGINAL_INIT
        _PATCH_APPLIED = True
        return

    # Split disabled actions into "strip at init time" and "strip after
    # initial-action execution".  Currently `navigate` is the only entry in
    # the deferred set; everything else strips immediately.
    deferred = {a for a in disabled if a in {"navigate"}}
    immediate = disabled - deferred

    original_init = _ORIGINAL_INIT  # always wrap the original, never a wrapper

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        report = _strip_actions(self, immediate)
        # Stash the deferred set on the instance so the Agent-level hook can
        # find and apply it after the initial action runs.
        if deferred:
            setattr(self, "_bu_ablation_deferred", set(deferred))
        try:
            self._bu_ablation_report = report
        except Exception:
            pass

    Tools.__init__ = patched_init
    logger.info(
        "[BU-ablation] patched Tools.__init__; immediate strip: %s; "
        "deferred (post-initial-action) strip: %s",
        sorted(immediate),
        sorted(deferred),
    )

    if deferred:
        # Wrap Agent._execute_initial_actions to strip deferred actions
        # immediately after the initial action runs.
        if not getattr(Agent, "_bu_ablation_run_patched", False):
            original_exec = Agent._execute_initial_actions

            async def patched_exec(self):
                result = await original_exec(self)
                tools = getattr(self, "tools", None)
                if tools is not None:
                    pending = getattr(tools, "_bu_ablation_deferred", set())
                    if pending:
                        post_report = _strip_actions(tools, pending)
                        logger.info(
                            "[BU-ablation] post-initial-action strip applied: %s",
                            sorted(pending),
                        )
                        try:
                            tools._bu_ablation_deferred = set()
                            existing = getattr(tools, "_bu_ablation_report", {}) or {}
                            existing.update(post_report)
                            tools._bu_ablation_report = existing
                        except Exception:
                            pass
                return result

            Agent._execute_initial_actions = patched_exec
            Agent._bu_ablation_run_patched = True
            logger.info(
                "[BU-ablation] patched Agent._execute_initial_actions to "
                "strip deferred actions after initial URL load"
            )

    _PATCH_APPLIED = True


# Apply the patch eagerly on import so any subsequent `from browser_use ...`
# import in the same process picks up the modified behavior.
apply_patch()
