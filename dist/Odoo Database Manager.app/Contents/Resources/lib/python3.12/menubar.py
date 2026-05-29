#!/usr/bin/env python3
"""Menu bar status bar app pour Odoo Database Manager (style Postgres.app)."""
import os
import sys
import webbrowser
import subprocess

import rumps

from config import get_odoo_http_port
from odoo_ops import get_all_running_odoo_dbs, restart_odoo_server, stop_odoo_server

# Port Flask (doit correspondre à app_web)
MENUBAR_FLASK_PORT = 5151
APP_BUNDLE_ID = "com.odoo.dbmanager"


def _ensure_icon() -> str | None:
    """Crée ou trouve l'icône menu bar. Retourne le chemin ou None."""
    base = os.path.dirname(os.path.abspath(__file__))
    assets = os.path.join(base, "assets")
    for name in ("menubar_icon.png", "Odoo.png", "icon.png", "menubar_icon.ico"):
        p = os.path.join(assets, name)
        if os.path.exists(p):
            return p
    if getattr(sys, "frozen", False):
        res_dir = os.path.join(os.path.dirname(sys.executable), "..", "Resources")
        for name in ("Odoo.icns", "icon.icns"):
            p = os.path.join(res_dir, name)
            if os.path.exists(p):
                return p
    # Télécharger l'icône Odoo si absente
    try:
        import urllib.request
        os.makedirs(assets, exist_ok=True)
        url = "https://www.odoo.com/openerp_website/static/src/img/favicon.ico"
        req = urllib.request.Request(url, headers={"User-Agent": "Odoo-DB-Manager/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read()
        # Sauvegarder en .ico (rumps accepte peut-être .ico) ou convertir
        ico_path = os.path.join(assets, "menubar_icon.ico")
        with open(ico_path, "wb") as f:
            f.write(data)
        return ico_path
    except Exception:
        pass
    return None


class OdooMenubarApp(rumps.App):
    """App menu bar : bases en cours (Ouvrir/Déconnecter), Ouvrir l'app."""

    def __init__(self, port: int = MENUBAR_FLASK_PORT):
        icon = _ensure_icon()
        super().__init__("" if icon else "Odoo DB", icon=icon, quit_button=None)
        self.port = port
        self.app_url = f"http://127.0.0.1:{port}"
        self.odoo_url = f"http://localhost:{get_odoo_http_port()}"
        self._refresh_timer = rumps.Timer(self._refresh_menu, 2.0)
        self._refresh_timer.start()

    def _refresh_menu(self, _=None) -> None:
        """Met à jour le menu avec les bases en cours."""
        self.menu.clear()
        running = get_all_running_odoo_dbs()

        if running:
            for db in running:
                sub = rumps.MenuItem(db)
                sub.add(rumps.MenuItem("Ouvrir", callback=self._open_odoo))
                sub.add(rumps.MenuItem("Restart", callback=lambda s, d=db: self._restart_db(d)))
                sub.add(rumps.MenuItem("Déconnecter", callback=lambda s, d=db: self._stop_db(d)))
                self.menu.add(sub)
        else:
            self.menu.add(rumps.MenuItem("Aucune base en cours", callback=None))

        self.menu.add(None)  # separator
        self.menu.add(rumps.MenuItem("Ouvrir l'app", callback=self._open_app))
        self.menu.add(rumps.MenuItem("Réglages", callback=self._open_settings))
        self.menu.add(None)
        self.menu.add(rumps.MenuItem("Quitter", callback=self._quit_all))

    def _open_odoo(self, _) -> None:
        webbrowser.open(self.odoo_url)

    def _stop_db(self, db: str) -> None:
        ok, msg = stop_odoo_server(db)
        rumps.notification("Odoo DB Manager", msg, ok)

    def _restart_db(self, db: str) -> None:
        ok, msg = restart_odoo_server(db)
        rumps.notification("Odoo DB Manager", msg, ok)

    def _open_settings(self, _) -> None:
        """Met l'app au premier plan (l'utilisateur peut cliquer sur Réglages)."""
        self._activate_app()

    def _open_app(self, _) -> None:
        """Active la fenêtre existante (comme un clic sur l'app dans le dock)."""
        self._activate_app()

    def _activate_app(self) -> None:
        """Active l'application via bundle id (plus fiable que le nom)."""
        subprocess.run(
            ["/usr/bin/osascript", "-e", f'tell application id "{APP_BUNDLE_ID}" to activate'],
            check=False,
        )

    def _quit_all(self, _) -> None:
        """Quitte toute l'app (menubar + fenêtre)."""
        # Ne pas bloquer la fermeture de la menubar: quitter l'app globale en arrière-plan.
        subprocess.Popen(
            ["/usr/bin/osascript", "-e", f'tell application id "{APP_BUNDLE_ID}" to quit'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        rumps.quit_application()

    def run(self) -> None:
        self._refresh_menu()
        super().run()


def main(port: int = MENUBAR_FLASK_PORT) -> None:
    OdooMenubarApp(port=port).run()
