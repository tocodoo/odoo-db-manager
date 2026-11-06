#!/usr/bin/env python3
"""
Assistant IA du Odoo Database Manager.

Pilote l'app en langage naturel (créer / supprimer / lancer une base, lancer des
commandes odoo-bin) en s'appuyant sur le CLI `claude` (abonnement Claude Code).

Principes :
  - Aucune dépendance Python supplémentaire : on parle au binaire `claude`.
  - Le modèle ne reçoit AUCUN outil Claude Code (`--restricted`) : il ne peut ni
    lire ni écrire de fichiers, ni lancer de shell. Il peut seulement répondre et
    demander une action dans notre catalogue fermé (TOOLS).
  - Les actions qui modifient l'état (créer, lancer, supprimer, odoo-bin…) sont
    exécutées par CE module, après validation de l'utilisateur dans l'interface.
  - La conversation est gérée ici (prompt complet à chaque appel) : pas de
    dépendance aux sessions du CLI, donc comportement reproductible.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from odoo_ops import _env_with_path

CLAUDE_TIMEOUT = 240
MAX_TOOL_ITERATIONS = 8

# Bloc d'action attendu en fin de réponse du modèle.
ACTION_BLOCK_RE = re.compile(
    r"```(?:odoo-action|json)?\s*\n(\{.*?\})\s*\n```",
    re.DOTALL,
)

# Métacaractères shell refusés dans les arguments libres d'odoo-bin.
SHELL_META_RE = re.compile(r"[;&|`$><\n\r]|\$\(")


# ──────────────────────────────────────────────────────────────────────────────
# Détection du CLI Claude Code et de l'authentification
# ──────────────────────────────────────────────────────────────────────────────

def find_claude_cli() -> str:
    """Chemin du binaire `claude`, ou '' s'il est introuvable."""
    env = _env_with_path()
    found = shutil.which("claude", path=env.get("PATH", ""))
    if found:
        return found
    home = Path.home()
    candidates = [
        home / ".local" / "bin" / "claude",
        home / ".claude" / "local" / "claude",
        Path("/opt/homebrew/bin/claude"),
        Path("/usr/local/bin/claude"),
        home / ".bun" / "bin" / "claude",
        home / ".npm-global" / "bin" / "claude",
    ]
    for c in candidates:
        if c.exists() and os.access(c, os.X_OK):
            return str(c)
    return ""


# Variables propres à une session Claude Code parente : si l'app est lancée depuis un
# terminal où tourne Claude Code, elles feraient croire au CLI qu'il est un sous-agent.
_PARENT_SESSION_VARS = (
    "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS", "CLAUDE_PID",
    "CLAUDE_AGENT_SDK_VERSION", "CLAUDE_CODE_STARTUP_FAILURE_RESULTS",
)


def _claude_env() -> dict:
    env = _env_with_path()
    for k in _PARENT_SESSION_VARS:
        env.pop(k, None)
    return env


