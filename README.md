<div align="center">

<img src="docs/icon.png" width="72" alt="">

# Odoo Database Manager

**See every local Odoo database at a glance. Let your agents create, run and repair them.**

A native macOS app (SwiftUI) that monitors your databases, and the `odoodb` command that every Claude agent uses to drive them.

![macOS](https://img.shields.io/badge/macOS_14+-1a1a1a?style=flat-square&logo=apple&logoColor=white)
![Odoo 17 · 18 · 19 · saas](https://img.shields.io/badge/Odoo-17_·_18_·_19_·_saas-714B67?style=flat-square)
![Agents](https://img.shields.io/badge/Claude-skill_+_MCP-d97757?style=flat-square)

</div>

<br>

## Install

> [!IMPORTANT]
> You need a **Mac (macOS 14+)** with your Odoo setup: the community clone (with `odoo-bin`) and `enterprise/`, **pyenv** environments named `odoo-<version>` (`odoo-18`, `odoo-19`…) and **PostgreSQL** ([Postgres.app](https://postgresapp.com) or Homebrew). Everything else is installed for you. No Xcode needed.

**1. Open the Terminal.** Press <kbd>⌘</kbd> <kbd>Space</kbd>, type `Terminal`, press <kbd>Return</kbd>.

**2. Paste this line and press <kbd>Return</kbd>.**

```bash
curl -fsSL https://raw.githubusercontent.com/tocodoo/odoo-db-manager/main/install.sh | bash
```

It takes about two minutes: the app is compiled on your Mac. If a window asks to install the **Command Line Tools**, click **Install**: the Terminal waits for it, then continues on its own. When it is done, the app opens and its icon appears in the menu bar.

**3. Check the paths once** in **Settings** (<kbd>⌘</kbd> <kbd>,</kbd>): community folder (with `odoo-bin`), enterprise, launch scripts folder, pyenv. They are detected from your previous settings if you used an older version.

**4. Restart Claude Desktop** if you use it, so it picks up the `odoo-databases` MCP server. Claude Code needs nothing: new sessions see the skill.

That's it. Running the same line again repairs or updates everything.

### What the install does

| | |
|---|---|
| **Command Line Tools** | Git and Swift, from Apple, if missing |
| **Python 3.10+** | Homebrew if you have it, otherwise python.org (signature checked), if missing |
| **The clone** | this repository in `~/odoodbmanager` (`ODOO_DB_MANAGER_DIR` to change it), with a Python environment in `venv/` |
| **`odoodb`** | `~/.local/bin/odoodb`, the command agents and the app use |
| **Claude Code skill** | `~/.claude/skills/odoo-db` (a link to `agent/skills/odoo-db` in the clone) |
| **Claude Desktop** | the `odoo-databases` MCP server added to `claude_desktop_config.json` (only if Claude Desktop is installed) |
| **The app** | built from `macos/`, installed in `/Applications` (or `~/Applications` without admin rights) |

Your settings stay in `~/Library/Application Support/Odoo Database Manager/config.json`, your launch scripts where they are.

### Coming from version 2 (web app)

Paste the install line again: it replaces the old app. The web interface and the built-in AI assistant are gone; ask any Claude agent instead (see below). Your databases, launch scripts and settings are kept. The website theme generator is now `odoodb scaffold <name> -o <folder>`.

### From a clone (developers)

```bash
git clone https://github.com/tocodoo/odoo-db-manager.git && cd odoo-db-manager
./install.sh                  # everything, as above, but runs this clone
macos/build.sh                # only rebuild the app (into macos/build/)
agent/install-agent.sh        # only odoodb + skill + MCP
```

## The app: a monitor

- **Home**: every database as a card — real version (read from the database, not guessed), running state, port, memory, size, custom addons, last login. Search, filter (running, with addons, needing attention), sort. Hover a card to start, stop or open it.
- **Database page**:
  - **Overview**: health (version ≠ checked-out code, modules stuck *to upgrade*, installed modules without code, port busy, missing filestore…) with a **Fix** button for each, stats, the **repositories** of its addons path (branch, last commit, local changes, behind/ahead), custom modules, launch script.
  - **Modules**: every module with its version and where its code lives (community, enterprise, custom, *no code*). Select and **Update**.
  - **Logs**: live server log, errors highlighted.
  - **Translate**: pick a module and a language, get the translated `.po` in the module's `i18n/` folder.
- **Menu bar**: running databases with Open / Restart / Stop.
- Drop an Odoo backup (`.zip`, `.sql`, `.dump`) on the window to restore it.

## Agents: `odoodb`

Any Claude Code session knows how to create, start and repair your databases (the install adds the **`odoo-db` skill** to `~/.claude/skills`). Claude Desktop gets the same through the **`odoo-databases` MCP server**. Just ask:

> *Create an Odoo 19 database with website and my_module from ~/odoo/psbe-custom, then start it.*
> *The database bike won't start, fix it.*
> *Translate website_toco in Dutch (BE).*

Or use it yourself:

```bash
odoodb list                                 # all databases
odoodb info bike                            # repos, modules, issues, url…
odoodb create demo -b 19.0 -m sale,website  # created, started, admin/admin
odoodb create demo2 --dump ~/Downloads/backup.zip
odoodb start bike --switch-branch           # checks out the database's branch first
odoodb logs bike --errors
odoodb doctor bike && odoodb repair bike
odoodb update bike -m my_module
odoodb translate bike -m my_module -l fr_FR
odoodb --help
```

Add `--json` for machine-readable output. Databases started by `odoodb` (or the app) run in the background with their log in `~/Library/Logs/Odoo Database Manager/databases/`.

## Updates

`odoodb self-update`, or **Check for Updates…** in the app menu. Both `git pull` and rebuild.

## Something wrong?

| Problem | What to do |
|---|---|
| The app does not open | Paste the install line again in the Terminal. |
| *"Cannot reach PostgreSQL"* | Start [Postgres.app](https://postgresapp.com), or set the `psql` path in Settings. |
| A database does not start | Open it: the Overview lists what is wrong. Or `odoodb doctor <db>`. |
| *"pyenv environment not found"* | Create it with pyenv (`odoo-19`, `odoo-18`…). |
| Anything else | [Open an issue](https://github.com/tocodoo/odoo-db-manager/issues) with `~/Library/Logs/Odoo Database Manager/build.log` or `update.log` if present. |

<br>

<details>
<summary><b>How it is built</b></summary>

<br>

| | |
|---|---|
| `odoodb.py` / `dbtools.py` | the command and its logic (monitoring, diagnosis, lifecycle). Single source of truth. |
| `odoo_ops.py`, `config.py` | git, pyenv, PostgreSQL helpers; settings in `~/Library/Application Support/Odoo Database Manager/config.json` |
| `macos/` | the SwiftUI app (Swift package, built with the Command Line Tools: `macos/build.sh`). It only calls `odoodb --json`. |
| `agent/` | the Claude Code skill, the MCP server, `install-agent.sh` |

To uninstall:

```bash
rm -rf "/Applications/Odoo Database Manager.app" ~/odoodbmanager ~/.local/bin/odoodb ~/.claude/skills/odoo-db
```

then remove the `odoo-databases` entry from `~/Library/Application Support/Claude/claude_desktop_config.json`. Your databases and launch scripts are not touched.

</details>
