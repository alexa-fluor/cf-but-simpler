#!/usr/bin/env python3
"""Build compact sqlite shards from the open-r1/codeforces dataset.

Designed to run on a GitHub Actions runner (14 GB disk): the main dataset is streamed,
and the huge per-contest generated-test files are downloaded ONE AT A TIME and deleted
right after use. Only a capped, selected subset of tests per problem is kept.

Goal: load EVERY problem that can be judged. A problem is only left out when it truly
cannot be judged (interactive: no grader exists), has no usable tests, is a duplicate copy
of a problem already kept, or you asked for a narrower selection (rated-only / rating range
/ contest range). Every exclusion is counted and reported.

Output: <out>/<tag>.sqlite (+ _p2, _p3 ... if the shard budget is exceeded) and
<out>/<tag>.manifest.json (includes the exclusion counts).

Sources:
  --source hf     real data from Hugging Face (needs `datasets` and `huggingface_hub`)
  --source local  a directory with main.parquet and generated_tests/test_cases_<id>.parquet
                  (same schema; used for testing without network)
"""
import argparse
import collections
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zlib

import pyarrow.parquet as pq

REPO = "open-r1/codeforces"
GEN_RE = re.compile(r"generated_tests/test_cases_(\d+)\.parquet$")

REASONS = {
    "interactive": "interactive problem (no grader available)",
    "not_executable": "dataset could not run 3 human solutions (only loaded with --allow-nonexecutable)",
    "unrated": "no rating (only skipped with --rated-only)",
    "rating_out_of_range": "rated outside the requested range",
    "duplicate_copy": "same problem as one already kept (Div.1/Div.2 copy)",
    "no_usable_tests": "fewer than --min-tests usable tests",
}


# ----------------------------------------------------------------------------- helpers
def parse_rating(v):
    try:
        return int(float(str(v).strip()))
    except Exception:
        return None


def clean_math(s):
    # Codeforces marks inline math with $$$...$$$; drop the markers for readability.
    return (s or "").replace("$$$", "").strip()


def build_statement(row):
    parts = [clean_math(row.get("description"))]
    if (row.get("input_format") or "").strip():
        parts.append("Input\n" + clean_math(row["input_format"]))
    if (row.get("output_format") or "").strip():
        parts.append("Output\n" + clean_math(row["output_format"]))
    for i, ex in enumerate(row.get("examples") or [], 1):
        parts.append(f"Example {i}\nInput:\n{(ex.get('input') or '').rstrip()}\nOutput:\n{(ex.get('output') or '').rstrip()}")
    if (row.get("note") or "").strip():
        parts.append("Note\n" + clean_math(row["note"]))
    return "\n\n".join(p for p in parts if p)


def select_tests(pool, max_tests, max_bytes, max_test_bytes):
    """Pick a representative subset. Returns (tests, dropped_for_size).

    Keeps ~60% of the budget as the smallest tests (edge cases), then fills the rest with
    the largest tests that fit the byte budget (these are what catch too-slow solutions).
    """
    seen, uniq, dropped = set(), [], 0
    for t in pool:
        inp, out = t["input"], t["output"]
        if inp in seen:
            continue
        seen.add(inp)
        if len(inp) + len(out) > max_test_bytes:
            dropped += 1
            continue
        uniq.append({"input": inp, "output": out})
    size = lambda t: len(t["input"]) + len(t["output"])
    uniq.sort(key=size)
    if len(uniq) <= max_tests and sum(map(size, uniq)) <= max_bytes:
        return uniq, dropped
    n_small = max(1, int(max_tests * 0.6))
    chosen = set(range(min(n_small, len(uniq))))
    budget = max_bytes - sum(size(uniq[i]) for i in chosen)
    for i in range(len(uniq) - 1, -1, -1):
        if len(chosen) >= max_tests:
            break
        if i in chosen:
            continue
        if size(uniq[i]) <= budget:
            chosen.add(i)
            budget -= size(uniq[i])
    return [uniq[i] for i in sorted(chosen)], dropped


