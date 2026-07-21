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

import collections
import difflib
import json
import pathlib
import re
import sys
import time
import unicodedata
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
# Publishers deposit this name eight different ways ("Will Cornwell",
# "W. K. Cornwell", "William K Cornwell", ...). Pin one form so the owner of the
# list reads consistently on every entry, whatever the publisher sent.
ME_DISPLAY_NAME = "William K. Cornwell"

# Crossref DOI filters are OR'd; 20 keeps the URL comfortably short.
BATCH_SIZE = 20

# ORCID's DOI-field validator rejects legacy ESA-style DOIs containing
# "()[]:;", so these can never be entered there directly. Patched in here by
# exact title match instead; the existing Crossref enrichment step then fills
# in authors/journal for them normally, same as any ORCID-sourced DOI.
DOI_PATCHES = {
    "A trait-based test for habitat filtering: Convex hull volume":
        "10.1890/0012-9658(2006)87[1465:attfhf]2.0.co;2",
}

# ORCID records the preprint and the published paper as two separate works, and
# publishers deposit corrigenda under their own DOIs. Both show up as duplicate
# entries on the page. These are matched mechanically rather than cleaned up in
# ORCID because new preprints keep arriving with every paper.
PREPRINT_DOI_PREFIXES = (
    "10.1101/",     # bioRxiv / medRxiv
    "10.32942/",    # EcoEvoRxiv
    "10.31220/",    # agriRxiv
    "10.31223/",    # ESSOAr / EarthArXiv
    "10.22541/",    # Authorea
)

# Titles starting with these are publisher corrections, not distinct works.
CORRECTION_PREFIXES = (
    "correction:", "corrigendum:", "erratum:", "author correction:",
    "publisher correction:", "retraction:",
)

# Titles at or above this difflib ratio (after normalisation) are treated as the
# same work. 0.85 separates the real duplicate pairs from the closest distinct
# ones in this corpus, which sit near 0.7.
DUPLICATE_RATIO = 0.85

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "publications.json"


# Crossref returns JATS markup inside titles. <i>/<sub>/<sup> carry meaning
# (species names, CO<sub>2</sub>) and are kept, normalised to HTML; <scp> and
# friends are publisher typography only, so the tags go and the text stays.
# Anything outside this set is dropped, which is what makes the result safe to
# render with `safeHTML` in the template.
KEEP_TAGS = {"i": "em", "em": "em", "sub": "sub", "sup": "sup"}
TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)[^>]*>")


def clean_title(title):
    def repl(m):
        closing, name = m.group(1), m.group(2).lower()
        tag = KEEP_TAGS.get(name)
        if not tag:
            return ""
        return f"</{tag}>" if closing else f"<{tag}>"

    # Crossref sometimes pretty-prints titles across lines; collapse that too.
    text = re.sub(r"\s+", " ", TAG_RE.sub(repl, title or "")).strip()
    return unshout(text)


# Some legacy records are deposited in full caps. Only titles above this ratio
# are rewritten; in this corpus the shouting ones sit at 1.0 and the next
# highest is 0.15, so the threshold has a wide margin and leaves ordinary
# Title Case (and embedded acronyms like CO2 or TRY) untouched.
SHOUTING_RATIO = 0.8


def unshout(title):
    letters = [c for c in TAG_RE.sub("", title) if c.isalpha()]
    if not letters:
        return title
    if sum(c.isupper() for c in letters) / len(letters) < SHOUTING_RATIO:
        return title

    out, capitalise = [], True
    for ch in title.lower():
        if capitalise and ch.isalpha():
            out.append(ch.upper())
            capitalise = False
        else:
            out.append(ch)
            if ch in ":.?!":
                capitalise = True
    return "".join(out)


def normalise_title(title):
    """Fold a title down to comparable text: no markup, accents, or punctuation.

    ORCID and Crossref disagree about hyphens (U+2010 vs "-"), curly vs straight
    apostrophes, and JATS markup like <scp>, all of which otherwise defeat an
    exact match on titles that are word-for-word identical.
    """
    text = unicodedata.normalize("NFKD", title or "")
    text = re.sub(r"<[^>]+>", " ", text).lower()
    text = text.replace("’", "'").replace("‘", "'")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_preprint(work):
    doi = (work.get("doi") or "").lower()
    return doi.startswith(PREPRINT_DOI_PREFIXES)


