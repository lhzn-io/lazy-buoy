#!/usr/bin/env bash
# Enable persistent journald, rsyslog filter, sysstat, and a systemd-timer-based RPi health logger.
# Interactive, idempotent: asks confirmation before changes and skips already-configured steps.
# Usage:
#   sudo bash scripts/enable_rpi_logging.sh        # interactive (default)
#   sudo bash scripts/enable_rpi_logging.sh -y     # non-interactive, assume YES to all
set -euo pipefail

ASSUME_YES=0
if [[ ${1:-} == "-y" || ${1:-} == "--yes" ]]; then
  ASSUME_YES=1
fi

# Read answers from the TTY even if stdin is piped
if [[ ! -t 0 && -r /dev/tty ]]; then
  exec < /dev/tty
fi

confirm() {
  local prompt="$1"
  if [[ $ASSUME_YES -eq 1 ]]; then
    echo "[auto-yes] $prompt"
    return 0
  fi
  local reply
  while true; do
    read -r -p "$prompt [y/N]: " reply || reply=""
    case "$reply" in
      [yY]|[yY][eE][sS]) return 0 ;;
      [nN]|[nN][oO]|"") echo "Skipped."; return 1 ;;
      *) echo "Please answer 'y' or 'n'." ;;
    esac
  done
}

is_pkg_installed() { dpkg -s "$1" >/dev/null 2>&1; }
unit_is_enabled() { systemctl is-enabled --quiet "$1" 2>/dev/null; }
unit_is_active() { systemctl is-active --quiet "$1" 2>/dev/null; }
write_if_changed() {
  # usage: write_if_changed DEST [MODE]; content via stdin; returns 0 if changed, 1 if unchanged
  local dest="$1"; shift || true
  local mode="${1:-}"; [[ -n "${mode:-}" ]] && shift || true
  local tmp
  tmp=$(mktemp)
  cat >"$tmp"
  if [[ -f "$dest" ]] && cmp -s "$tmp" "$dest"; then
    rm -f "$tmp"
    echo " - $dest unchanged"
    return 1
  fi
  install -D ${mode:+-m "$mode"} "$tmp" "$dest"
  rm -f "$tmp"
  echo " - wrote $dest"
  return 0
}

# Calculate md5 checksum (fallback to sha256sum if md5sum unavailable)
calc_md5() {
  if command -v md5sum >/dev/null 2>&1; then
    md5sum "$1" 2>/dev/null | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" 2>/dev/null | awk '{print $1}'
  else
    # crude fallback: file size as a weak proxy
    wc -c "$1" 2>/dev/null | awk '{print $1}'
  fi
}

require_root() {
  if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    echo "Please run as root: sudo bash scripts/enable_rpi_logging.sh" >&2
    exit 1
  fi
}

require_root

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUDO_USER_NAME=${SUDO_USER:-${USER:-pi}}
JOURNAL_DIR="/var/log/journal"
# realpath may be missing on minimal images; fall back to POSIX methods
if command -v realpath >/dev/null 2>&1; then
  REPO_ROOT="$(realpath "$SCRIPT_DIR/..")"
else
  REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
fi

step() { echo; echo "== $* =="; }

step "Current journald disk usage"
journalctl --disk-usage || true

# 1) Enable persistent journald with caps (idempotent) + retention & rotation
step "Enable persistent systemd-journald (Storage=persistent; retention/size caps)"
JOURNAL_CONF="/etc/systemd/journald.conf.d/10-persistent.conf"
ALREADY_JOURNAL=0
if [[ -d "$JOURNAL_DIR" && -f "$JOURNAL_CONF" ]] && grep -q '^Storage=persistent' "$JOURNAL_CONF" 2>/dev/null; then
  ALREADY_JOURNAL=1
fi
if [[ $ALREADY_JOURNAL -eq 1 ]]; then
  echo "journald persistence already configured."
else
  if confirm "Create $JOURNAL_DIR and configure journald persistence?"; then
    mkdir -p "$JOURNAL_DIR"
    chmod 2755 "$JOURNAL_DIR" || true
    chown root:systemd-journal "$JOURNAL_DIR" || true
    mkdir -p "$(dirname "$JOURNAL_CONF")"
    changed=0
    if write_if_changed "$JOURNAL_CONF" <<'EOF'
