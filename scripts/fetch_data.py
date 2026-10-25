#!/usr/bin/env python3
"""Download Codeforces data shards from this repo's GitHub Release into data/.

Examples:
  python scripts/fetch_data.py --list                 # show shards and sizes, download nothing
  python scripts/fetch_data.py                        # download newest contests first, up to 8 GB
  python scripts/fetch_data.py --max-gb 3             # smaller budget
  python scripts/fetch_data.py --only 'c1[89]*'       # only shards matching a pattern
  python scripts/fetch_data.py --order oldest

Re-running only downloads what is missing. The repo is taken from $GITHUB_REPOSITORY (set
inside Codespaces) or from `git remote get-url origin`.
"""
import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data")


def detect_repo():
    repo = os.environ.get("GITHUB_REPOSITORY")
    if repo:
        return repo
    try:
        url = subprocess.check_output(["git", "remote", "get-url", "origin"], text=True, cwd=HERE).strip()
    except Exception:
        sys.exit("Could not work out the GitHub repo. Pass --repo owner/name.")
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+)", url)
    if not m:
        sys.exit(f"Could not parse a GitHub repo from {url!r}. Pass --repo owner/name.")
    return m.group(1)


def api(url):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "cf-fetch"})
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def contest_key(name):
    m = re.match(r"c(\d+)-(\d+)", name)
    return int(m.group(1)) if m else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo")
    ap.add_argument("--tag", default="data-v1")
    ap.add_argument("--max-gb", type=float, default=8.0, help="stop once this much would be exceeded")
    ap.add_argument("--only", action="append", help="fnmatch pattern on the file name; repeatable")
    ap.add_argument("--order", choices=["newest", "oldest"], default="newest",
                    help="which contest ranges to take first when the budget is limited")
    ap.add_argument("--list", action="store_true", help="only list the shards")
    ap.add_argument("--api-base", default="https://api.github.com")
    ap.add_argument("--data-dir", default=DATA)
    a = ap.parse_args()
    data_dir = a.data_dir

    repo = a.repo or detect_repo()
    try:
        rel = api(f"{a.api_base}/repos/{repo}/releases/tags/{a.tag}")
    except Exception as e:
        sys.exit(f"Could not read release '{a.tag}' of {repo} ({e}). "
                 "Has the 'Build Codeforces dataset shards' workflow finished?")
    assets = [x for x in rel.get("assets", []) if x["name"].endswith(".sqlite")]
    if a.only:
        assets = [x for x in assets if any(fnmatch.fnmatch(x["name"], p) for p in a.only)]
    assets.sort(key=lambda x: (contest_key(x["name"]), x["name"]), reverse=(a.order == "newest"))
    if not assets:
        sys.exit("No matching shards in the release yet.")

    os.makedirs(data_dir, exist_ok=True)
    budget = int(a.max_gb * 1e9)
    used = sum(os.path.getsize(os.path.join(data_dir, f)) for f in os.listdir(data_dir) if f.endswith(".sqlite"))
    total_new = 0
    for x in assets:
        dest = os.path.join(data_dir, x["name"])
        have = os.path.exists(dest) and os.path.getsize(dest) == x["size"]
        mark = "have" if have else "get "
        if a.list:
            print(f"{mark} {x['name']:28} {x['size'] / 1e6:9.1f} MB")
            continue
        if have:
            print(f"have {x['name']}")
            continue
        if used + x["size"] > budget:
            print(f"skip {x['name']} ({x['size'] / 1e6:.0f} MB): would exceed --max-gb {a.max_gb}")
            continue
        print(f"get  {x['name']} ({x['size'] / 1e6:.0f} MB) ...", flush=True)
        tmp = dest + ".part"
        urllib.request.urlretrieve(x["browser_download_url"], tmp)
        os.replace(tmp, dest)
        used += x["size"]
        total_new += x["size"]
    if a.list:
        print(f"\n{sum(x['size'] for x in assets) / 1e9:.2f} GB in {len(assets)} shards")
    else:
        print(f"\ndone: downloaded {total_new / 1e6:.0f} MB; data/ now holds {used / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
