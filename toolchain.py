"""What the databases need on this Mac, and how to install it.

- PostgreSQL: Postgres.app (installed from its GitHub release, then started).
- Odoo code: the community / enterprise clones (one checkout each, branches switched as today).
- GitHub: the `gh` command (sign-in, git credentials), GitHub Desktop.
- Python: one pyenv environment per Odoo version (`odoo-19`…), created here, with the packages of
  the branch's requirements.txt and the `external_dependencies` of the installed modules.
- wkhtmltopdf: PDF reports.

`odoodb setup` lists every item with its state and the action that fixes it,
`odoodb setup --fix <action>` runs it. The app's Setup tab shows the same list.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

import config
import dbtools as T
import odoo_ops as ops

Log = Optional[Callable[[str], None]]

CACHE_DIR = Path.home() / "Library" / "Caches" / "Odoo Database Manager"
APP_DIRS = [Path("/Applications"), Path.home() / "Applications"]
REMOTES = {"community": "https://github.com/odoo/odoo.git", "enterprise": "https://github.com/odoo/enterprise.git"}
POSTGRES_RELEASES = "https://api.github.com/repos/PostgresApp/PostgresApp/releases/latest"
WKHTMLTOPDF_PKG = ("https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6-2/"
                   "wkhtmltox-0.12.6-2.macos-cocoa.pkg")
# The package is not signed: its content is pinned instead.
WKHTMLTOPDF_SHA256 = "81a66b77b508fede8dbcaa67127203748376568b3673a17f6611b6d51e9894f8"
# Requirements that need system libraries to build on a Mac: installed apart, a failure is only reported.
OPTIONAL_REQUIREMENTS = {"python-ldap"}
VERSION_BRANCH_RE = re.compile(r"^(master|\d+\.0|saas-\d+\.\d+)$")


def _log(log: Log, line: str) -> None:
    if log:
        log(line)


def _which(name: str) -> Optional[str]:
    return shutil.which(name, path=ops._env_with_path()["PATH"])


def _stream(cmd: list[str], log: Log, cwd: Optional[str] = None, env: Optional[dict] = None,
            keep: Callable[[str], bool] = lambda line: True) -> tuple[int, list[str]]:
    """Runs a command, forwards its output lines to `log` (those `keep` accepts), returns (code, lines)."""
    full_env = ops._env_with_path()
    for k in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH", "PYENV_VERSION"):
        full_env.pop(k, None)
    full_env.update(env or {})
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, env=full_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", bufsize=1)
    except OSError as e:
        _log(log, str(e))
        return -1, [str(e)]
    lines: list[str] = []
    for raw in proc.stdout:
        # git and pip redraw their progress with \r: only the last state of the line matters.
        line = raw.rstrip("\n").split("\r")[-1].rstrip()
        if not line:
            continue
        lines.append(line)
        if keep(line):
            _log(log, line)
    return proc.wait(), lines


def _download(url: str, dest: Path, log: Log) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "odoodb"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        done, last = 0, -10
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
            done += len(chunk)
            pct = int(done * 100 / total) if total else 0
            if total and pct >= last + 10:
                last = pct
                _log(log, f"  {pct}% of {total // (1 << 20)} MB")


def _app(name: str) -> Optional[Path]:
    for d in APP_DIRS:
        if (d / name).is_dir():
            return d / name
    return None


def _admin_shell(script: str) -> tuple[bool, str]:
    """Runs a shell script as root, macOS asks for the password in its own dialog."""
    escaped = script.replace("\\", "\\\\").replace('"', '\\"')
    code, out, err = ops.run_cmd(["osascript", "-e", f'do shell script "{escaped}" with administrator privileges'],
                                 timeout=900)
    if code != 0:
        return False, "Cancelled." if "-128" in err else (err.strip() or out.strip())
    return True, out.strip()


# --------------------------------------------------------------------------- #
# PostgreSQL (Postgres.app)
# --------------------------------------------------------------------------- #

def postgres_app() -> Optional[Path]:
    return _app("Postgres.app")


def postgres_status() -> dict:
    ok, rows = T.psql("postgres", "SHOW server_version", timeout=8)
    res = {"reachable": ok, "psql": ops._find_psql(), "app": str(postgres_app() or ""), "state": "running" if ok else "down"}
    if ok:
        res["version"] = rows[0][0] if rows else ""
        return res
    err = rows[0][0] if rows else ""
    res["error"] = err
    low = err.lower()
    if "shutting down" in low:
        # Smart shutdown: refuses new connections until the open ones (often Odoo servers) close.
        res["state"] = "shutting_down"
    elif "starting up" in low or "not yet accepting" in low or "in recovery" in low:
        res["state"] = "starting"
    elif "postgres.app" in low or "permission" in low or "rejected" in low:
        res["state"] = "denied"
    return res


def _postmasters() -> list[dict]:
    """Running PostgreSQL servers: binary, data directory, and from postmaster.pid their port, socket folder
    and status (ready, starting, stopping…)."""
    code, out, _ = ops.run_cmd(["ps", "-axww", "-o", "command="], timeout=10)
    found = []
    for line in out.splitlines():
        m = re.match(r"^(/\S+/bin/postgres)\s+-D\s+(.+?)(?=\s+-\w|\s*$)", line.strip())
        if not m or not Path(m.group(2)).is_dir():
            continue
        info = {"binary": m.group(1), "data": m.group(2), "port": None, "socket": "", "status": ""}
        try:
            lines = (Path(m.group(2)) / "postmaster.pid").read_text().splitlines()
            info["port"] = int(lines[3]) if len(lines) > 3 and lines[3].strip().isdigit() else None
            info["socket"] = lines[4].strip() if len(lines) > 4 else ""
            info["status"] = lines[7].strip() if len(lines) > 7 else ""
        except OSError:
            pass
        found.append(info)
    return found


def _postmaster() -> Optional[tuple[str, str]]:
    """(binary, data directory) of the server psql talks to (PGPORT, else 5432); among them, the stopping one."""
    port = int(os.environ.get("PGPORT") or 5432)
    servers = _postmasters()
    mine = [s for s in servers if s["port"] == port] or servers
    mine.sort(key=lambda s: s["status"] != "stopping")
    return (mine[0]["binary"], mine[0]["data"]) if mine else None


def postgres_restart(log: Log = None) -> str:
    """Restart of the running server with its own options, closing its sessions (Odoo servers reconnect).
    Ends a shutdown stuck waiting for open connections. Fast mode, then immediate if fast does not finish."""
    pm = _postmaster()
    if not pm:
        others = ", ".join(f"{s['data']} (port {s['port']})" for s in _postmasters())
        if others:
            raise T.DBError(f"No PostgreSQL server on port {os.environ.get('PGPORT') or 5432}; running: {others}.",
                            code="postgres_down")
        return postgres_start(log)
    binary, data = pm
    pg_ctl = str(Path(binary).with_name("pg_ctl"))
    errors = []
    for mode in ("fast", "immediate"):
        _log(log, f"→ pg_ctl restart -m {mode} ({data})")
        code, out, err = ops.run_cmd([pg_ctl, "restart", "-D", data, "-m", mode, "-w", "-t", "60",
                                      "-l", str(Path(data) / "postgresql.log")], timeout=150)
        if code == 0:
            break
        errors.append(f"{mode}: {(err or out).strip()[-300:]}")
        _log(log, f"⚠ {errors[-1]}")
    else:
        raise T.DBError("PostgreSQL could not be restarted. " + " · ".join(errors),
                        hint="In Postgres.app, click Stop then Start, or restart the Mac. "
                             f"Its log: {Path(data) / 'postgresql.log'}", code="postgres_down")
    if _wait_postgres(20):
        return "PostgreSQL restarted."
    status = postgres_status()
    raise T.DBError(f"PostgreSQL restarted but still refuses connections: {status.get('error', '')[:200]}",
                    hint=f"Check again in a few seconds. Log: {Path(data) / 'postgresql.log'}", code="postgres_down")


def _wait_postgres(seconds: int) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if T.psql("postgres", "SELECT 1", timeout=5)[0]:
            return True
        time.sleep(1)
    return False


def _brew_postgres_formula() -> Optional[str]:
    brew = _which("brew")
    if not brew:
        return None
    code, out, _ = ops.run_cmd([brew, "list", "--formula"], timeout=30)
    formulas = sorted((f for f in out.split() if re.fullmatch(r"postgresql(@\d+)?", f)), reverse=True)
    return formulas[0] if code == 0 and formulas else None


def postgres_start(log: Log = None) -> str:
    if postgres_status()["reachable"]:
        return "PostgreSQL is running."
    app = postgres_app()
    if app:
        _log(log, f"→ Opening {app.name}")
        ops.run_cmd(["open", "-g", str(app)], timeout=20)
        if _wait_postgres(25):
            return "PostgreSQL is running (Postgres.app)."
        ops.run_cmd(["open", str(app)], timeout=20)
        raise T.DBError("Postgres.app is open but its server is not running.",
                        hint="In the Postgres window, click Initialize (first time) or Start, then retry.",
                        code="postgres_down")
    formula = _brew_postgres_formula()
    if formula:
        _log(log, f"→ brew services start {formula}")
        ops.run_cmd([_which("brew"), "services", "start", formula], timeout=60)
        if _wait_postgres(20):
            return f"PostgreSQL is running (Homebrew {formula})."
        raise T.DBError(f"Homebrew {formula} did not start.", hint=f"Run `brew services info {formula}`.",
                        code="postgres_down")
    raise T.DBError("PostgreSQL is not installed.", hint="Install Postgres.app: `odoodb setup --fix postgres-install`.",
                    code="postgres_missing")


def postgres_install(log: Log = None) -> str:
    """Installs the latest Postgres.app (single PostgreSQL version) and opens it."""
    if postgres_app():
        return postgres_start(log)
    _log(log, "→ Looking for the latest Postgres.app")
    req = urllib.request.Request(POSTGRES_RELEASES, headers={"User-Agent": "odoodb", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        release = json.load(resp)
    # Postgres-2.9.6-18.dmg: one PostgreSQL version; the multi-version dmg is not needed.
    assets = [(int(m.group(1)), a["browser_download_url"]) for a in release.get("assets", [])
              if (m := re.fullmatch(r"Postgres-[\d.]+-(\d+)\.dmg", a.get("name", "")))]
    if not assets:
        raise T.DBError("No Postgres.app download found.", hint="Download it from https://postgresapp.com.")
    major, url = max(assets)
    dest_dir = APP_DIRS[0] if os.access(APP_DIRS[0], os.W_OK) else APP_DIRS[1]
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        dmg, mnt = Path(tmp) / "Postgres.dmg", Path(tmp) / "mnt"
        _log(log, f"→ Downloading Postgres.app {release.get('tag_name', '')} (PostgreSQL {major})")
        _download(url, dmg, log)
        code, _, err = ops.run_cmd(["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", str(mnt), str(dmg)],
                                   timeout=120)
        if code != 0:
            raise T.DBError(f"Cannot open the disk image: {err.strip()}")
        try:
            src = mnt / "Postgres.app"
            if not ops.run_cmd(["codesign", "--verify", "--deep", "--strict", str(src)], timeout=120)[0] == 0:
                raise T.DBError("The downloaded Postgres.app is not correctly signed: not installed.")
            _log(log, f"→ Installing in {dest_dir}")
            code, _, err = ops.run_cmd(["ditto", str(src), str(dest_dir / "Postgres.app")], timeout=300)
            if code != 0:
                raise T.DBError(f"Copy failed: {err.strip()}")
        finally:
            ops.run_cmd(["hdiutil", "detach", "-quiet", str(mnt)], timeout=60)
    ops._PSQL_PATH = None
    _log(log, "→ Opening Postgres.app")
    ops.run_cmd(["open", str(dest_dir / "Postgres.app")], timeout=20)
    if _wait_postgres(90):
        return f"Postgres.app installed, PostgreSQL {major} is running."
    raise T.DBError("Postgres.app is installed but its server is not initialised yet.",
                    hint="In the Postgres window, click Initialize, then retry.", code="postgres_down")


# --------------------------------------------------------------------------- #
# GitHub (gh, GitHub Desktop) and the Odoo clones
# --------------------------------------------------------------------------- #

def github_desktop() -> Optional[Path]:
    return _app("GitHub Desktop.app")


def gh_status() -> dict:
    gh = _which("gh")
    res = {"installed": bool(gh), "path": gh or "", "user": "", "git_credentials": False,
           "desktop": str(github_desktop() or "")}
    if not gh:
        return res
    code, out, _ = ops.run_cmd([gh, "api", "user", "--jq", ".login"], timeout=10)
    res["user"] = out.strip() if code == 0 else ""
    code, out, _ = ops.run_cmd(["git", "config", "--global", "--get-all", "credential.https://github.com.helper"],
                               timeout=5)
    res["git_credentials"] = "gh auth git-credential" in out
    return res


def gh_install(log: Log = None) -> str:
    if _which("gh"):
        return "GitHub CLI already installed."
    brew = _which("brew")
    if not brew:
        raise T.DBError("Homebrew is needed to install the GitHub CLI.",
                        hint="Install it from https://cli.github.com, or use GitHub Desktop.", code="missing")
    code, _ = _stream([brew, "install", "gh"], log, env={"HOMEBREW_NO_AUTO_UPDATE": "1"})
    if code != 0:
        raise T.DBError("brew install gh failed.")
    return "GitHub CLI installed."


def _login_command() -> str:
    gh = _which("gh")
    return (f"{gh} auth login --hostname github.com --git-protocol https --web "
            f"&& {gh} auth setup-git --hostname github.com") if gh else ""


def gh_login() -> str:
    """Sign-in is interactive (browser code): it runs in the terminal app."""
    if not _which("gh"):
        raise T.DBError("The GitHub CLI is not installed.", hint="`odoodb setup --fix gh-install`", code="missing")
    ok, msg = ops._open_in_terminal(_login_command())
    if not ok:
        raise T.DBError(msg)
    return "Finish signing in in the terminal, then check again."


def gh_setup_git() -> str:
    gh = _which("gh")
    if not gh:
        raise T.DBError("The GitHub CLI is not installed.", code="missing")
    code, out, err = ops.run_cmd([gh, "auth", "setup-git", "--hostname", "github.com"], timeout=20)
    if code != 0:
        raise T.DBError((err or out).strip() or "gh auth setup-git failed", hint="Sign in first: `gh auth login`.")
    return "git now uses your GitHub sign-in (gh) for github.com."


def repo_access(path: Path) -> tuple[bool, str]:
    """Can this clone talk to its remote? (no prompt: fails instead of asking a password)"""
    code, _, err = ops.run_cmd(["git", "-C", str(path), "ls-remote", "--exit-code", "origin", "HEAD"],
                               env={"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}, timeout=20)
    if code == 0:
        return True, ""
    err = err.strip().splitlines()[-1] if err.strip() else "no access"
    return False, err


def _repo_target(kind: str) -> Path:
    if kind == "community":
        return T.community_path()
    p = config.get_odoo_enterprise_path()
    return Path(p).expanduser() if p else T.odoo_root() / "enterprise"


def clone_repo(kind: str, branch: str = "", log: Log = None) -> str:
    """Shallow clone of odoo/odoo or odoo/enterprise. Other branches are fetched when switched to."""
    if kind not in REMOTES:
        raise T.DBError(f"Unknown repository {kind}", code="bad_args")
    dest = _repo_target(kind)
    if dest.exists() and any(dest.iterdir()):
        raise T.DBError(f"{dest} already exists and is not empty.", code="exists")
    if not branch:
        other = T.enterprise_path() if kind == "community" else T.community_path()
        branch = (ops.get_current_branch(str(other)) if other and other.is_dir() else None) or "19.0"
    dest.parent.mkdir(parents=True, exist_ok=True)
    _log(log, f"→ git clone {REMOTES[kind]} ({branch}) into {dest}")
    code, lines = _stream(["git", "clone", "--progress", "--depth", "1", "--branch", branch, REMOTES[kind], str(dest)],
                          log, env={"GIT_TERMINAL_PROMPT": "0"},
                          keep=lambda l: not re.search(r"\(\d+/\d+\)", l) or l.endswith("done."))
    if code != 0:
        hint = ("Your GitHub account needs access to odoo/enterprise. Sign in with `gh auth login`, "
                "then `gh auth setup-git`." if kind == "enterprise" else "")
        raise T.DBError(f"Clone failed: {lines[-1] if lines else '?'}", hint=hint, code="git")
    if kind == "enterprise" and not config.get_odoo_enterprise_path():
        config.save_odoo_enterprise_path(str(dest))
    return f"{kind} cloned on {branch} in {dest}."


def locate_repo(kind: str, path: str) -> str:
    """Use an existing copy of odoo/odoo or odoo/enterprise: checks the folder, then saves it in the settings."""
    if kind not in REMOTES:
        raise T.DBError(f"Unknown repository {kind}", code="bad_args")
    if not path:
        raise T.DBError("Folder required.", hint=f"`odoodb setup --fix locate:{kind} --path <folder>`", code="bad_args")
    p = Path(path).expanduser().resolve()
    if kind == "community":
        # The Odoo folder itself (core/, community/…) or its parent holding one of them.
        found = p if (p / "odoo-bin").is_file() else next(
            (c for c in (p / "core", p / "community", p / "odoo") if (c / "odoo-bin").is_file()), None)
        if not found:
            raise T.DBError(f"No odoo-bin in {p}.", hint="Choose the odoo/odoo clone: the folder that contains odoo-bin.",
                            code="bad_args")
        config.save_odoo_community_path(str(found))
    else:
        found = p if (p / "web_enterprise" / "__manifest__.py").is_file() else next(
            (c for c in (p / "enterprise",) if (c / "web_enterprise" / "__manifest__.py").is_file()), None)
        if not found:
            raise T.DBError(f"{p} is not odoo/enterprise (no web_enterprise module).",
                            hint="Choose the odoo/enterprise clone.", code="bad_args")
        config.save_odoo_enterprise_path(str(found))
    branch = ops.get_current_branch(str(found)) if (found / ".git").exists() else None
    note = f" on {branch}" if branch else " (not a git clone: branches cannot be switched)"
    return f"Odoo {kind}: {found}{note}."


def desktop_clone_url(kind: str) -> str:
    """Opens GitHub Desktop's clone window on the repository."""
    return f"x-github-client://openRepo/{REMOTES[kind][:-4]}"


