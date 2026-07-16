#!/usr/bin/env python3
"""Create a root-invoked one-time InkyPi setup or recovery token."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from types import SimpleNamespace
import sys

if os.name != "nt":
    import pwd
    from repair_env_permissions import repair_runtime_env_permissions


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from security.credentials import CredentialError, CredentialStore  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("bootstrap", "recover", "ensure-bootstrap"),
        help="create an initial pairing token or an administrator recovery token",
    )
    parser.add_argument(
        "--data-dir",
        default=os.getenv("INKYPI_DATA_DIR", "/var/lib/inkypi/data"),
    )
    parser.add_argument(
        "--service-user",
        default=os.getenv("INKYPI_SERVICE_USER", "inkypi"),
    )
    args = parser.parse_args(argv)
    if os.name != "nt" and os.geteuid() != 0:
        parser.error("this command must be run as root")

    if args.command == "ensure-bootstrap" and os.name != "nt":
        try:
            repair_runtime_env_permissions()
        except (KeyError, OSError, RuntimeError) as error:
            print(f"runtime env permission repair failed: {error}", file=sys.stderr)
            return 1

    store = CredentialStore(SimpleNamespace(data_dir=Path(args.data_dir)))
    try:
        if args.command == "ensure-bootstrap" and store.has_admin():
            print("InkyPi administrator is already configured")
            return 0
        if args.command in {"bootstrap", "ensure-bootstrap"}:
            store.create_bootstrap_token()
            path = store.bootstrap_plaintext_path
        else:
            store.create_recovery_token()
            path = store.recovery_plaintext_path
        if os.name != "nt":
            account = pwd.getpwnam(args.service_user)
            os.chown(store.root, account.pw_uid, account.pw_gid)
            os.chown(store.credentials_path, account.pw_uid, account.pw_gid)
            os.chmod(store.root, 0o700)
            os.chmod(store.credentials_path, 0o600)
            os.chmod(path, 0o600)
    except CredentialError as error:
        print(f"inkypi administrator token failed: {error}", file=sys.stderr)
        return 1
    except (KeyError, OSError) as error:
        print(f"inkypi administrator token ownership failed: {error}", file=sys.stderr)
        return 1

    print(f"One-time administrator token written to {path}")
    print(f"Read it locally with: sudo cat {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
