"""Escalation state machine built on pytransitions.

Phase 4 implementation.  The four explicit triggers (send_reminder, escalate,
hand_off, close) are the *only* ways to move a case.  ``auto_transitions=False``
ensures the library does not generate shortcut methods like ``to_closed()``.

Guard conditions (``contact_allowed``, ``ladder_step_due``) run *before* the
transition fires — a blocked trigger simply returns False and the state is
unchanged.  This is the safety-critical property that Track 03's
"compliant escalation, stopping rules" language is judged against.
"""

from __future__ import annotations

from typing import Any, Optional

from transitions import Machine

from src.core.audit import append_decision_trace
from src.ml.versioning import utc_now


class EscalationCase:
    """Finite state machine governing one invoice through the escalation ladder.

    States
    ------
    monitoring -> reminded -> escalated -> human_handoff -> closed

    Design details
    --------------
    - ``auto_transitions=False``:  Only the four explicit triggers below can
      move state.  The library will *not* generate ``to_closed()`` etc.
    - ``ignore_invalid_triggers=True``:  Triggering from a wrong state returns
      False instead of raising ``MachineError``.
    - Guard conditions on ``send_reminder`` and ``escalate`` run *before* the
      transition is attempted.  If the guard fails the trigger returns False
      and the state does not change — there is no "transition happened but
      was illegal" case.
    - Every successful transition writes exactly one Decision Trace entry via
      ``log_transition``, attached as a ``before`` callback.  This is structural
      (the FSM calls it) not conventional (a developer must remember).
    """

    _TRIGGER_NAMES: dict[str, str] = {
        "send_reminder": "send_reminder",
        "escalate": "escalate",
        "hand_off": "hand_off",
        "close": "close",
    }

    states = ["monitoring", "reminded", "escalated", "human_handoff", "closed"]

    transitions = [
        {
            "trigger": "send_reminder",
            "source": "monitoring",
            "dest": "reminded",
            "conditions": "contact_allowed",
            "before": "log_transition",
        },
        {
            "trigger": "escalate",
            "source": "reminded",
            "dest": "escalated",
            "conditions": ["ladder_step_due", "contact_allowed"],
            "before": "log_transition",
        },
        {
            "trigger": "hand_off",
            "source": "escalated",
            "dest": "human_handoff",
            "before": "log_transition",
        },
        {
            "trigger": "close",
            "source": "*",
            "dest": "closed",
            "before": "log_transition",
        },
    ]

    def __init__(
        self,
        invoice_id: str,
        policy_engine: Any,
        *,
        ladder_index: int = 0,
        initial_state: str = "monitoring",
    ):
        self.invoice_id = invoice_id
        self.policy_engine = policy_engine
        self.ladder_index = ladder_index
        self._current_trigger: str = "unknown"

        self.machine = Machine(
            model=self,
            states=self.states,
            transitions=self.transitions,
            initial=initial_state,
            auto_transitions=False,
            ignore_invalid_triggers=True,
            after_state_change="advance_ladder_index",
        )

        # Wrap each trigger so we can capture the name before the FSM runs.
        for name in self._TRIGGER_NAMES:
            original = getattr(self, name)

            def _wrapped(_name=name, _orig=original):
                def wrapper(*args: Any, **kwargs: Any) -> Any:
                    self._current_trigger = _name
                    return _orig(*args, **kwargs)
                return wrapper

            setattr(self, name, _wrapped())

    # ── Guard conditions ───────────────────────────────────────────────────────

    def contact_allowed(self) -> bool:
        """Check the opt-out registry via the policy engine.

        This guard runs on every trigger that sends a message and is never
        bypassed — opt-out is the highest-priority rule.
        """
        return self.policy_engine.is_contact_allowed(self.invoice_id)

    def ladder_step_due(self) -> bool:
        """Ask the policy engine whether the current ladder step is due."""
        return self.policy_engine.escalation_step_due(
            self.invoice_id, self.ladder_index
        )

    # ── Transition callbacks ───────────────────────────────────────────────────

    def advance_ladder_index(self) -> None:
        """Increment the ladder index after a successful transition."""
        self.ladder_index += 1

    def log_transition(self) -> None:
        """Write exactly one Decision Trace entry for this transition.

        Called via ``before`` on every transition — structurally guaranteed,
        not a convention the caller must follow.
        """
        append_decision_trace(
            invoice_id=self.invoice_id,
            event=f"transition:{self._current_trigger}",
            from_state=self.state,  # current state *before* the move
            ladder_index=self.ladder_index,
            reasoning="Triggered by automated escalation engine.",
        )

    # ── Convenience helpers ────────────────────────────────────────────────────

    @property
    def is_terminal(self) -> bool:
        """True once the case is closed."""
        return self.state == "closed"

    @property
    def needs_human(self) -> bool:
        """True once the case has been handed off."""
        return self.state == "human_handoff"

    def __repr__(self) -> str:
        return (
            f"EscalationCase(invoice={self.invoice_id!r}, "
            f"state={self.state!r}, ladder_index={self.ladder_index})"
        )