def remote_branches(max_age: int = 3600) -> list[str]:
    """Version branches of odoo/odoo on GitHub (master, 19.0, saas-19.4…), cached one hour."""
    cache = CACHE_DIR / "remote_branches.json"
    try:
        if time.time() - cache.stat().st_mtime < max_age:
            return json.loads(cache.read_text())
    except (OSError, ValueError):
        pass
    comm = T.community_path()
    code, out, _ = ops.run_cmd(["git", "-C", str(comm), "ls-remote", "--heads", "origin"],
                               env={"GIT_TERMINAL_PROMPT": "0"}, timeout=30)
    if code != 0:
        return []
    names = [line.split("refs/heads/", 1)[1] for line in out.splitlines() if "refs/heads/" in line]
    names = sorted((n for n in names if VERSION_BRANCH_RE.match(n)), key=ops._branch_sort_key)
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(names))
    except OSError:
        pass
    return names


def pull(paths: list[str], log: Log = None) -> dict:
    results = []
    for p in paths:
        path = Path(p).expanduser()
        _log(log, f"→ git pull {path.name}")
        code, lines = _stream(["git", "-C", str(path), "pull", "--ff-only", "--progress"], log,
                              env={"GIT_TERMINAL_PROMPT": "0"}, keep=lambda l: not re.search(r"\(\d+/\d+\)", l))
        results.append({"path": str(path), "ok": code == 0, "message": lines[-1] if lines else ""})
    failed = [r for r in results if not r["ok"]]
    if failed:
        raise T.DBError("; ".join(f"{Path(r['path']).name}: {r['message']}" for r in failed),
                        hint="Commit or stash local changes, or check your GitHub sign-in.", code="git")
    return {"ok": True, "pulled": results,
            "message": ", ".join(f"{Path(r['path']).name}: {r['message']}" for r in results)}


