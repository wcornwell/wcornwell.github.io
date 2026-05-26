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

## Architecture

This is a [Hugo](https://gohugo.io/) static site using the [PaperMod](https://github.com/adityatelange/hugo-papermod) theme (located at `themes/PaperMod/`). The site is Will Cornwell's academic homepage at willcornwell.org.

**Content** lives in `content/` as Markdown files. Pages use standard Hugo front matter. The `labgroup.md` page uses a custom layout (`layout: "labgroup"`).

**Custom layouts** in `layouts/` override or extend PaperMod:
- `layouts/_default/labgroup.html` — renders a photo gallery grid from images in `assets/images/labgroup/`. Images are loaded via Hugo's `resources.Match` pipeline, fingerprinted for cache-busting, and captions are auto-generated from filenames (underscores/hyphens → spaces, title-cased).
- `layouts/partials/svg.html` — custom SVG icon partial that adds iNaturalist and ORCID icons not built into PaperMod. Social icons in `hugo.toml` route through this partial.

**Assets** in `assets/` are processed by Hugo Pipes:
- `assets/css/lightbox.css` and `assets/js/lightbox.js` import Lightbox2 from CDN. These are injected globally via `hugo.toml` params (`customCSS`, `customJS`).
- `assets/images/labgroup/` — source images for the lab group gallery page.

**Adding a new gallery image**: drop a `.jpg`/`.jpeg`/`.png` into `assets/images/labgroup/`. The filename (without extension, with `_`/`-` replaced by spaces) becomes the caption automatically.

**Adding a social icon**: add the entry to `[[params.socialIcons]]` in `hugo.toml` and, if the icon isn't already in `layouts/partials/svg.html`, add its SVG there.

The `Gemfile.lock` is present because Jekyll was previously used — it's not needed for Hugo and can be ignored.
