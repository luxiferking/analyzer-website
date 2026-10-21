# core/ai_client.py
"""
Multi-provider AI client with FULL FIXES:
✅ Gemini 429 auto-fallback
✅ OpenRouter multi-model fallback chain
✅ Reasoning disabled (content null fix)
✅ Robust error handling
"""
import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()


# ==========================================
# OPENROUTER FALLBACK MODELS (Priority order)
# ==========================================
OPENROUTER_FALLBACK_MODELS = [
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "inclusionai/ling-3.0-flash-sante:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "dots-studio/dots-3-note-preview:free",
]


# ==========================================
# PROVIDER 1: GOOGLE GEMINI
# ==========================================
def ask_gemini(prompt, max_tokens=2000):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None

    model = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": max_tokens,
        }
    }

    try:
        r = requests.post(url, json=payload, timeout=60)
        if r.status_code == 200:
            data = r.json()
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", "")
        elif r.status_code == 429:
            print(f"   ⚠️ Gemini quota exceeded (429) — switching to OpenRouter")
            return None
        else:
            print(f"   ⚠️ Gemini error {r.status_code}")
    except Exception as e:
        print(f"   [Gemini error] {e}")
    return None


# ==========================================
# PROVIDER 2: OPENROUTER (with multi-model fallback)
# ==========================================
def ask_openrouter(prompt, max_tokens=2000):
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None

    # Primary model + fallbacks
    primary_model = os.getenv("OPENROUTER_MODEL", "google/gemma-4-31b-it:free")
    models_to_try = [primary_model] + [m for m in OPENROUTER_FALLBACK_MODELS if m != primary_model]

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://jarvisagent.local",
        "X-Title": "Jarvis Trading Agent",
    }

    for model in models_to_try:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": max_tokens,
            "reasoning": {"enabled": False},
        }

        try:
            r = requests.post(url, headers=headers, json=payload, timeout=60)

            if r.status_code == 200:
                data = r.json()
                msg = data["choices"][0]["message"]
                content = msg.get("content")

                if content:
                    print(f"   ✅ OpenRouter responded ({model.split('/')[1]})")
                    return content

                # Content null — reasoning used all tokens
                finish_reason = data["choices"][0].get("finish_reason")
                if finish_reason == "length":
                    # Retry with higher budget
                    retry = _openrouter_retry(prompt, max_tokens * 3, api_key, model, headers)
                    if retry:
                        return retry
                # Last fallback: reasoning text
                reasoning = msg.get("reasoning", "")
                if reasoning:
                    return reasoning
                # Move to next model
                continue

            elif r.status_code == 404:
                # Model unavailable — try next
                print(f"   ⏭️  {model.split('/')[1]} unavailable, trying next...")
                continue

            elif r.status_code == 429:
                print(f"   ⏳ {model.split('/')[1]} rate-limited, trying next...")
                time.sleep(1)
                continue

            else:
                print(f"   ⚠️ {model.split('/')[1]} error {r.status_code}")
                continue

        except Exception as e:
            print(f"   [OpenRouter error with {model.split('/')[1]}] {e}")
            continue

    return None


def _openrouter_retry(prompt, new_max_tokens, api_key, model, headers):
    """Retry with higher max_tokens when reasoning consumed the budget."""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": new_max_tokens,
        "reasoning": {"enabled": False, "effort": "low"},
    }
    try:
        r = requests.post("https://openrouter.ai/api/v1/chat/completions",
                          headers=headers, json=payload, timeout=90)
        if r.status_code == 200:
            msg = r.json()["choices"][0]["message"]
            content = msg.get("content")
            if content:
                print(f"   ✅ OpenRouter retry success ({model.split('/')[1]})")
                return content
            return msg.get("reasoning", "")
    except Exception as e:
        print(f"   [OpenRouter retry error] {e}")
    return None


# ==========================================
# MAIN: MULTI-PROVIDER FALLBACK
# ==========================================
# ==========================================
# MAIN: MULTI-PROVIDER FALLBACK
# ==========================================
def ask_groq(prompt: str, max_tokens: int = 2000) -> str:
    """
    Fallback chain (REORDERED by reliability):
    1. Groq (fastest, 14,400 RPD)
    2. Gemini (best quality, 1,500 RPD)
    3. NVIDIA NIM (10,000 RPD, when available)
    4. OpenRouter (fallback)
    """
    # 1. Groq (fastest + highest RPD)
    print("   🤖 Trying Groq...")
    result = ask_groq_api(prompt, max_tokens)
    if result:
        print("   ✅ Groq responded")
        return result

    # 2. Gemini
    print("   🤖 Trying Gemini...")
    result = ask_gemini(prompt, max_tokens)
    if result:
        print("   ✅ Gemini responded")
        return result

    # 3. NVIDIA NIM
    print("   🤖 Trying NVIDIA NIM...")
    result = ask_nvidia(prompt, max_tokens)
    if result:
        print("   ✅ NVIDIA responded")
        return result

    # 4. OpenRouter
    print("   🤖 Trying OpenRouter...")
    result = ask_openrouter(prompt, max_tokens)
    if result:
        print("   ✅ OpenRouter responded")
        return result

    return "Error: All AI providers failed."

# core/ai_client.py

# ... (existing imports)

# ==========================================
# PROVIDER 3: NVIDIA NIM
# ==========================================
def ask_nvidia(prompt, max_tokens=2000):
    api_key = os.getenv("NVIDIA_API_KEY")
    if not api_key:
        return None
    
    model = os.getenv("NVIDIA_MODEL", "nvidia/nemotron-3-super-120b-a12b")
    url = "https://integrate.api.nvidia.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
        elif r.status_code == 429:
            print(f"   ⚠️ NVIDIA NIM rate-limited")
    except Exception as e:
        print(f"   [NVIDIA error] {e}")
    return None


# ==========================================
# PROVIDER 4: GROQ (via ai_client)
# ==========================================
def ask_groq_api(prompt, max_tokens=2000):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
    
    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
        elif r.status_code == 429:
            print(f"   ⚠️ Groq rate-limited")
    except Exception as e:
        print(f"   [Groq error] {e}")
    return None


# ==========================================
# PROVIDER 5: CEREBRAS
# ==========================================
def ask_cerebras(prompt, max_tokens=2000):
    api_key = os.getenv("CEREBRAS_API_KEY")
    if not api_key:
        return None
    
    model = os.getenv("CEREBRAS_MODEL", "llama3.1-8b")
    url = "https://api.cerebras.ai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
        elif r.status_code == 429:
            print(f"   ⚠️ Cerebras rate-limited")
    except Exception as e:
        print(f"   [Cerebras error] {e}")
    return None