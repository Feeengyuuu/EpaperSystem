from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import stat
from uuid import uuid4

from utils.atomic_file import fsync_directory

from .contracts import AssetInput, SCHEMA_VERSION


_MAX_ASSET_BYTES = 24 * 1024 * 1024


def _read_bounded_regular_file(path: Path, expected_size: int) -> bytes:
    if (
        isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or not 0 <= expected_size <= _MAX_ASSET_BYTES
    ):
        raise OSError("asset size is outside the supported bound")
    if path.is_symlink():
        raise OSError("asset path is link-like")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != expected_size:
            raise OSError("asset metadata does not match the ledger")
        chunks = []
        remaining = expected_size + 1
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        body = b"".join(chunks)
        if len(body) != expected_size:
            raise OSError("asset changed while it was being read")
        return body
    finally:
        os.close(descriptor)


class StoredAsset:
    def __init__(
        self,
        asset_id: str,
        name: str,
        media_type: str,
        size: int,
        relative_path: str,
    ) -> None:
        self.asset_id = asset_id
        self.name = name
        self.media_type = media_type
        self.size = size
        self.relative_path = relative_path


class LedgerStore:
    def __init__(self, root: Path, *, read_only: bool = False) -> None:
        self.root = Path(root)
        self.database_path = self.root / "publication.sqlite3"
        self.asset_root = self.root / "assets" / "sha256"
        self.read_only = read_only
        if read_only:
            if not self.database_path.is_file():
                raise FileNotFoundError(f"publication ledger does not exist: {self.database_path}")
            self._validate_schema()
        else:
            self.root.mkdir(parents=True, exist_ok=True)
            self.asset_root.mkdir(parents=True, exist_ok=True)
            self._initialize()

    @contextmanager
    def connect(self):
        if self.read_only:
            database_uri = f"{self.database_path.resolve().as_uri()}?mode=ro"
            connection = sqlite3.connect(
                database_uri,
                timeout=30,
                uri=True,
            )
        else:
            connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        if self.read_only:
            connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _validate_schema(self) -> None:
        with self.connect() as connection:
            row = connection.execute("SELECT schema_version FROM publication_meta WHERE singleton = 1").fetchone()
        if row is None or row["schema_version"] != SCHEMA_VERSION:
            raise RuntimeError("unsupported publication schema version")

    def _initialize(self) -> None:
        with self.connect() as connection:
            # The authenticated web container mounts this ledger read-only.
            # WAL readers may still need to create ``-wal``/``-shm`` files,
            # which violates that boundary when the writer is between runs.
            # The rollback journal is written only by the worker and keeps
            # read-only readers free of filesystem side effects.
            mode = connection.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
            if str(mode).lower() != "delete":
                raise RuntimeError("publication ledger requires SQLite DELETE journal mode")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS publication_meta (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    schema_version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS editions (
                    edition_id TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL,
                    instance_uuid TEXT NOT NULL,
                    plugin_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    settings_revision INTEGER NOT NULL,
                    source_revision INTEGER NOT NULL,
                    generated_at TEXT NOT NULL,
                    fresh_until TEXT NOT NULL,
                    stale_until TEXT NOT NULL,
                    presentation TEXT NOT NULL,
                    document BLOB NOT NULL,
                    etag TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS current_editions (
                    instance_uuid TEXT PRIMARY KEY,
                    edition_id TEXT NOT NULL REFERENCES editions(edition_id)
                );
                CREATE TABLE IF NOT EXISTS assets (
                    asset_id TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL,
                    media_type TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    relative_path TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS edition_assets (
                    edition_id TEXT NOT NULL REFERENCES editions(edition_id),
                    ordinal INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    asset_id TEXT NOT NULL REFERENCES assets(asset_id),
                    media_type TEXT NOT NULL,
                    PRIMARY KEY (edition_id, ordinal),
                    UNIQUE (edition_id, name)
                );
                CREATE TABLE IF NOT EXISTS instance_status (
                    instance_uuid TEXT PRIMARY KEY,
                    plugin_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    settings_revision INTEGER NOT NULL,
                    source_revision INTEGER NOT NULL,
                    reason_code TEXT,
                    attempted_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS catalogs (
                    catalog_id TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL,
                    catalog_revision INTEGER NOT NULL UNIQUE,
                    document BLOB NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS current_catalog (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    catalog_id TEXT NOT NULL REFERENCES catalogs(catalog_id)
                );
                CREATE TABLE IF NOT EXISTS catalog_instances (
                    catalog_id TEXT NOT NULL REFERENCES catalogs(catalog_id),
                    instance_uuid TEXT NOT NULL,
                    PRIMARY KEY (catalog_id, instance_uuid)
                );
                """
            )
            edition_asset_columns = {item["name"] for item in connection.execute("PRAGMA table_info(edition_assets)")}
            if "media_type" not in edition_asset_columns:
                connection.execute("ALTER TABLE edition_assets ADD COLUMN media_type TEXT")
                connection.execute(
                    """
                    UPDATE edition_assets
                    SET media_type = (
                        SELECT assets.media_type
                        FROM assets
                        WHERE assets.asset_id = edition_assets.asset_id
                    )
                    """
                )
            row = connection.execute("SELECT schema_version FROM publication_meta WHERE singleton = 1").fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO publication_meta(singleton, schema_version) VALUES (1, ?)",
                    (SCHEMA_VERSION,),
                )
            elif row["schema_version"] != SCHEMA_VERSION:
                raise RuntimeError("unsupported publication schema version")

    def persist_asset(self, asset: AssetInput) -> StoredAsset:
        body = bytes(asset.body)
        if len(body) > _MAX_ASSET_BYTES:
            raise ValueError("publication asset exceeds the supported bound")
        asset_id = hashlib.sha256(body).hexdigest()
        relative_path = f"{asset_id[:2]}/{asset_id}"
        path = self.asset_root / relative_path
        shard_existed = path.parent.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not shard_existed:
            fsync_directory(self.asset_root)
        try:
            existing_body = _read_bounded_regular_file(path, len(body))
        except FileNotFoundError:
            existing_body = None
        except OSError as error:
            raise RuntimeError("content-addressed asset is corrupt") from error
        if existing_body is not None:
            if hashlib.sha256(existing_body).hexdigest() != asset_id:
                raise RuntimeError("content-addressed asset is corrupt")
        else:
            temporary = path.parent / f".{asset_id}.{uuid4().hex}.tmp"
            try:
                with temporary.open("xb") as handle:
                    handle.write(body)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
                fsync_directory(path.parent)
            finally:
                temporary.unlink(missing_ok=True)
        return StoredAsset(
            asset_id,
            asset.name,
            asset.media_type,
            len(body),
            relative_path,
        )

    def read_asset(self, relative_path: str, expected_id: str, size: int) -> bytes | None:
        path = self._verified_asset_path(relative_path, expected_id)
        if path is None:
            return None
        try:
            body = _read_bounded_regular_file(path, size)
        except OSError:
            return None
        if len(body) != size or hashlib.sha256(body).hexdigest() != expected_id:
            return None
        return body

    def prune(self, max_per_instance: int, max_catalogs: int) -> None:
        if self.read_only:
            raise RuntimeError("publication ledger is read-only")
        delete_after_commit: set[tuple[str, str]] = set()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            instances = connection.execute("SELECT DISTINCT instance_uuid FROM editions").fetchall()
            for instance in instances:
                current = connection.execute(
                    "SELECT edition_id FROM current_editions WHERE instance_uuid = ?",
                    (instance["instance_uuid"],),
                ).fetchone()
                keep = {current["edition_id"]} if current is not None else set()
                rows = connection.execute(
                    """
                    SELECT edition_id
                    FROM editions
                    WHERE instance_uuid = ?
                    ORDER BY source_revision DESC, created_at DESC, edition_id DESC
                    """,
                    (instance["instance_uuid"],),
                ).fetchall()
                for row in rows:
                    if len(keep) >= max_per_instance:
                        break
                    keep.add(row["edition_id"])
                for row in rows:
                    edition_id = row["edition_id"]
                    if edition_id in keep:
                        continue
                    connection.execute(
                        "DELETE FROM edition_assets WHERE edition_id = ?",
                        (edition_id,),
                    )
                    connection.execute(
                        "DELETE FROM editions WHERE edition_id = ?",
                        (edition_id,),
                    )
            current_catalog = connection.execute(
                "SELECT catalog_id FROM current_catalog WHERE singleton = 1"
            ).fetchone()
            keep_catalogs = {current_catalog["catalog_id"]} if current_catalog is not None else set()
            catalogs = connection.execute(
                """
                SELECT catalog_id
                FROM catalogs
                ORDER BY catalog_revision DESC, created_at DESC, catalog_id DESC
                """
            ).fetchall()
            for row in catalogs:
                if len(keep_catalogs) >= max_catalogs:
                    break
                keep_catalogs.add(row["catalog_id"])
            for row in catalogs:
                catalog_id = row["catalog_id"]
                if catalog_id in keep_catalogs:
                    continue
                connection.execute(
                    "DELETE FROM catalog_instances WHERE catalog_id = ?",
                    (catalog_id,),
                )
                connection.execute(
                    "DELETE FROM catalogs WHERE catalog_id = ?",
                    (catalog_id,),
                )
            orphaned = connection.execute(
                """
                SELECT assets.asset_id, assets.relative_path
                FROM assets
                WHERE NOT EXISTS (
                    SELECT 1 FROM edition_assets
                    WHERE edition_assets.asset_id = assets.asset_id
                )
                """
            ).fetchall()
            for row in orphaned:
                path = self._verified_asset_path(
                    row["relative_path"],
                    row["asset_id"],
                )
                if path is None:
                    continue
                connection.execute(
                    "DELETE FROM assets WHERE asset_id = ?",
                    (row["asset_id"],),
                )
                delete_after_commit.add((row["relative_path"], row["asset_id"]))
            tracked_asset_ids = {row["asset_id"] for row in connection.execute("SELECT asset_id FROM assets")}
            for path in self.asset_root.glob("*/*"):
                asset_id = path.name
                if asset_id in tracked_asset_ids:
                    continue
                try:
                    relative_path = path.relative_to(self.asset_root).as_posix()
                except ValueError:
                    continue
                verified = self._verified_asset_path(relative_path, asset_id)
                if verified is not None:
                    delete_after_commit.add((relative_path, asset_id))
        for relative_path, asset_id in delete_after_commit:
            self._delete_if_still_untracked(relative_path, asset_id)

    def _delete_if_still_untracked(self, relative_path: str, asset_id: str) -> None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            tracked = connection.execute(
                "SELECT 1 FROM assets WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()
            if tracked is not None:
                return
            path = self._verified_asset_path(relative_path, asset_id)
            if path is not None:
                path.unlink(missing_ok=True)

    def _verified_asset_path(self, relative_path: str, asset_id: str) -> Path | None:
        if len(asset_id) != 64 or any(character not in "0123456789abcdef" for character in asset_id):
            return None
        expected = f"{asset_id[:2]}/{asset_id}"
        if relative_path.replace("\\", "/") != expected:
            return None
        path = self.asset_root / relative_path
        try:
            if path.is_symlink():
                return None
            resolved_root = self.asset_root.resolve()
            resolved_path = path.resolve(strict=False)
            if not resolved_path.is_relative_to(resolved_root):
                return None
        except OSError:
            return None
        return path
