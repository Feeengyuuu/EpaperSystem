/* Now Playing page: live display card, playlist tabs, instance cards and
 * playlist management. All changes update the page in place. */
(function () {
    "use strict";

    const ui = window.InkyUI;
    const hero = document.getElementById("hero");
    const heroImage = hero.querySelector(".hero-media img");

    let nowPlaying = {};
    try {
        nowPlaying = JSON.parse(hero.dataset.nowPlaying || "{}");
    } catch (error) {
        nowPlaying = {};
    }

    /* ---------- Helpers ---------- */
    const cards = () => Array.from(document.querySelectorAll("[data-instance-card]"));

    function cardIdentity(card) {
        return {
            playlist_name: card.dataset.playlist,
            plugin_id: card.dataset.pluginId,
            plugin_instance: card.dataset.instance,
        };
    }

    function describeRefresh(refresh, beforeDisplay) {
        if (beforeDisplay) {
            return "Updates before each display";
        }
        const interval = Number(refresh && refresh.interval);
        if (interval > 0) {
            for (const [unit, seconds] of [["day", 86400], ["hour", 3600], ["minute", 60]]) {
                if (interval >= seconds && interval % seconds === 0) {
                    const count = interval / seconds;
                    return count === 1 ? `Every ${unit}` : `Every ${count} ${unit}s`;
                }
            }
            return `Every ${Math.max(1, Math.round(interval / 60))} minutes`;
        }
        if (refresh && refresh.scheduled) {
            return `Daily at ${refresh.scheduled}`;
        }
        return "Manual refresh";
    }

    function translate(text) {
        const i18n = window.InkyPiI18n;
        return i18n ? i18n.translateValue(text, i18n.getLanguage()) : text;
    }

    function reducedMotion() {
        return typeof window.matchMedia === "function"
            && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    }

    /* Replay a one-shot CSS highlight such as `is-fresh`. */
    function flash(node, className) {
        if (reducedMotion()) {
            return;
        }
        node.classList.remove(className);
        void node.offsetWidth;
        node.classList.add(className);
        window.setTimeout(() => node.classList.remove(className), 1800);
    }

    /* ---------- Display card ---------- */
    let imageTag = null;
    let objectUrl = null;

    async function refreshHeroImage() {
        const headers = imageTag ? { "If-None-Match": imageTag } : {};
        const response = await fetch("/api/current_image", { headers, cache: "no-cache" });
        if (response.status === 304) {
            return false;
        }
        if (!response.ok) {
            hero.classList.add("is-empty");
            hero.querySelector("[data-hero-empty]").hidden = false;
            return false;
        }
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const previous = objectUrl;
        objectUrl = url;
        const changed = imageTag !== null;
        heroImage.addEventListener("load", () => {
            if (previous) {
                URL.revokeObjectURL(previous);
            }
            if (changed) {
                flash(hero, "is-fresh");
            }
        }, { once: true });
        heroImage.src = url;
        imageTag = response.headers.get("ETag");
        hero.classList.remove("is-empty");
        hero.querySelector("[data-hero-empty]").hidden = true;
        return changed;
    }

    /* ---------- Browser tab: title and favicon follow the display ---------- */
    let unseenChange = false;

    function updateTabTitle() {
        const device = hero.dataset.deviceName || "InkyPi";
        const instance = nowPlaying.plugin_instance;
        let title = `${translate("Now Playing")} · ${device}`;
        if (instance) {
            const plugin = nowPlaying.display_name && nowPlaying.display_name !== instance
                ? translate(nowPlaying.display_name)
                : device;
            title = `▶ ${instance} · ${plugin}`;
        }
        document.title = unseenChange ? `● ${title}` : title;
    }

    /* Draw what the e-paper shows as the tab icon, with a live dot. */
    function paintFavicon() {
        if (!heroImage.naturalWidth || typeof document.createElement !== "function") {
            return;
        }
        const size = 64;
        const canvas = document.createElement("canvas");
        canvas.width = size;
        canvas.height = size;
        const context = canvas.getContext && canvas.getContext("2d");
        if (!context) {
            return;
        }
        context.beginPath();
        if (context.roundRect) {
            context.roundRect(0, 0, size, size, 14);
        } else {
            context.rect(0, 0, size, size);
        }
        context.clip();
        const scale = Math.max(size / heroImage.naturalWidth, size / heroImage.naturalHeight);
        const width = heroImage.naturalWidth * scale;
        const height = heroImage.naturalHeight * scale;
        context.drawImage(heroImage, (size - width) / 2, (size - height) / 2, width, height);
        const styles = window.getComputedStyle(document.documentElement);
        context.fillStyle = styles.getPropertyValue("--c-bg").trim() || "#111316";
        context.beginPath();
        context.arc(50, 50, 12, 0, Math.PI * 2);
        context.fill();
        context.fillStyle = styles.getPropertyValue("--c-accent").trim() || "#1abc9c";
        context.beginPath();
        context.arc(50, 50, 8, 0, Math.PI * 2);
        context.fill();
        let link = document.getElementById("liveFavicon");
        if (!link) {
            document.querySelectorAll('link[rel~="icon"]').forEach((node) => node.remove());
            link = document.createElement("link");
            link.id = "liveFavicon";
            link.rel = "icon";
            link.type = "image/png";
            document.head.appendChild(link);
        }
        try {
            link.href = canvas.toDataURL("image/png");
        } catch (error) {
            /* a tainted canvas keeps the previous icon */
        }
    }

    heroImage.addEventListener("load", paintFavicon);
    if (heroImage.complete && heroImage.naturalWidth) {
        paintFavicon();
    }

    document.addEventListener("visibilitychange", () => {
        if (!document.hidden && unseenChange) {
            unseenChange = false;
            updateTabTitle();
        }
    });
    document.getElementById("languageToggle").addEventListener("click", () => {
        window.setTimeout(updateTabTitle, 0);
    });

    function renderNowPlaying() {
        const title = nowPlaying.plugin_instance || nowPlaying.display_name || "—";
        document.getElementById("heroTitle").textContent = title;
        const plugin = document.getElementById("heroPlugin");
        plugin.textContent = nowPlaying.display_name || "";
        plugin.hidden = !nowPlaying.plugin_instance || nowPlaying.display_name === nowPlaying.plugin_instance;
        const playlist = document.getElementById("heroPlaylist");
        playlist.textContent = nowPlaying.playlist || "";
        playlist.hidden = !nowPlaying.playlist;
        const updated = document.getElementById("heroUpdated");
        if (nowPlaying.refresh_time) {
            updated.setAttribute("data-relative-time", nowPlaying.refresh_time);
        } else {
            updated.removeAttribute("data-relative-time");
            updated.textContent = "";
        }
        document.getElementById("heroRefresh").disabled = !nowPlaying.plugin_instance;
        cards().forEach((card) => {
            const current = card.dataset.playlist === nowPlaying.playlist
                && card.dataset.instance === nowPlaying.plugin_instance;
            card.classList.toggle("is-current", current);
            card.querySelector("[data-now-flag]").hidden = !current;
        });
        ui.updateRelativeTimes(hero);
        updateTabTitle();
    }

    async function loadNowPlaying() {
        try {
            nowPlaying = await ui.api("/api/now-playing", { quiet: true });
            renderNowPlaying();
        } catch (error) {
            /* keep the last known state */
        }
    }

    /* ---------- Live sync: display, cards and tab update in place ---------- */
    function displayKey(state) {
        return [state.plugin_id, state.playlist, state.plugin_instance, state.refresh_time].join("\u0000");
    }

    function cardKey(playlist, pluginId, name) {
        return `${playlist}\u0000${pluginId}\u0000${name}`;
    }

    function timeValue(iso) {
        const value = Date.parse(iso || "");
        return Number.isNaN(value) ? 0 : value;
    }

    function setLive(online) {
        const badge = document.getElementById("heroLive");
        if (!badge || badge.dataset.state === (online ? "live" : "offline")) {
            return;
        }
        badge.dataset.state = online ? "live" : "offline";
        const label = badge.querySelector("[data-live-label]");
        if (label) {
            label.textContent = online ? "Live" : "Reconnecting…";
        }
    }

    function setCardStamp(card, iso) {
        card.dataset.renderedAt = iso;
        const wrap = card.querySelector("[data-refresh-stamp]");
        if (wrap) {
            wrap.hidden = false;
        }
        const stamp = card.querySelector("[data-relative-time]");
        if (stamp) {
            stamp.setAttribute("data-relative-time", iso);
            ui.updateRelativeTimes(card);
        }
    }

    /* Load the new render off-screen first so the card never flashes empty. */
    function showNewRender(card, renderedAt) {
        setCardStamp(card, renderedAt);
        const image = card.querySelector("[data-preview-image]");
        const url = `${card.dataset.imageUrl}?v=${encodeURIComponent(renderedAt)}`;
        const panel = card.closest("[data-playlist-panel]");
        if ((panel && panel.hidden) || typeof window.Image !== "function") {
            image.src = url;
            return;
        }
        const loader = new window.Image();
        loader.onload = () => {
            image.src = url;
            flash(card, "is-fresh");
        };
        loader.src = url;
    }

    function applyPreviews(state) {
        const latest = new Map((state.instances || []).map((row) => [
            cardKey(row.playlist, row.plugin_id, row.name),
            row.latest_refresh_time,
        ]));
        cards().forEach((card) => {
            const renderedAt = latest.get(cardKey(card.dataset.playlist, card.dataset.pluginId, card.dataset.instance));
            if (renderedAt && timeValue(renderedAt) > timeValue(card.dataset.renderedAt)) {
                showNewRender(card, renderedAt);
            }
        });
        const current = state.now_playing;
        if (current && displayKey(current) !== displayKey(nowPlaying)) {
            const switched = current.plugin_instance !== nowPlaying.plugin_instance
                || current.playlist !== nowPlaying.playlist;
            nowPlaying = current;
            if (switched && document.hidden) {
                unseenChange = true;
            }
            renderNowPlaying();
            if (switched) {
                flash(document.getElementById("heroTitle"), "is-fresh");
            }
        }
    }

    async function syncLive() {
        const [image, previews] = await Promise.allSettled([
            refreshHeroImage(),
            ui.api("/api/now-playing/previews", { quiet: true }),
        ]);
        setLive(image.status === "fulfilled" || previews.status === "fulfilled");
        if (previews.status === "fulfilled") {
            applyPreviews(previews.value);
        }
    }

    // Keep the tab title and icon current in the background too, at a slower pace.
    ui.poll(syncLive, 10000, { hiddenIntervalMs: 60000 });
    updateTabTitle();

    function heroCaption() {
        return [nowPlaying.plugin_instance || nowPlaying.display_name, nowPlaying.playlist].filter(Boolean).join(" · ");
    }

    document.getElementById("heroMedia").addEventListener("click", () => ui.openLightbox(heroImage.src, heroCaption()));
    document.getElementById("heroExpand").addEventListener("click", () => ui.openLightbox(heroImage.src, heroCaption()));

    document.getElementById("heroRefresh").addEventListener("click", (event) => {
        const button = event.currentTarget;
        const card = cards().find((item) => item.dataset.playlist === nowPlaying.playlist
            && item.dataset.instance === nowPlaying.plugin_instance);
        if (!card) {
            ui.toast("The current item is not in a playlist, so it cannot be refreshed here.", "warn");
            return;
        }
        ui.withBusy(button, () => refreshThenDisplay(card));
    });

    /* ---------- Card images ---------- */
    function markImageMissing(card) {
        card.querySelector("[data-preview-image]").hidden = true;
        card.querySelector("[data-preview-empty]").hidden = false;
    }

    function reloadCardImage(card) {
        const image = card.querySelector("[data-preview-image]");
        image.hidden = false;
        card.querySelector("[data-preview-empty]").hidden = true;
        image.src = `${card.dataset.imageUrl}?v=${Date.now()}`;
    }

    cards().forEach((card) => {
        const image = card.querySelector("[data-preview-image]");
        image.addEventListener("error", () => markImageMissing(card));
        image.addEventListener("load", () => {
            image.hidden = false;
            card.querySelector("[data-preview-empty]").hidden = true;
        });
        if (image.complete && image.naturalWidth === 0 && image.getAttribute("src")) {
            markImageMissing(card);
        }
    });

    /* ---------- Tabs ---------- */
    function selectPlaylist(name) {
        document.querySelectorAll("[data-playlist-tab]").forEach((tab) => {
            const active = tab.dataset.playlistTab === name;
            tab.classList.toggle("is-active", active);
            tab.setAttribute("aria-selected", active ? "true" : "false");
        });
        document.querySelectorAll("[data-playlist-panel]").forEach((panel) => {
            panel.hidden = panel.dataset.playlistPanel !== name;
        });
        const url = new URL(window.location.href);
        url.searchParams.set("playlist", name);
        window.history.replaceState(null, "", url);
    }

    function selectedPanel() {
        return document.querySelector("[data-playlist-panel]:not([hidden])");
    }

    document.querySelectorAll("[data-playlist-tab]").forEach((tab) => {
        tab.addEventListener("click", () => selectPlaylist(tab.dataset.playlistTab));
    });

    /* ---------- Instance actions ---------- */
    async function displayCard(card) {
        card.classList.add("is-working");
        try {
            const body = await ui.api("/display_plugin_instance", { method: "POST", json: cardIdentity(card) });
            ui.toast("Sending to the display…", "info");
            const job = await ui.waitForJob(body.job_id);
            if (!job || job.status !== "completed") {
                ui.toast(ui.jobFailureMessage(job), "err");
                return;
            }
            await refreshHeroImage();
            await loadNowPlaying();
            ui.toast("Now on the display.", "ok");
        } finally {
            card.classList.remove("is-working");
        }
    }

    async function refreshCardData(card) {
        card.classList.add("is-working");
        try {
            const body = await ui.api("/refresh_plugin_instance", { method: "POST", json: cardIdentity(card) });
            ui.toast("Refreshing data…", "info");
            const job = await ui.waitForJob(body.job_id);
            if (!job || job.status !== "completed") {
                ui.toast(ui.jobFailureMessage(job), "err");
                return false;
            }
            reloadCardImage(card);
            setCardStamp(card, new Date().toISOString());
            ui.toast("Data refreshed.", "ok");
            return true;
        } finally {
            card.classList.remove("is-working");
        }
    }

    async function refreshThenDisplay(card) {
        const refreshed = await refreshCardData(card);
        if (refreshed) {
            await displayCard(card);
        }
    }

    async function deleteCard(card) {
        const confirmed = await ui.confirm({
            title: "Delete this item?",
            message: `“${card.dataset.instance}” will be removed from ${card.dataset.playlist}. Its settings are deleted too.`,
            confirmText: "Delete",
            danger: true,
        });
        if (!confirmed) {
            return;
        }
        try {
            await ui.api("/delete_plugin_instance", { method: "POST", json: cardIdentity(card) });
        } catch (error) {
            ui.reportError(error);
            return;
        }
        const panel = card.closest("[data-playlist-panel]");
        card.classList.add("is-removing");
        window.setTimeout(() => {
            card.remove();
            updatePanelCount(panel);
        }, 200);
        ui.toast("Deleted.", "ok");
    }

    function updatePanelCount(panel) {
        const count = panel.querySelectorAll("[data-instance-card]").length;
        panel.querySelector("[data-preview-grid]").hidden = count === 0;
        panel.querySelector("[data-empty-playlist]").hidden = count !== 0;
        const tab = Array.from(document.querySelectorAll("[data-playlist-tab]"))
            .find((item) => item.dataset.playlistTab === panel.dataset.playlistPanel);
        if (tab) {
            tab.querySelector("[data-tab-count]").textContent = String(count);
        }
    }

    /* ---------- Instance sheet ---------- */
    let sheetCard = null;
    const refreshManager = window.createRefreshSettingsManager
        ? window.createRefreshSettingsManager("refreshSettingsModal", "modal")
        : null;

    function openInstanceSheet(card) {
        sheetCard = card;
        document.getElementById("instanceSheetTitle").textContent = card.dataset.instance;
        document.getElementById("instanceSheetSub").textContent = card.dataset.displayName;
        document.getElementById("instanceEditLink").href = card.dataset.editUrl;
        const beforeDisplay = card.dataset.beforeDisplay === "true";
        const scheduleButton = document.querySelector("[data-menu='refresh-schedule']");
        scheduleButton.disabled = beforeDisplay;
        document.getElementById("instanceScheduleHint").textContent = beforeDisplay
            ? "Updates before each display"
            : describeRefresh(JSON.parse(card.dataset.refresh || "{}"), false);
        ui.openSheet("instanceSheet");
    }

    function openRefreshSheet(card) {
        if (!refreshManager || !refreshManager.initialized) {
            ui.toast("Refresh settings are not available.", "err");
            return;
        }
        let refresh = {};
        try {
            refresh = JSON.parse(card.dataset.refresh || "{}");
        } catch (error) {
            refresh = {};
        }
        refreshManager.currentData = { card };
        refreshManager.prepopulate(refresh);
        document.getElementById("refreshSheetSub").textContent = card.dataset.instance;
        ui.openSheet("refreshSettingsModal");
    }

    document.getElementById("refreshSave").addEventListener("click", (event) => {
        const card = refreshManager && refreshManager.currentData && refreshManager.currentData.card;
        if (!card) {
            return;
        }
        const formData = refreshManager.getFormData();
        const check = refreshManager.validate(formData);
        if (!check.valid) {
            ui.toast(check.error, "err");
            return;
        }
        ui.withBusy(event.currentTarget, async () => {
            const data = new FormData();
            data.append("plugin_id", card.dataset.pluginId);
            data.append("refresh_settings", JSON.stringify(formData));
            let result;
            try {
                result = await ui.api(`/update_plugin_instance/${encodeURIComponent(card.dataset.instance)}`, { method: "PUT", body: data });
            } catch (error) {
                ui.reportError(error);
                return;
            }
            // Plugin policy may normalize the requested schedule on the server.
            const refresh = result.refresh;
            if (!refresh || typeof refresh !== "object" || Array.isArray(refresh)) {
                window.location.reload();
                return;
            }
            card.dataset.refresh = JSON.stringify(refresh);
            card.querySelector("[data-refresh-label]").textContent = describeRefresh(refresh, false);
            ui.closeSheet("refreshSettingsModal");
            ui.toast("Refresh schedule saved.", "ok");
        });
    });

    document.querySelector("#instanceSheet .sheet-menu").addEventListener("click", (event) => {
        const item = event.target.closest("[data-menu]");
        if (!item || !sheetCard) {
            return;
        }
        const card = sheetCard;
        const action = item.dataset.menu;
        ui.closeSheet("instanceSheet");
        if (action === "refresh-data") {
            refreshCardData(card).catch(ui.reportError);
        } else if (action === "refresh-schedule") {
            openRefreshSheet(card);
        } else if (action === "delete") {
            deleteCard(card);
        }
    });

    document.addEventListener("click", (event) => {
        const button = event.target.closest("[data-instance-card] [data-action]");
        if (!button) {
            return;
        }
        const card = button.closest("[data-instance-card]");
        const action = button.dataset.action;
        if (action === "preview") {
            const image = card.querySelector("[data-preview-image]");
            if (!image.hidden) {
                ui.openLightbox(image.currentSrc || image.src, `${card.dataset.instance} · ${card.dataset.displayName}`);
            }
        } else if (action === "display") {
            ui.withBusy(button, () => displayCard(card)).catch(ui.reportError);
        } else if (action === "more") {
            openInstanceSheet(card);
        }
    });

    /* ---------- Runtime status pills ---------- */
    function applyStatus(rows) {
        const byKey = new Map(rows.map((row) => [`${row.plugin_id}\u0000${row.name}`, row]));
        cards().forEach((card) => {
            const pill = card.querySelector("[data-status-pill]");
            const row = byKey.get(`${card.dataset.pluginId}\u0000${card.dataset.instance}`);
            pill.className = "status-pill";
            pill.removeAttribute("title");
            if (!row || (!row.issue && !row.last_success_at)) {
                pill.hidden = true;
                return;
            }
            pill.hidden = false;
            if (!row.issue) {
                pill.classList.add("ok");
                pill.textContent = "Healthy";
            } else if (row.issue === "retry_wait") {
                pill.classList.add("warn");
                pill.textContent = "Waiting to retry";
                if (row.next_retry_at) {
                    pill.title = new Date(row.next_retry_at).toLocaleString();
                }
            } else {
                pill.classList.add("err");
                pill.textContent = row.cache_present ? "Failed · showing cache" : "Failed";
                pill.title = row.issue;
            }
        });
    }

    let stopStatus = null;
    stopStatus = ui.poll(async () => {
        try {
            const body = await ui.api("/api/runtime-status", { quiet: true });
            applyStatus((body.runtime && body.runtime.instances) || []);
        } catch (error) {
            if (error.status === 401 || error.status === 403) {
                if (stopStatus) {
                    stopStatus();
                }
            }
        }
    }, 30000);

    /* ---------- Playlist sheet ---------- */
    const playlistForm = {
        name: document.getElementById("playlistName"),
        start: document.getElementById("playlistStart"),
        end: document.getElementById("playlistEnd"),
        title: document.getElementById("playlistSheetTitle"),
        remove: document.getElementById("playlistDelete"),
        save: document.getElementById("playlistSave"),
    };
    let editingPlaylist = null;

    (function fillTimes() {
        const options = [];
        for (let hour = 0; hour < 24; hour += 1) {
            for (const minute of [0, 15, 30, 45]) {
                options.push(`${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`);
            }
        }
        options.push("24:00");
        [playlistForm.start, playlistForm.end].forEach((select) => {
            options.forEach((value) => select.appendChild(new Option(value, value)));
        });
    })();

    function ensureOption(select, value) {
        if (value && !Array.from(select.options).some((option) => option.value === value)) {
            select.appendChild(new Option(value, value));
        }
        select.value = value;
    }

    function openPlaylistSheet(panel) {
        editingPlaylist = panel ? panel.dataset.playlistPanel : null;
        playlistForm.title.textContent = panel ? "Playlist settings" : "New playlist";
        playlistForm.name.value = editingPlaylist || "";
        ensureOption(playlistForm.start, panel ? panel.dataset.start : "00:00");
        ensureOption(playlistForm.end, panel ? panel.dataset.end : "24:00");
        playlistForm.remove.hidden = !panel;
        ui.openSheet("playlistSheet");
    }

    document.querySelectorAll("[data-action='new-playlist']").forEach((button) => {
        button.addEventListener("click", () => openPlaylistSheet(null));
    });
    document.querySelectorAll("[data-action='edit-playlist']").forEach((button) => {
        button.addEventListener("click", () => {
            const panel = selectedPanel();
            if (panel) {
                openPlaylistSheet(panel);
            }
        });
    });

    function reloadWith(name) {
        const url = new URL(window.location.href);
        if (name) {
            url.searchParams.set("playlist", name);
        } else {
            url.searchParams.delete("playlist");
        }
        window.location.assign(url);
    }

    document.getElementById("playlistForm").addEventListener("submit", (event) => {
        event.preventDefault();
        playlistForm.save.click();
    });

    playlistForm.save.addEventListener("click", (event) => {
        const name = playlistForm.name.value.trim();
        if (!name) {
            ui.toast("Enter a playlist name.", "err");
            playlistForm.name.focus();
            return;
        }
        const times = { start_time: playlistForm.start.value, end_time: playlistForm.end.value };
        ui.withBusy(event.currentTarget, async () => {
            try {
                if (editingPlaylist) {
                    await ui.api(`/update_playlist/${encodeURIComponent(editingPlaylist)}`, {
                        method: "PUT",
                        json: { new_name: name, ...times },
                    });
                } else {
                    await ui.api("/create_playlist", { method: "POST", json: { playlist_name: name, ...times } });
                }
            } catch (error) {
                ui.reportError(error);
                return;
            }
            reloadWith(name);
        });
    });

    playlistForm.remove.addEventListener("click", async () => {
        if (!editingPlaylist) {
            return;
        }
        const name = editingPlaylist;
        ui.closeSheet("playlistSheet");
        const confirmed = await ui.confirm({
            title: "Delete this playlist?",
            message: `“${name}” and every item in it will be deleted.`,
            confirmText: "Delete playlist",
            danger: true,
        });
        if (!confirmed) {
            return;
        }
        try {
            await ui.api(`/delete_playlist/${encodeURIComponent(name)}`, { method: "DELETE" });
        } catch (error) {
            ui.reportError(error);
            return;
        }
        reloadWith(null);
    });

    ui.updateRelativeTimes();
})();
