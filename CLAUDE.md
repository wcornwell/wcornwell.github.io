# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Serve locally with live reload
hugo server

# Build the site
hugo

# Build including draft content
hugo server -D
```

The site builds to `public/` (configured via `publishDir` in `hugo.toml`). The `_site/` directory in the repo root is unrelated (likely a Jekyll artifact) — ignore it.

**Deployment** is automatic: `.github/workflows/hugo.yml` runs `hugo --minify` on every push to `main` and publishes to GitHub Pages. It pins **Hugo 0.163.3 extended** — keep that pin in sync with your local `hugo version`, since a mismatch means `hugo server` can pass locally while CI renders differently. Hugo **extended** is required (the gallery emits WebP). The PaperMod theme is *vendored* as ordinary tracked files under `themes/PaperMod/`, not fetched at build time — the stale `[submodule]` entry in `.gitmodules` is a leftover and does nothing, which is why CI works without a submodule checkout.

## Architecture

This is a [Hugo](https://gohugo.io/) static site using the [PaperMod](https://github.com/adityatelange/hugo-papermod) theme (located at `themes/PaperMod/`). The site is Will Cornwell's academic homepage at willcornwell.org.

**Content** lives in `content/` as Markdown files. Pages use standard Hugo front matter. The `labgroup.md` page uses a custom layout (`layout: "labgroup"`).

**Custom layouts** in `layouts/` override or extend PaperMod. Note the theme uses Hugo's **new template system** (0.146+): partials live in `layouts/_partials/` (not `layouts/partials/`) and page layouts are flat in `layouts/` (not `layouts/_default/`). Overrides placed at the old paths are silently ignored.
- `layouts/labgroup.html` — renders a photo gallery grid. The source directory is **not** hardcoded: the layout reads the `galleryPath` front-matter param (`content/labgroup.md` sets `galleryPath: "images/labgroup"`, resolved under `assets/`). Images are loaded via Hugo's `resources.Match` pipeline and resized to 400w/800w WebP derivatives served via `srcset` (Hugo's image processing puts a content hash in the output filename, so no separate `resources.Fingerprint` call is needed). Captions are auto-generated from filenames (underscores/hyphens → spaces, title-cased) and reused as the `alt` text. A page using `layout: "labgroup"` without a `galleryPath` renders a "No images found" message. The gallery's CSS lives in an inline `<style>` block at the bottom of this file, not in `assets/css/`.
- `layouts/_partials/social_icons.html` + `layouts/_partials/inaturalist_icon.html` — the theme ships 151 icons but not iNaturalist. Rather than overriding the huge `svg.html`, this overrides the 8-line `social_icons.html` to special-case `inaturalist` and delegate every other name to the theme's `svg.html`. **Adding a social icon PaperMod already supports needs no layout change** — just the `hugo.toml` entry. Only add markup here if the icon is genuinely absent upstream (the theme renders a generic chain-link glyph for unknown names).

**Publications** (`content/publications.md` + `layouts/publications.html` + `data/publications.json`) — the list is generated, not hand-maintained.
- `scripts/fetch_publications.py` writes `data/publications.json`, which **is committed**. Hugo reads it at build time, so builds need no network and cannot be broken by an API outage.
- **ORCID is the authoritative source** for which works exist (as of 2026-07-21: 169, of which 164 have DOIs and 164 have authors). Crossref is used *only* to fill in author lists, which ORCID's work summaries omit. Do **not** switch to Crossref's `filter=orcid:` search as the primary source — it only returns works where a publisher deposited the ORCID iD. Crossref is queried in batches of 20 DOIs via OR'd `filter=doi:` params, so all 164 take ~9 requests.
- The script is **idempotent**: if the fetched works are identical to what's already in the JSON it keeps the old `generated` date and writes nothing, so the weekly refresh doesn't produce a no-op commit.
- `.github/workflows/refresh-publications.yml` re-runs it weekly. Note the GitHub gotcha it works around: **a commit pushed with `GITHUB_TOKEN` does not fire a `push` trigger**, so that workflow cannot rely on `hugo.yml`'s `push` event to redeploy. `hugo.yml` therefore also has a `workflow_call:` trigger, and the refresh job invokes it directly (`uses: ./.github/workflows/hugo.yml`) only when the data actually changed. If you remove that `workflow_call:` trigger, new papers will land in the repo but never reach the live site.
- **5 works have no DOI**, so they get no author list (authors come from Crossref, which is keyed on DOI). They still render, with the title linked to ORCID's `url` when one exists. **Fix these upstream in ORCID, not with a local patch file** — a patch would be a second source of truth that drifts, whereas an ORCID edit is picked up automatically by the next weekly refresh. Four of the five legitimately have no DOI (two AGU conference abstracts, a book chapter, a PhD thesis). The remaining one is fixable in ORCID: **Am J Botany 2001** *Occurrence of arbuscular mycorrhizal fungi in a phosphorus-poor wetland…* → `10.2307/3558359`.
  - **The one sanctioned exception is `DOI_PATCHES` in `fetch_publications.py`.** ORCID's DOI-field validator rejects legacy ESA-style DOIs containing `()[]:;`, so Ecology 2006 *A trait-based test for habitat filtering* (`10.1890/0012-9658(2006)87[1465:attfhf]2.0.co;2`) **cannot** be entered upstream — the dict injects it by exact title match before Crossref enrichment, so it gets authors like any other work. Add to `DOI_PATCHES` only when ORCID genuinely refuses the value; anything ORCID will accept belongs upstream.
  - If you ever run a Crossref title search to recover a missing DOI: **verify each hit's year and journal before trusting it.** In a past round of 11 title lookups, 4 returned confidently-wrong papers. Never bulk-import Crossref title matches into the ORCID record.
- In the template, remember **JSON numbers arrive as `float64`** — `index` on a slice needs `int .me_index` or the build fails. `me_index` exists so that on consortium papers (one has 729 authors) the collapsed `<details>` summary can still surface Will's name when he falls outside the first 8.

**Bibliometrics** (`content/bibliometrics.md` + `layouts/bibliometrics.html` + `assets/css/bibliometrics.css` + `data/abstract_clusters.json`) — the "By the Numbers" page, charting publication history and thematic clusters.
- `scripts/cluster_abstracts.py` writes `data/abstract_clusters.json` (committed, like `publications.json`). It pulls abstracts from Crossref for every DOI in `publications.json`, then groups them into 6–12 research themes.
- **This script is manual and occasional — it is deliberately NOT on a schedule**, unlike `fetch_publications.py`. Re-run it by hand when the publication list has moved on enough to be worth reclustering. Don't wire it into `refresh-publications.yml`: it costs money per run and reclustering on every new paper would churn the theme names.
- Clustering uses **OpenAI** (`openai` package, `chat.completions` with a strict `json_schema` response format, so the output is validated rather than parsed out of prose). Model defaults to `gpt-5`, overridable via `--model` or `OPENAI_MODEL`. **The API key comes from `OPENAI_API_KEY` in the environment and must never be committed — this repo is public and deploys to GitHub Pages.**
- `--offline` swaps in a TF-IDF + k-means fallback (scikit-learn, picks `k` by silhouette score). Lower quality and the theme names are just top terms, but it needs no key and no spend. Both paths emit the same JSON shape, so `layouts/bibliometrics.html` is indifferent to which produced it.

**Assets** in `assets/`:
- `assets/images/labgroup/` — source images for the lab group gallery page. These *are* processed by Hugo Pipes (via `resources.Match` in the labgroup layout).
- `assets/css/lightbox.css` and `assets/js/lightbox.js` — a self-contained, dependency-free lightbox for the gallery (no CDN, no Lightbox2). **They are emitted by `layouts/labgroup.html` directly**, via `resources.Get | minify | fingerprint` with an SRI `integrity` attribute — *not* by `hugo.toml`'s `params.assets.customCSS`/`customJS`, which PaperMod does not read (grep the theme: it has no such params; those keys have been removed from the config). They are therefore loaded only on gallery pages, not site-wide. If you ever need a genuinely site-wide asset, PaperMod's extension points are `layouts/_partials/extend_head.html` and `extend_footer.html`, neither of which this repo defines.
  - The JS binds to `a[data-lightbox]`, which `labgroup.html` wraps around each thumbnail; the `href` points at a full-size derivative capped at 1600w (`math.Min 1600 .Width` avoids upscaling small originals). Without JS the anchors degrade to plain links to that image.
  - The overlay element is built in JS on first open, so the stylesheet can load at the end of `<body>` with no flash of unstyled content. Features: prev/next buttons, arrow-key nav, Esc/backdrop close, focus restore, neighbour preloading, and a counter. The backdrop is always dark, so the controls do not need light/dark theme variants.
- The live gallery styles are the inline `<style>` block at the bottom of `labgroup.html`, not a file in `assets/css/`. (A dead `gallery.css` used to sit here; it was deleted, since nothing emitted it.)

**Adding a new gallery image**: drop an image into the directory named by the page's `galleryPath` (currently `assets/images/labgroup/`). The glob is `*.{jpg,jpeg,png}` and matches case-insensitively, so `.JPG` works too. The filename (without extension, with `_`/`-` replaced by spaces) becomes the caption automatically.

**Adding a social icon**: add the entry to `[[params.socialIcons]]` in `hugo.toml`. That is usually all — PaperMod ships 151 icons (`themes/PaperMod/layouts/_partials/svg.html` is the list). Only if the name is absent there do you need a custom glyph, following the `inaturalist_icon.html` pattern.

**Updating the theme**: PaperMod is vendored, so replace `themes/PaperMod/` wholesale from upstream `master` — do **not** use the tagged releases, which are years stale (`v8.0` is from 2024). Currently pinned to upstream master `154d006` (2026-05-10). After updating, re-check the two overrides above still match the theme's expectations.

The `Gemfile.lock` is present because Jekyll was previously used — it's not needed for Hugo and can be ignored.
