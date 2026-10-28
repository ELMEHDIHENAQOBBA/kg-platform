"""Minimal multi-provider LLM client (Ollama, Anthropic, OpenAI-compatible) + mock mode."""
import json
import re
import time
from dataclasses import dataclass

import requests

from . import config


@dataclass
class LLMResult:
    text: str
    in_tokens: int = 0
    out_tokens: int = 0
    seconds: float = 0.0


class LLMError(RuntimeError):
    pass


def parse_json(text):
    """Extract the first JSON object from an LLM answer (tolerates ```json fences and chatter)."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        a, b = text.find("{"), text.rfind("}")
        if a >= 0 and b > a:
            try:
                return json.loads(text[a:b + 1])
            except json.JSONDecodeError:
                pass
    raise LLMError("LLM did not return valid JSON")


class LLM:
    def __init__(self, provider=None):
        self.provider = (provider or config.LLM_PROVIDER).lower()

    @property
    def is_mock(self):
        return self.provider == "mock"

    @property
    def model(self):
        return {"ollama": config.OLLAMA_MODEL, "anthropic": config.ANTHROPIC_MODEL,
                "openai": config.OPENAI_MODEL}.get(self.provider, "rule-based")

    def complete(self, system, user, json_mode=True, max_tokens=2000):
        if self.is_mock:
            raise LLMError("mock provider has no completion endpoint")
        last = None
        for attempt in range(3):
            t0 = time.time()
            try:
                res = getattr(self, f"_{self.provider}")(system, user, json_mode, max_tokens)
                res.seconds = time.time() - t0
                return res
            except (requests.RequestException, KeyError) as e:
                last = e
                time.sleep(2 * (attempt + 1))
        raise LLMError(f"{self.provider} call failed: {last}")

    # --- providers ---
    def _ollama(self, system, user, json_mode, max_tokens):
        body = {"model": config.OLLAMA_MODEL, "stream": False,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "options": {"temperature": 0, "num_predict": max_tokens}}
        if json_mode:
            body["format"] = "json"
        r = requests.post(f"{config.OLLAMA_URL}/api/chat", json=body, timeout=900)
        r.raise_for_status()
        d = r.json()
        return LLMResult(d["message"]["content"], d.get("prompt_eval_count", 0), d.get("eval_count", 0))

    def _anthropic(self, system, user, json_mode, max_tokens):
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": config.ANTHROPIC_MODEL, "max_tokens": max_tokens, "system": system,
                  "messages": [{"role": "user", "content": user}]},
            timeout=300)
        r.raise_for_status()
        d = r.json()
        text = "".join(b.get("text", "") for b in d["content"])
        return LLMResult(text, d["usage"]["input_tokens"], d["usage"]["output_tokens"])

    def _openai(self, system, user, json_mode, max_tokens):
        body = {"model": config.OPENAI_MODEL, "temperature": 0, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        r = requests.post(f"{config.OPENAI_BASE_URL}/chat/completions",
                          headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"}, json=body, timeout=300)
        r.raise_for_status()
        d = r.json()
        u = d.get("usage", {})
        return LLMResult(d["choices"][0]["message"]["content"], u.get("prompt_tokens", 0), u.get("completion_tokens", 0))


def cost_usd(in_tokens, out_tokens):
    return (in_tokens * config.LLM_PRICE_IN + out_tokens * config.LLM_PRICE_OUT) / 1e6
