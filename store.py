"""Problem storage: hand-written starter problems (problems/*.json) plus Codeforces shards
(data/*.sqlite) built by importer/build_dataset.py."""
import glob
import json
import os
import random
import sqlite3
import zlib

COLS = ("id, contest_id, idx, contest_name, title, rating, tags, statement, editorial, "
        "time_limit, memory_limit, checker, link, year, n_tests, capped, input_mode, verified")


class Store:
    def __init__(self, base):
        self.base = base
        self.refresh()

    def refresh(self):
        self.starters = {}
        for path in sorted(glob.glob(os.path.join(self.base, "problems", "*.json"))):
            with open(path) as f:
                p = json.load(f)
            p.setdefault("rating", None)
            p.setdefault("tags", [])
            p.setdefault("time_limit", 3.0)
            p.setdefault("memory_limit", 256.0)
            p.setdefault("checker", "")
            p.setdefault("editorial", "")
            p.setdefault("input_mode", "stdio")
            p.setdefault("verified", True)
            p.setdefault("contest_name", "Starter problems")
            p["source"] = "starter"
            self.starters[p["id"]] = p
        self.shards = sorted(glob.glob(os.path.join(self.base, "data", "*.sqlite")))

    def _connect(self, path):
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)

    def shard_count(self):
        return len(self.shards)

    def list(self, min_rating=None, max_rating=None, q="", limit=300):
        q = (q or "").strip().lower()
        rated_filter = min_rating is not None or max_rating is not None
        out = []
        if not rated_filter:
            for p in self.starters.values():
                if q and q not in p["title"].lower():
                    continue
                out.append({"id": p["id"], "title": p["title"], "rating": None,
                            "label": "starter", "source": "starter"})
        for path in self.shards:
            db = self._connect(path)
            # A rating filter automatically excludes unrated problems (NULL never compares true).
            sql = "SELECT id, contest_id, idx, title, rating FROM problems WHERE 1=1"
            args = []
            if min_rating is not None:
                sql += " AND rating >= ?"; args.append(min_rating)
            if max_rating is not None:
                sql += " AND rating <= ?"; args.append(max_rating)
            if q:
                sql += " AND (lower(title) LIKE ? OR id LIKE ? OR lower(contest_name) LIKE ?)"
                args += [f"%{q}%", f"%{q}%", f"%{q}%"]
            for pid, cid, idx, title, rating in db.execute(sql, args):
                out.append({"id": pid, "title": title, "rating": rating,
                            "label": f"{cid}{idx}", "source": "cf"})
            db.close()
        # starters first, then rated problems by rating, unrated Codeforces problems last
        out.sort(key=lambda r: (0 if r["source"] == "starter" else (1 if r["rating"] is not None else 2),
                                r["rating"] or 0, r["label"]))
        return out[:limit], len(out)

    def get(self, pid):
        if pid in self.starters:
            return dict(self.starters[pid])
        for path in self.shards:
            db = self._connect(path)
            row = db.execute(f"SELECT {COLS} FROM problems WHERE id = ?", (pid,)).fetchone()
            db.close()
            if row:
                (id_, cid, idx, cname, title, rating, tags, statement, editorial, tl, ml,
                 checker, link, year, n_tests, capped, input_mode, verified) = row
                return {"id": id_, "contest_id": cid, "idx": idx, "contest_name": cname,
                        "title": title, "rating": rating, "tags": json.loads(tags or "[]"),
                        "statement": statement, "editorial": editorial, "time_limit": tl,
                        "memory_limit": ml, "checker": checker, "link": link, "year": year,
                        "n_tests": n_tests, "capped": bool(capped), "source": "cf",
                        "input_mode": input_mode or "stdio", "verified": bool(verified)}
        return None

    def tests(self, pid):
        if pid in self.starters:
            return self.starters[pid]["tests"]
        for path in self.shards:
            db = self._connect(path)
            rows = db.execute("SELECT input, output FROM tests WHERE problem_id = ? ORDER BY i",
                              (pid,)).fetchall()
            db.close()
            if rows:
                return [{"input": zlib.decompress(a).decode(), "output": zlib.decompress(b).decode()}
                        for a, b in rows]
        return []

    def random_id(self, min_rating=None, max_rating=None):
        items, _ = self.list(min_rating, max_rating, limit=10**9)
        return random.choice(items)["id"] if items else None