def keep_rank(work):
    """Sort key picking the better of two records for the same work.

    Prefers the published version over the preprint, and a record with a DOI
    over one without (a DOI-less ORCID entry carries no authors, since author
    lists come from Crossref and are keyed on DOI). Higher sorts first.
    """
    return (
        0 if is_preprint(work) else 1,
        1 if work.get("doi") else 0,
        1 if work.get("journal") else 0,
        work.get("year") or 0,
    )


def dedupe(works):
    """Drop corrigenda and near-identical duplicate titles.

    Runs before Crossref enrichment so the dropped records cost no requests.
    """
    kept = []
    for w in works:
        if normalise_title(w["title"]).startswith(
            tuple(normalise_title(p) for p in CORRECTION_PREFIXES)
        ):
            print(f"  dropping correction notice: {w['title'][:60]}", file=sys.stderr)
            continue
        kept.append(w)

    # Compare every remaining pair; keep the better record of each match.
    result = []
    for w in sorted(kept, key=keep_rank, reverse=True):
        nw = normalise_title(w["title"])
        match = next(
            (
                r for r in result
                if difflib.SequenceMatcher(None, nw, normalise_title(r["title"])).ratio()
                >= DUPLICATE_RATIO
            ),
            None,
        )
        if match:
            why = "preprint of" if is_preprint(w) else "duplicate of"
            print(f"  dropping {why} '{match['title'][:45]}': {w.get('doi')}", file=sys.stderr)
            continue
        result.append(w)
    return result


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
            "title": clean_title(
                (s.get("title") or {}).get("title", {}).get("value", "")
            ),
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


# Surname particles stay lowercase when they follow a given name, per the usual
# convention ("van der Berg"), but not when they lead the name.
NAME_PARTICLES = {
    "van", "von", "der", "den", "de", "del", "della", "di", "da", "dos", "du",
    "la", "le", "ten", "ter", "y", "bin", "al",
}
# Initials that must stay uppercase rather than being title-cased into "R.m.".
# Requiring a dot (or a lone letter) is what keeps short surnames -- LUSK, WOOD,
# BELL -- from being mistaken for initials and left shouting.
INITIALS_RE = re.compile(r"(?:[A-Za-z]\.)+|[A-Za-z]")


def fix_name_case(name):
    """Rewrite a SHOUTED author name as "R. M. Kooyman".

    Some publishers deposit author names in full caps. Only names that are
    overwhelmingly uppercase are touched, so correctly-cased names -- including
    genuine all-caps consortium names -- pass through untouched.
    """
    letters = [c for c in name if c.isalpha()]
    if len(letters) < 2:
        return name
    if sum(c.isupper() for c in letters) / len(letters) < SHOUTING_RATIO:
        return name

    tokens = []
    for i, token in enumerate(name.split()):
        # Initials keep their capitals; "W.K." must not become "W.k.".
        if INITIALS_RE.fullmatch(token):
            tokens.append(token.upper())
            continue
        low = token.lower()
        if i > 0 and low.strip(".") in NAME_PARTICLES:
            tokens.append(low)
            continue
        # Capitalise the first letter and any letter after an apostrophe or
        # hyphen, so O'CONNOR -> O'Connor and SMITH-JONES -> Smith-Jones.
        cased = re.sub(
            r"(^|[-'’])([a-z])",
            lambda m: m.group(1) + m.group(2).upper(),
            low,
        )
        # "MCDONALD" -> "McDonald", not "Mcdonald".
        cased = re.sub(r"^(Mc|Mac)([a-z])", lambda m: m.group(1) + m.group(2).upper(), cased)
        tokens.append(cased)
    return " ".join(tokens)


def tokens_compatible(a, b):
    """Could these two given-name tokens refer to the same person?

    True when they are equal, when one is an initial of the other ("W." vs
    "William"), or when one is a prefix of the other ("Will" vs "William").
    "John" and "Jane" are not compatible, which is what stops two different
    people who share a surname and first initial from being merged.
    """
    # Fold away hyphens and dots so "Wei-Wei" and "Weiwei" compare equal.
    a, b = (re.sub(r"[^a-z]", "", x.lower()) for x in (a, b))
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) == 1 or len(b) == 1:
        return a[0] == b[0]
    # Require a decent shared prefix: "Will"/"William" is a real abbreviation,
    # but "Ji"/"Jingming" is more likely two different people.
    shorter = min(len(a), len(b))
    return shorter >= 3 and (a.startswith(b) or b.startswith(a))


