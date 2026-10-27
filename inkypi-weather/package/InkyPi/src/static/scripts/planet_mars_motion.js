/* Planet Now's real NASA photographs: browser-only motion over an exact cached frame. */
(function () {
    "use strict";
    const active = new WeakMap();
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    function stop(image) {
        const old = active.get(image);
        if (old) old.dispose();
    }
    async function mount(image, endpoint) {
        stop(image);
        if (!image || !endpoint || reduced.matches) return;
        const controller = new AbortController();
        let canvas, button, observer, frame = 0, last = null, elapsed = 0, paused = false, dead = false;
        const state = {dispose() {
            dead = true; controller.abort(); cancelAnimationFrame(frame);
            if (canvas) canvas.remove();
            if (button) button.remove();
            if (observer) observer.disconnect();
            reduced.removeEventListener("change", onReduce);
            document.removeEventListener("visibilitychange", onVisibility);
            if (active.get(image) === state) active.delete(image);
        }};
        const onReduce = () => { if (reduced.matches) state.dispose(); };
        const onVisibility = () => { last = null; };
        active.set(image, state);
        try {
            const url = new URL(endpoint, location.href);
            if (url.origin !== location.origin) throw Error("local motion only");
            url.searchParams.set("motion", "mars");
            const response = await fetch(url, {signal: controller.signal, cache: "no-store"});
            if (!response.ok) throw Error("motion unavailable");
            const data = (await response.json()).motion;
            if (!data || data.schema !== 1 || !Array.isArray(data.slides) || !data.slides.length || data.slides.length > 3
                || JSON.stringify(data.box) !== "[320,304,432,468]" || JSON.stringify(data.size) !== "[800,480]") {
                throw Error("no matching motion");
            }
            const decode = async (uri, mime) => {
                if (typeof uri !== "string" || !uri.startsWith(`data:image/${mime};base64,`) || uri.length > 1500000) {
                    throw Error("invalid cached photo");
                }
                const item = new Image(); item.src = uri; await item.decode(); return item;
            };
            const base = await decode(data.base, "png");
            const slides = await Promise.all(data.slides.map(async s => ({
                photo: await decode(s.photo, "jpeg"), facts: await decode(s.facts, "png"),
            })));
            if (dead || reduced.matches) return state.dispose();
            const parent = image.parentElement;
            canvas = document.createElement("canvas"); canvas.width = 800; canvas.height = 480;
            canvas.setAttribute("aria-hidden", "true");
            canvas.className = "planet-mars-motion";
            Object.assign(canvas.style, {position: "absolute", pointerEvents: "none", zIndex: "2"});
            parent.appendChild(canvas);
            const context = canvas.getContext("2d");
            if (!context) throw Error("canvas unavailable");
            const resize = () => {
                const box = image.getBoundingClientRect(), origin = parent.getBoundingClientRect();
                const scale = Math.min(box.width / 800, box.height / 480);
                Object.assign(canvas.style, {width: `${800 * scale}px`, height: `${480 * scale}px`,
                    left: `${box.left - origin.left + (box.width - 800 * scale) / 2}px`,
                    top: `${box.top - origin.top + (box.height - 480 * scale) / 2}px`});
            };
            observer = new ResizeObserver(resize); observer.observe(image); resize();
            const controls = parent.classList.contains("lightbox") ? parent.querySelector(".lightbox-caption")
                : image.closest(".hero")?.querySelector(".hero-actions");
            if (controls) {
                button = document.createElement("button"); button.type = "button";
                button.className = "btn btn-secondary btn-sm"; button.textContent = "暂停照片";
                button.setAttribute("aria-label", "暂停火星照片轮换");
                button.addEventListener("click", event => {
                    event.stopPropagation(); paused = !paused; last = null;
                    button.textContent = paused ? "播放照片" : "暂停照片";
                    button.setAttribute("aria-label", paused ? "播放火星照片轮换" : "暂停火星照片轮换");
                });
                controls.appendChild(button);
            }
            function paintPhoto(index, progress, alpha) {
                const slide = slides[index], width = 112, height = 164;
                const fit = Math.max(width / slide.photo.naturalWidth, height / slide.photo.naturalHeight);
                const zoom = 1.04 + 0.035 * progress;
                const pw = slide.photo.naturalWidth * fit * zoom, ph = slide.photo.naturalHeight * fit * zoom;
                const maxShift = Math.min(24, Math.max(0, (pw - width) / 2 - 2));
                const shift = Math.sin((progress - 0.5) * Math.PI) * maxShift;
                context.save(); context.globalAlpha = alpha;
                context.translate(320, 304); context.beginPath(); context.roundRect(0, 0, width, height, 6); context.clip();
                context.drawImage(slide.photo, (width - pw) / 2 + shift, (height - ph) / 2, pw, ph);
                const top = context.createLinearGradient(0, 0, 0, 40);
                top.addColorStop(0, "rgba(8,10,14,.85)"); top.addColorStop(1, "rgba(8,10,14,0)");
                context.fillStyle = top; context.fillRect(0, 0, width, 40);
                const bottom = context.createLinearGradient(0, 102, 0, 164);
                bottom.addColorStop(0, "rgba(8,10,14,0)"); bottom.addColorStop(1, "rgba(8,10,14,.9)");
                context.fillStyle = bottom; context.fillRect(0, 102, width, 62);
                context.drawImage(slide.facts, 0, 0, width, height); context.restore();
            }
            function paint() {
                context.clearRect(0, 0, 800, 480); context.drawImage(base, 0, 0);
                const index = Math.floor(elapsed / 10000) % slides.length, local = elapsed % 10000;
                paintPhoto(index, local / 10000, 1);
                if (slides.length > 1 && local > 9100) paintPhoto((index + 1) % slides.length, 0, (local - 9100) / 900);
            }
            function tick(now) {
                if (dead) return;
                if (!image.isConnected || (parent.classList.contains("lightbox") && !parent.classList.contains("is-open"))) {
                    state.dispose(); return;
                }
                if (!document.hidden && !paused && last !== null) {
                    elapsed += Math.min(100, now - last); paint();
                }
                last = now; frame = requestAnimationFrame(tick);
            }
            reduced.addEventListener("change", onReduce);
            document.addEventListener("visibilitychange", onVisibility);
            paint(); frame = requestAnimationFrame(tick);
        } catch (_) {
            // Missing/expired assets and cancelled fetches keep the original static picture.
            state.dispose();
        }
    }
    window.PlanetMarsMotion = {mount, stop};
})();
