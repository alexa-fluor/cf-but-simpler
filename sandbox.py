"""Compiles/runs untrusted (AI-generated) programs with time and memory limits, and
checks their output (exact token match, or a Codeforces-style checker program)."""
import os
import shutil
import subprocess
import sys
import tempfile

PY_FACTOR = 4  # CPython is far slower than the C++ the CF limits were set for
SLACK = 0.5    # seconds of grace for process start-up on a loaded machine


def have_cpp():
    return shutil.which("g++") is not None


DEFAULT_LANG = os.environ.get("JUDGE_LANG") or ("cpp" if have_cpp() else "python")


def _limiter(mem_mb, cpu_s):
    def apply():
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (int(mem_mb * 1024 * 1024),) * 2)
            resource.setrlimit(resource.RLIMIT_CPU, (int(cpu_s) + 2,) * 2)
        except Exception:
            pass  # e.g. macOS refuses RLIMIT_AS; the wall-clock timeout still applies
    return apply if os.name == "posix" else None


class Program:
    """Compile once, run many times."""

    def __init__(self, code, lang):
        self.lang = lang
        self.dir = tempfile.mkdtemp(prefix="sol_")
        self.error = None
        if lang == "cpp":
            src, exe = os.path.join(self.dir, "sol.cpp"), os.path.join(self.dir, "sol")
            with open(src, "w") as f:
                f.write(code)
            try:
                p = subprocess.run(["g++", "-O2", "-std=c++17", "-o", exe, src],
                                   capture_output=True, text=True, timeout=120)
                if p.returncode != 0:
                    self.error = p.stderr[-800:]
            except subprocess.TimeoutExpired:
                self.error = "compilation timed out"
            self.cmd = [exe]
        else:
            src = os.path.join(self.dir, "sol.py")
            with open(src, "w") as f:
                f.write(code)
            self.cmd = [sys.executable, src]

    def run(self, stdin, time_limit, mem_mb, input_mode="stdio"):
        """input_mode 'file': the program reads input.txt and writes output.txt instead of
        using stdin/stdout (a few old Codeforces problems work this way)."""
        factor = PY_FACTOR if self.lang == "python" else 1
        limit = time_limit * factor + SLACK
        mem = max(mem_mb * 2, 768) if self.lang == "python" else max(mem_mb, 64)
        out_path = os.path.join(self.dir, "output.txt")
        feed = stdin
        if input_mode == "file":
            with open(os.path.join(self.dir, "input.txt"), "w") as f:
                f.write(stdin)
            if os.path.exists(out_path):
                os.remove(out_path)
            feed = ""
        try:
            p = subprocess.run(self.cmd, input=feed, capture_output=True, text=True,
                               timeout=limit, cwd=self.dir, env={"PATH": os.environ.get("PATH", "")},
                               preexec_fn=_limiter(mem, limit))
        except subprocess.TimeoutExpired:
            return "timeout", "", limit
        if p.returncode != 0:
            return "error", p.stderr[-400:] or f"exit code {p.returncode}", limit
        if input_mode == "file":
            if not os.path.exists(out_path):
                return "error", "The program did not write output.txt (this problem uses files, not stdin/stdout).", limit
            with open(out_path) as f:
                return "ok", f.read(), limit
        return "ok", p.stdout, limit

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def _short(s, n=300):
    return s if len(s) <= n else s[:n] + f"... [{len(s)} chars total]"


class Checker:
    """Runs a Codeforces-style checker: python checker.py input correct_output solution_output
    prints a score; full marks (1 or 100) means correct."""

    def __init__(self, source):
        self.dir = tempfile.mkdtemp(prefix="chk_")
        with open(os.path.join(self.dir, "checker.py"), "w") as f:
            f.write(source)

    def ok(self, test, got):
        for name, text in (("input.txt", test["input"]), ("correct_output.txt", test["output"]),
                           ("solution_output.txt", got)):
            with open(os.path.join(self.dir, name), "w") as f:
                f.write(text)
        try:
            p = subprocess.run([sys.executable, "checker.py", "input.txt", "correct_output.txt",
                                "solution_output.txt"], capture_output=True, text=True, timeout=20,
                               cwd=self.dir, env={"PATH": os.environ.get("PATH", "")})
            tok = p.stdout.split()
            return bool(tok) and float(tok[0]) in (1.0, 100.0), ""
        except Exception as e:
            return False, f"checker problem: {type(e).__name__}"

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def run_tests(code, tests, lang=None, time_limit=3.0, mem_mb=256, checker_source="", input_mode="stdio"):
    """tests: [{"input","output"}]. Smallest inputs first, so the first failure is the
    smallest counterexample."""
    lang = lang or DEFAULT_LANG
    ordered = sorted(tests, key=lambda t: len(t["input"]))
    prog = Program(code, lang)
    chk = Checker(checker_source) if checker_source else None
    try:
        if prog.error:
            return {"passed": False, "passed_count": 0, "total": len(ordered),
                    "failure": {"kind": "compile error", "input": "", "expected": "", "detail": prog.error}}
        passed = 0
        for t in ordered:
            status, out, limit = prog.run(t["input"], time_limit, mem_mb, input_mode)
            if status == "ok":
                good, why = chk.ok(t, out) if chk else (out.split() == t["output"].split(), "")
            else:
                good, why = False, ""
            if good:
                passed += 1
                continue
            f = {"input": _short(t["input"]), "expected": _short(t["output"].strip()), "test": passed + 1}
            if status == "timeout":
                f.update(kind="too slow",
                         detail=f"Did not finish within {limit:.1f}s on an input of {len(t['input'])} characters.")
            elif status == "error":
                f.update(kind="crashed", detail=out)
            else:
                f.update(kind="wrong output", got=_short(out.strip()), detail=why)
            return {"passed": False, "passed_count": passed, "total": len(ordered), "failure": f}
        return {"passed": True, "passed_count": passed, "total": len(ordered), "failure": None}
    finally:
        prog.close()
        if chk:
            chk.close()