# --------------------------------------------------------------------------- #
# Python environments (pyenv)
# --------------------------------------------------------------------------- #

def pyenv_root() -> Path:
    return Path(config.get_pyenv_root() or "~/.pyenv").expanduser()


def pyenv_bin() -> Optional[str]:
    for c in (pyenv_root() / "bin" / "pyenv", Path("/opt/homebrew/bin/pyenv"), Path("/usr/local/bin/pyenv")):
        if os.access(c, os.X_OK):
            return str(c)
    return _which("pyenv")


def python_for_major(major: int) -> str:
    """Python used for a new environment: what Odoo's own CI runs for that version."""
    return "3.12" if major >= 18 else "3.10"


def env_name_for(series: str) -> str:
    major = T.major_of(series)
    return f"odoo-{major}" if major else ""


def env_path(name: str) -> Path:
    return pyenv_root() / "versions" / name


def env_exists(name: str) -> bool:
    return bool(name) and (env_path(name) / "bin" / "python3").exists()


def env_python_version(name: str) -> str:
    try:
        text = (env_path(name).resolve() / "pyvenv.cfg").read_text()
    except OSError:
        return ""
    m = re.search(r"^version(?:_info)?\s*=\s*([\d.]+)", text, re.MULTILINE)
    return m.group(1) if m else ""


