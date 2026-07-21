#!/usr/bin/env python3
"""update_macro.py — daily refresh of macro_events.json for the 🌍 Macro & Events tab.

Two layers, run in order:

  1. DETERMINISTIC MAINTENANCE (always; no API key, no cost, no network):
       • prune diplomatic signals older than SIGNAL_TTL_DAYS (they've decayed to ~nil)
       • drop calendar events more than KEEP_PAST_DAYS in the past (the app filters them
         out anyway; this just keeps the file tidy)
       • validate the JSON + the model, and only rewrite if something actually changed

  2. NEWS REFRESH (only if ANTHROPIC_API_KEY is set):
       • calls Claude (web search) to re-research the narrative and rewrite the facts,
         following MACRO_REFRESH.md — India-US deal, Iran war (incl. peace_model.phase_start
         if a ceasefire collapsed), new calendar events, dovish/hawkish signals.
       • the result is validated the same way before it's written; on any failure the file
         is left untouched and the deterministic result stands.

The app's date math (calendar D-1/D-0 flags, the peace curve) advances on its own, so the
deterministic layer is a light touch — the value is layer 2, which needs the key.

Run locally:   python3 update_macro.py            # deterministic only (no key)
In CI:         ANTHROPIC_API_KEY=... python3 update_macro.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

DATA_PATH = "macro_events.json"
IST = timezone(timedelta(hours=5, minutes=30))
SIGNAL_TTL_DAYS = 21
KEEP_PAST_DAYS = 7
MODEL = "claude-opus-4-8"


def today_ist() -> date:
    return datetime.now(IST).date()


def load(path=DATA_PATH) -> dict:
    with open(path) as f:
        return json.load(f)


# ---- layer 1: deterministic maintenance -------------------------------------------
def maintain(data: dict, today: date) -> bool:
    """Prune stale signals + past events. Returns True if anything changed."""
    changed = False

    pm = data.get("peace_model", {})
    sigs = pm.get("signals", [])
    kept = [s for s in sigs
            if "date" not in s or (today - date.fromisoformat(s["date"])).days <= SIGNAL_TTL_DAYS]
    if len(kept) != len(sigs):
        pm["signals"] = kept
        changed = True

    cal = data.get("calendar", [])
    fresh = [e for e in cal
             if (date.fromisoformat(e["date"]) - today).days >= -KEEP_PAST_DAYS]
    if len(fresh) != len(cal):
        data["calendar"] = fresh
        changed = True

    return changed


# ---- layer 2: LLM news refresh (only with ANTHROPIC_API_KEY) -----------------------
def llm_refresh(data: dict, today: date) -> dict | None:
    """Ask Claude (web search) to rewrite the facts per MACRO_REFRESH.md. Returns the new
    dict, or None on any failure (caller keeps the deterministic result)."""
    import re

    try:
        import anthropic
    except ImportError:
        print("  anthropic package not installed — skipping news refresh")
        return None

    try:
        playbook = open("MACRO_REFRESH.md").read()
    except OSError:
        playbook = "(playbook missing — update facts only, keep the JSON schema intact)"

    prompt = (
        f"You are the daily macro-refresh agent for the NIFTY Teller app. Today (IST) is "
        f"{today.isoformat()}. Follow this playbook exactly:\n\n=== MACRO_REFRESH.md ===\n{playbook}\n\n"
        f"=== current macro_events.json ===\n{json.dumps(data, indent=2, ensure_ascii=False)}\n\n"
        "Using web search, research the CURRENT status of: the India-US trade deal; the Iran-US war "
        "(any ceasefire / peace talks / new fighting phase — if a ceasefire has collapsed, set "
        "peace_model.phase_start to the new phase-start date and note it in phase_note); and any "
        "newly-announced high-impact scheduled NIFTY events to add to calendar[]. Append any dovish/"
        "hawkish diplomatic headline to peace_model.signals[] using the documented schema.\n\n"
        "Then output the COMPLETE updated macro_events.json. Rules: facts only, no fabrication; keep "
        "the exact same schema/keys; NO personal names of officials (institutions/roles only); set "
        f'last_updated to "{today.isoformat()}" and updated_by to "daily-refresh". '
        "Output ONLY the JSON object — no prose, no markdown fences."
    )

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}]

    resp = None
    for _ in range(6):  # bound server-tool pause_turn resumes
        resp = client.messages.create(model=MODEL, max_tokens=16000, tools=tools, messages=messages)
        if resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        break

    text = "".join(b.text for b in (resp.content if resp else []) if b.type == "text").strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        print("  news refresh: no JSON found in model output — keeping deterministic result")
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        print(f"  news refresh: invalid JSON from model ({e}) — keeping deterministic result")
        return None


# ---- validation gate ---------------------------------------------------------------
def valid(data: dict, today: date) -> bool:
    """Same gate MACRO_REFRESH.md requires: schema intact + the model runs clean."""
    for key in ("calendar", "dynamic", "peace_model"):
        if key not in data:
            print(f"  validation FAILED: missing '{key}'")
            return False
    try:
        import macro_events as m
        m.build_calendar(today, data)
        m.peace_model(today, data["peace_model"])
    except Exception as e:  # noqa: BLE001
        print(f"  validation FAILED: model error — {e}")
        return False
    return True


def main() -> int:
    today = today_ist()
    data = load()
    before = json.dumps(data, sort_keys=True, ensure_ascii=False)

    changed = maintain(data, today)
    print(f"deterministic maintenance: {'pruned stale entries' if changed else 'nothing to prune'}")

    if os.environ.get("ANTHROPIC_API_KEY"):
        print(f"news refresh: calling {MODEL} with web search…")
        fresh = llm_refresh(data, today)
        if fresh is not None and valid(fresh, today):
            data = fresh
            print("  news refresh: applied")
        elif fresh is not None:
            print("  news refresh: result failed validation — discarded")
    else:
        print("news refresh: ANTHROPIC_API_KEY not set — skipping (deterministic only)")

    if not valid(data, today):
        print("FINAL validation failed — leaving file untouched")
        return 1

    after = json.dumps(data, sort_keys=True, ensure_ascii=False)
    if after == before:
        print("no changes — nothing to write")
        return 0

    with open(DATA_PATH, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {DATA_PATH} (last_updated={data.get('last_updated')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
