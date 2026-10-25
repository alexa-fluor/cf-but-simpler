"""Checks for the online-hosting pieces: the release downloader (against a fake GitHub API),
the optional password gate, and the Codespaces config. Run: python tests/test_hosting.py"""
import base64
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""), flush=True)
    if not cond:
        check.failed += 1


check.failed = 0

# ---------------------------------------------------------------- fake GitHub release server
FILES = {"c1-599.sqlite": 100, "c600-1099.sqlite": 200, "c1900-2299.sqlite": 300,
         "c1900-2299.manifest.json": 50}
MISSING_TAG = {"flag": False}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/repos/o/r/releases/tags/"):
            if MISSING_TAG["flag"]:
                self.send_response(404); self.end_headers(); return
            base = f"http://127.0.0.1:{self.server.server_port}"
            body = json.dumps({"assets": [
                {"name": n, "size": s, "browser_download_url": f"{base}/dl/{n}"} for n, s in FILES.items()]}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/dl/"):
            name = self.path[4:]
            self.send_response(200); self.end_headers()
            self.wfile.write(b"x" * FILES[name])
        else:
            self.send_response(404); self.end_headers()


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
API = f"http://127.0.0.1:{srv.server_port}"


def fetch(data_dir, *args):
    return subprocess.run([sys.executable, "scripts/fetch_data.py", "--repo", "o/r", "--api-base", API,
                           "--data-dir", data_dir, *args], capture_output=True, text=True)


d = tempfile.mkdtemp()
r = fetch(d, "--list")
check("--list shows sizes and downloads nothing", r.returncode == 0 and os.listdir(d) == [] and "c1900-2299.sqlite" in r.stdout
      and "manifest" not in r.stdout, r.stdout.strip().splitlines()[:2])

d = tempfile.mkdtemp()
r = fetch(d)
check("downloads every .sqlite shard (and not the manifest)", sorted(os.listdir(d)) == ["c1-599.sqlite", "c1900-2299.sqlite", "c600-1099.sqlite"],
      sorted(os.listdir(d)))
check("downloaded files have the right size", os.path.getsize(os.path.join(d, "c600-1099.sqlite")) == 200)
r2 = fetch(d)
check("re-running skips what is already there", "have c1900-2299.sqlite" in r2.stdout and "get " not in r2.stdout.replace("get  ", "GET"), r2.stdout.strip().splitlines()[:1])

d = tempfile.mkdtemp()
r = fetch(d, "--max-gb", "0.0000004")  # 400 bytes: newest first = 300, then 200 skipped, then 100 fits
check("budget: newest contests first, skips what would exceed it", sorted(os.listdir(d)) == ["c1-599.sqlite", "c1900-2299.sqlite"]
      and "would exceed" in r.stdout, sorted(os.listdir(d)))
d = tempfile.mkdtemp()
fetch(d, "--max-gb", "0.0000004", "--order", "oldest")
check("budget: --order oldest flips the preference", sorted(os.listdir(d)) == ["c1-599.sqlite", "c600-1099.sqlite"], sorted(os.listdir(d)))
d = tempfile.mkdtemp()
fetch(d, "--only", "c19*")
check("--only pattern", os.listdir(d) == ["c1900-2299.sqlite"], os.listdir(d))

MISSING_TAG["flag"] = True
d = tempfile.mkdtemp()
r = fetch(d)
check("missing release gives a helpful message and a non-zero exit", r.returncode != 0 and "workflow" in (r.stdout + r.stderr), (r.stdout + r.stderr).strip()[:90])
MISSING_TAG["flag"] = False
srv.shutdown()

# ---------------------------------------------------------------- password gate
import app as appmod
c = appmod.app.test_client()
os.environ.pop("APP_PASSWORD", None)
check("no APP_PASSWORD: open access", c.get("/api/problems").status_code == 200)
os.environ["APP_PASSWORD"] = "s3cret"
check("APP_PASSWORD set: no credentials -> 401 with login prompt",
      c.get("/").status_code == 401 and "Basic" in c.get("/").headers.get("WWW-Authenticate", ""))
check("APP_PASSWORD set: submit endpoint is protected too",
      c.post("/api/submit", json={"problem_id": "x", "text": "y" * 20}).status_code == 401)


def basic(pw, user="me"):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


check("wrong password -> 401", c.get("/api/problems", headers=basic("nope")).status_code == 401)
check("right password (any username) -> 200", c.get("/api/problems", headers=basic("s3cret", "anyone")).status_code == 200)
check("static page also behind the gate", c.get("/", headers=basic("s3cret")).status_code == 200)
os.environ.pop("APP_PASSWORD")

# ---------------------------------------------------------------- Codespaces config
dc = json.load(open(os.path.join(ROOT, ".devcontainer", "devcontainer.json")))
check("devcontainer: forwards port 5000 privately", 5000 in dc["forwardPorts"] and dc["portsAttributes"]["5000"]["visibility"] == "private")
check("devcontainer: binds all interfaces inside the container", dc["remoteEnv"]["HOST"] == "0.0.0.0")
check("devcontainer: asks for the API key secret", "ANTHROPIC_API_KEY" in dc["secrets"])
check("devcontainer: installs deps, g++, fetches data, starts the app",
      all(s in dc["postCreateCommand"] for s in ("g++", "requirements.txt", "fetch_data.py")) and "app.py" in dc["postStartCommand"])
src = open(os.path.join(ROOT, "app.py")).read()
check("app reads HOST and PORT from the environment", 'os.environ.get("HOST"' in src and 'os.environ.get("PORT"' in src)

print(f"\n{check.failed} failure(s)")
sys.exit(1 if check.failed else 0)
