"""Builds problems/*.json. Each problem = statement + rubric + reference solution + tests.

Expected outputs are computed by running the reference solution, so a wrong reference
means a wrong judge. To add a problem: add an entry to PROBLEMS with a reference
solution and an inputs() function, then run `python make_problems.py`.
"""
import json
import os
import random

from sandbox import run_code

HERE = os.path.dirname(os.path.abspath(__file__))
rnd = random.Random(12345)


def fmt(*lines):
    return "\n".join(str(l) for l in lines) + "\n"


# ---------- Problem 1: maximum subarray sum ----------
def max_subarray_inputs():
    ins = [fmt(1, "-5"), fmt(3, "-3 -1 -2"), fmt(5, "1 2 3 4 5"), fmt(4, "5 -9 6 -2"),
           fmt(6, "2 -1 2 -1 2 -10")]
    for _ in range(25):
        n = rnd.randint(1, 8)
        ins.append(fmt(n, " ".join(str(rnd.randint(-10, 10)) for _ in range(n))))
    n = 1000
    ins.append(fmt(n, " ".join(str(rnd.randint(-1000, 1000)) for _ in range(n))))
    n = 200000
    ins.append(fmt(n, " ".join(str(rnd.randint(-10**9, 10**9)) for _ in range(n))))
    return ins


