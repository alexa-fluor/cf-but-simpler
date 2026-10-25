"""End-to-end checks using a fake dataset that has the real open-r1/codeforces schema.
Run: python tests/run_checks.py   (needs g++, pyarrow, flask, anthropic, pyyaml)
Model calls are mocked; compiling and running programs is real."""
import inspect
import json
import os
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
TMP = tempfile.mkdtemp(prefix="cfjudge_tests_")
FAKE = os.path.join(TMP, "fake")


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""), flush=True)
    if not cond:
        check.failed += 1


check.failed = 0


# ------------------------------------------------------------------ fake dataset
def make_fake_dataset(root):
    os.makedirs(os.path.join(root, "generated_tests"), exist_ok=True)
    rnd = random.Random(7)

    def kadane(a):
        best = cur = a[0]
        for x in a[1:]:
            cur = max(x, cur + x)
            best = max(best, cur)
        return best

    def sub_in(a):
        return f"{len(a)}\n{' '.join(map(str, a))}\n"

    official_a = [{"input": sub_in(a), "output": f"{kadane(a)}\n"} for a in ([-5], [1, 2, 3], [5, -9, 6, -2])]
    gen_a = []
    for _ in range(15):
        a = [rnd.randint(-10, 10) for _ in range(rnd.randint(1, 8))]
        gen_a.append((sub_in(a), f"{kadane(a)}\n"))
    a = [rnd.randint(-1000, 1000) for _ in range(1000)]
    gen_a.append((sub_in(a), f"{kadane(a)}\n"))
    a = [rnd.randint(-10**9, 10**9) for _ in range(200000)]
    gen_a.append((sub_in(a), f"{kadane(a)}\n"))            # large, within the cap
    a = [rnd.randint(-10**9, 10**9) for _ in range(330000)]
    gen_a.append((sub_in(a), f"{kadane(a)}\n"))            # > 3 MB: must be dropped and flagged
    gen_a.append((official_a[1]["input"], official_a[1]["output"]))  # duplicate of an official test

    def row(**kw):
        base = dict(id="", aliases=[], index="A", contest_id="", contest_name="", contest_type="CF",
                    contest_start=1_600_000_000, contest_start_year=2021, time_limit=2.0, memory_limit=256.0,
                    title="", description="", input_format="", output_format="", interaction_format="",
                    note="", examples=[], editorial="", rating="1500", tags=["dp"], testset_size=30,
                    official_tests=[], official_tests_complete=False, input_mode="stdio",
                    generated_checker="", executable=True, generated_tests=0)
        base.update(kw)
        return base

    plus1 = [{"input": f"{i}\n", "output": f"{i + 1}\n"} for i in (1, 5, 9)]
    chk = ("import sys\nn=int(open(sys.argv[1]).read().split()[0])\n"
           "try:\n    x,y=map(int,open(sys.argv[3]).read().split())\n    print(1 if x>=0 and y>=0 and x+y==n else 0)\n"
           "except Exception:\n    print(0)\n")
    rows = [
        row(id="900/A", index="A", contest_id="900", contest_name="Fake Round 900", title="Best stretch",
            description="Given $$$n$$$ integers, find the max sum of a non-empty block of consecutive elements.",
            input_format="First line $$$n$$$, then $$$n$$$ integers.", output_format="One integer.",
            examples=[{"input": "3\n1 2 3\n", "output": "6\n"}], note="Take everything.",
            editorial="Kadane: best ending here is max(x, prev + x).", rating="1500", tags=["dp", "greedy"],
            official_tests=official_a),
        row(id="900/B", index="B", contest_id="900", contest_name="Fake Round 900", title="Plus one",
            aliases=["901/A"], description="Print n+1.", rating="800", tags=["math"],
            official_tests_complete=True, official_tests=plus1),
        row(id="901/A", index="A", contest_id="901", contest_name="Fake Round 901 (Div. 2)",
            title="Plus one (copy)", aliases=["900/B"], description="Print n+1.", rating="800",
            official_tests_complete=True, official_tests=plus1),
        row(id="902/A", index="A", contest_id="902", contest_name="Fake Round 902", title="Split it",
            description="Print any two non-negative integers x y with x+y=n.", rating="1000",
            tags=["constructive"], official_tests_complete=True, generated_checker=chk,
            official_tests=[{"input": f"{i}\n", "output": f"0 {i}\n"} for i in (5, 0, 10)]),
        row(id="903/A", index="A", contest_id="903", title="Interactive one", interaction_format="ask ?",
            rating="1200", official_tests=official_a),
        row(id="904/A", index="A", contest_id="904", contest_name="Fake Round 904", title="File io",
            description="Max block sum, but read input.txt and write output.txt.", input_mode="file",
            rating="1200", official_tests=official_a),
        row(id="905/A", index="A", contest_id="905", contest_name="Fake Round 905", title="Unrated",
            description="Print n+1.", rating="", official_tests_complete=True, official_tests=plus1),
        row(id="906/A", index="A", contest_id="906", contest_name="Fake Round 906", title="Hard one",
            description="Print n+1.", rating="3000", official_tests_complete=True, official_tests=plus1),
        row(id="907/A", index="A", contest_id="907", contest_name="Fake Round 907", title="Two tests only",
            description="Print n+1.", rating="1100", official_tests_complete=True,
            official_tests=[{"input": "1\n", "output": "2\n"}, {"input": "2\n", "output": "3\n"}]),
        row(id="908/A", index="A", contest_id="908", contest_name="Fake Round 908", title="Unverified",
            description="Print n+1.", rating="1300", executable=False, official_tests=plus1),
    ]
    ex_t = pa.struct([("input", pa.string()), ("output", pa.string())])
    schema = pa.schema([
        ("id", pa.string()), ("aliases", pa.list_(pa.string())), ("index", pa.string()),
        ("contest_id", pa.string()), ("contest_name", pa.string()), ("contest_type", pa.string()),
        ("contest_start", pa.int64()), ("contest_start_year", pa.int64()), ("time_limit", pa.float64()),
        ("memory_limit", pa.float64()), ("title", pa.string()), ("description", pa.string()),
        ("input_format", pa.string()), ("output_format", pa.string()), ("interaction_format", pa.string()),
        ("note", pa.string()), ("examples", pa.list_(ex_t)), ("editorial", pa.string()),
        ("rating", pa.string()), ("tags", pa.list_(pa.string())), ("testset_size", pa.int64()),
        ("official_tests", pa.list_(ex_t)), ("official_tests_complete", pa.bool_()),
        ("input_mode", pa.string()), ("generated_checker", pa.string()), ("executable", pa.bool_()),
        ("generated_tests", pa.int64())])
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), os.path.join(root, "main.parquet"))

    def write_gen(cid, items):
        t = pa.table({"problem_id": [i[0] for i in items], "input": [i[1] for i in items],
                      "output": [i[2] for i in items], "test_case_i": list(range(len(items)))})
        pq.write_table(t, os.path.join(root, "generated_tests", f"test_cases_{cid}.parquet"), row_group_size=8)

    write_gen(900, [("900/A", i, o) for i, o in gen_a] + [("900/ZZZ", "decoy\n", "decoy\n")])
    write_gen(902, [("902/A", "7\n", "0 7\n"), ("902/A", "3\n", "0 3\n")])


