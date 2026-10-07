/* Plugin library: search, grid/list view and drag reordering. */
(function () {
    "use strict";

    const ui = window.InkyUI;
    const VIEW_KEY = "inkypi-plugins-view";
    const container = document.getElementById("pluginsContainer");
    const empty = document.getElementById("pluginsEmpty");
    const search = document.getElementById("pluginSearch");
    const sortToggle = document.getElementById("sortToggle");
    const sortLabel = sortToggle.querySelector("[data-sort-label]");
    const sortHint = document.getElementById("sortHint");
    const tiles = () => Array.from(container.querySelectorAll(".plugin-tile"));

    function readView() {
        try {
            return window.localStorage.getItem(VIEW_KEY) === "list" ? "list" : "grid";
        } catch (error) {
            return "grid";
        }
    }

    function applyView(view) {
        container.classList.toggle("list-view", view === "list");
        document.querySelectorAll("input[name='pluginView']").forEach((radio) => {
            radio.checked = radio.value === view;
        });
    }

    document.querySelectorAll("input[name='pluginView']").forEach((radio) => {
        radio.addEventListener("change", () => {
            try {
                window.localStorage.setItem(VIEW_KEY, radio.value);
            } catch (error) {
                /* view preference is optional */
            }
            applyView(radio.value);
        });
    });
    applyView(readView());

    search.addEventListener("input", () => {
        const query = search.value.trim().toLowerCase();
        let visible = 0;
        tiles().forEach((tile) => {
            const match = !query || tile.dataset.search.includes(query)
                || tile.querySelector(".plugin-tile-name").textContent.toLowerCase().includes(query);
            tile.hidden = !match;
            visible += match ? 1 : 0;
        });
        empty.hidden = visible !== 0;
    });

    /* ---------- Reordering ---------- */
    let sorting = false;
    let dragged = null;

    function onDragStart(event) {
        dragged = this;
        this.classList.add("dragging");
        event.dataTransfer.effectAllowed = "move";
    }

    function onDragEnd() {
        this.classList.remove("dragging");
        tiles().forEach((tile) => tile.classList.remove("drag-over"));
    }

    function onDragOver(event) {
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        if (this !== dragged) {
            this.classList.add("drag-over");
        }
    }

    function onDragLeave() {
        this.classList.remove("drag-over");
    }

    function onDrop(event) {
        event.preventDefault();
        this.classList.remove("drag-over");
        if (!dragged || this === dragged) {
            return;
        }
        const items = tiles();
        if (items.indexOf(dragged) < items.indexOf(this)) {
            this.after(dragged);
        } else {
            this.before(dragged);
        }
    }

    function blockNavigation(event) {
        if (sorting) {
            event.preventDefault();
        }
    }

    function setSorting(enabled) {
        sorting = enabled;
        container.classList.toggle("sorting", enabled);
        sortHint.hidden = !enabled;
        sortLabel.textContent = enabled ? "Done" : "Reorder";
        sortToggle.classList.toggle("btn-primary", enabled);
        sortToggle.classList.toggle("btn-secondary", !enabled);
        if (enabled) {
            search.value = "";
            search.dispatchEvent(new Event("input"));
        }
        search.disabled = enabled;
        tiles().forEach((tile) => {
            tile.draggable = enabled;
            const method = enabled ? "addEventListener" : "removeEventListener";
            tile[method]("dragstart", onDragStart);
            tile[method]("dragend", onDragEnd);
            tile[method]("dragover", onDragOver);
            tile[method]("dragleave", onDragLeave);
            tile[method]("drop", onDrop);
            tile[method]("click", blockNavigation);
        });
    }

    sortToggle.addEventListener("click", async () => {
        if (!sorting) {
            setSorting(true);
            return;
        }
        setSorting(false);
        const order = tiles().map((tile) => tile.dataset.pluginId);
        try {
            await ui.api("/api/plugin_order", { method: "POST", json: { order } });
            ui.toast("Plugin order saved.", "ok");
        } catch (error) {
            ui.reportError(error);
        }
    });
})();
