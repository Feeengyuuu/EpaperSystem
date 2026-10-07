"""Execute the shipped page scripts against a small in-memory DOM and HTTP boundary."""

import json
from pathlib import Path
import subprocess

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "src/static/scripts"
HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const requests = [], toasts = [];

class Element {
    constructor() {
        this.dataset = {}; this.attributes = {}; this.listeners = {};
        this.children = []; this.options = []; this.value = ''; this.checked = false;
        this.hidden = false; this.style = {}; this.textContent = '';
        const classes = new Set();
        this.classList = {
            add: (...names) => names.forEach(name => classes.add(name)),
            remove: (...names) => names.forEach(name => classes.delete(name)),
            contains: name => classes.has(name),
            toggle: (name, value) => value ? classes.add(name) : classes.delete(name),
        };
        this.queries = {};
    }
    addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
    dispatchEvent(event) {
        event.currentTarget = this;
        for (const listener of this.listeners[event.type] || []) listener(event);
    }
    querySelector(selector) { return this.queries[selector] || null; }
    querySelectorAll() { return []; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name] || null; }
    removeAttribute(name) { delete this.attributes[name]; }
    appendChild(child) { this.children.push(child); this.options.push(child); }
    focus() {}
}
const elements = new Map();
const get = id => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
};
const document = new Element();
document.readyState = 'complete';
document.documentElement = new Element();
document.getElementById = get;
document.body = new Element();
const card = new Element();
card.dataset = {
    playlist: 'Default', pluginId: 'simple_calendar', instance: 'Calendar',
    refresh: JSON.stringify({interval: 3600}), beforeDisplay: 'false',
    displayName: 'Simple Calendar', imageUrl: '/preview/calendar',
};
const image = new Element();
image.src = '/preview/calendar';
const stamp = new Element();
stamp.setAttribute('data-relative-time', '2026-10-06T10:00:00Z');
card.queries = {
    '[data-preview-image]': image, '[data-preview-empty]': new Element(),
    '[data-relative-time]': stamp, '[data-refresh-label]': new Element(),
    '[data-now-flag]': new Element(),
};
card.closest = () => card;
get('hero').dataset.nowPlaying = JSON.stringify({playlist: 'Default', plugin_instance: 'Calendar'});
get('hero').queries = {'.hero-media img': new Element(), '[data-hero-empty]': new Element()};
const menu = new Element();
document.querySelectorAll = selector => selector === '[data-instance-card]' ? [card] : [];
document.querySelector = selector => {
    if (selector === '#instanceSheet .sheet-menu') return menu;
    if (selector === "[data-menu='refresh-schedule']") return get('scheduleButton');
    if (selector === 'input[name="refreshType"]:checked') {
        return get('modal-refresh-scheduled').checked ? get('modal-refresh-scheduled') : get('modal-refresh-interval');
    }
    return null;
};
get('modal-refresh-scheduled').value = 'scheduled';
get('modal-refresh-interval').value = 'interval';
get('modal-refresh-interval').checked = true;

let reloads = 0;
const context = vm.createContext({
    document, console, Headers, FormData, URL, Node: Element,
    Option: class { constructor(label, value) { this.value = value; } },
    CustomEvent: class { constructor(type) { this.type = type; } },
    setTimeout: callback => { callback(); return 1; },
    window: {
        localStorage: {getItem: () => null},
        setTimeout: callback => { callback(); return 1; }, setInterval: () => 1,
        location: {reload: () => { reloads += 1; }},
    },
    fetch: async (url, options = {}) => {
        requests.push({url, method: options.method || 'GET'});
        let status = 200, body;
        if (url === '/display_plugin_instance' || url === '/refresh_plugin_instance') {
            body = input.noJobId ? {} : {job_id: 'job-1'};
        } else if (url.startsWith('/refresh_job/')) {
            status = input.jobStatus === 'missing' ? 404 : 200;
            body = status === 404 ? {message: 'Refresh job not found'} : {job: {status: input.jobStatus}};
        } else if (url.startsWith('/update_plugin_instance/')) {
            body = input.savedResponse || {success: true, refresh: {interval: 3600}};
        } else if (url === '/api/current_image') {
            status = 304;
        } else if (url === '/api/now-playing') {
            body = {playlist: 'Default', plugin_instance: 'Calendar'};
        } else {
            throw new Error(`Unexpected request ${url}`);
        }
        return {status, ok: status >= 200 && status < 300,
            headers: {get: () => 'application/json'}, json: async () => body};
    },
});
for (const filename of ['app.js', 'refresh_settings_manager.js']) {
    vm.runInContext(fs.readFileSync(path.join(input.scripts, filename), 'utf8'), context, {filename});
}
const ui = context.window.InkyUI;
// Polling and visual mounting are outside the clicked action under test.
ui.poll = () => () => {};
ui.toast = (message, tone) => toasts.push({message, tone});
ui.openSheet = () => {};
ui.closeSheet = () => {};
context.window.createRefreshSettingsManager = context.createRefreshSettingsManager;
vm.runInContext(fs.readFileSync(path.join(input.scripts, 'now_playing.js'), 'utf8'), context, {filename: 'now_playing.js'});

