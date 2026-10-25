"""Talks to whichever LLM provider is configured.

Anthropic is used when ANTHROPIC_API_KEY is set. Otherwise any OpenAI-compatible endpoint
works, which covers the free tiers of Google Gemini, Groq and OpenRouter, or anything else
you point LLM_BASE_URL at. Free tiers change often, so nothing here depends on one provider.

Settings (environment variables):
  LLM_PROVIDER   anthropic | gemini | groq | openrouter | openai   (default: first one with a key)
  ANTHROPIC_API_KEY, GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY
  LLM_BASE_URL + LLM_API_KEY   for provider "openai" (any OpenAI-compatible server)
  LLM_PROVIDER=claude-cli   run the `claude` CLI (Claude Code) logged in to your own account:
                 model aliases like haiku / sonnet. Uses your plan's limits; slower per call.
                 Check Anthropic's current terms for scripted use of a subscription login.
  JUDGE_MODEL    model for the rubric gate and first translation
  RETRY_MODEL    model for the second translation (default: same as JUDGE_MODEL)
"""
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request

RETRY_DELAYS = (3, 8, 20)  # seconds to wait before retrying a rate-limited / flaky call

PRESETS = {
    "anthropic": {"kind": "anthropic", "key_env": "ANTHROPIC_API_KEY", "judge": "claude-haiku-5-5"},
    "gemini": {"kind": "openai", "base": "https://generativelanguage.googleapis.com/v1beta/openai",
               "key_env": "GEMINI_API_KEY", "judge": "gemini-flash-latest"},
    "groq": {"kind": "openai", "base": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY", "judge": None},
    "openrouter": {"kind": "openai", "base": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY", "judge": None},
    "openai": {"kind": "openai", "base": None, "key_env": "LLM_API_KEY", "judge": None},
    "claude-cli": {"kind": "cli", "key_env": "(claude login)", "judge": "haiku"},
}
AUTO_ORDER = ["anthropic", "gemini", "groq", "openrouter", "openai"]  # claude-cli is explicit only


def _resolve(name):
    p = dict(PRESETS[name], name=name)
    if p["kind"] == "cli":
        return p if shutil.which("claude") else None
    if name == "openai":
        p["base"] = os.environ.get("LLM_BASE_URL")
    key = os.environ.get(p["key_env"])
    if key and (p["kind"] == "anthropic" or p.get("base")):
        p["key"] = key
        return p
    return None


def provider():
    """The active provider as a dict, or None if nothing is configured."""
    name = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if name:
        if name not in PRESETS:
            raise RuntimeError(f"LLM_PROVIDER={name!r} is not one of: {', '.join(PRESETS)}")
        return _resolve(name)
    for n in AUTO_ORDER:
        p = _resolve(n)
        if p:
            return p
    return None


def missing_message():
    name = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if name in PRESETS:
        need = ("LLM_BASE_URL and LLM_API_KEY" if name == "openai" else
                "the `claude` command (install Claude Code and run `claude` once to log in)" if name == "claude-cli"
                else PRESETS[name]["key_env"])
        return f"LLM_PROVIDER is '{name}' but {need} is not available/set."
    return ("No AI provider is configured. Set one of ANTHROPIC_API_KEY, GEMINI_API_KEY (free tier), "
            "GROQ_API_KEY, OPENROUTER_API_KEY, or LLM_BASE_URL + LLM_API_KEY. See the README.")


def judge_model():
    m = os.environ.get("JUDGE_MODEL")
    if m:
        return m
    p = provider()
    if p is None:
        return PRESETS["anthropic"]["judge"]  # ask() will explain what is missing
    if not p["judge"]:
        raise RuntimeError(f"Set JUDGE_MODEL: there is no default model for provider '{p['name']}'. "
                           "Run `python scripts/check_llm.py --models` to list the ones your key can use.")
    return p["judge"]


def retry_model():
    return os.environ.get("RETRY_MODEL") or judge_model()


def ask(model, system, user, max_tokens=1500):
    p = provider()
    if p is None:
        raise RuntimeError(missing_message())
    if p["kind"] == "anthropic":
        import anthropic
        r = anthropic.Anthropic(api_key=p["key"]).messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}])
        return "".join(b.text for b in r.content if b.type == "text")
    if p["kind"] == "cli":
        return _claude_cli(model, system, user)
    # Some models "think" before answering and that can eat the budget, so give them room.
    return _openai_chat(p, model, system, user, max(max_tokens * 3, 4096))


def _claude_cli(model, system, user):
    cmd = ["claude", "-p", "--model", model, "--system-prompt", system, "--tools", "",
           "--no-session-persistence", "--disable-slash-commands"]
    last = ""
    for delay in (0,) + RETRY_DELAYS[:1]:
        if delay:
            time.sleep(delay)
        try:
            r = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            last = "timed out"
            continue
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
        last = (r.stderr or r.stdout).strip()[:300] or f"exit code {r.returncode}"
    raise RuntimeError(f"The claude CLI failed: {last}. Run `claude` once in the terminal to check you are "
                       "logged in and haven't hit your plan's usage limit.")


def list_models(p=None):
    """Model ids the key can use (OpenAI-compatible providers only)."""
    p = p or provider()
    if p is None or p["kind"] != "openai":
        return []
    req = urllib.request.Request(p["base"].rstrip("/") + "/models",
                                 headers={"Authorization": "Bearer " + p["key"], "User-Agent": "logic-judge"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    items = data.get("data", data.get("models", []))
    return sorted(str(x.get("id") or x.get("name")) for x in items)


def _openai_chat(p, model, system, user, max_tokens):
    url = p["base"].rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens,
    }).encode()
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + p["key"],
               "User-Agent": "logic-judge"}
    last = ("unknown", "")
    data = None
    for delay in (0,) + RETRY_DELAYS:
        if delay:
            time.sleep(delay)
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                data = json.load(r)
            break
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")[:300]
            if e.code in (429, 500, 502, 503, 504):
                last = (e.code, text)
                continue
            if e.code in (401, 403):
                raise RuntimeError(f"{p['name']} rejected the API key (HTTP {e.code}). Check that the "
                                   f"{p['key_env']} secret is correct and still valid. {text}")
            if e.code in (400, 404):
                raise RuntimeError(f"{p['name']} says the request or model is invalid (HTTP {e.code}): {text} "
                                   f"-- check JUDGE_MODEL (currently '{model}').")
            raise RuntimeError(f"{p['name']} returned HTTP {e.code}: {text}")
        except urllib.error.URLError as e:
            last = ("network", str(e.reason))
            continue
    if data is None:
        code, text = last
        if code == 429:
            raise RuntimeError(f"{p['name']} says the free quota or rate limit is used up (HTTP 429). "
                               "Wait a bit (per-minute limits) or until tomorrow (daily limits), or switch provider.")
        raise RuntimeError(f"{p['name']} could not be reached or kept failing ({code}): {text}")
    try:
        content = data["choices"][0]["message"].get("content")
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"{p['name']} returned an unexpected reply: {str(data)[:200]}")
    if not content:
        raise RuntimeError(f"{p['name']} returned an empty reply (the model may have spent its whole "
                           "budget thinking; try another JUDGE_MODEL).")
    return content
