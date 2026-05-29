"""Logique métier : git, pyenv, base de données Odoo."""
import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


_PSQL_PATH: Optional[str] = None


def _community_path(odoo_path: Optional[str] = None) -> Path:
    """Retourne le chemin du dossier community (core/odoo)."""
    from config import get_odoo_community_path
    if odoo_path:
        return Path(odoo_path).expanduser().resolve()
    return Path(get_odoo_community_path()).expanduser().resolve()


def _enterprise_path(odoo_path: Optional[str] = None) -> Path:
    """Retourne le chemin du dossier enterprise."""
    from config import get_odoo_enterprise_path
    if odoo_path:
        return Path(odoo_path).expanduser().resolve()
    return Path(get_odoo_enterprise_path()).expanduser().resolve()


def _effective_enterprise_path() -> Optional[Path]:
    """Retourne enterprise si configuré et existant, sinon None."""
    from config import get_odoo_enterprise_path
    raw = (get_odoo_enterprise_path() or "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser().resolve()
    return p if p.is_dir() else None


def _env_with_path() -> dict:
    """Environnement avec PATH étendu pour l'app (psql, git, etc.)."""
    extra = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
    if os.name == "posix":
        try:
            _res = subprocess.run(
                ["/bin/zsh", "-l", "-c", "echo $PATH"],
                capture_output=True, text=True, timeout=2,
                env={**os.environ, "PATH": extra},
            )
            if _res.returncode == 0 and _res.stdout.strip():
                extra = _res.stdout.strip() + ":" + extra
        except Exception:
            pass
    e = os.environ.copy()
    e["PATH"] = extra + (":" + e.get("PATH", "") if e.get("PATH") else "")
    return e


def detect_psql_path() -> str:
    """Détecte le chemin psql (sans utiliser la config personnalisée)."""
    import shutil
    env = _env_with_path()
    found = shutil.which("psql", path=env["PATH"])
    if found:
        return found
    for path in ["/opt/homebrew/bin/psql", "/usr/local/bin/psql", "/usr/bin/psql"]:
        if Path(path).exists():
            return path
    pg_app = Path("/Applications/Postgres.app/Contents/Versions")
    if pg_app.exists():
        for v in sorted(pg_app.iterdir(), reverse=True):
            p = v / "bin" / "psql"
            if p.exists():
                return str(p)
    return ""


def _find_psql() -> str:
    """Retourne le chemin de psql (pour l'app .app)."""
    global _PSQL_PATH
    from config import get_psql_path
    custom = get_psql_path()
    if custom and Path(custom).exists():
        return custom
    if _PSQL_PATH:
        return _PSQL_PATH
    import shutil
    env = _env_with_path()
    found = shutil.which("psql", path=env["PATH"])
    if found:
        _PSQL_PATH = found
        return found
    for path in [
        "/opt/homebrew/bin/psql",
        "/usr/local/bin/psql",
        "/usr/bin/psql",
    ]:
        if Path(path).exists():
            _PSQL_PATH = path
            return path
    pg_app = Path("/Applications/Postgres.app/Contents/Versions")
    if pg_app.exists():
        for v in sorted(pg_app.iterdir(), reverse=True):
            p = v / "bin" / "psql"
            if p.exists():
                _PSQL_PATH = str(p)
                return _PSQL_PATH
    _PSQL_PATH = "psql"
    return "psql"


def run_cmd(
    cmd: list[str],
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
    capture: bool = True,
) -> tuple[int, str, str]:
    """Exécute une commande et retourne (exit_code, stdout, stderr)."""
    full_env = _env_with_path()
    if env:
        full_env.update(env)
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            env=full_env,
            capture_output=capture,
            text=True,
            timeout=300,
        )
        return result.returncode, result.stdout or "", result.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", "Timeout"
    except FileNotFoundError:
        return -1, "", f"Commande non trouvée: {cmd[0]}"
    except Exception as e:
        return -1, "", str(e)


def get_pyenv_env(version: str) -> Optional[str]:
    """Retourne le nom de l'environnement pyenv pour la version Odoo."""
    from config import VERSION_CONFIG
    cfg = VERSION_CONFIG.get(version)
    if not cfg:
        return None
    return cfg.get("pyenv")


def get_git_branches(repo_path: str) -> list[str]:
    """Récupère la liste des branches locales d'un dépôt git."""
    code, out, _ = run_cmd(["git", "branch", "-a", "--format=%(refname:short)"], cwd=repo_path)
    if code != 0:
        return []
    branches = [b.strip() for b in out.strip().split("\n") if b.strip()]
    seen = set()
    result = []
    for b in branches:
        # Simplifier remote/origin/X -> X
        short = b.replace("remotes/origin/", "") if "remotes/origin/" in b else b
        if short not in seen:
            seen.add(short)
            result.append(short)
    return sorted(result, key=lambda x: (x.startswith("saas"), x))


def get_current_branch(repo_path: str) -> Optional[str]:
    """Branche actuelle du dépôt."""
    code, out, _ = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)
    if code != 0:
        return None
    return out.strip()


def switch_branch(odoo_path: str, branch: str) -> tuple[bool, str]:
    """Passe core et enterprise sur la branche donnée. Retourne (ok, message)."""
    core = _community_path(odoo_path)
    enterprise = _effective_enterprise_path()
    errors = []
    repos = [("core", core)] + ([("enterprise", enterprise)] if enterprise else [])
    for name, path in repos:
        if not path.is_dir():
            continue
        code, out, err = run_cmd(["git", "checkout", branch], cwd=str(path))
        if code != 0:
            errors.append(f"{name}: {err or out}")
    if errors:
        return False, "Erreurs:\n" + "\n".join(errors)
    return True, f"core et enterprise passés sur {branch}"


def check_branches_match(odoo_path: str, expected_branch: str) -> tuple[bool, str]:
    """
    Vérifie que core et enterprise sont sur la branche attendue.
    Retourne (ok, message).
    """
    core = _community_path(odoo_path)
    enterprise = _effective_enterprise_path()
    errors = []
    repos = [("core", core)] + ([("enterprise", enterprise)] if enterprise else [])
    for name, path in repos:
        if not path.is_dir():
            continue
        curr = get_current_branch(str(path))
        if curr != expected_branch:
            errors.append(f"{name}: {curr or '?'} (attendu: {expected_branch})")
    if errors:
        return False, "Branches incohérentes:\n" + "\n".join(errors)
    return True, f"OK - core et enterprise sur {expected_branch}"


