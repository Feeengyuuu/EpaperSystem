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
  // The player swaps a new edition into the open page so fullscreen, the
  // rotation and the wake lock survive. Without it (the empty reader) a new
  // edition reloads the page.
  let replacePublication = null;
  const pollingRoot = document.querySelector("[data-poll-ms]");
  if (pollingRoot) {
    let pollDelay = Number(pollingRoot.dataset.pollMs);
    let renderedGeneration = Number(pollingRoot.dataset.generation);
    let pollTimer = null;
    let knownEtag = null;
    let pollInFlight = false;
    let pollAgain = false;
    let reloadPending = false;
    const reloadPublication = () => {
      reloadPending = true;
      savePlaybackBeforeReload();
      window.location.reload();
    };
    let startPolling = () => {};
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
          const outcome = replacePublication ? await replacePublication() : "reload";
          if (outcome === "login") {
            window.location.assign("/login");
            return;
          }
          if (outcome === "reload") {
            reloadPublication();
            return;
          }
          // Offline or timed out: keep the picture; the next check retries.
          if (outcome !== "replaced") return;
          renderedGeneration = Number(pollingRoot.dataset.generation);
          const nextDelay = Number(pollingRoot.dataset.pollMs);
          if (nextDelay !== pollDelay) {
            pollDelay = nextDelay;
            startPolling();
          }
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
    startPolling = () => {
      if (pollTimer !== null) window.clearInterval(pollTimer);
      pollTimer = Number.isFinite(pollDelay) && pollDelay >= 1000
        ? window.setInterval(poll, pollDelay)
        : null;
    };
    checkPublication = () => { void poll(); };
    void poll();
    startPolling();
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) void poll();
    });
  }

  const autoplay = document.querySelector("[data-autoplay]");
  if (!autoplay) return;

  const deck = autoplay.querySelector("[data-slide-deck]");
  const previousButton = autoplay.querySelector("[data-previous]");
  const nextButton = autoplay.querySelector("[data-next]");
  const pauseButton = autoplay.querySelector("[data-pause]");
  const fullscreenButton = autoplay.querySelector("[data-fullscreen]");
  const chromeToggle = autoplay.querySelector("[data-play-chrome-toggle]");
  const controls = autoplay.querySelector("[data-play-controls]");
  const interval = Number(autoplay.dataset.intervalMs) || 20000;

  // Everything that belongs to one edition is looked up again after an
  // in-place swap; the deck, buttons and document listeners stay bound once.
  let slides = [];
  let segments = [];
  let images = [];
  let currentLabel = null;
  let dockStatus = null;
  const bindPublication = () => {
    images.forEach((image) => image.removeEventListener("error", checkPublication));
    slides = Array.from(autoplay.querySelectorAll("[data-slide]"));
    segments = Array.from(autoplay.querySelectorAll("[data-progress-segment]"));
    images = Array.from(autoplay.querySelectorAll("[data-slide] img"));
    currentLabel = autoplay.querySelector("[data-slide-current]");
    dockStatus = autoplay.querySelector("[data-dock-status]");
    // Old content-addressed assets are retired at commit. A lazy image may
    // only be requested afterwards, so recover without waiting for a poll.
    images.forEach((image) => image.addEventListener("error", checkPublication));
  };
  bindPublication();
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

  const show = (next, steady = false) => {
    if (!slides.length) return;
    const previous = current;
    current = (next + slides.length) % slides.length;
    // Wrapping from the last slide to the first still reads as moving forward.
    // A swapped-in edition stays put instead of replaying the page turn.
    autoplay.dataset.direction = steady ? "steady" : next < previous ? "backward" : "forward";
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
    let dx = touch.clientX - start.x;
    let dy = touch.clientY - start.y;
    // A picture turned 90° clockwise swipes along the screen's vertical axis.
    if (pictureRotated()) [dx, dy] = [dy, -dx];
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

  // ---------- Rotate to fullscreen ----------
  // System fullscreen plus a landscape orientation lock where the browser
  // allows it (Android Chrome). Elsewhere (iPhone Safari has neither) the
  // picture itself turns 90° clockwise to fill a portrait screen.

  const readPreference = (key) => {
    try {
      return window.localStorage.getItem(key) === "on";
    } catch (_error) {
      return false;
    }
  };
  const writePreference = (key, enabled) => {
    try {
      window.localStorage.setItem(key, enabled ? "on" : "off");
    } catch (_error) {
      // The choice still applies to this page when storage is disabled.
    }
  };

  const rotationKey = "epaper-portal-rotation";
  const rotateButton = autoplay.querySelector("[data-rotate-fullscreen]");
  const portraitQuery = typeof window.matchMedia === "function"
    ? window.matchMedia("(orientation: portrait)")
    : null;
  const screenOrientation = window.screen?.orientation ?? null;
  let rotationWanted = readPreference(rotationKey);
  let rotationOwnsFullscreen = false;
  let orientationLocked = false;
  let rotationBusy = false;

  const isPortrait = () => Boolean(portraitQuery?.matches);
  // The stylesheet rotates only while the viewport is portrait, so a device
  // the visitor really turns to landscape is never rotated twice.
  const pictureRotated = () => autoplay.classList.contains("is-rotated") && isPortrait();

  const updateRotation = () => {
    const rotate = rotationWanted && !orientationLocked &&
      (autoplay.classList.contains("is-rotated") || isPortrait());
    autoplay.classList.toggle("is-rotated", rotate);
    if (!rotateButton) return;
    rotateButton.setAttribute("aria-pressed", rotationWanted ? "true" : "false");
    label(rotateButton, rotationWanted ? "退出旋转全屏" : "旋转全屏");
  };

  const enableRotation = async () => {
    rotationWanted = true;
    writePreference(rotationKey, true);
    if (fullscreenAvailable && !document.fullscreenElement) {
      try {
        await document.documentElement.requestFullscreen({ navigationUI: "hide" });
        rotationOwnsFullscreen = true;
      } catch (_error) {
        // The CSS rotation below still works without system fullscreen.
      }
    }
    orientationLocked = false;
    if (screenOrientation && typeof screenOrientation.lock === "function") {
      try {
        await screenOrientation.lock("landscape");
        orientationLocked = true;
      } catch (_error) {
        // iPhone Safari and desktop browsers cannot lock the orientation.
      }
    }
    updateRotation();
  };

  const disableRotation = async () => {
    rotationWanted = false;
    writePreference(rotationKey, false);
    if (orientationLocked) {
      orientationLocked = false;
      try {
        screenOrientation?.unlock?.();
      } catch (_error) {
        // Leaving fullscreen releases the lock anyway.
      }
    }
    autoplay.classList.remove("is-rotated");
    updateRotation();
    const ownedFullscreen = rotationOwnsFullscreen;
    rotationOwnsFullscreen = false;
    if (ownedFullscreen && document.fullscreenElement && typeof document.exitFullscreen === "function") {
      try {
        await document.exitFullscreen();
      } catch (_error) {
        // The visitor can still leave fullscreen with the system gesture.
      }
    }
  };

  rotateButton?.addEventListener("click", async () => {
    if (rotationBusy) return;
    rotationBusy = true;
    try {
      await (rotationWanted ? disableRotation() : enableRotation());
    } finally {
      rotationBusy = false;
    }
  });

  if (portraitQuery) {
    if (typeof portraitQuery.addEventListener === "function") {
      portraitQuery.addEventListener("change", updateRotation);
    } else if (typeof portraitQuery.addListener === "function") {
      portraitQuery.addListener(updateRotation);
    }
  }

  document.addEventListener("fullscreenchange", () => {
    const active = Boolean(document.fullscreenElement);
    if (fullscreenButton) {
      fullscreenButton.setAttribute("aria-pressed", active ? "true" : "false");
      label(fullscreenButton, active ? "退出全屏" : "全屏显示");
    }
    if (active) return;
    // Leaving fullscreen (also by the system gesture) releases the lock.
    rotationOwnsFullscreen = false;
    orientationLocked = false;
    if (rotationWanted && !autoplay.classList.contains("is-rotated")) {
      rotationWanted = false;
      writePreference(rotationKey, false);
    }
    updateRotation();
  });

  // ---------- Keep the screen awake ----------
  // The Screen Wake Lock is dropped whenever the page is hidden and may be
  // refused (power saving, no recent tap). A wanted lock is asked for again
  // when the page is visible or on the next tap or key, never in a loop.

  const wakeKey = "epaper-portal-keep-awake";
  const awakeButton = autoplay.querySelector("[data-keep-awake]");
  const wakeLockApi = window.navigator?.wakeLock &&
    typeof window.navigator.wakeLock.request === "function"
    ? window.navigator.wakeLock
    : null;
  let wakeWanted = Boolean(wakeLockApi) && readPreference(wakeKey);
  let wakeSentinel = null;
  let wakeRequest = null;

  const releaseQuietly = (sentinel) => {
    try {
      Promise.resolve(sentinel.release()).catch(() => {});
    } catch (_error) {
      // Already released.
    }
  };

  const updateWakeButton = () => {
    if (!awakeButton) return;
    if (!wakeLockApi) {
      awakeButton.setAttribute("aria-disabled", "true");
      awakeButton.setAttribute("aria-pressed", "false");
      awakeButton.dataset.wakeState = "unsupported";
      label(awakeButton, "此浏览器不支持不息屏");
      return;
    }
    awakeButton.setAttribute("aria-pressed", wakeWanted ? "true" : "false");
    awakeButton.dataset.wakeState = !wakeWanted ? "off" : wakeSentinel ? "active" : "pending";
    label(awakeButton, wakeWanted ? "关闭不息屏" : "开启不息屏");
  };

  const requestWakeLock = async () => {
    if (!wakeLockApi || !wakeWanted || wakeSentinel || wakeRequest || document.hidden) return;
    wakeRequest = (async () => {
      try {
        const sentinel = await wakeLockApi.request("screen");
        if (!wakeWanted) {
          releaseQuietly(sentinel);
          return;
        }
        wakeSentinel = sentinel;
        sentinel.addEventListener("release", () => {
          if (wakeSentinel !== sentinel) return;
          wakeSentinel = null;
          updateWakeButton();
        });
      } catch (_error) {
        // Refused: wait for the page to become visible or the next interaction.
      }
    })();
    try {
      await wakeRequest;
    } finally {
      wakeRequest = null;
      updateWakeButton();
    }
  };

  awakeButton?.addEventListener("click", () => {
    if (!wakeLockApi) return;
    wakeWanted = !wakeWanted;
    writePreference(wakeKey, wakeWanted);
    if (wakeWanted) {
      void requestWakeLock();
    } else if (wakeSentinel) {
      const sentinel = wakeSentinel;
      wakeSentinel = null;
      releaseQuietly(sentinel);
    }
    updateWakeButton();
  });

  if (wakeLockApi) {
    const retryWakeLock = () => { void requestWakeLock(); };
    document.addEventListener("visibilitychange", retryWakeLock);
    document.addEventListener("pointerdown", retryWakeLock, { passive: true });
    document.addEventListener("keydown", retryWakeLock);
  }
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

  // ---------- In-place publication swap ----------

  const PAGE_TIMEOUT_MS = 15000;
  const DECODE_WAIT_MS = 4000;

  const settleWithin = (promise, milliseconds) => new Promise((resolve) => {
    const timeout = window.setTimeout(resolve, milliseconds);
    const finish = () => {
      window.clearTimeout(timeout);
      resolve();
    };
    Promise.resolve(promise).then(finish, finish);
  });

  const replaceChildren = (parent, nodes) => {
    if (typeof parent.replaceChildren === "function") {
      parent.replaceChildren(...nodes);
      return;
    }
    while (parent.firstChild) parent.removeChild(parent.firstChild);
    nodes.forEach((node) => parent.appendChild(node));
  };

  const redirectedToLogin = (response) => {
    if (!response.redirected || typeof response.url !== "string") return false;
    const path = response.url.replace(/^[a-z][\w+.-]*:\/\/[^/]*/iu, "").split(/[?#]/u)[0];
    return path === "/login";
  };

  const adoptPublication = async (nextPlayer, nextSlides, nextStatus) => {
    // Fetch the picture that stays on screen before swapping so it does not flash.
    const wantedId = slides[current]?.dataset.instanceId;
    const upcoming = nextSlides.find((slide) => slide.dataset.instanceId === wantedId) ??
      nextSlides[Math.min(current, nextSlides.length - 1)];
    const upcomingImage = upcoming?.querySelector("img");
    upcomingImage?.setAttribute("loading", "eager");
    const adoptedSlides = nextSlides.map((slide) => document.adoptNode(slide));
    const adoptedStatus = nextStatus ? document.adoptNode(nextStatus) : null;
    if (upcomingImage && typeof upcomingImage.decode === "function") {
      await settleWithin(upcomingImage.decode(), DECODE_WAIT_MS);
    }
    // The slide timer may have moved on while the picture decoded.
    const instanceId = slides[current]?.dataset.instanceId;
    const remaining = advanceAt === null ? null : Math.max(0, Math.min(interval, advanceAt - Date.now()));
    replaceChildren(deck, adoptedSlides);
    // Same item count: keep the running progress bar untouched.
    const nextTotal = adoptedStatus?.querySelector("[data-slide-total]")?.textContent;
    const shownTotal = dockStatus?.querySelector("[data-slide-total]")?.textContent;
    const nextSegments = adoptedStatus ? adoptedStatus.querySelectorAll("[data-progress-segment]").length : 0;
    if (adoptedStatus && dockStatus && (nextSegments !== segments.length || nextTotal !== shownTotal)) {
      dockStatus.parentNode.replaceChild(adoptedStatus, dockStatus);
    }
    autoplay.dataset.generation = nextPlayer.dataset.generation;
    autoplay.dataset.pollMs = nextPlayer.dataset.pollMs;
    bindPublication();
    const kept = slides.findIndex((slide) => slide.dataset.instanceId === instanceId);
    show(kept >= 0 ? kept : Math.min(current, slides.length - 1), true);
    schedule(kept >= 0 && remaining !== null ? remaining : interval);
  };

  replacePublication = async () => {
    if (typeof window.DOMParser !== "function") return "reload";
    const controller = typeof window.AbortController === "function"
      ? new window.AbortController()
      : null;
    const timeout = controller
      ? window.setTimeout(() => controller.abort(), PAGE_TIMEOUT_MS)
      : null;
    let html;
    try {
      const response = await window.fetch(`${window.location.pathname}${window.location.search}`, {
        cache: "no-store",
        credentials: "same-origin",
        headers: { Accept: "text/html" },
        signal: controller?.signal,
      });
      if (response.status === 401 || redirectedToLogin(response)) return "login";
      if (!response.ok) return "reload";
      html = await response.text();
    } catch (_error) {
      return "retry";
    } finally {
      if (timeout !== null) window.clearTimeout(timeout);
    }
    try {
      const incoming = new window.DOMParser().parseFromString(html, "text/html");
      if (incoming.querySelector('form[action="/login"]')) return "login";
      const nextPlayer = incoming.querySelector("[data-autoplay]");
      const nextDeck = nextPlayer?.querySelector("[data-slide-deck]");
      const nextSlides = nextDeck ? Array.from(nextDeck.querySelectorAll("[data-slide]")) : [];
      const nextStatus = nextPlayer?.querySelector("[data-dock-status]") ?? null;
      // A different playlist, an emptied catalog or a new Worker deployment
      // (new script and stylesheet) needs a real page load.
      if (
        !nextPlayer ||
        !nextSlides.length ||
        nextPlayer.dataset.playlistId !== autoplay.dataset.playlistId ||
        nextPlayer.dataset.assetVersion !== autoplay.dataset.assetVersion ||
        Boolean(nextStatus) !== Boolean(dockStatus)
      ) {
        return "reload";
      }
      await adoptPublication(nextPlayer, nextSlides, nextStatus);
      return "replaced";
    } catch (_error) {
      return "reload";
    }
  };

  show(restoredIndex >= 0 ? restoredIndex : 0);
  updatePauseButton();
  // Fullscreen needs a fresh tap after a reload; the CSS rotation does not.
  updateRotation();
  updateWakeButton();
  void requestWakeLock();
  revealControls();
  const remaining = restoredIndex >= 0 && Number.isFinite(savedPlayback?.advanceAt)
    ? Math.max(0, Math.min(interval, savedPlayback.advanceAt - Date.now()))
    : interval;
  schedule(remaining);

  // Images that failed before this deferred script ran never fire "error".
  for (const image of images) {
    if (image.complete && image.naturalWidth === 0) checkPublication();
  }
})();
