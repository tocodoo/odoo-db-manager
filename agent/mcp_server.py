#!/usr/bin/env python3
"""MCP server (stdio) exposing the local Odoo databases to any MCP client (Claude Desktop, Claude Code…).

Every tool runs `odoodb --json …`, the same command the macOS app and the skill use.
No dependency: speaks JSON-RPC 2.0 over stdin/stdout.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = os.path.join(REPO, "venv", "bin", "python")
if not os.path.exists(PYTHON):
    PYTHON = sys.executable
ODOODB = os.path.join(REPO, "odoodb.py")

S = {"type": "string"}
B = {"type": "boolean"}
I = {"type": "integer"}
L = {"type": "array", "items": {"type": "string"}}


def tool(name, description, props=None, required=None, build=None):
    return {
        "name": name,
        "description": description,
        "inputSchema": {"type": "object", "properties": props or {}, "required": required or []},
        "build": build,
    }


def _flag(cond, *flags):
    return list(flags) if cond else []


TOOLS = [
    tool("list_databases",
         "List the local Odoo databases: real version (from the database), running state, port, size, "
         "custom addons, last login. Also gives the branch checked out in core/enterprise.",
         {"running_only": B},
         build=lambda a: ["list"] + _flag(a.get("running_only"), "--running")),
    tool("database_info",
         "Everything about one database: real version vs code, repositories of its addons path (branch, commit, "
         "local changes), installed modules (custom ones listed), size, filestore, users, languages, launch script, "
         "running process, URL, log file and detected issues.",
         {"name": S, "include_modules": B}, ["name"],
         build=lambda a: ["info", a["name"]] + _flag(a.get("include_modules"), "--modules")),
    tool("create_database",
         "Create a local Odoo database and start it (waits until Odoo is up; login admin/admin). "
         "Switches core/enterprise to `branch` if needed. Modules found in folders under the Odoo directory are "
         "added to the addons path automatically. Can restore a backup instead (.zip from the Odoo database "
         "manager, .sql, pg_dump file).",
         {"name": S, "branch": {**S, "description": "e.g. 19.0, saas-19.4 (default: currently checked out)"},
          "modules": {**L, "description": "modules to install (default: base)"},
          "addons": {**L, "description": "extra addons folders"},
          "dump": {**S, "description": "backup file to restore"},
          "filestore": S, "neutralize": {**B, "description": "for dumps, default true"},
          "start": {**B, "description": "default true"}},
         ["name"],
         build=lambda a: (["create", a["name"]]
                          + (["-b", a["branch"]] if a.get("branch") else [])
                          + (["-m", ",".join(a["modules"])] if a.get("modules") else [])
                          + (["-a", *a["addons"]] if a.get("addons") else [])
                          + (["--dump", a["dump"]] if a.get("dump") else [])
                          + (["--filestore", a["filestore"]] if a.get("filestore") else [])
                          + _flag(a.get("neutralize") is False, "--no-sanitize")
                          + _flag(a.get("start") is False, "--no-start"))),
    tool("start_database",
         "Start a database in the background (logs captured) and wait until it is up. If the database's version "
         "differs from the checked-out code, it fails with code 'branch_mismatch': retry with switch_branch=true.",
         {"name": S, "switch_branch": B, "force": B}, ["name"],
         build=lambda a: ["start", a["name"]] + _flag(a.get("switch_branch"), "--switch-branch")
         + _flag(a.get("force"), "--force")),
    tool("stop_database", "Stop a running database.", {"name": S}, ["name"],
         build=lambda a: ["stop", a["name"]]),
    tool("restart_database", "Restart a database.", {"name": S}, ["name"],
         build=lambda a: ["restart", a["name"]]),
    tool("database_logs", "Server log of a database started by odoodb (or by the app).",
         {"name": S, "lines": I, "errors_only": B}, ["name"],
         build=lambda a: ["logs", a["name"], "-n", str(a.get("lines") or 200)] + _flag(a.get("errors_only"), "--errors")),
    tool("database_history",
         "Past WARNING / ERROR / CRITICAL records of a database's server log, with their tracebacks, folded "
         "(count, first and last time). Survives restarts: use it to see what went wrong before.",
         {"name": S, "errors_only": B, "limit": I, "full": B}, ["name"],
         build=lambda a: ["history", a["name"], "-n", str(a.get("limit") or 50)]
         + _flag(a.get("errors_only"), "--errors")),
    tool("diagnose_database",
         "Find problems: version/branch mismatch, modules stuck in 'to upgrade', installed modules whose code is "
         "not in the addons path, missing launch script or addons folder, port conflict, missing filestore, "
         "missing Python environment or packages (requirements.txt, modules' external_dependencies), no wkhtmltopdf. "
         "Each issue names the fix to pass to repair_database.",
         {"name": S}, ["name"], build=lambda a: ["doctor", a["name"]]),
    tool("repair_database",
         "Apply fixes. Without `fixes`, applies every fix the diagnosis suggests (except 'branch'). "
         "Fixes: locks, script, addons, branch, assets, port, admin (reset to admin/admin), init, "
         "env (create the pyenv environment), pydeps (pip install what is missing in it), wkhtmltopdf.",
         {"name": S, "fixes": L, "force": B}, ["name"],
         build=lambda a: ["repair", a["name"]] + (["--fix", ",".join(a["fixes"])] if a.get("fixes") else [])
         + _flag(a.get("force"), "--force")),
    tool("update_modules", "Upgrade modules (-u) with the server stopped. Empty list = all.",
         {"name": S, "modules": L, "switch_branch": B}, ["name"],
         build=lambda a: ["update", a["name"], "-m", ",".join(a.get("modules") or ["all"])]
         + _flag(a.get("switch_branch"), "--switch-branch")),
    tool("install_modules", "Install modules (-i) with the server stopped.",
         {"name": S, "modules": L, "switch_branch": B}, ["name", "modules"],
         build=lambda a: ["install", a["name"], "-m", ",".join(a["modules"])]
         + _flag(a.get("switch_branch"), "--switch-branch")),
    tool("duplicate_database", "Copy a database with its filestore and launch script.",
         {"source": S, "new_name": S}, ["source", "new_name"],
         build=lambda a: ["duplicate", a["source"], a["new_name"]]),
    tool("rename_database", "Rename a database, its filestore and its launch script.",
         {"name": S, "new_name": S}, ["name", "new_name"],
         build=lambda a: ["rename", a["name"], a["new_name"]]),
    tool("delete_database",
         "Drop a database, its filestore and its launch script. Irreversible: only when the user asked for it, "
         "and pass confirm=true.",
         {"name": S, "confirm": B}, ["name", "confirm"],
         build=lambda a: ["delete", a["name"]] + _flag(a.get("confirm") is True, "--yes")),
    tool("reset_database",
         "Drop a database and its filestore, then recreate it right away with the same name, launch script, "
         "branch, addons path and port, and start it. Installs the launch script's modules (what it was created "
         "with), or `modules`, or with same_modules=true the modules installed now. All data is lost: only "
         "when the user asked for it, and pass confirm=true.",
         {"name": S, "confirm": B, "modules": L, "same_modules": B, "start": {**B, "description": "default true"},
          "switch_branch": B}, ["name", "confirm"],
         build=lambda a: ["reset", a["name"]] + _flag(a.get("confirm") is True, "--yes")
         + (["-m", ",".join(a["modules"])] if a.get("modules") else [])
         + _flag(a.get("same_modules"), "--same-modules") + _flag(a.get("start") is False, "--no-start")
         + _flag(a.get("switch_branch"), "--switch-branch")),
    tool("neutralize_database", "Neutralize a database (crons, mail servers, payment providers…).",
         {"name": S}, ["name"], build=lambda a: ["neutralize", a["name"]]),
    tool("translate_module",
         "Export a module's terms from a database and translate them with AI into a .po file "
         "(default: the module's own i18n/ folder). The server must be stopped.",
         {"name": S, "module": S, "lang": {**S, "description": "e.g. fr_FR, nl_BE"}, "out": S, "ai": B},
         ["name", "module", "lang"],
         build=lambda a: ["translate", a["name"], "-m", a["module"], "-l", a["lang"]]
         + (["-o", a["out"]] if a.get("out") else []) + _flag(a.get("ai") is False, "--no-ai")),
    tool("launch_script",
         "Show or edit a database's launch script. Without changes: its branch, addons path, modules installed at "
         "creation, modules updated at each start (-u or --reinit), port and Python environment. With changes: "
         "rewrites it (takes effect at the next start). `addons` is the whole addons path, in order.",
         {"name": S, "branch": S, "addons": L, "install": L, "update": L,
          "update_mode": {**S, "description": "update (-u) or reinit (--reinit)"}, "port": I, "pyenv": S}, ["name"],
         build=lambda a: ["script", a["name"]]
         + (["-b", a["branch"]] if a.get("branch") else [])
         + (["-a", *a["addons"]] if a.get("addons") else [])
         + (["--install", ",".join(a["install"])] if a.get("install") is not None else [])
         + (["--update", ",".join(a["update"])] if a.get("update") is not None else [])
         + (["--update-mode", a["update_mode"]] if a.get("update_mode") else [])
         + (["--port", str(a["port"])] if a.get("port") else [])
         + (["--pyenv", a["pyenv"]] if a.get("pyenv") else [])),
    tool("set_port", "Set the HTTP port of a database (to run several at once).",
         {"name": S, "port": I}, ["name", "port"], build=lambda a: ["port", a["name"], str(a["port"])]),
    tool("list_branches", "Branches available in core and the one checked out. remote=true adds the version "
         "branches on GitHub not fetched yet (fetched automatically when a database uses them).",
         {"remote": B}, build=lambda a: ["branches"] + _flag(a.get("remote"), "--remote")),
    tool("setup_status",
         "What this Mac needs to run Odoo databases: PostgreSQL (Postgres.app), community/enterprise clones and "
         "their GitHub access, GitHub CLI sign-in, pyenv and one environment per Odoo version (odoo-19…) with its "
         "requirements, wkhtmltopdf. Each item has a status and the `action` that fixes it.",
         build=lambda a: ["setup"]),
    tool("setup_fix",
         "Run one setup action: postgres-install, postgres-start, postgres-restart (fast restart: ends a shutdown "
         "stuck on open connections), clone:community, clone:enterprise, "
         "locate:community / locate:enterprise (use an existing clone: pass its folder as `path`), gh-install, "
         "gh-setup-git, pyenv-install, env-create:<name>, env-deps:<name>, wkhtmltopdf (asks the user's password "
         "in a macOS dialog). gh-login opens the terminal: the user must finish it.",
         {"action": S, "path": S}, ["action"],
         build=lambda a: ["setup", "--fix", a["action"]] + (["--path", a["path"]] if a.get("path") else [])),
    tool("python_environments",
         "pyenv environments of the Odoo versions: Python version, branch whose requirements fill it, missing "
         "packages. To create one: setup_fix env-create:odoo-19.",
         build=lambda a: ["env", "list"]),
    tool("pull_repositories", "git pull --ff-only core + enterprise, or the given repository folders.",
         {"paths": L}, build=lambda a: ["pull", *(a.get("paths") or [])]),
    tool("checkout_branch", "Switch core + enterprise to a branch (refuses while other databases run, unless force).",
         {"branch": S, "force": B}, ["branch"],
         build=lambda a: ["checkout", a["branch"]] + _flag(a.get("force"), "--force")),
]
BY_NAME = {t["name"]: t for t in TOOLS}


def call(name: str, args: dict) -> tuple[str, bool]:
    t = BY_NAME.get(name)
    if not t:
        return f"Unknown tool {name}", True
    cmd = [PYTHON, ODOODB, "--json", *t["build"](args or {})]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, cwd=REPO,
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return "Timed out after 1 hour.", True
    text = p.stdout.strip() or "{}"
    progress = [l for l in p.stderr.splitlines() if l.strip()]
    if progress:
        text += "\n\nProgress (last lines):\n" + "\n".join(progress[-40:])
    return text, p.returncode != 0


def send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        method, rid = req.get("method"), req.get("id")
        if rid is None:
            continue  # notification
        if method == "initialize":
            result = {
                "protocolVersion": req.get("params", {}).get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "odoo-databases", "version": "3.0"},
                "instructions": "Local Odoo databases of this Mac. Use these tools to list, create, start, stop, "
                                "diagnose and repair databases instead of calling odoo-bin, psql or git directly.",
            }
        elif method == "tools/list":
            result = {"tools": [{k: v for k, v in t.items() if k != "build"} for t in TOOLS]}
        elif method == "tools/call":
            params = req.get("params", {})
            text, is_error = call(params.get("name", ""), params.get("arguments") or {})
            result = {"content": [{"type": "text", "text": text}], "isError": is_error}
        elif method == "ping":
            result = {}
        else:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"Unknown method {method}"}})
            continue
        send({"jsonrpc": "2.0", "id": rid, "result": result})


if __name__ == "__main__":
    main()
