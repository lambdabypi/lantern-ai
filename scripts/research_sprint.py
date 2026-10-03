"""Research sprint: what do people complain about and wish for in personal AI assistants?

1. Run one Tavily basic search per query in research/queries.txt (1 credit each).
2. Save raw results to research/raw/<run>/ (gitignored).
3. Send results in batches to Nemotron Nano to extract complaints and wishes as JSON.
4. Ask Nemotron Ultra once to cluster everything into 5-10 themes.
5. Write aggregated themes (no names, usernames or quotes) to research/findings.md.
6. Print Tavily credits and tokens used.

Identical searches and LLM calls are cached in .cache/, so re-running is free.

Usage:
    python scripts/research_sprint.py
    python scripts/research_sprint.py --max-results 5 --batch-size 6
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import ConfigError, load_settings  # noqa: E402
from app.llm.client import LLMError  # noqa: E402
from app.llm.parsing import extract_json  # noqa: E402
from app.llm.router import ModelRouter, Tier  # noqa: E402
from app.tools.web_search import SearchError, WebSearch  # noqa: E402

SNIPPET_CHARS = 1200
ITEM_CHARS = 300

EXTRACT_SYSTEM = """You analyse web search results about personal AI assistants and chatbots.
Extract every distinct user COMPLAINT (a frustration or problem) and WISH (a feature or behaviour people want).

Rules:
- Only include items about AI assistants, chatbots or AI agents.
- Paraphrase each item as one short, general sentence.
- Never include names, usernames, handles or direct quotes.
- If a snippet has nothing relevant, skip it.

Reply with only JSON, no other text:
{"items": [{"kind": "complaint" or "wish", "text": "...", "source": <snippet number>}]}"""

CLUSTER_SYSTEM = """You group user feedback about personal AI assistants into themes.

Group the numbered items into 5 to 10 themes. For each theme give:
- "name": at most 6 words
- "summary": one line (at most 25 words) describing the shared need or pain point
- "item_ids": the ids of the items that belong to it

Put each item in at most one theme. Leave out off-topic items.
Reply with only JSON, no other text:
{"themes": [{"name": "...", "summary": "...", "item_ids": [1, 2, 3]}]}"""


def load_queries(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def run_searches(search: WebSearch, queries: list[str], max_results: int) -> list[dict[str, Any]]:
    """Search every query and return unique results (by URL), each tagged with its query."""
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for q in queries:
        for r in search.search(q, depth="basic", max_results=max_results):
            if r["url"] and r["url"] not in seen:
                seen.add(r["url"])
                results.append({**r, "query": q})
        print(f"  searched: {q!r}")
    return results


def ask_json(router: ModelRouter, messages: list[dict[str, str]], tier: Tier, purpose: str, max_tokens: int) -> Any:
    """Call the model and parse JSON from the reply, retrying once if it isn't valid."""
    r = router.complete(messages, tier, purpose=purpose, temperature=0, max_tokens=max_tokens)
    try:
        return extract_json(r.text)
    except ValueError:
        retry = messages + [
            {"role": "assistant", "content": r.text[-2000:]},
            {"role": "user", "content": "That was not valid JSON. Reply with only the JSON object, nothing else."},
        ]
        r = router.complete(retry, tier, purpose=purpose + ":retry", temperature=0, max_tokens=max_tokens)
        return extract_json(r.text)


def extract_items(router: ModelRouter, results: list[dict[str, Any]], batch_size: int) -> tuple[list[dict], int]:
    """Nano pass: pull complaints and wishes out of results. Returns (items, failed_batches)."""
    items: list[dict] = []
    failed = 0
    batches = [results[i : i + batch_size] for i in range(0, len(results), batch_size)]
    for n, batch in enumerate(batches, 1):
        snippets = "\n\n".join(
            f"[{i}] {r['title']}\n{r['content'][:SNIPPET_CHARS]}" for i, r in enumerate(batch, 1)
        )
        messages = [
            {"role": "system", "content": EXTRACT_SYSTEM},
            {"role": "user", "content": f"Search results:\n\n{snippets}"},
        ]
        try:
            data = ask_json(router, messages, Tier.FAST, "research:extract", max_tokens=4096)
        except (ValueError, LLMError) as e:
            failed += 1
            print(f"  WARNING: batch {n}/{len(batches)} skipped: {e}", file=sys.stderr)
            continue

        raw_items = data.get("items", []) if isinstance(data, dict) else []
        kept = 0
        for it in raw_items:
            if not isinstance(it, dict):
                continue
            kind, text = it.get("kind"), it.get("text")
            if kind not in ("complaint", "wish") or not isinstance(text, str) or not text.strip():
                continue
            src = it.get("source")
            url = batch[src - 1]["url"] if isinstance(src, int) and 1 <= src <= len(batch) else ""
            items.append({"kind": kind, "text": text.strip()[:ITEM_CHARS], "source_url": url})
            kept += 1
        print(f"  batch {n}/{len(batches)}: {kept} items")
    return items, failed


def cluster_themes(router: ModelRouter, items: list[dict]) -> tuple[tuple[list[dict], int], Any]:
    """Ultra pass (one call): group items into themes. Counts are computed here, not by the model.

    Returns ((themes sorted by count, number of unassigned items), raw model JSON).
    """
    listing = "\n".join(f"{i}. ({it['kind']}) {it['text']}" for i, it in enumerate(items, 1))
    messages = [
        {"role": "system", "content": CLUSTER_SYSTEM},
        {"role": "user", "content": f"Items:\n{listing}"},
    ]
    data = ask_json(router, messages, Tier.REASONING, "research:cluster", max_tokens=8192)
    return build_themes(data, items), data