def check_repos_up_to_date(odoo_path: str) -> tuple[bool, str]:
    """Vérifie si core/enterprise sont à jour par rapport à origin."""
    core = _community_path(odoo_path)
    enterprise = _effective_enterprise_path()
    repos = [("core", core)] + ([("enterprise", enterprise)] if enterprise else [])
    lines: list[str] = []
    all_ok = True
    for name, path in repos:
        if not path or not path.is_dir():
            continue
        fetch_code, _, fetch_err = run_cmd(["git", "fetch", "origin"], cwd=str(path))
        if fetch_code != 0:
            all_ok = False
            lines.append(f"{name}: fetch impossible ({fetch_err.strip() or 'erreur'})")
            continue
        code, out, err = run_cmd(["git", "status", "-sb"], cwd=str(path))
        if code != 0:
            all_ok = False
            lines.append(f"{name}: status impossible ({(err or out).strip()})")
            continue
        status_line = (out.splitlines()[0] if out.splitlines() else "").strip()
        if "behind " in status_line:
            all_ok = False
            lines.append(f"{name}: en retard ({status_line})")
        elif "ahead " in status_line:
            lines.append(f"{name}: en avance ({status_line})")
        else:
            lines.append(f"{name}: à jour ({status_line or 'OK'})")
    return all_ok, "\n".join(lines) if lines else "Aucun dépôt configuré"


def get_repos_behind_counts(odoo_path: str) -> tuple[bool, dict]:
    """Retourne le nombre de commits de retard pour core/enterprise."""
    core = _community_path(odoo_path)
    enterprise = _effective_enterprise_path()
    repos = [("core", core)] + ([("enterprise", enterprise)] if enterprise else [])
    counts: dict[str, int] = {}
    all_ok = True
    for name, path in repos:
        if not path or not path.is_dir():
            counts[name] = 0
            continue
        fetch_code, _, _ = run_cmd(["git", "fetch", "origin"], cwd=str(path))
        if fetch_code != 0:
            all_ok = False
            counts[name] = 0
            continue
        branch_code, branch_out, _ = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(path))
        if branch_code != 0:
            all_ok = False
            counts[name] = 0
            continue
        branch = branch_out.strip()
        count_code, count_out, _ = run_cmd(["git", "rev-list", "--count", f"HEAD..origin/{branch}"], cwd=str(path))
        if count_code != 0:
            all_ok = False
            counts[name] = 0
            continue
        try:
            counts[name] = int((count_out or "0").strip() or "0")
        except ValueError:
            counts[name] = 0
            all_ok = False
    if "enterprise" not in counts:
        counts["enterprise"] = 0
    return all_ok, counts


def pull_core_enterprise(odoo_path: str) -> tuple[bool, str]:
    """Exécute git pull sur core et enterprise (si configuré)."""
    core = _community_path(odoo_path)
    enterprise = _effective_enterprise_path()
    repos = [("core", core)] + ([("enterprise", enterprise)] if enterprise else [])
    lines: list[str] = []
    all_ok = True
    for name, path in repos:
        if not path or not path.is_dir():
            continue
        code, out, err = run_cmd(["git", "pull", "--ff-only"], cwd=str(path))
        if code != 0:
            all_ok = False
            lines.append(f"{name}: échec pull ({(err or out).strip()})")
        else:
            result = (out or "").strip().splitlines()
            lines.append(f"{name}: {result[-1] if result else 'pull OK'}")
    return all_ok, "\n".join(lines) if lines else "Aucun dépôt configuré"


def db_exists(db_name: str) -> bool:
    """Vérifie si la base PostgreSQL existe."""
    psql = _find_psql()
    code, out, _ = run_cmd([psql, "-lqt"])
    if code != 0:
        return False
    dbs = [line.split("|")[0].strip() for line in out.strip().split("\n")]
    return db_name in dbs


def _script_matches_db(text: str, db_name: str) -> bool:
    """Vérifie si le script concerne cette base (DB= ou -d)."""
    esc = re.escape(db_name)
    return bool(
        re.search(rf"DB\s*=\s*['\"]?{esc}", text, re.IGNORECASE | re.MULTILINE)
        or re.search(rf"-d\s+['\"]?{esc}", text)
    )


def _script_filename_matches_db(script_path: Path, db_name: str) -> bool:
    """Vérifie si le nom du fichier correspond : odoo-{db}.sh ou {db}.sh."""
    stem = script_path.stem
    return stem == db_name or stem == f"odoo-{db_name}"


def _find_script_for_db(scripts_dir: Path, db_name: str) -> Optional[Path]:
    """Retourne le chemin du script .sh pour cette base, ou None."""
    for script in scripts_dir.rglob("*.sh"):
        try:
            text = script.read_text(encoding="utf-8")
            if _script_filename_matches_db(script, db_name) or _script_matches_db(text, db_name):
                return script
        except Exception:
            pass
    return None


def get_script_path_for_db(db_name: str) -> Optional[Path]:
    """Retourne le chemin du script pour la base (ex: concert.sh, odoo-mydb.sh)."""
    for scripts_dir in _scripts_search_dirs():
        if scripts_dir.exists():
            found = _find_script_for_db(scripts_dir, db_name)
            if found:
                return found
    return None


def is_script_industry(db_name: str) -> bool:
    """True si le script de la base est dans un dossier inc (ex: /scripts/inc)."""
    path = get_script_path_for_db(db_name)
    if not path:
        return False
    return "inc" in path.parts


def quit_warp() -> bool:
    """Ferme Warp via AppleScript. Retourne True si réussi."""
    try:
        res = subprocess.run(
            ["/usr/bin/osascript", "-e", 'tell application "Warp" to quit'],
            capture_output=True,
            timeout=3,
        )
        return res.returncode == 0
    except Exception:
        return False


def _run_command_via_applescript(command: str) -> tuple[bool, str]:
    """Lance une commande dans Terminal.app via AppleScript — pas de fichier temporaire."""
    try:
        escaped = command.replace("\\", "\\\\").replace('"', '\\"')
        script = f'tell application "Terminal" to do script "{escaped}"'
        res = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0:
            return True, "Script lancé dans Terminal"
        return False, res.stderr or "Échec osascript"
    except Exception as e:
        return False, str(e)


