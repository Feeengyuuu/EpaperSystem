/* Shared behaviour for the InkyPi web UI shell: theme, language, toasts,
 * sheets, confirmation, JSON requests and visibility-aware polling.
 * Loaded with `defer` after inkypi-security.js, so fetch already carries the
 * CSRF header for unsafe methods. */
(function () {
    "use strict";

    const THEME_KEY = "inkypi-theme";

    function readStorage(key) {
        try {
            return window.localStorage.getItem(key);
        } catch (error) {
            return null;
        }
    }

    function writeStorage(key, value) {
        try {
            window.localStorage.setItem(key, value);
        } catch (error) {
            /* storage can be unavailable in private windows */
        }
    }

    /* ---------- Theme ---------- */
    function currentTheme() {
        return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
    }

    function applyTheme(theme) {
        const value = theme === "light" ? "light" : "dark";
        document.documentElement.setAttribute("data-theme", value);
        const meta = document.querySelector('meta[name="theme-color"]');
        if (meta) {
            meta.setAttribute("content", value === "light" ? "#f4f5f7" : "#111316");
        }
        document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
            button.setAttribute("aria-pressed", value === "light" ? "true" : "false");
            button.title = value === "light" ? "Switch to dark mode" : "Switch to light mode";
        });
    }

    function toggleTheme() {
        const next = currentTheme() === "light" ? "dark" : "light";
        writeStorage(THEME_KEY, next);
        applyTheme(next);
    }

    /* ---------- Toasts ---------- */
    function toastStack() {
        let stack = document.getElementById("toastStack");
        if (!stack) {
            stack = document.createElement("div");
            stack.id = "toastStack";
            stack.className = "toast-stack";
            stack.setAttribute("aria-live", "polite");
            document.body.appendChild(stack);
        }
        return stack;
    }

    function toast(message, type, options) {
        const opts = options || {};
        const item = document.createElement("div");
        item.className = `toast ${type || "info"}`;
        item.setAttribute("role", type === "err" ? "alert" : "status");
        const body = document.createElement("div");
        body.className = "grow";
        const text = document.createElement("span");
        text.textContent = String(message || "");
        body.appendChild(text);
        if (opts.href && opts.linkText) {
            body.appendChild(document.createTextNode(" "));
            const link = document.createElement("a");
            link.href = opts.href;
            link.textContent = opts.linkText;
            body.appendChild(link);
        }
        item.appendChild(body);
        item.addEventListener("click", (event) => {
            if (event.target.tagName !== "A") {
                item.remove();
            }
        });
        toastStack().appendChild(item);
        const timeout = opts.timeout || (type === "err" ? 9000 : 4500);
        window.setTimeout(() => item.remove(), timeout);
        return item;
    }

    /* Compatibility for scripts written against the old response modal. */
    window.showResponseModal = function (status, message) {
        const text = String(message || "").replace(/^(Success!|Error!)\s*/u, "");
        toast(text || message, status === "success" ? "ok" : "err");
    };
    window.closeResponseModal = function () {};

    /* ---------- Sheets ---------- */
    const openSheets = [];

    function sheetElement(target) {
        return typeof target === "string" ? document.getElementById(target) : target;
    }

    function openSheet(target) {
        const sheet = sheetElement(target);
        if (!sheet || sheet.classList.contains("is-open")) {
            return sheet;
        }
        sheet._returnFocus = document.activeElement;
        sheet.classList.add("is-open");
        sheet.setAttribute("aria-hidden", "false");
        openSheets.push(sheet);
        document.body.style.overflow = "hidden";
        const focusTarget = sheet.querySelector("[autofocus], input:not([type=hidden]), select, textarea, button:not([data-sheet-close])");
        if (focusTarget) {
            window.setTimeout(() => focusTarget.focus({ preventScroll: true }), 30);
        }
        return sheet;
    }

    function closeSheet(target) {
        const sheet = sheetElement(target) || openSheets[openSheets.length - 1];
        if (!sheet || !sheet.classList.contains("is-open")) {
            return;
        }
        sheet.classList.remove("is-open");
        sheet.setAttribute("aria-hidden", "true");
        const index = openSheets.indexOf(sheet);
        if (index >= 0) {
            openSheets.splice(index, 1);
        }
        if (!openSheets.length) {
            document.body.style.overflow = "";
        }
        if (sheet._returnFocus && typeof sheet._returnFocus.focus === "function") {
            sheet._returnFocus.focus({ preventScroll: true });
        }
        sheet.dispatchEvent(new CustomEvent("sheet:closed"));
    }

    function buildSheet({ title, subtitle, body, actions }) {
        const sheet = document.createElement("div");
        sheet.className = "sheet";
        sheet.setAttribute("role", "dialog");
        sheet.setAttribute("aria-modal", "true");
        sheet.innerHTML = `
            <div class="sheet-backdrop" data-sheet-close></div>
            <div class="sheet-panel">
                <div class="sheet-head">
                    <div class="grow">
                        <div class="sheet-title"></div>
                        <div class="sheet-sub"></div>
                    </div>
                    <button type="button" class="sheet-close" data-sheet-close aria-label="Close">×</button>
                </div>
                <div class="sheet-body"></div>
                <div class="sheet-actions"></div>
            </div>`;
        sheet.querySelector(".sheet-title").textContent = title || "";
        const sub = sheet.querySelector(".sheet-sub");
        if (subtitle) {
            sub.textContent = subtitle;
        } else {
            sub.remove();
        }
        const bodyNode = sheet.querySelector(".sheet-body");
        if (body instanceof Node) {
            bodyNode.appendChild(body);
        } else if (body) {
            bodyNode.textContent = body;
        } else {
            bodyNode.remove();
        }
        const actionsNode = sheet.querySelector(".sheet-actions");
        (actions || []).forEach((button) => actionsNode.appendChild(button));
        document.body.appendChild(sheet);
        return sheet;
    }

    function makeButton(label, className) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `btn ${className}`;
        button.textContent = label;
        return button;
    }

    /* Resolve true when the user confirms, false when they dismiss. */
    function confirmAction({ title, message, confirmText, danger }) {
        return new Promise((resolve) => {
            const cancel = makeButton("Cancel", "btn-secondary");
            const accept = makeButton(confirmText || "Confirm", danger ? "btn-danger" : "btn-primary");
            const sheet = buildSheet({ title, body: message, actions: [cancel, accept] });
            let result = false;
            cancel.setAttribute("data-sheet-close", "");
            accept.addEventListener("click", () => {
                result = true;
                closeSheet(sheet);
            });
            sheet.addEventListener("sheet:closed", () => {
                sheet.remove();
                resolve(result);
            }, { once: true });
            openSheet(sheet);
            window.setTimeout(() => accept.focus({ preventScroll: true }), 40);
        });
    }

    document.addEventListener("click", (event) => {
        const closer = event.target.closest("[data-sheet-close]");
        if (closer) {
            const sheet = closer.closest(".sheet");
            if (sheet) {
                event.preventDefault();
                closeSheet(sheet);
            }
            return;
        }
        const opener = event.target.closest("[data-sheet-open]");
        if (opener) {
            event.preventDefault();
            openSheet(opener.getAttribute("data-sheet-open"));
        }
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            if (lightboxNode && lightboxNode.classList.contains("is-open")) {
                closeLightbox();
            } else if (openSheets.length) {
                closeSheet(openSheets[openSheets.length - 1]);
            }
        }
    });

    /* ---------- Lightbox ---------- */
    let lightboxNode = null;

    function openLightbox(src, caption) {
        if (!lightboxNode) {
            lightboxNode = document.createElement("div");
            lightboxNode.className = "lightbox";
            lightboxNode.innerHTML = '<img alt=""><div class="lightbox-caption"></div>';
            lightboxNode.addEventListener("click", closeLightbox);
            document.body.appendChild(lightboxNode);
        }
        lightboxNode.querySelector("img").src = src;
        lightboxNode.querySelector(".lightbox-caption").textContent = caption || "";
        lightboxNode.classList.add("is-open");
        document.body.style.overflow = "hidden";
    }

    function closeLightbox() {
        if (lightboxNode) {
            lightboxNode.classList.remove("is-open");
            if (!openSheets.length) {
                document.body.style.overflow = "";
            }
        }
    }

    /* ---------- Requests ---------- */
    class ApiError extends Error {
        constructor(message, status, data) {
            super(message);
            this.status = status;
            this.data = data;
            this.handled = false;
        }
    }

    function loginUrl() {
        const next = window.location.pathname + window.location.search;
        return `/auth/login?next=${encodeURIComponent(next)}`;
    }

    async function api(url, options) {
        const opts = options || {};
        const headers = new Headers(opts.headers || {});
        headers.set("Accept", "application/json");
        const init = { method: opts.method || "GET", headers };
        if (opts.json !== undefined) {
            headers.set("Content-Type", "application/json");
            init.body = JSON.stringify(opts.json);
        } else if (opts.body !== undefined) {
            init.body = opts.body;
        }

        let response;
        try {
            response = await fetch(url, init);
        } catch (error) {
            const failure = new ApiError("Could not reach the device.", 0, null);
            if (!opts.quiet) {
                failure.handled = true;
                toast(failure.message, "err");
            }
            throw failure;
        }

        let data = null;
        const type = response.headers.get("Content-Type") || "";
        if (type.includes("application/json")) {
            try {
                data = await response.json();
            } catch (error) {
                data = null;
            }
        }
        if (response.ok) {
            return data || {};
        }

        const message = (data && (data.error || data.message)) || `Request failed (${response.status})`;
        const failure = new ApiError(message, response.status, data);
        if (!opts.quiet) {
            if (response.status === 401) {
                failure.handled = true;
                toast("Sign in to make changes.", "warn", { href: loginUrl(), linkText: "Sign in" });
            } else if (response.status === 403 && data && data.error_code === "csrf_failed") {
                failure.handled = true;
                toast("Your session changed. Reload the page and try again.", "err");
            }
        }
        throw failure;
    }

    function reportError(error) {
        if (error && error.handled) {
            return;
        }
        toast((error && error.message) || "Something went wrong.", "err");
    }

    /* Mark a button busy while an async task runs. */
    async function withBusy(button, task) {
        if (button) {
            button.classList.add("is-busy");
            button.disabled = true;
        }
        try {
            return await task();
        } finally {
            if (button) {
                button.classList.remove("is-busy");
                button.disabled = false;
            }
        }
    }

    /* Call `tick` now and every `intervalMs` while the page is visible. */
    function poll(tick, intervalMs) {
        let timer = null;
        let stopped = false;

        async function run() {
            timer = null;
            if (stopped || document.hidden) {
                return;
            }
            try {
                await tick();
            } catch (error) {
                /* the next tick retries */
            }
            if (!stopped && !document.hidden) {
                timer = window.setTimeout(run, intervalMs);
            }
        }

        function onVisibility() {
            if (!document.hidden && !timer && !stopped) {
                run();
            }
        }

        document.addEventListener("visibilitychange", onVisibility);
        run();
        return function stop() {
            stopped = true;
            if (timer) {
                window.clearTimeout(timer);
            }
            document.removeEventListener("visibilitychange", onVisibility);
        };
    }

    /* Short English relative time; i18n.js translates the patterns. */
    function relativeTime(iso) {
        if (!iso) {
            return "";
        }
        const then = new Date(iso);
        if (Number.isNaN(then.getTime())) {
            return "";
        }
        const seconds = Math.round((Date.now() - then.getTime()) / 1000);
        if (seconds < 0) {
            const ahead = -seconds;
            if (ahead < 90) {
                return "in a moment";
            }
            if (ahead < 3600) {
                return `in ${Math.round(ahead / 60)} min`;
            }
            return `in ${Math.round(ahead / 3600)} h`;
        }
        if (seconds < 90) {
            return "just now";
        }
        if (seconds < 3600) {
            return `${Math.round(seconds / 60)} min ago`;
        }
        if (seconds < 86400) {
            return `${Math.round(seconds / 3600)} h ago`;
        }
        return `${Math.round(seconds / 86400)} d ago`;
    }

    function updateRelativeTimes(root) {
        (root || document).querySelectorAll("[data-relative-time]").forEach((node) => {
            const label = relativeTime(node.getAttribute("data-relative-time"));
            const prefix = node.getAttribute("data-relative-prefix");
            const text = prefix ? `${prefix} ${label}` : label;
            if (node.textContent !== text) {
                node.textContent = text;
            }
        });
    }

    /* ---------- Header controls ---------- */
    function wireHeader() {
        document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
            button.addEventListener("click", toggleTheme);
        });

        const language = document.getElementById("languageToggle");
        if (language && !language.dataset.wired) {
            language.dataset.wired = "true";
            language.addEventListener("click", () => {
                const i18n = window.InkyPiI18n;
                if (i18n) {
                    i18n.setLanguage(i18n.getLanguage() === "zh" ? "en" : "zh");
                }
            });
        }

        document.querySelectorAll("[data-action='logout']").forEach((button) => {
            button.addEventListener("click", async () => {
                const confirmed = await confirmAction({
                    title: "Sign out?",
                    message: "You can still view pages, but changes need a sign-in.",
                    confirmText: "Sign out",
                });
                if (!confirmed) {
                    return;
                }
                try {
                    await api("/auth/logout", { method: "POST", json: {} });
                    window.location.reload();
                } catch (error) {
                    reportError(error);
                }
            });
        });
    }

    applyTheme(readStorage(THEME_KEY) === "light" ? "light" : "dark");

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", wireHeader);
    } else {
        wireHeader();
    }
    window.setInterval(() => updateRelativeTimes(), 30000);

    window.InkyUI = {
        api,
        ApiError,
        closeLightbox,
        closeSheet,
        confirm: confirmAction,
        loginUrl,
        openLightbox,
        openSheet,
        poll,
        relativeTime,
        reportError,
        toast,
        updateRelativeTimes,
        withBusy,
    };
})();