[Journal]
Storage=persistent
# Size/rotation guards
SystemMaxUse=200M
SystemMaxFileSize=50M
# Time-based retention (requires systemd >= 247)
MaxRetentionSec=14day
Compress=yes
Seal=yes
RateLimitIntervalSec=30s
RateLimitBurst=10000
EOF
    then changed=1; fi
    if [[ $changed -eq 1 ]]; then
      systemctl restart systemd-journald
      echo "journald restarted."
    else
      echo "journald config unchanged; no restart needed."
    fi
  else
    echo "journald persistence left unchanged."
  fi
fi

# Optionally add user to systemd-journal group for journalctl access
if id -nG "$SUDO_USER_NAME" 2>/dev/null | tr ' ' '\n' | grep -qx "systemd-journal"; then
  echo "User '$SUDO_USER_NAME' already in group systemd-journal."
else
  if confirm "Add user '$SUDO_USER_NAME' to group 'systemd-journal' for journalctl access without sudo?"; then
    usermod -aG systemd-journal "$SUDO_USER_NAME" || true
    echo "User '$SUDO_USER_NAME' added to systemd-journal (re-login required)."
  else
    echo "Skipped adding user to systemd-journal."
  fi
fi

# 2) Ensure rsyslog active and capture undervoltage lines (idempotent) + logrotate
step "Enable rsyslog and capture undervoltage messages"
RSYSLOG_CONF="/etc/rsyslog.d/50-rpi-undervolt.conf"
LOGROTATE_UNDERVOLT="/etc/logrotate.d/undervolt"
RSYSLOG_PRESENT=0
if command -v rsyslogd >/dev/null 2>&1 || is_pkg_installed rsyslog; then RSYSLOG_PRESENT=1; fi
CONF_PRESENT=0
if [[ -f "$RSYSLOG_CONF" ]] && grep -q 'Under-voltage detected!' "$RSYSLOG_CONF" 2>/dev/null; then CONF_PRESENT=1; fi
if [[ $RSYSLOG_PRESENT -eq 1 && $CONF_PRESENT -eq 1 ]]; then
  echo "rsyslog installed and undervolt config already present."
else
  if confirm "Configure undervoltage logging. Install/enable rsyslog if needed?"; then
    if [[ $RSYSLOG_PRESENT -eq 0 ]]; then
      if confirm "rsyslog not found. Install it now?"; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -y
        apt-get install -y rsyslog
        RSYSLOG_PRESENT=1
      else
        echo "Skipping rsyslog installation; undervoltage will remain only in the journal."
      fi
    fi
    if [[ $RSYSLOG_PRESENT -eq 1 ]]; then
      systemctl enable --now rsyslog || true
      mkdir -p /etc/rsyslog.d
      changed=0
      if write_if_changed "$RSYSLOG_CONF" <<'EOF'
# Log RPi firmware undervoltage messages to a separate file
:msg, contains, "Under-voltage detected!" -/var/log/undervolt.log
& stop
EOF
      then changed=1; fi
      if [[ $changed -eq 1 ]]; then
        systemctl restart rsyslog || true
        echo "rsyslog reloaded with undervolt filter. Logs: /var/log/undervolt.log"
      else
        echo "rsyslog config unchanged; no restart needed."
      fi
    fi
  else
    echo "Skipped rsyslog/undervolt configuration."
  fi
fi
# logrotate for undervolt log (weekly rotate, keep 4, compress)
if [[ -f "$LOGROTATE_UNDERVOLT" ]]; then
  echo "logrotate for undervolt already present."
else
  if confirm "Install logrotate policy for /var/log/undervolt.log (weekly, keep 4, compress)?"; then
    write_if_changed "$LOGROTATE_UNDERVOLT" <<'EOF'
/var/log/undervolt.log {
  weekly
  rotate 4
  missingok
  notifempty
  compress
  delaycompress
  copytruncate
}
EOF
  else
    echo "Skipped logrotate policy for undervolt.log."
  fi
fi

