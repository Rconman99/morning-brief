"""Unified LLM client: Ollama (local, free) → Claude (cloud, paid) → None.

Usage:
    from lib.llm import generate_json, generate_text

    result = generate_json(prompt, max_tokens=300)
    # Tries: Ollama qwen3:8b → Claude Haiku → None

    text = generate_text(prompt, max_tokens=500)
    # Same fallback chain, returns raw text
"""

import json
import logging
import os
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

OLLAMA_API = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")
CLAUDE_MODEL = "claude-haiku-4-5-20251001"


def _call_ollama(prompt: str, max_tokens: int = 500) -> str | None:
    """Call local Ollama. Returns response text or None on failure.

    Note: qwen3 uses ~200-400 tokens for internal "thinking" before producing
    visible output. We add 400 to max_tokens to account for this overhead.
    """
    # qwen3 thinking mode needs extra token budget for internal reasoning
    actual_predict = max_tokens + 400

    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": actual_predict, "temperature": 0.3},
    }).encode()

    try:
        req = urllib.request.Request(
            f"{OLLAMA_API}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
            text = data.get("response", "").strip()
            if not text:
                # qwen3 thinking mode: response may be in 'thinking' field if
                # token budget was exhausted before generating visible output
                thinking = data.get("thinking", "").strip()
                if thinking:
                    logger.debug("Ollama response empty but thinking present (%d chars)", len(thinking))
            logger.info("Ollama (%s) responded (%d chars)", OLLAMA_MODEL, len(text))
            return text if text else None
    except (urllib.error.URLError, KeyError, TimeoutError) as e:
        logger.debug("Ollama unavailable: %s", e)
        return None


def _call_claude(prompt: str, max_tokens: int = 500) -> str | None:
    """Call Claude Haiku via Anthropic API. Returns response text or None."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        logger.info("Claude (%s) responded (%d chars)", CLAUDE_MODEL, len(text))
        return text
    except Exception as e:
        logger.warning("Claude failed: %s", e)
        return None


def _extract_json(text: str) -> dict | None:
    """Extract JSON object from a response that might have extra text."""
    if not text:
        return None
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try to find JSON in the response
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass
    return None


def generate_text(prompt: str, max_tokens: int = 500) -> str | None:
    """Generate text via Ollama → Claude fallback chain.

    Returns response text or None if all providers fail.
    """
    # Try Ollama first (free, fast, local)
    result = _call_ollama(prompt, max_tokens)
    if result:
        return result

    # Fall back to Claude (paid)
    result = _call_claude(prompt, max_tokens)
    if result:
        return result

    logger.warning("All LLM providers failed for prompt: %s...", prompt[:80])
    return None


def generate_json(prompt: str, max_tokens: int = 500) -> dict | None:
    """Generate JSON via Ollama → Claude fallback chain.

    Returns parsed dict or None if all providers fail or JSON parsing fails.
    """
    text = generate_text(prompt, max_tokens)
    if text is None:
        return None

    parsed = _extract_json(text)
    if parsed is None:
        logger.warning("Could not parse JSON from LLM response: %s...", text[:200])
    return parsed


def get_provider_status() -> dict:
    """Check which LLM providers are available."""
    status = {"ollama": False, "claude": False, "model": None}

    # Check Ollama
    try:
        req = urllib.request.Request(f"{OLLAMA_API}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            models = [m["name"] for m in data.get("models", [])]
            if OLLAMA_MODEL in models or any(OLLAMA_MODEL.split(":")[0] in m for m in models):
                status["ollama"] = True
                status["model"] = OLLAMA_MODEL
    except Exception:
        pass

    # Check Claude
    if os.getenv("ANTHROPIC_API_KEY"):
        status["claude"] = True
        if not status["model"]:
            status["model"] = CLAUDE_MODEL

    return status
