#!/usr/bin/env python3
"""Odoo Database Manager - Interface web (Flask) avec CLI en arrière-plan."""
import json
import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, render_template, request, Response

from config import (
    detect_all_paths,
    get_odoo_community_path,
    get_odoo_enterprise_path,
    get_odoo_http_port,
    get_terminal_app,
    get_pyenv_for_branch,
    get_pyenv_global_env,
    get_pyenv_root,
    get_psql_path,
    get_scripts_paths,
    save_odoo_path,
    save_psql_path,
    save_pyenv_root,
    save_pyenv_global_env,
    save_scripts_paths,
)
from scaffold_generator import (
    SUPPORTED_ODOO_VERSIONS,
    generate_scaffold,
    get_defaults,
    get_template_dir,
)
from odoo_ops import (
    build_addons_path,
    build_addons_path_for_modules,
    check_branches_match,
    get_repos_behind_counts,
    check_repos_up_to_date,
    clear_module_locks,
    create_db_from_dump,
    create_launch_script,
    db_exists,
    delete_db_complete,
    duplicate_database,
    get_current_branch,
    get_db_version,
    get_db_version_and_branch,
    get_git_branches,
    get_pyenv_env,
    get_running_odoo_db,
    get_script_config,
    get_script_path_for_db,
    get_script_subdirectory,
    list_scripts_subdirectories,
    list_databases,
    quit_warp,
    restart_odoo_server,
    run_odoo_create_in_terminal,
    run_odoo_in_terminal,
    run_prerequisites_check_in_terminal,
    run_script_for_db,
    pull_core_enterprise,
    start_odoo_server,
    stop_all_odoo_servers,
    stop_odoo_server,
    switch_branch,
    update_script_config,
)

# Chemin templates (py2app bundle ou dev)
if getattr(sys, "frozen", False):
    _base = os.path.dirname(sys.executable)
    _template = os.path.join(_base, "..", "Resources", "templates")
else:
    _template = os.path.join(os.path.dirname(__file__), "templates")

app = Flask(__name__, template_folder=_template)

_pending_navigation = {"target": None}


def request_navigation(target: str) -> None:
    """Demande à la fenêtre de l'app (pywebview) de naviguer vers un onglet donné.

    La fenêtre et le menu bar tournent dans des process séparés mais partagent
    ce même serveur Flask : le front-end poll /api/pending-navigation.
    """
    _pending_navigation["target"] = target


@app.route("/api/pending-navigation")
def api_pending_navigation():
    target = _pending_navigation["target"]
    _pending_navigation["target"] = None
    return jsonify({"target": target})


def _path():
    return get_odoo_community_path()


@app.route("/")
def index():
    from version import get_app_version

    return render_template(
        "index.html",
        default_path=get_odoo_community_path(),
        app_version=get_app_version(),
    )


@app.route("/api/app/version")
def api_app_version():
    from version import get_app_version, UPDATE_MANIFEST_URL

    return jsonify({"version": get_app_version(), "manifest_url": UPDATE_MANIFEST_URL})


@app.route("/api/app/update-check")
def api_app_update_check():
    from config import get_dismissed_app_version
    from version import check_for_update

    return jsonify(check_for_update(dismissed_version=get_dismissed_app_version()))


@app.route("/api/app/update-dismiss", methods=["POST"])
def api_app_update_dismiss():
    from config import save_dismissed_app_version

    data = request.json or {}
    version = (data.get("version") or "").strip()
    if not version:
        return jsonify({"ok": False, "message": "version requise"}), 400
    ok = save_dismissed_app_version(version)
    return jsonify({"ok": ok})


@app.route("/api/app/open-download", methods=["POST"])
def api_app_open_download():
    data = request.json or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "message": "url requise"}), 400
    webbrowser.open(url)
    return jsonify({"ok": True})


def _detect_version(branch: str) -> str:
    """Déduit la version (18/19) depuis le nom de branche."""
    if not branch:
        return "19"
    if branch.startswith("19") or "19" in (branch.split("-")[-1] if "-" in branch else branch):
        return "19"
    return "18"