# 3) Install and enable sysstat (sar/pidstat) at 1-minute granularity (idempotent)
step "Install/enable sysstat for CPU/IO history"
SYSSTAT_INSTALLED=0
if is_pkg_installed sysstat; then SYSSTAT_INSTALLED=1; fi
NEED_ENABLE_SYSSTAT=0
if [[ $SYSSTAT_INSTALLED -eq 1 ]]; then
  if [[ -f /etc/default/sysstat ]] && grep -q '^ENABLED="\?true\?"\|^ENABLED="true"\|^ENABLED=true' /etc/default/sysstat 2>/dev/null; then
    :
  else
    NEED_ENABLE_SYSSTAT=1
  fi
  if [[ -f /etc/cron.d/sysstat ]] && grep -q 'sa1 1 1' /etc/cron.d/sysstat 2>/dev/null; then
    :
  else
    NEED_ENABLE_SYSSTAT=1
  fi
fi
if [[ $SYSSTAT_INSTALLED -eq 1 && $NEED_ENABLE_SYSSTAT -eq 0 ]]; then
  echo "sysstat already installed and 1-minute collection configured."
else
  if confirm "Install 'sysstat' (if missing) and enable 1-minute data collection?"; then
    if [[ $SYSSTAT_INSTALLED -eq 0 ]]; then
      export DEBIAN_FRONTEND=noninteractive
      apt-get update -y
      apt-get install -y sysstat
      SYSSTAT_INSTALLED=1
    fi
    if [[ -f /etc/default/sysstat ]]; then
      sed -i 's/^ENABLED=.*/ENABLED="true"/' /etc/default/sysstat || true
    fi
    if [[ -f /etc/cron.d/sysstat ]]; then
      sed -i 's/^\(.*sa1.*\)$/# \1/' /etc/cron.d/sysstat || true
      if ! grep -q 'sa1 1 1' /etc/cron.d/sysstat; then
        echo "*/1 * * * * root command -v sa1 >/dev/null 2>&1 && sa1 1 1" >> /etc/cron.d/sysstat
      fi
    fi
    systemctl enable --now sysstat || true
    echo "sysstat enabled. Use 'sar' and 'pidstat'."
  else
    echo "Skipped sysstat installation/configuration."
  fi
fi

# 4) Install health logger and systemd timer (idempotent) + logrotate
step "Install RPi health logger (systemd timer, 1/min)"
LOGGER_BIN_DST="/usr/local/sbin/rpi-health-logger.sh"
SERVICE_FILE="/etc/systemd/system/rpi-health-logger.service"
TIMER_FILE="/etc/systemd/system/rpi-health-logger.timer"
LOGROTATE_HEALTH="/etc/logrotate.d/rpi-health"
LOGGER_INSTALLED=0
LOGGER_DIFF=1
if [[ -x "$LOGGER_BIN_DST" ]]; then
  src_md5=$(calc_md5 "$SCRIPT_DIR/rpi-health-logger.sh" || true)
  dst_md5=$(calc_md5 "$LOGGER_BIN_DST" || true)
  if [[ -n "${src_md5:-}" && -n "${dst_md5:-}" && "$src_md5" == "$dst_md5" ]]; then
    LOGGER_INSTALLED=1
    LOGGER_DIFF=0
  fi
fi
UNITS_PRESENT=0
if [[ -f "$SERVICE_FILE" && -f "$TIMER_FILE" ]]; then UNITS_PRESENT=1; fi
TIMER_ENABLED=0
if unit_is_enabled rpi-health-logger.timer; then TIMER_ENABLED=1; fi
if [[ $LOGGER_INSTALLED -eq 1 && $UNITS_PRESENT -eq 1 && $TIMER_ENABLED -eq 1 ]]; then
  echo "Health logger already installed and timer enabled."
  if [[ $LOGGER_DIFF -eq 1 ]]; then
    # Script exists but differs; offer update
    if confirm "Local rpi-health-logger.sh differs from installed. Update it now?"; then
      install -Dm0755 "$SCRIPT_DIR/rpi-health-logger.sh" "$LOGGER_BIN_DST"
      systemctl restart rpi-health-logger.timer || true
      echo "Updated health logger script (checksum changed)."
    else
      echo "Skipped updating health logger script."
    fi
  fi
