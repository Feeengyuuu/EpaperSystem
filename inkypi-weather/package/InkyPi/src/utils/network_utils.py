import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import privileged_actions


logger = logging.getLogger(__name__)
_wifi_watchdog_thread = None


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
    iw_bin = iw_path or _find_iw()
    if iw_bin:
        result = _run_command([iw_bin, "dev", interface, "link"], timeout=5)
        if result and result.returncode == 0:
            if "Connected to" in result.stdout:
                return True
            if "Not connected" in result.stdout:
                return False

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

    return None


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

    Unknown probes never count as disconnections. Repeated recovery attempts
    back off to 15 minutes, and only a successful probe reports recovery.
    """
    failures = 0
    last_reconnect_attempt = None
    reconnect_attempts = 0
    outage_started = None
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

        if reachable is True:
            if outage_started is not None:
                logger.info(
                    "Wi-Fi watchdog recovered %s. | outage_seconds: %.1f | attempts: %s",
                    "after reconnect" if reconnect_attempts else "without intervention",
                    now - outage_started, reconnect_attempts,
                )
            outage_started = None
            reconnect_attempts = 0
            failures = 0
        elif reachable is None:
            # Failed tooling / a connection still activating is not evidence
            # that disconnecting the radio would help.
            failures = 0
        else:
            failures += 1
            delay = confirmation_interval_seconds
            if outage_started is None:
                outage_started = now
                logger.warning(
                    "Wi-Fi watchdog detected connectivity issue; confirming before reconnect. | interface: %s | connected: %s | gateway: %s",
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
