import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory_doctor.llm import LLMClient, LLMError, DEFAULT_MODEL


class _FakeResponse:
    """Mimics the context-manager object urlopen() returns."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestLLMClientConstruction(unittest.TestCase):
    def test_missing_key_raises_clean_error(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(LLMError) as cm:
                LLMClient()
            # key must never appear anywhere, obviously, but also check the
            # message is actionable and doesn't dump env/secrets.
            self.assertIn("ANTHROPIC_API_KEY", str(cm.exception))

    def test_explicit_key_bypasses_env(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            client = LLMClient(api_key="sk-test-123")
            self.assertEqual(client.api_key, "sk-test-123")

    def test_default_model_from_env(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k",
                                             "MEMDOC_LLM_MODEL": "claude-custom"}, clear=True):
            client = LLMClient()
            self.assertEqual(client.model, "claude-custom")

    def test_default_model_fallback(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"}, clear=True):
            client = LLMClient()
            self.assertEqual(client.model, DEFAULT_MODEL)


class TestLLMClientComplete(unittest.TestCase):
    def setUp(self):
        self.client = LLMClient(api_key="sk-test-not-real")

    def test_complete_parses_text_response(self):
        payload = json.dumps({"content": [{"type": "text", "text": "hello world"}]}).encode()
        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(payload)) as m:
            out = self.client.complete("prompt", system="sys")
        self.assertEqual(out, "hello world")
        # verify the key was sent as a header, never in the body/prompt
        req = m.call_args[0][0]
        self.assertEqual(req.headers.get("X-api-key"), "sk-test-not-real")

    def test_http_error_raises_llmerror_without_leaking_key(self):
        err = urllib.error.HTTPError(
            url="https://api.anthropic.com/v1/messages", code=401, msg="unauthorized",
            hdrs=None, fp=io.BytesIO(b'{"error": "invalid x-api-key"}'))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(LLMError) as cm:
                self.client.complete("prompt")
        self.assertNotIn("sk-test-not-real", str(cm.exception))
        self.assertIn("401", str(cm.exception))

    def test_url_error_raises_llmerror(self):
        with mock.patch("urllib.request.urlopen",
                         side_effect=urllib.error.URLError("no route to host")):
            with self.assertRaises(LLMError):
                self.client.complete("prompt")

    def test_unexpected_shape_raises_llmerror(self):
        payload = json.dumps({"unexpected": "shape"}).encode()
        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
            with self.assertRaises(LLMError):
                self.client.complete("prompt")

    def test_non_json_body_raises_llmerror(self):
        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(b"not json")):
            with self.assertRaises(LLMError):
                self.client.complete("prompt")


if __name__ == "__main__":
    unittest.main()