@app.route("/api/current")
def api_current():
    """Retourne la branche et version actuelles du dépôt core."""
    path = _path()
    core = Path(get_odoo_community_path())
    if not core.is_dir():
        return jsonify({"branch": "19.0", "version": "19", "branches": ["19.0"]})
    branch = get_current_branch(str(core)) or "19.0"
    branches = get_git_branches(str(core)) or ["19.0"]
    version = _detect_version(branch)
    return jsonify({"branch": branch, "version": version, "branches": branches})


def _list_pyenv_envs() -> list[str]:
    """Liste les environnements trouvés sous <pyenv_root>/versions."""
    root = Path(get_pyenv_root()).expanduser().resolve()
    versions = root / "versions"
    if not versions.is_dir():
        return []
    envs: list[str] = []
    for item in versions.iterdir():
        if not item.is_dir():
            continue
        # Certains envs exposent "python" sans "python3" (selon machine/pyenv).
        if (item / "bin" / "python3").exists() or (item / "bin" / "python").exists():
            envs.append(item.name)
    envs.sort()
    return envs


@app.route("/api/pyenv/envs")
def api_pyenv_envs():
    envs = _list_pyenv_envs()
    return jsonify({"envs": envs, "global_env": get_pyenv_global_env()})


@app.route("/api/pyenv/global", methods=["POST"])
def api_pyenv_global_set():
    data = request.json or {}
    env_name = (data.get("env_name") or "").strip()
    envs = _list_pyenv_envs()
    if env_name and env_name not in envs:
        return jsonify({"ok": False, "message": f"Environnement introuvable: {env_name}"}), 400
    # Forcer l'environnement global pyenv pour tous les nouveaux shells.
    applied = False
    apply_message = ""
    shell_profiles_updated = False
    try:
        pyenv_root = Path(get_pyenv_root()).expanduser().resolve()
        pyenv_bin = pyenv_root / "bin" / "pyenv"
        extra_path = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
        which_pyenv = shutil.which("pyenv", path=extra_path) or shutil.which("pyenv")
        candidates = [
            str(pyenv_bin),
            "/opt/homebrew/bin/pyenv",
            "/usr/local/bin/pyenv",
            which_pyenv or "",
        ]
        pyenv_cmd = next((c for c in candidates if c and Path(c).exists()), "")
        if pyenv_cmd:
            target = env_name or "system"
            cmd_args = [pyenv_cmd, "global", target]
            env = os.environ.copy()
            env["PYENV_ROOT"] = str(pyenv_root)
            env["PATH"] = f"{pyenv_root / 'bin'}:{pyenv_root / 'shims'}:{extra_path}:{env.get('PATH', '')}"
            result = subprocess.run(
                cmd_args,
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
            )
            if result.returncode != 0:
                # Fallback 1: exécuter via shell login (certaines VM requièrent init shell pyenv)
                shell_cmd = (
                    'eval "$(pyenv init -)" >/dev/null 2>&1 || true; '
                    'eval "$(pyenv virtualenv-init -)" >/dev/null 2>&1 || true; '
                    f'pyenv global {target}'
                )
                result2 = subprocess.run(
                    ["/bin/zsh", "-lc", shell_cmd],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    env=env,
                )
                if result2.returncode == 0:
                    applied = True
                else:
                    # Fallback 2 hard: écrire directement le fichier ~/.pyenv/version.
                    try:
                        version_file = pyenv_root / "version"
                        version_file.write_text(target + "\n", encoding="utf-8")
                        applied = True
                        apply_message = "pyenv global indisponible, fallback fichier version appliqué"
                    except Exception:
                        err = (result2.stderr or result2.stdout or result.stderr or result.stdout or "Erreur pyenv global").strip()
                        return jsonify({"ok": False, "message": err}), 500
            else:
                applied = True
        else:
            # Fallback hard sans binaire pyenv.
            target = env_name or "system"
            try:
                version_file = pyenv_root / "version"
                version_file.write_text(target + "\n", encoding="utf-8")
                applied = True
                apply_message = "pyenv binaire introuvable, fallback fichier version appliqué"
            except Exception:
                apply_message = "pyenv introuvable (env enregistré dans l'app uniquement)"

        # Override durable pour tous les nouveaux shells (zsh/bash),
        # même si un profil local force un autre env.
        manager_file = Path.home() / ".dbmanager_pyenv_env"
        if env_name:
            manager_content = (
                f'export PYENV_ROOT="{pyenv_root}"\n'
                f'export PYENV_VERSION="{env_name}"\n'
            )
        else:
            manager_content = (
                f'export PYENV_ROOT="{pyenv_root}"\n'
                "unset PYENV_VERSION\n"
            )
        manager_file.write_text(manager_content, encoding="utf-8")

        source_line = '[ -f "$HOME/.dbmanager_pyenv_env" ] && source "$HOME/.dbmanager_pyenv_env"'
        profiles = [
            Path.home() / ".zshrc",
            Path.home() / ".zprofile",
            Path.home() / ".bashrc",
            Path.home() / ".bash_profile",
            Path.home() / ".profile",
        ]
        for profile in profiles:
            try:
                if profile.exists():
                    txt = profile.read_text(encoding="utf-8")
                    if source_line in txt:
                        continue
                    profile.write_text(txt.rstrip() + "\n\n# Managed by Odoo DB Manager\n" + source_line + "\n", encoding="utf-8")
                else:
                    profile.write_text("# Managed by Odoo DB Manager\n" + source_line + "\n", encoding="utf-8")
                shell_profiles_updated = True
            except Exception:
                # Ne pas bloquer si un profil n'est pas accessible.
                pass
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500
    if not save_pyenv_global_env(env_name):
        return jsonify({"ok": False, "message": "Impossible d'enregistrer l'environnement global"}), 500
    return jsonify({
        "ok": True,
        "global_env": env_name,
        "applied": applied,
        "message": apply_message or ("Override shell installé" if shell_profiles_updated else ""),
    })


