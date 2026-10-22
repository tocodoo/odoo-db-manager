"""Version de l'application et mise à jour depuis GitHub.

L'app tourne directement depuis le clone git créé par install.sh :
mettre à jour = git pull, puis install.sh --update (dépendances, app, relance).
"""
from __future__ import annotations

import os
import subprocess
from typing import Any

APP_VERSION = "2.0"

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.expanduser("~/Library/Logs/Odoo Database Manager")
INSTALL_COMMAND = (
    "curl -fsSL https://raw.githubusercontent.com/tocodoo/odoo-db-manager/main/install.sh | bash"
)


def _git(*args: str, timeout: float = 15.0) -> str:
    result = subprocess.run(
        ["git", "-C", REPO_DIR, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or f"git {args[0]} failed")
    return result.stdout.strip()


def get_app_version() -> str:
    """Version + commit installé (ex. 2.0 · a1b2c3d)."""
    try:
        return f"{APP_VERSION} · {_git('rev-parse', '--short', 'HEAD', timeout=5)}"
    except Exception:
        return APP_VERSION


def check_for_update() -> dict[str, Any]:
    """Compte les commits GitHub pas encore installés."""
    current = get_app_version()
    if not os.path.isdir(os.path.join(REPO_DIR, ".git")):
        return {
            "ok": False,
            "current_version": current,
            "update_available": False,
            "error": f"Not a git install: re-run {INSTALL_COMMAND}",
        }
    try:
        _git("fetch", "--quiet")
        behind = int(_git("rev-list", "--count", "HEAD..@{u}", timeout=5) or 0)
    except Exception as e:
        return {"ok": False, "current_version": current, "update_available": False, "error": str(e)}
    return {"ok": True, "current_version": current, "update_available": behind > 0, "behind": behind}


def apply_update() -> dict[str, Any]:
    """git pull, puis install.sh --update en arrière-plan (il quitte et relance l'app)."""
    try:
        _git("pull", "--ff-only", "--quiet", timeout=120)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    os.makedirs(LOG_DIR, exist_ok=True)
    log = open(os.path.join(LOG_DIR, "update.log"), "w")
    subprocess.Popen(
        ["/bin/bash", os.path.join(REPO_DIR, "install.sh"), "--update"],
        cwd=REPO_DIR,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return {"ok": True}