def build(tag, *flags):
    out = os.path.join(TMP, "out_" + tag)
    shutil.rmtree(out, ignore_errors=True)
    r = subprocess.run([sys.executable, "importer/build_dataset.py", "--source", "local", "--local-dir", FAKE,
                        "--tag", tag, "--out", out, *flags], capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout, r.stderr)
    return out, json.load(open(os.path.join(out, f"{tag}.manifest.json")))


def ids_of(path):
    return [x[0] for x in sqlite3.connect(path).execute("select id from problems order by id")]


make_fake_dataset(FAKE)

# ------------------------------------------------------------------ importer: selection logic
import importlib.util
spec = importlib.util.spec_from_file_location("bd", os.path.join(ROOT, "importer", "build_dataset.py"))
bd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bd)
pool = [{"input": "x" * n, "output": "1"} for n in range(1, 101)]
sel, dropped = bd.select_tests(pool, max_tests=10, max_bytes=10**9, max_test_bytes=10**9)
sizes = [len(t["input"]) for t in sel]
check("select_tests: smallest 6 + largest 4", sizes == [1, 2, 3, 4, 5, 6, 97, 98, 99, 100], sizes)
sel, dropped = bd.select_tests(pool, 10, 10**9, 50)
check("select_tests: oversize tests dropped and counted", dropped > 0 and max(len(t["input"]) for t in sel) < 50, dropped)
sel, _ = bd.select_tests(pool, 10, 300, 10**9)
check("select_tests: byte budget respected", sum(len(t["input"]) for t in sel) <= 303)

# ------------------------------------------------------------------ importer: coverage
out, man = build("all")
db = os.path.join(out, "all.sqlite")
st_ = man["stats"]
check("default run loads every judgeable problem (unrated, file-io, 2-test ones included)",
      ids_of(db) == ["900/A", "900/B", "902/A", "904/A", "905/A", "906/A", "907/A"], ids_of(db))
