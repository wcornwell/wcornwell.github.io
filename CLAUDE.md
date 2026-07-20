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

**Assets** in `assets/`:
- `assets/images/labgroup/` — source images for the lab group gallery page. These *are* processed by Hugo Pipes (via `resources.Match` in the labgroup layout).
- `assets/css/lightbox.css` and `assets/js/lightbox.js` import Lightbox2 from CDN, but **they are currently dead files — nothing emits them.** `hugo.toml` sets `params.assets.customCSS` / `customJS`, which PaperMod does not read (grep the theme: it has no such params). PaperMod's extension points are `layouts/partials/extend_head.html` and `extend_footer.html`, neither of which this repo defines. A `hugo` build produces zero references to "lightbox" anywhere in `public/`, and the gallery thumbnails are plain `<img>` tags with no lightbox markup. Either wire these up via `extend_head.html`/`extend_footer.html` (and add the `<a data-lightbox>` wrappers in `labgroup.html`), or delete them — but do not assume adding a third `customCSS` entry will do anything.
- `assets/css/gallery.css` — also dead. Its `.image-grid`/`.grid-item` classes appear in no layout; the live gallery styles are the inline `<style>` block in `labgroup.html`.

**Adding a new gallery image**: drop an image into the directory named by the page's `galleryPath` (currently `assets/images/labgroup/`). The glob is `*.{jpg,jpeg,png}` and matches case-insensitively, so `.JPG` works too. The filename (without extension, with `_`/`-` replaced by spaces) becomes the caption automatically.

**Adding a social icon**: add the entry to `[[params.socialIcons]]` in `hugo.toml`. That is usually all — PaperMod ships 151 icons (`themes/PaperMod/layouts/_partials/svg.html` is the list). Only if the name is absent there do you need a custom glyph, following the `inaturalist_icon.html` pattern.

**Updating the theme**: PaperMod is vendored, so replace `themes/PaperMod/` wholesale from upstream `master` — do **not** use the tagged releases, which are years stale (`v8.0` is from 2024). Currently pinned to upstream master `154d006` (2026-05-10). After updating, re-check the two overrides above still match the theme's expectations.

The `Gemfile.lock` is present because Jekyll was previously used — it's not needed for Hugo and can be ignored.