def _run_command_via_warp_launch(command: str | list[str], cwd: Optional[str] = None) -> tuple[bool, str]:
    """
    Lance une commande dans Warp via Launch Configuration.
    Écrit dans ~/.warp/launch_configurations/dbmanager.yaml (réutilisé, pas de temp).
    """
    try:
        launch_dir = Path.home() / ".warp" / "launch_configurations"
        launch_dir.mkdir(parents=True, exist_ok=True)
        config_path = launch_dir / "dbmanager.yaml"
        cwd_abs = str(Path(cwd or "~").expanduser().resolve()) if cwd else str(Path.home())
        commands = command if isinstance(command, list) else [command]
        cmds_yaml = []
        for cmd in commands:
            cmd_escaped = cmd.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
            cmds_yaml.append(f'            - exec: "{cmd_escaped}"')
        commands_yaml = "\n".join(cmds_yaml)
        yaml_content = f'''---
name: dbmanager
windows:
  - tabs:
      - title: Odoo
        layout:
          cwd: "{cwd_abs}"
          commands:
{commands_yaml}
'''
        config_path.write_text(yaml_content, encoding="utf-8")
        res = subprocess.run(
            ["/usr/bin/open", "warp://launch/dbmanager"],
            capture_output=True,
            timeout=5,
        )
        if res.returncode == 0:
            return True, "Script lancé dans Warp"
        return False, res.stderr.decode() or "Échec open warp://"
    except Exception as e:
        return False, str(e)


def _run_command_via_warp_tab(command: str) -> tuple[bool, str]:
    """
    Lance une commande dans un nouvel onglet Warp si Warp est déjà ouvert.
    Nécessite les permissions d'accessibilité macOS pour System Events.
    """
    try:
        escaped = command.replace("\\", "\\\\").replace('"', '\\"')
        script = f'''
set warpRunning to false
tell application "System Events"
    set warpRunning to exists (process "Warp")
end tell
if warpRunning then
    tell application "Warp" to activate
    tell application "System Events"
        keystroke "t" using command down
        keystroke "{escaped}"
        key code 36
    end tell
    return "ok"
else
    return "not_running"
end if
'''
        res = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0 and (res.stdout or "").strip() == "ok":
            return True, "Script lancé dans un onglet Warp"
        return False, (res.stdout or res.stderr or "Warp non ouvert").strip()
    except Exception as e:
        return False, str(e)


def _run_command_via_file(command: str, target: str = "Warp") -> tuple[bool, str]:
    """
    Fallback: crée un .command temporaire et l'ouvre avec l'app demandée.
    Warp et Terminal exécutent automatiquement les .command à l'ouverture.
    """
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".command", delete=False, dir=tempfile.gettempdir()
        ) as f:
            cmd_path = f.name
            f.write("#!/bin/bash\ntrap 'cd; rm -f \"$0\"' EXIT\n" + command + "\n")
        os.chmod(cmd_path, 0o755)
        res = subprocess.run(
            ["/usr/bin/open", "-a", target, cmd_path],
            env=_env_with_path(),
            capture_output=True,
            timeout=5,
        )
        if res.returncode == 0:
            return True, f"Script lancé dans {target}"
        return False, res.stderr.decode() or f"Échec open -a {target}"
    except Exception as e:
        return False, str(e)


def _launch_in_terminal(inner: str | list[str], cwd: Optional[str], script_name: Optional[str] = None) -> tuple[bool, str]:
    """Lance une commande dans le terminal configuré (Warp, Terminal, iTerm2...)."""
    from config import get_terminal_app
    app_raw = (get_terminal_app() or "Warp").strip()
    app_l = app_raw.lower().rstrip("/")
    if app_l.endswith(".app"):
        app_l = Path(app_l).stem

    if "warp" in app_l:
        app = "Warp"
        # Le mode "nouvel onglet" envoie une seule ligne : pour plusieurs commandes,
        # on force la launch config Warp (multi exec séparés).
        if not isinstance(inner, list):
            ok, _ = _run_command_via_warp_tab(inner)
            if ok:
                return True, f"Script lancé dans un onglet Warp" + (f" : {script_name}" if script_name else "")
        ok, _ = _run_command_via_warp_launch(inner, cwd)
        if ok:
            return True, f"Script lancé dans Warp" + (f" : {script_name}" if script_name else "")
        # Warp récent peut ouvrir le .command comme un fichier au lieu de l'exécuter.
        # On évite donc ce fallback pour garantir un lancement de commande.
    elif "terminal" == app_l:
        app = "Terminal"
        term_inner = " && ".join(inner) if isinstance(inner, list) else inner
        ok, _ = _run_command_via_applescript(term_inner)
        if ok:
            return True, f"Script lancé dans Terminal" + (f" : {script_name}" if script_name else "")
        ok, _ = _run_command_via_file(term_inner, "Terminal")
        if ok:
            return True, f"Script lancé dans Terminal" + (f" : {script_name}" if script_name else "")
    else:
        app = app_raw
        file_inner = " && ".join(inner) if isinstance(inner, list) else inner
        ok, _ = _run_command_via_file(file_inner, app)
        if ok:
            return True, f"Script lancé dans {app}" + (f" : {script_name}" if script_name else "")
    if app == "Warp":
        term_inner = " && ".join(inner) if isinstance(inner, list) else inner
        ok, _ = _run_command_via_applescript(term_inner)
        if ok:
            return True, f"Script lancé dans Terminal (fallback)"
    elif app == "Terminal":
        ok, _ = _run_command_via_warp_launch(inner, cwd)
        if ok:
            return True, f"Script lancé dans Warp (fallback)"
    final_inner = " && ".join(inner) if isinstance(inner, list) else inner
    ok, _ = _run_command_via_file(final_inner, "Terminal")
    if ok:
        return True, f"Script lancé dans Terminal (fallback)"
    return False, f"Impossible de lancer dans {app}"