def names_compatible(a, b):
    """Same surname, and no conflict in any given name they both specify."""
    pa, pb = a.split(), b.split()
    if len(pa) < 2 or len(pb) < 2 or pa[-1].lower() != pb[-1].lower():
        return False
    ga, gb = pa[:-1], pb[:-1]
    # A missing middle name is fine; a contradictory one is not.
    return all(tokens_compatible(x, y) for x, y in zip(ga, gb))


def canonicalise_authors(works):
    """Collapse spellings of the same author to one form across all works.

    Publishers deposit the same person as "W. K. Cornwell", "Will Cornwell" and
    "William K. Cornwell". Left alone these read as different people and inflate
    any distinct-co-author count. Within each (surname, first initial) bucket,
    mutually compatible spellings are merged onto the fullest, most frequently
    deposited form.
    """
    counts = collections.Counter(
        a["name"] for w in works for a in w.get("authors", [])
    )
    buckets = collections.defaultdict(list)
    for name in counts:
        parts = name.split()
        if len(parts) < 2:
            continue
        buckets[(parts[-1].lower(), parts[0][0].lower())].append(name)

    mapping = {}
    for names in buckets.values():
        # Fullest spelling first. A spelled-out given name beats an initial, so
        # "Agata Buchwal" wins over "A. Buchwal"; only then do token count and
        # deposit frequency break ties, with the name itself last so that runs
        # are reproducible.
        def fullness(n):
            given = n.split()[:-1]
            spelled = sum(1 for t in given if len(t.strip(".")) > 1)
            return (-spelled, -len(given), -counts[n], -len(n), n)

        ordered = sorted(names, key=fullness)
        clusters = []
        for name in ordered:
            for canonical, members in clusters:
                if names_compatible(canonical, name):
                    members.append(name)
                    break
            else:
                clusters.append((name, [name]))
        for canonical, members in clusters:
            for name in members:
                mapping[name] = canonical

    changed = 0
    for w in works:
        for a in w.get("authors", []):
            new = mapping.get(a["name"], a["name"])
            if new != a["name"]:
                a["name"] = new
                changed += 1
    return changed


def format_authors(item):
    authors = []
    for a in item.get("author", []):
        if a.get("name"):  # consortium/group author
            # Left as deposited: these are often legitimately all-caps acronyms
            # (TRY, NEON), which the personal-name caser would ruin.
            name, family, given = a["name"].strip(), "", ""
        else:
            family = (a.get("family") or "").strip()
            given = (a.get("given") or "").strip()
            name = fix_name_case(f"{given} {family}".strip())
        # Collapses doubled spaces and the NBSP some publishers deposit.
        name = re.sub(r"\s+", " ", name).strip()
        if not name:
            continue
        is_me = (
            family.lower() == ME_FAMILY
            and given.lower().startswith(ME_GIVEN_INITIAL)
        )
        if is_me:
            name = ME_DISPLAY_NAME
        authors.append({"name": name, "me": is_me})
    return authors


def main():
    print("Fetching ORCID works...", file=sys.stderr)
    works = fetch_orcid()
    print(f"  {len(works)} works", file=sys.stderr)

    for w in works:
        patch_doi = DOI_PATCHES.get(w["title"])
        if patch_doi and not w["doi"]:
            w["doi"] = patch_doi

    print("Removing duplicates and correction notices...", file=sys.stderr)
    works = dedupe(works)
    print(f"  {len(works)} works remain", file=sys.stderr)

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
        title = clean_title((item.get("title") or [""])[0])
        if title:
            w["title"] = title
        if not w["year"]:
            parts = item.get("issued", {}).get("date-parts", [[None]])[0]
            if parts and parts[0]:
                w["year"] = int(parts[0])

    # Needs every work's authors in hand, so it runs after enrichment.
    merged = canonicalise_authors(works)
    print(f"Normalised {merged} author name spellings", file=sys.stderr)

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