@app.route("/api/branches")
def api_branches():
    core = Path(get_odoo_community_path())
    if not core.is_dir():
        return jsonify({"error": f"core introuvable: {core}"}), 404
    branches = get_git_branches(str(core))
    return jsonify({"branches": branches})


@app.route("/api/check", methods=["POST"])
def api_check():
    path = _path()
    branch = (request.json or {}).get("branch", "19.0")
    ok, msg = check_branches_match(path, branch)
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/branch", methods=["POST"])
def api_branch():
    path = _path()
    branch = (request.json or {}).get("branch")
    if not branch:
        return jsonify({"error": "branch requis"}), 400
    ok, msg = switch_branch(path, branch)
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/git/check-updates", methods=["POST"])
def api_git_check_updates():
    path = _path()
    ok, counts = get_repos_behind_counts(path)
    total = int(counts.get("core", 0)) + int(counts.get("enterprise", 0))
    return jsonify({"ok": ok and total == 0, "counts": counts})


@app.route("/api/git/pull", methods=["POST"])
def api_git_pull():
    path = _path()
    ok, msg = pull_core_enterprise(path)
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/locks", methods=["POST"])
def api_locks():
    db = (request.json or {}).get("db")
    if not db:
        return jsonify({"error": "db requis"}), 400
    ok = clear_module_locks(db)
    return jsonify({"ok": ok, "message": "Verrous nettoyés." if ok else "Impossible de nettoyer."})


def _escape_applescript(s: str) -> str:
    """Échappe les guillemets pour AppleScript."""
    return (s or "").replace("\\", "\\\\").replace('"', '\\"')