def base_pythons() -> dict[str, str]:
    """'3.12' -> '3.12.10': highest pyenv-installed patch of each Python version."""
    found: dict[str, str] = {}
    versions = pyenv_root() / "versions"
    if not versions.is_dir():
        return found
    for d in versions.iterdir():
        if re.fullmatch(r"\d+\.\d+\.\d+", d.name) and (d / "bin" / "python3").exists():
            minor = d.name.rsplit(".", 1)[0]
            if minor not in found or tuple(map(int, d.name.split("."))) > tuple(map(int, found[minor].split("."))):
                found[minor] = d.name
    return found


def install_pyenv(log: Log = None) -> str:
    if pyenv_bin():
        return "pyenv already installed."
    brew = _which("brew")
    if brew:
        code, _ = _stream([brew, "install", "pyenv"], log, env={"HOMEBREW_NO_AUTO_UPDATE": "1"})
        if code != 0:
            raise T.DBError("brew install pyenv failed.")
        return "pyenv installed (Homebrew)."
    # git install, in place: the existing versions/ folder (ignored by pyenv's .gitignore) is kept.
    root = pyenv_root()
    root.mkdir(parents=True, exist_ok=True)
    for cmd in (["git", "init", "-q"], ["git", "remote", "add", "origin", "https://github.com/pyenv/pyenv.git"],
                ["git", "fetch", "-q", "--depth", "1", "origin", "master"], ["git", "checkout", "-q", "-f", "-B", "master", "FETCH_HEAD"]):
        code, out, err = ops.run_cmd(cmd, cwd=str(root), timeout=300)
        if code != 0 and "remote origin already exists" not in err:
            raise T.DBError(f"pyenv install failed: {(err or out).strip()}")
    return f"pyenv installed in {root}."


def install_python(minor: str, log: Log = None) -> str:
    """pyenv install of the latest patch of `minor` (compiled: a few minutes)."""
    have = base_pythons().get(minor)
    if have:
        return have
    install_pyenv(log)
    pyenv = pyenv_bin()
    env = {"PYENV_ROOT": str(pyenv_root()), "HOMEBREW_NO_AUTO_UPDATE": "1"}
    brew = _which("brew")
    if brew:
        _log(log, "→ Build libraries for Python (Homebrew)")
        _stream([brew, "install", "openssl@3", "readline", "sqlite3", "xz", "zlib", "tcl-tk"], log, env=env,
                keep=lambda l: l.startswith(("==>", "Error")))
    code, out, _ = ops.run_cmd([pyenv, "latest", "--known", minor], env=env, timeout=30)
    version = out.strip() if code == 0 and out.strip() else minor
    _log(log, f"→ pyenv install {version} (a few minutes)")
    code, lines = _stream([pyenv, "install", "--skip-existing", version], log, env=env)
    if code != 0 or not base_pythons().get(minor):
        raise T.DBError(f"pyenv install {version} failed: {lines[-1] if lines else '?'}",
                        hint="Python is compiled from source: the Command Line Tools and, ideally, Homebrew are needed.")
    return base_pythons()[minor]


