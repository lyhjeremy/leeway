"""Gemini client for voice-query parsing only - the one place this backend
uses an LLM. Reuses the exact pattern proven on an earlier project
([[skillcompass-flagship-project]]), including two real fixes learned there
the hard way: gemini-2.5-flash spends output-token budget on invisible
"thinking" tokens by default (thinkingBudget must be forced to 0 for a
single-shot extraction call like this one), and the free tier returns a
real, retriable 503 under load.
"""

import asyncio
import os

import httpx

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")  # 2.0-flash has no free-tier quota
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"


class GeminiNotConfigured(Exception):
    pass


async def gemini_generate_json(prompt: str, max_tokens: int = 512) -> str:
    if not GEMINI_API_KEY:
        raise GeminiNotConfigured("GEMINI_API_KEY is not set")

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "thinkingConfig": {"thinkingBudget": 0},
            "responseMimeType": "application/json",
        },
    }

    async with httpx.AsyncClient(timeout=20) as client:
        for attempt in range(2):
            resp = await client.post(GEMINI_URL, params={"key": GEMINI_API_KEY}, json=body)
            if resp.status_code == 503 and attempt == 0:
                await asyncio.sleep(2)
                continue
            resp.raise_for_status()
            data = resp.json()
            break

    candidate = data["candidates"][0]
    if candidate.get("finishReason") == "MAX_TOKENS":
        raise RuntimeError(f"Gemini response truncated at max_tokens={max_tokens}")
    return candidate["content"]["parts"][0]["text"]
