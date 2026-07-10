(function () {
    "use strict";

    const safeMethods = new Set(["GET", "HEAD", "OPTIONS"]);
    const tokenMeta = document.querySelector('meta[name="inkypi-csrf-token"]');
    const csrfToken = tokenMeta ? tokenMeta.getAttribute("content") : "";
    const originalFetch = window.fetch.bind(window);

    window.fetch = function (input, init) {
        const options = Object.assign({}, init || {});
        const inputMethod = input instanceof Request ? input.method : "GET";
        const method = String(options.method || inputMethod || "GET").toUpperCase();
        const rawUrl = input instanceof Request ? input.url : input;
        const target = new URL(rawUrl, window.location.href);
        if (target.origin === window.location.origin && !safeMethods.has(method) && csrfToken) {
            const headers = new Headers(
                options.headers || (input instanceof Request ? input.headers : undefined)
            );
            if (!headers.has("X-CSRF-Token")) {
                headers.set("X-CSRF-Token", csrfToken);
            }
            options.headers = headers;
        }
        return originalFetch(input, options);
    };

    const warningMeta = document.querySelector('meta[name="inkypi-plain-http"]');
    if (warningMeta && warningMeta.getAttribute("content") === "true") {
        document.addEventListener("DOMContentLoaded", function () {
            if (document.getElementById("inkypi-http-warning")) {
                return;
            }
            const warning = document.createElement("div");
            warning.id = "inkypi-http-warning";
            warning.setAttribute("role", "status");
            warning.textContent = "Connection is not encrypted. Administrative changes should use a trusted local network or HTTPS.";
            warning.style.cssText = "position:sticky;top:0;z-index:10000;padding:8px 14px;background:#8f3f22;color:#fff;font:600 13px/1.4 system-ui,sans-serif;text-align:center";
            document.body.insertBefore(warning, document.body.firstChild);
        });
    }
})();
