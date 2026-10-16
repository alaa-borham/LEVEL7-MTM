"""Local SQLite database (single user) with forward-only migrations, transactions, audit log and backups."""
from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import sqlite3
import threading
import uuid
from contextlib import contextmanager

MIGRATIONS: list[str] = [
    # 1 — initial schema
    """
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE documents (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,              -- model / mapping / xvars / finishing
        doc_id TEXT NOT NULL,
        version TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        status TEXT NOT NULL,
        body TEXT NOT NULL,
        created_at TEXT NOT NULL,
        created_by TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        UNIQUE(kind, doc_id, version)
    );
    CREATE TABLE import_files (
        id INTEGER PRIMARY KEY,
        sha256 TEXT NOT NULL UNIQUE,
        file_name TEXT NOT NULL,
        source_path TEXT NOT NULL,
        received_at TEXT NOT NULL,
        size INTEGER NOT NULL,
        encoding TEXT NOT NULL DEFAULT '',
        mapping_doc INTEGER REFERENCES documents(id),
        status TEXT NOT NULL,            -- imported / failed
        message TEXT NOT NULL DEFAULT '',
        raw BLOB NOT NULL,
        structure TEXT NOT NULL DEFAULT '{}'
    );
    CREATE TABLE orders (
        id INTEGER PRIMARY KEY,
        source TEXT NOT NULL,
        line_ref TEXT NOT NULL UNIQUE,   -- reseller-order-line, e.g. 9-53-1
        order_no TEXT,
        reseller TEXT,
        customer_id TEXT,
        created_at TEXT NOT NULL,
        cancelled_at TEXT,
        cancel_reason TEXT
    );
    CREATE TABLE revisions (
        id INTEGER PRIMARY KEY,
        order_id INTEGER NOT NULL REFERENCES orders(id),
        revision TEXT NOT NULL,
        line_number TEXT NOT NULL,       -- as sent by ERP, e.g. 9-53-1_2
        import_file INTEGER REFERENCES import_files(id),
        imported_at TEXT NOT NULL,
        quantity INTEGER,
        raw_line TEXT NOT NULL,          -- the line as read (codes and source values)
        normalized TEXT NOT NULL,        -- after mapping (mm, options)
        mapping_doc INTEGER REFERENCES documents(id),
        status TEXT NOT NULL,
        superseded_by INTEGER REFERENCES revisions(id),
        data_sha256 TEXT NOT NULL,
        UNIQUE(order_id, revision)
    );
    CREATE TABLE generations (
        id TEXT PRIMARY KEY,
        revision_id INTEGER NOT NULL REFERENCES revisions(id),
        model_doc INTEGER NOT NULL REFERENCES documents(id),
        xvars_doc INTEGER REFERENCES documents(id),
        finishing_doc INTEGER REFERENCES documents(id),
        engine_version TEXT NOT NULL,
        inputs TEXT NOT NULL,
        options TEXT NOT NULL,
        result TEXT NOT NULL,
        issues TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        input_sha256 TEXT NOT NULL,
        status TEXT NOT NULL,            -- ok / failed / invalidated
        created_at TEXT NOT NULL
    );
    CREATE TABLE approvals (
        id INTEGER PRIMARY KEY,
        generation_id TEXT NOT NULL UNIQUE REFERENCES generations(id),
        approved_by TEXT NOT NULL,
        approved_at TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE nesting_jobs (
        id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        path TEXT NOT NULL,
        contract TEXT NOT NULL,
        status TEXT NOT NULL,            -- written / sent / done / failed
        external_job_id TEXT,
        test_only INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE nesting_items (
        job_id TEXT NOT NULL REFERENCES nesting_jobs(id),
        revision_id INTEGER NOT NULL REFERENCES revisions(id),
        generation_id TEXT NOT NULL REFERENCES generations(id),
        PRIMARY KEY (job_id, revision_id)
    );
    CREATE TABLE audit (
        id INTEGER PRIMARY KEY,
        at TEXT NOT NULL,
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        entity TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        details TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX idx_rev_order ON revisions(order_id);
    CREATE INDEX idx_gen_rev ON generations(revision_id);
    CREATE INDEX idx_audit_entity ON audit(entity, entity_id);
    """,
    # 2 — manual geometric edits (recorded, re-applicable, never silently lost)
    """
    CREATE TABLE manual_edits (
        id INTEGER PRIMARY KEY,
        revision_id INTEGER NOT NULL REFERENCES revisions(id),
        piece TEXT NOT NULL,
        target TEXT NOT NULL,            -- point name
        dx REAL NOT NULL,
        dy REAL NOT NULL,
        reason TEXT NOT NULL,
        created_at TEXT NOT NULL,
        created_by TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1
    );
    """,
    # 3 — generations reference the catalog (rules) document instead of the old X table
    """
    ALTER TABLE generations RENAME COLUMN xvars_doc TO rules_doc
    """,
]


