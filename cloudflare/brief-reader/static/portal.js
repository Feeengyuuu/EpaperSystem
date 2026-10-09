(() => {
  "use strict";

  const root = document.documentElement;
  const themeButton = document.querySelector("[data-theme-toggle]");

  const savedTheme = (() => {
    try {
      return window.localStorage.getItem("epaper-portal-theme");
    } catch (_error) {
      return null;
    }
  })();

  if (savedTheme === "light" || savedTheme === "dark") {
    root.dataset.theme = savedTheme;
  }

  const updateThemeButton = () => {
    if (!themeButton) return;
    const dark = root.dataset.theme === "dark" ||
      (!root.dataset.theme && window.matchMedia("(prefers-color-scheme: dark)").matches);
    themeButton.setAttribute("aria-pressed", dark ? "true" : "false");
    themeButton.textContent = dark ? "使用浅色" : "使用深色";
  };

  if (themeButton) {
    updateThemeButton();
    themeButton.addEventListener("click", () => {
      const dark = themeButton.getAttribute("aria-pressed") === "true";
      const nextTheme = dark ? "light" : "dark";
      root.dataset.theme = nextTheme;
      try {
        window.localStorage.setItem("epaper-portal-theme", nextTheme);
      } catch (_error) {
        // The selected theme still applies for this page when storage is disabled.
      }
      updateThemeButton();
    });
  }

  const playbackStateKey = "epaper-portal-publication-playback";
  let savePlaybackBeforeReload = () => {};
  let checkPublication = () => {};
  const pollingRoot = document.querySelector("[data-poll-ms]");
  if (pollingRoot) {
    const pollDelay = Number(pollingRoot.dataset.pollMs);
    const renderedGeneration = Number(pollingRoot.dataset.generation);
    let knownEtag = null;
    let pollInFlight = false;
    let pollAgain = false;
    let reloadPending = false;
    const poll = async () => {
      if (document.hidden || reloadPending) return;
      if (pollInFlight) {
        pollAgain = true;
        return;
      }
      pollInFlight = true;
      pollAgain = false;
      const headers = knownEtag ? { "If-None-Match": knownEtag } : {};
      const controller = typeof window.AbortController === "function"
        ? new window.AbortController()
        : null;
      const fetchTimeout = controller
        ? window.setTimeout(() => controller.abort(), 10000)
        : null;
      try {
        const response = await window.fetch("/api/publications", {
          cache: "no-store",
          credentials: "same-origin",
          headers,
          signal: controller?.signal,
        });
        if (response.status === 401) {
          window.location.assign("/login");
          return;
        }
        if (response.status === 304) return;
        if (!response.ok) return;
        const nextEtag = response.headers.get("ETag");
        const payload = await response.json();
        const nextGeneration = Number(payload?.release?.generation);
        const generationChanged = (
          Number.isSafeInteger(renderedGeneration) &&
          Number.isSafeInteger(nextGeneration) &&
          renderedGeneration !== nextGeneration
        );
        const etagChanged = Boolean(
          knownEtag && nextEtag && knownEtag !== nextEtag,
        );
        if (generationChanged || etagChanged) {
          reloadPending = true;
          savePlaybackBeforeReload();
          window.location.reload();
          return;
        }
        knownEtag = nextEtag;
      } catch (_error) {
        // Keep the last rendered publication visible while offline.
      } finally {
        if (fetchTimeout !== null) window.clearTimeout(fetchTimeout);
        pollInFlight = false;
        if (pollAgain && !document.hidden) {
          pollAgain = false;
          void poll();
        }
      }
    };
    checkPublication = () => { void poll(); };
    void poll();
    if (Number.isFinite(pollDelay) && pollDelay >= 1000) {
      window.setInterval(poll, pollDelay);
    }
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) void poll();
    });
  }

  const autoplay = document.querySelector("[data-autoplay]");
  if (!autoplay) return;

  const slides = Array.from(autoplay.querySelectorAll("[data-slide]"));
  const segments = Array.from(autoplay.querySelectorAll("[data-progress-segment]"));
  const deck = autoplay.querySelector("[data-slide-deck]");
  const previousButton = autoplay.querySelector("[data-previous]");
  const nextButton = autoplay.querySelector("[data-next]");
  const pauseButton = autoplay.querySelector("[data-pause]");
  const fullscreenButton = autoplay.querySelector("[data-fullscreen]");
  const chromeToggle = autoplay.querySelector("[data-play-chrome-toggle]");
  const currentLabel = autoplay.querySelector("[data-slide-current]");
  const controls = autoplay.querySelector("[data-play-controls]");
  const interval = Number(autoplay.dataset.intervalMs) || 20000;
  const savedPlayback = (() => {
    try {
      const encoded = window.sessionStorage.getItem(playbackStateKey);
      window.sessionStorage.removeItem(playbackStateKey);
      const saved = JSON.parse(encoded ?? "null");
      return saved?.playlistId === autoplay.dataset.playlistId ? saved : null;
    } catch (_error) {
      return null;
    }
  })();
  const restoredIndex = savedPlayback
    ? slides.findIndex((slide) => slide.dataset.instanceId === savedPlayback.instanceId)
    : -1;
  let current = 0;
  let paused = savedPlayback?.paused === true;
  let timer = null;
  let advanceAt = null;
  let idleTimer = null;

  savePlaybackBeforeReload = () => {
    try {
      window.sessionStorage.setItem(playbackStateKey, JSON.stringify({
        playlistId: autoplay.dataset.playlistId,
        instanceId: slides[current]?.dataset.instanceId,
        paused,
        advanceAt,
      }));
    } catch (_error) {
      // A browser that disables storage can still load the new publication.
    }
  };

  // Icon-only controls carry their state in the accessible name and tooltip.
  const label = (button, text) => {
    button.setAttribute("aria-label", text);
    button.title = text;
  };

  const setChromeHidden = (hidden) => {
    autoplay.classList.toggle("is-chrome-hidden", hidden);
    if (!chromeToggle) return;
    chromeToggle.setAttribute("aria-expanded", hidden ? "false" : "true");
    label(chromeToggle, hidden ? "显示控制" : "隐藏控制");
  };
  const toggleChrome = () => setChromeHidden(!autoplay.classList.contains("is-chrome-hidden"));

  const show = (next) => {
    if (!slides.length) return;
    const previous = current;
    current = (next + slides.length) % slides.length;
    // Wrapping from the last slide to the first still reads as moving forward.
    autoplay.dataset.direction = next < previous ? "backward" : "forward";
    slides.forEach((slide, index) => {
      const active = index === current;
      slide.hidden = !active;
      slide.classList.toggle("is-active", active);
      slide.setAttribute("aria-hidden", active ? "false" : "true");
    });
    if (currentLabel) currentLabel.textContent = String(current + 1);
  };

  // Story-style progress: earlier segments are full and the current one fills
  // over the remaining slide time. A stopped timer freezes the current fill.
  const paintProgress = (delay) => {
    segments.forEach((segment, index) => {
      segment.classList.toggle("is-done", index < current);
      segment.classList.toggle("is-current", index === current);
      if (index !== current) segment.classList.remove("is-running", "is-frozen");
    });
    const segment = segments[current];
    if (!segment) return;
    if (delay === null) {
      segment.classList.add("is-frozen");
      return;
    }
    segment.classList.remove("is-running", "is-frozen");
    segment.style.setProperty("--slide-duration", `${interval}ms`);
    segment.style.setProperty("--slide-offset", `${Math.min(0, delay - interval)}ms`);
    void segment.offsetWidth; // restart the fill animation
    segment.classList.add("is-running");
  };

  const schedule = (delay = interval) => {
    window.clearTimeout(timer);
    advanceAt = null;
    if (!paused && slides.length > 1) {
      advanceAt = Date.now() + delay;
      timer = window.setTimeout(() => {
        show(current + 1);
        schedule();
      }, delay);
    }
    paintProgress(advanceAt === null ? null : delay);
  };

  const go = (next) => {
    show(next);
    schedule();
  };

  const updatePauseButton = () => {
    autoplay.classList.toggle("is-paused", paused);
    if (!pauseButton) return;
    pauseButton.setAttribute("aria-pressed", paused ? "true" : "false");
    label(pauseButton, paused ? "继续播放" : "暂停播放");
  };

  const togglePause = () => {
    paused = !paused;
    updatePauseButton();
    schedule();
  };

  const revealControls = () => {
    if (!controls) return;
    controls.classList.remove("is-idle");
    window.clearTimeout(idleTimer);
    idleTimer = window.setTimeout(() => controls.classList.add("is-idle"), 4000);
  };

  previousButton?.addEventListener("click", () => go(current - 1));
  nextButton?.addEventListener("click", () => go(current + 1));
  pauseButton?.addEventListener("click", togglePause);
  chromeToggle?.addEventListener("click", toggleChrome);

  // Tapping the picture shows or hides the controls; a horizontal swipe turns
  // the page. Pinch-zoomed pages keep native panning instead of swiping.
  let touchStart = null;
  deck?.addEventListener("click", toggleChrome);
  deck?.addEventListener("touchstart", (event) => {
    const touch = event.touches.length === 1 ? event.touches[0] : null;
    touchStart = touch ? { x: touch.clientX, y: touch.clientY } : null;
  }, { passive: true });
  deck?.addEventListener("touchmove", (event) => {
    if (event.touches.length > 1) touchStart = null;
  }, { passive: true });
  deck?.addEventListener("touchend", (event) => {
    const start = touchStart;
    touchStart = null;
    const touch = event.changedTouches[0];
    if (!start || !touch || (window.visualViewport?.scale ?? 1) > 1.01) return;
    const dx = touch.clientX - start.x;
    const dy = touch.clientY - start.y;
    if (Math.abs(dx) < 48 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
    go(dx < 0 ? current + 1 : current - 1);
  }, { passive: true });

  document.addEventListener("keydown", (event) => {
    if (event.altKey || event.ctrlKey || event.metaKey || event.defaultPrevented) return;
    if (event.key === "ArrowLeft") go(current - 1);
    else if (event.key === "ArrowRight") go(current + 1);
    // Space on a focused button already activates that button.
    else if (event.key === " " && event.target?.tagName !== "BUTTON") {
      event.preventDefault();
      togglePause();
    }
  });

  const fullscreenAvailable = Boolean(
    document.fullscreenEnabled && document.documentElement.requestFullscreen,
  );
  // iPhone Safari and vehicle browsers do not expose system fullscreen; the
  // picture already fills the page there, so the control is omitted.
  if (fullscreenButton && !fullscreenAvailable) fullscreenButton.hidden = true;
  fullscreenButton?.addEventListener("click", async () => {
    if (!fullscreenAvailable) return;
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
      } else {
        await document.documentElement.requestFullscreen();
      }
    } catch (_error) {
      // Some vehicle browsers intentionally do not expose the Fullscreen API.
    }
  });
  document.addEventListener("fullscreenchange", () => {
    if (!fullscreenButton) return;
    const active = Boolean(document.fullscreenElement);
    fullscreenButton.setAttribute("aria-pressed", active ? "true" : "false");
    label(fullscreenButton, active ? "退出全屏" : "全屏显示");
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      window.clearTimeout(timer);
      advanceAt = null;
    } else {
      schedule();
    }
  });
  ["pointermove", "pointerdown", "keydown", "focusin"].forEach((eventName) => {
    document.addEventListener(eventName, revealControls, { passive: true });
  });

  show(restoredIndex >= 0 ? restoredIndex : 0);
  updatePauseButton();
  revealControls();
  const remaining = restoredIndex >= 0 && Number.isFinite(savedPlayback?.advanceAt)
    ? Math.max(0, Math.min(interval, savedPlayback.advanceAt - Date.now()))
    : interval;
  schedule(remaining);

  // Old content-addressed assets are retired at commit. A lazy image may only
  // be requested afterwards, so recover without waiting for the next poll.
  for (const image of autoplay.querySelectorAll("[data-slide] img")) {
    image.addEventListener("error", checkPublication);
    if (image.complete && image.naturalWidth === 0) checkPublication();
  }
})();
