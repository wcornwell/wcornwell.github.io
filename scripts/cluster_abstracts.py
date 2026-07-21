#!/usr/bin/env python3
"""Build data/abstract_clusters.json: thematic clusters of the publication list.

Two steps:
  1. Fetch abstracts from Crossref for every work in data/publications.json
     that has a DOI (~90% coverage, including works back to 2001).
  2. Group them into coherent research themes, either by asking Claude (a
     tool call, not free-text parsing) or, with --offline, via TF-IDF +
     k-means -- a lower-quality but zero-dependency, zero-cost fallback that
     needs no Anthropic API access at all. Both paths write the same JSON
     shape, so the site layout doesn't care which one produced it.

This is a manual, occasional script -- unlike fetch_publications.py it is NOT
run by a scheduled workflow. Re-run it by hand whenever the publication list
has moved on enough to be worth reclustering.

The default (LLM) path requires the `anthropic` package and API credentials
(ANTHROPIC_API_KEY, or an `ant auth login` profile).

Run with no arguments:       python3 scripts/cluster_abstracts.py
Run offline (no API needed): python3 scripts/cluster_abstracts.py --offline
"""

import argparse
import json
import pathlib
import re
import sys
import time
import urllib.parse

from fetch_publications import CONTACT, get_json

ROOT = pathlib.Path(__file__).resolve().parent.parent
PUBLICATIONS = ROOT / "data" / "publications.json"
OUT = ROOT / "data" / "abstract_clusters.json"

MODEL = "claude-opus-4-8"
BATCH_SIZE = 20  # Crossref DOI filters are OR'd; keeps the URL comfortably short.

JATS_TAG = re.compile(r"<[^>]+>")


def fetch_abstracts(dois):
    """Return {doi: abstract_text} for every DOI Crossref has an abstract for."""
    found = {}
    for i in range(0, len(dois), BATCH_SIZE):
        batch = dois[i:i + BATCH_SIZE]
        params = urllib.parse.urlencode({
            "rows": len(batch),
            "select": "DOI,abstract",
            "filter": ",".join(f"doi:{d}" for d in batch),
            "mailto": CONTACT,
        })
        print(f"  crossref abstracts {i + 1}-{i + len(batch)} of {len(dois)}", file=sys.stderr)
        try:
            msg = get_json(f"https://api.crossref.org/works?{params}")["message"]
        except Exception as exc:
            print(f"  batch failed, continuing without it: {exc}", file=sys.stderr)
            continue
        for item in msg.get("items", []):
            abstract = item.get("abstract")
            if abstract:
                text = JATS_TAG.sub(" ", abstract)
                text = re.sub(r"\s+", " ", text).strip()
                if text:
                    found[item["DOI"].lower()] = text
        time.sleep(0.2)
    return found


CLUSTER_TOOL = {
    "name": "assign_research_clusters",
    "description": "Group the given academic works into coherent research themes.",
    "input_schema": {
        "type": "object",
        "properties": {
            "clusters": {
                "type": "array",
                "description": "Every work must be assigned to exactly one cluster.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Short theme name, 2-5 words, e.g. 'Plant functional traits'",
                        },
                        "description": {
                            "type": "string",
                            "description": "One or two sentences describing the theme, written for a general scientific audience.",
                        },
                        "dois": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "The DOIs (as given) of every work belonging to this cluster.",
                        },
                    },
                    "required": ["name", "description", "dois"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["clusters"],
        "additionalProperties": False,
    },
    "strict": True,
}

SYSTEM_PROMPT = """You are helping an ecologist understand the shape of their own \
25-year publication record. You will be given titles, years, journals, and (where \
available) abstracts for their academic papers.

Group these works into 6-12 coherent research themes based on their actual scientific \
content -- not by journal, year, or author. Prefer a moderate number of well-separated, \
substantive themes over many overlapping micro-clusters. Base each cluster on genuine \
thematic/methodological similarity in the abstracts and titles, not superficial keyword \
overlap. Every work must end up in exactly one cluster, including works with no abstract \
(cluster those by title alone). Use plain, specific theme names an ecologist would \
recognize (e.g. "Plant functional traits and decomposition", not "Cluster 3" or "Misc")."""


