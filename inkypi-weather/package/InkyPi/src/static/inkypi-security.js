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

})();
