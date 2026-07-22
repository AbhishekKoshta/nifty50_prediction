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
import re
import sys
import time
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


# ---- layer 0: free news + market (always; NO API key) ------------------------------
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
_DOVISH = ("ceasefire", "cease-fire", "truce", "peace talk", "peace deal", "de-escalat",
           "diplomacy", "negotiat", "agreement", "stand down", "withdraw", "pause", "detente")
_HAWKISH = ("strike", "attack", "escalat", "blockade", "missile", "killed", "offensive",
            "bomb", "retaliat", "targets", "assault", "invasion", "threat")

# scrub personal names from fetched headlines (dashboard rule: institutions/roles only)
_NAME_SUB = [
    (r"\b(Trump|Biden)\b", "the US administration"),
    (r"\bModi\b", "the Indian govt"),
    (r"\bNetanyahu\b", "Israel"),
    (r"\bKhamenei\b", "Iran's leadership"),
    (r"\b(Warsh|Powell)\b", "the Fed"),
    (r"\b(Goyal|Sitharaman|Piyush|Malhotra)\b", "Indian officials"),
]


def _scrub(text: str) -> str:
    for pat, rep in _NAME_SUB:
        text = re.sub(pat, rep, text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def gdelt(query: str, timespan: str = "3d", n: int = 6, must_match=(), _last=[0.0]):
    """Newest-first headlines from the free, key-less GDELT DOC 2.0 API. Honors GDELT's
    1-request-per-5-seconds limit and retries once on a throttled/empty response. Fetches a
    wide pool, keeps only titles that contain a `must_match` term (relevance — GDELT's loose
    matching otherwise surfaces off-topic articles), de-duplicates syndicated copies, and
    scrubs personal names to roles. Returns up to `n`, or [] on failure."""
    import requests
    for attempt in (0, 1):
        wait = (6.0 if attempt else 5.2) - (time.monotonic() - _last[0])
        if wait > 0:
            time.sleep(wait)
        try:
            r = requests.get(GDELT_URL, timeout=25, headers={"User-Agent": "nifty-teller/1.0"},
                             params={"query": query, "mode": "artlist", "maxrecords": max(4 * n, 25),
                                     "timespan": timespan, "sort": "datedesc", "format": "json"})
            _last[0] = time.monotonic()
            arts = r.json().get("articles", [])
        except Exception:  # noqa: BLE001  (rate-limit text, timeout, bad JSON…)
            _last[0] = time.monotonic()
            arts = []
        out, seen = [], set()
        for a in arts:
            title = _scrub((a.get("title") or "").strip())
            low = title.lower()
            if not title or low in seen or (must_match and not any(k in low for k in must_match)):
                continue
            seen.add(low)
            sd = a.get("seendate", "")
            out.append({"date": f"{sd[0:4]}-{sd[4:6]}-{sd[6:8]}" if len(sd) >= 8 else "",
                        "title": title, "domain": a.get("domain", ""), "url": a.get("url", "")})
            if len(out) >= n:
                break
        if out:
            return out
    return []


def _tone(headlines):
    """Per-headline dovish/hawkish classification → (hawkish_count, dovish_count)."""
    hawk = dove = 0
    for h in headlines:
        t = h["title"].lower()
        d = any(k in t for k in _DOVISH)
        w = any(k in t for k in _HAWKISH)
        if w and not d:
            hawk += 1
        elif d and not w:
            dove += 1
    return hawk, dove


def free_refresh(data: dict, today: date) -> None:
    """Free layer (no key): GDELT headlines + a keyword-tone signal + yfinance market quotes."""
    iran = gdelt("Iran ceasefire sourcelang:english", "3d", 6,
                 must_match=("iran", "tehran", "israel", "hormuz"))
    india = gdelt("India United States trade deal sourcelang:english", "7d", 6,
                  must_match=("india", "tariff"))
    if iran or india:  # don't clobber prior headlines if the fetch failed
        data["headlines"] = {"iran": iran, "india_us": india, "fetched": today.isoformat()}

    # keyword tone → ONE net heuristic signal on the peace model, replaced each run
    pm = data.setdefault("peace_model", {})
    sigs = [s for s in pm.get("signals", []) if s.get("source") != "gdelt-heuristic"]
    hawk, dove = _tone(iran)
    if iran and hawk != dove:
        sigs.append({"date": today.isoformat(),
                     "kind": "hawkish" if hawk > dove else "dovish",
                     "weight": "medium" if abs(hawk - dove) >= 3 else "weak",
                     "note": f"GDELT tone: {hawk} hawkish vs {dove} dovish headlines (3d)",
                     "source": "gdelt-heuristic"})
    pm["signals"] = sigs

    # yfinance quotes — crude is the Iran→NIFTY channel; INR/VIX for context
    market = {}
    try:
        import yfinance as yf
        for key, tk in (("brent", "BZ=F"), ("usdinr", "INR=X"), ("indiavix", "^INDIAVIX")):
            h = yf.Ticker(tk).history(period="5d")
            if len(h):
                last = float(h["Close"].iloc[-1])
                prev = float(h["Close"].iloc[-2]) if len(h) > 1 else last
                market[key] = {"last": round(last, 2), "chg_pct": round((last / prev - 1) * 100, 2)}
    except Exception:  # noqa: BLE001
        pass
    if market:
        market["fetched"] = today.isoformat()
        data["market"] = market

    if (iran or india) or market:  # something fresh landed → stamp the refresh
        data["last_updated"] = today.isoformat()
        if data.get("updated_by") != "daily-refresh":  # preserve the LLM stamp if it ran
            data["updated_by"] = "auto-refresh"


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
        print("news refresh: ANTHROPIC_API_KEY not set — skipping the LLM layer")

    # free layer (always, no key): GDELT headlines + keyword-tone signal + market quotes
    free_refresh(data, today)
    hl = data.get("headlines", {})
    print(f"free news+market: {len(hl.get('iran', []))} Iran + {len(hl.get('india_us', []))} "
          f"India-US headlines; market={'yes' if data.get('market') else 'no'}")

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
