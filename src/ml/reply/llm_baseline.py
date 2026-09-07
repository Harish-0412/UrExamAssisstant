"""Reply-intent classifier using instructor for structured LLM output.

Phase 3 implementation.  The LLM is called once; instructor guarantees the
response matches `ReplyIntentFields` or retries automatically.  If all retries
fail the prediction degrades to OTHER with `fallback_used=True`.
"""

from __future__ import annotations

import logging
from typing import Optional

from instructor.core import InstructorRetryException

from src.integrations.llm_client import LLMClient
from src.ml.schemas.reply import (
    ExtractedEntities,
    FallbackResult,
    IntentLabel,
    ReplyIntentFields,
    ReplyIntentPrediction,
)
from src.ml.versioning import utc_now

log = logging.getLogger(__name__)


# ── Prompt construction ────────────────────────────────────────────────────────

_INTENT_LABELS = "\n".join(f"- {label.value}" for label in IntentLabel)

_INTENT_PROMPT_TEMPLATE = """\
You are a debt-collection reply classifier.  Given the debtor's reply text
and the invoice context, classify the primary intent.

Possible intents:
{labels}

Also extract any entities: mentioned amounts, dates, reason keywords, and
emotional tone.

Reply text:
\"\"\"{raw_text}\"\"\"

Invoice context:
- Invoice ID: {invoice_id}
- Outstanding amount: {outstanding_amount}
- Days past due: {days_past_due}

Return a JSON object with keys: intent, intent_confidence, entities, explanation.
"""


def build_intent_prompt(raw_text: str, invoice_context: dict) -> str:
    """Assemble the few-shot prompt for intent classification."""
    return _INTENT_PROMPT_TEMPLATE.format(
        labels=_INTENT_LABELS,
        raw_text=raw_text,
        invoice_id=invoice_context.get("invoice_id", "unknown"),
        outstanding_amount=invoice_context.get("outstanding_amount", "unknown"),
        days_past_due=invoice_context.get("days_past_due", "unknown"),
    )


# ── Classifier ─────────────────────────────────────────────────────────────────

def classify_reply_llm(
    raw_text: str,
    invoice_context: dict,
    llm_client: LLMClient,
    *,
    reply_id: str = "",
    fallback_intent: IntentLabel = IntentLabel.OTHER,
    fallback_confidence: float = 0.0,
) -> ReplyIntentPrediction:
    """Classify a debtor reply using the LLM via instructor.

    On malformed / unparseable output (after retries), returns a fallback
    prediction with ``fallback_used=True`` — never raises to the caller.
    """
    prompt = build_intent_prompt(raw_text, invoice_context)
    resolved_reply_id = reply_id or invoice_context.get("reply_id", "unknown")
    resolved_invoice_id = invoice_context.get("invoice_id", "unknown")

    try:
        result = llm_client.generate_structured(
            prompt, schema=ReplyIntentFields, max_retries=2
        )
        return ReplyIntentPrediction(
            reply_id=resolved_reply_id,
            invoice_id=resolved_invoice_id,
            raw_text=raw_text,
            intent=result.intent,
            intent_confidence=result.intent_confidence,
            entities=result.entities,
            explanation=result.explanation,
            model_used="instructor-llm-baseline",
            model_version="llm-baseline-v1",
            confidence=result.intent_confidence,
            fallback_used=False,
            scored_at=utc_now(),
        )
    except InstructorRetryException:
        log.warning(
            "LLM output failed validation after retries — degrading to fallback."
        )
        return ReplyIntentPrediction(
            reply_id=resolved_reply_id,
            invoice_id=resolved_invoice_id,
            raw_text=raw_text,
            intent=fallback_intent,
            entities=ExtractedEntities(),
            explanation="Fallback: LLM output could not be validated.",
            model_used="instructor-llm-baseline",
            model_version="llm-baseline-v1",
            confidence=fallback_confidence,
            fallback_used=True,
            fallback=FallbackResult(
                triggered=True,
                reason="malformed_output",
                resolved_by="human_review_queue",
            ),
            scored_at=utc_now(),
        )
    except Exception as exc:
        log.warning("Unexpected error in LLM classifier: %s", exc)
        return ReplyIntentPrediction(
            reply_id=resolved_reply_id,
            invoice_id=resolved_invoice_id,
            raw_text=raw_text,
            intent=fallback_intent,
            entities=ExtractedEntities(),
            explanation=f"Fallback: unexpected error — {exc}",
            model_used="instructor-llm-baseline",
            model_version="llm-baseline-v1",
            confidence=fallback_confidence,
            fallback_used=True,
            fallback=FallbackResult(
                triggered=True,
                reason=f"exception:{type(exc).__name__}",
                resolved_by="human_review_queue",
            ),
            scored_at=utc_now(),
        )
