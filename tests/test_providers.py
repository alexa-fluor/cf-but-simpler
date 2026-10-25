"""Checks for the AI-provider layer (llm.py) against a fake OpenAI-compatible server,
including one end-to-end judge run with a real C++ compile. Run: python tests/test_providers.py"""
import http.server
import json
import os
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "LLM_BASE_URL",
          "LLM_API_KEY", "LLM_PROVIDER", "JUDGE_MODEL", "RETRY_MODEL"):
    os.environ.pop(k, None)

import llm  # noqa: E402


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""), flush=True)
    if not cond:
        check.failed += 1


check.failed = 0

CPP_OK = ('#include <bits/stdc++.h>\nusing namespace std;\nint main(){int n;scanf("%d",&n);long long best=LLONG_MIN,cur=0;'
          'for(int i=0;i<n;i++){long long x;scanf("%lld",&x);cur=max(x,cur+x);best=max(best,cur);}printf("%lld\\n",best);}\n')

STATE = {"mode": "ok", "requests": [], "fail_left": 0, "fail_code": 429}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        STATE["requests"].append(("GET", self.path, dict(self.headers), None))
        body = json.dumps({"data": [{"id": "model-b"}, {"id": "model-a"}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n))
        STATE["requests"].append(("POST", self.path, dict(self.headers), req))
        if STATE["fail_left"] > 0:
            STATE["fail_left"] -= 1
            self.send_response(STATE["fail_code"]); self.end_headers(); self.wfile.write(b'{"error":"nope"}')
            return
        system = req["messages"][0]["content"]
        if system.startswith("You judge"):
            text = json.dumps({"verdict": "correct", "plausible": True, "feedback": "ok", "missing": []})
        elif "C++17" in system:
            text = "```cpp\n" + CPP_OK + "```"
        else:
            text = "hello"
        body = json.dumps({"choices": [{"message": {"content": text}}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(body)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_port}/v1"
llm.RETRY_DELAYS = (0.01, 0.01, 0.01)


def clear_env():
    for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "LLM_BASE_URL",
              "LLM_API_KEY", "LLM_PROVIDER", "JUDGE_MODEL", "RETRY_MODEL"):
        os.environ.pop(k, None)


# ------------------------------------------------------------ auto-detection
clear_env()
check("nothing configured -> provider() is None with a helpful message",
      llm.provider() is None and "GEMINI_API_KEY" in llm.missing_message())
try:
    llm.ask("m", "s", "u")
    check("ask() with nothing configured raises", False)
except RuntimeError as e:
    check("ask() with nothing configured raises a clear error", "No AI provider" in str(e))

os.environ["GEMINI_API_KEY"] = "g"
check("gemini key alone selects gemini", llm.provider()["name"] == "gemini")
check("gemini has a default model", llm.judge_model() == "gemini-flash-latest")
os.environ["GROQ_API_KEY"] = "q"
check("gemini wins over groq in auto order", llm.provider()["name"] == "gemini")
os.environ["ANTHROPIC_API_KEY"] = "a"
check("anthropic wins over everything", llm.provider()["name"] == "anthropic" and llm.judge_model() == "claude-haiku-5-5")
os.environ["LLM_PROVIDER"] = "groq"
check("LLM_PROVIDER overrides auto order", llm.provider()["name"] == "groq")
try:
    llm.judge_model()
    check("groq without JUDGE_MODEL asks you to set it", False)
except RuntimeError as e:
    check("groq without JUDGE_MODEL asks you to set it", "JUDGE_MODEL" in str(e))
os.environ["JUDGE_MODEL"] = "llama-x"
check("JUDGE_MODEL is honoured; RETRY_MODEL defaults to it", llm.judge_model() == "llama-x" and llm.retry_model() == "llama-x")
os.environ["RETRY_MODEL"] = "other"
check("RETRY_MODEL is honoured", llm.retry_model() == "other")
os.environ["LLM_PROVIDER"] = "nonsense"
try:
    llm.provider()
    check("unknown LLM_PROVIDER rejected", False)
except RuntimeError as e:
    check("unknown LLM_PROVIDER rejected", "nonsense" in str(e))
os.environ["LLM_PROVIDER"] = "openrouter"
check("forced provider without its key -> None and a specific message",
      llm.provider() is None and "OPENROUTER_API_KEY" in llm.missing_message())

# ------------------------------------------------------------ request shape
clear_env()
os.environ["LLM_BASE_URL"] = BASE
os.environ["LLM_API_KEY"] = "secret-key"
os.environ["JUDGE_MODEL"] = "my-model"
check("LLM_BASE_URL + LLM_API_KEY selects the generic provider", llm.provider()["name"] == "openai")
STATE["requests"].clear()
out = llm.ask("my-model", "SYS", "USR", max_tokens=1500)
kind, path, headers, body = STATE["requests"][-1]
check("reply text returned", out == "hello", out)
check("POST goes to /chat/completions", path == "/v1/chat/completions", path)
check("bearer token sent", headers.get("Authorization") == "Bearer secret-key")
check("system and user messages mapped", body["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USR"}])
check("model passed through", body["model"] == "my-model")
check("token budget raised for non-Anthropic models", body["max_tokens"] == 4500, body["max_tokens"])
STATE["requests"].clear()
llm.ask("m", "s", "u", max_tokens=100)
check("small budgets get a floor of 4096", STATE["requests"][-1][3]["max_tokens"] == 4096)

check("list_models returns sorted ids", llm.list_models() == ["model-a", "model-b"])

# ------------------------------------------------------------ errors and retries
STATE.update(fail_left=2, fail_code=429)
STATE["requests"].clear()
check("429 twice then success is retried transparently", llm.ask("m", "s", "u") == "hello" and len(STATE["requests"]) == 3)
STATE.update(fail_left=99, fail_code=429)
try:
    llm.ask("m", "s", "u")
    check("persistent 429 raises", False)
except RuntimeError as e:
    check("persistent 429 explains the quota", "quota" in str(e) or "rate limit" in str(e), str(e)[:80])
STATE.update(fail_left=1, fail_code=401)
try:
    llm.ask("m", "s", "u")
    check("401 raises", False)
except RuntimeError as e:
    check("401 says the key was rejected", "rejected the API key" in str(e))
STATE.update(fail_left=1, fail_code=404)
try:
    llm.ask("m", "s", "u")
    check("404 raises", False)
except RuntimeError as e:
    check("404 points at JUDGE_MODEL", "JUDGE_MODEL" in str(e))
STATE.update(fail_left=0)

# ------------------------------------------------------------ end to end through judge.py
import judge  # noqa: E402
import sandbox  # noqa: E402

tmp = tempfile.mkdtemp()
judge.USER_DIR = os.path.join(tmp, "user")
judge.CACHE_PATH = os.path.join(tmp, "user", "cache.json")
judge.RUBRIC_PATH = os.path.join(tmp, "user", "rubrics.json")
prob = json.load(open(os.path.join(ROOT, "problems", "max-subarray.json")))
prob.setdefault("id", "max-subarray")
if sandbox.DEFAULT_LANG == "cpp":
    STATE["requests"].clear()
    r = judge.judge(prob, "Track the best block ending at each position and keep the maximum overall.", False)
    check("end to end via fake provider: correct", r["verdict"] == "correct" and r["executed"], r.get("feedback"))
    systems = [q[3]["messages"][0]["content"][:10] for q in STATE["requests"] if q[0] == "POST"]
    check("gate then translate were both sent", len(systems) == 2 and systems[0].startswith("You judge"), systems)
else:
    print("SKIP end-to-end: g++ not installed")

# ------------------------------------------------------------ claude CLI provider (fake `claude` on PATH)
clear_env()
fake_dir = tempfile.mkdtemp()
fake = os.path.join(fake_dir, "claude")
open(fake, "w").write("#!/bin/sh\necho \"$@\" > \"$0.args\"\ncat > \"$0.stdin\"\necho cli-reply\n")
os.chmod(fake, 0o755)
old_path = os.environ["PATH"]
os.environ["PATH"] = fake_dir + os.pathsep + old_path
check("claude-cli is never auto-selected", llm.provider() is None)
os.environ["LLM_PROVIDER"] = "claude-cli"
check("claude-cli selected when forced; default model haiku", llm.provider()["name"] == "claude-cli" and llm.judge_model() == "haiku")
out = llm.ask("haiku", "SYSPROMPT", "the user text")
args = open(fake + ".args").read()
check("cli: reply returned, system prompt + model passed, tools off",
      out.strip() == "cli-reply" and "SYSPROMPT" in args and "--model haiku" in args and "--tools" in args, args[:80])
check("cli: user text sent on stdin", open(fake + ".stdin").read() == "the user text")
os.environ["PATH"] = "/nonexistent"
check("claude-cli without the binary gives a specific message", llm.provider() is None and "claude" in llm.missing_message())
os.environ["PATH"] = old_path

print(f"\n{check.failed} failure(s)")
srv.shutdown()
sys.exit(1 if check.failed else 0)