# ----------------------------------------------------------------------------- sources
class LocalSource:
    def __init__(self, root):
        self.root = root

    def main_rows(self):
        yield from pq.read_table(os.path.join(self.root, "main.parquet")).to_pylist()

    def contests_with_generated_tests(self):
        d = os.path.join(self.root, "generated_tests")
        out = {}
        if os.path.isdir(d):
            for f in os.listdir(d):
                m = re.match(r"test_cases_(\d+)\.parquet$", f)
                if m:
                    out[int(m.group(1))] = os.path.join(d, f)
        return out

    def fetch(self, handle, workdir):  # local file, nothing to download
        return handle, False


class HFSource:
    def __init__(self, subset):
        self.subset = subset
        self.token = os.environ.get("HF_TOKEN") or None

    def main_rows(self):
        from datasets import load_dataset
        for split in ("train", "test"):
            ds = load_dataset(REPO, name=self.subset, split=split, streaming=True, token=self.token)
            yield from ds

    def contests_with_generated_tests(self):
        from huggingface_hub import HfApi
        out = {}
        for f in HfApi().list_repo_files(REPO, repo_type="dataset", token=self.token):
            m = GEN_RE.search(f)
            if m:
                out[int(m.group(1))] = f
        return out

    def fetch(self, handle, workdir):
        from huggingface_hub import hf_hub_download
        path = hf_hub_download(REPO, handle, repo_type="dataset", local_dir=workdir, token=self.token)
        return path, True


def read_generated(path, wanted_ids, max_test_bytes):
    """-> ({problem_id: [tests]}, {problem_id: n_oversize}). Oversized tests are dropped
    immediately to bound memory, but counted so we can flag the problem."""
    tests = {pid: [] for pid in wanted_ids}
    oversize = {pid: 0 for pid in wanted_ids}
    pf = pq.ParquetFile(path)
    for batch in pf.iter_batches(batch_size=32, columns=["problem_id", "input", "output"]):
        for r in batch.to_pylist():
            pid = r["problem_id"]
            if pid not in tests:
                continue
            if len(r["input"]) + len(r["output"]) > max_test_bytes:
                oversize[pid] += 1
                continue
            tests[pid].append({"input": r["input"], "output": r["output"]})
    return tests, oversize


# ----------------------------------------------------------------------------- output
SCHEMA = """
CREATE TABLE problems (
  id TEXT PRIMARY KEY, contest_id INTEGER, idx TEXT, contest_name TEXT, title TEXT,
  rating INTEGER, tags TEXT, statement TEXT, editorial TEXT, time_limit REAL,
  memory_limit REAL, checker TEXT, link TEXT, year INTEGER, n_tests INTEGER, capped INTEGER,
  input_mode TEXT, verified INTEGER);
CREATE TABLE tests (
  problem_id TEXT, i INTEGER, input BLOB, output BLOB, PRIMARY KEY (problem_id, i));
CREATE INDEX problems_rating ON problems(rating);
"""


