"""LLM client: dual-model ensemble via OpenRouter for better probability calibration.

Calls two models independently and returns both estimates.
When both agree (within 5%), confidence is HIGH.
When they disagree (15%+), confidence is LOW — skip the trade.
"""
import json, logging, os, urllib.request, urllib.error

logger = logging.getLogger(__name__)

OPENROUTER_API = "https://openrouter.ai/api/v1/chat/completions"
MODEL_A = os.getenv("OPENROUTER_MODEL_A", "google/gemma-4-31b-it:free")
MODEL_B = os.getenv("OPENROUTER_MODEL_B", "nvidia/nemotron-3-super-120b-a12b:free")
OLLAMA_API = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")

def _call_openrouter(prompt, max_tokens=500, model=None):
    key = os.getenv("OPENROUTER_API_KEY", "")
    if not key: return None
    model = model or MODEL_A
    payload = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens, "temperature": 0.3}).encode()
    try:
        req = urllib.request.Request(OPENROUTER_API, data=payload, headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            if text: logger.info("OpenRouter (%s) responded (%d chars)", model.split("/")[-1][:20], len(text))
            return text if text else None
    except Exception as e:
        logger.debug("OpenRouter %s failed: %s", model, e)
        return None

def _call_ollama(prompt, max_tokens=500):
    payload = json.dumps({"model": OLLAMA_MODEL, "prompt": prompt, "stream": False, "options": {"num_predict": max_tokens + 400, "temperature": 0.3}}).encode()
    try:
        req = urllib.request.Request(f"{OLLAMA_API}/api/generate", data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            text = json.loads(resp.read()).get("response", "").strip()
            return text if text else None
    except: return None

def _extract_json(text):
    if not text: return None
    try: return json.loads(text)
    except: pass
    s, e = text.find("{"), text.rfind("}") + 1
    if s >= 0 and e > s:
        try: return json.loads(text[s:e])
        except: pass
    return None

def generate_text(prompt, max_tokens=500):
    return _call_openrouter(prompt, max_tokens, MODEL_A) or _call_ollama(prompt, max_tokens)

def generate_json(prompt, max_tokens=500):
    text = generate_text(prompt, max_tokens)
    if text is None: return None
    parsed = _extract_json(text)
    if parsed is None: logger.warning("Could not parse JSON: %s...", text[:100])
    return parsed

def ensemble_probability(prompt, max_tokens=300):
    """Ask two models for a probability estimate independently.
    
    Returns: {"probability": float, "confidence": "high"|"medium"|"low",
              "model_a": float, "model_b": float, "spread": float, "reasoning": str}
    """
    result_a = _call_openrouter(prompt, max_tokens, MODEL_A)
    result_b = _call_openrouter(prompt, max_tokens, MODEL_B)
    
    def extract_prob(text):
        if not text: return None
        parsed = _extract_json(text)
        if parsed and "probability" in parsed:
            return float(parsed["probability"])
        # Try to find a decimal number
        import re
        nums = re.findall(r'0\.\d+', text)
        if nums: return float(nums[0])
        pcts = re.findall(r'(\d{1,3})%', text)
        if pcts: return float(pcts[0]) / 100
        return None
    
    prob_a = extract_prob(result_a)
    prob_b = extract_prob(result_b)
    
    if prob_a is None and prob_b is None:
        return {"probability": None, "confidence": "none", "model_a": None, "model_b": None, "spread": None, "reasoning": "Both models failed"}
    
    if prob_a is None:
        return {"probability": prob_b, "confidence": "low", "model_a": None, "model_b": prob_b, "spread": None, "reasoning": "Only one model responded"}
    if prob_b is None:
        return {"probability": prob_a, "confidence": "low", "model_a": prob_a, "model_b": None, "spread": None, "reasoning": "Only one model responded"}
    
    spread = abs(prob_a - prob_b)
    avg = (prob_a + prob_b) / 2
    
    if spread <= 0.05:
        confidence = "high"  # Models agree closely
    elif spread <= 0.15:
        confidence = "medium"
    else:
        confidence = "low"  # Models disagree — high uncertainty
    
    reasoning_a = (result_a or "")[:100]
    reasoning_b = (result_b or "")[:100]
    
    logger.info("Ensemble: A=%.2f B=%.2f spread=%.2f conf=%s", prob_a, prob_b, spread, confidence)
    
    return {
        "probability": round(avg, 3),
        "confidence": confidence,
        "model_a": round(prob_a, 3),
        "model_b": round(prob_b, 3),
        "spread": round(spread, 3),
        "reasoning": f"Gemma: {reasoning_a[:50]} | Llama: {reasoning_b[:50]}",
    }

def get_provider_status():
    return {"openrouter": bool(os.getenv("OPENROUTER_API_KEY")), "ollama": False, "model": f"{MODEL_A} + {MODEL_B}"}
