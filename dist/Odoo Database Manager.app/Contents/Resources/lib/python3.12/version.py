"""Version de l'application et vérification des mises à jour."""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Any, Optional

# Source unique — aligner setup.py et latest.json à chaque release.
APP_VERSION = "1.4"

# Manifeste hébergé sur tocotools (à ajuster si l'URL change).
UPDATE_MANIFEST_URL = os.environ.get(
    "ODOO_DB_MANAGER_UPDATE_MANIFEST_URL",
    "https://raw.githubusercontent.com/tocodoo/odoo-db-manager/main/release/latest.json",
    )

_VERSION_RE = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def _parse_version(version: str) -> tuple[int, int, int]:
    version = (version or "").strip()
    if not version:
        return (0, 0, 0)
    m = _VERSION_RE.match(version.split("-")[0].split("+")[0])
    if not m:
        return (0, 0, 0)
    return tuple(int(g or 0) for g in m.groups())


def version_gt(a: str, b: str) -> bool:
    """True si a est strictement plus récent que b."""
    return _parse_version(a) > _parse_version(b)


def get_app_version() -> str:
    """Version installée (plist du bundle macOS ou APP_VERSION en dev)."""
    if getattr(sys, "frozen", False):
        try:
            import plistlib

            exe = os.path.dirname(sys.executable)
            plist_path = os.path.join(exe, "..", "Info.plist")
            plist_path = os.path.normpath(plist_path)
            if os.path.isfile(plist_path):
                with open(plist_path, "rb") as f:
                    info = plistlib.load(f)
                v = info.get("CFBundleShortVersionString") or info.get("CFBundleVersion")
                if v:
                    return str(v).strip()
        except Exception:
            pass
    return APP_VERSION


def fetch_update_manifest(url: Optional[str] = None, timeout: float = 8.0) -> dict[str, Any]:
    """Télécharge et parse latest.json."""
    manifest_url = (url or UPDATE_MANIFEST_URL).strip()
    if not manifest_url:
        return {"error": "URL du manifeste non configurée"}

    req = urllib.request.Request(
        manifest_url,
        headers={"User-Agent": f"Odoo-DB-Manager/{get_app_version()}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        return {"error": str(e.reason) if getattr(e, "reason", None) else str(e)}
    except Exception as e:
        return {"error": str(e)}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "Réponse JSON invalide"}

    if not isinstance(data, dict):
        return {"error": "Format manifeste invalide"}
    return data


def check_for_update(
    dismissed_version: str = "",
    manifest_url: Optional[str] = None,
) -> dict[str, Any]:
    """Compare la version locale au manifeste distant."""
    current = get_app_version()
    manifest = fetch_update_manifest(manifest_url)
    if manifest.get("error"):
        return {
            "ok": False,
            "current_version": current,
            "update_available": False,
            "error": manifest["error"],
            "manifest_url": manifest_url or UPDATE_MANIFEST_URL,
        }

    latest = str(manifest.get("version") or "").strip()
    min_version = str(manifest.get("min_version") or "").strip()
    download_url = str(manifest.get("download_url") or "").strip()
    release_notes = str(manifest.get("release_notes") or "").strip()

    needs_update = bool(latest) and version_gt(latest, current)
    if min_version and version_gt(min_version, current):
        needs_update = True

    dismissed = (dismissed_version or "").strip()
    if needs_update and dismissed and dismissed == latest:
        needs_update = False

    return {
        "ok": True,
        "current_version": current,
        "latest_version": latest,
        "update_available": needs_update,
        "download_url": download_url,
        "release_notes": release_notes,
        "published_at": manifest.get("published_at"),
        "manifest_url": manifest_url or UPDATE_MANIFEST_URL,
    }
