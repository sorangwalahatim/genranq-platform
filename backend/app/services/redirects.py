"""Redirect chain / loop detection for the 301 redirect plan.

A chain is A -> B -> C (two or more redirect rules linked head-to-tail).
A loop is A -> B -> A (a cycle). Both hurt SEO and should be flattened.
"""
from __future__ import annotations


def _norm(url: str) -> str:
    return (url or "").strip().rstrip("/")


def find_chains(rules: list[dict]) -> list[dict]:
    """rules: list of {id, old_url, new_url, status}.

    Returns flagged problems:
      {"type": "loop", "hops": [u1, u2, u1], "rule_ids": [...], "final_url": None,
       "suggestion": "..."}
      {"type": "chain", "hops": [u1, u2, u3], "rule_ids": [...], "final_url": u3,
       "suggestion": "Point /old-1 directly to /final to flatten this chain."}
    """
    fwd = {}  # normalised old -> rule
    for r in rules:
        old = _norm(r["old_url"])
        if old:
            fwd[old] = r
    problems: list[dict] = []
    seen_global: set[str] = set()

    for start_old, start_rule in fwd.items():
        if start_old in seen_global:
            continue
        hops = [start_old]
        rule_ids = [str(start_rule["id"])]
        visited = {start_old}
        cur = start_rule
        looped = False
        while True:
            nxt = _norm(cur["new_url"])
            if not nxt:
                break
            hops.append(nxt)  # next hop (another rule's old_url, or the final live page)
            if nxt not in fwd:
                break  # chain ends at a real page
            rule_ids.append(str(fwd[nxt]["id"]))
            if nxt in visited:
                looped = True
                break
            visited.add(nxt)
            cur = fwd[nxt]
        seen_global.update(visited)
        if looped:
            cyc = hops[hops.index(hops[-1]):]  # the repeating cycle, e.g. /x -> /y -> /x
            problems.append({
                "type": "loop",
                "hops": cyc,
                "rule_ids": rule_ids,
                "final_url": None,
                "suggestion": "Break the loop: point one of these URLs to a final live page instead.",
            })
        elif len(hops) >= 3:
            final_url = hops[-1]
            problems.append({
                "type": "chain",
                "hops": hops,
                "rule_ids": rule_ids,
                "final_url": final_url,
                "suggestion": f"Flatten: point {hops[0]} directly to {final_url} (skips {len(hops) - 2} hop(s)).",
            })
    return problems
