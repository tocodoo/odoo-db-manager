"""Monitoring, diagnostic and lifecycle of local Odoo databases.

Single source of truth used by the `odoodb` CLI, the MCP server and the macOS app.
Everything returns plain dicts (JSON-serialisable).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

import config
import history
import odoo_ops as ops

LOG_DIR = Path.home() / "Library" / "Logs" / "Odoo Database Manager" / "databases"
NON_ODOO_DBS = {"template0", "template1", "postgres"}
READY_RE = re.compile(r"Registry loaded in|odoo\.modules\.loading: Modules loaded")
LOG_LINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+ \d+ (INFO|WARNING|ERROR|CRITICAL|DEBUG) ")

Log = Optional[Callable[[str], None]]


class DBError(Exception):
    """Error with an optional hint telling the caller (often an agent) what to do next."""

    def __init__(self, message: str, hint: str = "", code: str = "error"):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.code = code

    def to_dict(self) -> dict:
        d = {"ok": False, "error": self.message, "code": self.code}
        if self.hint:
            d["hint"] = self.hint
        return d


# --------------------------------------------------------------------------- #
# Low level helpers
# --------------------------------------------------------------------------- #

def _run(cmd: list[str], cwd: Optional[str] = None, timeout: int = 60) -> tuple[int, str, str]:
    return ops.run_cmd(cmd, cwd=cwd, timeout=timeout)


def psql(db: str, sql: str, timeout: int = 30) -> tuple[bool, list[list[str]]]:
    """Run a query, returns rows split on the unit separator."""
    code, out, err = _run(
        [ops._find_psql(), "-X", "-q", "-d", db, "-t", "-A", "-F", "\x1f", "-v", "ON_ERROR_STOP=1", "-c", sql],
        timeout=timeout,
    )
    if code != 0:
        return False, [[err.strip()]]
    rows = [line.split("\x1f") for line in out.splitlines() if line != ""]
    return True, rows


def series_from_version(v: str) -> str:
    """'19.0.1.3' -> '19.0', 'saas~18.4.1.3' -> 'saas~18.4', '19.0' -> '19.0'."""
    v = (v or "").strip()
    if not v:
        return ""
    parts = v.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else v


def norm_series(s: str) -> str:
    """Comparable form of a series or branch name: 'saas~18.4' == 'saas-18.4'."""
    return (s or "").strip().lower().replace("~", "-")


def branch_for_series(series: str) -> str:
    return norm_series(series)


def major_of(series: str) -> str:
    m = re.search(r"(\d+)", series or "")
    return m.group(1) if m else ""


def pyenv_for_series(series: str) -> str:
    """odoo-<major>, existing or not (a missing one is reported, and created on demand)."""
    major = major_of(series)
    return f"odoo-{major}" if major else config.get_pyenv_for_branch(series)


def effective_env(sc: Optional[dict], series: str) -> str:
    """The environment that really runs a database: forced in the settings, else its script's, else odoo-<major>."""
    return config.get_pyenv_global_env() or (sc or {}).get("pyenv") or pyenv_for_series(series)


def community_path() -> Path:
    return Path(config.get_odoo_community_path()).expanduser().resolve()


def enterprise_path() -> Optional[Path]:
    return ops._effective_enterprise_path()


def odoo_root() -> Path:
    return community_path().parent


def code_series() -> str:
    """Series of the checked-out community code, from odoo/release.py."""
    release = community_path() / "odoo" / "release.py"
    try:
        text = release.read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r"^version_info\s*=\s*\(([^)]*)\)", text, re.MULTILINE)
    if not m:
        return ""
    items = [x.strip().strip("'\"") for x in m.group(1).split(",")]
    if len(items) < 2:
        return ""
    return f"{items[0]}.{items[1]}"


def _dir_size(path: Path) -> int:
    code, out, _ = _run(["/usr/bin/du", "-sk", str(path)], timeout=30)
    if code == 0 and out.split():
        try:
            return int(out.split()[0]) * 1024
        except ValueError:
            pass
    return 0


def log_path(db: str) -> Path:
    return LOG_DIR / f"{db}.log"


def _pid_path(db: str) -> Path:
    return LOG_DIR / f"{db}.pid"


# --------------------------------------------------------------------------- #
# Launch scripts
# --------------------------------------------------------------------------- #

