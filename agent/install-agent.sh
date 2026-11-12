#!/bin/bash
# Makes `odoodb` available to every agent on this Mac:
#   - ~/.local/bin/odoodb               the command (runs this clone)
#   - ~/.claude/skills/odoo-db          Claude Code skill (every project, every session)
#   - MCP server "odoo-databases"       Claude Desktop (if installed)
# Safe to run again.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PY="$HERE/venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"

# 1. Command
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/odoodb" <<EOF
#!/bin/bash
exec "$PY" "$HERE/odoodb.py" "\$@"
EOF
chmod +x "$HOME/.local/bin/odoodb"
echo "✓ odoodb → $HOME/.local/bin/odoodb"
# ~/.local/bin is not in macOS's default PATH: add it to the login shell profiles (once), so `odoodb`
# works in new terminals and for agents. bash only when its profile exists or bash is the login shell.
PATH_LINE='export PATH="$HOME/.local/bin:$PATH"  # odoodb'
profiles=("$HOME/.zprofile")
if [[ -f "$HOME/.bash_profile" || "${SHELL:-}" == */bash ]]; then profiles+=("$HOME/.bash_profile"); fi
for profile in "${profiles[@]}"; do
  if ! grep -qs '\.local/bin' "$profile"; then
    printf '\n%s\n' "$PATH_LINE" >> "$profile"
    echo "✓ ~/.local/bin added to the PATH in ${profile/#$HOME/~} (new terminals)"
  fi
done

# 2. Claude Code skill (symlink: updates with the app)
mkdir -p "$HOME/.claude/skills"
rm -rf "$HOME/.claude/skills/odoo-db"
ln -s "$HERE/agent/skills/odoo-db" "$HOME/.claude/skills/odoo-db"
echo "✓ Claude Code skill → ~/.claude/skills/odoo-db"

# 3. MCP server
DESKTOP_CFG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
if [[ -d "$(dirname "$DESKTOP_CFG")" ]]; then
  "$PY" - "$DESKTOP_CFG" "$PY" "$HERE/agent/mcp_server.py" <<'PYEOF'
import json, os, sys
path, py, server = sys.argv[1:4]
cfg = {}
if os.path.exists(path):
    try:
        cfg = json.load(open(path))
    except ValueError:
        sys.exit("✗ claude_desktop_config.json is not valid JSON, left untouched")
cfg.setdefault("mcpServers", {})["odoo-databases"] = {"command": py, "args": [server]}
json.dump(cfg, open(path, "w"), indent=2)
print("✓ Claude Desktop MCP server 'odoo-databases' (restart Claude Desktop)")
PYEOF
fi