def _cli_version(cli: str) -> str:
    try:
        res = subprocess.run(
            [cli, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, env=_env_with_path(),
        )
        return (res.stdout or "").strip().split("\n")[0]
    except Exception:
        return ""


def _keychain_has_credentials() -> bool:
    """Credentials OAuth Claude Code stockés dans le trousseau macOS ?"""
    for service in ("Claude Code-credentials", "Claude Code"):
        try:
            res = subprocess.run(
                ["security", "find-generic-password", "-s", service],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            )
            if res.returncode == 0:
                return True
        except Exception:
            pass
    return False


def _account_label() -> str:
    """Email du compte Claude Code, si connu."""
    try:
        data = json.loads((Path.home() / ".claude.json").read_text())
        account = data.get("oauthAccount")
        if isinstance(account, dict):
            return account.get("emailAddress") or account.get("organizationName") or ""
    except Exception:
        pass
    return ""


def auth_status(probe: bool = False) -> dict:
    """
    État de la connexion à Claude Code.

    probe=False : détection locale (instantanée, aucun appel réseau).
    probe=True  : appel réel au CLI pour lever le doute (coûte un aller-retour).
    """
    cli = find_claude_cli()
    status: dict[str, Any] = {
        "cli_installed": bool(cli),
        "cli_path": cli,
        "cli_version": _cli_version(cli) if cli else "",
        "account": _account_label(),
        "method": "",
        "authenticated": False,
        "checked": "local",
        "message": "",
    }

    if not cli:
        status["message"] = (
            "Claude Code n'est pas installé. Installez-le puis relancez : "
            "npm install -g @anthropic-ai/claude-code"
        )
        return status

    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        status["method"] = "api_key"
        status["authenticated"] = True
    elif _keychain_has_credentials() or (Path.home() / ".claude" / ".credentials.json").exists():
        status["method"] = "subscription"
        status["authenticated"] = True
    else:
        status["message"] = "Claude Code n'est pas connecté. Connectez-vous avec votre abonnement."

    if probe:
        ok, msg = _probe_cli(cli)
        status["checked"] = "probe"
        status["authenticated"] = ok
        status["message"] = "" if ok else msg
        if ok and not status["method"]:
            status["method"] = "subscription"

    return status


def _probe_cli(cli: str) -> tuple[bool, str]:
    """Appel minimal au CLI pour vérifier que l'authentification passe."""
    try:
        res = subprocess.run(
            [cli, "-p", "ok", "--output-format", "json", "--max-turns", "1",
             "--restricted", "--disable-slash-commands"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
            cwd=str(_agent_cwd()), env=_claude_env(),
        )
        payload = json.loads(res.stdout or "{}")
        if payload.get("is_error"):
            return False, str(payload.get("result") or "Appel refusé par Claude Code.")
        return True, ""
    except json.JSONDecodeError:
        return False, "Réponse illisible du CLI Claude Code."
    except subprocess.TimeoutExpired:
        return False, "Délai dépassé en contactant Claude Code."
    except Exception as e:
        return False, str(e)


def login_in_terminal() -> tuple[bool, str]:
    """Ouvre le terminal configuré sur `claude /login`."""
    cli = find_claude_cli()
    if not cli:
        return False, "Binaire `claude` introuvable."
    from odoo_ops import _launch_in_terminal
    inner = f"{shlex.quote(cli)} /login"
    ok, msg = _launch_in_terminal(inner, cwd=str(Path.home()), script_name="claude /login")
    if ok:
        return True, "Connexion Claude Code ouverte dans le terminal. Revenez ici une fois connecté."
    return False, msg


def _agent_cwd() -> Path:
    """
    Dossier de travail neutre pour le CLI : évite qu'un CLAUDE.md du projet
    courant ne vienne polluer le contexte de l'assistant.
    """
    p = Path.home() / "Library" / "Application Support" / "Odoo Database Manager" / "agent"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        return Path.home()
    return p


# ──────────────────────────────────────────────────────────────────────────────
# Catalogue d'outils
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Tool:
    name: str
    kind: str           # "read" | "write" | "danger"
    summary: str
    params: dict[str, str]
    handler: Callable[..., tuple[bool, str, Any]]
    preview: Optional[Callable[[dict], str]] = None
    danger_if: Optional[Callable[[dict], bool]] = None

    def kind_for(self, args: dict) -> str:
        """Niveau effectif : certaines commandes deviennent destructives selon leurs arguments."""
        if self.danger_if:
            try:
                if self.danger_if(args or {}):
                    return "danger"
            except Exception:
                pass
        return self.kind


TOOLS: dict[str, Tool] = {}


def tool(name: str, kind: str, summary: str, params: dict[str, str],
         preview: Optional[Callable[[dict], str]] = None,
         danger_if: Optional[Callable[[dict], bool]] = None):
    def deco(fn):
        TOOLS[name] = Tool(name=name, kind=kind, summary=summary, params=params,
                           handler=fn, preview=preview, danger_if=danger_if)
        return fn
    return deco


def _odoo_path() -> str:
    from config import get_odoo_path
    return get_odoo_path()


# ── Lecture ───────────────────────────────────────────────────────────────────

@tool("list_databases", "read",
      "Liste les bases PostgreSQL Odoo, leur version et celle qui tourne.",
      {})
def _t_list_databases() -> tuple[bool, str, Any]:
    from odoo_ops import (get_all_running_odoo_dbs, get_db_version, get_script_subdirectory,
                          list_databases)
    dbs = list_databases()
    running = get_all_running_odoo_dbs()
    return True, f"{len(dbs)} base(s).", {
        "databases": [
            {
                "name": db,
                "version": get_db_version(db),
                "scripts_subdir": get_script_subdirectory(db),
                "running": db in running,
            }
            for db in dbs
        ],
        "running": running,
    }


@tool("database_info", "read",
      "Détail d'une base : existence, version, branche, script de lancement, état.",
      {"db": "nom de la base"})
def _t_database_info(db: str = "") -> tuple[bool, str, Any]:
    from odoo_ops import (db_exists, get_all_running_odoo_dbs, get_db_version_and_branch,
                          get_script_path_for_db, get_script_subdirectory)
    db = (db or "").strip()
    if not db:
        return False, "Paramètre `db` requis.", None
    version, branch = get_db_version_and_branch(db)
    script = get_script_path_for_db(db)
    return True, f"Infos sur {db}.", {
        "name": db,
        "exists": db_exists(db),
        "version": version,
        "branch": branch,
        "script": str(script) if script else None,
        "scripts_subdir": get_script_subdirectory(db),
        "running": db in get_all_running_odoo_dbs(),
    }


@tool("list_branches", "read",
      "Branches git disponibles dans core/ et branche courante.",
      {})
def _t_list_branches() -> tuple[bool, str, Any]:
    from odoo_ops import get_current_branch, get_git_branches
    core = Path(_odoo_path()) / "core"
    if not core.is_dir():
        return False, f"core introuvable : {core}", None
    return True, "Branches listées.", {
        "current": get_current_branch(str(core)),
        "branches": get_git_branches(str(core)),
    }


@tool("get_config", "read",
      "Configuration de l'app : chemins Odoo/scripts/pyenv/psql, terminal, port HTTP.",
      {})
def _t_get_config() -> tuple[bool, str, Any]:
    from config import _load_config, get_odoo_path, get_scripts_paths
    cfg = dict(_load_config())
    cfg.pop("db_ports", None)
    cfg["odoo_path_resolved"] = get_odoo_path()
    cfg["scripts_paths_resolved"] = get_scripts_paths()
    cfg["scripts_subdirs"] = _scripts_subdirs()
    return True, "Configuration courante.", cfg


@tool("list_modules", "read",
      "Modules Odoo présents dans un dossier d'addons (core/addons et enterprise par défaut).",
      {"folder": "dossier à inspecter (optionnel)"})
def _t_list_modules(folder: str = "") -> tuple[bool, str, Any]:
    from odoo_ops import get_modules_in_folder
    base = Path(_odoo_path())
    folders = [folder] if folder else [str(base / "core" / "addons"), str(base / "enterprise")]
    out = {}
    for f in folders:
        expanded = os.path.expanduser(f)
        mods = get_modules_in_folder(expanded)
        out[expanded] = {"count": len(mods), "modules": mods[:400]}
    return True, "Modules listés.", out


# ── Écriture ──────────────────────────────────────────────────────────────────

@tool("create_database", "write",
      "Crée une base (ou la met à jour avec --reinit si elle existe) et génère son script .sh. "
      "Lance odoo-bin dans le terminal.",
      {"db": "nom de la base", "modules": "liste de modules à installer",
       "branch": "branche git (optionnel)", "version": "18 ou 19 (optionnel)",
       "scripts_subdir": "sous-dossier de rangement du script .sh (optionnel, "
                         "voir scripts_subdirs dans l'état de la machine)"},
      preview=lambda a: f"Créer / mettre à jour « {a.get('db')} » "
                        f"(modules : {', '.join(_as_list(a.get('modules'))) or 'aucun'})")
def _t_create_database(db: str = "", modules: Any = None, branch: str = "",
                       version: str = "", scripts_subdir: str = "",
                       scripts_dir: str = "") -> tuple[bool, str, Any]:
    from config import get_pyenv_for_branch
    from odoo_ops import (build_addons_path, clear_module_locks, create_launch_script,
                          db_exists, get_pyenv_env, run_odoo_create_in_terminal, switch_branch)
    db = (db or "").strip()
    if not db:
        return False, "Paramètre `db` requis.", None

    mods = _as_list(modules)
    path = _odoo_path()
    branch = (branch or "").strip() or _current_branch_or_default()
    version = (version or "").strip() or ("19" if "19" in branch else "18")
    pyenv = get_pyenv_env(version) if version in ("18", "19") else get_pyenv_for_branch(branch)

    ok_switch, msg_switch = switch_branch(path, branch)
    if not ok_switch:
        return False, msg_switch, None

    if db_exists(db):
        clear_module_locks(db)

    addons = build_addons_path(path, [])
    ok_script, msg_script = create_launch_script(
        path, db, addons, pyenv, version=version, branch=branch,
        scripts_dir=scripts_dir or None, scripts_subdir=scripts_subdir or None,
        install_modules=mods, update_modules=mods,
    )
    ok, msg = run_odoo_create_in_terminal(path, db, addons, mods, mods, pyenv)
    if not ok:
        return False, msg, None
    return True, msg, {"script": msg_script if ok_script else None,
                       "script_error": None if ok_script else msg_script,
                       "branch": branch, "version": version, "modules": mods}


@tool("start_database", "write",
      "Démarre Odoo pour une base (via son script .sh s'il existe).",
      {"db": "nom de la base"},
      preview=lambda a: f"Lancer Odoo sur « {a.get('db')} »")
def _t_start_database(db: str = "") -> tuple[bool, str, Any]:
    from config import get_pyenv_for_branch
    from odoo_ops import (build_addons_path, get_db_version_and_branch, get_script_path_for_db,
                          run_odoo_in_terminal, run_script_for_db, switch_branch)
    db = (db or "").strip()
    if not db:
        return False, "Paramètre `db` requis.", None
    path = _odoo_path()

    script = get_script_path_for_db(db)
    if script:
        ok, msg = run_script_for_db(script, odoo_root=path)
        return ok, msg, {"script": str(script)}

    version, branch = get_db_version_and_branch(db)
    branch = branch or _current_branch_or_default()
    pyenv = f"odoo-{version}" if version in ("18", "19") else get_pyenv_for_branch(branch)
    ok_switch, msg_switch = switch_branch(path, branch)
    if not ok_switch:
        return False, msg_switch, None
    ok, msg = run_odoo_in_terminal(path, db, build_addons_path(path, []), pyenv)
    return ok, msg, None


@tool("stop_database", "write",
      "Arrête le serveur Odoo d'une base.",
      {"db": "nom de la base"},
      preview=lambda a: f"Arrêter Odoo sur « {a.get('db')} »")
def _t_stop_database(db: str = "") -> tuple[bool, str, Any]:
    from config import get_terminal_app
    from odoo_ops import quit_warp, stop_odoo_server
    db = (db or "").strip()
    if not db:
        return False, "Paramètre `db` requis.", None
    ok, msg = stop_odoo_server(db)
    if ok and get_terminal_app() == "Warp":
        quit_warp()
    return ok, msg, None


@tool("stop_all_servers", "write",
      "Arrête tous les processus Odoo en cours.",
      {},
      preview=lambda a: "Arrêter tous les processus Odoo")
def _t_stop_all() -> tuple[bool, str, Any]:
    from config import get_terminal_app
    from odoo_ops import quit_warp, stop_all_odoo_servers
    count, msg = stop_all_odoo_servers()
    if count > 0 and get_terminal_app() == "Warp":
        quit_warp()
    return count > 0, msg, {"stopped": count}


@tool("switch_branch", "write",
      "Change la branche git de core/ et enterprise/.",
      {"branch": "nom de la branche"},
      preview=lambda a: f"Basculer core/ et enterprise/ sur « {a.get('branch')} »")
def _t_switch_branch(branch: str = "") -> tuple[bool, str, Any]:
    from odoo_ops import switch_branch
    branch = (branch or "").strip()
    if not branch:
        return False, "Paramètre `branch` requis.", None
    ok, msg = switch_branch(_odoo_path(), branch)
    return ok, msg, None


@tool("clear_locks", "write",
      "Nettoie les verrous de modules d'une base (états 'to install'/'to upgrade'/'to remove').",
      {"db": "nom de la base"},
      preview=lambda a: f"Nettoyer les verrous de modules de « {a.get('db')} »")
def _t_clear_locks(db: str = "") -> tuple[bool, str, Any]:
    from odoo_ops import clear_module_locks
    db = (db or "").strip()
    if not db:
        return False, "Paramètre `db` requis.", None
    ok = clear_module_locks(db)
    return ok, "Verrous nettoyés." if ok else "Impossible de nettoyer les verrous.", None


@tool("open_browser", "write",
      "Ouvre l'interface Odoo dans le navigateur.",
      {"url": "URL (optionnel, défaut http://localhost:<port>)"},
      preview=lambda a: f"Ouvrir {a.get('url') or 'Odoo'} dans le navigateur")
def _t_open_browser(url: str = "") -> tuple[bool, str, Any]:
    import webbrowser
    from config import get_odoo_http_port
    target = url or f"http://localhost:{get_odoo_http_port()}"
    try:
        webbrowser.open(target)
        return True, f"Ouvert : {target}", None
    except Exception as e:
        return False, str(e), None


@tool("duplicate_database", "write",
      "Duplique une base (copie PostgreSQL exacte + filestore + script .sh).",
      {"source": "base à copier", "target": "nom de la nouvelle base"},
      preview=lambda a: f"Dupliquer « {a.get('source')} » vers « {a.get('target')} »")
def _t_duplicate_database(source: str = "", target: str = "") -> tuple[bool, str, Any]:
    from odoo_ops import duplicate_database
    source, target = (source or "").strip(), (target or "").strip()
    if not source or not target:
        return False, "Paramètres `source` et `target` requis.", None
    ok, msg = duplicate_database(source, target)
    return ok, msg, None


@tool("rename_database", "write",
      "Renomme une base PostgreSQL et son filestore (le serveur Odoo de la base doit être arrêté).",
      {"db": "nom actuel", "new_name": "nouveau nom"},
      preview=lambda a: f"Renommer « {a.get('db')} » en « {a.get('new_name')} »")
def _t_rename_database(db: str = "", new_name: str = "") -> tuple[bool, str, Any]:
    from odoo_ops import (_find_psql, _terminate_db_connections, db_exists,
                          get_default_filestore_dir, run_cmd)
    db, new_name = (db or "").strip(), (new_name or "").strip()
    if not db or not new_name:
        return False, "Paramètres `db` et `new_name` requis.", None
    if not DB_NAME_RE.match(new_name):
        return False, f"Nom de base invalide : {new_name!r}", None
    if not db_exists(db):
        return False, f"La base « {db} » n'existe pas.", None
    if db_exists(new_name):
        return False, f"Une base « {new_name} » existe déjà.", None
    _terminate_db_connections(db)
    code, out, err = run_cmd([_find_psql(), "-d", "postgres", "-c",
                              f'ALTER DATABASE "{db}" RENAME TO "{new_name}";'])
    if code != 0:
        return False, f"psql : {(err or out).strip()}", None
    notes = [f"✓ Base renommée : {db} → {new_name}"]
    src, dst = get_default_filestore_dir(db), get_default_filestore_dir(new_name)
    if src.is_dir() and not dst.exists():
        try:
            src.rename(dst)
            notes.append("✓ Filestore déplacé")
        except OSError as e:
            notes.append(f"✗ Filestore non déplacé : {e}")
    return True, "\n".join(notes), None


@tool("sanitize_database", "write",
      "Neutralise une base importée d'un dump (admin/admin, crons et serveurs mail désactivés…).",
      {"db": "nom de la base"},
      preview=lambda a: f"Neutraliser « {a.get('db')} » (admin/admin, crons off, mails off)")
def _t_sanitize_database(db: str = "") -> tuple[bool, str, Any]:
    from odoo_ops import sanitize_database
    db = (db or "").strip()
    if not db:
        return False, "Paramètre `db` requis.", None
    lines = sanitize_database(db)
    return all(l.startswith("✓") for l in lines), "\n".join(lines), None


@tool("set_database_port", "write",
      "Change le port HTTP d'une base (patch son script .sh).",
      {"db": "nom de la base", "port": "port HTTP (ex. 8070)"},
      preview=lambda a: f"Passer « {a.get('db')} » sur le port {a.get('port')}")
def _t_set_database_port(db: str = "", port: Any = None) -> tuple[bool, str, Any]:
    from odoo_ops import set_db_port
    db = (db or "").strip()
    try:
        port_i = int(port)
    except (TypeError, ValueError):
        return False, "Paramètre `port` invalide.", None
    if not db or not (1024 <= port_i <= 65535):
        return False, "Paramètres `db` et `port` (1024-65535) requis.", None
    ok, msg = set_db_port(db, port_i)
    return ok, msg, None


# Sous-commandes livrées par défaut avec odoo-bin (odoo/cli/*.py).
ODOO_SUBCOMMANDS = {
    "server", "shell", "scaffold", "db", "cloc", "deploy", "duplicate", "help", "i18n",
    "module", "neutralize", "obfuscate", "populate", "start", "upgrade_code",
}
# Sous-commandes dont la sortie est utile à l'assistant (exécution inline, non interactive).
_CAPTURE_SUBCOMMANDS = {
    "help", "cloc", "db", "duplicate", "i18n", "module", "neutralize", "obfuscate",
    "populate", "scaffold", "upgrade_code", "deploy",
}
CLI_CAPTURE_TIMEOUT = 900
CLI_OUTPUT_TAIL = 6000
DB_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,62}$")


def _split_subcommand(argv: list[str]) -> tuple[str, list[str]]:
    if argv and not argv[0].startswith("-") and argv[0] in ODOO_SUBCOMMANDS:
        return argv[0], argv[1:]
    return "", argv


def _arg_value(argv: list[str], *names: str) -> str:
    """Valeur d'une option (`-d x`, `--database=x`)."""
    for i, a in enumerate(argv):
        for n in names:
            if a == n and i + 1 < len(argv):
                return argv[i + 1]
            if n.startswith("--") and a.startswith(n + "="):
                return a.split("=", 1)[1]
    return ""


def _odoo_cli_is_dangerous(a: dict) -> bool:
    """Commandes odoo-bin irréversibles → validation renforcée, jamais auto-exécutées."""
    sub, rest = _split_subcommand(_as_list(a.get("args")))
    if sub == "db" and any(x in ("drop", "load") for x in rest):
        return True
    if sub in ("neutralize", "obfuscate", "deploy"):
        return True
    return any(x.startswith("--uninstall") or x == "--reinit" for x in rest)


def _odoo_cli_mode(sub: str, rest: list[str]) -> str:
    if any(x in ("-h", "--help", "--version") for x in rest):
        return "capture"
    if sub in _CAPTURE_SUBCOMMANDS:
        return "capture"
    if sub in ("", "server") and "--stop-after-init" in rest:
        return "capture"
    return "terminal"  # serveur, shell interactif, start…


def _python_for(version: str, db: str) -> str:
    from config import get_pyenv_for_branch, get_pyenv_root
    from odoo_ops import get_db_version_and_branch, get_pyenv_env
    pyenv = get_pyenv_env(version) if version in ("18", "19") else None
    if not pyenv and db:
        v, b = get_db_version_and_branch(db)
        pyenv = get_pyenv_env(v) if v in ("18", "19") else (get_pyenv_for_branch(b) if b else None)
    if not pyenv:
        pyenv = get_pyenv_for_branch(_current_branch_or_default())
    if pyenv:
        root = get_pyenv_root() or os.environ.get("PYENV_ROOT", os.path.expanduser("~/.pyenv"))
        candidate = Path(root) / "versions" / pyenv / "bin" / "python3"
        if candidate.exists():
            return str(candidate)
    return "python3"


def _addons_for(db: str) -> str:
    from odoo_ops import build_addons_path, get_script_config, get_script_path_for_db
    if db:
        script = get_script_path_for_db(db)
        if script:
            try:
                addons = get_script_config(script).get("addons_path") or ""
                if addons and "$" not in addons:
                    return addons
            except Exception:
                pass
    return build_addons_path(_odoo_path(), [])


def _cli_preview(a: dict) -> str:
    return "odoo-bin " + " ".join(_as_list(a.get("args")))


@tool("odoo_cli", "write",
      "Exécute n'importe quelle commande odoo-bin standard : options serveur "
      "(ex. ['-d','ma_base','-u','sale','--stop-after-init']) ou sous-commandes "
      f"({', '.join(sorted(ODOO_SUBCOMMANDS))}), ex. ['cloc','-d','ma_base'], "
      "['db','--help'], ['shell','-d','ma_base']. Le --addons-path est ajouté "
      "automatiquement. Les commandes non interactives (--stop-after-init, cloc, help, "
      "i18n, module, db…) s'exécutent ici et leur sortie t'est renvoyée ; le serveur "
      "et le shell s'ouvrent dans le terminal. Un argument par élément, aucun opérateur shell.",
      {"args": "liste d'arguments odoo-bin",
       "version": "18 ou 19 (choix du pyenv, optionnel)",
       "mode": "auto (défaut) | capture | terminal"},
      preview=_cli_preview, danger_if=lambda a: _odoo_cli_is_dangerous(a))
def _t_odoo_cli(args: Any = None, version: str = "", mode: str = "auto") -> tuple[bool, str, Any]:
    from odoo_ops import _open_in_terminal, run_cmd

    argv = _as_list(args)
    if not argv:
        return False, "Paramètre `args` requis (liste d'arguments).", None
    for a in argv:
        if SHELL_META_RE.search(a):
            return False, f"Argument refusé (opérateur shell interdit) : {a!r}", None
    if argv[0] in ("odoo-bin", "./odoo-bin", "odoo"):
        argv = argv[1:]

    core = Path(_odoo_path()) / "core"
    if not (core / "odoo-bin").exists():
        return False, f"odoo-bin introuvable : {core / 'odoo-bin'}", None

    sub, rest = _split_subcommand(argv)
    db = _arg_value(rest, "-d", "--database")
    # odoo-bin attend `--addons-path=…` AVANT la sous-commande : valable pour toutes.
    pre: list[str] = []
    if not _arg_value(rest, "--addons-path") and "--version" not in rest:
        pre = [f"--addons-path={_addons_for(db)}"]
    full = [_python_for(str(version or ""), db), "odoo-bin"] + pre + ([sub] if sub else []) + rest
    mode = mode if mode in ("capture", "terminal") else _odoo_cli_mode(sub, rest)
    display = "odoo-bin " + " ".join(shlex.quote(a) for a in full[2:])

    if mode == "terminal":
        cmd = f"cd {shlex.quote(str(core))} && " + " ".join(shlex.quote(a) for a in full)
        ok, msg = _open_in_terminal(cmd, cwd=str(core))
        return ok, msg, {"command": display, "mode": "terminal"}

    code, out, err = run_cmd(full, cwd=str(core), timeout=CLI_CAPTURE_TIMEOUT)
    output = ((out or "") + ("\n" + err if err else "")).strip()
    if len(output) > CLI_OUTPUT_TAIL:
        output = "…\n" + output[-CLI_OUTPUT_TAIL:]
    summary = f"$ {display}\n(exit {code})\n{output}".strip()
    return code == 0, summary, {"command": display, "mode": "capture", "exit_code": code}


# ── Destructif ────────────────────────────────────────────────────────────────

@tool("delete_database", "danger",
      "Supprime définitivement une base PostgreSQL et son script .sh.",
      {"db": "nom de la base"},
      preview=lambda a: f"SUPPRIMER définitivement la base « {a.get('db')} » et son script .sh")
def _t_delete_database(db: str = "") -> tuple[bool, str, Any]:
    from odoo_ops import delete_db_complete
    db = (db or "").strip()
    if not db:
        return False, "Paramètre `db` requis.", None
    ok, msg = delete_db_complete(db)
    return ok, msg, None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _as_list(value: Any) -> list[str]:
    """Normalise un paramètre liste : accepte liste, chaîne séparée par virgules, None."""
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.replace(",", "\n").split("\n") if v.strip()]
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value)]


