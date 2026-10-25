"""Checks that a shard built from the real dataset is sane and loadable by the app.
Usage: python tests/verify_shard.py out/smoke.sqlite"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

path = sys.argv[1]
db = sqlite3.connect(path)
n = db.execute("select count(*) from problems").fetchone()[0]
manifest = path.replace(".sqlite", ".manifest.json")
if os.path.exists(manifest):
    print("exclusion stats:", json.load(open(manifest)).get("stats"))
assert n > 0, "no problems were imported (see the stats above for why)"
print(f"{n} problems imported")

bad = db.execute("select count(*) from problems p where not exists (select 1 from tests t where t.problem_id = p.id)").fetchone()[0]
assert bad == 0, f"{bad} problems have no tests"
empty = db.execute("select count(*) from problems where length(statement) < 20").fetchone()[0]
assert empty == 0, f"{empty} problems have an empty statement"

base = tempfile.mkdtemp()
os.makedirs(os.path.join(base, "data"))
shutil.copy(path, os.path.join(base, "data", "shard.sqlite"))
from store import Store
st = Store(base)
items, total = st.list()
assert total == n, (total, n)
for it in items[:5]:
    p = st.get(it["id"])
    tests = st.tests(it["id"])
    assert tests and all(t["input"] is not None and t["output"] is not None for t in tests)
    print(f"  {p['id']:10} rating={p['rating']} tests={len(tests)} tl={p['time_limit']}s "
          f"checker={'yes' if p['checker'] else 'no'} mode={p['input_mode']} title={p['title'][:40]!r}")
print("shard OK")
