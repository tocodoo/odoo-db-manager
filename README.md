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
> You need a **Mac (macOS 14+)**. What Odoo needs is checked and installed from the app's **Setup** (or `odoodb setup`): PostgreSQL (Postgres.app), the community and enterprise clones, the GitHub sign-in (`gh`), pyenv with one environment per Odoo version (`odoo-18`, `odoo-19`…) and wkhtmltopdf. An existing setup is detected and kept as is. No Xcode needed.

**1. Open the Terminal.** Press <kbd>⌘</kbd> <kbd>Space</kbd>, type `Terminal`, press <kbd>Return</kbd>.

**2. Paste this line and press <kbd>Return</kbd>.**

```bash
curl -fsSL https://raw.githubusercontent.com/tocodoo/odoo-db-manager/main/install.sh | bash
```

It takes about two minutes: the app is compiled on your Mac. If a window asks to install the **Command Line Tools**, click **Install**: the Terminal waits for it, then continues on its own. When it is done, the app opens and its icon appears in the menu bar.

**3. Open Setup** (the wrench in the toolbar, or **Settings › Setup**) and click the buttons of what is missing: **Install Postgres.app**, **Clone odoo/odoo**, **Sign In to GitHub**, **Create** an environment… Paths (community, enterprise, launch scripts, pyenv) are in **Settings › General**, detected from your previous settings if you used an older version.

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
  - **History**: every warning, error and traceback the server logged, folded with a count and first / last time, kept across restarts (`odoodb history <db>`).
  - **Terminal**: built-in terminal — run the server in the foreground (Ctrl+C, breakpoints, colored log, still captured for Logs and History), an Odoo shell, or zsh with the database's Python environment. **Start in Terminal** uses it; the external app (Warp, Terminal, iTerm2) stays available. Quitting the app stops what runs in it.
  - **Translate**: pick a module and a language, get the translated `.po` in the module's `i18n/` folder.
- **Setup**: PostgreSQL, clones and their access to GitHub, GitHub CLI / GitHub Desktop, pyenv environments and their missing packages, wkhtmltopdf — each with the button that installs or fixes it. A database page also warns when its environment lacks a package (from `requirements.txt` or a module's `external_dependencies`) or wkhtmltopdf is missing, and says where to install it.
- **Branches**: still one checkout switched per database; a branch not downloaded yet (single-branch or shallow clone) is fetched from GitHub when a database needs it. Repositories behind their remote get a **Pull** button, and open in GitHub Desktop or on github.com.
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
odoodb reset bike --yes                     # drop + recreate fresh, same name/script/addons/port
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
| *"Cannot reach PostgreSQL"* | **Setup › Start** (or `odoodb setup --fix postgres-start`). First install: click **Initialize** in the Postgres window. |
| *"the database system is shutting down"* (Postgres.app says "Running" but "does not accept passwordless local connections") | PostgreSQL waits for open connections before stopping: **Setup › Restart PostgreSQL** (`odoodb setup --fix postgres-restart`). |
| A database does not start | Open it: the Overview lists what is wrong. Or `odoodb doctor <db>`. |
| *"Python environment … not found"* | **Create** in Setup or on the database page (`odoodb env create odoo-19`). |
| Enterprise cannot be fetched | **Setup › Sign In to GitHub**, then **Use for git** (`gh auth setup-git`). |
| Anything else | [Open an issue](https://github.com/tocodoo/odoo-db-manager/issues) with `~/Library/Logs/Odoo Database Manager/build.log` or `update.log` if present. |

<br>

<details>
<summary><b>How it is built</b></summary>

<br>

| | |
|---|---|
| `odoodb.py` / `dbtools.py` | the command and its logic (monitoring, diagnosis, lifecycle). Single source of truth. |
| `odoo_ops.py`, `config.py` | git, pyenv, PostgreSQL helpers; settings in `~/Library/Application Support/Odoo Database Manager/config.json` |
| `toolchain.py` | what the Mac needs (`odoodb setup`, `odoodb env`): Postgres.app, clones, GitHub, pyenv environments, wkhtmltopdf |
| `macos/` | the SwiftUI app (Swift package, built with the Command Line Tools: `macos/build.sh`). It only calls `odoodb --json`. |
| `agent/` | the Claude Code skill, the MCP server, `install-agent.sh` |

To uninstall:

```bash
rm -rf "/Applications/Odoo Database Manager.app" ~/odoodbmanager ~/.local/bin/odoodb ~/.claude/skills/odoo-db
# and the line ending with "# odoodb" in ~/.zprofile
```

then remove the `odoo-databases` entry from `~/Library/Application Support/Claude/claude_desktop_config.json`. Your databases and launch scripts are not touched.

</details>