def _current_branch_or_default() -> str:
    from odoo_ops import get_current_branch
    core = Path(_odoo_path()) / "core"
    if core.is_dir():
        return get_current_branch(str(core)) or "19.0"
    return "19.0"


def _scripts_subdirs() -> list[str]:
    """Sous-dossiers de rangement disponibles sous les racines de scripts."""
    from config import get_scripts_paths
    from odoo_ops import list_scripts_subdirectories
    out: list[str] = []
    for root in get_scripts_paths() or []:
        for name in list_scripts_subdirectories(root) or []:
            if name not in out:
                out.append(name)
    return out


def action_preview(action: dict) -> str:
    """Phrase lisible décrivant l'action proposée."""
    spec = TOOLS.get(action.get("tool", ""))
    args = action.get("args") or {}
    if spec and spec.preview:
        try:
            return spec.preview(args)
        except Exception:
            pass
    return f"{action.get('tool')} {json.dumps(args, ensure_ascii=False)}"


def run_tool(name: str, args: dict) -> dict:
    """Exécute un outil du catalogue. Ne lève jamais."""
    spec = TOOLS.get(name)
    if not spec:
        return {"ok": False, "message": f"Outil inconnu : {name}", "data": None}
    try:
        ok, message, data = spec.handler(**(args or {}))
        return {"ok": bool(ok), "message": message, "data": data}
    except TypeError as e:
        return {"ok": False, "message": f"Paramètres invalides pour {name} : {e}", "data": None}
    except Exception as e:
        return {"ok": False, "message": f"Erreur pendant {name} : {e}", "data": None}


