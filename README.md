# Odoo Database Manager

macOS app to create and manage local Odoo databases, with both a GUI (menu bar + web window) and CLI commands.

## Download

You can get the app quickly from:

- Website: [tocotools.odoo.com/odoo-db-manager](https://tocotools.odoo.com/odoo-db-manager)
- Local project output: `dist/` folder, by installing the `.pkg` file

## Features

### Database management

- Create an Odoo database if it does not exist
- Update/reinitialize an existing database with selected modules
- Clear module locks in the database (useful after interrupted updates)

### Version and branch management

- Select Odoo versions `18` / `19`
- SaaS branch support (branch list read from git repositories)
- Switch branches for `core` and `enterprise`
- Verify that `core` and `enterprise` are aligned on the same branch

### Odoo runtime management

- Auto-detect and use the right pyenv environment (`odoo-18` / `odoo-19`)
- Build `addons_path` automatically (standard addons + custom extras)
- Generate one launch `.sh` script per database
- Choose launch terminal app (Warp, Terminal, iTerm2)

### UI and usability

- Local web interface embedded in a macOS app
- Menu bar + window mode
- Module input by comma-separated list or one per line
- Persistent settings through a user config file

### Available CLI commands

- `create <db>`: create/update a database
- `branch <name>`: switch `core` + `enterprise` branch
- `check`: verify branch consistency
- `locks <db>`: clear module locks
- `branches`: list available git branches

## Minimum required configuration

### System

- macOS (Apple Silicon or Intel)
- Python 3.10+
- PostgreSQL installed with `psql` available

### Repositories and environments

- Odoo directory containing:
  - `core/`
  - `enterprise/`
- pyenv installed with at least:
  - `odoo-18`
  - `odoo-19`

### Minimum app setup

Set these values in the **Settings** tab (or config file):

1. **Odoo folder** (root containing `core/` and `enterprise/`)
2. **Scripts folder** (where launch scripts are generated)
3. **pyenv root**
4. **psql path** (leave empty if auto-detection works)

## Detailed configuration

User config file:
`~/Library/Application Support/Odoo Database Manager/config.json`

| Parameter        | Description                                           | Default     |
|------------------|-------------------------------------------------------|-------------|
| Odoo folder      | Root folder containing `core/` and `enterprise/`     | `~/odoo`    |
| Scripts folder   | One or multiple paths (comma-separated)              | `~/Scripts` |
| pyenv root       | Folder containing `versions/`                        | `~/.pyenv`  |
| psql path        | `psql` executable (empty = auto-detection)           | *(auto)*    |
| Terminal         | Warp, Terminal, or iTerm2                            | Warp        |
| Odoo HTTP port   | Odoo listening port                                   | 8069        |

Environment variables (override config values):

- `ODOO_DB_MANAGER_PATH`: Odoo path
- `ODOO_DB_MANAGER_SCRIPTS`: scripts paths (comma-separated)
- `PYENV_ROOT`: pyenv root

## Install from source

```bash
# Clone the repository
git clone https://github.com/YOUR_ORG/odoo-database-manager.git
cd odoo-database-manager

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run the app
python app.py
```

## Build (macOS app)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# .app + .pkg (pas de DMG)
./scripts/build.sh
```

Artifacts in `dist/`:

- `Odoo Database Manager.app`
- `Odoo-Database-Manager.pkg`