def run_script_for_db(
    script_path: Path,
    odoo_root: Optional[str] = None,
    pyenv_env: Optional[str] = None,
) -> tuple[bool, str]:
    """Lance le script dans le terminal configuré."""
    def _scripts_cwd(root_or_community: str) -> str:
        p = Path(root_or_community).expanduser().resolve()
        # Si on reçoit le chemin community (.../core), lancer depuis la racine Odoo (...).
        if p.name in {"core", "community"}:
            return str(p.parent)
        return str(p)

    def _resolve_pyenv_python(env_name: str) -> Optional[str]:
        try:
            from config import get_pyenv_root
            pyenv_root = Path(get_pyenv_root()).expanduser().resolve()
            py = pyenv_root / "versions" / env_name / "bin" / "python3"
            return str(py) if py.exists() else None
        except Exception:
            return None

    def _ensure_pyenv_override_support(path: Path) -> None:
        """
        Rend les anciens scripts compatibles avec l'override PYENV_ENV.
        Legacy: PYENV_ENV="odoo-${VERSION}"
        New:    PYENV_ENV="${PYENV_ENV:-odoo-${VERSION}}"
        """
        try:
            txt = path.read_text(encoding="utf-8")
        except Exception:
            return
        old = 'PYENV_ENV="odoo-${VERSION}"'
        new = 'PYENV_ENV="${PYENV_ENV:-${PYENV_VERSION:-odoo-${VERSION}}}"'
        modern = 'PYENV_ENV="${PYENV_ENV:-odoo-${VERSION}}"'
        old_py = 'PYTHON="${PYENV_ROOT}/versions/${PYENV_ENV}/bin/python3"'
        new_py = 'PYTHON="${PYTHON:-${PYENV_ROOT}/versions/${PYENV_ENV}/bin/python3}"'
        guard_line = "unset PYENV_VERSION VIRTUAL_ENV"
        export_line = 'export PYENV_VERSION="${PYENV_ENV}"'
        changed = False
        if old in txt:
            txt = txt.replace(old, new)
            changed = True
        elif modern in txt:
            txt = txt.replace(modern, new)
            changed = True
        if old_py in txt:
            txt = txt.replace(old_py, new_py)
            changed = True
        if guard_line not in txt:
            txt = txt.replace("set -euo pipefail\n", f"set -euo pipefail\n{guard_line}\n", 1)
            changed = True
        if export_line not in txt and 'PYENV_ENV="${PYENV_ENV:-${PYENV_VERSION:-odoo-${VERSION}}}"' in txt:
            txt = txt.replace(
                'PYENV_ENV="${PYENV_ENV:-${PYENV_VERSION:-odoo-${VERSION}}}"\n',
                'PYENV_ENV="${PYENV_ENV:-${PYENV_VERSION:-odoo-${VERSION}}}"\nexport PYENV_VERSION="${PYENV_ENV}"\n',
                1,
            )
            changed = True
        if not changed:
            return
        try:
            path.write_text(txt, encoding="utf-8")
        except Exception:
            pass

    from config import get_odoo_community_path
    cwd = _scripts_cwd(odoo_root or get_odoo_community_path())
    script_abs = str(script_path.resolve())
    _ensure_pyenv_override_support(script_path)
    clean_env = "unset VIRTUAL_ENV CONDA_PREFIX PYTHONHOME PYTHONPATH"
    if pyenv_env:
        env_cmd = (
            f"cd {shlex.quote(cwd)} && "
            "unset PYENV_VERSION VIRTUAL_ENV PYTHONHOME PYTHONPATH && "
            "eval \"$(pyenv init -)\" >/dev/null 2>&1 || true && "
            "eval \"$(pyenv virtualenv-init -)\" >/dev/null 2>&1 || true && "
            f"pyenv activate {shlex.quote(pyenv_env)}"
        )
        wait_cmd = "sleep 0.8"
        run_cmd = (
            f"cd {shlex.quote(cwd)} && "
            "unset PYENV_VERSION VIRTUAL_ENV PYTHONHOME PYTHONPATH && "
            f"export PYENV_VERSION={shlex.quote(pyenv_env)} && "
            f"export PYENV_ENV={shlex.quote(pyenv_env)} && "
            f"/bin/bash {shlex.quote(script_abs)}"
        )
        inner = [env_cmd, wait_cmd, run_cmd]
    else:
        inner = f"cd {shlex.quote(cwd)} && {clean_env} && /bin/bash {shlex.quote(script_abs)}"
    return _launch_in_terminal(inner, cwd, script_path.name)


def _open_in_terminal(content: str, cwd: Optional[str] = None) -> tuple[bool, str]:
    """Ouvre une commande dans le terminal configuré."""
    from config import get_terminal_app
    ok, _ = _launch_in_terminal(content, cwd)
    app = get_terminal_app() or "Warp"
    return ok, f"Ouvert dans {app}" if ok else "Impossible de lancer"


def run_odoo_create_in_terminal(
    odoo_path: str,
    db_name: str,
    addons_path: str,
    install_modules: list[str],
    update_modules: list[str],
    pyenv_env: Optional[str],
) -> tuple[bool, str]:
    """Lance la création/mise à jour de la DB dans Warp (comme Connect)."""
    core_path = _community_path(odoo_path)
    odoo_bin = core_path / "odoo-bin"
    if not odoo_bin.exists():
        return False, f"odoo-bin introuvable: {odoo_bin}"
    python_exe = "python3"
    if pyenv_env:
        from config import get_pyenv_root
        pyenv_root = get_pyenv_root() or os.environ.get("PYENV_ROOT", os.path.expanduser("~/.pyenv"))
        venv_python = Path(pyenv_root) / "versions" / pyenv_env / "bin" / "python3"
        if venv_python.exists():
            python_exe = str(venv_python)
    import shlex
    db_filter = f"^{db_name}$"
    from config import get_odoo_http_port
    port = get_odoo_http_port()
    base = f"cd {shlex.quote(str(core_path))} && {python_exe} odoo-bin --addons-path={shlex.quote(addons_path)} --limit-memory-hard 0 --db-filter={shlex.quote(db_filter)} -d {shlex.quote(db_name)} --without-demo=all --dev=xml --http-port={port}"
    if db_exists(db_name):
        base += f" --reinit {','.join(update_modules)}" if update_modules else " --reinit"
    else:
        base += f" -i {','.join(install_modules)}" if install_modules else ""
    return _open_in_terminal(base, cwd=str(core_path))


def run_odoo_in_terminal(
    odoo_path: str,
    db_name: str,
    addons_path: str,
    pyenv_env: Optional[str],
) -> tuple[bool, str]:
    """Lance Odoo dans Warp/Terminal (quand aucun script n'existe)."""
    core_path = _community_path(odoo_path)
    odoo_bin = core_path / "odoo-bin"
    if not odoo_bin.exists():
        return False, f"odoo-bin introuvable: {odoo_bin}"
    python_exe = "python3"
    if pyenv_env:
        from config import get_pyenv_root
        pyenv_root = get_pyenv_root() or os.environ.get("PYENV_ROOT", os.path.expanduser("~/.pyenv"))
        venv_python = Path(pyenv_root) / "versions" / pyenv_env / "bin" / "python3"
        if venv_python.exists():
            python_exe = str(venv_python)
    import shlex
    from config import get_odoo_http_port
    db_filter = f"^{db_name}$"
    port = get_odoo_http_port()
    cmd = f"cd {shlex.quote(str(core_path))} && {python_exe} odoo-bin --addons-path={shlex.quote(addons_path)} --limit-memory-hard 0 --db-filter={shlex.quote(db_filter)} -d {shlex.quote(db_name)} --without-demo=all --http-port={port}"
    return _open_in_terminal(cmd, cwd=str(core_path))