@app.route("/api/browse-folder", methods=["POST"])
def api_browse_folder():
    """Ouvre le sélecteur de dossier natif macOS et retourne le chemin choisi."""
    data = request.json or {}
    prompt = _escape_applescript(data.get("prompt", "Choisir un dossier"))
    try:
        result = subprocess.run(
            ["osascript", "-e", f'return POSIX path of (choose folder with prompt "{prompt}")'],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            return jsonify({"ok": True, "path": result.stdout.strip()})
        return jsonify({"ok": False, "message": "Aucun dossier sélectionné"})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "message": "Délai dépassé"}), 500
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/browse-file", methods=["POST"])
def api_browse_file():
    """Ouvre le sélecteur de fichier natif macOS et retourne le chemin choisi."""
    data = request.json or {}
    prompt = _escape_applescript(data.get("prompt", "Choisir un fichier"))
    try:
        result = subprocess.run(
            ["osascript", "-e", f'return POSIX path of (choose file with prompt "{prompt}")'],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            return jsonify({"ok": True, "path": result.stdout.strip()})
        return jsonify({"ok": False, "message": "Aucun fichier sélectionné"})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "message": "Délai dépassé"}), 500
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/permissions/accessibility", methods=["POST"])
def api_permissions_accessibility():
    """Déclenche l'apparition de l'app dans la liste Accessibilité et ouvre le réglage."""
    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to get name of first process'],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        pass
    try:
        subprocess.run(
            ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500
    return jsonify({"ok": True, "message": "Réglages Accessibilité ouverts. Cochez Terminal (ou l'app) dans la liste."})


@app.route("/api/permissions/automation", methods=["POST"])
def api_permissions_automation():
    """Déclenche l'apparition de l'app dans la liste Automatisation (contrôle de Warp/System Events) et ouvre le réglage."""
    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to get name of first process'],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        pass
    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "Warp" to activate'],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        pass
    try:
        subprocess.run(
            ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500
    return jsonify({"ok": True, "message": "Réglages Automatisation ouverts. Cochez System Events et Warp dans la liste."})


@app.route("/api/check-prerequisites", methods=["POST"])
def api_check_prerequisites():
    """Ouvre le terminal configuré et lance un script de vérification des prérequis."""
    ok, msg = run_prerequisites_check_in_terminal()
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/scaffold/defaults")
def api_scaffold_defaults():
    """Options par défaut pour le générateur de module website."""
    try:
        defaults = get_defaults()
        defaults["template_exists"] = all(
            get_template_dir(v).is_dir() for v in SUPPORTED_ODOO_VERSIONS
        )
        defaults["templates_available"] = {
            v: get_template_dir(v).is_dir() for v in SUPPORTED_ODOO_VERSIONS
        }
        return jsonify(defaults)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scaffold/generate", methods=["POST"])
def api_scaffold_generate():
    """Génère un module website_* depuis le template website_scaffold."""
    data = request.json or {}
    try:
        result = generate_scaffold(data)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"ok": False, "message": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/config/detect")
def api_config_detect():
    """Retourne les chemins auto-détectés (sans sauvegarder)."""
    try:
        detected = detect_all_paths()
        return jsonify(detected)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/config")
def api_config_get():
    """Retourne toute la config (chemins personnalisables)."""
    from config import _load_config
    cfg = _load_config()
    return jsonify({
        "odoo_path": cfg.get("odoo_path", "~/odoo"),
        "odoo_community_path": cfg.get("odoo_community_path", ""),
        "odoo_enterprise_path": cfg.get("odoo_enterprise_path", ""),
        "scripts_paths": cfg.get("scripts_paths", ["~/Scripts"]),
        "pyenv_root": cfg.get("pyenv_root", "~/.pyenv"),
        "pyenv_global_env": cfg.get("pyenv_global_env", ""),
        "psql_path": cfg.get("psql_path", ""),
        "terminal_app": cfg.get("terminal_app", "Warp"),
        "odoo_http_port": int(cfg.get("odoo_http_port", 8069)),
    })


@app.route("/api/config", methods=["POST"])
def api_config_save():
    """Sauvegarde la config."""
    data = request.json or {}
    from config import _save_config

    updates = {}
    if "odoo_path" in data:
        updates["odoo_path"] = (data["odoo_path"] or "").strip()
    if "odoo_community_path" in data:
        updates["odoo_community_path"] = (data["odoo_community_path"] or "").strip()
    if "odoo_enterprise_path" in data:
        updates["odoo_enterprise_path"] = (data["odoo_enterprise_path"] or "").strip()
    if "scripts_paths" in data:
        paths = data["scripts_paths"]
        if isinstance(paths, str):
            paths = [p.strip() for p in paths.split(",") if p.strip()]
        else:
            paths = [p.strip() for p in (paths or []) if p and str(p).strip()]
        updates["scripts_paths"] = paths or ["~/Scripts"]
    if "pyenv_root" in data:
        updates["pyenv_root"] = (data["pyenv_root"] or "").strip()
    if "psql_path" in data:
        updates["psql_path"] = (data["psql_path"] or "").strip()
    if "terminal_app" in data:
        updates["terminal_app"] = (data["terminal_app"] or "Warp").strip()
    if "odoo_http_port" in data:
        try:
            updates["odoo_http_port"] = int(data["odoo_http_port"]) or 8069
        except (ValueError, TypeError):
            updates["odoo_http_port"] = 8069

    if updates and _save_config(updates):
        from config import _load_config
        cfg = _load_config()
        return jsonify({
            "ok": True,
            "odoo_path": cfg.get("odoo_path", "~/odoo"),
            "odoo_community_path": cfg.get("odoo_community_path", ""),
            "odoo_enterprise_path": cfg.get("odoo_enterprise_path", ""),
            "scripts_paths": cfg.get("scripts_paths", ["~/Scripts"]),
            "pyenv_root": cfg.get("pyenv_root", "~/.pyenv"),
            "pyenv_global_env": cfg.get("pyenv_global_env", ""),
            "psql_path": cfg.get("psql_path", ""),
            "terminal_app": cfg.get("terminal_app", "Warp"),
            "odoo_http_port": int(cfg.get("odoo_http_port", 8069)),
        })
    return jsonify({"ok": False, "message": "Impossible d'enregistrer"}), 500


@app.route("/api/config/scripts")
def api_config_scripts_get():
    paths = get_scripts_paths()
    return jsonify({"scripts_paths": paths})


@app.route("/api/config/scripts", methods=["POST"])
def api_config_scripts_save():
    data = request.json or {}
    paths = data.get("scripts_paths", data.get("scripts_path", []))
    if isinstance(paths, str):
        paths = [paths]
    paths = [p.strip() for p in paths if p and str(p).strip()]
    if save_scripts_paths(paths):
        return jsonify({"ok": True, "scripts_paths": paths})
    return jsonify({"ok": False, "message": "Impossible d'enregistrer"}), 500


@app.route("/api/debug/scripts")
def api_debug_scripts():
    """Pour diagnostiquer : dirs cherchés + version par db."""
    from odoo_ops import _scripts_search_dirs, _parse_script_for_db
    dbs = list_databases()
    dirs = [str(d) for d in _scripts_search_dirs() if d.exists()]
    per_db = {}
    for db in dbs:
        ver, branch = None, None
        for sd in _scripts_search_dirs():
            if sd.exists():
                v, b = _parse_script_for_db(sd, db)
                if v or b:
                    ver, branch = v, b
                    break
        per_db[db] = {"version": ver, "branch": branch}
    return jsonify({"search_dirs": dirs, "databases": per_db})


@app.route("/api/scripts-subdirs")
def api_scripts_subdirs():
    """Sous-dossiers directs du dossier scripts choisi (réglages)."""
    scripts_dir = (request.args.get("dir") or "").strip()
    if not scripts_dir:
        return jsonify({"ok": False, "subdirs": [], "message": "Dossier scripts requis"}), 400
    subdirs = list_scripts_subdirectories(scripts_dir)
    return jsonify({"ok": True, "dir": scripts_dir, "subdirs": subdirs})


@app.route("/api/databases")
def api_databases():
    """Liste les bases + indique laquelle tourne + version et sous-dossier script par base."""
    dbs = list_databases()
    running = get_running_odoo_db()
    versions = {db: get_db_version(db) for db in dbs}
    script_subdirs = {db: get_script_subdirectory(db) for db in dbs}
    return jsonify({
        "databases": dbs,
        "running": running,
        "versions": versions,
        "script_subdirs": script_subdirs,
    })


@app.route("/api/open-in-browser")
def api_open_in_browser():
    """Ouvre l'URL dans le navigateur par défaut."""
    url = request.args.get("url", f"http://localhost:{get_odoo_http_port()}")
    try:
        webbrowser.open(url)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/disconnect-all", methods=["POST"])
def api_disconnect_all():
    """Arrête tous les processus Odoo en cours et ferme le terminal si Warp."""
    count, msg = stop_all_odoo_servers()
    if count > 0 and get_terminal_app() == "Warp":
        quit_warp()
    return jsonify({"ok": count > 0, "message": msg})


@app.route("/api/delete", methods=["POST"])
def api_delete():
    """Supprime la base (dropdb), le script .sh et arrête Odoo si en cours."""
    data = request.json or {}
    db = data.get("db", "").strip()
    if not db:
        return jsonify({"ok": False, "message": "Nom de base requis"}), 400
    ok, msg = delete_db_complete(db)
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/duplicate", methods=["POST"])
def api_duplicate():
    """Duplique une base Postgres (+ filestore) sous un nouveau nom."""
    data = request.json or {}
    db = (data.get("db") or "").strip()
    new_db = (data.get("new_db") or "").strip()
    if not db or not new_db:
        return jsonify({"ok": False, "message": "Nom de base source et nouveau nom requis"}), 400
    ok, msg = duplicate_database(db, new_db)
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/disconnect", methods=["POST"])
def api_disconnect():
    """Arrête Odoo (Ctrl+C) pour la base en cours et ferme le terminal si Warp."""
    data = request.json or {}
    db = data.get("db", "").strip()
    if not db:
        return jsonify({"ok": False, "message": "Nom de base requis"}), 400
    ok, msg = stop_odoo_server(db)
    if ok and get_terminal_app() == "Warp":
        quit_warp()
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/restart", methods=["POST"])
def api_restart():
    """Redémarre Odoo pour la base demandée."""
    data = request.json or {}
    db = data.get("db", "").strip()
    if not db:
        return jsonify({"ok": False, "message": "Nom de base requis"}), 400
    ok, msg = restart_odoo_server(db, odoo_path=_path())
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/connect", methods=["POST"])
def api_connect():
    """Démarre Odoo : lance le script de la base si trouvé (version/addons gérés par le script), sinon fallback."""
    data = request.json or {}
    path = _path()
    db = data.get("db", "").strip()

    if not db:
        return jsonify({"ok": False, "message": "Nom de base requis"}), 400

    requested_env = (data.get("pyenv_env") or "").strip() or get_pyenv_global_env() or ""
    script_path = get_script_path_for_db(db)
    if script_path:
        ok, msg = run_script_for_db(script_path, odoo_root=path, pyenv_env=requested_env or None)
        return jsonify({"ok": ok, "message": msg})

    version, branch = get_db_version_and_branch(db)
    if not version:
        version = data.get("version", "19")
    if not branch:
        branch = data.get("branch", "19.0")
    if version not in ("18", "19"):
        version = "19" if "19" in (branch or "") else "18"

    pyenv = (
        (data.get("pyenv_env") or "").strip()
        or get_pyenv_global_env()
        or (f"odoo-{version}" if version in ("18", "19") else get_pyenv_for_branch(branch))
    )
    ok_switch, msg_switch = switch_branch(path, branch)
    if not ok_switch:
        return jsonify({"ok": False, "message": msg_switch})

    addons = build_addons_path(path, [])
    ok, msg = run_odoo_in_terminal(path, db, addons, pyenv)
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/db-exists")
def api_db_exists():
    db = request.args.get("db", "").strip()
    if not db:
        return jsonify({"exists": False})
    return jsonify({"exists": db_exists(db)})


def _parse_modules(raw: str) -> list[str]:
    mods = []
    for line in raw.replace(",", "\n").split("\n"):
        m = line.strip()
        if m and not m.startswith("#"):
            mods.append(m)
    return list(dict.fromkeys(mods))


def _normalize_update_mode(raw: str) -> str:
    mode = (raw or "").strip().lower()
    return "update" if mode in {"u", "update", "-u"} else "reinit"


@app.route("/api/create", methods=["POST"])
def api_create():
    """Lance la création dans Warp (comme Connect)."""
    data = request.json or {}
    path = _path()
    db = data.get("db", "").strip()
    modules_raw = data.get("modules", "website,website_sale")
    branch = data.get("branch", "19.0")
    version = data.get("version", "19")
    if version not in ("18", "19"):
        version = "19" if "19" in (branch or "") else "18"
    custom_addons_raw = data.get("custom_addons", [])
    if isinstance(custom_addons_raw, str):
        custom_addons = [os.path.expanduser(f.strip()) for f in custom_addons_raw.split(",") if f.strip()]
    else:
        custom_addons = [os.path.expanduser(str(f).strip()) for f in (custom_addons_raw or []) if str(f).strip()]
    # Compat old payload key.
    extra_legacy = [os.path.expanduser(f.strip()) for f in (data.get("extra") or "").split(",") if f.strip()]
    extra = list(dict.fromkeys(custom_addons + extra_legacy))

    if not db:
        return jsonify({"error": "Nom de base requis"}), 400

    modules = _parse_modules(modules_raw)
    update_modules_raw = data.get("update_modules", modules_raw)
    if not (update_modules_raw or "").strip():
        update_modules_raw = modules_raw
    update_modules = _parse_modules(update_modules_raw)
    update_mode = _normalize_update_mode(data.get("update_mode", "reinit"))
    pyenv = (
        (data.get("pyenv_env") or "").strip()
        or get_pyenv_global_env()
        or (get_pyenv_env(version) if version in ("18", "19") else get_pyenv_for_branch(branch))
    )
    addons, detected_addons, missing_modules = build_addons_path_for_modules(path, extra, modules)
    scripts_dir = data.get("scripts_dir", "").strip() or None
    scripts_subdir = (data.get("scripts_subdir") or "").strip() or None

    ok_switch, msg_switch = switch_branch(path, branch)
    if not ok_switch:
        return jsonify({"ok": False, "message": msg_switch}), 400

    if db_exists(db):
        clear_module_locks(db)

    ok_script, msg_script = create_launch_script(
        path,
        db,
        addons,
        pyenv,
        version=version,
        branch=branch,
        scripts_dir=scripts_dir,
        scripts_subdir=scripts_subdir,
        install_modules=modules,
        update_modules=update_modules,
        update_mode=update_mode,
    )
    ok, msg = run_odoo_create_in_terminal(path, db, addons, modules, update_modules, pyenv)
    if not ok:
        return jsonify({"ok": False, "message": msg})
    warnings = []
    if detected_addons:
        warnings.append(f"Dossiers addons auto-détectés: {', '.join(detected_addons)}")
    if missing_modules:
        warnings.append(f"Modules introuvables dans addons-path: {', '.join(missing_modules)}")
    return jsonify({
        "ok": True,
        "message": f"Création lancée dans Warp : {db}",
        "script": msg_script if ok_script else None,
        "script_error": None if ok_script else msg_script,
        "warnings": warnings,
    })


@app.route("/api/create-from-dump", methods=["POST"])
def api_create_from_dump():
    """Crée une base à partir d'un dump.sql (+ filestore optionnel) et la sanitize."""
    data = request.json or {}
    db = (data.get("db") or "").strip()
    dump_path = os.path.expanduser((data.get("dump_path") or "").strip())
    filestore_path = os.path.expanduser((data.get("filestore_path") or "").strip()) or None
    drop_existing = bool(data.get("drop_existing"))
    sanitize = data.get("sanitize", True)

    if not db:
        return jsonify({"ok": False, "message": "Nom de base requis"}), 400
    if not dump_path or not os.path.isfile(dump_path):
        return jsonify({"ok": False, "message": "Fichier dump introuvable"}), 400
    if filestore_path and not os.path.isdir(filestore_path):
        return jsonify({"ok": False, "message": "Dossier filestore introuvable"}), 400

    ok, message, log_lines = create_db_from_dump(
        db,
        dump_path,
        filestore_path=filestore_path,
        drop_existing=drop_existing,
        sanitize=bool(sanitize),
    )
    return jsonify({"ok": ok, "message": message, "log": log_lines})


@app.route("/api/script-config")
def api_script_config():
    db = request.args.get("db", "").strip()
    if not db:
        return jsonify({"ok": False, "message": "Nom de base requis"}), 400
    script_path = get_script_path_for_db(db)
    if not script_path:
        return jsonify({"ok": False, "message": "Script introuvable"}), 404
    try:
        cfg = get_script_config(script_path)
        return jsonify({"ok": True, "script": str(script_path), **cfg})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/script-config", methods=["POST"])
def api_script_config_update():
    data = request.json or {}
    db = (data.get("db") or "").strip()
    if not db:
        return jsonify({"ok": False, "message": "Nom de base requis"}), 400
    script_path = get_script_path_for_db(db)
    if not script_path:
        return jsonify({"ok": False, "message": "Script introuvable"}), 404
    addons_path = (data.get("addons_path") or "").strip()
    install_modules = _parse_modules(data.get("install_modules", ""))
    update_modules = _parse_modules(data.get("update_modules", ""))
    update_mode = _normalize_update_mode(data.get("update_mode", "reinit"))
    if not addons_path:
        return jsonify({"ok": False, "message": "addons_path requis"}), 400
    ok, msg = update_script_config(
        script_path=script_path,
        addons_path=addons_path,
        install_modules=install_modules,
        update_modules=update_modules,
        update_mode=update_mode,
    )
    return jsonify({"ok": ok, "message": msg})


def _activate_app() -> None:
    """Met la fenêtre existante au premier plan (sans en ouvrir une nouvelle)."""
    subprocess.run(
        ["osascript", "-e", 'tell application "Odoo Database Manager" to activate'],
        check=False,
    )


def _open_window_process(port: int) -> None:
    """Lance la fenêtre dans un subprocess (rumps garde la boucle principale)."""
    url = f"http://127.0.0.1:{port}"
    if getattr(sys, "frozen", False):
        app_bundle = os.path.dirname(os.path.dirname(os.path.dirname(sys.executable)))
        # En pratique c'est le mode le plus fiable pour ouvrir la GUI pywebview depuis l'instance menubar.
        subprocess.Popen(["open", "-n", "-a", app_bundle, "--args", "--window-only", url])
    else:
        base = os.path.dirname(os.path.abspath(__file__))
        subprocess.Popen([sys.executable, os.path.join(base, "app.py"), "--window-only", url])


def main():
    import webview

    port = int(os.environ.get("PORT", 5151))
    url = f"http://127.0.0.1:{port}"

    def start_server():
        app.run(host="127.0.0.1", port=port, debug=False, threaded=True, use_reloader=False)

    threading.Thread(target=start_server, daemon=True).start()
    import time
    time.sleep(1.2)

    # --window-only : subprocess (fenêtre seule)
    if "--window-only" in sys.argv:
        win_url = next((a for a in sys.argv if a.startswith("http")), url)
        webview.create_window("Odoo Database Manager", win_url, width=960, height=820, min_size=(700, 550))
        webview.start()
        return

    # Menu bar (rumps.run) + ouvre la fenêtre en subprocess
    from menubar import OdooMenubarApp
    menubar = OdooMenubarApp(port=port)
    menubar._open_app = lambda _: _activate_app()  # Ramener la fenêtre au premier plan
    _open_window_process(port)
    menubar.run()


if __name__ == "__main__":
    main()