def _script_var(text: str, name: str) -> str:
    m = re.search(rf"^(?:export\s+)?{name}\s*=\s*\"([^\"]*)\"", text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    m = re.search(rf"^(?:export\s+)?{name}\s*=\s*'([^']*)'", text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    m = re.search(rf"^(?:export\s+)?{name}\s*=\s*([^\s#]+)", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def parse_script(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    db = _script_var(text, "DB")
    if not db:
        m = re.search(r"(?:^|\s)-d\s+['\"]?([\w.\-]+)", text)
        db = m.group(1) if m else ""
    addons = _script_var(text, "ADDONS")
    if not addons:
        m = re.search(r"--addons-path[= ]['\"]?([^\s'\"]+)", text)
        addons = m.group(1) if m else ""
    # Relative folders are relative to where the script runs: odoo_root() (see _spawn_script), then its `cd`.
    cd_m = re.search(r"^\s*cd\s+['\"]?([^'\"\s;&]+)", text, re.MULTILINE)
    base = odoo_root() / Path(cd_m.group(1)).expanduser() if cd_m else odoo_root()
    addons_path = [p if Path(p).expanduser().is_absolute() else os.path.normpath(base / p)
                   for p in addons.split(",") if p]
    port_m = re.search(r"--http-port[=\s]+(\d+)", text)
    pyenv_m = re.search(r"PYENV_ENV=\"\$\{PYENV_ENV:-([^}]+)\}\"", text)
    branch = _script_var(text, "BRANCH") or _script_var(text, "VERSION")
    return {
        "path": str(path),
        "db": db,
        "branch": branch,
        "addons_path": addons_path,
        "install_modules": [m for m in _script_var(text, "INSTALL_MODULES").split(",") if m],
        "update_modules": [m for m in _script_var(text, "UPDATE_MODULES").split(",") if m],
        "update_mode": _script_var(text, "UPDATE_MODE") or "reinit",
        "port": int(port_m.group(1)) if port_m else None,
        "pyenv": (pyenv_m.group(1).strip("'\"") if pyenv_m else ""),
        "managed": "INSTALL_MODULES=" in text and "exec \"$PYTHON\"" in text,
    }


def script_index() -> dict[str, dict]:
    """db name -> parsed script, scanning every scripts folder once."""
    by_db: dict[str, dict] = {}
    by_stem: dict[str, dict] = {}
    for d in ops._scripts_search_dirs():
        for script in sorted(d.rglob("*.sh")):
            try:
                info = parse_script(script)
            except OSError:
                continue
            try:
                rel = script.parent.relative_to(d)
                info["subdir"] = rel.parts[0] if rel.parts else ""
            except ValueError:
                info["subdir"] = ""
            if info["db"] and info["db"] not in by_db:
                by_db[info["db"]] = info
            stem = script.stem[5:] if script.stem.startswith("odoo-") else script.stem
            by_stem.setdefault(stem, info)
    for stem, info in by_stem.items():
        by_db.setdefault(stem, info)
    return by_db


def script_for(db: str, index: Optional[dict] = None) -> Optional[dict]:
    return (index if index is not None else script_index()).get(db)


# --------------------------------------------------------------------------- #
# Processes
# --------------------------------------------------------------------------- #

def _etime_to_seconds(et: str) -> int:
    days = 0
    if "-" in et:
        d, et = et.split("-", 1)
        days = int(d or 0)
    parts = [int(p or 0) for p in et.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts[-3:]
    return days * 86400 + h * 3600 + m * 60 + s


def odoo_processes() -> list[dict]:
    """Running Odoo processes, one entry per database (workers folded in)."""
    code, out, _ = _run(["/bin/ps", "-axww", "-o", "pid=,ppid=,etime=,rss=,pcpu=,command="], timeout=10)
    if code != 0:
        return []
    procs = []
    for line in out.splitlines():
        parts = line.strip().split(None, 5)
        if len(parts) < 6:
            continue
        pid, ppid, etime, rss, pcpu, cmd = parts
        if "odoo-bin" not in cmd and "odoo.py" not in cmd:
            continue
        if cmd.startswith(("grep", "/bin/bash", "bash ", "/bin/sh", "sh ", "/bin/zsh")):
            continue
        m = (re.search(r"(?:^|\s)-d[= ]?['\"]?([^\s'\"]+)", cmd)
             or re.search(r"--database[= ]['\"]?([^\s'\"]+)", cmd))
        if not m:
            continue
        port_m = re.search(r"--http-port[= ](\d+)", cmd) or re.search(r"(?:^|\s)-p\s+(\d+)", cmd)
        kind = "server"
        if re.search(r"\s(shell|i18n|neutralize|module|db|cloc|populate)\s", f" {cmd} ") or "--stop-after-init" in cmd:
            kind = "task"
        procs.append({
            "pid": int(pid), "ppid": int(ppid), "uptime": _etime_to_seconds(etime),
            "rss": int(rss) * 1024, "cpu": float(pcpu.replace(",", ".")),
            "db": m.group(1), "port": int(port_m.group(1)) if port_m else 8069,
            "kind": kind, "command": cmd,
        })
    pids = {p["pid"] for p in procs}
    grouped: dict[tuple, dict] = {}
    for p in procs:
        key = (p["db"], p["kind"])
        g = grouped.get(key)
        is_main = p["ppid"] not in pids
        if g is None:
            grouped[key] = {**p, "workers": 0}
            continue
        g["rss"] += p["rss"]
        g["cpu"] += p["cpu"]
        g["workers"] += 1
        if is_main:
            g.update({k: p[k] for k in ("pid", "ppid", "uptime", "port", "command")})
    result = []
    for g in grouped.values():
        g["mode"] = "background" if _read_pid(g["db"]) == g["pid"] else "terminal"
        g["url"] = f"http://localhost:{g['port']}"
        g.pop("ppid", None)
        result.append(g)
    return sorted(result, key=lambda x: x["db"].lower())


def _read_pid(db: str) -> Optional[int]:
    try:
        return int(_pid_path(db).read_text().strip())
    except (OSError, ValueError):
        return None


def running_server(db: str, procs: Optional[list] = None) -> Optional[dict]:
    for p in procs if procs is not None else odoo_processes():
        if p["db"] == db and p["kind"] == "server":
            return p
    return None


# --------------------------------------------------------------------------- #
# Databases
# --------------------------------------------------------------------------- #

_SUMMARY_SQL = """
SELECT
  (SELECT latest_version FROM ir_module_module WHERE name = 'base'),
  (SELECT count(*) FROM ir_module_module WHERE state = 'installed'),
  (SELECT count(*) FROM ir_module_module WHERE state IN ('to install', 'to upgrade', 'to remove')),
  (SELECT to_char(max(create_date), 'YYYY-MM-DD"T"HH24:MI:SS') FROM res_users_log),
  (SELECT to_char(min(create_date), 'YYYY-MM-DD"T"HH24:MI:SS') FROM res_users)
"""


def database_names() -> list[str]:
    ok, rows = psql("postgres", "SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY lower(datname)")
    if not ok:
        err = rows[0][0] if rows else "?"
        hint = ("PostgreSQL is stuck shutting down (it waits for open connections): "
                "`odoodb setup --fix postgres-restart`." if "shutting down" in err else
                "Start PostgreSQL: `odoodb setup --fix postgres-start` (or set psql_path with "
                "`odoodb settings --set psql_path=...`).")
        raise DBError("Cannot reach PostgreSQL: " + err, hint=hint, code="postgres_down")
    return [r[0] for r in rows if r and r[0] not in NON_ODOO_DBS]


CACHE_DIR = Path.home() / "Library" / "Caches" / "Odoo Database Manager"


def database_sizes(max_age: int = 600) -> dict[str, int]:
    if max_age < 0:
        return _compute_sizes(CACHE_DIR / "sizes.json")
    """Size of every database. pg_database_size is slow on many databases: cached."""
    cache = CACHE_DIR / "sizes.json"
    try:
        cached = json.loads(cache.read_text())
        if time.time() - cache.stat().st_mtime > max_age:
            # Stale: serve it and refresh in the background.
            import sys
            subprocess.Popen([sys.executable, "-c", "import dbtools; dbtools.database_sizes(max_age=-1)"],
                             cwd=os.path.dirname(os.path.abspath(__file__)), start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return cached
    except (OSError, ValueError):
        pass
    return _compute_sizes(cache)


def _compute_sizes(cache: Path) -> dict[str, int]:
    ok, rows = psql("postgres", "SELECT datname, pg_database_size(datname) FROM pg_database WHERE NOT datistemplate")
    sizes = {r[0]: int(r[1]) for r in rows if ok and len(r) == 2 and r[1].isdigit()}
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(sizes))
    except OSError:
        pass
    return sizes


def database_size(db: str) -> int:
    ok, rows = psql("postgres", f"SELECT pg_database_size('{db}')")
    return int(rows[0][0]) if ok and rows and rows[0][0].isdigit() else 0


def _db_summary(db: str) -> dict:
    ok, rows = psql(db, _SUMMARY_SQL, timeout=15)
    if not ok or not rows:
        return {"is_odoo": False}
    r = rows[0] + [""] * 5
    return {
        "is_odoo": bool(r[0]),
        "base_version": r[0],
        "series": series_from_version(r[0]),
        "modules_installed": int(r[1] or 0),
        "modules_locked": int(r[2] or 0),
        "last_login": r[3] or None,
        "created": r[4] or None,
    }


def custom_addons(addons_path: list[str]) -> list[dict]:
    """The addons folders of a script that are not community/enterprise."""
    comm = community_path()
    ent = enterprise_path()
    out = []
    for p in addons_path:
        pp = Path(p).expanduser()
        try:
            rp = pp.resolve()
        except OSError:
            rp = pp
        if rp == comm / "addons" or rp == comm or (ent and rp == ent) or rp == comm / "odoo" / "addons":
            continue
        try:
            rel = rp.relative_to(odoo_root())
            label = rel.parts[0] if rel.parts else rp.name
        except ValueError:
            label = rp.name
        if any(o["name"] == label for o in out):
            continue
        out.append({"path": str(rp), "name": label, "exists": rp.is_dir()})
    return out


def list_databases() -> dict:
    names = database_names()
    sizes = database_sizes()
    index = script_index()
    procs = odoo_processes()
    with ThreadPoolExecutor(max_workers=12) as pool:
        summaries = dict(zip(names, pool.map(_db_summary, names)))
    current = code_series()
    items = []
    for name in names:
        s = summaries[name]
        sc = index.get(name)
        run = running_server(name, procs)
        port = (run or {}).get("port") or (sc or {}).get("port") or config.get_db_port(name)
        items.append({
            "name": name,
            **s,
            "size": sizes.get(name, 0),
            "script": sc["path"] if sc else None,
            "script_branch": sc["branch"] if sc else None,
            "folder": (sc or {}).get("subdir") or None,
            "custom_addons": [a["name"] for a in custom_addons(sc["addons_path"])] if sc else [],
            "running": bool(run),
            "port": port,
            "url": f"http://localhost:{port}",
            "matches_code": (norm_series(s.get("series", "")) == norm_series(current)) if s.get("series") else None,
        })
    return {
        "ok": True,
        "code_series": current,
        "repos_branch": ops.get_current_branch(str(community_path())),
        "databases": items,
        "running": procs,
    }


def require_db(db: str) -> None:
    if db not in database_names():
        raise DBError(f"Database '{db}' does not exist.", hint="Run `odoodb list` to see the databases.", code="not_found")


# --------------------------------------------------------------------------- #
# Repositories & modules
# --------------------------------------------------------------------------- #

def _git(path: str, *args: str, timeout: int = 20) -> Optional[str]:
    code, out, _ = _run(["git", "-C", path, *args], timeout=timeout)
    return out.strip() if code == 0 else None


def repo_info(path: str, kind: str) -> dict:
    p = Path(path).expanduser()
    info = {"path": str(p), "kind": kind, "exists": p.is_dir(), "git": False}
    try:
        info["name"] = str(p.resolve().relative_to(odoo_root()))
    except (ValueError, OSError):
        info["name"] = p.name
    if not p.is_dir():
        return info
    top = _git(str(p), "rev-parse", "--show-toplevel")
    if not top:
        return info
    info.update({"git": True, "root": top})
    log = _git(top, "log", "-1", "--format=%h\x1f%s\x1f%an\x1f%cI")
    if log:
        sha, subject, author, date = (log.split("\x1f") + ["", "", "", ""])[:4]
        info.update({"commit": sha, "subject": subject, "author": author, "date": date})
    info["branch"] = _git(top, "rev-parse", "--abbrev-ref", "HEAD")
    untracked = "no" if kind in ("community", "enterprise") else "normal"
    status = _git(top, "status", "--porcelain", f"--untracked-files={untracked}", timeout=30)
    info["changes"] = len(status.splitlines()) if status else 0
    counts = _git(top, "rev-list", "--left-right", "--count", "HEAD...@{u}")
    if counts and len(counts.split()) == 2:
        info["ahead"], info["behind"] = (int(x) for x in counts.split())
    remote = _git(top, "remote", "get-url", "origin")
    if remote:
        info["remote"] = remote
    return info


def module_dirs(addons_path: list[str]) -> dict[str, str]:
    """module name -> addons folder where its code lives (first match wins)."""
    found: dict[str, str] = {}
    roots = list(addons_path) + [str(community_path() / "odoo" / "addons")]
    for root in roots:
        r = Path(root).expanduser()
        if not r.is_dir():
            continue
        try:
            for child in r.iterdir():
                if child.name not in found and (child / "__manifest__.py").is_file():
                    found[child.name] = str(r.resolve())
        except OSError:
            continue
    return found


def effective_addons_path(db: str, sc: Optional[dict]) -> list[str]:
    if sc and sc["addons_path"]:
        return sc["addons_path"]
    return ops.build_addons_path(str(community_path()), []).split(",")


def list_modules(db: str, addons: Optional[list[str]] = None) -> list[dict]:
    ok, rows = psql(db, """
        SELECT name, state, coalesce(latest_version, ''), coalesce(author, ''),
               coalesce(shortdesc->>'en_US', name), application::text
        FROM ir_module_module
        WHERE state <> 'uninstalled' AND state <> 'uninstallable'
        ORDER BY name""")
    if not ok:
        return []
    addons = addons if addons is not None else effective_addons_path(db, script_for(db))
    where = module_dirs(addons)
    comm = str(community_path())
    ent = str(enterprise_path() or "\0")
    mods = []
    for r in rows:
        r = (r + [""] * 6)[:6]
        loc = where.get(r[0])
        if loc is None:
            source = "missing"
        elif loc.startswith(comm):
            source = "community"
        elif loc.startswith(ent):
            source = "enterprise"
        else:
            source = "custom"
        mods.append({
            "name": r[0], "state": r[1], "version": r[2], "author": r[3], "title": r[4],
            "application": r[5] == "true", "source": source, "location": loc,
        })
    return mods


# --------------------------------------------------------------------------- #
# Info & diagnosis
# --------------------------------------------------------------------------- #

def database_info(db: str, with_modules: bool = False) -> dict:
    require_db(db)
    index = script_index()
    sc = index.get(db)
    summary = _db_summary(db)
    procs = odoo_processes()
    run = running_server(db, procs)
    addons = effective_addons_path(db, sc)
    port = (run or {}).get("port") or (sc or {}).get("port") or config.get_db_port(db)
    filestore = ops.get_default_filestore_dir(db)

    repos: list[dict] = []
    seen_roots: set[str] = set()
    comm = community_path()
    ent = enterprise_path()

    def kind_of(p: str) -> str:
        rp = Path(p).expanduser().resolve()
        if rp in (comm, comm / "addons"):
            return "community"
        if ent and rp == ent:
            return "enterprise"
        return "custom"

    with ThreadPoolExecutor(max_workers=6) as pool:
        infos = list(pool.map(lambda p: repo_info(p, kind_of(p)), addons))
    for info in infos:
        key = info.get("root") or info["path"]
        if key in seen_roots:
            continue
        seen_roots.add(key)
        repos.append(info)

    details = {}
    if summary.get("is_odoo"):
        ok, rows = psql(db, "SELECT code, name FROM res_lang WHERE active ORDER BY code")
        details["languages"] = [{"code": r[0], "name": r[1]} for r in rows] if ok else []
        ok, rows = psql(db, "SELECT count(*) FROM res_users WHERE active AND NOT share")
        details["users"] = int(rows[0][0]) if ok and rows else None
        ok, rows = psql(db, "SELECT name FROM res_company ORDER BY id LIMIT 1")
        details["company"] = rows[0][0] if ok and rows else None
        ok, rows = psql(db, "SELECT login FROM res_users WHERE id = 2")
        details["admin_login"] = rows[0][0] if ok and rows else None
        ok, rows = psql(db, "SELECT value FROM ir_config_parameter WHERE key = 'web.base.url'")
        details["base_url"] = rows[0][0] if ok and rows else None

    modules = list_modules(db, addons) if summary.get("is_odoo") else []
    custom = [m["name"] for m in modules if m["source"] == "custom" and m["state"] == "installed"]
    by_location: dict[str, list[str]] = {}
    for m in modules:
        if m["location"] and m["state"] == "installed":
            by_location.setdefault(m["location"], []).append(m["name"])
    for r in repos:
        rp = str(Path(r["path"]).expanduser().resolve())
        r["installed_modules"] = len(by_location.get(rp, []))
        if r["kind"] == "custom":
            r["modules"] = sorted(by_location.get(rp, []))

    lp = log_path(db)
    info = {
        "ok": True,
        "name": db,
        **summary,
        "code_series": code_series(),
        "repos_branch": ops.get_current_branch(str(comm)),
        "size": database_size(db),
        "filestore": {"path": str(filestore), "exists": filestore.is_dir(),
                      "size": _dir_size(filestore) if filestore.is_dir() else 0},
        "script": sc,
        "addons_path": addons,
        "repos": repos,
        "running": run,
        "port": port,
        "url": f"http://localhost:{port}",
        "log": {"path": str(lp), "exists": lp.is_file(), "size": lp.stat().st_size if lp.is_file() else 0},
        "pyenv": effective_env(sc, summary.get("series") or (sc or {}).get("branch") or ""),
        "custom_modules": custom,
        "module_counts": _count_by(modules),
        "history": history.summary(db),
        **details,
    }
    info["issues"] = diagnose(db, info, modules, procs)
    if with_modules:
        info["modules"] = modules
    return info


def _count_by(modules: list[dict]) -> dict:
    counts = {"installed": 0, "community": 0, "enterprise": 0, "custom": 0, "missing": 0, "locked": 0}
    for m in modules:
        if m["state"] == "installed":
            counts["installed"] += 1
            counts[m["source"]] = counts.get(m["source"], 0) + 1
        elif m["state"] in ("to install", "to upgrade", "to remove"):
            counts["locked"] += 1
    return counts


def _issue(id_: str, severity: str, title: str, detail: str = "", fix: Optional[str] = None, fix_label: str = "") -> dict:
    return {"id": id_, "severity": severity, "title": title, "detail": detail, "fix": fix, "fix_label": fix_label}


def diagnose(db: str, info: dict, modules: list[dict], procs: list[dict]) -> list[dict]:
    issues = []
    sc = info.get("script")
    if not info.get("is_odoo"):
        issues.append(_issue("not_odoo", "error", "Not an initialised Odoo database",
                             "ir_module_module is missing: the database is empty or the initialisation failed.",
                             "init", "Initialise"))
        return issues

    series = info.get("series") or ""
    code = info.get("code_series") or ""
    if series and code and norm_series(series) != norm_series(code):
        issues.append(_issue(
            "branch_mismatch", "error", f"Database is {series}, repos are on {code}",
            f"Starting it now would run {code} code on a {series} database. "
            f"Switch core/enterprise to {branch_for_series(series)} first.",
            "branch", f"Switch repos to {branch_for_series(series)}"))

    locked = [m["name"] for m in modules if m["state"] in ("to install", "to upgrade", "to remove")]
    if locked:
        issues.append(_issue("module_locks", "error", f"{len(locked)} module(s) stuck in a pending state",
                             ", ".join(locked[:12]) + ("…" if len(locked) > 12 else "") +
                             " — usually an interrupted install/update.", "locks", "Clear locks"))

    if not sc:
        issues.append(_issue("no_script", "warning", "No launch script",
                             "The database cannot be started in one click.", "script", "Create the script"))
    else:
        if series and sc.get("branch") and norm_series(sc["branch"]) != norm_series(series):
            issues.append(_issue("script_branch", "warning",
                                 f"Script says {sc['branch']}, database is {series}",
                                 "The BRANCH of the launch script is out of date.", "script", "Fix the script"))
        missing_dirs = [p for p in sc["addons_path"] if not Path(p).expanduser().is_dir()]
        if missing_dirs:
            issues.append(_issue("addons_missing", "error", "Addons folder not found",
                                 ", ".join(missing_dirs), "addons", "Fix the addons path"))

    missing = [m["name"] for m in modules if m["state"] == "installed" and m["source"] == "missing"]
    if missing:
        found = ops._detect_module_parent_dirs(str(community_path()), missing)
        issues.append(_issue(
            "modules_missing", "warning", f"{len(missing)} installed module(s) without code",
            ", ".join(missing[:12]) + ("…" if len(missing) > 12 else "") +
            (f" — found in {', '.join(Path(f).name for f in found)}" if found else " — not found under the Odoo folder."),
            "addons" if found else None, "Add the folders to the addons path" if found else ""))

    issues += _runtime_issues(info, modules)

    run = info.get("running")
    port = info.get("port")
    others = [p for p in procs if p["kind"] == "server" and p["db"] != db and p["port"] == port]
    if others and not run:
        issues.append(_issue("port_busy", "warning", f"Port {port} is used by {others[0]['db']}",
                             "Starting this database would fail. Pick another port.", "port", "Use a free port"))

    if not info["filestore"]["exists"]:
        ok, rows = psql(db, "SELECT count(*) FROM ir_attachment WHERE store_fname IS NOT NULL")
        if ok and rows and int(rows[0][0] or 0) > 0:
            issues.append(_issue("filestore_missing", "warning", "Filestore missing",
                                 f"{rows[0][0]} attachments point to files that are not in {info['filestore']['path']}. "
                                 "Images and assets may be broken.", "assets", "Regenerate assets"))
    return issues


def _runtime_issues(info: dict, modules: list[dict]) -> list[dict]:
    """Python environment (exists, requirements, modules' external dependencies) and wkhtmltopdf."""
    import toolchain as TC
    issues = []
    env = info.get("pyenv") or ""
    series = info.get("series") or (info.get("script") or {}).get("branch") or ""
    deps = TC.manifest_dependencies(modules)
    if env and not TC.env_exists(env):
        issues.append(_issue("pyenv_missing", "error", f"Python environment {env} not found",
                             f"Expected in {TC.env_path(env)}. Created with pyenv on Python "
                             f"{TC.python_for_major(int(major_of(env) or major_of(series) or 19))}, with the requirements "
                             f"of {branch_for_series(series) or 'the code'} (a few minutes).",
                             "env", f"Create {env}"))
    elif env:
        check = TC.check_env(env, TC.requirements_lines(branch_for_series(series)), list(deps["python"]))
        names = [TC._req_name(line) for line in check.get("missing", [])] + check.get("missing_modules", [])
        labels = [TC._req_name(line) for line in check.get("missing", [])]
        for mod in check.get("missing_modules", []):
            users = deps["python"].get(mod, [])
            labels.append(f"{mod} (for {', '.join(users[:3])}{'…' if len(users) > 3 else ''})")
        if names:
            issues.append(_issue(
                "python_packages", "warning", f"{len(names)} Python package(s) missing in {env}",
                ", ".join(labels[:12]) + ("…" if len(labels) > 12 else "") +
                f" — to install in the {env} environment: `{TC.env_path(env)}/bin/pip install {' '.join(names)}`",
                "pydeps", f"Install in {env}"))
    lacking = [b for b in deps["bin"] if b != "wkhtmltopdf" and not TC._which(b)]
    if lacking:
        issues.append(_issue("programs_missing", "warning", f"Program(s) not found: {', '.join(lacking)}",
                             "; ".join(f"{b} is needed by {', '.join(deps['bin'][b][:3])}" for b in lacking) +
                             ". Install them for the whole Mac (e.g. with Homebrew)."))
    if not TC.wkhtmltopdf_path():
        issues.append(_issue("wkhtmltopdf_missing", "warning", "wkhtmltopdf not installed",
                             "PDF reports (invoices, quotations, delivery slips…) cannot be printed. "
                             "It is a program for the whole Mac, not a package of the Python environment: "
                             "version 0.12.6 with patched Qt (macOS asks for your password).",
                             "wkhtmltopdf", "Install wkhtmltopdf"))
    return issues


def doctor(db: str) -> dict:
    info = database_info(db)
    return {"ok": True, "name": db, "healthy": not any(i["severity"] == "error" for i in info["issues"]),
            "issues": info["issues"]}


# --------------------------------------------------------------------------- #
# Repairs
# --------------------------------------------------------------------------- #

REPAIRS = {
    "locks": "Reset modules stuck in 'to install/to upgrade/to remove'",
    "script": "Create or fix the launch script (branch, addons path)",
    "addons": "Add the folders holding missing modules to the addons path",
    "branch": "Switch core/enterprise to the database's branch",
    "assets": "Delete compiled asset bundles so Odoo regenerates them",
    "port": "Move the database to a free HTTP port",
    "admin": "Reset the admin user (id 2) to login 'admin' / password 'admin'",
    "init": "Initialise an empty database (install base)",
    "env": "Create the database's Python environment (pyenv, requirements of its branch)",
    "pydeps": "Install the missing Python packages in the database's environment",
    "wkhtmltopdf": "Install wkhtmltopdf 0.12.6 for the whole Mac (asks for the password)",
}


def repair(db: str, fixes: Optional[list[str]] = None, force: bool = False, log: Log = None) -> dict:
    """Apply fixes. Without `fixes`, applies every fix suggested by the diagnosis
    (except `branch`, which affects the other databases, unless asked explicitly)."""
    info = database_info(db)
    suggested = [i["fix"] for i in info["issues"] if i.get("fix")]
    if not fixes:
        fixes = [f for f in dict.fromkeys(suggested) if f != "branch"]
    unknown = [f for f in fixes if f not in REPAIRS]
    if unknown:
        raise DBError(f"Unknown fix: {', '.join(unknown)}", hint="Available: " + ", ".join(REPAIRS), code="bad_args")
    results = []
    for fix in fixes:
        try:
            msg = _apply_fix(db, fix, info, force=force, log=log)
            results.append({"fix": fix, "ok": True, "message": msg})
        except DBError as e:
            results.append({"fix": fix, "ok": False, "message": e.message, "hint": e.hint})
        if log:
            r = results[-1]
            log(("✓ " if r["ok"] else "✗ ") + f"{fix}: {r['message']}")
    after = doctor(db)
    return {"ok": all(r["ok"] for r in results), "name": db, "applied": results, "remaining_issues": after["issues"]}


def _apply_fix(db: str, fix: str, info: dict, force: bool, log: Log) -> str:
    sc = info.get("script")
    if fix == "locks":
        if not ops.clear_module_locks(db):
            raise DBError("Could not reset the module states.")
        return "Pending module states reset."
    if fix == "assets":
        ok, rows = psql(db, "DELETE FROM ir_attachment WHERE url LIKE '/web/assets/%' OR "
                            "(res_model = 'ir.ui.view' AND name LIKE '%.assets_%') RETURNING id")
        if not ok:
            raise DBError(rows[0][0] if rows else "Query failed")
        return f"{len(rows)} compiled asset(s) deleted; they are rebuilt on the next page load."
    if fix == "admin":
        ok, rows = psql(db, "UPDATE res_users SET login = 'admin', password = 'admin', active = true WHERE id = 2 RETURNING id")
        if not ok:
            raise DBError(rows[0][0] if rows else "Query failed")
        return "Admin reset to admin / admin (the password is hashed at the next start)."
    if fix == "branch":
        target = branch_for_series(info.get("series") or (sc or {}).get("branch") or "")
        if not target:
            raise DBError("Unknown database version.")
        return ensure_branch(target, db=db, force=force, log=log)
    if fix in ("env", "pydeps", "wkhtmltopdf"):
        import toolchain as TC
        env = info.get("pyenv") or ""
        series = info.get("series") or (sc or {}).get("branch") or ""
        if fix == "wkhtmltopdf":
            return TC.install_wkhtmltopdf(log)
        if fix == "env":
            res = TC.create_env(env, branch_for_series(series), log=log)
        else:
            deps = TC.manifest_dependencies(list_modules(db, info.get("addons_path")))
            check = TC.check_env(env, TC.requirements_lines(branch_for_series(series)), list(deps["python"]))
            res = TC.install_packages(env, check.get("missing", []) + check.get("missing_modules", []), log)
        if not res["ok"]:
            raise DBError(res["message"], hint="Install the failed ones by hand in that environment.", code="pip")
        return res["message"]
    if fix == "port":
        port = free_port(exclude_db=db)
        ops.set_db_port(db, port)
        return f"Port set to {port}."
    if fix == "init":
        series = (sc or {}).get("branch") or code_series()
        ensure_branch(branch_for_series(series), db=db, force=force, log=log)
        res = run_odoo_task(db, ["-i", "base", "--stop-after-init"], log=log)
        return res["message"]
    if fix in ("script", "addons"):
        series = info.get("series") or (sc or {}).get("branch") or code_series()
        addons = list((sc or {}).get("addons_path") or [])
        if not addons:
            addons = ops.build_addons_path(str(community_path()), []).split(",")
        addons = [a for a in addons if Path(a).expanduser().is_dir()]
        missing = [m["name"] for m in list_modules(db, addons) if m["state"] == "installed" and m["source"] == "missing"]
        for folder in ops._detect_module_parent_dirs(str(community_path()), missing):
            if folder not in addons:
                addons.append(folder)
        write_script(db, branch=branch_for_series(series), addons=addons,
                     install=(sc or {}).get("install_modules") or [],
                     update=(sc or {}).get("update_modules") or [],
                     update_mode=(sc or {}).get("update_mode") or "reinit",
                     subdir=(sc or {}).get("subdir") or None)
        return f"Launch script written ({len(addons)} addons folders, branch {branch_for_series(series)})."
    raise DBError(f"Unknown fix {fix}")


def free_port(exclude_db: str = "") -> int:
    used = {p["port"] for p in odoo_processes() if p["db"] != exclude_db}
    used |= {v for k, v in config.get_db_ports().items() if k != exclude_db and isinstance(v, int)}
    port = 8069
    while port in used:
        port += 1
    return port


# --------------------------------------------------------------------------- #
# Branches
# --------------------------------------------------------------------------- #

def ensure_branch(branch: str, db: str = "", force: bool = False, log: Log = None) -> str:
    """Put core + enterprise on `branch`. Refuses while a database of another version runs."""
    branch = norm_series(branch) if branch.startswith("saas") else branch
    current = ops.get_current_branch(str(community_path()))
    if current == branch:
        return f"Repos already on {branch}."
    if not force:
        others = [p["db"] for p in odoo_processes() if p["kind"] == "server" and p["db"] != db]
        if others:
            raise DBError(
                f"Switching to {branch} would pull the code from under the running database(s): {', '.join(others)}.",
                hint="Stop them first (`odoodb stop <db>`) or pass --force.", code="busy")
    if log:
        log(f"→ git checkout {branch} (core + enterprise)")
    ok, msg = ops.switch_branch(str(community_path()), branch)
    if not ok:
        raise DBError(msg, hint=f"Commit or stash local changes, or fetch the branch: `git -C {community_path()} fetch origin {branch}`.",
                      code="git")
    return f"Repos switched from {current} to {branch}."


def branches() -> dict:
    comm = community_path()
    return {"ok": True, "current": ops.get_current_branch(str(comm)), "code_series": code_series(),
            "branches": ops.get_git_branches(str(comm))}


# --------------------------------------------------------------------------- #
# Scripts writing
# --------------------------------------------------------------------------- #

def write_script(db: str, branch: str, addons: list[str], install: list[str], update: list[str],
                 update_mode: str = "reinit", subdir: Optional[str] = None, pyenv: str = "") -> str:
    existing = script_for(db)
    scripts_dir = str(Path(existing["path"]).parent) if existing else config.get_scripts_paths()[0]
    sub = None if existing else subdir
    pyenv = pyenv or pyenv_for_series(branch)
    ok, msg = ops.create_launch_script(
        str(community_path()), db, ",".join(addons), pyenv,
        version=major_of(branch) or "19", branch=branch, scripts_dir=scripts_dir, scripts_subdir=sub,
        install_modules=install, update_modules=update, update_mode=update_mode,
    )
    if not ok:
        raise DBError(msg, code="script")
    if existing and Path(existing["path"]) != Path(msg):
        try:
            Path(existing["path"]).unlink()
        except OSError:
            pass
    return msg


# --------------------------------------------------------------------------- #
# Start / stop
# --------------------------------------------------------------------------- #

def _python_for(pyenv: str) -> str:
    root = Path(config.get_pyenv_root() or "~/.pyenv").expanduser()
    py = root / "versions" / pyenv / "bin" / "python3"
    return str(py) if py.exists() else "python3"


def _check_startable(db: str, info: dict, switch_branch: bool, force: bool, log: Log) -> None:
    series = info.get("series") or (info.get("script") or {}).get("branch") or ""
    code = info.get("code_series") or ""
    if series and code and norm_series(series) != norm_series(code):
        if not switch_branch:
            raise DBError(
                f"'{db}' is a {series} database but core/enterprise are on {code}.",
                hint=f"Retry with --switch-branch to checkout {branch_for_series(series)} (stops nothing, "
                     "refuses if another database is running).", code="branch_mismatch")
        msg = ensure_branch(branch_for_series(series), db=db, force=force, log=log)
        if log:
            log("✓ " + msg)
    env = info.get("pyenv") or ""
    root = Path(config.get_pyenv_root() or "~/.pyenv").expanduser()
    if env and not (root / "versions" / env / "bin" / "python3").exists():
        raise DBError(f"The Python environment {env} does not exist.",
                      hint=f"Create it: `odoodb repair {db} --fix env` (or `odoodb env create {env}`).", code="env_missing")
    port = info.get("port")
    clash = [p for p in odoo_processes() if p["kind"] == "server" and p["db"] != db and p["port"] == port]
    if clash:
        raise DBError(f"Port {port} is already used by '{clash[0]['db']}'.",
                      hint=f"Stop it, or move this database: `odoodb port {db} {free_port(db)}`.", code="port_busy")


TERMINAL_KINDS = ("server", "shell", "zsh")


def terminal_command(db: str, kind: str = "server", switch_branch: bool = False, force: bool = False,
                     log: Log = None) -> dict:
    """What the app's built-in terminal runs for a database, prepared like `start` (branch, locks, script,
    port, log rotation): `server` = the launch script in the foreground (Ctrl+C, breakpoints), `shell` =
    `odoo-bin shell`, `zsh` = a shell in the community folder with the database's Python environment first
    in PATH. The app writes the terminal output to `log` so the Logs and History tabs see it."""
    if kind not in TERMINAL_KINDS:
        raise DBError(f"Unknown terminal kind {kind}", hint="One of: " + ", ".join(TERMINAL_KINDS), code="bad_args")
    info = database_info(db)
    sc = info.get("script")
    pyenv = info.get("pyenv") or ""
    env = _script_env(pyenv)
    env.update({"TERM": "xterm-256color", "COLORTERM": "truecolor", "ODOODB_DB": db})
    env.pop("NO_COLOR", None)
    py_bin = str(Path(config.get_pyenv_root() or "~/.pyenv").expanduser() / "versions" / pyenv / "bin")
    if kind == "zsh":
        env["PATH"] = py_bin + ":" + env.get("PATH", "")
        env["VIRTUAL_ENV"] = str(Path(py_bin).parent)
        return {"ok": True, "name": db, "kind": kind, "title": f"zsh · {db}", "executable": "/bin/zsh",
                "args": ["-l"], "cwd": str(community_path()), "env": env, "log": None}
    if kind == "server" and info.get("running"):
        raise DBError(f"{db} is already running ({info['running'].get('mode')}).",
                      hint=f"Stop it first: `odoodb stop {db}`.", code="running")
    # Checks first: a refused run must not have changed the database.
    _check_startable(db, {**info, "port": info["port"] if kind == "server" else None}, switch_branch, force, log)
    if info.get("modules_locked"):
        ops.clear_module_locks(db)
        if log:
            log("✓ Pending module states reset")
    if kind == "shell":
        addons = effective_addons_path(db, sc)
        args = [str(community_path() / "odoo-bin"), "shell", "-d", db, f"--addons-path={','.join(addons)}"]
        return {"ok": True, "name": db, "kind": kind, "title": f"odoo shell · {db}",
                "executable": str(Path(py_bin) / "python3"), "args": args, "cwd": str(community_path()),
                "env": env, "log": None}
    if not sc:
        _apply_fix(db, "script", info, force=force, log=log)
        sc = script_for(db)
    lp = _rotate_log(db)
    lp.touch()
    try:
        _pid_path(db).unlink()  # not a background run: `ps` shows it as "terminal"
    except OSError:
        pass
    return {"ok": True, "name": db, "kind": kind, "title": f"{db} · {info.get('series') or ''}".strip(" ·"),
            "executable": "/bin/bash", "args": [sc["path"]], "cwd": str(odoo_root()), "env": env,
            "log": str(lp), "url": info["url"]}


def start(db: str, terminal: bool = False, wait: bool = True, switch_branch: bool = False,
          force: bool = False, timeout: int = 600, log: Log = None) -> dict:
    info = database_info(db)
    if info.get("running"):
        return {"ok": True, "name": db, "already_running": True, "url": info["url"], "running": info["running"]}
    # Checks first: a refused start must not have changed the database.
    _check_startable(db, info, switch_branch, force, log)
    if info.get("modules_locked"):
        ops.clear_module_locks(db)
        if log:
            log("✓ Pending module states reset")
    sc = info.get("script")
    if not sc:
        _apply_fix(db, "script", info, force=force, log=log)
        sc = script_for(db)
        if log:
            log(f"✓ Launch script created: {sc['path']}")
    pyenv = config.get_pyenv_global_env() or sc.get("pyenv") or info.get("pyenv") or ""
    if terminal:
        ok, msg = ops.run_script_for_db(Path(sc["path"]), odoo_root=str(community_path()), pyenv_env=pyenv or None)
        if not ok:
            raise DBError(msg, code="terminal")
        return {"ok": True, "name": db, "mode": "terminal", "message": msg, "url": info["url"],
                "note": "Running in the terminal: logs are not captured by odoodb."}
    return _spawn_script(db, Path(sc["path"]), pyenv, wait=wait, timeout=timeout, url=info["url"], log=log)


def _rotate_log(db: str) -> Path:
    """Before a new run: the history reads the end of the previous log, which becomes <db>.previous.log."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    lp = log_path(db)
    if lp.exists() and lp.stat().st_size:
        history.ingest(db, final=True)
        lp.replace(lp.with_suffix(".previous.log"))
    return lp


def _script_env(pyenv: str) -> dict:
    env = ops._env_with_path()
    for k in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH", "PYENV_VERSION", "CONDA_PREFIX"):
        env.pop(k, None)
    if pyenv:
        env["PYENV_ENV"] = pyenv
        env["PYENV_VERSION"] = pyenv
    env["PSQL_BIN"] = ops._find_psql()
    return env


def _spawn_script(db: str, script: Path, pyenv: str, wait: bool, timeout: int, url: str, log: Log) -> dict:
    lp = _rotate_log(db)
    env = _script_env(pyenv)
    with open(lp, "ab") as fh:
        proc = subprocess.Popen(["/bin/bash", str(script)], cwd=str(odoo_root()), env=env,
                                stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                                start_new_session=True)
    _pid_path(db).write_text(str(proc.pid))
    if log:
        log(f"→ Started {script.name} (pid {proc.pid}), log: {lp}")
    result = {"ok": True, "name": db, "mode": "background", "pid": proc.pid, "url": url, "log": str(lp)}
    if not wait:
        result["message"] = "Starting in the background."
        return result
    state = wait_ready(db, proc, timeout=timeout, log=log)
    result.update(state)
    return result


def wait_ready(db: str, proc: Optional[subprocess.Popen], timeout: int, log: Log = None) -> dict:
    lp = log_path(db)
    deadline = time.time() + timeout
    pos = 0
    errors: list[str] = []
    while time.time() < deadline:
        try:
            with open(lp, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(pos)
                chunk = fh.read()
                pos = fh.tell()
        except OSError:
            chunk = ""
        for line in chunk.splitlines():
            if log and (" INFO " in line and ("loading" in line or "Registry" in line) or " ERROR " in line
                        or " CRITICAL " in line or " WARNING " in line or not LOG_LINE_RE.match(line)):
                log(line)
            if " ERROR " in line or " CRITICAL " in line or line.startswith("Traceback"):
                errors.append(line)
            if READY_RE.search(line):
                return {"ready": True, "message": "Odoo is up.", "errors": errors[-10:]}
        if proc is not None and proc.poll() is not None:
            tail = read_log(db, lines=40)["lines"]
            return {"ok": False, "ready": False, "exit_code": proc.returncode,
                    "error": "Odoo exited during startup.", "errors": errors[-10:], "log_tail": tail,
                    "hint": f"Read the log (`odoodb logs {db}`), then `odoodb doctor {db}`."}
        time.sleep(0.5)
    return {"ready": False, "message": f"Still loading after {timeout}s (it keeps running).", "errors": errors[-10:]}


def stop(db: str, timeout: int = 15) -> dict:
    procs = [p for p in odoo_processes() if p["db"] == db and p["kind"] == "server"]
    if not procs:
        return {"ok": True, "name": db, "was_running": False, "message": f"{db} is not running."}
    ok, msg = ops.stop_odoo_server(db)
    deadline = time.time() + timeout
    while time.time() < deadline and running_server(db):
        time.sleep(0.3)
    if running_server(db):
        for p in odoo_processes():
            if p["db"] == db:
                try:
                    os.kill(p["pid"], signal.SIGKILL)
                except OSError:
                    pass
        msg = f"{db} killed (did not stop within {timeout}s)."
    try:
        _pid_path(db).unlink()
    except OSError:
        pass
    return {"ok": True, "name": db, "was_running": True, "message": msg}


def restart(db: str, wait: bool = True, log: Log = None) -> dict:
    prev = running_server(db)
    stop(db)
    terminal = bool(prev and prev["mode"] == "terminal")
    return start(db, terminal=terminal, wait=wait, log=log)


def read_log(db: str, lines: int = 200, errors_only: bool = False) -> dict:
    lp = log_path(db)
    if not lp.is_file():
        run = running_server(db)
        note = ("Running in an external terminal: its output is not captured. Restart it from the app "
                "(background or built-in terminal) to capture logs."
                if run else "No log yet: start the database with `odoodb start`.")
        return {"ok": True, "name": db, "path": str(lp), "lines": [], "note": note}
    with open(lp, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - 2_000_000))
        text = fh.read().decode("utf-8", errors="replace")
    out = text.splitlines()
    if errors_only:
        keep, in_tb = [], False
        for line in out:
            if LOG_LINE_RE.match(line):
                in_tb = " ERROR " in line or " CRITICAL " in line
            elif line.startswith("Traceback"):
                in_tb = True
            if in_tb:
                keep.append(line)
        out = keep
    return {"ok": True, "name": db, "path": str(lp), "lines": out[-lines:]}


# --------------------------------------------------------------------------- #
# Odoo one-shot tasks (update, init, neutralize, i18n)
# --------------------------------------------------------------------------- #

def run_odoo_task(db: str, args: list[str], log: Log = None, timeout: int = 3600, subcommand: str = "") -> dict:
    """Run odoo-bin once on the database (server must be stopped), streaming the output."""
    if running_server(db):
        raise DBError(f"{db} is running.", hint=f"Stop it first: `odoodb stop {db}`.", code="running")
    sc = script_for(db)
    addons = effective_addons_path(db, sc)
    summary = _db_summary(db)
    series = summary.get("series") or (sc or {}).get("branch") or code_series()
    pyenv = config.get_pyenv_global_env() or (sc or {}).get("pyenv") or pyenv_for_series(series)
    cmd = [_python_for(pyenv), str(community_path() / "odoo-bin")]
    if subcommand:
        cmd += [subcommand]
    cmd += [f"--addons-path={','.join(addons)}", "-d", db] + args
    if not subcommand:
        cmd += ["--no-http"]
    if log:
        log("$ " + " ".join(shlex.quote(c) for c in cmd))
    errors: list[str] = []
    # Appended to the database's log: the Logs tab and the history see what an update or install raised.
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_path(db), "a", encoding="utf-8")
    log_fh.write(f"# odoodb: {' '.join(shlex.quote(c) for c in cmd)}\n")

    def on_line(line: str):
        if " ERROR " in line or " CRITICAL " in line:
            errors.append(line)
        log_fh.write(line + "\n")
        if log:
            log(line)

    try:
        code, out = ops.run_cmd_streaming(cmd, cwd=str(community_path()), on_output=on_line)
    finally:
        log_fh.close()
        history.ingest(db, final=True)
    ok = code == 0
    return {"ok": ok, "name": db, "exit_code": code, "errors": errors[-10:],
            "message": "Done." if ok else f"odoo-bin exited with code {code}.",
            **({} if ok else {"log_tail": out.splitlines()[-30:]})}


def update_modules(db: str, modules: list[str], log: Log = None, switch_branch: bool = False, force: bool = False) -> dict:
    info = database_info(db)
    _check_startable(db, {**info, "port": None}, switch_branch, force, log)
    ops.clear_module_locks(db)
    mods = ",".join(modules) if modules else "all"
    return run_odoo_task(db, ["-u", mods, "--stop-after-init"], log=log)


def install_modules(db: str, modules: list[str], log: Log = None, switch_branch: bool = False, force: bool = False) -> dict:
    info = database_info(db)
    _check_startable(db, {**info, "port": None}, switch_branch, force, log)
    return run_odoo_task(db, ["-i", ",".join(modules), "--stop-after-init"], log=log)


def neutralize(db: str, log: Log = None) -> dict:
    series = _db_summary(db).get("series", "")
    major = int(major_of(series) or 0)
    if major >= 16:
        res = run_odoo_task(db, [], log=log, subcommand="neutralize")
        if res["ok"]:
            res["message"] = "Database neutralized (odoo-bin neutralize)."
        return res
    lines = ops.sanitize_database(db)
    return {"ok": all(line.startswith("✓") for line in lines), "name": db, "log": lines}


# --------------------------------------------------------------------------- #
# Create / duplicate / rename / delete
# --------------------------------------------------------------------------- #

def create(db: str, branch: str = "", modules: Optional[list[str]] = None, addons: Optional[list[str]] = None,
           dump: str = "", filestore: str = "", sanitize: bool = True, start_server: bool = True,
           force: bool = False, subdir: str = "", log: Log = None, timeout: int = 1800) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.\-]*", db or ""):
        raise DBError("Invalid database name (letters, digits, '-', '_' and '.' only).", code="bad_args")
    if db in database_names():
        raise DBError(f"Database '{db}' already exists.",
                      hint=f"Start it with `odoodb start {db}`, or pick another name.", code="exists")
    modules = [m for m in (modules or []) if m]
    extra = [str(Path(a).expanduser().resolve()) for a in (addons or []) if a]
    for a in extra:
        if not Path(a).is_dir():
            raise DBError(f"Addons folder not found: {a}", code="bad_args")
    if dump:
        if not Path(dump).expanduser().is_file():
            raise DBError(f"Dump not found: {dump}", code="bad_args")
        if log:
            log(f"→ Restoring {dump}")
        dump, filestore = _prepare_dump(Path(dump).expanduser(), filestore, log)
        if _is_custom_dump(dump):
            ok, msg, lines = _restore_custom(db, dump, filestore, sanitize)
        else:
            ok, msg, lines = ops.create_db_from_dump(db, dump, filestore_path=filestore or None, sanitize=sanitize)
        for line in lines:
            if log:
                log(line)
        if not ok:
            raise DBError(msg, code="restore")
        branch = branch or branch_for_series(_db_summary(db).get("series", "")) or code_series()
    branch = branch or ops.get_current_branch(str(community_path())) or "19.0"
    msg = ensure_branch(branch, db=db, force=force, log=log)
    if log:
        log("✓ " + msg)
    addons_path, detected, missing = ops.build_addons_path_for_modules(str(community_path()), extra, modules)
    if missing:
        raise DBError(f"Module(s) not found in any addons folder: {', '.join(missing)}",
                      hint="Pass the folder that contains them with --addons.", code="module_not_found")
    if detected and log:
        log(f"✓ Addons folders detected: {', '.join(detected)}")
    env = config.get_pyenv_global_env() or pyenv_for_series(branch)
    import toolchain as TC
    if not TC.env_exists(env):
        if log:
            log(f"→ Python environment {env} missing: creating it (a few minutes, once)")
        res = TC.create_env(env, branch, log=log)
        if log:
            log(("✓ " if res["ok"] else "⚠ ") + res["message"])
    install = modules or (["base"] if not dump else [])
    # Re-initialise only the custom modules at each start (the developer's own code).
    custom_dirs = {a["path"] for a in custom_addons(addons_path.split(","))}
    where = module_dirs(addons_path.split(","))
    custom_mods = [m for m in modules if where.get(m) in custom_dirs]
    script = write_script(db, branch=branch, addons=addons_path.split(","), install=install,
                          update=custom_mods, update_mode="reinit" if custom_mods else "update",
                          subdir=subdir or None)
    if log:
        log(f"✓ Launch script: {script}")
    result = {"ok": True, "name": db, "branch": branch, "script": script, "addons_path": addons_path.split(",")}
    if dump and not start_server:
        return result
    if not start_server:
        res = run_odoo_task(db, ["-i", ",".join(install), "--stop-after-init", "--without-demo=all"], log=log)
        result.update({k: v for k, v in res.items() if k != "name"})
        return result
    port = config.get_db_port(db)
    clash = [p for p in odoo_processes() if p["kind"] == "server" and p["port"] == port]
    if clash:
        port = free_port(db)
        ops.set_db_port(db, port)
        if log:
            log(f"✓ Port {port} ({clash[0]['port']} is busy with {clash[0]['db']})")
    res = _spawn_script(db, Path(script), config.get_pyenv_global_env() or pyenv_for_series(branch),
                        wait=True, timeout=timeout, url=f"http://localhost:{config.get_db_port(db)}", log=log)
    result.update({k: v for k, v in res.items() if k != "name"})
    if res.get("ready"):
        result["login"] = "admin / admin"
    return result


def _is_custom_dump(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(5) == b"PGDMP"
    except OSError:
        return False


def _prepare_dump(path: Path, filestore: str, log: Log) -> tuple[str, str]:
    """Accepts a .sql, a pg_dump custom file, a .gz, or an Odoo backup .zip (dump.sql + filestore/)."""
    import tempfile
    import zipfile
    import gzip
    if zipfile.is_zipfile(path):
        tmp = Path(tempfile.mkdtemp(prefix="odoodb-restore-"))
        if log:
            log(f"→ Extracting {path.name}")
        with zipfile.ZipFile(path) as z:
            z.extractall(tmp)
        sql = next((p for p in (tmp / "dump.sql", *tmp.rglob("*.sql"), *tmp.rglob("*.dump")) if p.is_file()), None)
        if not sql:
            raise DBError("No dump.sql in the archive.", code="bad_args")
        fs = tmp / "filestore"
        return str(sql), filestore or (str(fs) if fs.is_dir() else "")
    if path.suffix == ".gz":
        tmp = Path(tempfile.mkdtemp(prefix="odoodb-restore-"))
        out = tmp / path.stem
        with gzip.open(path, "rb") as src, open(out, "wb") as dst:
            shutil.copyfileobj(src, dst)
        return str(out), filestore
    return str(path), filestore


def _restore_custom(db: str, dump: str, filestore: str, sanitize: bool) -> tuple[bool, str, list[str]]:
    lines = []
    code, out, err = _run([ops._find_createdb(), db])
    if code != 0:
        return False, f"createdb: {err or out}", lines
    lines.append(f"✓ Database {db} created")
    pg_restore = str(Path(ops._find_psql()).parent / "pg_restore") if "/" in ops._find_psql() else "pg_restore"
    code, out, err = _run([pg_restore, "--no-owner", "--no-acl", "-d", db, dump], timeout=7200)
    if code != 0 and "errors ignored" not in err:
        return False, f"pg_restore failed: {(err or out)[-800:]}", lines
    lines.append("✓ Dump restored (pg_restore)")
    if filestore and Path(filestore).is_dir():
        dest = ops.get_default_filestore_dir(db)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(filestore, dest, dirs_exist_ok=True)
        lines.append(f"✓ Filestore copied to {dest}")
    if sanitize:
        lines.extend(ops.sanitize_database(db))
    return True, f"{db} restored", lines


def duplicate(src: str, new: str) -> dict:
    ok, msg = ops.duplicate_database(src, new)
    if not ok:
        raise DBError(msg, code="duplicate")
    history.copy(src, new)
    return {"ok": True, "name": new, "source": src, "message": msg}


def rename(old: str, new: str) -> dict:
    require_db(old)
    if new in database_names():
        raise DBError(f"Database '{new}' already exists.", code="exists")
    stop(old)
    ops._terminate_db_connections(old)
    ok, rows = psql("postgres", f'ALTER DATABASE "{old}" RENAME TO "{new}"')
    if not ok:
        raise DBError(rows[0][0] if rows else "Rename failed", code="rename")
    msgs = [f"Database renamed to {new}"]
    src_fs, dst_fs = ops.get_default_filestore_dir(old), ops.get_default_filestore_dir(new)
    if src_fs.is_dir() and not dst_fs.exists():
        src_fs.rename(dst_fs)
        msgs.append("filestore moved")
    sc = script_for(old)
    if sc:
        p = Path(sc["path"])
        text = re.sub(r"^DB=.*$", f"DB={new}", p.read_text(encoding="utf-8"), count=1, flags=re.MULTILINE)
        target = p.with_name(f"odoo-{new}.sh")
        target.write_text(text, encoding="utf-8")
        target.chmod(0o755)
        if target != p:
            p.unlink()
        msgs.append(f"script {target.name}")
    ports = config.get_db_ports()
    if old in ports:
        config.save_db_port(new, ports[old])
    history.rename(old, new)
    for suffix in (".log", ".previous.log"):
        try:
            (LOG_DIR / f"{old}{suffix}").replace(LOG_DIR / f"{new}{suffix}")
        except OSError:
            pass
    return {"ok": True, "name": new, "previous": old, "message": ", ".join(msgs)}


def delete(db: str) -> dict:
    require_db(db)
    ok, msg = ops.delete_db_complete(db)
    if not ok:
        raise DBError(msg, code="delete")
    fs = ops.get_default_filestore_dir(db)
    if fs.is_dir():
        shutil.rmtree(fs, ignore_errors=True)
        msg += ", filestore deleted"
    for p in (log_path(db), _pid_path(db), log_path(db).with_suffix(".previous.log")):
        try:
            p.unlink()
        except OSError:
            pass
    history.drop(db)
    return {"ok": True, "name": db, "message": msg}


_LEAF_MODULES_SQL = """
    SELECT m.name FROM ir_module_module m
    WHERE m.state = 'installed' AND NOT m.auto_install AND NOT EXISTS (
        SELECT 1 FROM ir_module_module_dependency d
        JOIN ir_module_module dep ON dep.id = d.module_id AND dep.state = 'installed' AND NOT dep.auto_install
        WHERE d.name = m.name)
    ORDER BY m.name"""


def installed_leaf_modules(db: str, addons: list[str]) -> list[str]:
    """The installed modules no other installed module depends on (auto-install ones excepted, they follow):
    installing them again gives the same set of modules. Modules whose code is no longer in the addons path
    are left out."""
    ok, rows = psql(db, _LEAF_MODULES_SQL)
    if not ok:
        return []
    where = module_dirs(addons)
    return [r[0] for r in rows if r and r[0] in where]


def reset(db: str, modules: Optional[list[str]] = None, same_modules: bool = False, start_server: bool = True,
          switch_branch: bool = False, force: bool = False, log: Log = None, timeout: int = 1800) -> dict:
    """Drop the database and its filestore, then create it again right away under the same name, with the
    same launch script (branch, addons path, folder), port, logs and history.

    Modules installed in the new database: `modules` if given, else the currently installed ones with
    `same_modules`, else the INSTALL_MODULES of its launch script (what it was created with), else the
    currently installed ones."""
    exists = db in database_names()
    sc = script_for(db)
    if not exists and not sc:
        raise DBError(f"Database '{db}' does not exist.", hint="Run `odoodb list` to see the databases.",
                      code="not_found")
    if exists:
        info = database_info(db)
        sc = info.get("script")
    else:  # an earlier reset dropped it but did not get to recreate it: its script is the recipe
        branch = sc.get("branch") or code_series()
        info = {"script": sc, "series": "", "code_series": code_series(), "pyenv": effective_env(sc, branch),
                "port": sc.get("port") or config.get_db_port(db)}
    series = info.get("series") or (sc or {}).get("branch") or code_series()
    addons = effective_addons_path(db, sc)

    install = [m for m in (modules or []) if m]
    if not install and not same_modules:
        install = list((sc or {}).get("install_modules") or [])
    if not install and exists:
        install = installed_leaf_modules(db, addons)
    install = install or ["base"]

    # Checks first: a refused reset must not have dropped anything.
    _check_startable(db, {**info, "port": info.get("port") if start_server else None}, switch_branch, force, log)

    if exists:
        if running_server(db):
            stop(db)
            if log:
                log(f"✓ {db} stopped")
        ops._terminate_db_connections(db)
        code, out, err = _run([ops._find_dropdb(), db], timeout=120)
        if code != 0 and db in database_names():
            raise DBError(f"dropdb: {(err or out).strip()}", hint="Close the other connections (psql, pgAdmin…) "
                          "to the database and retry.", code="reset")
        if log:
            log(f"✓ Database {db} dropped")
    fs = ops.get_default_filestore_dir(db)
    if fs.is_dir():
        shutil.rmtree(fs, ignore_errors=True)
        if log:
            log("✓ Filestore deleted")

    # The launch script installs its INSTALL_MODULES when the database does not exist: starting it is enough.
    # Other modules are installed by a one-shot run first, so the script (its recipe) is left untouched.
    by_script = bool(sc and sc.get("managed")) and install == sc.get("install_modules")
    if log:
        log(f"→ Recreating {db} with: {', '.join(install)}")

    result = {"ok": True, "name": db, "modules": install, "script": (sc or {}).get("path"),
              "port": info.get("port")}
    if not start_server or not by_script:
        res = run_odoo_task(db, ["-i", ",".join(install), "--stop-after-init"], log=log, timeout=timeout)
        if not res["ok"]:
            result.update({k: v for k, v in res.items() if k != "name"})
            result["hint"] = f"Read the log (`odoodb logs {db}`), fix the module, then `odoodb reset {db} --yes` again."
            return result
        if not start_server:
            result["message"] = f"{db} recreated (not started)."
            return result
        res = start(db, timeout=timeout, log=log)
    else:
        res = _spawn_script(db, Path(sc["path"]), effective_env(sc, series), wait=True, timeout=timeout,
                            url=f"http://localhost:{info.get('port') or config.get_db_port(db)}", log=log)
    result.update({k: v for k, v in res.items() if k != "name"})
    if res.get("ready"):
        result["login"] = "admin / admin"
    result.setdefault("message", f"{db} recreated.")
    return result


MODULE_RE = re.compile(r"[A-Za-z0-9_]+")


def python_envs() -> list[str]:
    """pyenv environments and Python versions installed (names usable as PYENV_ENV)."""
    root = Path(config.get_pyenv_root() or "~/.pyenv").expanduser() / "versions"
    if not root.is_dir():
        return []
    return sorted((d.name for d in root.iterdir() if (d / "bin" / "python3").exists()),
                  key=lambda n: (not n.startswith("odoo-"), n))


def script_settings(db: str) -> dict:
    """The launch script of a database, parsed, with what its editor offers (Python environments)."""
    sc = script_for(db)
    return {"ok": True, "name": db, "script": sc, "exists": db in database_names(),
            "running": bool(running_server(db)), "port": (sc or {}).get("port") or config.get_db_port(db),
            "envs": python_envs()}


def edit_script(db: str, branch: Optional[str] = None, addons: Optional[list[str]] = None,
                install: Optional[list[str]] = None, update: Optional[list[str]] = None,
                update_mode: Optional[str] = None, port: Optional[int] = None, pyenv: Optional[str] = None) -> dict:
    """Rewrite a database's launch script with some settings changed (None = keep). Takes effect at the
    next start. A script odoodb did not write is kept next to it as <name>.sh.bak."""
    sc = script_for(db)
    if not sc:
        if db not in database_names():
            raise DBError(f"Database '{db}' has no launch script and does not exist.", code="not_found")
        raise DBError(f"{db} has no launch script yet.", hint=f"Create it: `odoodb repair {db} --fix script`.",
                      code="no_script")
    branch = (branch if branch is not None else sc.get("branch") or "").strip() or code_series()
    addons = [str(Path(a).expanduser()) for a in (addons if addons is not None else sc["addons_path"]) if a.strip()]
    if not addons:
        raise DBError("The addons path cannot be empty.", code="bad_args")
    missing = [a for a in addons if not Path(a).is_dir()]
    if missing:
        raise DBError(f"Addons folder not found: {', '.join(missing)}", code="bad_args")
    install = [m for m in (install if install is not None else sc["install_modules"]) if m]
    update = [m for m in (update if update is not None else sc["update_modules"]) if m]
    bad = [m for m in install + update if not MODULE_RE.fullmatch(m)]
    if bad:
        raise DBError(f"Invalid module name(s): {', '.join(bad)}", code="bad_args")
    update_mode = update_mode or sc.get("update_mode") or "reinit"
    if update_mode not in ("update", "reinit"):
        raise DBError("update_mode is 'update' (-u) or 'reinit' (--reinit).", code="bad_args")
    if pyenv is None:
        # A new major version needs its own environment, unless one was chosen.
        same_major = major_of(branch) == major_of(sc.get("branch") or "")
        pyenv = (sc.get("pyenv") if same_major else "") or pyenv_for_series(branch)
    if port is not None:
        if not 1024 <= port <= 65535:
            raise DBError("The port must be between 1024 and 65535.", code="bad_args")
        config.save_db_port(db, port)
    elif sc.get("port"):
        config.save_db_port(db, sc["port"])  # create_launch_script reads the port from the settings
    if not sc.get("managed"):
        shutil.copy2(sc["path"], sc["path"] + ".bak")
    path = write_script(db, branch=branch, addons=addons, install=install, update=update,
                        update_mode=update_mode, subdir=sc.get("subdir") or None, pyenv=pyenv.strip())
    res = {"ok": True, "name": db, "script": script_for(db), "message": f"Launch script of {db} saved."}
    if not sc.get("managed"):
        res["backup"] = sc["path"] + ".bak"
    if running_server(db):
        res["note"] = "Restart the database to apply."
    return res


def set_port(db: str, port: Optional[int]) -> dict:
    if port is None:
        sc = script_for(db)
        return {"ok": True, "name": db, "port": (sc or {}).get("port") or config.get_db_port(db)}
    ok, msg = ops.set_db_port(db, port)
    if not ok:
        raise DBError(msg)
    return {"ok": True, "name": db, "port": port, "message": msg,
            "note": "Restart the database to apply." if running_server(db) else ""}


# --------------------------------------------------------------------------- #
# Translation
# --------------------------------------------------------------------------- #

def translate(db: str, module: str, lang: str, out: str = "", ai: bool = True, log: Log = None) -> dict:
    """Export the module's .po for `lang`, translate it with the configured AI, write it.

    Default destination: the module's own i18n/ folder (custom modules), else ~/Downloads."""
    require_db(db)
    if running_server(db):
        raise DBError(f"{db} is running: odoo-bin cannot export while the server holds the database.",
                      hint=f"Stop it first: `odoodb stop {db}`.", code="running")
    sc = script_for(db)
    loc = module_dirs(effective_addons_path(db, sc)).get(module)
    if out:
        dest = Path(out).expanduser()
        if dest.is_dir() or not dest.suffix:
            dest = dest / f"{lang}.po"
    elif loc and not loc.startswith(str(community_path())) and not loc.startswith(str(enterprise_path() or "\0")):
        short = lang.split("_")[0]
        dest = Path(loc) / module / "i18n" / (f"{short}.po" if lang.lower() in (f"{short}_{short}",) else f"{lang}.po")
    else:
        dest = Path.home() / "Downloads" / f"{module}_{lang}.po"
    if log:
        log(f"→ Exporting {module} ({lang})")
    ok, po_or_err = ops.export_module_po(db, module, lang, odoo_path=str(community_path()),
                                         version=major_of(_db_summary(db).get("series", "")) or None, on_output=log)
    if not ok:
        raise DBError(po_or_err, code="export")
    content = Path(po_or_err).read_text(encoding="utf-8")
    if ai:
        import ai_translate
        mode = config.get_ai_mode() or "claude_cli"
        if log:
            log(f"→ Translating with {'Claude Code' if mode == 'claude_cli' else config.get_ai_api_provider()}")
        if mode == "api_key":
            provider = config.get_ai_api_provider()
            ok, content = ai_translate.translate_with_api_key(content, lang, provider, config.get_ai_api_key(provider), on_output=log)
        else:
            ok, content = ai_translate.translate_with_claude_cli(content, lang, on_output=log)
        if not ok:
            raise DBError(content, code="ai")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    entries = content.count('\nmsgid "') - 1
    if log:
        log(f"✓ Written {dest}")
    return {"ok": True, "name": db, "module": module, "lang": lang, "path": str(dest),
            "translated": ai, "entries": max(entries, 0)}


# --------------------------------------------------------------------------- #
# Misc
# --------------------------------------------------------------------------- #

def is_up(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url + "/web/webclient/version_info", data=b"{}", timeout=timeout):
            return True
    except Exception:
        return False


def settings() -> dict:
    cfg = config._load_config()
    return {
        "ok": True,
        "community_path": str(community_path()),
        "enterprise_path": str(enterprise_path() or ""),
        "scripts_paths": config.get_scripts_paths(),
        "pyenv_root": config.get_pyenv_root(),
        "pyenv_global_env": config.get_pyenv_global_env(),
        "psql": ops._find_psql(),
        "terminal_app": config.get_terminal_app(),
        "default_port": config.get_odoo_http_port(),
        "ai_mode": cfg.get("ai_mode") or "claude_cli",
        "logs_dir": str(LOG_DIR),
        "config_file": config._config_path(),
    }


def dumps(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)
