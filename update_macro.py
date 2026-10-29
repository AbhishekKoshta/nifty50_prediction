#!/usr/bin/env python3
"""update_macro.py — daily refresh of macro_events.json for the 🌍 Macro & Events tab (v2).

Three layers, run in order:

  1. DETERMINISTIC MAINTENANCE (always; no key, no network):
       • prune diplomatic signals older than SIGNAL_TTL_DAYS, calendar events > KEEP_PAST_DAYS old,
         stale auto-tagged headlines, and `pending` items whose `expires` date has passed
       • validate the JSON + the models, and only rewrite if something actually changed

  2. LLM NEWS REFRESH (only if ANTHROPIC_API_KEY is set):
       • Claude (web search) re-researches the narrative per MACRO_REFRESH.md and rewrites the
         *hand-maintained* facts: dynamic[] trackers, pending[] (any-day catalysts + P(30d)),
         sector_policy[] (govt stance per sector), rates{}, calendar[] one-offs, schedule corrections,
         peace_model signals/phase. Auto-filled containers are withheld from the prompt and merged
         back afterwards, so the model can't clobber them.

  3. FREE DATA LAYER (always; no key):
       • NSE: trading holidays, NIFTY 50 / Next 50 / Bank Nifty members, the board-meeting (results)
         calendar for those members + which of them reported in the last 10 days
       • RSS: ET industry/economy/markets, BusinessLine economy, Mint economy → auto-tagged to
         sectors with a tone heuristic; RBI press releases + notifications; GDELT for PIB (English)
         and the Iran / India-US headline pools
       • yfinance: Brent, USD/INR, India VIX, DXY, US 10Y
       Every fetch is best-effort: on failure the previous snapshot is kept.

Run locally:   python3 update_macro.py            # layers 1 + 3 (no key)
In CI:         ANTHROPIC_API_KEY=... python3 update_macro.py
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

DATA_PATH = "macro_events.json"
IST = timezone(timedelta(hours=5, minutes=30))
SIGNAL_TTL_DAYS = 21
KEEP_PAST_DAYS = 7
NEWS_TTL_DAYS = 10
MODEL = "claude-opus-4-8"
AUTO_KEYS = ("earnings", "sector_news", "rbi_releases", "index_members", "nse_holidays", "market", "headlines")
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0 Safari/537.36")


def today_ist() -> date:
    return datetime.now(IST).date()


def load(path=DATA_PATH) -> dict:
    with open(path) as f:
        return json.load(f)


# ---- personal-name scrub (dashboard rule: institutions/roles only) -------------------
# best-effort: covers current-cycle officials; live news can still name others we don't list
_NAME_SUB = [
    (r"\b(Donald\s+)?Trump\b", "the US President"),
    (r"\b(Biden|Hegseth|Vance|Waltz|Bessent|Lutnick|Greer)\b", "the US administration"),
    (r"\bRubio\b", "the US Secretary of State"),
    (r"\b(Narendra\s+)?Modi\b", "the Prime Minister"),
    (r"\b(Nirmala\s+)?Sitharaman\b", "the finance minister"),
    (r"\b(Piyush\s+)?Goyal\b", "the commerce minister"),
    (r"\bJaishankar\b", "the external affairs minister"),
    (r"\b(Sanjay\s+)?Malhotra\b", "the RBI governor"),
    (r"\bTuhin\s+Kanta\s+Pandey\b", "the SEBI chief"),
    (r"\b(Ashwini\s+)?Vaishnaw\b", "the railways & IT minister"),
    (r"\b(Nitin\s+)?Gadkari\b", "the road transport minister"),
    (r"\bHardeep\s+(Singh\s+)?Puri\b", "the petroleum minister"),
    (r"\b(Rajnath\s+)?Singh\b(?=.*\b(defence|defense|DAC)\b)", "the defence minister"),
    (r"\b(Amit\s+)?Shah\b(?=.*\b(home|cooperat)\w*\b)", "the home minister"),
    (r"\b(Netanyahu|Katz)\b", "Israel's leadership"),
    (r"\b(Khamenei|Pezeshkian|Araghchi)\b", "Iran's leadership"),
    (r"\b(Warsh|Powell)\b", "the Fed chair"),
    (r"\bGuterres\b", "the UN"),
    (r"\b(Putin|Lavrov)\b", "Russia's leadership"),
    (r"\b(Xi\s+Jinping|Xi)\b", "China's leadership"),
]


def _scrub(text: str) -> str:
    for pat, rep in _NAME_SUB:
        text = re.sub(pat, rep, text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


# ---- GDELT (free, keyless, 1 req / 5 s) ---------------------------------------------
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
_DOVISH = ("ceasefire", "cease-fire", "truce", "peace talk", "peace deal", "de-escalat",
           "diplomacy", "negotiat", "agreement", "stand down", "withdraw", "pause", "detente")
_HAWKISH = ("strike", "attack", "escalat", "blockade", "missile", "killed", "offensive",
            "bomb", "retaliat", "targets", "assault", "invasion", "threat")


def gdelt(query: str, timespan: str = "3d", n: int = 6, must_match=(), _last=[0.0]):
    """Newest-first headlines from the free GDELT DOC 2.0 API. Honors the 1-request-per-5-seconds
    limit and retries once on a throttled/empty response. Keeps only titles containing a
    `must_match` term, de-duplicates syndicated copies, scrubs personal names. [] on failure."""
    import requests
    for attempt in (0, 1):
        wait = (7.0 if attempt else 5.5) - (time.monotonic() - _last[0])
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


# ---- RSS → sector-tagged policy headlines ---------------------------------------------
RSS_FEEDS = {
    "ET industry": "https://economictimes.indiatimes.com/industry/rssfeeds/13352306.cms",
    "ET economy": "https://economictimes.indiatimes.com/news/economy/finance/rssfeeds/1373380680.cms",
    "ET markets": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "BusinessLine economy": "https://www.thehindubusinessline.com/economy/feeder/default.rss",
    "Mint economy": "https://www.livemint.com/rss/economy",
}
RBI_FEEDS = {"press": "https://www.rbi.org.in/pressreleases_rss.xml",
             "notifications": "https://www.rbi.org.in/notifications_rss.xml"}

# sector → title keywords (lower-case substring match). Order = display order.
SECTOR_KW = {
    "Banks & NBFC": ["rbi", "repo", "liquidity", "omo", "crr", "npa", "bank", "nbfc", "credit growth", "deposit",
                     "lending", "microfinance", "housing finance"],
    "Rupee & FX": ["rupee", "forex", "fx ", "dollar", "usd/inr", "remittance", "fcnr", "nri deposit", "ecb norms"],
    "IT": ["it services", "software", "h-1b", "h1b", "visa", "outsourcing", "infosys", "tcs", "wipro", "hcltech",
           "tech mahindra", "gcc "],
    "Auto": ["auto ", "automobile", "vehicle", " ev ", "electric vehicle", "two-wheeler", "passenger vehicle", "pm e-drive",
             "scrappage", "maruti", "tata motors", "mahindra", "bajaj auto", "hero moto"],
    "Pharma": ["pharma", "drug", " api ", "usfda", "generic", "hospital", "medical device", "biosimilar", "cdsco"],
    "Oil & gas": ["crude", "brent", "opec", "oil ", "lng", "refiner", "omc", "petrol", "diesel", "windfall", "hormuz",
                  "ongc", "gas price", "atf"],
    "Power & renewables": ["power", "electricity", "transmission", "grid", "solar", "wind", "renewable", "coal", "discom",
                           "green energy", "hydrogen", "battery storage", "almm", "nuclear"],
    "Metals & mining": ["steel", "aluminium", "copper", "iron ore", "mining", "metal", "safeguard duty", "anti-dumping",
                        "coking coal", "critical mineral"],
    "Cement & infra": ["cement", "highway", "nhai", "infrastructure", "construction", "capex", "gati shakti", "smart city"],
    "Realty": ["real estate", "realty", "housing", "rera", "home loan", "affordable housing"],
    "Defence": ["defence", "defense", " dac ", "drdo", "procurement", "indigenous", "navy", "air force", "army", "missile"],
    "Railways": ["railway", "rail ", "vande bharat", "metro", "kavach", "rvnl", "irfc"],
    "FMCG & agri": ["fmcg", "msp", "kharif", "rabi", "monsoon", "edible oil", "wheat", "rice", "fertiliser", "fertilizer",
                    "urea", "agri", "farm", "food inflation", "pulses", "milk", "cotton", "tea ", "coffee"],
    "Sugar & agri": ["sugar", "ethanol", "sugarcane", "molasses", "distiller"],
    "Telecom": ["telecom", "spectrum", "5g", "agr", "trai", "satcom", "starlink", "jio", "airtel", "vodafone"],
    "Capital markets": ["sebi", "f&o", "derivative", "stt", "ltcg", "capital gains", "broker", "exchange", "mutual fund",
                        "ipo", "fpi", "fii", "weekly expiry", "demat"],
    "Textiles & exports": ["textile", "apparel", "garment", "gems", "jewellery", "export", "fta", "trade deal",
                          "trade pact", "tariff", "shrimp", "leather", "dgft"],
    "Chemicals": ["chemical", "agrochem", "petrochem", "fluorine"],
    "Aviation & travel": ["aviation", "airline", "airport", "tourism", "hotel", "indigo", "air india"],
    "Electronics & PLI": ["electronics", "semiconductor", "pli", "mobile", "smartphone", "appliance", "component",
                          "data centre", "data center", "ai mission"],
    "Insurance": ["insurance", "irdai", "lic ", "insurer"],
    "Macro": ["cpi", "inflation", "gdp", "iip", "fiscal", "budget", "bond yield", "fomc", "fed ", "gst", "growth forecast",
              "current account", "trade deficit", "cabinet"],
}
_POS = ("approve", "approves", "approved", "clears", "cleared", "incentive", "boost", "relief", "waive", "exempt",
        "subsidy", "extends", "allows", "eases", "liberalis", "record high", "surge", "0% tariff", "zero tariff",
        "hike msp", "raises msp", "cut duty", "duty cut", "rate cut", "package", "scheme", "allocat", "green light")
_NEG = ("ban", "prohibit", "curb", "restrict", "hike duty", "duty hike", "levy", "penalty", "probe", "cap on",
        "windfall", "tariff on", "sanction", "crackdown", "shortage", "slump", "default", "downgrade", "rate hike",
        "tighten", "blockade", "strike")


def _tag_sectors(title: str) -> list[str]:
    t = f" {title.lower()} "
    return [sec for sec, kws in SECTOR_KW.items() if any(k in t for k in kws)]


def _tone_pm(title: str) -> str:
    t = title.lower()
    pos = any(k in t for k in _POS)
    neg = any(k in t for k in _NEG)
    return "±" if pos and neg else "+" if pos else "−" if neg else "·"


def fetch_rss(url: str, timeout: int = 20) -> list[dict]:
    """[{date, title, url}] newest-first from an RSS feed; [] on any failure."""
    import requests
    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=timeout)
        root = ET.fromstring(r.content)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for it in root.findall(".//item"):
        title = (it.findtext("title") or "").strip()
        if not title or not title.isascii():          # English only (PIB mixes Hindi)
            continue
        pd = it.findtext("pubDate") or ""
        try:
            dt = parsedate_to_datetime(pd).astimezone(IST).date().isoformat()
        except Exception:  # noqa: BLE001
            dt = ""
        out.append({"date": dt, "title": _scrub(title), "url": (it.findtext("link") or "").strip()})
    return out


def fetch_sector_news(today: date) -> list[dict]:
    items, seen = [], set()
    for src, url in RSS_FEEDS.items():
        for it in fetch_rss(url):
            secs = _tag_sectors(it["title"])
            key = it["title"].lower()[:80]
            if not secs or key in seen:
                continue
            seen.add(key)
            items.append({**it, "source": src, "sectors": secs, "tone": _tone_pm(it["title"])})
    # PIB (govt wire) in English via GDELT — the PIB RSS is Hindi-only now
    for a in gdelt("domain:pib.gov.in sourcelang:english", "3d", 12):
        key = a["title"].lower()[:80]
        secs = _tag_sectors(a["title"])
        if secs and key not in seen:
            seen.add(key)
            items.append({"date": a["date"], "title": a["title"], "url": a["url"], "source": "PIB",
                          "sectors": secs, "tone": _tone_pm(a["title"])})
    items.sort(key=lambda x: x["date"], reverse=True)
    return items[:80]


def fetch_rbi_releases() -> dict:
    out = {}
    for key, url in RBI_FEEDS.items():
        rows = fetch_rss(url)[:10]
        if rows:
            out[key] = rows
    return out


# ---- NSE (cookie-bootstrapped session; may be geo/bot-blocked from some hosts) ----------
def _nse_session():
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": _UA, "Accept": "application/json, text/plain, */*",
                      "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/"})
    s.get("https://www.nseindia.com/", timeout=15)
    return s


def _nse_json(s, url, referer, params=None):
    s.get(referer, timeout=15)
    r = s.get(url, params=params, headers={"Referer": referer}, timeout=25)
    return r.json()


def fetch_nse_holidays(s) -> list[str]:
    j = _nse_json(s, "https://www.nseindia.com/api/holiday-master?type=trading",
                  "https://www.nseindia.com/resources/exchange-communication-holidays")
    out = []
    for x in j.get("FO", []) or j.get("CM", []):
        try:
            out.append(datetime.strptime(x["tradingDate"], "%d-%b-%Y").date().isoformat())
        except (KeyError, ValueError):
            pass
    return sorted(set(out))


def fetch_index_members() -> dict:
    """NIFTY 50 / Next 50 / Bank Nifty constituents from the NSE archives CSVs (no cookie needed)."""
    import requests
    out = {}
    for key, fn in (("NIFTY50", "ind_nifty50list"), ("NIFTYNEXT50", "ind_niftynext50list"),
                    ("BANK", "ind_niftybanklist")):
        try:
            r = requests.get(f"https://archives.nseindia.com/content/indices/{fn}.csv",
                             headers={"User-Agent": _UA, "Referer": "https://www.nseindia.com/"}, timeout=20)
            rows = list(csv.DictReader(io.StringIO(r.text)))
            mem = [{"symbol": x["Symbol"], "company": x["Company Name"], "industry": x["Industry"]}
                   for x in rows if x.get("Symbol")]
            if mem:
                out[key] = mem
        except Exception:  # noqa: BLE001
            pass
    return out


def fetch_earnings(s, members: dict, today: date, horizon_days: int = 45) -> dict:
    """Board meetings for financial results (NSE event calendar) for index members, plus which
    members filed results in the last 10 days (NSE corporate announcements)."""
    rank = {"NIFTY50": 0, "BANK": 1, "NIFTYNEXT50": 2}
    who = {}
    for idx in sorted(members, key=lambda k: rank.get(k, 9)):
        for m in members[idx]:
            who.setdefault(m["symbol"], {**m, "index": idx})     # NIFTY50 wins over NEXT50/BANK
    f, t = today.strftime("%d-%m-%Y"), (today + timedelta(days=horizon_days)).strftime("%d-%m-%Y")
    cal = _nse_json(s, "https://www.nseindia.com/api/event-calendar",
                    "https://www.nseindia.com/companies-listing/corporate-filings-event-calendar",
                    {"index": "equities", "from_date": f, "to_date": t})
    upcoming, load = [], {}
    for x in cal:
        if "result" not in (x.get("purpose", "") + x.get("bm_desc", "")).lower():
            continue
        try:
            d = datetime.strptime(x["date"], "%d-%b-%Y").date()
        except (KeyError, ValueError):
            continue
        if d < today:
            continue
        load[d.isoformat()] = load.get(d.isoformat(), 0) + 1
        m = who.get(x.get("symbol"))
        if m:
            upcoming.append({"date": d.isoformat(), "symbol": m["symbol"], "company": m["company"],
                             "industry": m["industry"], "index": m["index"]})
    upcoming.sort(key=lambda e: (e["date"], rank.get(e["index"], 9), e["symbol"]))

    reported = {}
    try:
        ann = _nse_json(s, "https://www.nseindia.com/api/corporate-announcements",
                        "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
                        {"index": "equities", "from_date": (today - timedelta(days=10)).strftime("%d-%m-%Y"),
                         "to_date": today.strftime("%d-%m-%Y")})
        for x in ann:
            m = who.get(x.get("symbol"))
            blob = (x.get("desc", "") + " " + (x.get("attchmntText") or "")).lower()
            if not m or "financial result" not in blob:
                continue
            try:
                d = datetime.strptime(x["an_dt"][:11], "%d-%b-%Y").date().isoformat()
            except (KeyError, ValueError):
                continue
            if m["symbol"] not in reported or d > reported[m["symbol"]]["date"]:
                reported[m["symbol"]] = {"date": d, "symbol": m["symbol"], "company": m["company"],
                                         "index": m["index"], "pdf": x.get("attchmntFile", "")}
    except Exception:  # noqa: BLE001
        pass
    return {"fetched": today.isoformat(), "upcoming": upcoming, "load": load,
            "reported": sorted(reported.values(), key=lambda r: r["date"], reverse=True)}


# ---- layer 3: free data ------------------------------------------------------------
def free_refresh(data: dict, today: date) -> dict:
    """Free layer (no key). Each block is best-effort and keeps the prior snapshot on failure."""
    got = {}

    # -- NSE: holidays, members, earnings (one bootstrapped session) --
    try:
        s = _nse_session()
        try:
            hol = fetch_nse_holidays(s)
            if hol:
                data["nse_holidays"] = hol
                got["holidays"] = len(hol)
        except Exception as e:  # noqa: BLE001
            print(f"  nse holidays: {e}")
        mem = fetch_index_members()
        if mem:
            data["index_members"] = {"fetched": today.isoformat(), **mem}
            got["members"] = sum(len(v) for v in mem.values())
        members = {k: v for k, v in (data.get("index_members") or {}).items() if isinstance(v, list)}
        if members:
            try:
                earn = fetch_earnings(s, members, today)
                prev = data.get("earnings") or {}
                if earn["upcoming"] or earn["reported"]:
                    data["earnings"] = {"season": prev.get("season", ""), **earn}
                    got["earnings"] = len(earn["upcoming"])
            except Exception as e:  # noqa: BLE001
                print(f"  nse earnings: {e}")
    except Exception as e:  # noqa: BLE001
        print(f"  nse session: {e}")

    # -- RSS: sector-tagged policy headlines + RBI wire --
    news = fetch_sector_news(today)
    if news:
        data["sector_news"] = {"fetched": today.isoformat(), "items": news}
        got["sector_news"] = len(news)
    rbi = fetch_rbi_releases()
    if rbi:
        data["rbi_releases"] = {"fetched": today.isoformat(), **rbi}
        got["rbi"] = sum(len(v) for v in rbi.values())

    # -- GDELT geopolitical pools (+ keyword-tone signal on the peace model) --
    iran = gdelt("Iran ceasefire sourcelang:english", "3d", 6, must_match=("iran", "tehran", "israel", "hormuz"))
    india = gdelt("India United States trade deal sourcelang:english", "7d", 6, must_match=("india", "tariff"))
    if iran or india:  # don't clobber prior headlines if the fetch failed
        data["headlines"] = {"iran": iran, "india_us": india, "fetched": today.isoformat()}
        got["geo_headlines"] = len(iran) + len(india)
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

    # -- yfinance quotes: crude is the Iran→NIFTY channel; INR/VIX/DXY/UST for context --
    market = {}
    try:
        import yfinance as yf
        for key, tk in (("brent", "BZ=F"), ("usdinr", "INR=X"), ("indiavix", "^INDIAVIX"),
                        ("dxy", "DX-Y.NYB"), ("us10y", "^TNX")):
            try:
                h = yf.Ticker(tk).history(period="5d")
                if len(h):
                    last = float(h["Close"].iloc[-1])
                    prev = float(h["Close"].iloc[-2]) if len(h) > 1 else last
                    market[key] = {"last": round(last, 2), "chg_pct": round((last / prev - 1) * 100, 2)}
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    if market:
        market["fetched"] = today.isoformat()
        data["market"] = market
        got["market"] = len(market) - 1

    if got:
        data["last_updated"] = today.isoformat()
        if data.get("updated_by") != "daily-refresh":  # preserve the LLM stamp if it ran
            data["updated_by"] = "auto-refresh"
    return got


# ---- layer 1: deterministic maintenance -------------------------------------------
def maintain(data: dict, today: date) -> bool:
    """Prune stale signals, past events, expired pending items, old headlines. True if changed."""
    changed = False

    pm = data.get("peace_model", {})
    sigs = pm.get("signals", [])
    kept = [s for s in sigs
            if "date" not in s or (today - date.fromisoformat(s["date"])).days <= SIGNAL_TTL_DAYS]
    if len(kept) != len(sigs):
        pm["signals"] = kept
        changed = True

    cal = data.get("calendar", [])
    fresh = [e for e in cal if (date.fromisoformat(e["date"]) - today).days >= -KEEP_PAST_DAYS]
    if len(fresh) != len(cal):
        data["calendar"] = fresh
        changed = True

    pend = data.get("pending", [])
    live = [p for p in pend if not p.get("expires") or date.fromisoformat(p["expires"]) >= today]
    if len(live) != len(pend):
        data["pending"] = live
        changed = True

    sn = data.get("sector_news") or {}
    items = sn.get("items", [])
    kept_n = [i for i in items if not i.get("date") or (today - date.fromisoformat(i["date"])).days <= NEWS_TTL_DAYS]
    if len(kept_n) != len(items):
        sn["items"] = kept_n
        changed = True

    earn = data.get("earnings") or {}
    up = earn.get("upcoming", [])
    kept_e = [e for e in up if date.fromisoformat(e["date"]) >= today]
    if len(kept_e) != len(up):
        earn["upcoming"] = kept_e
        changed = True
    return changed


# ---- layer 2: LLM news refresh (only with ANTHROPIC_API_KEY) -----------------------
def llm_refresh(data: dict, today: date) -> dict | None:
    """Ask Claude (web search) to rewrite the hand-maintained facts per MACRO_REFRESH.md. Returns the
    new dict (auto-filled containers merged back), or None on any failure."""
    try:
        import anthropic
    except ImportError:
        print("  anthropic package not installed — skipping news refresh")
        return None

    try:
        playbook = open("MACRO_REFRESH.md").read()
    except OSError:
        playbook = "(playbook missing — update facts only, keep the JSON schema intact)"

    hand = {k: v for k, v in data.items() if k not in AUTO_KEYS}
    prompt = (
        f"You are the daily macro-refresh agent for the NIFTY Teller app. Today (IST) is "
        f"{today.isoformat()}. Follow this playbook exactly:\n\n=== MACRO_REFRESH.md ===\n{playbook}\n\n"
        f"=== current macro_events.json (hand-maintained part; auto-filled keys withheld) ===\n"
        f"{json.dumps(hand, indent=2, ensure_ascii=False)}\n\n"
        "Using web search, research the CURRENT status of everything that moves NIFTY in the next month: "
        "(1) dynamic[] trackers — India-US trade deal, Iran-US war (if a ceasefire collapsed, set "
        "peace_model.phase_start to the new phase-start date and note it in phase_note); (2) pending[] — "
        "undated catalysts that can land any day in the next ~30 days: rupee/RBI measures, RBI liquidity or "
        "off-cycle action, cabinet/CCEA sector schemes, GST Council, SEBI board decisions, US tariff actions, "
        "trade-deal/FTA milestones, OPEC+, Parliament session, rating actions, big sector policy (sugar/ethanol, "
        "EV, telecom, PLI, defence DAC, pharma) — update status/p_30d/direction/watch, add new ones, drop dead "
        "ones; (3) sector_policy[] — the government/regulator stance per sector with a dated driver; (4) rates{} "
        "— policy rates, last moves, latest India CPI. FIRST check schedule{} for anything that LANDED since the "
        "last refresh (any RBI MPC / FOMC / India CPI / US CPI date <= today — a US print lands overnight IST, so "
        "the day after its US date it is already out): record the OUTCOME (decision, vote split, guidance, the "
        "print's value) in rates{} and replace any 'priced'/'expected' wording with what actually happened; "
        "(5) calendar[] — newly announced dated events (elections, "
        "budget, off-cycle meetings, FTA signings, MSCI dates) and schedule{} corrections if an official "
        "calendar changed. Append dovish/hawkish Iran headlines to peace_model.signals[] using the documented "
        "schema.\n\n"
        "Then output the COMPLETE updated JSON for the hand-maintained part (same keys as given). Rules: facts "
        "only, no fabrication; keep the exact same schema/keys/types; NO personal names of officials "
        "(institutions/roles only — also inside status/driver/watch text); ISO dates; set last_updated to "
        f'"{today.isoformat()}" and updated_by to "daily-refresh". Output ONLY the JSON object — no prose, '
        "no markdown fences."
    )

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 12}]

    resp = None
    for _ in range(6):  # bound server-tool pause_turn resumes
        resp = client.messages.create(model=MODEL, max_tokens=24000, tools=tools, messages=messages)
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
        fresh = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        print(f"  news refresh: invalid JSON from model ({e}) — keeping deterministic result")
        return None
    for k in AUTO_KEYS:                      # the model never sees/overwrites the auto layer
        if k in data:
            fresh[k] = data[k]
    return fresh


# ---- validation gate ---------------------------------------------------------------
def valid(data: dict, today: date) -> bool:
    """Same gate MACRO_REFRESH.md requires: schema intact + the models run clean."""
    for key in ("calendar", "dynamic", "peace_model", "pending", "sector_policy", "schedule"):
        if key not in data:
            print(f"  validation FAILED: missing '{key}'")
            return False
    try:
        import macro_events as m
        m.build_calendar(today, data, 150)
        pm = m.peace_model(today, data["peace_model"])
        m.build_pending(today, data, pm)
        m.earnings_window(today, data, 30)
        m.sector_news(today, data)
        m.alerts(today, data)
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

    got = free_refresh(data, today)
    print("free layer:", ", ".join(f"{k}={v}" for k, v in got.items()) or "nothing fetched")

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
