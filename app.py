import hmac
import os

from flask import Flask, Response, jsonify, request, send_from_directory

import judge
from store import Store

HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(HERE, "static"))
store = Store(HERE)


@app.before_request
def password_gate():
    """If APP_PASSWORD is set, every request needs it (HTTP basic auth, any username).
    Protects your API credits and the code-running endpoint if the port is ever made public."""
    password = os.environ.get("APP_PASSWORD")
    if not password:
        return None
    auth = request.authorization
    if auth and hmac.compare_digest((auth.password or "").encode(), password.encode()):
        return None
    return Response("Password required.", 401, {"WWW-Authenticate": 'Basic realm="Logic Judge"'})


def _int(name):
    v = request.args.get(name, "").strip()
    return int(v) if v.isdigit() else None


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/problems")
def list_problems():
    store.refresh()
    items, total = store.list(_int("min"), _int("max"), request.args.get("q", ""))
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
    keys = ("id", "title", "statement", "link", "rating", "tags", "contest_name", "idx",
            "time_limit", "memory_limit", "capped", "source", "year", "input_mode", "verified")
    return jsonify({k: p.get(k) for k in keys})


@app.post("/api/submit")
def submit():
    body = request.get_json(force=True)
    p = store.get(body.get("problem_id") or "")
    text = (body.get("text") or "").strip()
    if not p:
        return jsonify({"error": "unknown problem"}), 404
    if len(text) < 10:
        return jsonify({"error": "Write your approach in a few sentences first."}), 400
    if len(text) > 4000:
        return jsonify({"error": "Keep it under 4000 characters."}), 400
    try:
        res = judge.judge(p, text, skip_gate=bool(body.get("skip_gate")),
                          get_tests=lambda: store.tests(p["id"]))
        return jsonify(res)
    except Exception as e:  # API key missing, network, bad JSON from model, ...
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")), debug=False)
