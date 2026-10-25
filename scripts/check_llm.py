"""Quick self-test for your AI provider setup.

  python scripts/check_llm.py            # sends one tiny prompt and prints the reply
  python scripts/check_llm.py --models   # lists model ids your key can use (not for Anthropic)
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import llm  # noqa: E402


def main():
    try:
        p = llm.provider()
    except RuntimeError as e:
        print("Problem:", e)
        return 1
    if p is None:
        print("Problem:", llm.missing_message())
        return 1
    print(f"Provider: {p['name']}" + (f"  ({p['base']})" if p.get("base") else ""))
    if "--models" in sys.argv:
        try:
            for m in llm.list_models(p):
                print("  ", m)
        except Exception as e:
            print("Could not list models:", e)
            return 1
        return 0
    try:
        model = llm.judge_model()
        print("Judge model:", model, "| retry model:", llm.retry_model())
        out = llm.ask(model, "Reply with exactly one word.", "Say: ready", max_tokens=50)
    except Exception as e:
        print("FAILED:", e)
        return 1
    print("Reply:", out.strip()[:200])
    print("OK: the AI connection works.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
