#!/usr/bin/env python3
"""
Odoo Database Manager - Web UI + CLI.
  python3 app.py              → launches the web UI
  python3 app.py create mydb  → CLI

CLI commands:
  create <db>     Create or update a database
  branch <name>   Switch the git branch (core + enterprise)
  check           Check that core and enterprise are on the same branch
  locks <db>      Clear module locks
  branches        List available branches
"""
import argparse
import sys
import os
import subprocess
from pathlib import Path

from config import DEFAULT_ODOO_PATH, VERSION_CONFIG, get_pyenv_for_branch
from odoo_ops import (
    build_addons_path,
    check_branches_match,
    clear_module_locks,
    create_launch_script,
    db_exists,
    get_git_branches,
    get_pyenv_env,
    run_odoo,
    switch_branch,
)


def _set_activation_policy(window_only: bool) -> None:
    """
    window_only=True  -> app régulière (icône Dock visible).
    window_only=False -> app accessoire menubar (pas d'icône Dock).
    """
    try:
        from AppKit import NSApplication
        app = NSApplication.sharedApplication()
        # 0: Regular, 1: Accessory
        app.setActivationPolicy_(0 if window_only else 1)
    except Exception:
        # Ne pas bloquer l'app si AppKit n'est pas disponible.
        pass


