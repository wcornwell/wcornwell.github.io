// Minimal dependency-free lightbox for gallery pages.
// Progressive enhancement: without JS the thumbnails are plain links to the
// full-size image, so the gallery still works.
(function () {
  "use strict";

  var links = Array.prototype.slice.call(
    document.querySelectorAll("a[data-lightbox]")
  );
  if (!links.length) return;

  var overlay, imgEl, textEl, counterEl;
  var index = 0;
  var lastFocused = null;

  function build() {
    overlay = document.createElement("div");
    overlay.className = "lb-overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-label", "Image viewer");
    overlay.innerHTML =
      '<button class="lb-btn lb-close" type="button" aria-label="Close">×</button>' +
      '<button class="lb-btn lb-prev" type="button" aria-label="Previous image">‹</button>' +
      '<button class="lb-btn lb-next" type="button" aria-label="Next image">›</button>' +
      '<figure class="lb-figure">' +
      '<img class="lb-image" alt="">' +
      '<figcaption class="lb-caption">' +
      '<span class="lb-text"></span><span class="lb-counter"></span>' +
      "</figcaption></figure>";
    document.body.appendChild(overlay);

    imgEl = overlay.querySelector(".lb-image");
    textEl = overlay.querySelector(".lb-text");
    counterEl = overlay.querySelector(".lb-counter");

    imgEl.addEventListener("load", function () {
      imgEl.classList.add("is-loaded");
    });

    overlay.querySelector(".lb-close").addEventListener("click", close);
    overlay.querySelector(".lb-prev").addEventListener("click", function (e) {
      e.stopPropagation();
      step(-1);
    });
    overlay.querySelector(".lb-next").addEventListener("click", function (e) {
      e.stopPropagation();
      step(1);
    });

    // Backdrop (but not the image itself) closes.
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay || e.target.classList.contains("lb-figure")) {
        close();
      }
    });
  }

  function preload(i) {
    var link = links[(i + links.length) % links.length];
    if (link) new Image().src = link.getAttribute("href");
  }

  function show(i) {
    index = (i + links.length) % links.length;
    var link = links[index];
    var caption = link.getAttribute("data-caption") || "";

    imgEl.classList.remove("is-loaded");
    imgEl.src = link.getAttribute("href");
    imgEl.alt = caption;
    textEl.textContent = caption;
    counterEl.textContent = index + 1 + " / " + links.length;

    // Cached images can finish before the load listener fires.
    if (imgEl.complete) imgEl.classList.add("is-loaded");

    // Only one neighbour each way — these are big files.
    preload(index + 1);
    preload(index - 1);
  }

  function open(i) {
    if (!overlay) build();
    lastFocused = document.activeElement;
    show(i);
    overlay.classList.add("is-open");
    document.body.classList.add("lb-lock");
    document.addEventListener("keydown", onKey);
    overlay.querySelector(".lb-close").focus();
  }

  function close() {
    overlay.classList.remove("is-open");
    document.body.classList.remove("lb-lock");
    document.removeEventListener("keydown", onKey);
    // Drop the decoded image so a big photo isn't pinned in memory.
    imgEl.removeAttribute("src");
    if (lastFocused && lastFocused.focus) lastFocused.focus();
  }

  function step(delta) {
    show(index + delta);
  }

  function onKey(e) {
    switch (e.key) {
      case "Escape":
        close();
        break;
      case "ArrowLeft":
        step(-1);
        break;
      case "ArrowRight":
        step(1);
        break;
      case "Tab":
        // Cheap focus trap: keep tabbing inside the dialog.
        e.preventDefault();
        break;
      default:
        return;
    }
    e.preventDefault();
  }

  links.forEach(function (link, i) {
    link.addEventListener("click", function (e) {
      // Let modifier-clicks open the raw image in a new tab as usual.
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
      e.preventDefault();
      open(i);
    });
  });
})();