# ──────────────────────────────────────────────────────────────────────────────
# Contexte et prompt
# ──────────────────────────────────────────────────────────────────────────────

def snapshot() -> dict:
    """Photo de l'environnement, injectée dans le prompt et affichée dans l'UI."""
    from config import get_odoo_http_port, get_terminal_app
    snap: dict[str, Any] = {
        "odoo_path": _odoo_path(),
        "terminal": get_terminal_app(),
        "http_port": get_odoo_http_port(),
    }
    try:
        from odoo_ops import get_all_running_odoo_dbs, get_db_version, list_databases
        dbs = list_databases()
        running = get_all_running_odoo_dbs()
        snap["databases"] = [
            {"name": db, "version": get_db_version(db), "running": db in running}
            for db in dbs
        ]
        snap["running"] = running
    except Exception as e:
        snap["databases_error"] = str(e)
    try:
        from odoo_ops import get_current_branch, get_git_branches
        core = Path(_odoo_path()) / "core"
        if core.is_dir():
            snap["branch"] = get_current_branch(str(core))
            snap["branches"] = get_git_branches(str(core)) or []
    except Exception:
        pass
    try:
        snap["scripts_subdirs"] = _scripts_subdirs()
    except Exception:
        pass
    return snap


def _tools_doc() -> str:
    lines = []
    for spec in TOOLS.values():
        params = ", ".join(f"{k} ({v})" for k, v in spec.params.items()) or "aucun paramètre"
        flag = {"read": "lecture", "write": "action", "danger": "DESTRUCTIF"}[spec.kind]
        lines.append(f"- `{spec.name}` [{flag}] — {spec.summary}\n    paramètres : {params}")
    return "\n".join(lines)


