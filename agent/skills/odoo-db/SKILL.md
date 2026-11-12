---
name: odoo-db
description: Create, start, stop, inspect, diagnose and repair the local Odoo databases of this Mac with the `odoodb` command. Use it whenever the user wants a local Odoo database (new, restored from a backup/dump, duplicated, deleted), wants to run/launch/restart Odoo on a database, asks which databases exist or what version / modules / repos a database has, reads Odoo server logs, has a database that won't start or is broken (module stuck "to upgrade", wrong branch, missing module code, port busy, broken assets), wants to update/install modules (-u / -i), switch core/enterprise branch, or translate a module (.po). Prefer it over calling odoo-bin, psql, createdb or git checkout by hand.
---

# Local Odoo databases: `odoodb`

`odoodb` drives the developer's local Odoo setup: one `core/` (community) + `enterprise/` checkout,
pyenv environments `odoo-<major>`, PostgreSQL, one launch script per database. The macOS app
"Odoo Database Manager" is only a monitor of what `odoodb` does, so anything you do here shows up there.

Always pass `--json`: the result is JSON on stdout (`"ok": false` + `"error"`, `"hint"`, `"code"` on
failure), progress lines go to stderr. Follow the `hint` of an error, it says what to run next.

## Look first

```bash
odoodb --json list                 # every database: real version (series), running, port, url, size, custom_addons
odoodb --json info <db>            # repos (branch, commit, changes), modules, script, issues, url, log path
odoodb --json info <db> --modules  # + every module with state and source (community/enterprise/custom/missing)
odoodb --json ps                   # running Odoo servers (port, pid, memory, uptime)
odoodb --json branches             # core branches + the one checked out
odoodb --json branches --remote    # + version branches on GitHub not fetched yet ("remote_only")
odoodb --json setup                # what this Mac needs: PostgreSQL, clones, GitHub, Python envs, wkhtmltopdf
```

The database version is read from the database itself (`series`, e.g. `19.0`, `saas~19.4`);
`code_series` is what core/enterprise currently run. If they differ the database cannot run until
the repos are switched.

## Create

```bash
odoodb --json create <db> -b 19.0 -m sale_management,website            # fresh, then started (admin/admin)
odoodb --json create <db> -b saas-19.4 -m my_module -a ~/odoo/psbe-custom # custom addons folder(s)
odoodb --json create <db> --dump ~/Downloads/backup.zip                    # Odoo backup .zip, .sql or pg_dump
odoodb --json create <db> -m crm --no-start                                # initialise only
```

- Waits until Odoo is up and returns `url` (login `admin` / `admin`).
- Modules that live in a folder under the Odoo directory (`~/odoo/*`) are found automatically; pass
  `-a` only for folders elsewhere. A module found nowhere fails with `module_not_found`.
- Switching branch is refused while another database runs (`code: "busy"`): tell the user, and only
  use `--force` if they agree (the other database would run on the wrong code).
- Restored dumps are neutralized (admin/admin, crons and mail servers off) unless `--no-sanitize`.

## Run

```bash
odoodb --json start <db>                    # background, waits until up, returns url + log path
odoodb --json start <db> --switch-branch    # when it failed with code "branch_mismatch"
odoodb --json stop <db>        # or: stop --all
odoodb --json restart <db>
odoodb --json logs <db> -n 200              # add --errors for errors/tracebacks only
odoodb --json history <db> --errors         # past errors/tracebacks, folded with counts (survives restarts)
odoodb open <db>                            # browser
odoodb --json port <db> 8070                # run several databases at once
```

A failed start returns `log_tail` and `errors`: read them before anything else.

## Diagnose and repair

```bash
odoodb --json doctor <db>                   # issues, each with a "fix" id
odoodb --json repair <db>                   # apply every suggested fix (not "branch")
odoodb --json repair <db> --fix locks,assets
```

| fix | does |
|---|---|
| `locks` | resets modules stuck in to install / to upgrade / to remove (interrupted update) |
| `script` | (re)writes the launch script with the right branch and addons path |
| `addons` | adds the folders containing missing module code to the addons path |
| `branch` | checks out core + enterprise on the database's branch |
| `assets` | deletes compiled asset bundles (broken CSS/JS after an update) |
| `port` | moves the database to a free port |
| `admin` | resets user id 2 to admin / admin |
| `init` | installs `base` in an empty database |
| `env` | creates the database's pyenv environment (`odoo-<major>`) with its branch's requirements.txt |
| `pydeps` | pip installs, in that environment, what requirements.txt or the modules' `external_dependencies` lack |
| `wkhtmltopdf` | installs wkhtmltopdf 0.12.6 for the whole Mac (PDF reports; macOS asks the user's password) |

Typical "my database won't start": `doctor` → `repair` → `start` (with `--switch-branch` if asked) →
`logs --errors` if it still fails. Then fix the code or run `update`.

## Modules, translations, other

```bash
odoodb --json update <db> -m my_module      # -u, server must be stopped (stop it, update, start it again)
odoodb --json update <db>                   # -u all
odoodb --json install <db> -m sale
odoodb --json translate <db> -m my_module -l fr_FR    # .po written in the module's i18n/ folder
odoodb --json translate <db> -m my_module -l nl_BE --no-ai -o ~/Desktop
odoodb --json duplicate <db> <new>
odoodb --json rename <db> <new>
odoodb --json neutralize <db>
odoodb --json delete <db> --yes             # irreversible: only when the user explicitly asked
odoodb --json script <db>                   # launch script: branch, addons path, modules, port, python
odoodb --json script <db> -a <f1> <f2> --update my_mod --update-mode update   # edit it (next start)
odoodb --json reset <db> --yes              # drop + recreate fresh: same name, script, addons, port; started
odoodb --json reset <db> --yes --same-modules   # …reinstalling the modules installed now (-m a,b: these ones)
odoodb --json checkout saas-19.4            # core + enterprise
odoodb --json settings                      # paths in use (core, enterprise, scripts, pyenv, psql)
odoodb --json pull                          # git pull --ff-only core + enterprise (or given folders)
```

## This Mac: PostgreSQL, clones, GitHub, Python environments

```bash
odoodb --json setup                         # items with status ok/warning/error/optional and an "action"
odoodb --json setup --fix postgres-start    # or postgres-install (downloads Postgres.app)
odoodb --json setup --fix clone:enterprise  # shallow clones; other branches are fetched when switched to
odoodb --json setup --fix locate:enterprise --path ~/src/enterprise   # use a clone the user already has
odoodb --json setup --fix gh-setup-git      # git uses the gh sign-in (enterprise is private)
odoodb --json env list                      # odoo-<major> environments, Python, missing packages
odoodb --json env create odoo-19            # pyenv + Python 3.12 + requirements of 19.0 (minutes)
odoodb --json env install odoo-19 -p xlrd   # extra packages (default: the missing requirements)
```

Error code `postgres_down` → `setup --fix postgres-start` (`postgres-restart` when the error says
"the database system is shutting down"). Code `env_missing` → `repair <db> --fix env`.
`create` builds a missing environment by itself. `gh-login` and `wkhtmltopdf` need the user (terminal
sign-in, password dialog): tell them before running these.

## Rules

- Never `dropdb`, `git checkout` core/enterprise, or launch `odoo-bin` by hand for these tasks: `odoodb`
  keeps the launch scripts, ports, logs and the app consistent.
- Ask before `delete`, before `reset` (all data is lost), before `--force`, and before `neutralize` on a database the user didn't create for testing.
- After creating or starting, give the user the `url` and the login.