def now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex


class Database:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = FULL")
        self.lock = threading.RLock()
        self.migrate()

    # ------------------------------------------------------------ schema
    @property
    def schema_version(self) -> int:
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def migrate(self) -> None:
        v = self.schema_version
        if v > len(MIGRATIONS):
            raise RuntimeError(f"database schema {v} is newer than this program ({len(MIGRATIONS)}); update LEVEL7 MTM")
        for i in range(v, len(MIGRATIONS)):
            with self.tx():
                for stmt in _split(MIGRATIONS[i]):
                    self.conn.execute(stmt)
                self.conn.execute(f"PRAGMA user_version = {i + 1}")

    @contextmanager
    def tx(self):
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    def q(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, args=()):
        r = self.q(sql, args)
        return r[0] if r else None

    def audit(self, conn, actor: str, action: str, entity: str, entity_id, details: dict | None = None) -> None:
        conn.execute("INSERT INTO audit(at, actor, action, entity, entity_id, details) VALUES (?,?,?,?,?,?)",
                     (now(), actor, action, entity, str(entity_id), json.dumps(details or {}, ensure_ascii=False)))

    # ------------------------------------------------------------ settings
    def get_setting(self, key: str, default=None):
        r = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(r["value"]) if r else default

    def set_setting(self, key: str, value) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, json.dumps(value, ensure_ascii=False)))

    # ------------------------------------------------------------ backup
    def backup(self, folder: str) -> str:
        os.makedirs(folder, exist_ok=True)
        dest = os.path.join(folder, f"level7mtm-{_dt.datetime.now():%Y%m%d-%H%M%S}.db")
        with self.lock:
            target = sqlite3.connect(dest)
            try:
                self.conn.backup(target)
            finally:
                target.close()
        chk = sqlite3.connect(dest)
        try:
            if chk.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("backup failed integrity check")
        finally:
            chk.close()
        return dest

    def close(self) -> None:
        with self.lock:
            self.conn.close()


def restore_backup(backup_path: str, db_path: str) -> str:
    """Replace the database by a backup (the application must have closed the database). The current file is
    kept as ``<db>.before-restore-<time>``."""
    chk = sqlite3.connect(backup_path)
    try:
        if chk.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("backup file is damaged")
    finally:
        chk.close()
    keep = ""
    if os.path.exists(db_path):
        keep = f"{db_path}.before-restore-{_dt.datetime.now():%Y%m%d-%H%M%S}"
        shutil.copy2(db_path, keep)
        for ext in ("-wal", "-shm"):
            if os.path.exists(db_path + ext):
                os.remove(db_path + ext)
    shutil.copy2(backup_path, db_path + ".part")
    os.replace(db_path + ".part", db_path)
    return keep


def _split(script: str) -> list[str]:
    return [s.strip() for s in script.split(";") if s.strip() and not s.strip().startswith("--")]