class ShardWriter:
    def __init__(self, outdir, tag, budget_bytes):
        self.outdir, self.tag, self.budget = outdir, tag, budget_bytes
        self.parts = []
        self._open()

    def _path(self, n):
        return os.path.join(self.outdir, f"{self.tag}.sqlite" if n == 0 else f"{self.tag}_p{n + 1}.sqlite")

    def _open(self):
        n = len(self.parts)
        path = self._path(n)
        if os.path.exists(path):
            os.remove(path)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        self.cur = {"file": os.path.basename(path), "problems": 0, "bytes": 0, "ratings": [], "approx": 0}
        self.parts.append(self.cur)

    def _close(self):
        self.db.commit()
        self.db.execute("VACUUM")
        self.db.close()
        self.cur["bytes"] = os.path.getsize(os.path.join(self.outdir, self.cur["file"]))

    def add(self, prob, tests):
        blobs = [(zlib.compress(t["input"].encode(), 6), zlib.compress(t["output"].encode(), 6)) for t in tests]
        approx = sum(len(a) + len(b) for a, b in blobs) + len(prob["statement"]) + len(prob["editorial"])
        if self.cur["problems"] > 0 and self.cur["approx"] + approx > self.budget:
            self._close()
            self._open()
        self.cur["approx"] += approx
        self.db.execute(
            "INSERT INTO problems VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (prob["id"], prob["contest_id"], prob["idx"], prob["contest_name"], prob["title"],
             prob["rating"], json.dumps(prob["tags"]), prob["statement"], prob["editorial"],
             prob["time_limit"], prob["memory_limit"], prob["checker"], prob["link"],
             prob["year"], len(tests), int(prob["capped"]), prob["input_mode"], int(prob["verified"])),
        )
        self.db.executemany(
            "INSERT INTO tests VALUES (?,?,?,?)",
            [(prob["id"], i, a, b) for i, (a, b) in enumerate(blobs)],
        )
        self.cur["problems"] += 1
        if prob["rating"] is not None:
            self.cur["ratings"].append(prob["rating"])

    def finish(self, stats):
        self._close()
        for p in self.parts:
            r = p.pop("ratings")
            p.pop("approx", None)
            p["rating_min"], p["rating_max"] = (min(r), max(r)) if r else (None, None)
        with open(os.path.join(self.outdir, f"{self.tag}.manifest.json"), "w") as f:
            json.dump({"tag": self.tag, "parts": self.parts, "stats": stats}, f, indent=2)
        return self.parts