def build_themes(data: Any, items: list[dict]) -> tuple[list[dict], int]:
    """Turn the model's theme -> item_ids mapping into counted themes.

    Ignores invalid ids, and an item listed under several themes counts only for the first.
    """
    assigned: set[int] = set()
    themes = []
    raw_themes = data.get("themes", []) if isinstance(data, dict) else []
    for t in raw_themes:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        ids = []
        for i in t.get("item_ids", []):
            if isinstance(i, int) and 1 <= i <= len(items) and i not in assigned:
                assigned.add(i)
                ids.append(i)
        if not ids:
            continue
        kinds = [items[i - 1]["kind"] for i in ids]
        themes.append({
            "name": str(t["name"]).strip(),
            "summary": str(t.get("summary", "")).strip(),
            "count": len(ids),
            "complaints": kinds.count("complaint"),
            "wishes": kinds.count("wish"),
        })
    themes.sort(key=lambda t: t["count"], reverse=True)
    return themes, len(items) - len(assigned)


def render_findings(themes: list[dict], *, unassigned: int, stats: dict[str, Any]) -> str:
    def cell(s: str) -> str:
        return s.replace("|", "/").replace("\n", " ")

    lines = [
        "# Research findings: personal AI assistants",
        "",
        f"_Generated by `scripts/research_sprint.py` on {stats['date']}. "
        "Aggregated themes only: no names, usernames or quotes._",
        "",
        f"- **Sources:** {stats['results']} unique web results from {stats['queries']} Tavily basic searches",
        f"- **Extracted:** {stats['complaints']} complaints and {stats['wishes']} wishes "
        f"(Nemotron Nano), clustered into {len(themes)} themes (Nemotron Ultra)",
        f"- **Not assigned to any theme:** {unassigned}",
        "",
        "| # | Theme | Mentions | Complaints | Wishes | Summary |",
        "|---|---|---|---|---|---|",
    ]
    for n, t in enumerate(themes, 1):
        lines.append(
            f"| {n} | {cell(t['name'])} | {t['count']} | {t['complaints']} | {t['wishes']} | {cell(t['summary'])} |"
        )
    lines += ["", "## Queries", ""] + [f"- {q}" for q in stats["query_list"]] + [""]
    return "\n".join(lines)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queries", type=Path, default=ROOT / "research" / "queries.txt")
    parser.add_argument("--max-results", type=int, default=10, help="results per search (credit cost is the same)")
    parser.add_argument("--batch-size", type=int, default=8, help="search results per Nano call")
    parser.add_argument("--out", type=Path, default=ROOT / "research" / "findings.md")
    args = parser.parse_args()

    try:
        settings = load_settings(ROOT / ".env")
        settings.require("model_fast", "model_reasoning")
        router = ModelRouter(settings)
        search = WebSearch(settings)
        queries = load_queries(args.queries)
        if not queries:
            print(f"No queries found in {args.queries}", file=sys.stderr)
            return 1

        run_dir = ROOT / "research" / "raw" / datetime.now().strftime("%Y%m%d-%H%M%S")

        print(f"[1/4] Searching {len(queries)} queries with Tavily (basic)...")
        results = run_searches(search, queries, args.max_results)
        save_json(run_dir / "search_results.json", results)

        print(f"[2/4] Extracting complaints and wishes from {len(results)} results with Nano...")
        items, failed = extract_items(router, results, args.batch_size)
        save_json(run_dir / "items.json", items)
        if not items:
            print("No items extracted; nothing to cluster.", file=sys.stderr)
            return 1

        print(f"[3/4] Clustering {len(items)} items into themes with Ultra...")
        (themes, unassigned), raw_themes = cluster_themes(router, items)
        save_json(run_dir / "themes_raw.json", raw_themes)
        if not themes:
            print("Ultra returned no usable themes. Raw output saved in " + str(run_dir), file=sys.stderr)
            return 1

        print(f"[4/4] Writing {args.out}...")
        stats = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "queries": len(queries),
            "query_list": queries,
            "results": len(results),
            "complaints": sum(1 for i in items if i["kind"] == "complaint"),
            "wishes": sum(1 for i in items if i["kind"] == "wish"),
        }
        args.out.write_text(render_findings(themes, unassigned=unassigned, stats=stats), encoding="utf-8")
    except (ConfigError, LLMError, SearchError) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    except ValueError as e:  # clustering reply wasn't valid JSON even after a retry
        print(f"FAILED: Ultra's reply couldn't be parsed as JSON ({e}).", file=sys.stderr)
        return 1

    print("\nThemes:")
    for t in themes:
        print(f"  {t['count']:>4}  {t['name']}")
    if failed:
        print(f"\nWARNING: {failed} extraction batch(es) failed and were skipped.")

    print("\nUsage")
    print(f"  Tavily: {search.credits_used} credits ({search.live_searches} live, {search.cached_searches} cached)")
    total = 0
    for model, u in router.usage_summary().items():
        tokens = u["prompt_tokens"] + u["completion_tokens"]
        total += tokens
        print(f"  {model}: {u['calls']} calls ({u['cached_calls']} cached), "
              f"{u['prompt_tokens']} in + {u['completion_tokens']} out = {tokens} tokens")
    print(f"  Total tokens: {total}")
    print(f"\nRaw data: {run_dir.relative_to(ROOT)}  (gitignored)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
