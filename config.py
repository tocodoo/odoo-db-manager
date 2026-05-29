"""Configuration par défaut pour Odoo DB Manager."""
import json
import os

# Valeurs par défaut (système)
_DEFAULTS = {
    "odoo_path": "~/odoo",
    "odoo_community_path": "",
    "odoo_enterprise_path": "",
    "scripts_paths": ["~/Scripts"],
    "pyenv_root": "~/.pyenv",
    "pyenv_global_env": "",
    "psql_path": "",  # vide = auto-détection
    "terminal_app": "Warp",  # Warp, Terminal, iTerm2
    "odoo_http_port": 8069,
    "dismissed_app_version": "",
}


def _config_path() -> str:
    return os.path.expanduser("~/Library/Application Support/Odoo Database Manager/config.json")


def _resolve_path(p: str, base: str = "~") -> str:
    """Rend le chemin absolu. Relatif → depuis base (ou ~)."""
    p = (p or "").strip()
    if not p:
        return ""
    expanded = os.path.expanduser(p)
    if not os.path.isabs(expanded):
        base_exp = os.path.expanduser(base)
        expanded = os.path.normpath(os.path.join(base_exp, expanded))
    return os.path.normpath(expanded)


def _resolve_scripts_path(p: str) -> str:
    """Rend le chemin absolu. Relatif → depuis ~"""
    p = (p or "").strip()
    if not p:
        return ""
    expanded = os.path.expanduser(p)
    if not os.path.isabs(expanded):
        expanded = os.path.normpath(os.path.join(os.path.expanduser("~"), expanded))
    return os.path.normpath(expanded)


def _load_config() -> dict:
    """Charge la config : env > fichier > défauts."""
    data = dict(_DEFAULTS)
    cfg = _config_path()
    if os.path.isfile(cfg):
        try:
            loaded = json.loads(open(cfg).read())
            for k, v in loaded.items():
                if k in data:
                    data[k] = v
        except Exception:
            pass
    # Env override
    if os.environ.get("ODOO_DB_MANAGER_PATH"):
        data["odoo_path"] = os.environ["ODOO_DB_MANAGER_PATH"]
    if os.environ.get("ODOO_DB_MANAGER_SCRIPTS"):
        data["scripts_paths"] = [p.strip() for p in os.environ["ODOO_DB_MANAGER_SCRIPTS"].split(",") if p.strip()]
    if os.environ.get("PYENV_ROOT"):
        data["pyenv_root"] = os.environ["PYENV_ROOT"]
    return data


def _save_config(updates: dict) -> bool:
    """Sauvegarde des clés dans le fichier config."""
    try:
        cfg = _config_path()
        os.makedirs(os.path.dirname(cfg), exist_ok=True)
        data = _load_config()
        for k, v in updates.items():
            if k in _DEFAULTS:
                data[k] = v
        with open(cfg, "w") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception:
        return False


def get_odoo_path() -> str:
    """Chemin racine Odoo (legacy). Déduit depuis community/enterprise si fournis."""
    cfg = _load_config()
    comm = _resolve_path(cfg.get("odoo_community_path", ""))
    ent = _resolve_path(cfg.get("odoo_enterprise_path", ""))
    if comm:
        parent = os.path.dirname(comm.rstrip(os.sep))
        if parent:
            return parent
    if ent:
        parent = os.path.dirname(ent.rstrip(os.sep))
        if parent:
            return parent
    p = cfg.get("odoo_path", _DEFAULTS["odoo_path"])
    return _resolve_path(p)


def save_odoo_path(path: str) -> bool:
    return _save_config({"odoo_path": (path or "").strip()})


def get_odoo_community_path() -> str:
    """Chemin exact du dossier community (contenant odoo-bin)."""
    cfg = _load_config()
    p = cfg.get("odoo_community_path", "")
    if p:
        return _resolve_path(p)
    return os.path.join(get_odoo_path(), "core")


def save_odoo_community_path(path: str) -> bool:
    return _save_config({"odoo_community_path": (path or "").strip()})


def get_odoo_enterprise_path() -> str:
    """Chemin exact du dossier enterprise."""
    cfg = _load_config()
    p = cfg.get("odoo_enterprise_path", "")
    return _resolve_path(p) if p else ""


def save_odoo_enterprise_path(path: str) -> bool:
    return _save_config({"odoo_enterprise_path": (path or "").strip()})


def get_pyenv_root() -> str:
    """Racine pyenv (contient versions/odoo-18, odoo-19)."""
    p = _load_config().get("pyenv_root", _DEFAULTS["pyenv_root"])
    return _resolve_path(p) if p else ""


def save_pyenv_root(path: str) -> bool:
    return _save_config({"pyenv_root": (path or "").strip()})


def get_pyenv_global_env() -> str:
    """Environnement pyenv global choisi dans l'app (optionnel)."""
    return (_load_config().get("pyenv_global_env") or "").strip()


def save_pyenv_global_env(env_name: str) -> bool:
    return _save_config({"pyenv_global_env": (env_name or "").strip()})


def get_psql_path() -> str:
    """Chemin psql (vide = auto-détection)."""
    return (_load_config().get("psql_path") or "").strip()


def save_psql_path(path: str) -> bool:
    return _save_config({"psql_path": (path or "").strip()})


