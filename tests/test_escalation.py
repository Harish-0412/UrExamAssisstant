"""Tests for the transitions-based escalation state machine.

These tests exercise every guard condition, the full state walk, and verify
that each transition writes exactly one Decision Trace entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from src.core.audit import clear_trace, get_full_trace, get_trace
from src.core.escalation import EscalationCase


# ── Fake policy engine ────────────────────────────────────────────────────────

@dataclass
class FakePolicyEngine:
    """Configurable stub for the policy engine used by EscalationCase.

    Set `opted_out` to True to simulate an opted-out invoice.
    Set `step_due` to True/False to control whether escalation is allowed.
    """

    opted_out: bool = False
    step_due: bool = True

    def is_contact_allowed(self, invoice_id: str) -> bool:
        return not self.opted_out

    def escalation_step_due(self, invoice_id: str, ladder_index: int) -> bool:
        return self.step_due


@pytest.fixture(autouse=True)
def _clear_audit():
    """Reset the audit ledger before every test."""
    clear_trace()
    yield
    clear_trace()


@pytest.fixture
def policy() -> FakePolicyEngine:
    return FakePolicyEngine()


@pytest.fixture
def case(policy: FakePolicyEngine) -> EscalationCase:
    return EscalationCase(
        invoice_id="inv-001",
        policy_engine=policy,
        ladder_index=0,
    )


# ── Tests: basic state transitions ────────────────────────────────────────────

class TestBasicTransitions:
    """Happy path — a case that escalates through the full ladder."""

    def test_initial_state(self, case: EscalationCase) -> None:
        assert case.state == "monitoring"

    def test_send_reminder_moves_to_reminded(self, case: EscalationCase) -> None:
        result = case.send_reminder()
        assert result is True
        assert case.state == "reminded"

    def test_escalate_moves_to_escalated(self, case: EscalationCase) -> None:
        case.send_reminder()
        result = case.escalate()
        assert result is True
        assert case.state == "escalated"

    def test_hand_off_moves_to_human_handoff(
        self, case: EscalationCase
    ) -> None:
        case.send_reminder()
        case.escalate()
        result = case.hand_off()
        assert result is True
        assert case.state == "human_handoff"

    def test_close_from_any_state(self, case: EscalationCase) -> None:
        result = case.close()
        assert result is True
        assert case.state == "closed"
        assert case.is_terminal is True

    def test_full_ladder_walk(self, case: EscalationCase) -> None:
        """Drive a case through the complete sequence."""
        assert case.send_reminder() is True
        assert case.state == "reminded"
        assert case.escalate() is True
        assert case.state == "escalated"
        assert case.hand_off() is True
        assert case.state == "human_handoff"
        assert case.close() is True
        assert case.state == "closed"


# ── Tests: guard conditions ──────────────────────────────────────────────────

class TestGuardConditions:
    """Guards must *block* transitions, not just warn."""

    def test_opted_out_blocks_reminder(
        self, policy: FakePolicyEngine
    ) -> None:
        policy.opted_out = True
        case = EscalationCase(
            invoice_id="inv-002",
            policy_engine=policy,
        )
        result = case.send_reminder()
        assert result is False
        assert case.state == "monitoring"

    def test_opted_out_blocks_escalation(
        self, policy: FakePolicyEngine
    ) -> None:
        policy.opted_out = True
        case = EscalationCase(
            invoice_id="inv-003",
            policy_engine=policy,
            initial_state="reminded",
        )
        result = case.escalate()
        assert result is False
        assert case.state == "reminded"

    def test_step_not_due_blocks_escalation(
        self, policy: FakePolicyEngine
    ) -> None:
        policy.step_due = False
        case = EscalationCase(
            invoice_id="inv-004",
            policy_engine=policy,
            initial_state="reminded",
        )
        result = case.escalate()
        assert result is False
        assert case.state == "reminded"

    def test_opted_out_overrides_even_when_step_due(
        self, policy: FakePolicyEngine
    ) -> None:
        """Opt-out is the highest-priority rule."""
        policy.opted_out = True
        policy.step_due = True
        case = EscalationCase(
            invoice_id="inv-005",
            policy_engine=policy,
            initial_state="reminded",
        )
        result = case.escalate()
        assert result is False
        assert case.state == "reminded"

    def test_hand_off_has_no_guard(
        self, policy: FakePolicyEngine
    ) -> None:
        """hand_off is unconditional — once escalated, human handoff is always allowed."""
        case = EscalationCase(
            invoice_id="inv-006",
            policy_engine=policy,
            initial_state="escalated",
        )
        result = case.hand_off()
        assert result is True
        assert case.state == "human_handoff"

    def test_close_has_no_guard(
        self, policy: FakePolicyEngine
    ) -> None:
        """close works from *any* state (source='*')."""
        case = EscalationCase(
            invoice_id="inv-007",
            policy_engine=policy,
        )
        result = case.close()
        assert result is True
        assert case.state == "closed"


# ── Tests: invalid transitions ───────────────────────────────────────────────

class TestInvalidTransitions:
    """Attempting a trigger from the wrong state returns False."""

    def test_escalate_from_monitoring_fails(
        self, case: EscalationCase
    ) -> None:
        result = case.escalate()
        assert result is False
        assert case.state == "monitoring"

    def test_hand_off_from_reminded_fails(
        self, policy: FakePolicyEngine
    ) -> None:
        case = EscalationCase(
            invoice_id="inv-008",
            policy_engine=policy,
            initial_state="reminded",
        )
        result = case.hand_off()
        assert result is False
        assert case.state == "reminded"

    def test_no_auto_transitions(
        self, policy: FakePolicyEngine
    ) -> None:
        """auto_transitions=False means no to_closed() shortcut exists."""
        case = EscalationCase(
            invoice_id="inv-009",
            policy_engine=policy,
        )
        assert not hasattr(case, "to_closed"), (
            "to_closed() should not exist with auto_transitions=False"
        )
        assert not hasattr(case, "to_reminded"), (
            "to_reminded() should not exist with auto_transitions=False"
        )


# ── Tests: audit trail ──────────────────────────────────────────────────────

class TestAuditTrail:
    """Every successful transition must produce exactly one Decision Trace entry."""

    def test_one_entry_per_transition(self, case: EscalationCase) -> None:
        case.send_reminder()
        case.escalate()
        case.hand_off()
        case.close()

        trace = get_trace("inv-001")
        assert len(trace) == 4

    def test_entries_contain_correct_events(
        self, case: EscalationCase
    ) -> None:
        case.send_reminder()
        case.escalate()

        trace = get_trace("inv-001")
        events = [e.event for e in trace]
        assert events == ["transition:send_reminder", "transition:escalate"]

    def test_blocked_trigger_writes_no_entry(
        self, policy: FakePolicyEngine
    ) -> None:
        """A guard-blocked trigger should not produce a Decision Trace entry."""
        policy.opted_out = True
        case = EscalationCase(
            invoice_id="inv-010",
            policy_engine=policy,
        )
        case.send_reminder()  # blocked

        trace = get_trace("inv-010")
        assert len(trace) == 0

    def test_trace_entries_have_hashes(
        self, case: EscalationCase
    ) -> None:
        case.send_reminder()
        trace = get_trace("inv-001")
        assert trace[0].entry_hash is not None
        assert trace[0].prev_hash is None  # first entry

    def test_chain_links(
        self, case: EscalationCase
    ) -> None:
        case.send_reminder()
        case.close()

        trace = get_trace("inv-001")
        assert len(trace) == 2
        assert trace[0].prev_hash is None
        assert trace[1].prev_hash == trace[0].entry_hash


# ── Tests: ladder index tracking ─────────────────────────────────────────────

class TestLadderIndex:
    def test_ladder_index_advances(self, case: EscalationCase) -> None:
        assert case.ladder_index == 0
        case.send_reminder()
        assert case.ladder_index == 1
        case.escalate()
        assert case.ladder_index == 2

    def test_ladder_index_in_trace(
        self, case: EscalationCase
    ) -> None:
        case.send_reminder()
        trace = get_trace("inv-001")
        assert trace[0].ladder_index == 0  # recorded before advance


# ── Tests: human_handoff terminal behaviour ───────────────────────────────────

class TestHumanHandoff:
    def test_no_automated_trigger_from_handoff(
        self, policy: FakePolicyEngine
    ) -> None:
        """After human_handoff, only close can fire."""
        case = EscalationCase(
            invoice_id="inv-011",
            policy_engine=policy,
            initial_state="human_handoff",
        )
        # send_reminder is not valid from human_handoff
        assert case.send_reminder() is False
        # escalate is not valid from human_handoff
        assert case.escalate() is False
        # hand_off is not valid from human_handoff
        assert case.hand_off() is False
        # only close works
        assert case.close() is True
        assert case.state == "closed"

    def test_needs_human_property(
        self, policy: FakePolicyEngine
    ) -> None:
        case = EscalationCase(
            invoice_id="inv-012",
            policy_engine=policy,
            initial_state="human_handoff",
        )
        assert case.needs_human is True
        case.close()
        assert case.needs_human is False


# ── Tests: repr ──────────────────────────────────────────────────────────────

class TestRepr:
    def test_repr_shows_state(self, case: EscalationCase) -> None:
        r = repr(case)
        assert "inv-001" in r
        assert "monitoring" in r