# ---------- Problem 2: jump game ----------
def jump_inputs():
    ins = [fmt(1, "0"), fmt(2, "0 5"), fmt(5, "2 3 1 1 4"), fmt(5, "3 2 1 0 4"),
           fmt(4, "1 1 1 0"), fmt(6, "1 0 1 1 1 1")]
    for _ in range(30):
        n = rnd.randint(1, 9)
        ins.append(fmt(n, " ".join(str(rnd.randint(0, 3)) for _ in range(n))))
    n = 200000
    ins.append(fmt(n, " ".join("1" for _ in range(n))))                 # reachable, long
    ins.append(fmt(n, " ".join("0" if i == n // 2 else "2" for i in range(n))))  # blocked? (2 hops over 0)
    ins.append(fmt(n, " ".join("0" if i in (n // 2, n // 2 + 1) else "1" for i in range(n))))  # blocked
    return ins


# ---------- Problem 3: minimum coins ----------
def coins_inputs():
    ins = [fmt("3 6", "1 3 4"), fmt("1 3", "2"), fmt("2 0", "1 5"), fmt("3 11", "1 2 5"),
           fmt("2 7", "2 4"), fmt("4 23", "1 5 10 21"), fmt("3 9", "2 5 7")]
    for _ in range(30):
        k = rnd.randint(1, 4)
        coins = rnd.sample(range(1, 13), k)
        ins.append(fmt(f"{k} {rnd.randint(0, 40)}", " ".join(map(str, coins))))
    k = 100
    ins.append(fmt(f"{k} 10000", " ".join(str(c) for c in rnd.sample(range(50, 9999), k))))
    ins.append(fmt("5 9999", "7 11 13 17 19"))
    return ins


PROBLEMS = [
    {
        "id": "max-subarray",
        "title": "Best stretch",
        "link": "",
        "statement": (
            "You are given a sequence of n integers (they can be negative).\n"
            "Pick a non-empty block of consecutive elements so that the sum of the block is as large as possible.\n"
            "Print that largest sum.\n\n"
            "Input: first line n, second line n integers.\n"
            "Output: one integer.\n"
            "Limits: 1 <= n <= 200000, |a_i| <= 10^9.\n\n"
            "Example: 5 -9 6 -2  ->  6"
        ),
        "rubric": {
            "key_insight": "The best block ending at position i is either just a[i] or a[i] plus the best block ending at i-1. Drop the running sum when it goes negative. Track the global best separately.",
            "accepted_approaches": ["Kadane's algorithm (running sum, reset when negative)", "prefix sums with running minimum prefix", "divide and conquer O(n log n)"],
            "target_complexity": "O(n) (O(n log n) acceptable). O(n^2) is too slow for n=200000.",
            "edge_cases": ["all numbers negative: answer is the largest single element, not 0", "n=1"],
            "common_wrong": ["initialising best to 0 (fails when all negative)", "just summing all positives", "checking all blocks (too slow)"],
        },
        "reference": (
            "import sys\n"
            "def main():\n"
            "    d = sys.stdin.read().split(); n = int(d[0]); a = list(map(int, d[1:1+n]))\n"
            "    best = cur = a[0]\n"
            "    for x in a[1:]:\n"
            "        cur = max(x, cur + x); best = max(best, cur)\n"
            "    print(best)\n"
            "main()\n"
        ),
        "inputs": max_subarray_inputs,
    },
    {
        "id": "jump-game",
        "title": "Stepping stones",
        "link": "",
        "statement": (
            "There are n stones in a row, numbered 0 to n-1. You start on stone 0.\n"
            "Stone i has a number a_i: from it you may jump forward any distance from 1 up to a_i (or not at all if a_i = 0).\n"
            "Can you reach stone n-1? Print YES or NO.\n\n"
            "Input: first line n, second line n integers a_0 ... a_{n-1}.\n"
            "Output: YES or NO.\n"
            "Limits: 1 <= n <= 200000, 0 <= a_i <= 10^9.\n\n"
            "Example: 3 2 1 0 4  ->  NO"
        ),
        "rubric": {
            "key_insight": "Sweep left to right keeping the furthest index reachable so far. If you ever stand on an index beyond that furthest reach, you are stuck. You don't need to simulate individual jumps. Greedy on 'furthest reach' is correct because reachable positions form a prefix.",
            "accepted_approaches": ["greedy furthest-reach sweep", "DP/BFS over positions with an optimisation (plain DP is O(n^2) and too slow)", "work backwards keeping the leftmost good position"],
            "target_complexity": "O(n)",
            "edge_cases": ["n=1 is always YES", "a zero in the middle can be jumped over if an earlier stone reaches past it", "zeros that form an uncrossable wall"],
            "common_wrong": ["always jumping the maximum distance (fails)", "answering NO whenever any zero exists", "recursion/backtracking over all jump lengths (exponential)"],
        },
        "reference": (
            "import sys\n"
            "def main():\n"
            "    d = sys.stdin.read().split(); n = int(d[0]); a = list(map(int, d[1:1+n]))\n"
            "    far = 0\n"
            "    for i in range(n):\n"
            "        if i > far: print('NO'); return\n"
            "        far = max(far, i + a[i])\n"
            "    print('YES')\n"
            "main()\n"
        ),
        "inputs": jump_inputs,
    },
    {
        "id": "min-coins",
        "title": "Exact change",
        "link": "",
        "statement": (
            "A shop has k kinds of coins with the given values, each in unlimited supply.\n"
            "You must pay exactly T using as few coins as possible. Print that minimum number of coins, or -1 if it is impossible.\n\n"
            "Input: first line k and T, second line the k coin values.\n"
            "Output: one integer.\n"
            "Limits: 1 <= k <= 100, 0 <= T <= 10000, 1 <= coin value <= 10000.\n\n"
            "Example: coins 1 3 4, T = 6  ->  2"
        ),
        "rubric": {
            "key_insight": "Define best[x] = fewest coins to make exactly x. best[0] = 0 and best[x] = 1 + min(best[x - c]) over coins c <= x with best[x-c] reachable. Fill x upward. Answer best[T], or -1 if unreachable. Greedy (always take the largest coin) is WRONG in general, e.g. coins 1 3 4 and T=6 gives 3 coins greedy but 2 optimal.",
            "accepted_approaches": ["bottom-up DP over amounts", "memoised recursion on remaining amount", "BFS over amounts (each coin is an edge)"],
            "target_complexity": "O(T * k)",
            "edge_cases": ["T = 0 -> 0 coins", "impossible amounts -> -1", "greedy failure cases"],
            "common_wrong": ["largest-coin-first greedy", "unmemoised recursion (exponential)"],
        },
        "reference": (
            "import sys\n"
            "def main():\n"
            "    d = sys.stdin.read().split(); k, T = int(d[0]), int(d[1]); coins = list(map(int, d[2:2+k]))\n"
            "    INF = float('inf'); best = [0] + [INF] * T\n"
            "    for x in range(1, T + 1):\n"
            "        for c in coins:\n"
            "            if c <= x and best[x - c] + 1 < best[x]: best[x] = best[x - c] + 1\n"
            "    print(best[T] if best[T] != INF else -1)\n"
            "main()\n"
        ),
        "inputs": coins_inputs,
    },
]


def main():
    os.makedirs(os.path.join(HERE, "problems"), exist_ok=True)
    for p in PROBLEMS:
        tests = []
        for inp in p["inputs"]():
            status, out = run_code(p["reference"], inp, timeout=20)
            assert status == "ok", f"reference failed on {p['id']}: {status} {out}"
            tests.append({"input": inp, "output": out})
        data = {k: p[k] for k in ("id", "title", "link", "statement", "rubric", "reference")}
        data["tests"] = tests
        with open(os.path.join(HERE, "problems", p["id"] + ".json"), "w") as f:
            json.dump(data, f)
        print(f"{p['id']}: {len(tests)} tests")


if __name__ == "__main__":
    main()