SYSTEM_PROMPT = """Tu es l'assistant intégré de « Odoo Database Manager », une app macOS \
qui gère des bases Odoo en local. Tu parles à un développeur Odoo. Réponds en français, \
de façon brève et concrète.

Tu n'as accès ni au système de fichiers ni à un shell. Pour agir, tu disposes uniquement \
du catalogue d'outils ci-dessous, exécuté par l'application.

OUTILS
{tools}

PROTOCOLE
Pour appeler un outil, termine ton message par un bloc, et UN SEUL :

```odoo-action
{{"tool": "nom_de_outil", "args": {{...}}}}
```

Règles :
- Un seul outil par message. L'application l'exécute et te renvoie le résultat ; \
tu peux alors enchaîner.
- Les outils [lecture] s'exécutent automatiquement. Les outils [action] et [DESTRUCTIF] \
sont soumis à la validation de l'utilisateur : annonce en une phrase ce que tu vas faire, \
puis émets le bloc.
- Si la demande est ambiguë ou si un nom de base n'existe pas, pose la question au lieu \
de deviner. Ne supprime jamais une base qui n'a pas été nommée explicitement par l'utilisateur.
- Avant de lancer odoo-bin (ou de démarrer une base) sur une base existante, compare sa \
version à la branche courante de core/ (état ci-dessous). Si elles diffèrent, dis-le et \
propose d'abord `switch_branch` : sinon Odoo échoue ou migre la base sans le vouloir.
- Ne dis jamais qu'une branche n'existe pas sans l'avoir vérifiée avec `list_branches`. \
Si l'utilisateur demande explicitement une branche, fais ce qu'il demande : signale un \
écart de version en une phrase, mais ne lui impose pas une autre branche.
- Pour odoo-bin, passe les arguments tels qu'on les taperait (un par élément de liste) ; \
n'ajoute pas --addons-path, l'application s'en charge.
- Si aucune action n'est nécessaire, réponds simplement, sans bloc.
- Ne décris jamais le protocole ni les noms d'outils à l'utilisateur : parle en langage courant.

ÉTAT ACTUEL DE LA MACHINE
{snapshot}
"""