check("exclusions counted with reasons", st_["interactive"] == 1 and st_["not_executable"] == 1
      and st_["duplicate_copy"] == 1 and st_["kept"] == 7 and st_["in_range"] == 10, st_)
conn = sqlite3.connect(db)
r = conn.execute("select statement, capped, n_tests, link, input_mode, verified from problems where id='900/A'").fetchone()
check("statement assembled and $$$ removed", "$$$" not in r[0] and "Input\n" in r[0] and "Example 1" in r[0])
check("900/A flagged capped (a >3MB test dropped)", r[1] == 1)
check("900/A tests deduped, decoy problem ignored, <=30", 3 <= r[2] <= 30, r[2])
check("link built", r[3] == "https://codeforces.com/contest/900/problem/A", r[3])
check("file-io mode stored", conn.execute("select input_mode from problems where id='904/A'").fetchone()[0] == "file")
check("unrated problem stored with NULL rating", conn.execute("select rating from problems where id='905/A'").fetchone()[0] is None)
check("checker stored", conn.execute("select checker from problems where id='902/A'").fetchone()[0].startswith("import sys"))
check("large test (200k numbers) kept", conn.execute("select max(length(input)) from tests where problem_id='900/A'").fetchone()[0] > 100_000)

out2, man2 = build("allow", "--allow-nonexecutable")
check("--allow-nonexecutable also loads unverified problem, flagged verified=0",
      "908/A" in ids_of(os.path.join(out2, "allow.sqlite")) and
      sqlite3.connect(os.path.join(out2, "allow.sqlite")).execute("select verified from problems where id='908/A'").fetchone()[0] == 0)
out3, man3 = build("rated", "--rated-only")
check("--rated-only drops unrated and counts it", "905/A" not in ids_of(os.path.join(out3, "rated.sqlite")) and man3["stats"]["unrated"] == 1)
out4, man4 = build("band", "--min-rating", "1000", "--max-rating", "2000")
check("rating band keeps unrated + in-band", ids_of(os.path.join(out4, "band.sqlite")) == ["900/A", "902/A", "904/A", "905/A", "907/A"], ids_of(os.path.join(out4, "band.sqlite")))
out5, man5 = build("mintests", "--min-tests", "3")
check("--min-tests 3 skips the 2-test problem and counts it", "907/A" not in ids_of(os.path.join(out5, "mintests.sqlite")) and man5["stats"]["no_usable_tests"] == 1)
out6, man6 = build("roll", "--shard-budget-mb", "0")
check("shard rollover: one file per problem at budget 0", len(man6["parts"]) == 7, [p["file"] for p in man6["parts"]])

# ------------------------------------------------------------------ real-library signatures (HF glue)
try:
    import datasets, huggingface_hub
    ld = inspect.signature(datasets.load_dataset).parameters
    check("datasets.load_dataset accepts name/split/streaming/token", all(k in ld for k in ("name", "split", "streaming", "token")))
    dl = inspect.signature(huggingface_hub.hf_hub_download).parameters
    check("hf_hub_download accepts repo_type/local_dir/token", all(k in dl for k in ("repo_type", "local_dir", "token")))
    lf = inspect.signature(huggingface_hub.HfApi.list_repo_files).parameters
    check("HfApi.list_repo_files accepts repo_type/token", all(k in lf for k in ("repo_type", "token")))
    import re
    check("generated-test filename regex matches HF layout", bd.GEN_RE.search("generated_tests/test_cases_1920.parquet").group(1) == "1920")
except ImportError:
    print("SKIP datasets/huggingface_hub not installed: signature checks skipped")

# ------------------------------------------------------------------ store
base = os.path.join(TMP, "appbase")
os.makedirs(os.path.join(base, "data"))
shutil.copytree(os.path.join(ROOT, "problems"), os.path.join(base, "problems"))
shutil.copy(db, os.path.join(base, "data", "all.sqlite"))
from store import Store
st = Store(base)
items, total = st.list()
check("store: starters + 7 CF problems", total == 3 + 7, total)
check("store: starters first, then by rating, unrated last", items[-1]["id"] == "905/A" and items[0]["source"] == "starter"
      and [i["rating"] for i in items[3:-1]] == sorted(i["rating"] for i in items[3:-1]), [i["id"] for i in items])
