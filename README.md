<div align="center">

<img src="docs/icon.png" width="72" alt="">

# Odoo Database Manager

**Create, update and run your local Odoo databases in one click.**

A macOS app (menu bar + window) that drives your Odoo clones, pyenv environments and PostgreSQL for you.

![macOS](https://img.shields.io/badge/macOS-1a1a1a?style=flat-square&logo=apple&logoColor=white)
![Odoo 18 · 19](https://img.shields.io/badge/Odoo-18_·_19-714B67?style=flat-square)
![Updates](https://img.shields.io/badge/updates-one_click-2fc941?style=flat-square)

</div>

<br>

## Install

> [!IMPORTANT]
> You need a **Mac** with your Odoo setup: the `core/` and `enterprise/` clones, **pyenv** (`odoo-18`, `odoo-19`) and **PostgreSQL**. Everything else is installed for you.

**1. Open the Terminal app.** Press <kbd>⌘</kbd> <kbd>Space</kbd>, type `Terminal`, press <kbd>Return</kbd>.

**2. Copy this line, paste it in the Terminal, press <kbd>Return</kbd>.**

```bash
curl -fsSL https://raw.githubusercontent.com/tocodoo/odoo-db-manager/main/install.sh | bash
```

It takes a minute or two. When it is done, the app opens and its icon appears in the menu bar.

> [!TIP]
> If a window asks to install the **Command Line Tools**, click **Install**. The Terminal waits for it, then continues on its own.

**3. Check the Settings tab** the first time: Odoo folder, scripts folder, pyenv root, `psql` path (empty = auto-detected).

That's it, you only do this once.

## Use

- **Create a database**: four steps: environment and branch, launch script, database name and modules, then custom addons or a dump. Click **Create / Update**.
- **My databases**: start, stop, restart, open, clear module locks, change the port.
- **Menu bar**: the running databases, with **Open**, **Restart** and **Disconnect**.

Each database gets its own launch script (`.sh`), opened in Warp, Terminal or iTerm2.

## Updates

Nothing to download. When a new version is out, the app shows **Update available**: click **Update**, the app restarts on the new version.

From the menu bar: **Check for updates…**

## Something wrong?

| Problem | What to do |
|---|---|
| The app does not open | Paste the install line again in the Terminal. |
| The update fails | Paste the install line again in the Terminal. |
| *"PostgreSQL (psql) not found"* | Install [Postgres.app](https://postgresapp.com) or `brew install postgresql`, or set the `psql` path in Settings. |
| *"pyenv not found"* | Install [pyenv](https://github.com/pyenv/pyenv) and create the `odoo-18` / `odoo-19` environments. |
| Anything else | [Open an issue](https://github.com/tocodoo/odoo-db-manager/issues) and attach `~/Library/Logs/Odoo Database Manager/update.log` if it exists. |

<br>

<details>
<summary><b>Features</b></summary>

<br>

- **Databases**: create a database if it does not exist, update or reinitialize it with the chosen modules (`-i` / `-u`), clear module locks after an interrupted update.
- **Versions and branches**: Odoo `18` / `19` and SaaS branches, switch `core` + `enterprise` together, check they are on the same branch.
- **Runtime**: the right pyenv environment, the `addons_path` built for you (standard addons + your extra folders), one launch script per database.
- **Website scaffold**: generate a website theme module for Odoo 18 or 19.
- **Translations**: export a module's `.po` and translate it with AI (Claude, OpenAI, Gemini).

</details>

<details>
<summary><b>What the install does</b></summary>

<br>

[`install.sh`](install.sh) checks each prerequisite and installs what is missing, from the official sources:

| | Installed if missing |
|---|---|
| **Git** | Apple's Command Line Tools |
| **Python** 3.10+ | Homebrew if you have it, otherwise python.org (signature checked) |

Then it clones this repository into `~/odoodbmanager` (the app updates itself through git), creates a Python environment in `venv/`, installs the dependencies, and puts a light **Odoo Database Manager.app** in `/Applications` (or `~/Applications` without admin rights) that runs the code of the clone. Running it again updates everything; the app's **Update** button does a `git pull` then runs it with `--update`.

If an older version was installed with the `.pkg`, it is replaced (macOS asks for your password once).

Change the clone folder with `ODOO_DB_MANAGER_DIR`.

To uninstall: delete `/Applications/Odoo Database Manager.app` and `~/odoodbmanager`. Your settings are in `~/Library/Application Support/Odoo Database Manager`.

</details>

<details>
<summary><b>Settings</b></summary>

<br>

Stored in `~/Library/Application Support/Odoo Database Manager/config.json`.

| Setting | Description | Default |
|---|---|---|
| Odoo folder | Root folder containing `core/` and `enterprise/` | `~/odoo` |
| Scripts folder | One or several paths (comma-separated) | `~/Scripts` |
| pyenv root | Folder containing `versions/` | `~/.pyenv` |
| psql path | `psql` executable (empty = auto-detected) | *(auto)* |
| Terminal | Warp, Terminal or iTerm2 | Warp |
| Odoo HTTP port | Odoo listening port | `8069` |

Environment variables (override the settings): `ODOO_DB_MANAGER_PATH`, `ODOO_DB_MANAGER_SCRIPTS` (comma-separated), `PYENV_ROOT`.

</details>

<details>
<summary><b>Command line</b></summary>

<br>

```bash
cd ~/odoodbmanager
venv/bin/python app.py create mydb -m website,website_sale -b 19.0   # create / update a database
venv/bin/python app.py branch 19.0                                   # switch core + enterprise
venv/bin/python app.py check                                         # check both are on the same branch
venv/bin/python app.py locks mydb                                    # clear module locks
venv/bin/python app.py branches                                      # list the branches
```

</details>

<details>
<summary><b>For developers</b></summary>

<br>

```
install.sh             One-command install and update
app.py                 Entry point: GUI (no argument) or CLI
app_web.py             Flask server + pywebview window
menubar.py             Menu bar (rumps)
odoo_ops.py            Odoo, git, pyenv and PostgreSQL operations
config.py              User settings
version.py             Version and git-based updates
scaffold_generator.py  Website theme generator (website_scaffold, website_scaffold_18)
ai_translate.py        .po translation with AI
templates/index.html   The interface
setup.py               py2app alias build used by install.sh
release/latest.json    Read by versions ≤ 1.4 only, sends them to this page
```

Run from your clone without building the app: `./install.sh` once, then `venv/bin/python app.py`.

Release: push to `main`. Installed apps see the new commits and offer the update. Bump `APP_VERSION` in `version.py` for a visible version change.

</details>
