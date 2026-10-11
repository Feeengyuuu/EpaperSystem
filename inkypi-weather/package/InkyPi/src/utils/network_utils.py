import json
import logging
import math
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import privileged_actions
from .atomic_file import AtomicCommitUncertainError, atomic_write_json


logger = logging.getLogger(__name__)
_wifi_watchdog_thread = None
# A NetworkManager activation normally finishes well inside its 45 s DHCP
# timeout. One that is still "connecting" after this long is retrying an
# association the driver keeps refusing, and must count as an outage.
WIFI_ACTIVATION_GRACE_SECONDS = 120
# brcmfmac can wedge so that every association request fails until reboot
# (2026-10-10: 23 minutes offline after a mesh roam). Reboot only after a long
# outage that several reconnects did not fix, never soon after boot, and at
# most once per cooldown so a dead access point cannot cause a reboot loop.
WIFI_REBOOT_AFTER_OUTAGE_SECONDS = 20 * 60
WIFI_REBOOT_AFTER_RECONNECT_ATTEMPTS = 3
WIFI_REBOOT_MIN_UPTIME_SECONDS = 30 * 60
WIFI_REBOOT_COOLDOWN_SECONDS = 6 * 60 * 60
# Give the latest reconnect time to take effect before calling it a failure.
WIFI_REBOOT_RECONNECT_SETTLE_SECONDS = 120
_WIFI_REBOOT_STATE_NAME = "wifi_watchdog_reboot.json"
_last_wifi_reboot_wall = None


def _find_iw():
    for candidate in (
        shutil.which("iw"),
        "/usr/sbin/iw",
        "/sbin/iw",
        "/usr/bin/iw",
        "/bin/iw",
    ):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _find_command(command, candidates=()):
    found = shutil.which(command)
    if found:
        return found

    for candidate in candidates:
        if Path(candidate).exists():
            return candidate

    return None


def _wireless_interfaces(sys_class_net="/sys/class/net"):
    try:
        return sorted(
            entry.name
            for entry in Path(sys_class_net).iterdir()
            if entry.name.startswith("wl")
        )
    except OSError as exc:
        logger.warning("Could not list network interfaces: %s", exc)
        return []


def disable_wifi_powersave(interface_names=None, iw_path=None):
    """Ask the fixed privileged broker to disable Wi-Fi power saving."""
    interfaces = list(interface_names) if interface_names is not None else _wireless_interfaces()
    if not interfaces:
        logger.info("Skipping Wi-Fi powersave disable: no wireless interfaces found")
        return False

    any_disabled = False
    for interface in interfaces:
        try:
            privileged_actions.wifi_powersave_off(interface)
        except privileged_actions.PrivilegedActionError as exc:
            logger.warning("Could not disable Wi-Fi powersave on %s: %s", interface, exc)
            continue
        any_disabled = True
        logger.info("Disabled Wi-Fi powersave on %s", interface)

    return any_disabled


def _run_command(command, timeout=8):
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Command failed: %s | %s", command, exc)
        return None


def _wifi_is_connected(interface="wlan0", iw_path=None, nmcli_path=None):
    link_state = None
    iw_bin = iw_path or _find_iw()
    if iw_bin:
        result = _run_command([iw_bin, "dev", interface, "link"], timeout=5)
        if result and result.returncode == 0:
            if "Connected to" in result.stdout:
                return True
            if "Not connected" in result.stdout:
                link_state = False

    nmcli_bin = nmcli_path or _find_command("nmcli", ("/usr/bin/nmcli", "/bin/nmcli"))
    if nmcli_bin:
        result = _run_command(
            [nmcli_bin, "-t", "-f", "DEVICE,TYPE,STATE", "dev", "status"],
            timeout=5,
        )
        if result and result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.split(":")
                if len(parts) >= 3 and parts[0] == interface:
                    if parts[2].startswith("connecting"):
                        return None
                    return parts[1] == "wifi" and parts[2] == "connected"

    return link_state


def _wifi_link_activating(interface="wlan0", iw_path=None, nmcli_path=None):
    """True only while iw reports no link and NetworkManager is connecting."""
    iw_bin = iw_path or _find_iw()
    nmcli_bin = nmcli_path or _find_command("nmcli", ("/usr/bin/nmcli", "/bin/nmcli"))
    if not iw_bin or not nmcli_bin:
        return False
    link = _run_command([iw_bin, "dev", interface, "link"], timeout=5)
    if not link or link.returncode != 0 or "Not connected" not in link.stdout:
        return False
    status = _run_command(
        [nmcli_bin, "-t", "-f", "DEVICE,TYPE,STATE", "dev", "status"],
        timeout=5,
    )
    if not status or status.returncode != 0:
        return False
    for line in status.stdout.splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[0] == interface:
            return parts[2].startswith("connecting")
    return False


