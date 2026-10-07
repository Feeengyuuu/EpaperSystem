/* Device page: save settings, live slider values, runtime summary, power. */
(function () {
    "use strict";

    const ui = window.InkyUI;
    const form = document.getElementById("settingsForm");
    const saveBar = form.querySelector(".save-bar");
    const saveButton = document.getElementById("saveSettings");

    form.querySelectorAll("[data-slider]").forEach((slider) => {
        const output = document.getElementById(`${slider.id}-value`);
        const digits = Number(slider.step) < 0.1 ? 2 : 1;
        const render = () => {
            output.textContent = Number(slider.value).toFixed(digits);
        };
        slider.addEventListener("input", render);
        render();
    });

    form.addEventListener("input", () => saveBar.classList.add("is-dirty"));
    form.addEventListener("change", () => saveBar.classList.add("is-dirty"));

    saveButton.addEventListener("click", () => {
        ui.withBusy(saveButton, async () => {
            try {
                const result = await ui.api("/save_settings", { method: "POST", body: new FormData(form) });
                saveBar.classList.remove("is-dirty");
                ui.toast(result.message || "Saved settings.", "ok");
            } catch (error) {
                ui.reportError(error);
            }
        });
    });

    /* ---------- Runtime summary ---------- */
    const STATE_LABELS = {
        ready: ["Running normally", "ok"],
        degraded: ["Running with warnings", "warn"],
        not_ready: ["Not ready", "err"],
        starting: ["Starting", "warn"],
    };
    const stateNode = document.getElementById("runtimeState");
    const linesNode = document.getElementById("runtimeLines");
    const loginNode = document.getElementById("runtimeLogin");

    function line(text) {
        const node = document.createElement("span");
        node.textContent = text;
        linesNode.appendChild(node);
    }

    function formatUptime(seconds) {
        if (typeof seconds !== "number") {
            return "";
        }
        const hours = Math.floor(seconds / 3600);
        const days = Math.floor(hours / 24);
        if (days > 0) {
            return `${days} d ${hours % 24} h`;
        }
        return `${hours} h ${Math.floor((seconds % 3600) / 60)} min`;
    }

    let stopRuntime = null;
    stopRuntime = ui.poll(async () => {
        try {
            const body = await ui.api("/api/runtime-status", { quiet: true });
            const [label, tone] = STATE_LABELS[body.status] || [body.status || "Unknown", "warn"];
            stateNode.className = `status-pill ${tone}`;
            stateNode.textContent = label;
            linesNode.replaceChildren();
            const issues = (body.error_codes || []).length;
            line(issues ? `Needs attention: ${issues}` : "No service warnings");
            if (body.release_id) {
                line(`Release ${body.release_id}`);
            }
            if (typeof body.uptime_seconds === "number") {
                line(`Uptime ${formatUptime(body.uptime_seconds)}`);
            }
            const recoveries = ((body.runtime || {}).recoveries || []).length;
            if (recoveries) {
                line(`Automatic recoveries: ${recoveries}`);
            }
        } catch (error) {
            if (error.status === 401 || error.status === 403) {
                stateNode.hidden = true;
                linesNode.replaceChildren();
                loginNode.hidden = false;
                if (stopRuntime) {
                    stopRuntime();
                }
            }
        }
    }, 30000);

    /* ---------- Power ---------- */
    const POWER = {
        reboot: {
            title: "Reboot the device?",
            message: "The display keeps its last image. The web page comes back in about a minute.",
            confirmText: "Reboot",
            done: "The system is rebooting. The UI will be unavailable until the reboot is complete.",
        },
        shutdown: {
            title: "Shut down the device?",
            message: "It stays off until someone unplugs and reconnects the power.",
            confirmText: "Shutdown",
            done: "The system is shutting down. The UI will remain unavailable until it is manually restarted.",
        },
    };

    document.querySelectorAll("[data-power]").forEach((button) => {
        button.addEventListener("click", async () => {
            const kind = button.dataset.power;
            const copy = POWER[kind];
            const confirmed = await ui.confirm({ ...copy, danger: true });
            if (!confirmed) {
                return;
            }
            try {
                await ui.api("/shutdown", { method: "POST", json: { reboot: kind === "reboot" }, quiet: true });
                ui.toast(copy.done, "warn", { timeout: 15000 });
            } catch (error) {
                if (error.status === 0) {
                    ui.toast(copy.done, "warn", { timeout: 15000 });
                } else {
                    ui.reportError(error);
                }
            }
        });
    });
})();
