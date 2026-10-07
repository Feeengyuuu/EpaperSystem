/* API keys page. Saving uses the full-list protocol of /api-keys/save:
 * every key that stays is sent with keepExisting, a deleted key is left out. */
(function () {
    "use strict";

    const ui = window.InkyUI;
    const KEY_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/;
    let envKeys = [];
    try {
        envKeys = JSON.parse(document.getElementById("envKeys").textContent || "[]");
    } catch (error) {
        envKeys = [];
    }

    const sheet = {
        title: document.getElementById("keySheetTitle"),
        sub: document.getElementById("keySheetSub"),
        name: document.getElementById("keyName"),
        value: document.getElementById("keyValue"),
        help: document.getElementById("keyValueHelp"),
        show: document.getElementById("keyShow"),
        save: document.getElementById("keySave"),
    };

    function keepAll(except) {
        return envKeys
            .filter((key) => key !== except)
            .map((key) => ({ key, value: null, keepExisting: true }));
    }

    async function submit(entries, message) {
        await ui.api("/api-keys/save", { method: "POST", json: { entries } });
        ui.toast(message, "ok");
        window.setTimeout(() => window.location.reload(), 700);
    }

    function openKeySheet(row) {
        const fixedName = row ? row.dataset.envKey : "";
        const isPath = row && row.dataset.valueType === "path";
        sheet.title.textContent = row
            ? (row.dataset.configured === "true" ? "Replace key" : "Set key")
            : "Add variable";
        sheet.sub.textContent = row ? row.dataset.service : "";
        sheet.name.value = fixedName;
        sheet.name.readOnly = Boolean(row);
        sheet.value.value = "";
        sheet.show.checked = isPath;
        sheet.value.type = isPath ? "text" : "password";
        sheet.value.placeholder = isPath ? "/path/to/file" : "";
        ui.openSheet("keySheet");
        window.setTimeout(() => (row ? sheet.value : sheet.name).focus(), 40);
    }

    sheet.show.addEventListener("change", () => {
        sheet.value.type = sheet.show.checked ? "text" : "password";
    });

    document.getElementById("keyForm").addEventListener("submit", (event) => {
        event.preventDefault();
        sheet.save.click();
    });

    sheet.save.addEventListener("click", (event) => {
        const name = sheet.name.value.trim();
        const value = sheet.value.value.trim();
        if (!KEY_PATTERN.test(name)) {
            ui.toast("Use letters, digits and underscores, starting with a letter.", "err");
            sheet.name.focus();
            return;
        }
        if (!value) {
            ui.toast("Enter a value.", "err");
            sheet.value.focus();
            return;
        }
        const entries = envKeys.map((key) => (
            key === name ? { key, value } : { key, value: null, keepExisting: true }
        ));
        if (!envKeys.includes(name)) {
            entries.push({ key: name, value });
        }
        ui.withBusy(event.currentTarget, async () => {
            try {
                await submit(entries, "Key saved.");
                ui.closeSheet("keySheet");
            } catch (error) {
                ui.reportError(error);
            }
        });
    });

    async function deleteKey(row) {
        const name = row.dataset.envKey;
        const confirmed = await ui.confirm({
            title: "Delete this key?",
            message: `${name} will be removed from the device. Plugins that use it stop working until a new key is set.`,
            confirmText: "Delete",
            danger: true,
        });
        if (!confirmed) {
            return;
        }
        try {
            await submit(keepAll(name), "Key deleted.");
        } catch (error) {
            ui.reportError(error);
        }
    }

    document.addEventListener("click", (event) => {
        const button = event.target.closest("[data-key-action]");
        if (!button) {
            return;
        }
        const action = button.dataset.keyAction;
        const row = button.closest("[data-key-row]");
        if (action === "add-variable") {
            openKeySheet(null);
        } else if (action === "set" && row) {
            openKeySheet(row);
        } else if (action === "delete" && row) {
            deleteKey(row);
        }
    });

    const search = document.getElementById("keySearch");
    const available = document.getElementById("availableKeys");
    const empty = document.getElementById("keySearchEmpty");
    if (search && available) {
        search.addEventListener("input", () => {
            const query = search.value.trim().toLowerCase();
            let visible = 0;
            available.querySelectorAll("[data-key-row]").forEach((row) => {
                const match = !query || row.dataset.search.includes(query);
                row.hidden = !match;
                visible += match ? 1 : 0;
            });
            available.hidden = visible === 0;
            empty.hidden = visible !== 0;
        });
    }
})();
