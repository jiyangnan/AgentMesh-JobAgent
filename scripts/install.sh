#!/usr/bin/env bash
# Job Agent CLI installer.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/jiyangnan/AgentMesh-JobAgent/main/scripts/install.sh | bash
#
# Or, if you've already cloned the repo:
#   bash scripts/install.sh
#
# What it does:
# 1. Verifies prerequisites (Python ≥ 3.11, git, Chrome)
# 2. Clones the repo to ~/.local/share/job-agent (if running via curl)
# 3. Sets up an isolated venv at ~/.local/share/job-agent/.venv
# 4. Installs the CLI in editable mode and marks it as an official managed install
# 5. Adds a `jobagent` shim at ~/.local/bin/jobagent (PATH-friendly)
#
set -euo pipefail

REPO_URL="${JOBAGENT_REPO_URL:-https://github.com/jiyangnan/AgentMesh-JobAgent.git}"
INSTALL_DIR="${JOBAGENT_INSTALL_DIR:-$HOME/.local/share/job-agent}"
BIN_DIR="${JOBAGENT_BIN_DIR:-$HOME/.local/bin}"

err() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '\033[34m▶\033[0m %s\n' "$*"; }
ok() { printf '\033[32m✓\033[0m %s\n' "$*"; }

# 1. Prereqs
command -v git >/dev/null || err "git not found. Install git first."
command -v python3 >/dev/null || err "python3 not found. Install Python 3.11+."

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJ=${PY_VER%%.*}
PY_MIN=${PY_VER##*.}
if [ "$PY_MAJ" -lt 3 ] || { [ "$PY_MAJ" -eq 3 ] && [ "$PY_MIN" -lt 11 ]; }; then
    err "Python 3.11+ required (you have $PY_VER). Install a newer Python."
fi
ok "Python $PY_VER"

# Chrome detection (warn-only; macOS / Linux / Windows)
case "$(uname -s)" in
    Darwin)
        if [ -d "/Applications/Google Chrome.app" ]; then
            ok "Chrome installed"
        else
            info "⚠️  Google Chrome not found. Install it before running a platform login command."
        fi
        ;;
    Linux)
        if command -v google-chrome >/dev/null || command -v google-chrome-stable >/dev/null; then
            ok "Chrome installed"
        else
            info "⚠️  Google Chrome not found. Install it before running a platform login command."
        fi
        ;;
esac

# 2. Clone or update repo
if [ -d "$INSTALL_DIR/.git" ]; then
    info "Updating existing checkout at $INSTALL_DIR"
    [ -z "$(git -C "$INSTALL_DIR" status --porcelain)" ] || err "Existing install has local changes; automatic bootstrap update refused."
    git -C "$INSTALL_DIR" fetch origin main --tags
    git -C "$INSTALL_DIR" checkout --detach origin/main
else
    info "Cloning Job Agent into $INSTALL_DIR"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone "$REPO_URL" "$INSTALL_DIR"
fi
ok "Repo at $INSTALL_DIR"

# 3. Venv
if [ ! -d "$INSTALL_DIR/.venv" ]; then
    info "Creating venv"
    python3 -m venv "$INSTALL_DIR/.venv"
fi
ok "venv at $INSTALL_DIR/.venv"

# 4. Install package
info "Installing dependencies (this may take a minute)"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip --quiet
"$INSTALL_DIR/.venv/bin/pip" install -e "$INSTALL_DIR" --quiet
ok "CLI installed"

cat > "$INSTALL_DIR/.jobagent-install.json" <<EOF
{
  "managed": true,
  "install_type": "official-installer",
  "repository": "$REPO_URL",
  "install_dir": "$INSTALL_DIR"
}
EOF
ok "Managed install metadata written"

# 5. Shim
mkdir -p "$BIN_DIR"
SHIM="$BIN_DIR/jobagent"
cat > "$SHIM" <<EOF
#!/usr/bin/env bash
exec "$INSTALL_DIR/.venv/bin/python" -m jobagent "\$@"
EOF
chmod +x "$SHIM"
ok "Shim at $SHIM"

# 6. PATH check
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        info "Add $BIN_DIR to your PATH:"
        echo "    echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.zshrc"
        echo "    source ~/.zshrc"
        ;;
esac

cat <<EOF

==========================================
  Job Agent installed successfully
==========================================

Next steps:

1. Create an account and API Key at https://agentmesh360.com/app/.

2. Initialize:
     jobagent init --key <your_api_key>

3. Verify environment:
     jobagent doctor env

4. Analyze your resume:
     jobagent resume analyze --file ~/Downloads/your-resume.pdf

5. Start the four-platform round:
     jobagent round start

6. Follow the current platform:
     jobagent boss login --check

7. Read the full guide:
     $INSTALL_DIR/README.md

EOF