def branch_for_env(name: str) -> str:
    """The branch whose requirements.txt fills an environment: <major>.0, or what is checked out."""
    major = T.major_of(name)
    comm = str(T.community_path())
    current = ops.get_current_branch(comm) or ""
    if T.major_of(current) == major:
        return current
    for b in (f"{major}.0", f"origin/{major}.0"):
        if T._git(comm, "rev-parse", "--verify", "--quiet", b) is not None:
            return f"{major}.0"
    return current


def requirements_lines(branch: str = "", strict: bool = False) -> list[str]:
    """Requirement lines of requirements.txt on `branch` (default: the checked-out code). strict: the branch
    must be found (fetched from GitHub if needed) instead of falling back to the checked-out file."""
    comm = T.community_path()
    text = None
    if branch and branch != ops.get_current_branch(str(comm)):
        for attempt in range(2):
            for ref in (branch, f"origin/{branch}"):
                text = T._git(str(comm), "show", f"{ref}:requirements.txt")
                if text is not None:
                    break
            if text is not None or not strict or attempt:
                break
            # Only a branch GitHub has: fetching registers it on single-branch clones.
            code, _, _ = ops.run_cmd(["git", "-C", str(comm), "ls-remote", "--exit-code", "--heads", "origin", branch],
                                     env={"GIT_TERMINAL_PROMPT": "0"}, timeout=30)
            if code != 0:
                raise T.DBError(f"Branch {branch} not found on GitHub (odoo/odoo).", code="bad_args")
            error = ops.fetch_branch_if_missing(str(comm), branch)
            if error:
                raise T.DBError(f"Branch {branch}: {error}", code="git")
        if text is None and strict:
            raise T.DBError(f"No requirements.txt on {branch} in {comm}.", code="bad_args")
    if text is None:
        try:
            text = (comm / "requirements.txt").read_text(encoding="utf-8")
        except OSError:
            return []
    lines = []
    for line in text.splitlines():
        line = line.split(" #", 1)[0].split("\t#", 1)[0].strip()
        if line and not line.startswith(("#", "-")):
            lines.append(line)
    return lines


def _req_name(line: str) -> str:
    m = re.match(r"[A-Za-z0-9][A-Za-z0-9._\-]*", line)
    return m.group(0).lower().replace("_", "-") if m else ""


def manifest_dependencies(modules: list[dict]) -> dict:
    """external_dependencies of the installed modules: {"python": {pkg: [modules]}, "bin": {prog: [modules]}}."""
    deps: dict[str, dict[str, list[str]]] = {"python": {}, "bin": {}}
    for m in modules:
        if m.get("state") != "installed" or not m.get("location"):
            continue
        try:
            manifest = ast.literal_eval((Path(m["location"]) / m["name"] / "__manifest__.py").read_text(encoding="utf-8"))
        except (OSError, ValueError, SyntaxError, MemoryError):
            continue
        ext = manifest.get("external_dependencies") or {}
        for kind in ("python", "bin"):
            for dep in ext.get(kind) or []:
                if isinstance(dep, str) and dep.strip():
                    name = re.split(r"[<>=!~;\[\s]", dep.strip(), 1)[0]
                    deps[kind].setdefault(name, []).append(m["name"])
    return deps


# Runs inside the environment's own Python: markers and installed distributions are its own.
_PROBE = r'''
import importlib.metadata as md, importlib.util, json, sys
from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.utils import canonicalize_name
data = json.load(sys.stdin)
installed = {}
for d in md.distributions():
    name = d.metadata["Name"]
    if name:
        installed[canonicalize_name(name)] = d.version
alias = {"psycopg2": "psycopg2-binary", "psycopg2-binary": "psycopg2"}
reqs = []
for line in data["requirements"]:
    try:
        r = Requirement(line)
    except Exception:
        continue
    if r.marker and not r.marker.evaluate():
        continue
    n = canonicalize_name(r.name)
    reqs.append({"line": line, "name": r.name, "installed": installed.get(n) or installed.get(alias.get(n, ""))})
mods = []
for name in data["modules"]:
    ok = canonicalize_name(name) in installed
    if not ok:
        try:
            ok = importlib.util.find_spec(name) is not None
        except Exception:
            ok = False
    mods.append({"name": name, "ok": ok})
print(json.dumps({"python": sys.version.split()[0], "requirements": reqs, "modules": mods}))
'''


def _site_packages_mtime(name: str) -> float:
    lib = env_path(name).resolve() / "lib"
    try:
        return max((p.stat().st_mtime for p in lib.glob("python3*/site-packages")), default=0.0)
    except OSError:
        return 0.0


def check_env(name: str, requirements: list[str], modules: Optional[list[str]] = None) -> dict:
    """What `requirements` and the importable `modules` lack in the environment `name` (cached)."""
    modules = sorted(set(modules or []))
    if not env_exists(name):
        return {"exists": False, "name": name}
    payload = json.dumps({"requirements": requirements, "modules": modules})
    key = hashlib.sha1(f"{env_path(name).resolve()}|{_site_packages_mtime(name)}|{payload}".encode()).hexdigest()
    cache_file = CACHE_DIR / "python_env_checks.json"
    try:
        cache = json.loads(cache_file.read_text())
    except (OSError, ValueError):
        cache = {}
    if key in cache:
        return cache[key]
    try:
        res = subprocess.run([str(env_path(name) / "bin" / "python3"), "-I", "-c", _PROBE], input=payload,
                             capture_output=True, text=True, timeout=60, env={"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"})
        probe = json.loads(res.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {"exists": True, "name": name, "error": "Cannot inspect the environment (pip missing?)."}
    result = {
        "exists": True, "name": name, "python": probe["python"],
        "missing": [r["line"] for r in probe["requirements"] if not r["installed"]],
        "missing_modules": [m["name"] for m in probe["modules"] if not m["ok"]],
        "checked": len(probe["requirements"]),
    }
    cache = dict(list(cache.items())[-40:])
    cache[key] = result
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache))
    except OSError:
        pass
    return result


def _adapt(lines: list[str]) -> tuple[list[str], list[str]]:
    """(main, optional): psycopg2 → psycopg2-binary (no PostgreSQL headers needed), python-ldap apart."""
    main, optional = [], []
    for line in lines:
        name = _req_name(line)
        if name == "psycopg2":
            line = "psycopg2-binary" + line[len("psycopg2"):]
        (optional if name in OPTIONAL_REQUIREMENTS else main).append(line)
    return main, optional


