"""Pydantic schemas for the reply-intent classification pipeline.

These schemas serve two purposes:
1. They are the *instructor response_model* — the LLM is constrained to return
   data matching these fields, with automatic validation and retry on failure.
2. They are the *internal data contracts* passed between the ML classifier,
   the escalation engine, and the decision trace ledger.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ── Intent labels ──────────────────────────────────────────────────────────────

class IntentLabel(str, Enum):
    """Every classified reply lands in exactly one of these buckets."""

    ACKNOWLEDGED = "acknowledged"
    PROMISE_TO_PAY = "promise_to_pay"
    DISPUTE = "dispute"
    PARTIAL_PAYMENT = "partial_payment"
    HARDSHIP = "hardship"
    REQUEST_MORE_INFO = "request_more_info"
    OUT_OF_OFFICE = "out_of_office"
    OTHER = "other"


# ── Extracted entities ────────────────────────────────────────────────────────

class ExtractedEntities(BaseModel):
    """Free-text entity extraction from the debtor reply."""

    mentioned_amount: Optional[float] = Field(
        None, description="Any specific monetary amount mentioned in the reply."
    )
    mentioned_date: Optional[str] = Field(
        None, description="Any specific date or time reference (e.g. 'next Friday')."
    )
    reason_keywords: list[str] = Field(
        default_factory=list,
        description="Short phrases indicating the reason for non-payment.",
    )
    emotional_tone: Optional[str] = Field(
        None,
        description="Detected tone: cooperative, frustrated, aggressive, neutral.",
    )


# ── Instructor-facing schema (what the LLM must return) ──────────────────────

class ReplyIntentFields(BaseModel):
    """Schema the LLM is constrained to produce via instructor.

    This is deliberately stripped of pipeline-envelope fields (reply_id,
    invoice_id, scored_at …) — those are added *after* the LLM call by the
    classifier wrapper. Keeping the LLM schema small reduces token cost and
    makes validation tighter.
    """

    intent: IntentLabel = Field(
        ..., description="Primary intent classification of the debtor reply."
    )
    intent_confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Calibrated confidence score 0-1."
    )
    entities: ExtractedEntities = Field(
        default_factory=ExtractedEntities,
        description="Structured entities extracted from the reply text.",
    )
    explanation: str = Field(
        ...,
        description=(
            "One-sentence justification for the chosen intent label. "
            "Used in the Decision Trace as the 'reasoning' field."
        ),
    )


# ── Full prediction envelope (pipeline-internal) ─────────────────────────────

class FallbackResult(BaseModel):
    """Metadata when the primary model fails and a fallback fires."""

    triggered: bool = False
    reason: Optional[str] = None
    resolved_by: Optional[str] = None


class ReplyIntentPrediction(BaseModel):
    """Complete prediction record stored in the event store."""

    reply_id: str
    invoice_id: str
    raw_text: str
    intent: IntentLabel
    intent_confidence: float = 0.0
    entities: ExtractedEntities = Field(default_factory=ExtractedEntities)
    explanation: str = ""
    model_used: str = ""
    model_version: str = ""
    confidence: float = 0.0
    fallback_used: bool = False
    fallback: Optional[FallbackResult] = None
    scored_at: Optional[datetime] = None
    metadata: dict = Field(default_factory=dict)

    model_config = ConfigDict(
        json_encoders={datetime: lambda v: v.isoformat() if v else None},
    )
