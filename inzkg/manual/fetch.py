"""Fetch a snapshot of the public Operational Manual directly over HTTPS.

The manual's viewer loads two files: ``toc.json?node=NaN`` (the contents, with every
instruction and its dated earlier versions) and ``search.json`` (the text of every page).
This module downloads both and writes the same snapshot format as
``scripts/browser_export.js``. Use whichever works from your network.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
from pathlib import Path

import httpx

from .parse import MANUAL_BASE_URL, OUT_OF_SCOPE_CHAPTERS

USER_AGENT = "inzkg-manual-loader/0.1"


def fetch_snapshot(out_dir: str | Path, base_url: str = MANUAL_BASE_URL, timeout: float = 120.0) -> Path:
    with httpx.Client(base_url=base_url, timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        toc = client.get("toc.json", params={"node": "NaN"}).raise_for_status().json()
        search = client.get("search.json").raise_for_status().json()
        home = client.get("").text
    excluded: set[str] = set()

    def walk(nodes, top):
        for n in nodes:
            chapter = top if top is not None else n["text"]
            if chapter in OUT_OF_SCOPE_CHAPTERS:
                excluded.add(n["url"])
            walk(n.get("children") or [], chapter)

    walk(toc, None)
    pages = [p for p in search["pages"] if p["url"] not in excluded]
    issue = None
    marker = "Issue Date: "
    if marker in home:
        issue = home.split(marker, 1)[1].split("<", 1)[0].strip()[:20]
    now = dt.datetime.now(dt.timezone.utc)
    out = Path(out_dir) / f"opsmanual-{now.date().isoformat()}.json.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as fh:
        json.dump({"retrieved": now.isoformat(), "source": base_url, "issueDate": issue, "toc": toc, "pages": pages}, fh)
    return out