def _parse_script_for_db(scripts_dir: Path, db_name: str) -> tuple[Optional[str], Optional[str]]:
    """Parse les scripts .sh pour trouver VERSION et BRANCH. Retourne (version, branch)."""
    for script in scripts_dir.rglob("*.sh"):
        try:
            text = script.read_text(encoding="utf-8")
            if not _script_filename_matches_db(script, db_name) and not _script_matches_db(text, db_name):
                continue
            version, branch = None, None
            for line in text.replace("\r", "\n").split("\n")[:30]:
                line = line.strip()
                m = re.search(r"[vV]ERSION\s*=\s*['\"]?(\d+)['\"]?", line, re.IGNORECASE)
                if m:
                    version = m.group(1)
                m = re.search(r"[bB]RANCH\s*=\s*['\"]?([\w.-]+)['\"]?", line, re.IGNORECASE)
                if m:
                    branch = m.group(1).strip()
            if version or branch:
                if not version and branch:
                    version = "19" if ("19" in branch or branch.startswith("saas-19")) else "18"
                return (version or None, branch)
        except Exception:
            pass
    return (None, None)


def _scripts_search_dirs() -> list[Path]:
    """Dossiers où chercher les scripts : config + ~/odoo/scripts + projet/scripts + à côté du .app."""
    from config import get_odoo_path, get_scripts_paths
    import sys
    dirs: list[Path] = []
    seen: set[str] = set()

    def _add(p: Path) -> None:
        sp = str(p.resolve())
        if p.exists() and sp not in seen:
            dirs.append(p)
            seen.add(sp)

    for p in get_scripts_paths():
        _add(Path(p))
    # Fallback : ~/odoo/scripts (structure courante)
    odoo_scripts = Path(get_odoo_path()) / "scripts"
    _add(odoo_scripts)
    project_scripts = Path(__file__).resolve().parent / "scripts"
    _add(project_scripts)
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        for parent in exe.parents:
            _add(parent / "scripts")
            if parent.name.endswith(".app"):
                _add(parent.parent / "scripts")
                _add(parent.parent.parent / "scripts")
                break
    return dirs


def get_db_version(db_name: str) -> Optional[str]:
    """Retourne la version (18/19) pour une base en parsant les scripts .sh."""
    for scripts_dir in _scripts_search_dirs():
        if scripts_dir.exists():
            version, _ = _parse_script_for_db(scripts_dir, db_name)
            if version:
                return version
    return None


def get_db_version_and_branch(db_name: str) -> tuple[Optional[str], Optional[str]]:
    """Retourne (version, branch) pour une base. Version 19/saas-19 -> odoo-19, 18/saas-18 -> odoo-18."""
    for scripts_dir in _scripts_search_dirs():
        if scripts_dir.exists():
            version, branch = _parse_script_for_db(scripts_dir, db_name)
            if version or branch:
                if not version and branch:
                    version = "19" if ("19" in branch or branch.startswith("saas-19")) else "18"
                if version and not branch:
                    branch = "19.0" if version == "19" else "18.0"
                return (version, branch)
    return (None, None)


def list_databases() -> list[str]:
    """Liste toutes les bases PostgreSQL (hors template0, template1, postgres)."""
    psql = _find_psql()
    code, out, _ = run_cmd([psql, "-lqt"])
    if code != 0:
        return []
    exclude = {"template0", "template1", "postgres"}
    result = []
    for line in out.strip().split("\n"):
        name = line.split("|")[0].strip()
        if name and name not in exclude:
            result.append(name)
    return sorted(result)


def get_all_running_odoo_dbs() -> list[str]:
    """Retourne la liste des bases dont Odoo tourne actuellement."""
    result: list[str] = []
    try:
        import psutil
        for proc in psutil.process_iter(["cmdline"]):
            try:
                cmdline = proc.info.get("cmdline") or []
                cmdstr = " ".join(cmdline) if isinstance(cmdline, list) else str(cmdline)
                if "odoo-bin" not in cmdstr and "odoo.py" not in cmdstr:
                    continue
                m = re.search(r"[\s-]-d\s+(\S+)", cmdstr)
                if not m:
                    m = re.search(r"[\s-]-database[= ](\S+)", cmdstr)
                if m and m.group(1) not in result:
                    result.append(m.group(1))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except (ImportError, PermissionError):
        code, out, _ = run_cmd(["/usr/bin/ps", "-A", "-o", "pid,command"])
        if code != 0:
            return sorted(result)
        for line in out.split("\n"):
            if "odoo-bin" not in line and "odoo.py" not in line:
                continue
            m = re.search(r"[\s-]-d\s+(\S+)", line) or re.search(r"[\s-]-database[= ](\S+)", line)
            if m and m.group(1) not in result:
                result.append(m.group(1))
    return sorted(result)


def get_running_odoo_db() -> Optional[str]:
    """Retourne le nom de la base utilisée par Odoo s'il tourne, sinon None."""
    # psutil fonctionne depuis l'app .app (ps peut échouer en sandbox)
    try:
        import psutil
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmdline = proc.info.get("cmdline") or []
                cmdstr = " ".join(cmdline) if isinstance(cmdline, list) else str(cmdline)
                if "odoo-bin" not in cmdstr and "odoo.py" not in cmdstr:
                    continue
                m = re.search(r"[\s-]-d\s+(\S+)", cmdstr)
                if m:
                    return m.group(1)
                m = re.search(r"[\s-]-database[= ](\S+)", cmdstr)
                if m:
                    return m.group(1)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except (ImportError, PermissionError):
        pass
    # Fallback ps (peut échouer depuis .app)
    code, out, _ = run_cmd(["/usr/bin/ps", "-A", "-o", "pid,command"])
    if code != 0:
        return None
    for line in out.split("\n"):
        if "odoo-bin" in line or "odoo.py" in line:
            m = re.search(r"[\s-]-d\s+(\S+)", line)
            if m:
                return m.group(1)
            m = re.search(r"[\s-]-database[= ](\S+)", line)
            if m:
                return m.group(1)
    return None