def build_system_prompt() -> str:
    return SYSTEM_PROMPT.format(
        tools=_tools_doc(),
        snapshot=json.dumps(snapshot(), ensure_ascii=False, indent=2),
    )


def build_prompt(transcript: list[dict]) -> str:
    """Rejoue toute la conversation dans un prompt unique (appels sans état)."""
    parts = []
    for entry in transcript:
        role = entry.get("role")
        if role == "user":
            parts.append(f"Utilisateur : {entry['content']}")
        elif role == "assistant":
            parts.append(f"Toi : {entry['content']}")
        elif role == "tool":
            parts.append(f"[Résultat de {entry['name']}]\n{entry['content']}")
    parts.append("Réponds maintenant.")
    return "\n\n".join(parts)


def parse_action(text: str) -> tuple[str, Optional[dict]]:
    """
    Sépare la prose du bloc d'action.
    Retourne (texte_affichable, action|None).
    """
    match = None
    for match in ACTION_BLOCK_RE.finditer(text):
        pass  # on garde le dernier bloc
    if not match:
        return text.strip(), None
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return text.strip(), None
    if not isinstance(payload, dict) or "tool" not in payload:
        return text.strip(), None
    prose = (text[:match.start()] + text[match.end():]).strip()
    args = payload.get("args")
    return prose, {"tool": str(payload["tool"]), "args": args if isinstance(args, dict) else {}}


# ──────────────────────────────────────────────────────────────────────────────
# Appel du CLI Claude Code
# ──────────────────────────────────────────────────────────────────────────────

def _model() -> str:
    from config import get_ai_model
    return get_ai_model()


def call_claude(prompt: str, system_prompt: str,
                on_delta: Optional[Callable[[str], None]] = None) -> tuple[bool, str]:
    """
    Un tour de modèle. Retourne (ok, texte).
    Le prompt passe par stdin pour éviter toute limite de taille d'argv.
    """
    cli = find_claude_cli()
    if not cli:
        return False, "Claude Code n'est pas installé sur cette machine."

    cmd = [
        cli, "-p",
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",
        "--restricted",            # aucun outil intégré (pas de Bash, Read, Write…)
        "--strict-mcp-config",     # aucun serveur MCP hérité de la config utilisateur
        "--disable-slash-commands",
        "--max-turns", "1",
        "--model", _model(),
        "--system-prompt", system_prompt,
        "--session-id", str(uuid.uuid4()),
    ]

    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1, cwd=str(_agent_cwd()), env=_claude_env(),
        )
    except Exception as e:
        return False, f"Impossible de lancer Claude Code : {e}"

    def _feed():
        # Toujours fermer stdin : sinon le CLI attend 3 s puis échoue sans prompt.
        try:
            proc.stdin.write(prompt)
        except Exception:
            pass
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass

    threading.Thread(target=_feed, daemon=True).start()

    streamed: list[str] = []
    final = ""
    is_error = False
    timer = threading.Timer(CLAUDE_TIMEOUT, proc.kill)
    timer.start()
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = event.get("type")

            if etype == "stream_event":
                inner = event.get("event") or {}
                if inner.get("type") == "content_block_delta":
                    delta = inner.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        chunk = delta.get("text") or ""
                        if chunk:
                            streamed.append(chunk)
                            if on_delta:
                                on_delta(chunk)

            elif etype == "assistant" and not streamed:
                # Pas de deltas partiels : on récupère le message complet.
                for block in (event.get("message") or {}).get("content") or []:
                    if block.get("type") == "text":
                        chunk = block.get("text") or ""
                        streamed.append(chunk)
                        if on_delta:
                            on_delta(chunk)

            elif etype == "result":
                is_error = bool(event.get("is_error"))
                final = event.get("result") or ""
        proc.wait(timeout=10)
    except Exception as e:
        return False, f"Erreur pendant l'appel à Claude Code : {e}"
    finally:
        timer.cancel()

    text = "".join(streamed).strip() or final.strip()
    if is_error:
        return False, final.strip() or text or "Claude Code a renvoyé une erreur."
    if not text:
        err = (proc.stderr.read() or "").strip() if proc.stderr else ""
        return False, err or "Réponse vide de Claude Code."
    return True, text


# ──────────────────────────────────────────────────────────────────────────────
# Sessions de conversation
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Session:
    id: str
    transcript: list[dict] = field(default_factory=list)
    pending: Optional[dict] = None
    lock: threading.Lock = field(default_factory=threading.Lock)


