#!/usr/bin/env python3
"""Build data/publications.json from ORCID, enriched with Crossref metadata.

ORCID is the authoritative list of works. Crossref is only used to fill in
author lists, which ORCID's work summaries omit -- so a paper missing from
Crossref still shows up, just without authors.

Crossref's own `filter=orcid:` search is NOT a usable source here: it returns
only the works where a publisher happened to deposit the ORCID iD (68 of 176
at time of writing).

Run with no arguments:  python3 scripts/fetch_publications.py
"""

import json
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ORCID_ID = "0000-0003-4080-4073"
# Crossref's "polite pool" gives more reliable service to identified callers.
CONTACT = "w.cornwell@unsw.edu.au"
USER_AGENT = f"willcornwell.org publication list (mailto:{CONTACT})"

# Which author to bold on the rendered page.
ME_FAMILY = "cornwell"
ME_GIVEN_INITIAL = "w"

# Crossref DOI filters are OR'd; 20 keeps the URL comfortably short.
BATCH_SIZE = 20

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "publications.json"


def get_json(url, headers=None, retries=3):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == retries - 1:
                raise
            wait = 2 ** attempt
            print(f"  retry in {wait}s after {exc}", file=sys.stderr)
            time.sleep(wait)


def best_summary(group):
    """Pick the richest of ORCID's duplicate summaries for one work.

    A work claimed by several sources gets one summary per source; they vary in
    completeness, so prefer whichever carries the most metadata we actually use.
    """
    def score(s):
        return (
            bool((s.get("journal-title") or {}).get("value")),
            bool((s.get("publication-date") or {})),
            bool(s.get("url")),
        )

    return max(group["work-summary"], key=lambda s: sum(score(s)))


def fetch_orcid():
    url = f"https://pub.orcid.org/v3.0/{ORCID_ID}/works"
    data = get_json(url, headers={"Accept": "application/json"})

    works = []
    for group in data["group"]:
        s = best_summary(group)

        doi = None
        for ext in group.get("external-ids", {}).get("external-id", []):
            if ext.get("external-id-type") == "doi" and ext.get("external-id-value"):
                doi = ext["external-id-value"].strip().lower()
                break

        year = ((s.get("publication-date") or {}).get("year") or {}).get("value")
        url_val = (s.get("url") or {}).get("value")

        works.append({
            "title": (s.get("title") or {}).get("title", {}).get("value", "").strip(),
            "year": int(year) if year and year.isdigit() else None,
            "journal": ((s.get("journal-title") or {}).get("value") or "").strip(),
            "type": (s.get("type") or "").lower(),
            "doi": doi,
            "url": url_val,
            "authors": [],
            "me_index": -1,
        })
    return works


def fetch_crossref(dois):
    """Return {doi: crossref_item} for as many DOIs as Crossref knows about."""
    found = {}
    for i in range(0, len(dois), BATCH_SIZE):
        batch = dois[i:i + BATCH_SIZE]
        params = urllib.parse.urlencode({
            "rows": len(batch),
            "select": "DOI,author,container-title,issued,title",
            "filter": ",".join(f"doi:{d}" for d in batch),
            "mailto": CONTACT,
        })
        print(f"  crossref {i + 1}-{i + len(batch)} of {len(dois)}", file=sys.stderr)
        try:
            msg = get_json(f"https://api.crossref.org/works?{params}")["message"]
        except Exception as exc:  # a bad batch shouldn't lose the whole run
            print(f"  batch failed, continuing without it: {exc}", file=sys.stderr)
            continue
        for item in msg.get("items", []):
            found[item["DOI"].lower()] = item
        time.sleep(0.2)  # be gentle
    return found


def format_authors(item):
    authors = []
    for a in item.get("author", []):
        if a.get("name"):  # consortium/group author
            name, family, given = a["name"].strip(), "", ""
        else:
            family = (a.get("family") or "").strip()
            given = (a.get("given") or "").strip()
            name = f"{given} {family}".strip()
        if not name:
            continue
        is_me = (
            family.lower() == ME_FAMILY
            and given.lower().startswith(ME_GIVEN_INITIAL)
        )
        authors.append({"name": name, "me": is_me})
    return authors


def main():
    print("Fetching ORCID works...", file=sys.stderr)
    works = fetch_orcid()
    print(f"  {len(works)} works", file=sys.stderr)

    dois = [w["doi"] for w in works if w["doi"]]
    print(f"Enriching {len(dois)} DOIs from Crossref...", file=sys.stderr)
    cr = fetch_crossref(dois)
    print(f"  matched {len(cr)}", file=sys.stderr)

    for w in works:
        item = cr.get(w["doi"]) if w["doi"] else None
        if not item:
            continue
        w["authors"] = format_authors(item)
        # Consortium papers run to hundreds of authors, so the template shows a
        # short lead and collapses the rest. Record where to find Will in the
        # full list so his name can still be surfaced when he falls outside it.
        w["me_index"] = next(
            (i for i, a in enumerate(w["authors"]) if a["me"]), -1
        )
        # Crossref's container and title are typically cleaner than ORCID's.
        container = (item.get("container-title") or [""])[0].strip()
        if container:
            w["journal"] = container
        title = (item.get("title") or [""])[0].strip()
        if title:
            w["title"] = title
        if not w["year"]:
            parts = item.get("issued", {}).get("date-parts", [[None]])[0]
            if parts and parts[0]:
                w["year"] = int(parts[0])

    # Newest first; untitled/undated works sink to the bottom.
    works.sort(key=lambda w: (-(w["year"] or 0), w["title"].lower()))

    # Keep the old `generated` date when nothing substantive changed, so the
    # scheduled refresh doesn't produce a no-op commit every single week.
    generated = time.strftime("%Y-%m-%d")
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text())
            if old.get("publications") == works:
                print(f"No change; keeping generated={old.get('generated')}",
                      file=sys.stderr)
                return
        except (json.JSONDecodeError, OSError):
            pass  # unreadable existing file just means we rewrite it

    payload = {
        "generated": generated,
        "orcid": ORCID_ID,
        "count": len(works),
        "with_authors": sum(1 for w in works if w["authors"]),
        "publications": works,
    }

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(works)} works, "
          f"{payload['with_authors']} with authors", file=sys.stderr)


if __name__ == "__main__":
    main()
