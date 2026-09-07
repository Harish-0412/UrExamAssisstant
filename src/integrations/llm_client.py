"""Provider-agnostic LLM client wrapping instructor.

instructor adds automatic structured-output validation and retry on top of
any supported LLM provider.  Unstructured calls (e.g. generating explanation
narration) still go through the raw client directly — instructor is scoped
only to schema-bound calls via `generate_structured()`.
"""

from __future__ import annotations

from typing import Any, Optional, Type

from pydantic import BaseModel


class LLMClient:
    """Thin wrapper that routes to the right instructor-patched client."""

    def __init__(self, provider: str, api_key: str, *, model: Optional[str] = None):
        self.provider = provider
        self.model = model
        self._raw: Any = None
        self.client: Any = None  # instructor-patched client
        self._init_provider(api_key)

    # ── Provider wiring ────────────────────────────────────────────────────────

    def _init_provider(self, api_key: str) -> None:
        import instructor

        if self.provider == "groq":
            from groq import Groq

            self._raw = Groq(api_key=api_key)
            self.client = instructor.from_groq(self._raw, mode=instructor.Mode.JSON)
        elif self.provider == "anthropic":
            from anthropic import Anthropic

            self._raw = Anthropic(api_key=api_key)
            self.client = instructor.from_anthropic(self._raw)
        elif self.provider == "openai":
            from openai import OpenAI

            self._raw = OpenAI(api_key=api_key)
            self.client = instructor.from_openai(self._raw, mode=instructor.Mode.JSON)
        elif self.provider == "gemini":
            import google.generativeai as genai

            genai.configure(api_key=api_key)
            model_name = self.model or "gemini-1.5-flash"
            genai_model = genai.GenerativeModel(model_name)
            self._raw = genai_model
            self.client = instructor.from_gemini(
                genai_model, mode=instructor.Mode.GEMINI_JSON
            )
        else:
            raise ValueError(
                f"Unsupported LLM_PROVIDER: {self.provider!r}. "
                f"Expected one of: groq, anthropic, openai, gemini."
            )

    # ── Structured calls (instructor validates + retries) ──────────────────────

    def generate_structured(
        self, prompt: str, schema: Type[BaseModel], *, max_retries: int = 2
    ) -> BaseModel:
        """Send *prompt* and constrain the response to *schema*.

        instructor handles JSON-mode extraction, Pydantic validation, and
        automatic retry on malformed output.  If all retries fail it raises
        ``instructor.exceptions.InstructorRetryException`` — callers should
        catch that and degrade gracefully.
        """
        return self.client.chat.completions.create(
            response_model=schema,
            messages=[{"role": "user", "content": prompt}],
            max_retries=max_retries,
        )

    # ── Unstructured calls (raw provider) ──────────────────────────────────────

    def generate_explanation(self, prompt: str) -> str:
        """Free-text generation — used for Decision Trace narration, etc.

        This intentionally does *not* go through instructor; there is no
        schema to validate against.
        """
        if self.provider == "groq":
            resp = self._raw.chat.completions.create(
                model=self.model or "llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.choices[0].message.content or ""
        elif self.provider == "anthropic":
            resp = self._raw.messages.create(
                model=self.model or "claude-3-haiku-20240307",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.content[0].text
        elif self.provider == "openai":
            resp = self._raw.chat.completions.create(
                model=self.model or "gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.choices[0].message.content or ""
        elif self.provider == "gemini":
            resp = self._raw.generate_content(prompt)
            return resp.text or ""
        else:
            raise ValueError(f"Unsupported provider: {self.provider!r}")
