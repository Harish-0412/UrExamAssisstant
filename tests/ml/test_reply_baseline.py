"""Tests for the instructor-backed LLM reply classifier.

All tests mock at the ``llm_client.client.chat.completions.create`` boundary
— no network calls, no real API keys.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from instructor.core import InstructorRetryException

from src.integrations.llm_client import LLMClient
from src.ml.reply.llm_baseline import classify_reply_llm
from src.ml.schemas.reply import (
    ExtractedEntities,
    FallbackResult,
    IntentLabel,
    ReplyIntentFields,
    ReplyIntentPrediction,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def invoice_context() -> dict:
    return {
        "reply_id": "reply-001",
        "invoice_id": "inv-001",
        "outstanding_amount": 500.0,
        "days_past_due": 30,
    }


@pytest.fixture
def mock_llm_client() -> MagicMock:
    """A mock LLMClient with instructor-patched client."""
    client = MagicMock(spec=LLMClient)
    client.provider = "groq"
    client.client = MagicMock()
    return client


def _make_valid_response() -> ReplyIntentFields:
    """Return a well-formed ReplyIntentFields instance."""
    return ReplyIntentFields(
        intent=IntentLabel.PROMISE_TO_PAY,
        intent_confidence=0.92,
        entities=ExtractedEntities(
            mentioned_amount=500.0,
            mentioned_date="next Friday",
            reason_keywords=["cash flow"],
            emotional_tone="cooperative",
        ),
        explanation="The debtor explicitly promises to pay by next Friday.",
    )


# ── Tests: valid responses ────────────────────────────────────────────────────

class TestClassifyValidResponses:
    """Happy path — LLM returns well-formed structured output."""

    def test_promise_to_pay(
        self, mock_llm_client: MagicMock, invoice_context: dict
    ) -> None:
        mock_llm_client.generate_structured.return_value = _make_valid_response()

        result = classify_reply_llm(
            raw_text="I will pay by next Friday",
            invoice_context=invoice_context,
            llm_client=mock_llm_client,
        )

        assert isinstance(result, ReplyIntentPrediction)
        assert result.intent == IntentLabel.PROMISE_TO_PAY
        assert result.confidence == 0.92
        assert result.entities.mentioned_amount == 500.0
        assert result.fallback_used is False
        assert result.model_used == "instructor-llm-baseline"
        assert result.scored_at is not None

    def test_dispute_intent(
        self, mock_llm_client: MagicMock, invoice_context: dict
    ) -> None:
        mock_llm_client.generate_structured.return_value = ReplyIntentFields(
            intent=IntentLabel.DISPUTE,
            intent_confidence=0.85,
            entities=ExtractedEntities(reason_keywords=["wrong amount"]),
            explanation="Debtor claims the amount is incorrect.",
        )

        result = classify_reply_llm(
            raw_text="I don't owe that much, the invoice is wrong",
            invoice_context=invoice_context,
            llm_client=mock_llm_client,
        )

        assert result.intent == IntentLabel.DISPUTE
        assert result.fallback_used is False

    def test_reply_id_from_context(
        self, mock_llm_client: MagicMock, invoice_context: dict
    ) -> None:
        mock_llm_client.generate_structured.return_value = _make_valid_response()

        result = classify_reply_llm(
            raw_text="Okay",
            invoice_context=invoice_context,
            llm_client=mock_llm_client,
        )

        assert result.reply_id == "reply-001"
        assert result.invoice_id == "inv-001"


# ── Tests: fallback on malformed output ───────────────────────────────────────

class TestClassifyFallback:
    """InstructorRetryException → graceful degradation."""

    def test_malformed_output_returns_other(
        self, mock_llm_client: MagicMock, invoice_context: dict
    ) -> None:
        mock_llm_client.generate_structured.side_effect = (
            InstructorRetryException(
                "mocked retry failure",
                n_attempts=3,
                total_usage={
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
            )
        )

        result = classify_reply_llm(
            raw_text="some reply",
            invoice_context=invoice_context,
            llm_client=mock_llm_client,
        )

        assert result.intent == IntentLabel.OTHER
        assert result.fallback_used is True
        assert result.fallback is not None
        assert result.fallback.triggered is True
        assert result.fallback.reason == "malformed_output"
        assert result.fallback.resolved_by == "human_review_queue"
        assert result.confidence == 0.0

    def test_unexpected_exception_also_falls_back(
        self, mock_llm_client: MagicMock, invoice_context: dict
    ) -> None:
        mock_llm_client.generate_structured.side_effect = RuntimeError(
            "connection refused"
        )

        result = classify_reply_llm(
            raw_text="hello",
            invoice_context=invoice_context,
            llm_client=mock_llm_client,
        )

        assert result.intent == IntentLabel.OTHER
        assert result.fallback_used is True
        assert "connection refused" in (result.explanation or "")


# ── Tests: instructor integration boundary ────────────────────────────────────

class TestInstructorBoundary:
    """Verify the mock boundary is correct and the schema contract holds."""

    def test_generate_structured_called_with_schema(
        self, mock_llm_client: MagicMock, invoice_context: dict
    ) -> None:
        mock_llm_client.generate_structured.return_value = _make_valid_response()

        classify_reply_llm(
            raw_text="test",
            invoice_context=invoice_context,
            llm_client=mock_llm_client,
        )

        mock_llm_client.generate_structured.assert_called_once()
        call_args = mock_llm_client.generate_structured.call_args
        # Schema should be ReplyIntentFields
        assert call_args.kwargs.get("schema") == ReplyIntentFields or call_args[1].get("schema") == ReplyIntentFields

    def test_no_generate_explanation_called(
        self, mock_llm_client: MagicMock, invoice_context: dict
    ) -> None:
        mock_llm_client.generate_structured.return_value = _make_valid_response()

        classify_reply_llm(
            raw_text="test",
            invoice_context=invoice_context,
            llm_client=mock_llm_client,
        )

        # generate_explanation is for unstructured calls — should not be used here
        mock_llm_client.generate_explanation.assert_not_called()
