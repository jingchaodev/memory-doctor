"""Thin stdlib-only client for the Anthropic Messages API.

No `anthropic` SDK, no third-party dependency — just `urllib.request`, to keep
memory-doctor's "stdlib only" promise even for the opt-in LLM tier.

This module makes zero network calls on import. A network call only happens
when something explicitly calls `LLMClient(...).complete(...)`, and the only
caller in this codebase is the L-tier (`llm_detectors.py`), which `__main__`
only wires up when the user passes `--llm`.

Privacy: the API key is read from `ANTHROPIC_API_KEY` only (never a CLI flag,
never written to a file) and is never included in any exception message,
log line, or printed output.
"""
import json
import os
import urllib.error
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_MAX_TOKENS = 2000
DEFAULT_TIMEOUT = 60


class LLMError(Exception):
    """Raised for any LLM-call failure. Never includes the API key."""


class LLMClient:
    """Real client — only ever constructed by __main__ when --llm is passed."""

    def __init__(self, api_key=None, model=None, max_tokens=DEFAULT_MAX_TOKENS,
                 timeout=DEFAULT_TIMEOUT):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise LLMError(
                "ANTHROPIC_API_KEY is not set. --llm requires your own Anthropic API "
                "key in the environment (memory-doctor never asks for or stores it)."
            )
        self.model = model or os.environ.get("MEMDOC_LLM_MODEL", DEFAULT_MODEL)
        self.max_tokens = max_tokens
        self.timeout = timeout

    def complete(self, prompt: str, system: str = "") -> str:
        """POST one message to the Anthropic Messages API, return the
        concatenated text of the response. Raises LLMError on any failure —
        HTTP error, network error, timeout, or an unexpected response shape."""
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            API_URL, data=data, method="POST",
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": ANTHROPIC_VERSION,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode()[:300]
            except Exception:
                pass
            raise LLMError(
                f"Anthropic API returned HTTP {e.code}" + (f": {detail}" if detail else "")
            ) from None
        except urllib.error.URLError as e:
            raise LLMError(f"could not reach Anthropic API: {e.reason}") from None
        except TimeoutError:
            raise LLMError(f"Anthropic API call timed out after {self.timeout}s") from None
        except json.JSONDecodeError:
            raise LLMError("Anthropic API returned a non-JSON response") from None

        try:
            parts = payload["content"]
            return "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        except (KeyError, TypeError):
            raise LLMError("unexpected response shape from Anthropic API") from None