def get_scripts_paths() -> list[str]:
    """Charge les chemins scripts."""
    paths = _load_config().get("scripts_paths", _DEFAULTS["scripts_paths"])
    if isinstance(paths, str):
        paths = [paths]
    resolved = [_resolve_scripts_path(p) for p in paths if p]
    return [p for p in resolved if p] if resolved else [_resolve_scripts_path("~/Scripts")]


def save_scripts_paths(paths: list[str]) -> bool:
    """Enregistre les chemins scripts."""
    return _save_config({"scripts_paths": [p.strip() for p in paths if p.strip()]})


def get_terminal_app() -> str:
    """Application terminal à lancer (Warp, Terminal, iTerm2, etc.)."""
    return (_load_config().get("terminal_app") or _DEFAULTS["terminal_app"]).strip()


def save_terminal_app(app: str) -> bool:
    return _save_config({"terminal_app": (app or "").strip()})


def get_odoo_http_port() -> int:
    """Port HTTP Odoo (défaut 8069)."""
    v = _load_config().get("odoo_http_port", _DEFAULTS["odoo_http_port"])
    try:
        return int(v) if v not in (None, "") else 8069
    except (ValueError, TypeError):
        return 8069


def save_odoo_http_port(port: int) -> bool:
    return _save_config({"odoo_http_port": int(port)})


def _default_odoo() -> str:
    return get_odoo_path()


DEFAULT_ODOO_PATH = _default_odoo()  # utilisé à l'import, préférer get_odoo_path() à l'exécution
DEFAULT_CORE_PATH = os.path.join(DEFAULT_ODOO_PATH, "core")
DEFAULT_SCRIPTS_PATHS = get_scripts_paths()
DEFAULT_SCRIPTS_PATH = DEFAULT_SCRIPTS_PATHS[0] if DEFAULT_SCRIPTS_PATHS else os.path.expanduser("~/Scripts")
DEFAULT_ENTERPRISE_PATH = os.path.join(DEFAULT_ODOO_PATH, "enterprise")

# Mapping version -> branche git et environnement pyenv
VERSION_CONFIG = {
    "18": {"branch": "18.0", "pyenv": "odoo-18"},
    "19": {"branch": "19.0", "pyenv": "odoo-19"},
}


def _to_tilde(p: str, home: str) -> str:
    """Convertit un chemin absolu en ~/... si sous home."""
    if not p or not home:
        return p
    norm = os.path.normpath(p)
    if norm.startswith(home + os.sep) or norm == home:
        return "~" + norm[len(home):]
    return norm


def detect_all_paths() -> dict:
    """Détecte automatiquement les chemins (odoo, scripts, pyenv, psql)."""
    home = os.path.expanduser("~")
    result = {
        "odoo_path": "",
        "odoo_community_path": "",
        "odoo_enterprise_path": "",
        "scripts_paths": [],
        "pyenv_root": "",
        "psql_path": "",
    }

    # Odoo : chercher un dossier contenant core/ et enterprise/
    candidates = [
        os.path.join(home, "odoo"),
        os.path.join(home, "Desktop", "odoo"),
        os.path.join(home, "dev", "odoo"),
        os.path.join(home, "Documents", "odoo"),
        os.path.join(home, "workspace", "odoo"),
        os.path.join(home, "code", "odoo"),
    ]
    for candidate in candidates:
        c = os.path.normpath(candidate)
        if os.path.isdir(c):
            core = os.path.join(c, "core")
            ent = os.path.join(c, "enterprise")
            if os.path.isdir(core) and os.path.isdir(ent):
                result["odoo_path"] = _to_tilde(c, home) or c
                result["odoo_community_path"] = _to_tilde(core, home) or core
                result["odoo_enterprise_path"] = _to_tilde(ent, home) or ent
                break

    # Scripts : ~/Scripts, ~/scripts
    for rel in ["Scripts", "scripts", "Scripts Odoo"]:
        p = os.path.join(home, rel)
        if os.path.isdir(p):
            result["scripts_paths"].append(_to_tilde(p, home) or p)
    if not result["scripts_paths"]:
        result["scripts_paths"] = ["~/Scripts"]

    # Pyenv : PYENV_ROOT ou ~/.pyenv
    pyenv = os.environ.get("PYENV_ROOT", "")
    if not pyenv:
        pyenv = os.path.join(home, ".pyenv")
    if os.path.isdir(pyenv) and os.path.isdir(os.path.join(pyenv, "versions")):
        result["pyenv_root"] = _to_tilde(pyenv, home) or pyenv
    else:
        result["pyenv_root"] = "~/.pyenv"

    # psql (garder chemin absolu)
    try:
        from odoo_ops import detect_psql_path
        result["psql_path"] = detect_psql_path() or ""
    except Exception:
        pass

    return result


def get_dismissed_app_version() -> str:
    return str(_load_config().get("dismissed_app_version") or "").strip()


def save_dismissed_app_version(version: str) -> bool:
    return _save_config({"dismissed_app_version": (version or "").strip()})


def get_pyenv_for_branch(branch: str) -> str:
    """Déduit l'environnement pyenv à partir du nom de branche (ex: saas-18.4 -> odoo-18)."""
    if branch.startswith("19") or "19" in branch.split("-")[-1]:
        return "odoo-19"
    return "odoo-18"