_SESSIONS: dict[str, Session] = {}
_SESSIONS_LOCK = threading.Lock()


def get_session(session_id: str = "") -> Session:
    sid = session_id or str(uuid.uuid4())
    with _SESSIONS_LOCK:
        session = _SESSIONS.get(sid)
        if session is None:
            session = Session(id=sid)
            _SESSIONS[sid] = session
        return session


def reset_session(session_id: str) -> None:
    with _SESSIONS_LOCK:
        _SESSIONS.pop(session_id, None)


def _auto_approves(kind: str) -> bool:
    """Les actions non destructives peuvent être auto-validées si l'utilisateur l'a choisi."""
    if kind == "danger":
        return False
    from config import get_ai_auto_run
    return bool(get_ai_auto_run())


def _stream_call(prompt: str, system_prompt: str, emit: Callable[[dict], dict]):
    """
    Appelle Claude dans un thread et relaie chaque fragment de texte en événement `delta`.
    Générateur : `ok, text = yield from _stream_call(...)`.
    """
    import queue
    q: "queue.Queue[tuple[str, Any]]" = queue.Queue()

    def worker():
        try:
            res = call_claude(prompt, system_prompt, on_delta=lambda c: q.put(("delta", c)))
        except Exception as e:  # pragma: no cover
            res = (False, str(e))
        q.put(("end", res))

    threading.Thread(target=worker, daemon=True).start()
    while True:
        kind, payload = q.get()
        if kind == "delta":
            yield emit({"type": "delta", "text": payload})
        else:
            return payload


def converse(session: Session, user_message: str = "",
             resume_with: Optional[dict] = None) -> Iterator[dict]:
    """
    Déroule un tour de conversation et produit des événements pour l'interface.

    user_message : nouveau message de l'utilisateur.
    resume_with  : {"approved": bool} pour reprendre après validation d'une action.
    """
    events: list[dict] = []

    def emit(evt: dict) -> dict:
        events.append(evt)
        return evt

    if user_message:
        session.transcript.append({"role": "user", "content": user_message})

    if resume_with is not None:
        action = session.pending
        session.pending = None
        if not action:
            yield emit({"type": "error", "message": "Aucune action en attente."})
            yield emit({"type": "done"})
            return
        if resume_with.get("approved"):
            yield emit({"type": "tool", "name": action["tool"],
                        "args": action.get("args") or {},
                        "kind": TOOLS[action["tool"]].kind_for(action.get("args") or {})
                        if action["tool"] in TOOLS else "write"})
            result = run_tool(action["tool"], action.get("args") or {})
            yield emit({"type": "tool_result", "name": action["tool"],
                        "ok": result["ok"], "message": result["message"]})
            session.transcript.append({
                "role": "tool", "name": action["tool"],
                "content": json.dumps(result, ensure_ascii=False, default=str)[:4000],
            })
        else:
            session.transcript.append({
                "role": "tool", "name": action["tool"],
                "content": "L'utilisateur a refusé cette action. Ne la relance pas ; "
                           "demande-lui ce qu'il préfère.",
            })

    system_prompt = build_system_prompt()

    for _ in range(MAX_TOOL_ITERATIONS):
        yield emit({"type": "thinking"})

        ok, text = yield from _stream_call(build_prompt(session.transcript), system_prompt, emit)
        if not ok:
            yield emit({"type": "error", "message": text})
            yield emit({"type": "done"})
            return

        prose, action = parse_action(text)
        session.transcript.append({"role": "assistant", "content": text})
        yield emit({"type": "message", "text": prose or "…"})

        if not action:
            yield emit({"type": "done"})
            return

        name = action["tool"]
        spec = TOOLS.get(name)
        if not spec:
            session.transcript.append({
                "role": "tool", "name": name,
                "content": f"Outil inconnu : {name}. Utilise uniquement le catalogue fourni.",
            })
            continue

        kind = spec.kind_for(action.get("args") or {})
        if kind == "read" or _auto_approves(kind):
            yield emit({"type": "tool", "name": name, "args": action.get("args") or {},
                        "kind": kind})
            result = run_tool(name, action.get("args") or {})
            yield emit({"type": "tool_result", "name": name,
                        "ok": result["ok"], "message": result["message"]})
            session.transcript.append({
                "role": "tool", "name": name,
                "content": json.dumps(result, ensure_ascii=False, default=str)[:4000],
            })
            continue

        session.pending = action
        yield emit({"type": "approval", "action": action, "kind": kind,
                    "preview": action_preview(action)})
        return

    yield emit({"type": "error", "message": "Trop d'étapes enchaînées, conversation interrompue."})
    yield emit({"type": "done"})


# ──────────────────────────────────────────────────────────────────────────────
# Auto-test hors ligne : vérifie le catalogue, le parseur et la boucle,
# sans appeler le modèle.   python3 ai_agent.py --selftest
# ──────────────────────────────────────────────────────────────────────────────

