# Logic Judge (cf-but-simpler)

Practice real, rated Codeforces problems by describing your approach in plain English. No code.

## How it judges

1. **Rubric gate** (Claude Haiku, through the Claude Code CLI): checks your description against a hidden rubric (key idea, complexity, edge cases). For Codeforces problems the rubric is generated from the official editorial the first time you open a problem, then cached in `user/rubrics.json`. Obvious misses get instant feedback and nothing is executed. If you think it was too harsh, press **Run anyway**.
2. **Translate and run**: Claude turns your description into a C++ program *exactly as described* (it is told not to fix your mistakes), compiles it, and runs it against the problem's tests with the real time and memory limits, smallest tests first. Problems with several valid answers use the dataset's checker program. Problems that use `input.txt`/`output.txt` are run that way.
3. **Second opinion on failure**: a fresh, independent translation by a different model (OpenAI's Luna, through the Codex CLI) is tried before you are told you are wrong. Only if both fail do you get "Wrong" plus the smallest failing input, or "too slow" if it timed out.

Identical resubmissions are cached in `user/cache.json` and cost nothing.

The web UI follows the Codeforces layout: a problemset table (green/red rows for solved/failed, difficulty sorting, optional tags), problem pages with the usual statement and sample boxes, and **My submissions** with Codeforces-style verdicts ("Wrong answer on test 4", "Time limit exceeded on test 13", ...). Every submission is kept in `user/submissions.json`; open one to see the judge's comment, the failing test (input, output, answer) and the program your description was turned into.

## Getting the Codeforces problems (built on GitHub, not on your PC)

The source is the Hugging Face dataset `open-r1/codeforces`. Its generated tests are about 110 GB, so GitHub Actions processes it instead of your computer: one contest at a time, deleting each download right after use, keeping a capped set of tests per problem, and publishing small `.sqlite` files to a GitHub Release.

1. **Push the repo.** The first push automatically runs the **CI** workflow, including `hf-smoke`, which imports a small slice of the real dataset and checks the result. A green check means the Hugging Face path works.
2. **Optional:** add a free Hugging Face read token as a repo secret named `HF_TOKEN` (Settings, Secrets and variables, Actions). It avoids download rate limits.
3. **Build the data:** Actions tab, **Build Codeforces dataset shards**, **Run workflow**. Options:
   - `subset`: `verifiable` (recommended; problems with trustworthy tests) or `default` (literally every problem in the dataset, including ones the dataset authors couldn't verify; those are flagged "tests less verified" in the app).
   - `rated_only`: skip problems without a rating.
   - `min_rating` / `max_rating`, `max_tests`.
4. **Check the run summary.** Each job reports how many problems were loaded and exactly why any were left out.
5. **Download shards** from the repo's Releases page (`data-v1`) into this project's `data/` folder. Each file is named `c<first>-<last>.sqlite` after the contest IDs it covers (recent contests have the highest IDs), with a matching `.manifest.json` showing counts and sizes. Download as few or as many as your disk allows.

## What gets loaded

Everything that can be judged is loaded, including unrated problems, problems that use files for input/output, Div.1/Div.2 duplicates kept once, and problems with very few tests. Only these are left out, and each is counted in the run summary:

- **Interactive problems**: they need a grader that the dataset doesn't provide.
- **Duplicate copies** of a problem already kept.
- **Problems with no usable tests.**
- **Unverified problems** unless you choose `subset: default`.
- Anything outside the rating range, or unrated if you chose `rated_only`.

Tests are the dataset's own: official tests where they were not truncated, plus generated tests that were checked against several human solutions. They are good but are not Codeforces's own hidden tests. Very large tests (over 3 MB) are dropped to save space, and affected problems are flagged ("capped") in the feedback.

Codeforces writes inline math as `$$$...$$$`. The markers are kept and the web page typesets them with KaTeX, like on Codeforces. (Shards built before this change have the markers stripped; their formulas show as plain text until rebuilt.)

## Run it on GitHub (Codespaces)

Nothing runs on your computer: the data, the C++ compiling and the model tools all live on a GitHub machine, and you use the app in your browser.

1. On the repo page: **Code**, **Codespaces**, **Create codespace on main**. The first start installs Python, g++, Claude Code and Codex (a few minutes).
2. In the codespace terminal, log in once to each tool (both print a link and a code to open in your browser):
   ```
   claude auth login
   codex login --device-auth
   ```
3. Download the problem shards you want from the release (step 5 above), for example:
   ```
   gh release download data-v1 -p 'c1900-*.sqlite' -D data
   ```
4. Start the app; Codespaces opens it in a browser tab:
   ```
   python app.py
   ```

Codespaces stop after 30 idle minutes and keep their files and logins; reopen the same codespace from the repo page. Free accounts get a monthly allowance of hours and storage, so download only the shards you need and delete codespaces you no longer use.

## Run the app locally

```
pip install -r requirements.txt
python app.py                            # open http://127.0.0.1:5000
```

You need `g++` (Linux: usually installed; Windows: MSYS2/MinGW or WSL). On macOS the built-in compiler has no `<bits/stdc++.h>`, which the translated programs usually include, so use Codespaces or install GNU g++. Without g++ the app falls back to Python, which is much slower than the limits assume (limits are multiplied by 4).

## Models (through their CLIs, no API keys)

The judge talks to the models through their command-line tools, so it uses your own Claude and ChatGPT logins:

- **Claude Code** (`claude -p`) writes the rubrics, runs the rubric check and makes the first translation.
- **OpenAI Codex** (`codex exec`) with the **Luna** model makes the second, independent translation before you are told you are wrong. If Codex is missing or logged out, a fresh Claude translation is used instead.

Each call starts the CLI in an empty folder with tools turned off, so the models only read the problem and your text. Calls count toward your Claude and ChatGPT plan limits; a submission makes 2 to 4 calls, and identical resubmissions are free (cached).

Settings (environment variables). Models are written as `<tool>:<model id>`, where the tool is `claude`, `codex` or `api` (Anthropic API with `ANTHROPIC_API_KEY`):

- `JUDGE_MODEL` (default `claude:claude-haiku-5-5`): rubric generation, rubric gate, first translation
- `RETRY_MODEL` (default `codex:gpt-6-luna`): second translation. `codex:gpt-5.6-luna` also works.
- `CLI_TIMEOUT` (default `300`): seconds to wait for one model call
- `JUDGE_LANG` (`cpp` or `python`): force the translation language

## Tests

`python tests/run_checks.py` builds a fake dataset with the real schema and checks the importer, the store, the C++ judge, checkers, file I/O and the API (model calls are mocked). CI runs it on every push.

## Starter problems

Three hand-written problems (`problems/*.json`) are included. Add more in `make_problems.py` and run `python make_problems.py`.

## Safety note

Generated code runs in a subprocess with time and memory limits, but this is not a hardened sandbox. That is fine for local personal use. Do not expose the server to the internet. Never commit your API keys: `.env` files are git-ignored.