def _system_uptime_seconds():
    try:
        return float(Path("/proc/uptime").read_text(encoding="ascii").split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _wifi_reboot_state_path():
    data_dir = os.environ.get("INKYPI_DATA_DIR")
    return Path(data_dir) / _WIFI_REBOOT_STATE_NAME if data_dir else None


def _persisted_wifi_reboot():
    path = _wifi_reboot_state_path()
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("requested_at")
    except (OSError, ValueError, AttributeError):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    return value if math.isfinite(value) else None


def _last_wifi_reboot():
    known = [value for value in (_persisted_wifi_reboot(), _last_wifi_reboot_wall) if value is not None]
    return max(known) if known else None


def _record_wifi_reboot(requested_at):
    """Remember the request; return whether it reached disk and survives a reboot."""
    global _last_wifi_reboot_wall
    _last_wifi_reboot_wall = requested_at
    path = _wifi_reboot_state_path()
    if path is None:
        return False
    try:
        atomic_write_json(path, {"requested_at": requested_at})
    except AtomicCommitUncertainError as exc:
        if exc.target_replaced:
            return True
        logger.warning("Could not record the Wi-Fi watchdog reboot: %s", exc)
        return False
    except OSError as exc:
        logger.warning("Could not record the Wi-Fi watchdog reboot: %s", exc)
        return False
    return True


def _wifi_reboot_allowed(wall_now):
    uptime = _system_uptime_seconds()
    if uptime is None or uptime < WIFI_REBOOT_MIN_UPTIME_SECONDS:
        return False
    last = _last_wifi_reboot()
    if last is None:
        return True
    elapsed = wall_now - last
    if elapsed < 0:
        # A record from the future means the wall clock moved (no RTC); it
        # cannot date the last reboot, so only this boot's age bounds the rate.
        return uptime >= WIFI_REBOOT_COOLDOWN_SECONDS
    return elapsed >= WIFI_REBOOT_COOLDOWN_SECONDS


def reboot_for_wifi_recovery(interface, outage_seconds, attempts):
    """Reboot through the fixed privileged broker; record first so it is bounded."""
    if not _record_wifi_reboot(time.time()):
        # Without a durable record the next boot could not see this reboot,
        # so only this boot's own age can bound the rate (full SD card or a
        # read-only root must not turn into a reboot every 30 minutes).
        uptime = _system_uptime_seconds()
        if uptime is None or uptime < WIFI_REBOOT_COOLDOWN_SECONDS:
            logger.warning(
                "Wi-Fi watchdog reboot skipped: the reboot record could not be saved. | interface: %s | outage_seconds: %.1f",
                interface, outage_seconds,
            )
            return False
    logger.error(
        "Wi-Fi watchdog rebooting after an unrecoverable outage. | interface: %s | outage_seconds: %.1f | attempts: %s",
        interface, outage_seconds, attempts,
    )
    try:
        privileged_actions.reboot()
    except privileged_actions.PrivilegedActionError as error:
        logger.warning("Wi-Fi watchdog reboot request failed: %s", error)
        return False
    return True


def _default_gateway(interface="wlan0", ip_path=None):
    ip_bin = ip_path or _find_command("ip", ("/usr/sbin/ip", "/sbin/ip", "/usr/bin/ip", "/bin/ip"))
    if not ip_bin:
        return None

    result = _run_command([ip_bin, "route", "show", "default", "dev", interface], timeout=5)
    if not result or result.returncode != 0:
        return None

    parts = result.stdout.split()
    if "via" not in parts:
        return ""

    via_index = parts.index("via")
    if via_index + 1 >= len(parts):
        return None

    return parts[via_index + 1]


def _gateway_is_reachable(interface="wlan0", gateway=None, ping_path=None):
    if gateway is None:
        return None
    if not gateway:
        return False

    ping_bin = ping_path or _find_command("ping", ("/usr/bin/ping", "/bin/ping"))
    if not ping_bin:
        return None

    result = _run_command(
        [ping_bin, "-I", interface, "-c", "1", "-W", "3", gateway],
        timeout=6,
    )
    if result is None or result.returncode not in {0, 1}:
        return None
    return result.returncode == 0


def _known_wifi_connections(nmcli_path=None):
    nmcli_bin = nmcli_path or _find_command("nmcli", ("/usr/bin/nmcli", "/bin/nmcli"))
    if not nmcli_bin:
        return []

    result = _run_command(
        [nmcli_bin, "-t", "-f", "NAME,TYPE", "connection", "show"],
        timeout=8,
    )
    if not result or result.returncode != 0:
        return []

    connections = []
    for line in result.stdout.splitlines():
        parts = line.split(":")
        if len(parts) >= 2 and parts[-1] == "802-11-wireless":
            connections.append(":".join(parts[:-1]))
    return connections


def reconnect_wifi(interface="wlan0", nmcli_path=None, iw_path=None):
    """Ask the fixed privileged broker to rescan and reconnect Wi-Fi."""
    try:
        privileged_actions.wifi_reconnect(interface)
    except privileged_actions.PrivilegedActionError as error:
        logger.warning("Wi-Fi reconnect failed on %s: %s", interface, error)
        return False
    logger.info("Wi-Fi reconnect requested on %s; awaiting connectivity probe", interface)
    return True


def wifi_reconnect_watchdog_loop(
    interface="wlan0",
    interval_seconds=60,
    failure_threshold=3,
    reconnect_cooldown_seconds=180,
    *,
    confirmation_interval_seconds=15,
    stop_event=None,
):
    """Confirm short failures before asking NetworkManager to reconnect.

    Unknown probes never count as disconnections, but an activation that is
    still not complete after WIFI_ACTIVATION_GRACE_SECONDS does. Repeated
    recovery attempts back off to 15 minutes, a long outage that reconnects did
    not fix escalates to a bounded reboot, and only a successful probe reports
    recovery.
    """
    failures = 0
    last_reconnect_attempt = None
    reconnect_attempts = 0
    outage_started = None
    activating_since = None
    reboot_deferral_logged = False
    next_powersave_refresh = time.monotonic() + 600
    stop_event = stop_event if stop_event is not None else threading.Event()

    logger.info("Starting Wi-Fi reconnect watchdog on %s", interface)

    while not stop_event.is_set():
        now = time.monotonic()
        if now >= next_powersave_refresh:
            disable_wifi_powersave([interface])
            next_powersave_refresh = now + 600

        connected = _wifi_is_connected(interface)
        gateway = _default_gateway(interface)
        reachable = _gateway_is_reachable(interface, gateway) if connected is True else connected
        delay = interval_seconds
        activating = reachable is None and connected is None and _wifi_link_activating(interface)
        if activating:
            activating_since = now if activating_since is None else activating_since
            if now - activating_since >= WIFI_ACTIVATION_GRACE_SECONDS:
                reachable = False

        if reachable is True:
            if outage_started is not None:
                logger.info(
                    "Wi-Fi watchdog recovered %s. | outage_seconds: %.1f | attempts: %s",
                    "after reconnect" if reconnect_attempts else "without intervention",
                    now - outage_started, reconnect_attempts,
                )
            outage_started = None
            activating_since = None
            reboot_deferral_logged = False
            reconnect_attempts = 0
            failures = 0
        elif connected is True and reachable is None:
            # The link is up but the gateway check is inconclusive (ping
            # missing or failing). That is not an outage, so do not let old
            # reconnect attempts accumulate toward a reboot across days.
            outage_started = None
            activating_since = None
            reboot_deferral_logged = False
            reconnect_attempts = 0
            failures = 0
        elif reachable is None and activating:
            # Let a fresh activation finish, but keep any failure streak: a
            # driver that refuses associations alternates between
            # "disconnected" and "connecting" without ever linking.
            delay = confirmation_interval_seconds
        elif reachable is None:
            # Failed tooling is not evidence that disconnecting the radio
            # would help.
            failures = 0
        else:
            failures += 1
            delay = confirmation_interval_seconds
            if outage_started is None:
                # A stalled activation began when it started, not when its
                # grace ran out.
                outage_started = activating_since if activating and activating_since is not None else now
                logger.warning(
                    "Wi-Fi watchdog detected connectivity issue%s; confirming before reconnect. | interface: %s | connected: %s | gateway: %s",
                    " (activation has not completed)" if activating else "",
                    interface, connected, gateway,
                )

            cooldown = min(900, reconnect_cooldown_seconds * 2 ** min(max(reconnect_attempts - 1, 0), 4))
            if failures >= failure_threshold and (
                last_reconnect_attempt is None or now - last_reconnect_attempt >= cooldown
            ):
                logger.warning("Wi-Fi watchdog attempting reconnect on %s", interface)
                reconnect_wifi(interface)
                last_reconnect_attempt = now
                reconnect_attempts += 1
                failures = 0
                # The reconnect starts a new activation; let it have its grace.
                activating_since = None
            if (
                reconnect_attempts >= WIFI_REBOOT_AFTER_RECONNECT_ATTEMPTS
                and now - outage_started >= WIFI_REBOOT_AFTER_OUTAGE_SECONDS
                and now - last_reconnect_attempt >= WIFI_REBOOT_RECONNECT_SETTLE_SECONDS
            ):
                if _wifi_reboot_allowed(time.time()):
                    reboot_for_wifi_recovery(interface, now - outage_started, reconnect_attempts)
                elif not reboot_deferral_logged:
                    logger.info(
                        "Wi-Fi watchdog reboot deferred by its uptime or cooldown guard. | interface: %s | outage_seconds: %.1f | attempts: %s",
                        interface, now - outage_started, reconnect_attempts,
                    )
                    reboot_deferral_logged = True

        stop_event.wait(max(1, delay))


def start_wifi_reconnect_watchdog(interface="wlan0"):
    global _wifi_watchdog_thread

    if _wifi_watchdog_thread and _wifi_watchdog_thread.is_alive():
        return _wifi_watchdog_thread

    _wifi_watchdog_thread = threading.Thread(
        target=wifi_reconnect_watchdog_loop,
        kwargs={"interface": interface},
        name="wifi-reconnect-watchdog",
        daemon=True,
    )
    _wifi_watchdog_thread.start()
    return _wifi_watchdog_thread