def _selftest() -> int:
    failures = []

    def check(label: str, cond: bool, detail: str = ""):
        print(("  ok   " if cond else "  FAIL ") + label + (f"  → {detail}" if detail and not cond else ""))
        if not cond:
            failures.append(label)

    print("Catalogue d'outils")
    check("au moins 10 outils", len(TOOLS) >= 10, str(len(TOOLS)))
    check("kinds valides", all(t.kind in ("read", "write", "danger") for t in TOOLS.values()))
    check("delete_database est destructif", TOOLS["delete_database"].kind == "danger")
    check("list_databases est en lecture", TOOLS["list_databases"].kind == "read")
    check("previews sans exception",
          all(TOOLS[n].preview is None or isinstance(TOOLS[n].preview({"db": "x", "args": ["-d", "x"]}), str)
              for n in TOOLS))

    print("\nParseur de bloc d'action")
    prose, action = parse_action('Je liste les bases.\n\n```odoo-action\n{"tool": "list_databases", "args": {}}\n```')
    check("bloc odoo-action reconnu", action == {"tool": "list_databases", "args": {}}, str(action))
    check("prose nettoyée", prose == "Je liste les bases.", repr(prose))

    _, action = parse_action('```json\n{"tool": "delete_database", "args": {"db": "demo"}}\n```')
    check("bloc json accepté", action and action["args"]["db"] == "demo", str(action))

    prose, action = parse_action("Rien à faire ici.")
    check("réponse sans action", action is None and prose == "Rien à faire ici.")

    _, action = parse_action('```odoo-action\n{"pas_un_outil": 1}\n```')
    check("bloc sans clé tool ignoré", action is None)

    _, action = parse_action('```odoo-action\n{"tool": "x", "args"\n```')
    check("JSON cassé ignoré", action is None)

    two = ('a\n```odoo-action\n{"tool":"list_databases","args":{}}\n```\n'
           'b\n```odoo-action\n{"tool":"list_branches","args":{}}\n```')
    _, action = parse_action(two)
    check("dernier bloc retenu si plusieurs", action["tool"] == "list_branches", str(action))

    print("\nNormalisation des paramètres")
    check("liste depuis chaîne", _as_list("sale, crm") == ["sale", "crm"])
    check("liste depuis liste", _as_list(["sale", " crm "]) == ["sale", "crm"])
    check("liste depuis None", _as_list(None) == [])

    print("\nGarde-fous")
    ok, msg, _ = _t_odoo_cli(args=["-d", "demo; rm -rf /"])
    check("argument shell refusé", not ok and "refusé" in msg, msg)
    ok, msg, _ = _t_odoo_cli(args=[])
    check("args vide refusé", not ok, msg)
    res = run_tool("outil_inexistant", {})
    check("outil inconnu géré", not res["ok"] and "inconnu" in res["message"])
    res = run_tool("database_info", {"parametre_bidon": 1})
    check("paramètre invalide géré", not res["ok"], res["message"])
    res = run_tool("database_info", {"db": ""})
    check("db vide refusée", not res["ok"], res["message"])

    print("\nBoucle de conversation (modèle simulé)")
    scripted = [
        'Je regarde les bases.\n```odoo-action\n{"tool": "list_databases", "args": {}}\n```',
        'Je vais supprimer demo.\n```odoo-action\n{"tool": "delete_database", "args": {"db": "demo"}}\n```',
        "C'est fait.",
    ]
    calls = {"n": 0}

    def fake_call(prompt, system_prompt, on_delta=None):
        text = scripted[min(calls["n"], len(scripted) - 1)]
        calls["n"] += 1
        if on_delta:
            on_delta(text)
        return True, text

    def fake_read_tool(name, args):
        return {"ok": True, "message": "2 base(s).", "data": {"databases": []}}

    real_call, real_run = globals()["call_claude"], globals()["run_tool"]
    globals()["call_claude"] = fake_call
    globals()["run_tool"] = fake_read_tool
    try:
        session = Session(id="test")
        events = list(converse(session, "supprime la base demo"))
        types = [e["type"] for e in events]
        check("lecture auto-exécutée", "tool_result" in types, str(types))
        check("suppression mise en attente", types[-1] == "approval", str(types))
        approval = events[-1]
        check("aperçu de suppression explicite", "SUPPRIMER" in approval["preview"], approval["preview"])
        check("action mémorisée", session.pending["tool"] == "delete_database")

        events = list(converse(session, resume_with={"approved": False}))
        types = [e["type"] for e in events]
        check("refus → pas d'exécution", "tool_result" not in types, str(types))
        check("refus → conversation poursuivie", types[-1] == "done", str(types))
        check("refus consigné", any(e["role"] == "tool" and "refusé" in e["content"]
                                    for e in session.transcript))
        check("plus d'action en attente", session.pending is None)

        calls["n"] = 0
        session2 = Session(id="test2")
        list(converse(session2, "supprime la base demo"))
        events = list(converse(session2, resume_with={"approved": True}))
        check("validation → exécution", any(e["type"] == "tool_result" for e in events), str(events))
    finally:
        globals()["call_claude"] = real_call
        globals()["run_tool"] = real_run

    print("\nodoo-bin CLI")
    check("sous-commande détectée", _split_subcommand(["cloc", "-d", "x"]) == ("cloc", ["-d", "x"]))
    check("options serveur sans sous-commande", _split_subcommand(["-d", "x"])[0] == "")
    check("--stop-after-init → capture", _odoo_cli_mode("", ["-u", "sale", "--stop-after-init"]) == "capture")
    check("serveur → terminal", _odoo_cli_mode("", ["-d", "x"]) == "terminal")
    check("shell → terminal", _odoo_cli_mode("shell", ["-d", "x"]) == "terminal")
    check("help → capture", _odoo_cli_mode("db", ["--help"]) == "capture")
    check("-d lu", _arg_value(["-d", "abc"], "-d", "--database") == "abc")
    check("--database= lu", _arg_value(["--database=abc"], "-d", "--database") == "abc")
    check("db drop destructif", TOOLS["odoo_cli"].kind_for({"args": ["db", "drop", "x"]}) == "danger")
    check("neutralize destructif", TOOLS["odoo_cli"].kind_for({"args": ["neutralize", "-d", "x"]}) == "danger")
    check("-u sale non destructif", TOOLS["odoo_cli"].kind_for({"args": ["-d", "x", "-u", "sale"]}) == "write")
    check("nom de base valide", bool(DB_NAME_RE.match("my_db-19.0")))
    check("nom de base invalide", not DB_NAME_RE.match('x"; drop'))
    check("outils de modification présents",
          all(n in TOOLS for n in ("duplicate_database", "rename_database", "sanitize_database", "set_database_port")))
    print("\nPrompt")
    sys_prompt = SYSTEM_PROMPT.format(tools=_tools_doc(), snapshot="{}")
    check("outils listés dans le prompt", "`delete_database`" in sys_prompt)
    check("protocole présent", "odoo-action" in sys_prompt)
    check("accolades du bloc correctes", '{"tool": "nom_de_outil"' in sys_prompt)
    transcript = [{"role": "user", "content": "salut"},
                  {"role": "assistant", "content": "bonjour"},
                  {"role": "tool", "name": "list_databases", "content": "{}"}]
    prompt = build_prompt(transcript)
    check("transcript rejoué", "Utilisateur : salut" in prompt and "[Résultat de list_databases]" in prompt)

    print("\nDétection Claude Code")
    st = auth_status()
    check("statut structuré", {"cli_installed", "authenticated", "message"} <= set(st))
    print(f"       cli={st['cli_path'] or 'introuvable'} auth={st['authenticated']} "
          f"method={st['method'] or '-'}")

    print()
    if failures:
        print(f"{len(failures)} test(s) en échec : {', '.join(failures)}")
        return 1
    print("Tous les tests passent.")
    return 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print(json.dumps(auth_status(probe="--probe" in sys.argv), ensure_ascii=False, indent=2))