def _pip(name: str, args: list[str], log: Log) -> tuple[int, list[str]]:
    py = str(env_path(name) / "bin" / "python3")
    env = {"PIP_DISABLE_PIP_VERSION_CHECK": "1", "PYTHONNOUSERSITE": "1"}
    return _stream([py, "-m", "pip", "install", "--progress-bar", "off", *args], log, env=env,
                   keep=lambda l: l.startswith(("Collecting", "Successfully", "ERROR", "error:")))


def install_packages(name: str, lines: list[str], log: Log = None) -> dict:
    """pip install `lines` into the environment. All at once, then one by one (unpinned as last resort)."""
    if not env_exists(name):
        raise T.DBError(f"Python environment {name} not found.", hint=f"`odoodb env create {name}`", code="env_missing")
    main, optional = _adapt(lines)
    failed: list[str] = []
    if main:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write("\n".join(main) + "\n")
        _log(log, f"→ pip install {len(main)} package(s) in {name}")
        code, _ = _pip(name, ["-r", fh.name], log)
        os.unlink(fh.name)
        if code != 0:
            _log(log, "→ Some packages failed: installing them one by one")
            for line in main:
                if _pip(name, [line], log)[0] == 0:
                    continue
                bare = _req_name(line)
                if bare and bare != line and _pip(name, [bare], log)[0] == 0:
                    _log(log, f"  {bare}: pinned version failed, latest installed instead")
                    continue
                failed.append(line)
    skipped = []
    for line in optional:
        if _pip(name, [line], log)[0] != 0:
            skipped.append(_req_name(line))
    msg = f"{len(main) - len(failed)} package(s) installed in {name}"
    if failed:
        msg += f"; failed: {', '.join(_req_name(l) for l in failed)}"
    if skipped:
        msg += f"; skipped {', '.join(skipped)} (needs system libraries, optional)"
    return {"ok": not failed, "name": name, "failed": failed, "skipped": skipped, "message": msg}