def _quit_entire_app() -> None:
    """Quitte toute l'application sans bloquer la fermeture de la fenêtre."""
    if os.environ.get("ODOO_DBM_QUITTING") == "1":
        return
    os.environ["ODOO_DBM_QUITTING"] = "1"
    # 1) Demande de quit asynchrone (évite le "Not Responding" sur fermeture fenêtre)
    try:
        subprocess.Popen(
            ["/usr/bin/osascript", "-e", 'tell application id "com.odoo.dbmanager" to quit'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
    # 2) Fallback: si une instance reste bloquée, forcer l'arrêt des binaires de l'app.
    try:
        subprocess.Popen(
            [
                "/bin/sh",
                "-c",
                "sleep 1; /usr/bin/pkill -f 'Odoo Database Manager.app/Contents/MacOS/Odoo Database Manager' >/dev/null 2>&1 || true",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def _resolve_path(path: str) -> str:
    return os.path.expanduser(path) if path else DEFAULT_ODOO_PATH


def cmd_create(args):
    path = _resolve_path(args.path)
    db = args.db
    modules = [m.strip() for m in (args.modules or "website,website_sale").split(",") if m.strip()]
    extra = [os.path.expanduser(f.strip()) for f in (args.extra or "").split(",") if f.strip()]

    branch = args.branch or "19.0"
    version = args.version if args.version in ("18", "19") else ("19" if "19" in branch else "18")
    pyenv = get_pyenv_env(version) if version in ("18", "19") else get_pyenv_for_branch(branch)

    ok, msg = check_branches_match(path, branch)
    if not ok:
        print(f"Warning: {msg}\n")

    addons = build_addons_path(path, extra)
    print(f"Database: {db} | Pyenv: {pyenv}")
    print(f"Addons: {addons[:100]}...")
    print("---")

    if db_exists(db):
        clear_module_locks(db)

    code, out = run_odoo(
        path, db, addons,
        install_modules=modules,
        update_modules=modules,
        pyenv_env=pyenv,
        on_output=print,
    )
    if code == 0:
        ok_script, msg_script = create_launch_script(path, db, addons, pyenv, version=version, branch=branch)
        if ok_script:
            print(f"Script created: {msg_script}")
        else:
            print(f"Script: {msg_script}")
        print("--- Completed successfully ---")
    else:
        print(f"--- Error (code {code}) ---")
    return code


def cmd_branch(args):
    path = _resolve_path(args.path)
    ok, msg = switch_branch(path, args.branch)
    print(msg)
    return 0 if ok else 1


def cmd_check(args):
    path = _resolve_path(args.path)
    branch = args.branch or "19.0"
    ok, msg = check_branches_match(path, branch)
    print(msg)
    return 0 if ok else 1


def cmd_locks(args):
    if clear_module_locks(args.db):
        print("Locks cleared.")
        return 0
    print("Could not clear locks.")
    return 1


def cmd_branches(args):
    path = _resolve_path(args.path)
    core = Path(path) / "core"
    if not core.is_dir():
        print(f"core repository not found: {core}")
        return 1
    branches = get_git_branches(str(core))
    if not branches:
        print("No branches found.")
        return 0
    for b in branches:
        print(f"  {b}")
    return 0


WINDOW_BACKGROUND = "#0a0507"


def _dark_title_bar(win) -> None:
    """Barre de titre noire, sans titre, boutons fermer/réduire/agrandir conservés."""
    try:
        import AppKit
        from PyObjCTools import AppHelper
    except Exception:
        return

    def apply():
        try:
            ns_window = win.native
            color = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(10 / 255, 5 / 255, 7 / 255, 1.0)
            ns_window.setAppearance_(AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameDarkAqua))
            ns_window.setTitlebarAppearsTransparent_(True)
            ns_window.setTitleVisibility_(AppKit.NSWindowTitleHidden)
            ns_window.setBackgroundColor_(color)
            # pywebview peint la vue de la barre de titre avec la couleur système : on la remplace.
            titlebar = ns_window.contentView().superview().subviews().lastObject()
            if titlebar is not None and titlebar.respondsToSelector_("setBackgroundColor:"):
                titlebar.setBackgroundColor_(color)
        except Exception:
            pass

    # Les appels AppKit doivent se faire sur le thread principal.
    AppHelper.callAfter(apply)


def main():
    # --window-only : fenêtre seule (subprocess)
    if "--window-only" in sys.argv:
        _set_activation_policy(window_only=True)
        url = next((a for a in sys.argv[1:] if a.startswith("http")), "http://127.0.0.1:5151")
        import webview
        win = webview.create_window(
            "Odoo Database Manager", url, width=960, height=820, min_size=(700, 550),
            background_color=WINDOW_BACKGROUND,
        )
        try:
            # Fermer la fenetre GUI doit aussi fermer la menubar.
            win.events.closed += lambda: _quit_entire_app()
            win.events.shown += lambda: _dark_title_bar(win)
        except Exception:
            pass
        webview.start()
        return 0
    # Pas d'argument → menu bar + fenêtre
    if len(sys.argv) == 1:
        _set_activation_policy(window_only=False)
        from app_web import main as web_main
        return web_main() or 0

    parser = argparse.ArgumentParser(
        description="Odoo Database Manager - CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--path", "-p", help=f"Odoo path (default: {DEFAULT_ODOO_PATH})")

    sub = parser.add_subparsers(dest="cmd", required=True)

    # create
    c = sub.add_parser("create", help="Create or update a database")
    c.add_argument("db", help="Database name")
    c.add_argument("--modules", "-m", help="Modules to install (comma-separated)")
    c.add_argument("--branch", "-b", help="Git branch (default: 19.0)")
    c.add_argument("--version", "-v", choices=["18", "19"], help="Odoo version (determines pyenv)")
    c.add_argument("--extra", "-e", help="Extra addons folders (comma-separated)")
    c.set_defaults(func=cmd_create)

    # branch
    b = sub.add_parser("branch", help="Switch branch (core + enterprise)")
    b.add_argument("branch", help="Branch name (e.g. 19.0)")
    b.set_defaults(func=cmd_branch)

    # check
    k = sub.add_parser("check", help="Check branches")
    k.add_argument("--branch", "-b", default="19.0", help="Expected branch")
    k.set_defaults(func=cmd_check)

    # locks
    l = sub.add_parser("locks", help="Clear module locks")
    l.add_argument("db", help="Database name")
    l.set_defaults(func=cmd_locks)

    # branches
    br = sub.add_parser("branches", help="List branches")
    br.set_defaults(func=cmd_branches)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    exit(main())