items, total = st.list(1000, 1600)
check("store: rating filter hides unrated and starters", [i["id"] for i in items] == ["902/A", "907/A", "904/A", "900/A"], [i["id"] for i in items])
check("store: search", st.list(q="plus")[0][0]["id"] == "900/B")
p = st.get("900/A")
check("store: get + decompress tests", p["rating"] == 1500 and p["tags"] == ["dp", "greedy"] and len(st.tests("900/A")) == p["n_tests"])
check("store: unrated get works", st.get("905/A")["rating"] is None)
check("store: random in range", st.random_id(2900, 3100) == "906/A")

# ------------------------------------------------------------------ judge (mock model, real compile/run)
import judge, sandbox
judge.USER_DIR = os.path.join(TMP, "user")
judge.CACHE_PATH = os.path.join(TMP, "user", "cache.json")
judge.RUBRIC_PATH = os.path.join(TMP, "user", "rubrics.json")
OK_GATE = {"verdict": "correct", "plausible": True, "feedback": "ok", "missing": []}
CPP_OK = '#include <bits/stdc++.h>\nusing namespace std;\nint main(){int n;scanf("%d",&n);long long best=LLONG_MIN,cur=0;for(int i=0;i<n;i++){long long x;scanf("%lld",&x);cur=max(x,cur+x);best=max(best,cur);}printf("%lld\\n",best);}\n'
CPP_ZERO = CPP_OK.replace("best=LLONG_MIN", "best=0").replace("cur=max(x,cur+x)", "cur=max(0LL,cur+x)")
CPP_SLOW = '#include <bits/stdc++.h>\nusing namespace std;\nint main(){int n;scanf("%d",&n);vector<long long>a(n);for(auto&x:a)scanf("%lld",&x);long long best=LLONG_MIN;for(int i=0;i<n;i++){long long s=0;for(int j=i;j<n;j++){s+=a[j];best=max(best,s);}}printf("%lld\\n",best);}\n'
CPP_BROKEN = "int main( { oops"
CPP_SPLIT = '#include <bits/stdc++.h>\nint main(){long long n;scanf("%lld",&n);printf("%lld 0\\n",n);}\n'
CPP_FILE = ('#include <bits/stdc++.h>\nint main(){FILE*f=fopen("input.txt","r");int n;fscanf(f,"%d",&n);long long best=LLONG_MIN,cur=0;'
            'for(int i=0;i<n;i++){long long x;fscanf(f,"%lld",&x);cur=std::max(x,cur+x);best=std::max(best,cur);}fclose(f);'
            'FILE*o=fopen("output.txt","w");fprintf(o,"%lld\\n",best);fclose(o);}\n')
calls = []


def fake_for(translations, gate=OK_GATE):
    it = iter(translations)

    def f(model, system, user, max_tokens=1500):
        calls.append(system[:12])
        if system.startswith("You write a "):
            return json.dumps({"key_insight": "generated", "accepted_approaches": [], "target_complexity": "O(n)",
                               "edge_cases": [], "common_wrong": []})
        if system.startswith("You judge"):
            return json.dumps(gate)
        calls.append("T:" + user[:400])
        t = next(it)
        return t if t.startswith("UNIMPL") else f"```cpp\n{t}```"
    return f


pA = st.get("900/A")
getA = lambda: st.tests("900/A")
check("g++ is available", sandbox.DEFAULT_LANG == "cpp", sandbox.DEFAULT_LANG)
judge.ask = fake_for([CPP_OK])
r = judge.judge(pA, "Kadane: running best ending here ...", False, getA)
check("C++ correct approach -> correct", r["verdict"] == "correct", r.get("feedback"))
check("rubric generated lazily once and cached", calls.count("You write a ") == 1 and os.path.exists(judge.RUBRIC_PATH))
judge.ask = fake_for([CPP_ZERO, CPP_ZERO])
r = judge.judge(pA, "Same but start best at zero and reset when negative", False, getA)
check("wrong approach -> wrong, smallest counterexample first",
      r["verdict"] == "wrong" and r["failure"]["kind"] == "wrong output" and r["failure"]["input"].startswith("1\n-5"))
check("rubric not regenerated on later use", calls.count("You write a ") == 1)
check("capped problem mentions left-out tests on a non-timeout failure", "left out" in r["feedback"])
t0 = time.time()
judge.ask = fake_for([CPP_SLOW, CPP_SLOW])
r = judge.judge(pA, "Try every block with a double loop", False, getA)
check("O(n^2) approach -> too slow on the 200k test", r["verdict"] == "wrong" and r["failure"]["kind"] == "too slow",
      f'{r.get("failure", {}).get("kind")} in {time.time() - t0:.1f}s')
