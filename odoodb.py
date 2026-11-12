#!/usr/bin/env python3
"""odoodb — create, start, monitor and repair local Odoo databases.

Every command accepts --json (machine-readable result on stdout; progress on stderr).

  odoodb list                         all databases (real version, running, port, size)
  odoodb info <db>                    everything about one database (repos, modules, issues…)
  odoodb create <db> -b 19.0 -m sale  create a database (and start it)
  odoodb start|stop|restart <db>      run it in the background (logs captured)
  odoodb logs <db> [-f] [--errors]    server log
  odoodb history <db> [--errors]      past warnings, errors and tracebacks (folded, with counts)
  odoodb terminal <db> [--kind shell] run it in this terminal (foreground), or odoo shell / zsh
  odoodb doctor <db>                  diagnose problems
  odoodb repair <db> [--fix locks]    fix them
  odoodb update <db> -m mod1,mod2     upgrade modules (-u)
  odoodb script <db> [--port 8070]    show or edit the launch script (branch, addons, modules, python)
  odoodb reset <db> --yes             drop it and recreate it fresh (same name, script, addons, port)
  odoodb translate <db> -m mod -l fr_FR
  odoodb setup [--fix <action>]       what this Mac needs (PostgreSQL, clones, GitHub, Python envs, wkhtmltopdf)
  odoodb env list|create|install      pyenv environments of the Odoo versions
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dbtools as T  # noqa: E402

JSON = False


def log(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def _csv(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").replace(" ", ",").split(",") if v.strip()]


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _ago(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        t = time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return iso
    d = time.time() - t
    for secs, unit in ((86400 * 365, "y"), (86400 * 30, "mo"), (86400, "d"), (3600, "h"), (60, "min")):
        if d >= secs:
            return f"{int(d // secs)}{unit} ago"
    return "just now"


def _print_table(rows: list[list[str]], headers: list[str]) -> None:
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)] if rows else [len(h) for h in headers]
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    for r in rows:
        print("  ".join(str(x).ljust(w) for x, w in zip(r, widths)))


# --------------------------------------------------------------------------- #

def cmd_list(a):
    res = T.list_databases()
    if a.running:
        res["databases"] = [d for d in res["databases"] if d["running"]]
    if JSON:
        return res
    print(f"Repos on {res['repos_branch']} (code {res['code_series']})\n")
    rows = []
    for d in res["databases"]:
        ver = d.get("series") or ("not odoo" if not d["is_odoo"] else "?")
        if d.get("matches_code") is False:
            ver += " ≠"
        rows.append([("● " if d["running"] else "  ") + d["name"], ver, d["port"], _size(d["size"]),
                     d.get("modules_installed") or "", _ago(d.get("last_login")), ", ".join(d["custom_addons"])])
    _print_table(rows, ["DATABASE", "VERSION", "PORT", "SIZE", "MODULES", "LAST LOGIN", "CUSTOM ADDONS"])
    print("\n● running   ≠ database version differs from the checked-out code")


def cmd_info(a):
    res = T.database_info(a.db, with_modules=a.modules)
    if JSON:
        return res
    run = res.get("running")
    print(f"{res['name']}  {'● running ' + res['url'] if run else '○ stopped'}")
    print(f"  version      {res.get('series') or '?'} (base {res.get('base_version') or '?'}) — code on {res['code_series']}")
    print(f"  size         {_size(res['size'])} + filestore {_size(res['filestore']['size'])}")
    print(f"  modules      {res['module_counts']['installed']} installed "
          f"({res['module_counts']['custom']} custom, {res['module_counts']['enterprise']} enterprise)")
    if res.get("custom_modules"):
        print(f"  custom       {', '.join(res['custom_modules'])}")
    print(f"  company      {res.get('company') or '—'} · {res.get('users') or 0} users · last login {_ago(res.get('last_login'))}")
    print(f"  languages    {', '.join(l['code'] for l in res.get('languages', [])) or '—'}")
    print(f"  script       {(res.get('script') or {}).get('path') or '—'}")
    print(f"  log          {res['log']['path'] if res['log']['exists'] else '—'}")
    print("  repos")
    for r in res["repos"]:
        state = f"{r.get('branch')} @ {r.get('commit')}" if r.get("git") else ("not a git repo" if r["exists"] else "MISSING")
        extra = f", {r['changes']} changes" if r.get("changes") else ""
        extra += f", {r['behind']} behind" if r.get("behind") else ""
        print(f"    {r['kind']:<10} {r['name']:<28} {state}{extra}")
    if res["issues"]:
        print("  issues")
        for i in res["issues"]:
            fix = f"  → odoodb repair {a.db} --fix {i['fix']}" if i.get("fix") else ""
            print(f"    [{i['severity']}] {i['title']}{fix}")
    if a.modules:
        print("  modules")
        for m in res["modules"]:
            print(f"    {m['name']:<40} {m['state']:<12} {m['source']}")


def cmd_modules(a):
    mods = T.list_modules(a.db)
    if a.custom:
        mods = [m for m in mods if m["source"] in ("custom", "missing")]
    res = {"ok": True, "name": a.db, "modules": mods}
    if JSON:
        return res
    _print_table([[m["name"], m["state"], m["version"], m["source"]] for m in mods],
                 ["MODULE", "STATE", "VERSION", "SOURCE"])


def cmd_create(a):
    res = T.create(a.db, branch=a.branch or "", modules=_csv(a.modules), addons=a.addons or [],
                   dump=a.dump or "", filestore=a.filestore or "", sanitize=not a.no_sanitize,
                   start_server=not a.no_start, force=a.force, subdir=a.folder or "", log=log,
                   timeout=a.timeout)
    if JSON:
        return res
    print(f"\n{'✓' if res.get('ok') else '✗'} {res['name']} ({res['branch']})")
    if res.get("url"):
        print(f"  {res['url']}  (admin / admin)")
    print(f"  script: {res['script']}")


def cmd_start(a):
    res = T.start(a.db, terminal=a.terminal, wait=not a.no_wait, switch_branch=a.switch_branch,
                  force=a.force, timeout=a.timeout, log=log)
    if JSON:
        return res
    _summary(res)


def cmd_stop(a):
    if a.all:
        dbs = [p["db"] for p in T.odoo_processes() if p["kind"] == "server"]
        res = {"ok": True, "stopped": [T.stop(d)["name"] for d in dbs]}
        if JSON:
            return res
        print("Stopped: " + (", ".join(res["stopped"]) or "nothing was running"))
        return
    if not a.db:
        raise T.DBError("Database name required (or --all).", code="bad_args")
    res = T.stop(a.db)
    if JSON:
        return res
    print(res["message"])


def cmd_restart(a):
    res = T.restart(a.db, wait=not a.no_wait, log=log)
    if JSON:
        return res
    _summary(res)


def _summary(res: dict) -> None:
    if res.get("already_running"):
        print(f"{res['name']} is already running → {res['url']}")
    elif res.get("ok") is False:
        print(f"✗ {res.get('error')}")
        for line in res.get("log_tail", [])[-15:]:
            print("  " + line)
        if res.get("hint"):
            print(f"→ {res['hint']}")
    else:
        print(f"✓ {res['name']} → {res.get('url')}  ({res.get('message', '')})")


def cmd_logs(a):
    if a.follow:
        path = T.log_path(a.db)
        if not path.is_file():
            raise T.DBError("No log file.", hint=f"Start it with `odoodb start {a.db}`.")
        for line in T.read_log(a.db, lines=a.lines)["lines"]:
            print(line)
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            fh.seek(0, os.SEEK_END)
            try:
                while True:
                    line = fh.readline()
                    if line:
                        print(line, end="", flush=True)
                    else:
                        time.sleep(0.3)
            except KeyboardInterrupt:
                return
    res = T.read_log(a.db, lines=a.lines, errors_only=a.errors)
    if JSON:
        return res
    if res.get("note"):
        print(res["note"])
    print("\n".join(res["lines"]))


def cmd_history(a):
    import history
    if a.clear:
        res = history.clear(a.db)
        if JSON:
            return res
        print(res["message"])
        return
    T.require_db(a.db)
    levels = ["ERROR", "CRITICAL"] if a.errors else (["WARNING"] if a.warnings else None)
    res = history.events(a.db, levels=levels, limit=a.lines)
    if JSON:
        return res
    s = res["summary"]
    print(f"{a.db}: {s['errors']} error(s), {s['warnings']} warning(s) logged")
    for e in res["events"]:
        print(f"\n[{e['level']}] ×{e['count']}  last {e['last']}  {e['logger']}")
        print(f"  {e['message'][:300]}")
        if e.get("exception"):
            print(f"  → {e['exception']}")
        if a.full and e.get("detail"):
            print("\n".join("    " + l for l in e["detail"].splitlines()[-40:]))


def cmd_terminal(a):
    res = T.terminal_command(a.db, kind=a.kind, switch_branch=a.switch_branch, force=a.force, log=log)
    if JSON:
        return res
    os.chdir(res["cwd"])
    os.execve(res["executable"], [res["executable"], *res["args"]], res["env"])


def cmd_doctor(a):
    res = T.doctor(a.db)
    if JSON:
        return res
    if not res["issues"]:
        print(f"✓ {a.db}: no problem found")
    for i in res["issues"]:
        print(f"[{i['severity']}] {i['title']}")
        if i.get("detail"):
            print(f"    {i['detail']}")
        if i.get("fix"):
            print(f"    fix: odoodb repair {a.db} --fix {i['fix']}")


def cmd_repair(a):
    if a.list:
        res = {"ok": True, "fixes": T.REPAIRS}
        if JSON:
            return res
        for k, v in T.REPAIRS.items():
            print(f"  {k:<8} {v}")
        return
    res = T.repair(a.db, fixes=_csv(a.fix), force=a.force, log=log)
    if JSON:
        return res
    if not res["applied"]:
        print("Nothing to repair.")
    for i in res["remaining_issues"]:
        print(f"still: [{i['severity']}] {i['title']}")


def cmd_update(a):
    res = T.update_modules(a.db, [m for m in _csv(a.modules) if m != "all"], log=log,
                           switch_branch=a.switch_branch, force=a.force)
    if JSON:
        return res
    print(("✓ " if res["ok"] else "✗ ") + res["message"])


def cmd_install(a):
    res = T.install_modules(a.db, _csv(a.modules), log=log, switch_branch=a.switch_branch, force=a.force)
    if JSON:
        return res
    print(("✓ " if res["ok"] else "✗ ") + res["message"])


def cmd_neutralize(a):
    res = T.neutralize(a.db, log=log)
    if JSON:
        return res
    print(("✓ " if res["ok"] else "✗ ") + res.get("message", ""))


def cmd_duplicate(a):
    res = T.duplicate(a.src, a.new)
    if JSON:
        return res
    print(res["message"])


def cmd_rename(a):
    res = T.rename(a.old, a.new)
    if JSON:
        return res
    print(res["message"])


def cmd_delete(a):
    if not a.yes:
        raise T.DBError(f"Deleting '{a.db}' drops the database, its filestore and its script.",
                        hint="Confirm with --yes.", code="confirm")
    res = T.delete(a.db)
    if JSON:
        return res
    print(res["message"])


def cmd_reset(a):
    if not a.yes:
        raise T.DBError(f"Resetting '{a.db}' drops the database and its filestore, then creates it again "
                        "(same name, launch script, addons path and port).",
                        hint="Confirm with --yes.", code="confirm")
    res = T.reset(a.db, modules=_csv(a.modules), same_modules=a.same_modules, start_server=not a.no_start,
                  switch_branch=a.switch_branch, force=a.force, log=log, timeout=a.timeout)
    if JSON:
        return res
    print(f"\n{'✓' if res.get('ok') else '✗'} {res['name']} recreated with {', '.join(res['modules'])}")
    if res.get("url"):
        print(f"  {res['url']}  (admin / admin)")
    for line in res.get("errors") or []:
        print("  " + line)
    if res.get("hint"):
        print(f"→ {res['hint']}")


def cmd_script(a):
    changes = {"branch": a.branch, "addons": a.addons, "install": None if a.install is None else _csv(a.install),
               "update": None if a.update is None else _csv(a.update), "update_mode": a.update_mode,
               "port": a.port, "pyenv": a.pyenv}
    if all(v is None for v in changes.values()):
        res = T.script_settings(a.db)
        if JSON:
            return res
        sc = res["script"]
        if not sc:
            print(f"{a.db}: no launch script (`odoodb repair {a.db} --fix script`)")
            return
        print(sc["path"])
        for label, value in (("branch", sc["branch"]), ("python", sc["pyenv"]), ("port", res["port"]),
                             ("install", ", ".join(sc["install_modules"]) or "—"),
                             ("at start", f"{'-u' if sc['update_mode'] == 'update' else '--reinit'} "
                                          f"{', '.join(sc['update_modules'])}" if sc["update_modules"] else "nothing")):
            print(f"  {label:<9} {value}")
        print("  addons")
        for p in sc["addons_path"]:
            print(f"    {p}")
        return
    res = T.edit_script(a.db, **changes)
    if JSON:
        return res
    print(res["message"] + (f" {res['note']}" if res.get("note") else ""))


def cmd_port(a):
    res = T.set_port(a.db, a.port)
    if JSON:
        return res
    print(res.get("message") or res["port"])


def cmd_open(a):
    info = T.database_info(a.db)
    webbrowser.open(info["url"] + "/odoo")
    res = {"ok": True, "url": info["url"], "running": bool(info.get("running"))}
    if JSON:
        return res
    print(info["url"] + ("" if info.get("running") else "  (not running: `odoodb start " + a.db + "`)"))


def cmd_translate(a):
    res = T.translate(a.db, a.module, a.lang, out=a.out or "", ai=not a.no_ai, log=log)
    if JSON:
        return res
    print(f"✓ {res['path']}")


def cmd_branches(a):
    res = T.branches()
    if a.remote:
        import odoo_ops
        import toolchain
        local = set(res["branches"])
        res["remote_only"] = [b for b in toolchain.remote_branches(max_age=0 if a.refresh else 3600) if b not in local]
        res["branches"] = sorted(local | set(res["remote_only"]), key=odoo_ops._branch_sort_key)
    if JSON:
        return res
    print(f"current: {res['current']} (code {res['code_series']})")
    remote = set(res.get("remote_only", []))
    print("\n".join("  " + b + ("  (on GitHub, fetched when used)" if b in remote else "") for b in res["branches"]))


def cmd_pull(a):
    import toolchain
    paths = a.paths or [str(T.community_path())] + ([str(T.enterprise_path())] if T.enterprise_path() else [])
    res = toolchain.pull(paths, log=log)
    if JSON:
        return res
    for r in res["pulled"]:
        print(f"✓ {r['path']}: {r['message']}")


_MARK = {"ok": "✓", "warning": "⚠", "error": "✗", "optional": "○"}


def cmd_setup(a):
    import toolchain
    if a.list_actions:
        res = {"ok": True, "actions": toolchain.ACTIONS}
        if JSON:
            return res
        for k, v in toolchain.ACTIONS.items():
            print(f"  {k:<20} {v}")
        return
    if a.fix:
        res = toolchain.run_action(a.fix, log=log, path=a.path or "")
        if JSON:
            return res
        print(("✓ " if res.get("ok") else "✗ ") + res.get("message", ""))
        return
    res = toolchain.setup_status(network=not a.offline)
    if JSON:
        return res
    group = None
    for i in res["items"]:
        if i["group"] != group:
            group = i["group"]
            print(f"\n{group}")
        print(f"  {_MARK.get(i['status'], '?')} {i['title']}: {i['detail']}")
        if i.get("action"):
            print(f"      → odoodb setup --fix {i['action']}")
        elif i.get("link") and i["status"] != "ok":
            print(f"      → {i['link']}")
        for alt in i.get("alternatives") or []:
            if alt.get("action"):
                print(f"      → odoodb setup --fix {alt['action']} --path <folder>")
            elif alt.get("url"):
                print(f"      → {alt['label']} {alt['url']}")


def cmd_env(a):
    import toolchain
    if a.action == "list":
        res = toolchain.list_envs()
        if JSON:
            return res
        print(f"pyenv: {res['pyenv'] or 'not installed'} ({res['root']})"
              + (f" — every database forced on {res['forced']}" if res["forced"] else ""))
        for e in res["envs"]:
            if not e["exists"]:
                state = f"missing → odoodb env create {e['name']}"
            elif e.get("missing"):
                state = f"{len(e['missing'])} package(s) missing → odoodb env install {e['name']}"
            else:
                state = "ok"
            print(f"  {e['name']:<10} Python {e.get('python') or '—':<8} {e['branch']:<12} {state}")
        return
    if not a.name:
        raise T.DBError("Environment name required (e.g. odoo-19).", code="bad_args")
    # An Odoo version or branch (19.0, saas-19.4) stands for odoo-<major>; any other name is kept (3.12 is refused).
    is_series = re.fullmatch(r"(saas[-~])?\d{2}\.\d+", a.name)
    name = toolchain.env_name_for(a.name) if is_series else a.name
    if a.action == "create":
        res = toolchain.create_env(name, branch=a.branch or "", log=log, python=a.python or "",
                                   requirements=not a.no_requirements)
    else:
        lines = _csv(a.packages)
        if not lines:
            branch = a.branch or toolchain.branch_for_env(name)
            lines = toolchain.check_env(name, toolchain.requirements_lines(branch)).get("missing", [])
        res = toolchain.install_packages(name, lines, log=log) if lines else \
            {"ok": True, "name": name, "message": f"Nothing missing in {name}."}
    if JSON:
        return res
    print(("✓ " if res["ok"] else "✗ ") + res["message"])


def cmd_checkout(a):
    msg = T.ensure_branch(a.branch, force=a.force, log=log)
    res = {"ok": True, "message": msg, "code_series": T.code_series()}
    if JSON:
        return res
    print(msg)


def cmd_ps(a):
    res = {"ok": True, "running": T.odoo_processes()}
    if JSON:
        return res
    _print_table([[p["db"], p["port"], p["pid"], p["kind"], p["mode"], _size(p["rss"]), f"{p['uptime'] // 60} min"]
                  for p in res["running"]], ["DATABASE", "PORT", "PID", "KIND", "MODE", "MEMORY", "UPTIME"])


def cmd_settings(a):
    import config
    if a.set:
        updates = {}
        for item in a.set:
            k, _, v = item.partition("=")
            updates[k.strip()] = v.strip()
        unknown = [k for k in updates if k not in config._DEFAULTS]
        if unknown:
            raise T.DBError(f"Unknown setting: {', '.join(unknown)}",
                            hint="Known: " + ", ".join(config._DEFAULTS), code="bad_args")
        for k, v in list(updates.items()):
            if isinstance(config._DEFAULTS[k], int) and not isinstance(config._DEFAULTS[k], bool):
                updates[k] = int(v)
            elif isinstance(config._DEFAULTS[k], list):
                updates[k] = [x for x in v.split(",") if x]
        config._save_config(updates)
    res = T.settings()
    if JSON:
        return res
    for k, v in res.items():
        if k != "ok":
            print(f"  {k:<18} {v}")


def cmd_scaffold(a):
    import scaffold_generator as sg
    defaults = sg.get_defaults()
    theme = {k: v for k, v in defaults.items() if not isinstance(v, (list, dict)) or k.endswith("colors")}
    if a.theme:
        theme.update(json.load(open(os.path.expanduser(a.theme))))
    try:
        res = sg.generate_scaffold({"module_slug": a.slug, "output_path": a.out, "odoo_version": a.version,
                                    "theme_label": a.label or "", "theme": theme})
    except ValueError as e:
        raise T.DBError(str(e), code="bad_args")
    res = {"ok": True, **res}
    if JSON:
        return res
    print(f"✓ {res.get('path') or res}")


def cmd_self_update(a):
    import version
    if a.rebuild:
        res = {**version.rebuild(), "updating": True, "behind": 0}
    else:
        res = version.check_for_update()
        if not a.check and res.get("update_available"):
            res = {**res, **version.apply_update(), "updating": True}
    if JSON:
        return res
    if res.get("error"):
        print(f"✗ {res['error']}")
        if res.get("hint"):
            print(f"→ {res['hint']}")
    elif res.get("updating"):
        what = "Rebuilding" if a.rebuild else f"Updating ({res['behind']} new commit(s))"
        print(f"{what}: the app restarts when done. "
              f"Log: ~/Library/Logs/Odoo Database Manager/update.log")
    elif res.get("update_available"):
        print(f"{res['behind']} new commit(s) available: run `odoodb self-update`.")
    else:
        print(f"Up to date ({res['current_version']}).")


# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="odoodb", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true", help="JSON output on stdout (progress on stderr)")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command")

    def add(name, func, help_):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        sp.set_defaults(func=func)
        return sp

    s = add("list", cmd_list, "list the databases")
    s.add_argument("--running", action="store_true", help="only running ones")

    s = add("info", cmd_info, "everything about one database")
    s.add_argument("db")
    s.add_argument("--modules", action="store_true", help="include the module list")

    s = add("modules", cmd_modules, "modules of a database (state, version, source)")
    s.add_argument("db")
    s.add_argument("--custom", action="store_true", help="only custom / missing-code modules")

    s = add("create", cmd_create, "create a database, then start it")
    s.add_argument("db")
    s.add_argument("-b", "--branch", help="Odoo branch (default: the one checked out), e.g. 19.0, saas-19.4")
    s.add_argument("-m", "--modules", help="modules to install, comma-separated (default: base)")
    s.add_argument("-a", "--addons", nargs="*", help="extra addons folders")
    s.add_argument("--dump", help="restore this .sql dump instead of a fresh database")
    s.add_argument("--filestore", help="filestore folder that goes with the dump")
    s.add_argument("--no-sanitize", action="store_true", help="keep the dump as is (no neutralisation)")
    s.add_argument("--no-start", action="store_true", help="initialise then stop (no server left running)")
    s.add_argument("--folder", help="subfolder of the scripts folder for the launch script")
    s.add_argument("--force", action="store_true", help="switch branch even if other databases run")
    s.add_argument("--timeout", type=int, default=1800)

    for name, func, h in (("start", cmd_start, "start a database (background, logs captured)"),):
        s = add(name, func, h)
        s.add_argument("db")
        s.add_argument("--terminal", action="store_true", help="open it in the terminal app instead")
        s.add_argument("--no-wait", action="store_true", help="return immediately")
        s.add_argument("--switch-branch", action="store_true", help="checkout the database's branch if needed")
        s.add_argument("--force", action="store_true", help="switch branch even if other databases run")
        s.add_argument("--timeout", type=int, default=600)

    s = add("stop", cmd_stop, "stop a database")
    s.add_argument("db", nargs="?")
    s.add_argument("--all", action="store_true")

    s = add("restart", cmd_restart, "restart a database")
    s.add_argument("db")
    s.add_argument("--no-wait", action="store_true")

    s = add("logs", cmd_logs, "server log of a database started by odoodb")
    s.add_argument("db")
    s.add_argument("-n", "--lines", type=int, default=200)
    s.add_argument("-f", "--follow", action="store_true")
    s.add_argument("--errors", action="store_true", help="only errors and tracebacks")

    s = add("history", cmd_history, "past warnings, errors and tracebacks of a database")
    s.add_argument("db")
    s.add_argument("--errors", action="store_true", help="only errors / critical")
    s.add_argument("--warnings", action="store_true", help="only warnings")
    s.add_argument("-n", "--lines", type=int, default=50, help="how many events (most recent first)")
    s.add_argument("--full", action="store_true", help="print the tracebacks")
    s.add_argument("--clear", action="store_true", help="forget the history")

    s = add("terminal", cmd_terminal, "run a database in the foreground here (or odoo shell / zsh); --json: the command")
    s.add_argument("db")
    s.add_argument("--kind", choices=["server", "shell", "zsh"], default="server")
    s.add_argument("--switch-branch", action="store_true")
    s.add_argument("--force", action="store_true")

    s = add("doctor", cmd_doctor, "diagnose a database")
    s.add_argument("db")

    s = add("repair", cmd_repair, "fix the problems found by doctor")
    s.add_argument("db", nargs="?")
    s.add_argument("--fix", help="comma-separated fixes (default: all suggested). See --list")
    s.add_argument("--list", action="store_true", help="list the available fixes")
    s.add_argument("--force", action="store_true")

    for name, func, h in (("update", cmd_update, "upgrade modules (-u), server stopped"),
                          ("install", cmd_install, "install modules (-i), server stopped")):
        s = add(name, func, h)
        s.add_argument("db")
        s.add_argument("-m", "--modules", required=(name == "install"), help="comma-separated (update default: all)")
        s.add_argument("--switch-branch", action="store_true")
        s.add_argument("--force", action="store_true")

    s = add("neutralize", cmd_neutralize, "neutralize (crons, mail servers, …)")
    s.add_argument("db")

    s = add("duplicate", cmd_duplicate, "copy a database (+ filestore + script)")
    s.add_argument("src")
    s.add_argument("new")

    s = add("rename", cmd_rename, "rename a database (+ filestore + script)")
    s.add_argument("old")
    s.add_argument("new")

    s = add("delete", cmd_delete, "drop a database, its filestore and script")
    s.add_argument("db")
    s.add_argument("--yes", action="store_true")

    s = add("reset", cmd_reset, "drop a database and recreate it fresh (same name, script, addons, port)")
    s.add_argument("db")
    s.add_argument("--yes", action="store_true")
    s.add_argument("-m", "--modules", help="modules to install (default: the launch script's INSTALL_MODULES)")
    s.add_argument("--same-modules", action="store_true", help="reinstall the modules installed now")
    s.add_argument("--no-start", action="store_true", help="initialise then stop (no server left running)")
    s.add_argument("--switch-branch", action="store_true", help="checkout the database's branch if needed")
    s.add_argument("--force", action="store_true", help="switch branch even if other databases run")
    s.add_argument("--timeout", type=int, default=1800)

    s = add("script", cmd_script, "show or edit a database's launch script (branch, addons, modules, port…)")
    s.add_argument("db")
    s.add_argument("-b", "--branch")
    s.add_argument("-a", "--addons", nargs="+", help="the whole addons path, in order")
    s.add_argument("--install", help="modules installed when the database is created, comma-separated ('' = none)")
    s.add_argument("--update", help="modules updated at each start, comma-separated ('' = none)")
    s.add_argument("--update-mode", choices=["update", "reinit"], help="-u or --reinit at each start")
    s.add_argument("--port", type=int)
    s.add_argument("--pyenv", help="Python environment (pyenv), e.g. odoo-19")

    s = add("port", cmd_port, "show or set the HTTP port")
    s.add_argument("db")
    s.add_argument("port", nargs="?", type=int)

    s = add("open", cmd_open, "open in the browser")
    s.add_argument("db")

    s = add("translate", cmd_translate, "export a module's .po and translate it with AI")
    s.add_argument("db")
    s.add_argument("-m", "--module", required=True)
    s.add_argument("-l", "--lang", required=True, help="e.g. fr_FR, nl_BE")
    s.add_argument("-o", "--out", help="file or folder (default: the module's i18n/ folder)")
    s.add_argument("--no-ai", action="store_true", help="only export the .po")

    s = add("branches", cmd_branches, "branches available in core")
    s.add_argument("--remote", action="store_true", help="also the version branches on GitHub not fetched yet")
    s.add_argument("--refresh", action="store_true", help="with --remote: ignore the one-hour cache")
    s = add("pull", cmd_pull, "git pull --ff-only core + enterprise (or the given repositories)")
    s.add_argument("paths", nargs="*")
    s = add("setup", cmd_setup, "check what this Mac needs and install it")
    s.add_argument("--fix", metavar="ACTION", help="run one action (see --list-actions)")
    s.add_argument("--path", help="locate:<kind>: the folder of the existing clone")
    s.add_argument("--list-actions", action="store_true")
    s.add_argument("--offline", action="store_true", help="skip the GitHub access checks")
    s = add("env", cmd_env, "Python environments (pyenv) of the Odoo versions")
    s.add_argument("action", choices=["list", "create", "install"])
    s.add_argument("name", nargs="?", help="odoo-19, or a version/branch (19.0, saas-19.4)")
    s.add_argument("-b", "--branch", help="branch whose requirements.txt to use (default: <version>.0)")
    s.add_argument("-p", "--packages", help="install: these packages instead of the missing requirements")
    s.add_argument("--python", help="create: Python version, e.g. 3.12 (default: the one of the Odoo version)")
    s.add_argument("--no-requirements", action="store_true", help="create: an empty environment (pip only)")
    s = add("checkout", cmd_checkout, "switch core + enterprise to a branch")
    s.add_argument("branch")
    s.add_argument("--force", action="store_true")

    add("ps", cmd_ps, "running Odoo processes")
    s = add("scaffold", cmd_scaffold, "generate a website_<slug> theme module")
    s.add_argument("slug")
    s.add_argument("-o", "--out", required=True, help="destination folder")
    s.add_argument("-v", "--version", default="19", choices=["18", "19"])
    s.add_argument("--label", help="theme name")
    s.add_argument("--theme", help="JSON file overriding the theme variables (colors, fonts…)")
    s = add("self-update", cmd_self_update, "update odoodb and the app from GitHub")
    s.add_argument("--check", action="store_true", help="only check")
    s.add_argument("--rebuild", action="store_true", help="rebuild and reinstall the app from the current code, without pulling")
    s = add("settings", cmd_settings, "show or change settings")
    s.add_argument("--set", nargs="*", metavar="KEY=VALUE")
    return p


def main(argv=None) -> int:
    global JSON
    args = build_parser().parse_args(argv)
    JSON = bool(getattr(args, "json", False))
    try:
        res = args.func(args)
    except T.DBError as e:
        if JSON:
            print(T.dumps(e.to_dict()))
        else:
            print(f"✗ {e.message}", file=sys.stderr)
            if e.hint:
                print(f"→ {e.hint}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    if JSON and res is not None:
        print(T.dumps(res))
        return 0 if res.get("ok", True) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
