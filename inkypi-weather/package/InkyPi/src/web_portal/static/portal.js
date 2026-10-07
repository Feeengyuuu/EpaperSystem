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

  const pollingRoot = document.querySelector("[data-poll-ms]");
  if (pollingRoot) {
    const pollDelay = Number(pollingRoot.dataset.pollMs);
    const renderedGeneration = Number(pollingRoot.dataset.generation);
    let knownEtag = null;
    let pollInFlight = false;
    let pollAgain = false;
    const poll = async () => {
      if (document.hidden) return;
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
  const previousButton = autoplay.querySelector("[data-previous]");
  const nextButton = autoplay.querySelector("[data-next]");
  const pauseButton = autoplay.querySelector("[data-pause]");
  const fullscreenButton = autoplay.querySelector("[data-fullscreen]");
  const chromeToggle = autoplay.querySelector("[data-play-chrome-toggle]");
  const currentLabel = autoplay.querySelector("[data-slide-current]");
  const controls = autoplay.querySelector("[data-play-controls]");
  const interval = Number(autoplay.dataset.intervalMs) || 20000;
  let current = 0;
  let paused = false;
  let timer = null;
  let idleTimer = null;

  const setChromeHidden = (hidden) => {
    autoplay.classList.toggle("is-chrome-hidden", hidden);
    if (!chromeToggle) return;
    chromeToggle.setAttribute("aria-pressed", hidden ? "true" : "false");
    chromeToggle.textContent = hidden ? "显示导航" : "隐藏导航";
  };

  const show = (next) => {
    if (!slides.length) return;
    current = (next + slides.length) % slides.length;
    slides.forEach((slide, index) => {
      const active = index === current;
      slide.hidden = !active;
      slide.classList.toggle("is-active", active);
      slide.setAttribute("aria-hidden", active ? "false" : "true");
    });
    if (currentLabel) currentLabel.textContent = String(current + 1);
  };

  const schedule = () => {
    window.clearTimeout(timer);
    if (!paused && slides.length > 1) {
      timer = window.setTimeout(() => {
        show(current + 1);
        schedule();
      }, interval);
    }
  };

  const revealControls = () => {
    if (!controls) return;
    controls.classList.remove("is-idle");
    window.clearTimeout(idleTimer);
    idleTimer = window.setTimeout(() => controls.classList.add("is-idle"), 4000);
  };

  previousButton?.addEventListener("click", () => {
    show(current - 1);
    schedule();
  });
  nextButton?.addEventListener("click", () => {
    show(current + 1);
    schedule();
  });
  pauseButton?.addEventListener("click", () => {
    paused = !paused;
    pauseButton.setAttribute("aria-pressed", paused ? "true" : "false");
    pauseButton.textContent = paused ? "继续播放" : "暂停播放";
    schedule();
  });
  chromeToggle?.addEventListener("click", () => {
    setChromeHidden(!autoplay.classList.contains("is-chrome-hidden"));
  });
  const fullscreenAvailable = Boolean(
    document.fullscreenEnabled && document.documentElement.requestFullscreen,
  );
  if (fullscreenButton && !fullscreenAvailable) {
    fullscreenButton.disabled = true;
    fullscreenButton.textContent = "已铺满页面";
    fullscreenButton.title = "此浏览器未开放系统全屏；当前画面已铺满网页可用区域";
  }
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
    if (fullscreenButton) {
      fullscreenButton.textContent = document.fullscreenElement ? "退出全屏" : "全屏显示";
    }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      window.clearTimeout(timer);
    } else {
      schedule();
    }
  });
  ["pointermove", "pointerdown", "keydown", "focusin"].forEach((eventName) => {
    document.addEventListener(eventName, revealControls, { passive: true });
  });

  show(0);
  revealControls();
  schedule();
})();