judge.ask = fake_for([CPP_BROKEN, CPP_OK])
r = judge.judge(pA, "Kadane variant, compile slip then fine", False, getA)
check("compile error then good retry -> correct", r["verdict"] == "correct" and r.get("needed_retry"))
judge.ask = fake_for([CPP_BROKEN, CPP_BROKEN])
r = judge.judge(pA, "Something the translator cannot compile twice", False, getA)
check("two compile errors -> incomplete, not 'wrong'", r["verdict"] == "partial" and not r["executed"])

pS = st.get("902/A")
judge.ask = fake_for([CPP_SPLIT])
r = judge.judge(pS, "Output n and 0, which sums to n", False, lambda: st.tests("902/A"))
check("checker problem: different valid answer accepted", r["verdict"] == "correct", r.get("feedback"))
exact = sandbox.run_tests(CPP_SPLIT, st.tests("902/A"), lang="cpp", time_limit=2.0)
check("same answer rejected without the checker (the checker did the accepting)", not exact["passed"])

pF = st.get("904/A")
getF = lambda: st.tests("904/A")
judge.ask = fake_for([CPP_FILE])
r = judge.judge(pF, "Kadane, reading from the file as the statement says", False, getF)
check("file-io problem: program using input.txt/output.txt -> correct", r["verdict"] == "correct", r.get("feedback"))
check("file-io problem: translator is told to use files", any(c.startswith("T:") and "input.txt" in c for c in calls))
judge.ask = fake_for([CPP_OK, CPP_OK])
r = judge.judge(pF, "Kadane but reading stdin by mistake", False, getF)
check("file-io problem: stdin/stdout program fails with a clear reason",
      r["verdict"] == "wrong" and "output.txt" in r["failure"]["detail"], r.get("failure"))

sandbox.DEFAULT_LANG = "python"
judge.ask = lambda m, s, u, max_tokens=1500: (json.dumps(OK_GATE) if s.startswith("You judge") else
    "```python\nimport sys\nd=sys.stdin.read().split()\nn=int(d[0]);print(n+1)\n```")
p2 = st.get("900/B")
r = judge.judge(p2, "Just add one to the number given", False, lambda: st.tests("900/B"))
check("python fallback path works", r["verdict"] == "correct", r.get("feedback"))
sandbox.DEFAULT_LANG = "cpp"

# ------------------------------------------------------------------ HTTP
import app as appmod
appmod.store = Store(base)
c = appmod.app.test_client()
d = c.get("/api/problems?min=1000&max=1600").get_json()
check("api: list with filter", [i["id"] for i in d["items"]] == ["902/A", "907/A", "904/A", "900/A"] and d["shards"] == 1)
d = c.get("/api/problems").get_json()
check("api: unfiltered list includes everything", d["total"] == 10)
rp = c.get("/api/problem/900/A")
body = rp.get_json()
check("api: id containing a slash resolves", rp.status_code == 200 and body["rating"] == 1500)
check("api: hidden fields never leave the server", not any(k in body for k in ("editorial", "checker", "rubric", "tests")))
check("api: input_mode / verified exposed", c.get("/api/problem/904/A").get_json()["input_mode"] == "file"
      and c.get("/api/problem/900/A").get_json()["verified"] is True)
check("api: random", c.get("/api/random?min=2900&max=3100").get_json()["id"] == "906/A")
check("api: unknown problem 404", c.get("/api/problem/1/Z").status_code == 404)
judge.ask = fake_for([CPP_OK])
rs = c.post("/api/submit", json={"problem_id": "900/A", "text": "Kadane: running best ending here, reset ... O(n)"})
check("api: submit end-to-end", rs.status_code == 200 and rs.get_json()["verdict"] == "correct")

# ------------------------------------------------------------------ workflows
import yaml
for name, jobs in (("build-dataset", {"prepare", "build"}), ("ci", {"tests", "hf-smoke"})):
    wf = yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", name + ".yml")))
    check(f"workflow {name}: parses with expected jobs", set(wf["jobs"]) == jobs)
wf = yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", "build-dataset.yml")))
trig = wf.get("on") or wf.get(True)
check("build workflow: inputs for subset/rated_only/ratings/tests",
      {"subset", "rated_only", "min_rating", "max_rating", "max_tests", "release_tag"} <= set(trig["workflow_dispatch"]["inputs"]))
check("build workflow: 8 contest ranges and release-write permission",
      len(wf["jobs"]["build"]["strategy"]["matrix"]["range"]) == 8 and wf["permissions"]["contents"] == "write")

import py_compile
for f in ("importer/build_dataset.py", "judge.py", "sandbox.py", "store.py", "app.py", "tests/verify_shard.py"):
    py_compile.compile(f, doraise=True)
check("all python files compile", True)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{check.failed} failure(s)")
sys.exit(1 if check.failed else 0)
