(function () {
  "use strict";

  var gallery = [];
  var index = 0;
  var overlay = null;
  var imgEl = null;
  var capEl = null;
  var counterEl = null;

  function collect() {
    gallery = [];
    document.querySelectorAll(".shots img").forEach(function (img) {
      var fig = img.closest("figure");
      var cap = fig ? fig.querySelector("figcaption") : null;
      gallery.push({
        src: img.currentSrc || img.src,
        alt: img.alt || "",
        caption: cap ? cap.textContent.trim() : img.alt || "",
      });
      img.classList.add("shots-zoomable");
      img.setAttribute("tabindex", "0");
      img.setAttribute("role", "button");
      img.setAttribute(
        "aria-label",
        (img.alt || "Screenshot") +
          (document.documentElement.lang === "en" ? " — enlarge" : " — ingrandisci")
      );
    });
  }

  function ensureOverlay() {
    if (overlay) return;
    overlay = document.createElement("div");
    overlay.className = "lightbox";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    var isEn = document.documentElement.lang === "en";
    overlay.setAttribute("aria-label", isEn ? "Screenshot preview" : "Anteprima schermata");
    overlay.innerHTML =
      '<button type="button" class="lightbox-close" aria-label="' +
      (isEn ? "Close" : "Chiudi") +
      '">&times;</button>' +
      '<button type="button" class="lightbox-nav lightbox-prev" aria-label="' +
      (isEn ? "Previous" : "Precedente") +
      '">&#10094;</button>' +
      '<button type="button" class="lightbox-nav lightbox-next" aria-label="' +
      (isEn ? "Next" : "Successiva") +
      '">&#10095;</button>' +
      '<figure class="lightbox-figure">' +
      '  <img class="lightbox-img" alt="">' +
      '  <figcaption class="lightbox-cap"></figcaption>' +
      '  <p class="lightbox-counter"></p>' +
      "</figure>";
    document.body.appendChild(overlay);

    imgEl = overlay.querySelector(".lightbox-img");
    capEl = overlay.querySelector(".lightbox-cap");
    counterEl = overlay.querySelector(".lightbox-counter");

    overlay.querySelector(".lightbox-close").addEventListener("click", close);
    overlay.querySelector(".lightbox-prev").addEventListener("click", function (e) {
      e.stopPropagation();
      show(index - 1);
    });
    overlay.querySelector(".lightbox-next").addEventListener("click", function (e) {
      e.stopPropagation();
      show(index + 1);
    });
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) close();
    });
  }

  function show(i) {
    if (!gallery.length) return;
    ensureOverlay();
    index = (i + gallery.length) % gallery.length;
    var item = gallery[index];
    imgEl.src = item.src;
    imgEl.alt = item.alt;
    capEl.textContent = item.caption;
    counterEl.textContent = index + 1 + " / " + gallery.length;
    overlay.classList.add("is-open");
    document.documentElement.classList.add("lightbox-open");
  }

  function close() {
    if (!overlay) return;
    overlay.classList.remove("is-open");
    document.documentElement.classList.remove("lightbox-open");
    imgEl.removeAttribute("src");
  }

  function openFrom(img) {
    var i = Array.prototype.indexOf.call(
      document.querySelectorAll(".shots img"),
      img
    );
    if (i >= 0) show(i);
  }

  document.addEventListener("click", function (e) {
    var img = e.target.closest(".shots img");
    if (img) {
      e.preventDefault();
      openFrom(img);
    }
  });

  document.addEventListener("keydown", function (e) {
    if (e.target.closest(".shots img") && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      openFrom(e.target.closest(".shots img"));
      return;
    }
    if (!overlay || !overlay.classList.contains("is-open")) return;
    if (e.key === "Escape") close();
    else if (e.key === "ArrowLeft") show(index - 1);
    else if (e.key === "ArrowRight") show(index + 1);
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", collect);
  } else {
    collect();
  }
})();
