"""Build: python setup.py py2app"""
from setuptools import setup

from version import APP_VERSION

APP = ["app.py"]
def _scaffold_data_files():
    """Inclut les templates website_scaffold (19) et website_scaffold_18 dans le bundle .app."""
    import os

    base = os.path.dirname(os.path.abspath(__file__))
    entries = []
    for folder in ("website_scaffold", "website_scaffold_18"):
        root = os.path.join(base, folder)
        if not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            files = [
                os.path.join(dirpath, name)
                for name in filenames
                if name != ".DS_Store"
            ]
            if files:
                rel_dest = os.path.relpath(dirpath, base)
                entries.append((rel_dest, files))
    return entries


DATA_FILES = [
    ("templates", ["templates/index.html"]),
] + _scaffold_data_files()
OPTIONS = {
    "iconfile": "Odoo.icns",
    "argv_emulation": False,
    "packages": [
        "flask",
        "jinja2",
        "markupsafe",
        "werkzeug",
        "webview",
        "psutil",
        "rumps",
        "config",
        "odoo_ops",
        "ai_translate",
        "menubar",
        "version",
        "app_web",
        "scaffold_generator",
        "keyring",
        "keyring.backends",
    ],
    "plist": {
        "CFBundleName": "Odoo Database Manager",
        "CFBundleDisplayName": "Odoo Database Manager",
        "CFBundleIdentifier": "com.odoo.dbmanager",
        "CFBundleVersion": APP_VERSION,
        "CFBundleShortVersionString": APP_VERSION,
        "NSHighResolutionCapable": True,
        "NSAppleEventsUsageDescription": "Pour lancer Odoo dans le terminal.",
    },
}

setup(
    name="Odoo Database Manager",
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