def stop_odoo_server(db_name: str) -> tuple[bool, str]:
    """Arrête Odoo pour la base (équivalent Ctrl+C). Retourne (ok, message)."""
    pids: list[int] = []
    try:
        import psutil
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmdline = proc.info.get("cmdline") or []
                cmdstr = " ".join(cmdline) if isinstance(cmdline, list) else str(cmdline)
                if ("odoo-bin" not in cmdstr and "odoo.py" not in cmdstr) or db_name not in cmdstr:
                    continue
                m = re.search(r"[\s-]-d\s+(\S+)", cmdstr) or re.search(r"[\s-]-database[= ](\S+)", cmdstr)
                if m and m.group(1) == db_name:
                    pids.append(proc.info["pid"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except (ImportError, PermissionError):
        code, out, _ = run_cmd(["/usr/bin/ps", "-A", "-o", "pid,command"])
        if code != 0:
            return False, "Impossible de lister les processus"
        for line in out.split("\n"):
            if ("odoo-bin" not in line and "odoo.py" not in line) or db_name not in line:
                continue
            m = re.search(r"[\s-]-d\s+(\S+)", line) or re.search(r"[\s-]-database[= ](\S+)", line)
            if m and m.group(1) == db_name:
                pid_m = re.match(r"^\s*(\d+)", line)
                if pid_m:
                    pids.append(int(pid_m.group(1)))
    if not pids:
        return False, f"Aucun processus Odoo pour {db_name}"
    import signal
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError) as e:
            return False, str(e)
    return True, f"Odoo arrêté pour {db_name}"


def restart_odoo_server(db_name: str, odoo_path: Optional[str] = None) -> tuple[bool, str]:
    """
    Redémarre Odoo pour une base : stop immédiat puis relance sans attente artificielle.
    Privilégie le script .sh de la base, sinon fallback sur lancement direct.
    """
    from config import get_odoo_community_path, get_pyenv_for_branch, get_pyenv_global_env

    root = odoo_path or get_odoo_community_path()
    ok_stop, msg_stop = stop_odoo_server(db_name)
    if not ok_stop and not msg_stop.startswith("Aucun processus Odoo pour "):
        return False, msg_stop

    script_path = get_script_path_for_db(db_name)
    forced_env = get_pyenv_global_env() or None
    if script_path:
        ok, msg = run_script_for_db(script_path, odoo_root=root, pyenv_env=forced_env)
        if ok:
            return True, f"{db_name} relancée (script)"
        return False, msg

    version, branch = get_db_version_and_branch(db_name)
    version = version or "19"
    branch = branch or ("19.0" if version == "19" else "18.0")

    pyenv = get_pyenv_global_env() or (f"odoo-{version}" if version in ("18", "19") else get_pyenv_for_branch(branch))
    ok_switch, msg_switch = switch_branch(root, branch)
    if not ok_switch:
        return False, msg_switch

    addons = build_addons_path(root, [])
    ok_run, msg_run = run_odoo_in_terminal(root, db_name, addons, pyenv)
    if ok_run:
        return True, f"{db_name} relancée"
    return False, msg_run


def _find_dropdb() -> str:
    """Retourne le chemin de dropdb (même répertoire que psql)."""
    psql = _find_psql()
    if "/" in psql:
        d = Path(psql).parent / "dropdb"
        if d.exists():
            return str(d)
    return "dropdb"


def delete_db_complete(db_name: str) -> tuple[bool, str]:
    """Supprime la base (dropdb), le script .sh et arrête Odoo si en cours."""
    stop_odoo_server(db_name)
    dropdb = _find_dropdb()
    code, out, err = run_cmd([dropdb, db_name])
    combined = (out + " " + err).lower()
    if code != 0 and "does not exist" not in combined and "n'existe pas" not in combined:
        return False, f"dropdb: {err or out}"
    script_path = get_script_path_for_db(db_name)
    if script_path and script_path.exists():
        try:
            script_path.unlink()
        except OSError as e:
            return False, f"Script non supprimé: {e}"
    return True, f"{db_name} supprimée (DB + script)"


def stop_all_odoo_servers() -> tuple[int, str]:
    """Arrête tous les processus Odoo. Retourne (nb_arrêtés, message)."""
    pids: list[int] = []
    try:
        import psutil
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmdline = proc.info.get("cmdline") or []
                cmdstr = " ".join(cmdline) if isinstance(cmdline, list) else str(cmdline)
                if "odoo-bin" in cmdstr or "odoo.py" in cmdstr:
                    pids.append(proc.info["pid"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except (ImportError, PermissionError):
        code, out, _ = run_cmd(["/usr/bin/ps", "-A", "-o", "pid,command"])
        if code != 0:
            return 0, "Impossible de lister les processus"
        for line in out.split("\n"):
            if "odoo-bin" in line or "odoo.py" in line:
                pid_m = re.match(r"^\s*(\d+)", line)
                if pid_m:
                    pids.append(int(pid_m.group(1)))
    if not pids:
        return 0, "Aucun processus Odoo en cours"
    import signal
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    return len(pids), f"{len(pids)} processus Odoo arrêté(s)"


def start_odoo_server(
    odoo_path: str,
    db_name: str,
    addons_path: str,
    pyenv_env: Optional[str],
) -> tuple[bool, str]:
    """
    Démarre Odoo en mode serveur (en arrière-plan) pour la base donnée.
    Retourne (ok, message). Le serveur écoute sur 8069.
    """
    core_path = _community_path(odoo_path)
    odoo_bin = core_path / "odoo-bin"
    if not odoo_bin.exists():
        return False, f"odoo-bin introuvable: {odoo_bin}"

    from config import get_odoo_http_port
    port = get_odoo_http_port()
    base_cmd = [
        "python3", str(odoo_bin),
        "--addons-path", addons_path,
        "--limit-memory-hard", "0",
        "--db-filter", f"^{db_name}$",
        "-d", db_name,
        "--without-demo=all",
        "--http-port", str(port),
    ]

    env = _env_with_path()
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    if pyenv_env:
        from config import get_pyenv_root
        pyenv_root = get_pyenv_root() or os.environ.get("PYENV_ROOT", os.path.expanduser("~/.pyenv"))
        venv_python = Path(pyenv_root) / "versions" / pyenv_env / "bin" / "python3"
        if venv_python.exists():
            base_cmd[0] = str(venv_python)

    try:
        subprocess.Popen(
            base_cmd,
            cwd=str(core_path),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True, f"Odoo démarré pour {db_name} → http://localhost:{port}"
    except Exception as e:
        return False, str(e)


def create_launch_script(
    odoo_path: str,
    db_name: str,
    addons_path: str,
    pyenv_env: Optional[str],
    version: str = "19",
    branch: str = "19.0",
    scripts_dir: Optional[str] = None,
    industry: bool = False,
    install_modules: Optional[list[str]] = None,
    update_modules: Optional[list[str]] = None,
    update_mode: str = "reinit",
) -> tuple[bool, str]:
    """
    Crée un script shell pour lancer Odoo sur cette base.
    Si industry=True, le script est rangé dans scripts_dir/inc.
    Retourne (ok, chemin_du_script ou message d'erreur).
    """
    import os as _os
    from config import get_scripts_paths as _get_paths, get_odoo_http_port
    _odoo = Path(odoo_path).expanduser().resolve()
    if scripts_dir:
        base_path = Path(_os.path.expanduser(scripts_dir))
    else:
        base_path = _odoo / "Scripts"
    scripts_path = base_path / "inc" if industry else base_path
    scripts_path.mkdir(parents=True, exist_ok=True)

    core_path = _community_path(odoo_path)
    odoo_bin = core_path / "odoo-bin"
    if not odoo_bin.exists():
        return False, f"odoo-bin introuvable: {odoo_bin}"

    script_name = f"odoo-{db_name}.sh"
    script_path = scripts_path / script_name
    core_str = str(core_path)
    odoo_bin_str = str(odoo_bin)

    install_mods = ",".join([m.strip() for m in (install_modules or []) if (m or "").strip()])
    update_mods = ",".join([m.strip() for m in (update_modules or []) if (m or "").strip()])
    if update_mode not in {"reinit", "update"}:
        update_mode = "reinit"

    content = f"""#!/bin/bash
set -euo pipefail
unset PYENV_VERSION VIRTUAL_ENV

DB={db_name}
VERSION={version}
BRANCH={branch}
ADDONS="{addons_path}"
INSTALL_MODULES="{install_mods}"
UPDATE_MODULES="{update_mods}"
UPDATE_MODE="{update_mode}"

cd {core_str}

PYENV_ENV="${{PYENV_ENV:-${{PYENV_VERSION:-odoo-${{VERSION}}}}}}"
export PYENV_VERSION="${{PYENV_ENV}}"
PYENV_ROOT="${{PYENV_ROOT:-$HOME/.pyenv}}"
PYTHON="${{PYTHON:-${{PYENV_ROOT}}/versions/${{PYENV_ENV}}/bin/python3}}"
[[ -x "$PYTHON" ]] || PYTHON="python3"
PSQL_BIN="${{PSQL_BIN:-psql}}"

ARGS=(
  --addons-path "$ADDONS"
  --limit-memory-hard 0
  --db-filter "^${{DB}}$"
  -d "$DB"
  --dev=all
  --http-port={get_odoo_http_port()}
  --http-interface=127.0.0.1
)

DB_EXISTS=0
if "$PSQL_BIN" -lqt 2>/dev/null | cut -d '|' -f 1 | sed 's/[[:space:]]//g' | grep -Fxq "$DB"; then
  DB_EXISTS=1
fi

if [[ "$DB_EXISTS" -eq 1 ]]; then
  if [[ "$UPDATE_MODE" == "update" ]]; then
    if [[ -n "$UPDATE_MODULES" ]]; then
      ARGS+=(-u "$UPDATE_MODULES")
    fi
  else
    if [[ -n "$UPDATE_MODULES" ]]; then
      ARGS+=(--reinit "$UPDATE_MODULES")
    else
      ARGS+=(--reinit)
    fi
  fi
else
  if [[ -n "$INSTALL_MODULES" ]]; then
    ARGS+=(-i "$INSTALL_MODULES")
  fi
fi

exec "$PYTHON" {odoo_bin_str} \\
  "${{ARGS[@]}}"
"""
    try:
        script_path.write_text(content, encoding="utf-8")
        script_path.chmod(0o755)
        return True, str(script_path)
    except PermissionError as e:
        return False, f"Permission refusée pour {script_path} : {e}"
    except OSError as e:
        return False, f"Impossible d'écrire dans {script_path} : {e}"
    except Exception as e:
        return False, str(e)


def build_addons_path(odoo_path: str, extra_folders: list[str]) -> str:
    """Construit le --addons-path à partir du chemin Odoo et dossiers custom."""
    parts = [str(_community_path(odoo_path) / "addons")]
    enterprise = _effective_enterprise_path()
    if enterprise:
        parts.append(str(enterprise))
    for f in extra_folders:
        p = Path(f).resolve()
        if p.is_dir() and str(p) not in parts:
            parts.append(str(p))
    return ",".join(parts)


def get_script_config(script_path: Path) -> dict:
    """Retourne la config launch du script (addons/modules/mode)."""
    text = script_path.read_text(encoding="utf-8")
    def _extract(name: str) -> str:
        m = re.search(rf"^{name}\s*=\s*\"([^\"]*)\"", text, re.MULTILINE)
        if m:
            return m.group(1).strip()
        m = re.search(rf"^{name}\s*=\s*([^\n#]+)", text, re.MULTILINE)
        return (m.group(1).strip().strip("'").strip('"') if m else "")
    addons_path = _extract("ADDONS")
    install_modules = _extract("INSTALL_MODULES")
    update_modules = _extract("UPDATE_MODULES")
    update_mode = _extract("UPDATE_MODE") or "reinit"
    if update_mode not in {"reinit", "update"}:
        update_mode = "reinit"
    return {
        "addons_path": addons_path,
        "install_modules": install_modules,
        "update_modules": update_modules,
        "update_mode": update_mode,
    }


def update_script_config(
    script_path: Path,
    addons_path: str,
    install_modules: list[str],
    update_modules: list[str],
    update_mode: str,
) -> tuple[bool, str]:
    """Met à jour la config de lancement d'un script existant."""
    try:
        text = script_path.read_text(encoding="utf-8")
    except Exception as e:
        return False, str(e)
    if update_mode not in {"reinit", "update"}:
        update_mode = "reinit"
    install_mods = ",".join([m.strip() for m in install_modules if (m or "").strip()])
    update_mods = ",".join([m.strip() for m in update_modules if (m or "").strip()])
    replacements = {
        "ADDONS": addons_path,
        "INSTALL_MODULES": install_mods,
        "UPDATE_MODULES": update_mods,
        "UPDATE_MODE": update_mode,
    }
    missing_vars: list[str] = []
    for var, val in replacements.items():
        if re.search(rf"^{var}\s*=", text, re.MULTILINE):
            text = re.sub(rf"^{var}\s*=.*$", f'{var}="{val}"', text, flags=re.MULTILINE)
        else:
            missing_vars.append(var)
    if missing_vars:
        if re.search(r"^ADDONS\s*=", text, re.MULTILINE):
            insert_block = "\n".join(f'{var}="{replacements[var]}"' for var in missing_vars if var != "ADDONS")
            text = re.sub(r"^(ADDONS\s*=.*)$", rf"\1\n{insert_block}", text, count=1, flags=re.MULTILINE)
        elif "ADDONS" in missing_vars:
            header_block = "\n".join(f'{var}="{replacements[var]}"' for var in replacements)
            text = header_block + "\n" + text
    try:
        script_path.write_text(text, encoding="utf-8")
        script_path.chmod(0o755)
        return True, str(script_path)
    except Exception as e:
        return False, str(e)


def _module_exists_in_addons_root(addons_root: Path, module_name: str) -> bool:
    """Vérifie si un module existe dans un dossier addons donné."""
    if not addons_root.is_dir():
        return False
    candidate = addons_root / module_name
    return candidate.is_dir() and (candidate / "__manifest__.py").exists()


def _detect_module_parent_dirs(odoo_path: str, modules: list[str]) -> list[str]:
    """
    Détecte les dossiers addons probables pour les modules demandés.
    Recherche dans les sous-dossiers de la racine Odoo (hors core/enterprise/scripts).
    """
    if not modules:
        return []
    odoo_root = _community_path(odoo_path).parent
    if not odoo_root.is_dir():
        return []
    ignored = {"core", "enterprise", "scripts", ".git", "__pycache__"}
    found: list[str] = []
    seen: set[str] = set()
    for child in odoo_root.iterdir():
        if not child.is_dir() or child.name in ignored or child.name.startswith("."):
            continue
        for module_name in modules:
            if not module_name or module_name in seen:
                continue
            if _module_exists_in_addons_root(child, module_name):
                found.append(str(child.resolve()))
                seen.add(module_name)
        if len(seen) == len(modules):
            break
    return list(dict.fromkeys(found))


def build_addons_path_for_modules(
    odoo_path: str,
    extra_folders: list[str],
    modules: list[str],
) -> tuple[str, list[str], list[str]]:
    """
    Construit le addons-path final avec auto-détection des dossiers nécessaires.
    Retourne (addons_path, dossiers_detectes, modules_introuvables).
    """
    modules_clean = [m.strip() for m in modules if (m or "").strip()]
    base_parts = [p for p in build_addons_path(odoo_path, extra_folders).split(",") if p]
    found_dirs = _detect_module_parent_dirs(odoo_path, modules_clean)
    for path in found_dirs:
        if path not in base_parts:
            base_parts.append(path)
    missing: list[str] = []
    for module_name in modules_clean:
        exists = any(_module_exists_in_addons_root(Path(addons_root), module_name) for addons_root in base_parts)
        if not exists:
            missing.append(module_name)
    return ",".join(base_parts), found_dirs, missing


def get_modules_in_folder(folder: str) -> list[str]:
    """Récupère les noms des modules (dossiers avec __manifest__.py) dans un répertoire."""
    mods = []
    path = Path(folder)
    if not path.is_dir():
        return mods
    for item in path.iterdir():
        if item.is_dir() and (item / "__manifest__.py").exists():
            mods.append(item.name)
    return sorted(mods)


def run_odoo(
    odoo_path: str,
    db_name: str,
    addons_path: str,
    install_modules: list[str],
    update_modules: list[str],
    pyenv_env: Optional[str],
    on_output: callable = None,
) -> tuple[int, str]:
    """
    Lance odoo-bin pour créer ou mettre à jour la base.
    - install_modules: utilisés si la DB n'existe pas (-i)
    - update_modules: utilisés si la DB existe (--reinit)
    - on_output: callback(msg) pour afficher la sortie en temps réel
    """
    from config import get_odoo_http_port
    core_path = _community_path(odoo_path)
    odoo_bin = core_path / "odoo-bin"
    if not odoo_bin.exists():
        return -1, f"odoo-bin introuvable: {odoo_bin}"

    base_cmd = [
        "python3", str(odoo_bin),
        "--addons-path", addons_path,
        "--limit-memory-hard", "0",
        "--db-filter", f"^{db_name}$",
        "-d", db_name,
        "--without-demo=all",
        "--dev=xml",
        "--http-port", str(get_odoo_http_port()),
    ]

    if db_exists(db_name):
        # DB existe: reinit avec les modules de mise à jour
        if update_modules:
            base_cmd += ["--reinit", ",".join(update_modules)]
        else:
            base_cmd += ["--reinit"]
    else:
        # Création: installation des modules
        if install_modules:
            base_cmd += ["-i", ",".join(install_modules)]

    env = _env_with_path()
    # Éviter que PYTHONHOME/PYTHONPATH de l'app (ex. py2app) ne pollue le sous-processus
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    if pyenv_env:
        from config import get_pyenv_root
        pyenv_root = get_pyenv_root() or os.environ.get("PYENV_ROOT", os.path.expanduser("~/.pyenv"))
        venv_python = Path(pyenv_root) / "versions" / pyenv_env / "bin" / "python3"
        if venv_python.exists():
            base_cmd[0] = str(venv_python)

    def _output(line: str):
        if on_output and line.strip():
            on_output(line)

    try:
        proc = subprocess.Popen(
            base_cmd,
            cwd=str(core_path),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        full_out = []
        for line in proc.stdout:
            full_out.append(line)
            _output(line.rstrip())
        proc.wait()
        return proc.returncode or 0, "".join(full_out)
    except Exception as e:
        return -1, str(e)


def clear_module_locks(db_name: str) -> bool:
    """Nettoie les verrous de modules Odoo."""
    if not db_exists(db_name):
        return True
    psql = _find_psql()
    code, _, _ = run_cmd([
        psql, "-d", db_name,
        "-c", "UPDATE ir_module_module SET state = 'uninstalled' WHERE state IN ('to install', 'to upgrade', 'to remove');"
    ])
    return code == 0
