"""Build: python setup.py py2app"""
from setuptools import setup

from version import APP_VERSION

APP = ["app.py"]
DATA_FILES = [
    ("templates", ["templates/index.html"]),
]
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
        "menubar",
        "version",
        "app_web",
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
