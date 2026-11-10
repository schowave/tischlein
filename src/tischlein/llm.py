"""Minimal client for an OpenAI-compatible chat API (e.g. a LiteLLM proxy).

Only what Tischlein needs: one chat completion that returns JSON. No third-party library;
the transport is injectable for tests.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass

# transport(url, headers, body) -> (status, response bytes)
Transport = Callable[[str, Mapping[str, str], bytes], tuple[int, bytes]]


class LLMError(Exception):
    pass


def urllib_transport(url: str, headers: Mapping[str, str], body: bytes) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except OSError as e:
        raise LLMError(f"LLM nicht erreichbar: {e}") from e


def _parse_json(text: str):
    """Models sometimes wrap JSON in a code fence or add a sentence; take the JSON part."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError("Die Antwort des Modells war kein gültiges JSON.")


@dataclass
class LLMClient:
    base_url: str
    api_key: str
    model: str
    transport: Transport = urllib_transport

    def chat_json(self, system: str, user: str, temperature: float = 0.4):
        """Send one chat turn and return the parsed JSON answer."""
        url = self.base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": self.model,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        status, raw = self.transport(url, headers, json.dumps(body).encode())
        if status in (401, 403):
            raise LLMError("Das LLM hat den API-Schlüssel abgelehnt.")
        if status != 200:
            detail = raw[:200].decode(errors="replace")
            raise LLMError(f"LLM-Anfrage fehlgeschlagen (HTTP {status}): {detail}")
        try:
            content = json.loads(raw)["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise LLMError("Unerwartete Antwort vom LLM.") from e
        return _parse_json(content or "")