def create_env(name: str, branch: str = "", log: Log = None, python: str = "", requirements: bool = True) -> dict:
    """pyenv environment `name` on Python `python` (default: the one of the Odoo version, from the name or the
    branch), filled with requirements.txt of `branch` (default: <version>.0) unless requirements is False.
    Same layout as pyenv-virtualenv: versions/<python>/envs/<name>, linked as versions/<name>."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.\-]*", name or ""):
        raise T.DBError("Invalid environment name.", code="bad_args")
    if python and not re.fullmatch(r"3\.\d{1,2}", python):
        raise T.DBError("Python version like 3.12.", code="bad_args")
    if name in base_pythons().values() or re.fullmatch(r"\d+\.\d+(\.\d+)?", name):
        raise T.DBError(f"'{name}' is a Python version name: choose another name.", code="bad_args")
    major = T.major_of(name) if name.startswith("odoo-") else ""
    major = major or T.major_of(branch)
    if not python and not major:
        raise T.DBError(f"Cannot tell the Odoo version of '{name}'.",
                        hint="Pass --python 3.12, a branch (-b 19.0), or name it odoo-<version>.", code="bad_args")
    if requirements:
        branch = branch or branch_for_env(name)
        # Checked first: a wrong branch must not leave a half-made environment.
        lines = requirements_lines(branch, strict=bool(branch))
    if not env_exists(name):
        base = install_python(python or python_for_major(int(major)), log)
        target = pyenv_root() / "versions" / base / "envs" / name
        link = env_path(name)
        if link.is_symlink() or link.exists():
            raise T.DBError(f"{link} exists but has no Python.", hint="Remove it, then retry.", code="exists")
        _log(log, f"→ Creating {name} (Python {base})")
        code, out, err = ops.run_cmd([str(pyenv_root() / "versions" / base / "bin" / "python3"), "-m", "venv", str(target)],
                                     timeout=300)
        if code != 0:
            raise T.DBError(f"venv creation failed: {(err or out).strip()}")
        link.symlink_to(target)
        _pip(name, ["--upgrade", "pip", "setuptools", "wheel"], log)
    else:
        _log(log, f"✓ {name} exists (Python {env_python_version(name)})")
    if not requirements:
        return {"ok": True, "name": name, "message": f"{name} ready (Python {env_python_version(name)}, "
                                                     "no requirements installed)."}
    if not lines:
        return {"ok": True, "name": name, "message": f"{name} ready (no requirements.txt found for {branch})."}
    _log(log, f"→ Requirements of {branch}")
    res = install_packages(name, check_env(name, lines).get("missing", lines), log)
    res["message"] = f"{name} ready for {branch}: " + res["message"]
    return res


def list_envs() -> dict:
    """Odoo environments: the existing odoo-* ones and those the code / launch scripts need."""
    versions = pyenv_root() / "versions"
    # odoo-* ones, and any other virtualenv made here or by pyenv-virtualenv (linked to versions/<python>/envs/).
    names = {d.name for d in versions.glob("odoo-*")} if versions.is_dir() else set()
    if versions.is_dir():
        names |= {d.name for d in versions.iterdir() if d.is_symlink() and "/envs/" in os.readlink(d)}
    for series in [T.code_series()] + [sc.get("branch", "") for sc in T.script_index().values()]:
        if env_name_for(series):
            names.add(env_name_for(series))
    envs = []
    for n in sorted(names, key=lambda n: (not n.startswith("odoo-"), -int(T.major_of(n) or 0), n)):
        odoo = n.startswith("odoo-")
        branch = branch_for_env(n) if odoo else ""
        # Only odoo-<version> ones are checked against a requirements.txt: the others are free-form.
        info = check_env(n, requirements_lines(branch)) if odoo and env_exists(n) else {}
        envs.append({**info, "name": n, "branch": branch, "exists": env_exists(n),
                     "python": info.get("python") or env_python_version(n)})
    return {"ok": True, "pyenv": pyenv_bin() or "", "root": str(pyenv_root()), "forced": config.get_pyenv_global_env(),
            "envs": envs, "pythons": base_pythons()}


# --------------------------------------------------------------------------- #
# wkhtmltopdf
# --------------------------------------------------------------------------- #

def wkhtmltopdf_path() -> Optional[str]:
    return _which("wkhtmltopdf") or next((p for p in ("/usr/local/bin/wkhtmltopdf", "/opt/homebrew/bin/wkhtmltopdf")
                                          if os.access(p, os.X_OK)), None)


def install_wkhtmltopdf(log: Log = None) -> str:
    """wkhtmltopdf 0.12.6 with patched Qt (the build Odoo supports). Intel-only: Rosetta on Apple silicon."""
    if wkhtmltopdf_path():
        return "wkhtmltopdf already installed."
    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(tmp) / "wkhtmltox.pkg"
        _log(log, "→ Downloading wkhtmltopdf 0.12.6 (patched Qt)")
        _download(WKHTMLTOPDF_PKG, pkg, log)
        digest = hashlib.sha256(pkg.read_bytes()).hexdigest()
        if digest != WKHTMLTOPDF_SHA256:
            raise T.DBError("The wkhtmltopdf download does not match the expected file: not installed.")
        script = f"installer -pkg '{pkg}' -target /"
        if platform.machine() == "arm64" and ops.run_cmd(["arch", "-x86_64", "/usr/bin/true"], timeout=10)[0] != 0:
            script = "softwareupdate --install-rosetta --agree-to-license && " + script
        _log(log, "→ Installing: macOS asks for your password")
        ok, msg = _admin_shell(script)
        if not ok:
            raise T.DBError(f"wkhtmltopdf not installed: {msg}")
    return "wkhtmltopdf installed (/usr/local/bin/wkhtmltopdf)."


# --------------------------------------------------------------------------- #
# Setup checklist
# --------------------------------------------------------------------------- #

def _item(id_: str, group: str, title: str, status: str, detail: str = "", action: str = "",
          action_label: str = "", blocking: bool = False, link: str = "",
          alternatives: Optional[list[dict]] = None) -> dict:
    """alternatives: other ways to fix it, each {"id", "label"} with an "action" (needs --path for locate:*)
    or a "url" to open (GitHub Desktop)."""
    return {"id": id_, "group": group, "title": title, "status": status, "detail": detail,
            "action": action or None, "action_label": action_label, "blocking": blocking, "link": link or None,
            "alternatives": alternatives or []}


def setup_status(network: bool = True) -> dict:
    """Every prerequisite with its state ("ok", "warning", "error", "optional") and its fix."""
    items: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_pg = pool.submit(postgres_status)
        f_gh = pool.submit(gh_status)
        comm, ent = T.community_path(), T.enterprise_path()
        f_access = {k: pool.submit(repo_access, p) for k, p in (("community", comm), ("enterprise", ent))
                    if network and p and (p / ".git").exists()}
        f_envs = pool.submit(list_envs)

        pg = f_pg.result()
        if pg["reachable"]:
            items.append(_item("postgres", "PostgreSQL", "PostgreSQL server", "ok",
                               f"PostgreSQL {pg.get('version', '')} · {pg['psql']}"))
        elif pg["state"] == "shutting_down":
            items.append(_item("postgres", "PostgreSQL", "PostgreSQL server", "error",
                               "Stuck shutting down: it refuses new connections until the open ones close (often "
                               "running Odoo servers). Restarting it closes them; Odoo servers reconnect.",
                               "postgres-restart", "Restart PostgreSQL", blocking=True))
        elif pg["state"] == "starting":
            items.append(_item("postgres", "PostgreSQL", "PostgreSQL server", "warning",
                               "Starting up: check again in a few seconds.", blocking=True))
        elif pg["state"] == "denied":
            items.append(_item("postgres", "PostgreSQL", "PostgreSQL server", "error",
                               f"Postgres.app refused the connection ({pg.get('error', '')[:120]}). In Postgres.app › "
                               "Server Settings…, allow Odoo Database Manager and python (App Permissions), "
                               "then check again.", blocking=True))
        elif pg["app"] or _brew_postgres_formula():
            items.append(_item("postgres", "PostgreSQL", "PostgreSQL server", "error",
                               f"Installed but not running ({pg.get('error', '')[:120]}).", "postgres-start", "Start",
                               blocking=True))
        else:
            items.append(_item("postgres", "PostgreSQL", "PostgreSQL server", "error",
                               "Not installed. Postgres.app is downloaded from GitHub and opened: "
                               "click Initialize in its window.", "postgres-install", "Install Postgres.app",
                               blocking=True))

        gh = f_gh.result()
        for kind, path in (("community", comm), ("enterprise", ent or _repo_target("enterprise"))):
            title = "Odoo community" if kind == "community" else "Odoo enterprise"
            if not (path / ".git").exists():
                exists = path.is_dir() and any(path.iterdir())
                # Three ways: clone now (shallow), point to an existing copy, or clone later in GitHub Desktop.
                alternatives = [{"id": "locate", "label": "Locate…", "action": f"locate:{kind}"}]
                if gh["desktop"]:
                    alternatives.append({"id": "desktop", "label": "Clone in GitHub Desktop…",
                                         "url": desktop_clone_url(kind), "path": str(path)})
                else:
                    alternatives.append({"id": "desktop", "label": "Get GitHub Desktop",
                                         "url": "https://desktop.github.com"})
                detail = (f"{path} is not a git clone." if exists else f"Not found in {path}.")
                if kind == "enterprise":
                    detail += " Needs access to odoo/enterprise on GitHub."
                detail += " Clone it here, locate a copy you already have, or clone it later with GitHub Desktop."
                items.append(_item(kind, "Odoo code", title, "error" if kind == "community" else "warning", detail,
                                   "" if exists else f"clone:{kind}", "" if exists else "Clone",
                                   blocking=kind == "community", alternatives=alternatives))
                continue
            branch = ops.get_current_branch(str(path)) or "?"
            detail = f"{branch} · {path}"
            status, action, label = "ok", "", ""
            if kind in f_access:
                ok, err = f_access[kind].result()
                if not ok:
                    status = "warning"
                    detail += f" · cannot reach GitHub: {err[:140]}"
                    if gh["installed"] and gh["user"] and not gh["git_credentials"]:
                        action, label = "gh-setup-git", "Use GitHub sign-in"
                    elif gh["installed"] and not gh["user"]:
                        action, label = "gh-login", "Sign In to GitHub…"
            items.append(_item(kind, "Odoo code", title, status, detail, action, label))

        if not gh["installed"]:
            items.append(_item("gh", "GitHub", "GitHub CLI (gh)", "warning",
                               "Signs git in to GitHub (enterprise is private) and fetches the branches you switch to.",
                               "gh-install" if _which("brew") else "", "Install" if _which("brew") else "",
                               link="https://cli.github.com"))
        elif not gh["user"]:
            items.append(_item("gh", "GitHub", "GitHub CLI (gh)", "warning", "Installed, not signed in.",
                               "gh-login", "Sign In…"))
        else:
            detail = f"Signed in as {gh['user']}"
            if gh["git_credentials"]:
                items.append(_item("gh", "GitHub", "GitHub CLI (gh)", "ok", detail + " · used by git"))
            else:
                items.append(_item("gh", "GitHub", "GitHub CLI (gh)", "ok",
                                   detail + " · git uses its own credentials", "gh-setup-git", "Use for git"))
        items.append(_item("desktop", "GitHub", "GitHub Desktop", "ok" if gh["desktop"] else "optional",
                           "Repositories open in it from a database page." if gh["desktop"] else
                           "Optional: to review changes and switch branches visually.",
                           link="" if gh["desktop"] else "https://desktop.github.com"))

        envs = f_envs.result()
        if not envs["pyenv"]:
            items.append(_item("pyenv", "Python", "pyenv", "error",
                               "Runs each Odoo version on its own Python environment.", "pyenv-install", "Install",
                               blocking=False))
        else:
            pythons = ", ".join(sorted(envs["pythons"].values())) or "no Python yet"
            items.append(_item("pyenv", "Python", "pyenv", "ok", f"{envs['root']} · {pythons}"))
        if envs["forced"]:
            items.append(_item("forced", "Python", f"Every database runs on {envs['forced']}", "warning",
                               "Forced in Settings › General (Force pyenv env): clear it to use odoo-<version>."))
        for e in envs["envs"]:
            title = f"Environment {e['name']}"
            if not e.get("exists"):
                items.append(_item(f"env:{e['name']}", "Python", title, "error",
                                   f"Missing. Created on Python {python_for_major(int(T.major_of(e['name'])))} "
                                   f"with the requirements of {e['branch'] or 'the code'} (a few minutes).",
                                   f"env-create:{e['name']}", "Create"))
            elif e.get("missing"):
                names = [_req_name(l) for l in e["missing"]]
                items.append(_item(f"env:{e['name']}", "Python", title, "warning",
                                   f"Python {e.get('python', '')} · {len(names)} package(s) of {e['branch']} missing: "
                                   + ", ".join(names[:10]) + ("…" if len(names) > 10 else ""),
                                   f"env-deps:{e['name']}", "Install"))
            else:
                items.append(_item(f"env:{e['name']}", "Python", title, "ok",
                                   f"Python {e.get('python', '')} · requirements of {e['branch']} installed"
                                   if e.get("checked") else f"Python {e.get('python', '')}"))

    wk = wkhtmltopdf_path()
    items.append(_item("wkhtmltopdf", "Tools", "wkhtmltopdf", "ok" if wk else "warning",
                       wk or "Needed to print PDF reports (invoices, quotations…). Version 0.12.6 with patched Qt; "
                             "macOS asks for your password.", "" if wk else "wkhtmltopdf", "" if wk else "Install"))
    # Interactive: the app runs it in its built-in terminal (`setup --fix gh-login` opens the terminal app).
    login = _login_command()
    for i in items:
        if i["action"] == "gh-login" and login:
            i["terminal"] = login
    counts = {s: sum(1 for i in items if i["status"] == s) for s in ("ok", "warning", "error", "optional")}
    return {"ok": True, "items": items, "counts": counts, "blocking": any(i["blocking"] and i["status"] == "error" for i in items)}


ACTIONS = {
    "postgres-install": "Install Postgres.app (latest PostgreSQL) and open it",
    "postgres-start": "Start PostgreSQL (Postgres.app or Homebrew)",
    "postgres-restart": "Fast restart of PostgreSQL (ends a shutdown stuck on open connections)",
    "clone:community": "Clone odoo/odoo into the community path",
    "clone:enterprise": "Clone odoo/enterprise into the enterprise path",
    "locate:community": "Use an existing odoo/odoo folder (--path <folder>)",
    "locate:enterprise": "Use an existing odoo/enterprise folder (--path <folder>)",
    "gh-install": "Install the GitHub CLI (Homebrew)",
    "gh-login": "Sign in to GitHub (opens the terminal)",
    "gh-setup-git": "Make git use the GitHub CLI sign-in",
    "pyenv-install": "Install pyenv",
    "env-create:<name>": "Create a Python environment (e.g. env-create:odoo-19) with its requirements",
    "env-deps:<name>": "Install the missing requirements in an environment",
    "wkhtmltopdf": "Install wkhtmltopdf 0.12.6 (asks for the password)",
}


def run_action(action: str, log: Log = None, path: str = "") -> dict:
    kind, _, arg = action.partition(":")
    if kind == "postgres-install":
        msg = postgres_install(log)
    elif kind == "postgres-start":
        msg = postgres_start(log)
    elif kind == "postgres-restart":
        msg = postgres_restart(log)
    elif kind == "clone":
        msg = clone_repo(arg, log=log)
    elif kind == "locate":
        msg = locate_repo(arg, path)
    elif kind == "gh-install":
        msg = gh_install(log)
    elif kind == "gh-login":
        msg = gh_login()
    elif kind == "gh-setup-git":
        msg = gh_setup_git()
    elif kind == "pyenv-install":
        msg = install_pyenv(log)
    elif kind == "env-create":
        return create_env(arg, log=log)
    elif kind == "env-deps":
        lines = check_env(arg, requirements_lines(branch_for_env(arg))).get("missing", [])
        if not lines:
            return {"ok": True, "name": arg, "message": f"Nothing missing in {arg}."}
        return install_packages(arg, lines, log)
    elif kind == "wkhtmltopdf":
        msg = install_wkhtmltopdf(log)
    else:
        raise T.DBError(f"Unknown action: {action}", hint="Known: " + ", ".join(ACTIONS), code="bad_args")
    return {"ok": True, "message": msg}