else
  if confirm "Install/update health logger and enable rpi-health-logger.timer?"; then
    install -Dm0755 "$SCRIPT_DIR/rpi-health-logger.sh" "$LOGGER_BIN_DST"
    changed_units=0
    if write_if_changed "$SERVICE_FILE" <<'EOF'
[Unit]
Description=RPi Health Logger (vcgencmd, temp, top processes)

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/rpi-health-logger.sh
User=root
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
EOF
    then changed_units=1; fi
    if write_if_changed "$TIMER_FILE" <<'EOF'
[Unit]
Description=Run RPi Health Logger every minute

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
AccuracySec=10s
Persistent=true

[Install]
WantedBy=timers.target
EOF
    then changed_units=1; fi
    if [[ $changed_units -eq 1 ]]; then
      systemctl daemon-reload
    fi
    systemctl enable --now rpi-health-logger.timer
    echo "Health JSON logs will go to /var/log/rpi-health.log (every minute)."
  else
    echo "Skipped health logger installation."
  fi
fi
# logrotate for rpi-health log (daily rotate, keep 7, compress)
if [[ -f "$LOGROTATE_HEALTH" ]]; then
  echo "logrotate for rpi-health.log already present."
else
  if confirm "Install logrotate policy for /var/log/rpi-health.log (daily, keep 7, compress)?"; then
    write_if_changed "$LOGROTATE_HEALTH" <<'EOF'
/var/log/rpi-health.log {
  daily
  rotate 7
  missingok
  notifempty
  compress
  delaycompress
  copytruncate
}
EOF
  else
    echo "Skipped logrotate policy for rpi-health.log."
  fi
fi

# 5) Optional: enable hardware watchdog and auto-reboot on hangs/OOM (idempotent)
step "Optional: Configure hardware watchdog and auto-reboot on hangs/OOM"
BOOT_CONFIG="/boot/firmware/config.txt"
[[ -f /boot/config.txt ]] && BOOT_CONFIG="/boot/config.txt"
WATCHDOG_DTPARAM_SET=0
if [[ -f "$BOOT_CONFIG" ]] && grep -q '^dtparam=watchdog=on' "$BOOT_CONFIG" 2>/dev/null; then WATCHDOG_DTPARAM_SET=1; fi
if [[ $WATCHDOG_DTPARAM_SET -eq 1 ]]; then
  echo "Watchdog already enabled in $BOOT_CONFIG."
else
  if confirm "Enable watchdog in $BOOT_CONFIG (dtparam=watchdog=on)? Requires reboot."; then
    echo 'dtparam=watchdog=on' >> "$BOOT_CONFIG"
    echo " - wrote dtparam=watchdog=on to $BOOT_CONFIG"
  else
    echo "Skipped enabling dtparam watchdog."
  fi
fi

SYSTEMD_WD_CONF="/etc/systemd/system.conf.d/10-watchdog.conf"
if [[ -f "$SYSTEMD_WD_CONF" ]] && grep -q '^RuntimeWatchdogSec=' "$SYSTEMD_WD_CONF" 2>/dev/null; then
  echo "systemd RuntimeWatchdogSec already configured."
else
  if confirm "Set systemd RuntimeWatchdogSec=30s and ShutdownWatchdogSec=2min?"; then
    mkdir -p /etc/systemd/system.conf.d
    write_if_changed "$SYSTEMD_WD_CONF" <<'EOF'
[Manager]
RuntimeWatchdogSec=30s
ShutdownWatchdogSec=2min
EOF
    systemctl daemon-reexec || true
    modprobe bcm2835_wdt || true
    echo "Watchdog configured; will take effect immediately for systemd-managed units."
  else
    echo "Skipped configuring systemd watchdog."
  fi
fi

SYSCTL_CONF="/etc/sysctl.d/99-autoreboot-on-oom.conf"
if [[ -f "$SYSCTL_CONF" ]]; then
  echo "sysctl autoreboot on OOM already present."
else
  if confirm "Enable auto-reboot on OOM/oops (kernel.panic=10, vm.panic_on_oom=1, kernel.panic_on_oops=1)?"; then
    write_if_changed "$SYSCTL_CONF" <<'EOF'
