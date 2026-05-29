#!/bin/bash
# Bibliothèque de logs pour preinstall / postinstall (sourcée, pas exécutée seule).

INSTALL_LOG="/var/log/Odoo-Database-Manager-install.log"
USER_LOG=""

_init_install_log() {
  local phase="${1:-install}"
  touch "$INSTALL_LOG" 2>/dev/null || INSTALL_LOG="/tmp/Odoo-Database-Manager-install.log"
  chmod 644 "$INSTALL_LOG" 2>/dev/null || true

  local console_user
  console_user="$(/usr/bin/stat -f '%Su' /dev/console 2>/dev/null || true)"
  if [[ -n "$console_user" && "$console_user" != "root" ]]; then
    local user_home
    user_home="$(/usr/bin/dscl . -read "/Users/${console_user}" NFSHomeDirectory 2>/dev/null | awk '{print $2}')"
    if [[ -n "$user_home" ]]; then
      USER_LOG="${user_home}/Library/Logs/Odoo Database Manager/install.log"
      mkdir -p "$(dirname "$USER_LOG")" 2>/dev/null || true
      chown "${console_user}:staff" "$(dirname "$USER_LOG")" 2>/dev/null || true
      touch "$USER_LOG" 2>/dev/null || USER_LOG=""
      chown "${console_user}:staff" "$USER_LOG" 2>/dev/null || true
    fi
  fi

  {
    echo "========================================"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ${phase}"
    echo "Utilisateur console: ${console_user:-?}"
    echo "Log système: ${INSTALL_LOG}"
    [[ -n "$USER_LOG" ]] && echo "Log utilisateur: ${USER_LOG}"
    echo "Arguments: $*"
    echo "========================================"
  } | _install_write
}

_install_write() {
  while IFS= read -r line || [[ -n "$line" ]]; do
    echo "$line"
    echo "$line" >> "$INSTALL_LOG"
    if [[ -n "$USER_LOG" ]]; then
      echo "$line" >> "$USER_LOG" 2>/dev/null || true
    fi
  done
}

install_log() {
  local msg="[$(date '+%H:%M:%S')] $*"
  echo "$msg" | _install_write
}

install_fail() {
  local msg="$*"
  install_log "ERREUR: ${msg}"
  echo "" >&2
  echo "══════════════════════════════════════════════════" >&2
  echo "  Odoo Database Manager — installation échouée" >&2
  echo "══════════════════════════════════════════════════" >&2
  echo "  ${msg}" >&2
  echo "" >&2
  echo "  Journal complet :" >&2
  echo "    ${INSTALL_LOG}" >&2
  if [[ -n "$USER_LOG" ]]; then
    echo "    ${USER_LOG}" >&2
  fi
  echo "══════════════════════════════════════════════════" >&2
  exit 1
}

install_run() {
  local desc="$1"
  shift
  install_log "→ ${desc}"
  local out
  if out="$("$@" 2>&1)"; then
    if [[ -n "$out" ]]; then
      echo "$out" | _install_write
    fi
    return 0
  fi
  local code=$?
  install_log "Sortie de la commande :"
  if [[ -n "$out" ]]; then
    echo "$out" | _install_write
  fi
  install_fail "${desc} (code ${code}): $*"
}