def write_step_summary(tag, stats, parts):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [f"### Shard `{tag}`", "", "| | count |", "|---|---|",
             f"| problems scanned in contest range | {stats['in_range']} |",
             f"| **problems loaded** | **{stats['kept']}** |"]
    for k, text in REASONS.items():
        if stats.get(k):
            lines.append(f"| excluded: {text} | {stats[k]} |")
    if stats.get("capped"):
        lines.append(f"| loaded, but with some oversize tests left out | {stats['capped']} |")
    lines += ["", "| file | problems | MB |", "|---|---|---|"]
    for p in parts:
        lines.append(f"| {p['file']} | {p['problems']} | {p['bytes'] / 1e6:.1f} |")
    with open(path, "a") as f:
        f.write("\n".join(lines) + "\n\n")


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["hf", "local"], default="hf")
    ap.add_argument("--local-dir")
    ap.add_argument("--subset", default="verifiable", help="'verifiable' (recommended) or 'default' (all)")
    ap.add_argument("--contest-min", type=int, default=1)
    ap.add_argument("--contest-max", type=int, default=10**9)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out", default="out")
    ap.add_argument("--min-rating", type=int, default=0)
    ap.add_argument("--max-rating", type=int, default=10**6)
    ap.add_argument("--rated-only", action="store_true", help="skip problems that have no rating")
    ap.add_argument("--allow-nonexecutable", action="store_true",
                    help="also load problems the dataset could not verify (needed for --subset default)")
    ap.add_argument("--max-tests", type=int, default=30)
    ap.add_argument("--min-tests", type=int, default=1)
    ap.add_argument("--max-bytes", type=int, default=4_000_000, help="raw bytes of tests kept per problem")
    ap.add_argument("--max-test-bytes", type=int, default=3_000_000, help="drop any single test larger than this")
    ap.add_argument("--shard-budget-mb", type=int, default=1700, help="roll over to a new file past this size")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    src = LocalSource(a.local_dir) if a.source == "local" else HFSource(a.subset)
    stats = collections.Counter()

    # 1) stream the main dataset, keep only eligible problems in range (slimmed down)
    rows, scanned = [], 0
    for r in src.main_rows():
        scanned += 1
        try:
            cid = int(r["contest_id"])
        except Exception:
            continue
        if not (a.contest_min <= cid <= a.contest_max):
            continue
        stats["in_range"] += 1
        rating = parse_rating(r.get("rating"))
        if (r.get("interaction_format") or "").strip():
            stats["interactive"] += 1
            continue
        executable = bool(r.get("executable", True))
        if not executable and not a.allow_nonexecutable:
            stats["not_executable"] += 1
            continue
        if rating is None and a.rated_only:
            stats["unrated"] += 1
            continue
        if rating is not None and not (a.min_rating <= rating <= a.max_rating):
            stats["rating_out_of_range"] += 1
            continue
        rows.append({
            "id": r["id"], "aliases": list(r.get("aliases") or []), "index": r.get("index") or "",
            "contest_id": cid, "contest_name": r.get("contest_name") or "",
            "title": r.get("title") or "", "rating": rating, "tags": list(r.get("tags") or []),
            "statement": build_statement(r), "editorial": (r.get("editorial") or "")[:15000],
            "time_limit": float(r.get("time_limit") or 2.0),
            "memory_limit": float(r.get("memory_limit") or 256.0),
            "checker": r.get("generated_checker") or "",
            "input_mode": r.get("input_mode") or "stdio",
            "verified": executable,
            "year": r.get("contest_start_year") or 0,
            "official": list(r.get("official_tests") or []),
        })
    print(f"scanned {scanned} problems in the dataset, {stats['in_range']} in the contest range, "
          f"{len(rows)} eligible", flush=True)

    # 2) dedupe Div1/Div2 copies via aliases
    rows.sort(key=lambda r: (r["contest_id"], r["index"]))
    seen, uniq = set(), []
    for r in rows:
        if r["id"] in seen:
            stats["duplicate_copy"] += 1
            continue
        seen.add(r["id"])
        seen.update(r["aliases"])
        uniq.append(r)

    # 3) per contest: fetch generated tests, select, write
    available = src.contests_with_generated_tests()
    writer = ShardWriter(a.out, a.tag, a.shard_budget_mb * 1024 * 1024)
    by_contest = {}
    for r in uniq:
        by_contest.setdefault(r["contest_id"], []).append(r)

    for cid, probs in sorted(by_contest.items()):
        gen, oversize = {}, {}
        if cid in available:
            work = tempfile.mkdtemp(prefix="gen_")
            try:
                path, downloaded = src.fetch(available[cid], work)
                gen, oversize = read_generated(path, [p["id"] for p in probs], a.max_test_bytes)
            except Exception as e:
                print(f"  contest {cid}: generated tests unavailable ({type(e).__name__}: {e})", flush=True)
            finally:
                shutil.rmtree(work, ignore_errors=True)  # free disk immediately
        for p in probs:
            pool = list(p["official"]) + gen.get(p["id"], [])
            tests, dropped = select_tests(pool, a.max_tests, a.max_bytes, a.max_test_bytes)
            dropped += oversize.get(p["id"], 0)
            if len(tests) < max(1, a.min_tests):
                stats["no_usable_tests"] += 1
                continue
            writer.add({
                "id": p["id"], "contest_id": cid, "idx": p["index"], "contest_name": p["contest_name"],
                "title": p["title"], "rating": p["rating"], "tags": p["tags"],
                "statement": p["statement"], "editorial": p["editorial"],
                "time_limit": p["time_limit"], "memory_limit": p["memory_limit"],
                "checker": p["checker"], "year": p["year"], "capped": dropped > 0,
                "input_mode": p["input_mode"], "verified": p["verified"],
                "link": f"https://codeforces.com/contest/{cid}/problem/{p['index']}",
            }, tests)
            stats["kept"] += 1
            if dropped > 0:
                stats["capped"] += 1
        for p in probs:
            p["official"] = None  # release memory before the next contest
    stats = dict(stats)
    stats.setdefault("kept", 0)
    stats.setdefault("in_range", 0)
    parts = writer.finish(stats)
    write_step_summary(a.tag, stats, parts)
    print(json.dumps({"stats": stats, "parts": parts}, indent=2))


if __name__ == "__main__":
    main()