kernel.panic=10
vm.panic_on_oom=1
kernel.panic_on_oops=1
EOF
    sysctl --system || true
  else
    echo "Skipped sysctl autoreboot settings."
  fi
fi

# 6) Optional: create/manage systemd service for sensor_lora_daemon (idempotent)
step "Optional: Install sensor_lora_daemon systemd service"
SVC_FILE="/etc/systemd/system/sensor-lora-daemon.service"
LOG_DIR_APP="/var/log/lhznbuoy"
SVC_CONTENT=$(cat <<EOF
[Unit]
Description=Sensor LoRa Daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SUDO_USER_NAME
Group=$SUDO_USER_NAME
WorkingDirectory=$REPO_ROOT
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/env python3 -u $REPO_ROOT/sensor_lora_daemon.py -l $LOG_DIR_APP
Restart=always
RestartSec=10s
StartLimitIntervalSec=60
StartLimitBurst=5
Nice=5
IOSchedulingClass=best-effort
IOSchedulingPriority=6
StandardOutput=journal
StandardError=journal
LogRateLimitIntervalSec=0

[Install]
WantedBy=multi-user.target
EOF
)

mkdir -p "$LOG_DIR_APP"
chown "$SUDO_USER_NAME":"$SUDO_USER_NAME" "$LOG_DIR_APP" || true

if [[ -f "$SVC_FILE" ]]; then
  echo "sensor-lora-daemon.service already exists."
  tmp_svc=$(mktemp)
  printf "%s" "$SVC_CONTENT" > "$tmp_svc"
  if ! cmp -s "$tmp_svc" "$SVC_FILE"; then
    if confirm "Existing service differs. Update it to the new recommended content?"; then
      write_if_changed "$SVC_FILE" < "$tmp_svc"
      systemctl daemon-reload
      if unit_is_active sensor-lora-daemon.service; then
        systemctl try-restart sensor-lora-daemon.service || true
      fi
    else
      echo "Left existing service unchanged."
    fi
  else
    echo "Service content unchanged."
  fi
  rm -f "$tmp_svc"
  if ! unit_is_enabled sensor-lora-daemon.service; then
    if confirm "Enable and start sensor-lora-daemon.service now?"; then
      systemctl enable --now sensor-lora-daemon.service
    else
      echo "Service exists but remains disabled."
    fi
  fi
else
  if confirm "Create sensor-lora-daemon.service to auto-start and restart the daemon?"; then
    write_if_changed "$SVC_FILE" <<EOF
$SVC_CONTENT
EOF
    systemctl daemon-reload
    if confirm "Enable and start sensor-lora-daemon.service now?"; then
      systemctl enable --now sensor-lora-daemon.service
    else
      echo "Service created but not enabled/started."
    fi
  else
    echo "Skipped creating sensor-lora-daemon.service."
  fi
fi

step "Final status"
journalctl --disk-usage >/dev/null 2>&1 && journalctl --disk-usage || true
if command -v rsyslogd >/dev/null 2>&1 || is_pkg_installed rsyslog; then
  unit_is_active rsyslog && echo "rsyslog: active" || echo "rsyslog: inactive"
else
  echo "rsyslog: not installed"
fi
unit_is_active sysstat && echo "sysstat: active" || echo "sysstat: inactive"
unit_is_active rpi-health-logger.timer && echo "rpi-health-logger.timer: active" || echo "rpi-health-logger.timer: inactive"

if [[ -f "$SVC_FILE" ]]; then
  unit_is_active sensor-lora-daemon.service && echo "sensor-lora-daemon.service: active" || echo "sensor-lora-daemon.service: inactive"
fi

echo
echo "Notes:"
echo " - journald is now persistent with size and time limits (MaxRetentionSec=14day)."
echo " - New group memberships require re-login to take effect."
echo " - Reruns skip already-configured steps; use -y for non-interactive."
echo " - If rsyslog isn't installed, undervoltage remains in the journal; view with:"
echo "     journalctl -k -g 'voltage|undervolt'"
echo " - If the device becomes unresponsive, watchdog + auto-reboot can recover it."
