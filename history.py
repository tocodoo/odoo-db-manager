"""History of the warnings, errors and tracebacks of each database.

The server log of a database (`~/Library/Logs/Odoo Database Manager/databases/<db>.log`, written by
background starts and by the app's built-in terminal) is read incrementally; every WARNING, ERROR and
CRITICAL record, with its traceback, becomes an event. Identical events (same level, logger, message
shape and exception) are folded together with a count and their first / last time, so the history
survives restarts (the log itself is rotated at each start) and stays short.

Stored in `~/Library/Application Support/Odoo Database Manager/history/<db>.json`.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Optional

HISTORY_DIR = Path.home() / "Library" / "Application Support" / "Odoo Database Manager" / "history"
LOG_DIR = Path.home() / "Library" / "Logs" / "Odoo Database Manager" / "databases"
MAX_EVENTS = 300
MAX_DETAIL = 20_000
LEVELS = ("WARNING", "ERROR", "CRITICAL")
# Odoo: "%(asctime)s %(process)s %(levelname)s %(dbname)s %(name)s: %(message)s", times in UTC (Odoo sets TZ=UTC).
RECORD_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}),\d+ \d+ (DEBUG|INFO|WARNING|ERROR|CRITICAL) (\S+) ([^:\s]+): ?(.*)$")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")


def _file(db: str) -> Path:
    return HISTORY_DIR / f"{db}.json"


def _load(db: str) -> dict:
    try:
        data = json.loads(_file(db).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("events", {})
            data.setdefault("state", {})
            return data
    except (OSError, ValueError):
        pass
    return {"state": {}, "events": {}}


def _save(db: str, data: dict) -> None:
    events = data["events"]
    if len(events) > MAX_EVENTS:
        keep = sorted(events.values(), key=lambda e: e["last"], reverse=True)[:MAX_EVENTS]
        data["events"] = {e["id"]: e for e in keep}
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _file(db).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_file(db))


def _shape(message: str) -> str:
    """The message without what changes between two occurrences (ids, numbers, addresses, quoted values)."""
    m = re.sub(r"0x[0-9a-fA-F]+", "#", message)
    m = re.sub(r"'[^']{0,80}'|\"[^\"]{0,80}\"", "'…'", m)
    return re.sub(r"\d+", "#", m)[:300]


def _exception(detail: list[str]) -> str:
    """'KeyError: 'foo'' from the last line of a traceback."""
    if not any(l.startswith("Traceback") for l in detail):
        return ""
    for line in reversed(detail):
        line = line.strip()
        if line and not line.startswith(("File ", "^", "~")):
            return line[:400]
    return ""


def _commit(data: dict, rec: dict) -> None:
    exc = _exception(rec["detail"])
    sig = hashlib.sha1(f"{rec['level']}|{rec['logger']}|{_shape(rec['message'])}|{_shape(exc)}".encode()).hexdigest()[:16]
    detail = "\n".join(rec["detail"])[:MAX_DETAIL]
    ev = data["events"].get(sig)
    if ev:
        ev["count"] += 1
        ev["last"] = rec["time"]
        ev["message"] = rec["message"][:1000]
        if detail:
            ev["detail"] = detail
    else:
        data["events"][sig] = {"id": sig, "level": rec["level"], "logger": rec["logger"],
                               "message": rec["message"][:1000], "exception": exc, "detail": detail,
                               "count": 1, "first": rec["time"], "last": rec["time"]}


def ingest(db: str, final: bool = False) -> dict:
    """Reads what the log gained since last time. The last record is kept open (a traceback may still be
    being written) unless the log has been idle for 3 seconds or `final` (before the log is rotated)."""
    lp = LOG_DIR / f"{db}.log"
    data = _load(db)
    try:
        st = lp.stat()
    except OSError:
        return data
    state = data["state"]
    offset = state.get("offset", 0) if state.get("inode") == st.st_ino and state.get("offset", 0) <= st.st_size else 0
    if offset == st.st_size:
        return data
    final = final or time.time() - st.st_mtime > 3
    mtime_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime))
    with open(lp, "rb") as fh:
        fh.seek(offset)
        chunk = fh.read(20_000_000)
    end = chunk.rfind(b"\n") + 1 if not final else len(chunk)
    if end <= 0:
        return data

    pos = 0
    pending_start = None  # offset of the record still open
    rec: Optional[dict] = None
    changed = False
    for raw in chunk[:end].splitlines(keepends=True):
        line = ANSI_RE.sub("", raw.decode("utf-8", errors="replace")).rstrip("\r\n")
        m = RECORD_RE.match(line)
        if m:
            if rec:
                _commit(data, rec)
                changed = True
            rec = None
            if m.group(3) in LEVELS:
                rec = {"level": m.group(3), "logger": m.group(5), "message": m.group(6),
                       "time": f"{m.group(1)}T{m.group(2)}Z", "detail": []}
                pending_start = offset + pos
        elif rec is not None:
            if len(rec["detail"]) < 400:
                rec["detail"].append(line)
        elif line.startswith("Traceback"):
            # A crash printed outside the logger (startup failure): an error of its own.
            rec = {"level": "ERROR", "logger": "stderr", "message": "Uncaught exception",
                   "time": mtime_utc, "detail": [line]}
            pending_start = offset + pos
        pos += len(raw)
    new_offset = offset + end
    if rec:
        if final:
            _commit(data, rec)
            changed = True
        else:
            new_offset = pending_start  # re-read the open record next time
    if changed or new_offset != offset:
        data["state"] = {"inode": st.st_ino, "offset": new_offset}
        _save(db, data)
    return data


def events(db: str, levels: Optional[list[str]] = None, limit: int = 100) -> dict:
    data = ingest(db)
    items = sorted(data["events"].values(), key=lambda e: e["last"], reverse=True)
    if levels:
        items = [e for e in items if e["level"] in levels]
    return {"ok": True, "name": db, "summary": summary(db, data), "events": items[:limit]}


def summary(db: str, data: Optional[dict] = None) -> dict:
    data = data if data is not None else ingest(db)
    evs = list(data["events"].values())
    errors = [e for e in evs if e["level"] in ("ERROR", "CRITICAL")]
    return {"errors": len(errors), "warnings": len(evs) - len(errors),
            "last_error": max((e["last"] for e in errors), default=None),
            "last": max((e["last"] for e in evs), default=None)}


def clear(db: str) -> dict:
    """Forgets the events; what the current log already holds is not read again."""
    data = ingest(db, final=True)
    data["events"] = {}
    _save(db, data)
    return {"ok": True, "name": db, "message": f"History of {db} cleared."}


def rename(old: str, new: str) -> None:
    try:
        if _file(old).exists():
            _file(old).replace(_file(new))
    except OSError:
        pass


def copy(src: str, new: str) -> None:
    """The copy starts with the source's past events, not its log position."""
    data = _load(src)
    if data["events"]:
        _save(new, {"state": {}, "events": data["events"]})


def drop(db: str) -> None:
    try:
        _file(db).unlink()
    except OSError:
        pass


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)