def cluster_offline(works, abstracts, k_range=range(6, 13)):
    """TF-IDF + k-means fallback: no LLM, no API key, no cost.

    Picks k (within k_range) by silhouette score, then names each cluster
    after its top TF-IDF terms -- cruder than an LLM's thematic read, but
    fully local. Returns the same [{"name", "description", "dois"}, ...]
    shape main() expects from the Claude path.
    """
    from sklearn.cluster import KMeans
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics import silhouette_score

    docs = [f"{w['title']} {abstracts.get(w['doi'], '')}" for w in works]
    vectorizer = TfidfVectorizer(stop_words="english", max_df=0.6, min_df=2)
    X = vectorizer.fit_transform(docs)
    terms = vectorizer.get_feature_names_out()

    best_k, best_score, best_labels = None, -1.0, None
    for k in k_range:
        if k >= len(works):
            break
        km = KMeans(n_clusters=k, n_init=10, random_state=0).fit(X)
        score = silhouette_score(X, km.labels_)
        print(f"  k={k}: silhouette={score:.3f}", file=sys.stderr)
        if score > best_score:
            best_k, best_score, best_labels = k, score, km.labels_

    print(f"  picked k={best_k} (silhouette={best_score:.3f})", file=sys.stderr)
    km = KMeans(n_clusters=best_k, n_init=10, random_state=0).fit(X)

    clusters = []
    for cluster_id in range(best_k):
        member_idx = [i for i, label in enumerate(km.labels_) if label == cluster_id]
        centroid = km.cluster_centers_[cluster_id]
        top_terms = [terms[i] for i in centroid.argsort()[::-1][:4]]
        clusters.append({
            "name": " / ".join(t.title() for t in top_terms[:3]),
            "description": (
                "Works whose titles/abstracts share the terms: "
                + ", ".join(top_terms) + "."
            ),
            "dois": [works[i]["doi"] for i in member_idx],
        })
    return clusters


def cluster_with_claude(works, abstracts):
    try:
        import anthropic
    except ImportError:
        sys.exit("This script needs the `anthropic` package: pip install anthropic")

    corpus = build_corpus_text(works, abstracts)
    print(f"Asking {MODEL} to cluster {len(works)} works...", file=sys.stderr)
    client = anthropic.Anthropic()
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=8192,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            system=SYSTEM_PROMPT,
            tools=[CLUSTER_TOOL],
            tool_choice={"type": "tool", "name": "assign_research_clusters"},
            messages=[{"role": "user", "content": corpus}],
        )
    except anthropic.AuthenticationError:
        sys.exit(
            "No Anthropic API credentials found. Set ANTHROPIC_API_KEY, or run "
            "`ant auth login`, then re-run this script. Or pass --offline to "
            "cluster without the API (lower quality, but free and local)."
        )

    tool_use = next(b for b in response.content if b.type == "tool_use")
    return tool_use.input["clusters"]


def build_corpus_text(works, abstracts):
    lines = []
    for w in works:
        doi = w["doi"]
        abstract = abstracts.get(doi)
        lines.append(f"DOI: {doi}")
        lines.append(f"Title: {w['title']}")
        lines.append(f"Year: {w['year']}  Journal: {w.get('journal') or 'unknown'}")
        if abstract:
            lines.append(f"Abstract: {abstract}")
        else:
            lines.append("Abstract: (not available)")
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline", action="store_true",
        help="cluster with TF-IDF + k-means instead of Claude -- no API key or network cost beyond Crossref",
    )
    args = parser.parse_args()

    data = json.loads(PUBLICATIONS.read_text())
    works = [w for w in data["publications"] if w.get("doi")]
    print(f"{len(works)} works with a DOI", file=sys.stderr)

    print("Fetching abstracts from Crossref...", file=sys.stderr)
    abstracts = fetch_abstracts([w["doi"] for w in works])
    print(f"  {len(abstracts)}/{len(works)} have an abstract", file=sys.stderr)

    if args.offline:
        clusters = cluster_offline(works, abstracts)
        method = "tfidf-kmeans (offline fallback, not LLM-based)"
    else:
        clusters = cluster_with_claude(works, abstracts)
        method = MODEL

    by_doi = {w["doi"]: w for w in works}
    assigned_dois = set()
    out_clusters = []
    for c in clusters:
        cluster_works = []
        for doi in c["dois"]:
            doi = doi.lower()
            w = by_doi.get(doi)
            if not w:
                print(f"  warning: model returned unknown DOI {doi}, skipping", file=sys.stderr)
                continue
            assigned_dois.add(doi)
            cluster_works.append({
                "doi": doi,
                "title": w["title"],
                "year": w["year"],
                "journal": w.get("journal"),
                "has_abstract": doi in abstracts,
            })
        cluster_works.sort(key=lambda w: -(w["year"] or 0))
        out_clusters.append({
            "name": c["name"],
            "description": c["description"],
            "works": cluster_works,
        })
    out_clusters.sort(key=lambda c: -len(c["works"]))

    unassigned = [
        {"doi": w["doi"], "title": w["title"], "year": w["year"]}
        for w in works if w["doi"] not in assigned_dois
    ]
    if unassigned:
        print(f"  {len(unassigned)} works were not assigned to any cluster", file=sys.stderr)

    payload = {
        "generated": time.strftime("%Y-%m-%d"),
        "model": method,
        "source_works": len(works),
        "works_with_abstract": len(abstracts),
        "clusters": out_clusters,
        "unassigned": unassigned,
    }

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(out_clusters)} clusters", file=sys.stderr)


if __name__ == "__main__":
    main()
