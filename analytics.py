"""analytics.py — optional Google Analytics 4 tracking for the dashboard.

Opt-in and safe: set a GA4 Measurement ID and the gtag snippet is injected into
Streamlit's real page <head> (so GA counts true page views, not an iframe).
If no ID is set this is a complete no-op — the dashboard is unaffected.

Set the ID (first one found wins), no code change needed:
  - Streamlit secrets:  add  ga_measurement_id = "G-XXXXXXXXXX"  to the app's
    Secrets (Manage app -> Settings -> Secrets on Streamlit Cloud). Recommended.
  - Environment var:    GA_MEASUREMENT_ID=G-XXXXXXXXXX

The ID (G-XXXXXXXXXX) is not a secret — it's exposed to the browser anyway — but
using st.secrets keeps it out of the repo and lets you change it without a redeploy.
"""
from __future__ import annotations

import os
import pathlib

import streamlit as st

_MARKER = "<!-- ga4-injected -->"


def _measurement_id() -> str | None:
    """Resolve the GA4 Measurement ID from st.secrets, then env. None if unset."""
    mid = None
    try:
        mid = st.secrets.get("ga_measurement_id")  # raises if no secrets configured
    except Exception:  # noqa: BLE001
        mid = None
    mid = (mid or os.environ.get("GA_MEASUREMENT_ID") or "").strip()
    return mid or None


def inject_ga() -> str | None:
    """Patch Streamlit's index.html <head> with the GA4 gtag, once per instance.

    Returns the active Measurement ID, or None if tracking is off / on failure.
    Never raises — analytics must not be able to break the dashboard.
    """
    mid = _measurement_id()
    if not mid or not mid.startswith("G-"):
        return None
    try:
        index = pathlib.Path(st.__file__).parent / "static" / "index.html"
        html = index.read_text()
        if _MARKER in html:
            return mid  # already patched this container
        snippet = (
            f'<script async src="https://www.googletagmanager.com/gtag/js?id={mid}"></script>'
            "<script>window.dataLayer=window.dataLayer||[];"
            "function gtag(){dataLayer.push(arguments);}"
            "gtag('js',new Date());"
            f"gtag('config','{mid}');</script>{_MARKER}"
        )
        html = html.replace("<head>", "<head>" + snippet, 1)
        index.write_text(html)
        return mid
    except Exception:  # noqa: BLE001
        return None