function clickCard(action) {
    const button = new Element();
    button.dataset.action = action;
    button.closest = selector => selector === '[data-instance-card]' ? card
        : selector === '[data-instance-card] [data-action]' ? button : null;
    document.dispatchEvent({type: 'click', target: button});
}
function clickMenu(action) {
    const item = new Element();
    item.dataset.menu = action;
    item.closest = () => item;
    menu.dispatchEvent({type: 'click', target: item});
}
if (input.action === 'display') clickCard('display');
if (input.action === 'refresh_then_display') get('heroRefresh').dispatchEvent({type: 'click'});
if (input.action === 'save_schedule') {
    clickCard('more'); clickMenu('refresh-schedule');
    get('modal-refresh-scheduled').checked = true;
    get('modal-refresh-interval').checked = false;
    get('modal-scheduled').value = '09:00';
    get('refreshSave').dispatchEvent({type: 'click'});
}
(async () => {
    // Drain the requests, polling promises and event handler continuations.
    for (let i = 0; i < 40; i += 1) await new Promise(resolve => setImmediate(resolve));
    if (input.action === 'save_schedule') {
        clickCard('more'); clickMenu('refresh-schedule');
    }
    process.stdout.write(JSON.stringify({requests, toasts, reloads,
        refreshedAt: stamp.getAttribute('data-relative-time'), imageSrc: image.src,
        refresh: JSON.parse(card.dataset.refresh),
        refreshLabel: card.querySelector('[data-refresh-label]').textContent,
        interval: get('modal-interval').value, unit: get('modal-unit').value,
    }));
})();
"""


def run_page(**scenario):
    result = subprocess.run(
        ["node", "-e", HARNESS],
        input=json.dumps({"scripts": str(SCRIPTS), **scenario}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("action", ["display", "refresh_then_display"])
@pytest.mark.parametrize("no_job_id", [False, True])
def test_missing_job_cannot_confirm_display_or_refresh(action, no_job_id):
    result = run_page(action=action, jobStatus="missing", noJobId=no_job_id)

    assert not any(toast["tone"] == "ok" for toast in result["toasts"])
    assert any(toast["tone"] in {"warn", "err"} for toast in result["toasts"])
    assert result["refreshedAt"] == "2026-10-06T10:00:00Z"
    assert result["imageSrc"] == "/preview/calendar"
    if action == "refresh_then_display":
        assert not any(request["url"] == "/display_plugin_instance" for request in result["requests"])


def test_completed_refresh_still_displays_and_confirms_success():
    result = run_page(action="refresh_then_display", jobStatus="completed")

    successes = [toast["message"] for toast in result["toasts"] if toast["tone"] == "ok"]
    assert successes == ["Data refreshed.", "Now on the display."]
    assert result["refreshedAt"] != "2026-10-06T10:00:00Z"
    assert any(request["url"] == "/display_plugin_instance" for request in result["requests"])


def test_schedule_save_displays_and_reopens_the_server_normalized_plan():
    result = run_page(action="save_schedule")

    assert result["refresh"] == {"interval": 3600}
    assert result["refreshLabel"] == "Every hour"
    assert result["interval"] == 1
    assert result["unit"] == "hour"
    assert result["reloads"] == 0


def test_schedule_save_without_a_persisted_plan_reloads_instead_of_guessing():
    result = run_page(action="save_schedule", savedResponse={"success": True})

    assert result["reloads"] == 1
    assert result["refresh"] == {"interval": 3600}
