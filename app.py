import json
import os
import threading
import time

from flask import Flask, jsonify, request, send_from_directory

import judge
from store import Store

HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(HERE, "static"))
store = Store(HERE)


def _int(name):
    v = request.args.get(name, "").strip()
    return int(v) if v.isdigit() else None


# ----------------------------------------------------------------------------- submissions
# Every submission is kept in user/submissions.json so there is a "My submissions" page.
class Submissions:
    def __init__(self):
        self.lock = threading.Lock()
        self.items = None

    def path(self):
        return os.path.join(judge.USER_DIR, "submissions.json")

    def _load(self):
        if self.items is None:
            try:
                with open(self.path()) as f:
                    self.items = json.load(f)
            except Exception:
                self.items = []
            # anything still "running" was interrupted by a server restart
            for s in self.items:
                if s.get("status") == "running":
                    s.update(status="done", result={"error": "Judging was interrupted (server restarted)."})
        return self.items

    def _save(self):
        os.makedirs(judge.USER_DIR, exist_ok=True)
        tmp = self.path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.items, f)
        os.replace(tmp, self.path())

    def add(self, p, text, skip_gate):
        with self.lock:
            items = self._load()
            s = {"id": (items[-1]["id"] + 1) if items else 1, "when": time.time(),
                 "problem_id": p["id"], "title": p["title"], "label": _label(p), "rating": p.get("rating"),
                 "text": text, "skip_gate": skip_gate, "status": "running", "result": None}
            items.append(s)
            self._save()
            return dict(s)

    def finish(self, sid, result):
        with self.lock:
            for s in self._load():
                if s["id"] == sid:
                    s.update(status="done", result=result)
            self._save()

    def get(self, sid):
        with self.lock:
            return next((dict(s) for s in self._load() if s["id"] == sid), None)

    def list(self, problem_id=None):
        with self.lock:
            return [dict(s) for s in reversed(self._load())
                    if problem_id is None or s["problem_id"] == problem_id]


subs = Submissions()


def _label(p):
    if p.get("source") == "cf":
        return f"{p.get('contest_id', '')}{p.get('idx', '')}"
    return p.get("label", p["id"])


def _summary(s):
    """Status-table row: no source text, no generated code."""
    r = s.get("result") or {}
    keep = ("verdict", "error", "executed", "stages", "failure", "needed_retry", "cached", "models")
    out = {k: s[k] for k in ("id", "when", "problem_id", "title", "label", "rating", "status", "skip_gate")}
    out["result"] = {k: r[k] for k in keep if k in r} if s.get("result") else None
    if out["result"] and out["result"].get("failure"):
        out["result"]["failure"] = {k: v for k, v in r["failure"].items() if k in ("kind", "test")}
    return out


def _validate(body):
    p = store.get(body.get("problem_id") or "")
    text = (body.get("text") or "").strip()
    if not p:
        return None, text, (jsonify({"error": "unknown problem"}), 404)
    if len(text) < 10:
        return None, text, (jsonify({"error": "Write your approach in a few sentences first."}), 400)
    if len(text) > 4000:
        return None, text, (jsonify({"error": "Keep it under 4000 characters."}), 400)
    return p, text, None


def _run_judge(p, text, skip_gate):
    try:
        return judge.judge(p, text, skip_gate=skip_gate, get_tests=lambda: store.tests(p["id"]))
    except Exception as e:  # API key missing, network, bad JSON from model, ...
        return {"error": f"{type(e).__name__}: {e}"}


# ----------------------------------------------------------------------------- routes
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/problems")
def list_problems():
    store.refresh()
    limit = min(_int("limit") or 300, 1000)
    items, total = store.list(_int("min"), _int("max"), request.args.get("q", ""),
                              limit=limit, offset=_int("offset") or 0,
                              order=request.args.get("order", "rating"))
    return jsonify({"items": items, "total": total, "shards": store.shard_count()})


@app.get("/api/random")
def random_problem():
    pid = store.random_id(_int("min"), _int("max"))
    return jsonify({"id": pid})


@app.get("/api/problem/<path:pid>")
def get_problem(pid):
    p = store.get(pid)
    if not p:
        return jsonify({"error": "not found"}), 404
    # never send rubric / editorial / tests / checker to the browser
    keys = ("id", "title", "statement", "link", "rating", "tags", "contest_name", "contest_id", "idx",
            "time_limit", "memory_limit", "capped", "source", "year", "input_mode", "verified")
    out = {k: p.get(k) for k in keys}
    out["label"] = _label(p)
    return jsonify(out)


@app.post("/api/submit")
def submit():
    """Synchronous: judges and returns the result (also recorded in My submissions)."""
    body = request.get_json(force=True)
    p, text, err = _validate(body)
    if err:
        return err
    s = subs.add(p, text, bool(body.get("skip_gate")))
    res = _run_judge(p, text, bool(body.get("skip_gate")))
    subs.finish(s["id"], res)
    return jsonify({**res, "submission_id": s["id"]}), (500 if "error" in res else 200)


@app.post("/api/submissions")
def submit_async():
    """Records the submission and judges it in the background; poll /api/submissions."""
    body = request.get_json(force=True)
    p, text, err = _validate(body)
    if err:
        return err
    skip = bool(body.get("skip_gate"))
    s = subs.add(p, text, skip)
    threading.Thread(target=lambda: subs.finish(s["id"], _run_judge(p, text, skip)), daemon=True).start()
    return jsonify({"id": s["id"]})


@app.get("/api/submissions")
def list_submissions():
    return jsonify({"items": [_summary(s) for s in subs.list(request.args.get("problem") or None)]})


@app.get("/api/submission/<int:sid>")
def get_submission(sid):
    s = subs.get(sid)
    if not s:
        return jsonify({"error": "not found"}), 404
    return jsonify(s)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
