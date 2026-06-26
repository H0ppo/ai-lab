#!/usr/bin/env bash
#
# proxmox-lxc.sh — deploy the AI Runtime Security Demo into a Proxmox LXC.
#
# Run this ON a Proxmox VE host (as root). It interactively creates an
# unprivileged Debian 12 container (DHCP), then provisions the app to run
# natively via gunicorn under systemd — no Docker, no nesting required.
#
# Usage:
#   bash deploy/proxmox-lxc.sh
#
set -euo pipefail

# --------------------------------------------------------------------------- #
# Defaults (overridable in the prompts)
# --------------------------------------------------------------------------- #
DEF_HOSTNAME="ai-runtime-security"
DEF_CORES="2"
DEF_RAM="2048"
DEF_DISK="8"
DEF_BRIDGE="vmbr0"
DEF_REPO="https://github.com/H0ppo/ai-lab"
DEF_BRANCH="main"

APP_DIR="/opt/ai-lab"
DATA_DIR="/var/lib/ai-lab"
SVC_USER="ailab"
APP_PORT="5000"

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
RED=$'\033[0;31m'; GRN=$'\033[0;32m'; YLW=$'\033[1;33m'; BLU=$'\033[0;34m'; NC=$'\033[0m'
msg()  { echo -e "${BLU}==>${NC} $*"; }
ok()   { echo -e "${GRN}✓${NC} $*"; }
warn() { echo -e "${YLW}!${NC} $*"; }
die()  { echo -e "${RED}✗ $*${NC}" >&2; exit 1; }

CURRENT_STEP="startup"
CREATED_VMID=""
on_err() {
  echo -e "\n${RED}✗ Failed during: ${CURRENT_STEP}${NC}" >&2
  if [[ -n "$CREATED_VMID" ]]; then
    warn "The container ${CREATED_VMID} may be partially created."
    warn "Remove it with:  pct stop ${CREATED_VMID} 2>/dev/null; pct destroy ${CREATED_VMID}"
  fi
}
trap on_err ERR

need() { command -v "$1" >/dev/null 2>&1 || die "Required command '$1' not found. Run this on a Proxmox VE host."; }

# whiptail input box returning the entered (or default) value.
ask() {
  local title="$1" prompt="$2" default="$3" result
  result=$(whiptail --title "$title" --inputbox "$prompt" 10 70 "$default" 3>&1 1>&2 2>&3) \
    || die "Cancelled."
  echo "${result:-$default}"
}

ask_yesno() {
  whiptail --title "$1" --yesno "$2" 9 70 3>&1 1>&2 2>&3
}

# Coerce arbitrary input into a valid LXC/DNS hostname label: lowercase, only
# [a-z0-9-], no leading/trailing/repeated hyphens, max 63 chars. Proxmox rejects
# underscores, spaces and uppercase ("value does not look like a valid DNS name").
sanitize_hostname() {
  local h="${1,,}"                 # lowercase
  h="${h//[^a-z0-9-]/-}"           # any invalid char -> hyphen
  while [[ "$h" == *--* ]]; do h="${h//--/-}"; done  # collapse repeats
  h="${h#-}"; h="${h%-}"           # trim leading/trailing hyphen
  h="${h:0:63}"; h="${h%-}"        # cap length, retrim
  printf '%s' "$h"
}

# Scan Proxmox storages that support a given content type and let the user pick
# one from a whiptail menu. Auto-selects when only one is available.
#   pick_storage <content> <title> <prompt>
# <content> is e.g. "rootdir" (container rootfs) or "vztmpl" (LXC templates).
pick_storage() {
  local content="$1" title="$2" prompt="$3"
  local -a menu=()
  local name type status avail label
  while read -r name type status _ _ avail _; do
    [[ "$name" == "Name" || -z "$name" ]] && continue   # skip header / blanks
    [[ "$status" == "active" ]] || continue              # skip inactive storage
    # pvesm reports sizes in KiB; show type + free space (GiB) as the menu label.
    label="$(awk -v t="$type" -v a="$avail" 'BEGIN{printf "%-9s %.0f GiB free", t, a/1048576}')"
    menu+=("$name" "$label")
  done < <(pvesm status --content "$content" 2>/dev/null)

  local count=$(( ${#menu[@]} / 2 ))
  if (( count == 0 )); then
    die "No active storage supporting '$content' content was found. Add one under Datacenter > Storage, then re-run."
  fi
  if (( count == 1 )); then
    echo "${menu[0]}"   # only one choice — use it
    return
  fi
  whiptail --title "$title" --menu "$prompt" 20 78 10 "${menu[@]}" 3>&1 1>&2 2>&3 \
    || die "Cancelled."
}

# --------------------------------------------------------------------------- #
# Preflight
# --------------------------------------------------------------------------- #
CURRENT_STEP="preflight checks"
[[ $EUID -eq 0 ]] || die "Run as root on the Proxmox host."
need pct; need pveam; need pvesh; need whiptail
ok "Running on a Proxmox VE host as root."

# --------------------------------------------------------------------------- #
# Gather configuration
# --------------------------------------------------------------------------- #
CURRENT_STEP="collecting configuration"
NEXTID=$(pvesh get /cluster/nextid 2>/dev/null || echo "100")

VMID=$(ask "Container ID" "VMID for the new LXC:" "$NEXTID")
[[ "$VMID" =~ ^[0-9]+$ ]] || die "VMID must be numeric."
pct status "$VMID" >/dev/null 2>&1 && die "VMID $VMID already exists."

CT_HOSTNAME=$(ask "Hostname" "Container hostname (letters, digits, hyphens):" "$DEF_HOSTNAME")
_clean_hostname=$(sanitize_hostname "$CT_HOSTNAME")
[[ -z "$_clean_hostname" ]] && _clean_hostname="$DEF_HOSTNAME"
[[ "$_clean_hostname" != "$CT_HOSTNAME" ]] && warn "Adjusted hostname to a valid DNS form: ${_clean_hostname}"
CT_HOSTNAME="$_clean_hostname"
CORES=$(ask "CPU" "Number of CPU cores:" "$DEF_CORES")
RAM=$(ask "Memory" "RAM in MB:" "$DEF_RAM")
DISK=$(ask "Disk" "Root disk size in GB:" "$DEF_DISK")
STORAGE=$(pick_storage rootdir "Container storage" "Select the storage pool for the container rootfs:")
msg "Rootfs storage: ${STORAGE}"
TMPL_STORAGE=$(pick_storage vztmpl "Template storage" "Select the storage that holds LXC templates (vztmpl):")
msg "Template storage: ${TMPL_STORAGE}"
BRIDGE=$(ask "Network" "Network bridge (DHCP will be used):" "$DEF_BRIDGE")
REPO=$(ask "Repository" "Git repository URL:" "$DEF_REPO")
BRANCH=$(ask "Branch" "Git branch to deploy:" "$DEF_BRANCH")

UNPRIV=1
if ! ask_yesno "Container type" "Create an UNPRIVILEGED container? (recommended)"; then
  UNPRIV=0
fi

ROOT_PW=$(openssl rand -base64 12 2>/dev/null || echo "ChangeMe-$RANDOM")

whiptail --title "Confirm" --yesno \
  "Create LXC ${VMID} (${CT_HOSTNAME})?\n\nCores: ${CORES}   RAM: ${RAM}MB   Disk: ${DISK}GB\nStorage: ${STORAGE}   Bridge: ${BRIDGE} (DHCP)\nUnprivileged: $([[ $UNPRIV == 1 ]] && echo yes || echo no)\n\nApp: ${REPO} @ ${BRANCH}\nRun: gunicorn + systemd (native)" \
  16 72 3>&1 1>&2 2>&3 || die "Cancelled."

# --------------------------------------------------------------------------- #
# Ensure a Debian 12 template is available
# --------------------------------------------------------------------------- #
CURRENT_STEP="resolving Debian 12 template"
msg "Updating template catalog..."
pveam update >/dev/null 2>&1 || warn "pveam update failed (continuing with cached catalog)."

TEMPLATE=$(pveam available --section system 2>/dev/null \
  | awk '/debian-12-standard/ {print $2}' | sort -V | tail -n1)
[[ -n "$TEMPLATE" ]] || die "Could not find a debian-12-standard template in the catalog."

if ! pveam list "$TMPL_STORAGE" 2>/dev/null | grep -q "$TEMPLATE"; then
  msg "Downloading template ${TEMPLATE} to ${TMPL_STORAGE}..."
  pveam download "$TMPL_STORAGE" "$TEMPLATE"
fi
TMPL_REF="${TMPL_STORAGE}:vztmpl/${TEMPLATE}"
ok "Template ready: ${TMPL_REF}"

# --------------------------------------------------------------------------- #
# Create + start the container
# --------------------------------------------------------------------------- #
CURRENT_STEP="creating the LXC"
msg "Creating container ${VMID}..."
pct create "$VMID" "$TMPL_REF" \
  --hostname "$CT_HOSTNAME" \
  --cores "$CORES" \
  --memory "$RAM" \
  --swap "$RAM" \
  --rootfs "${STORAGE}:${DISK}" \
  --net0 "name=eth0,bridge=${BRIDGE},ip=dhcp" \
  --unprivileged "$UNPRIV" \
  --features nesting=1 \
  --onboot 1 \
  --password "$ROOT_PW" \
  --description "AI Runtime Security Demo (native gunicorn+systemd)"
CREATED_VMID="$VMID"
ok "Container ${VMID} created."

CURRENT_STEP="starting the LXC"
msg "Starting container..."
pct start "$VMID"

msg "Waiting for network (DHCP)..."
IP=""
for _ in $(seq 1 30); do
  IP=$(pct exec "$VMID" -- hostname -I 2>/dev/null | awk '{print $1}' || true)
  [[ -n "$IP" ]] && break
  sleep 2
done
[[ -n "$IP" ]] || warn "Could not detect an IP yet; the container may still be acquiring one."

# --------------------------------------------------------------------------- #
# Provision the app inside the container
# --------------------------------------------------------------------------- #
CURRENT_STEP="provisioning the app inside the LXC"
msg "Provisioning the app (this can take a few minutes)..."

PROVISION="/tmp/ai-lab-install.${VMID}.sh"
cat > "$PROVISION" <<PROV
#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

APP_DIR="${APP_DIR}"
DATA_DIR="${DATA_DIR}"
SVC_USER="${SVC_USER}"
REPO="${REPO}"
BRANCH="${BRANCH}"
APP_PORT="${APP_PORT}"

echo "[lxc] Installing packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git ca-certificates >/dev/null

echo "[lxc] Creating service user..."
id -u "\$SVC_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "\$SVC_USER"
install -d -o "\$SVC_USER" -g "\$SVC_USER" "\$DATA_DIR"

echo "[lxc] Fetching app (\$BRANCH)..."
if [ -d "\$APP_DIR/.git" ]; then
  git -C "\$APP_DIR" fetch --depth 1 origin "\$BRANCH"
  git -C "\$APP_DIR" checkout -f "\$BRANCH"
  git -C "\$APP_DIR" reset --hard "origin/\$BRANCH"
else
  rm -rf "\$APP_DIR"
  git clone --depth 1 --branch "\$BRANCH" "\$REPO" "\$APP_DIR"
fi

echo "[lxc] Building virtualenv..."
python3 -m venv "\$APP_DIR/.venv"
"\$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"\$APP_DIR/.venv/bin/pip" install --quiet -r "\$APP_DIR/requirements.txt"

echo "[lxc] Writing .env.local..."
if [ ! -f "\$APP_DIR/.env.local" ]; then
  cp "\$APP_DIR/.env.example" "\$APP_DIR/.env.local"
fi
sed -i 's|^HOST=.*|HOST=0.0.0.0|' "\$APP_DIR/.env.local"
sed -i "s|^PORT=.*|PORT=\${APP_PORT}|" "\$APP_DIR/.env.local"
chown -R "\$SVC_USER":"\$SVC_USER" "\$APP_DIR"

echo "[lxc] Installing systemd service..."
cat > /etc/systemd/system/ai-lab.service <<UNIT
[Unit]
Description=AI Runtime Security Demo
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=\${SVC_USER}
WorkingDirectory=\${APP_DIR}
EnvironmentFile=\${APP_DIR}/.env.local
Environment=SETUP_FILE=\${DATA_DIR}/setup.json
Environment=METRICS_DB=\${DATA_DIR}/metrics.db
ExecStart=\${APP_DIR}/.venv/bin/gunicorn --bind 0.0.0.0:\${APP_PORT} --workers 1 --threads 8 --timeout 180 app:app
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now ai-lab.service
echo "[lxc] Done."
PROV

pct push "$VMID" "$PROVISION" /root/ai-lab-install.sh --perms 755
pct exec "$VMID" -- bash /root/ai-lab-install.sh
rm -f "$PROVISION"

# Re-read the IP in case it appeared during provisioning.
[[ -z "$IP" ]] && IP=$(pct exec "$VMID" -- hostname -I 2>/dev/null | awk '{print $1}' || true)

# --------------------------------------------------------------------------- #
# Done
# --------------------------------------------------------------------------- #
trap - ERR
echo
ok "Deployment complete!"
echo -e "${GRN}────────────────────────────────────────────────────────────${NC}"
echo -e "  Container : ${VMID} (${CT_HOSTNAME}), unprivileged=$([[ $UNPRIV == 1 ]] && echo yes || echo no)"
echo -e "  Root pass : ${ROOT_PW}   (login: pct enter ${VMID})"
echo -e "  App URL   : ${BLU}http://${IP:-<container-ip>}:${APP_PORT}${NC}"
echo -e "  Setup     : ${BLU}http://${IP:-<container-ip>}:${APP_PORT}/setup${NC}"
echo -e "${GRN}────────────────────────────────────────────────────────────${NC}"
echo -e "  Open the Setup Wizard and point it at your external Ollama."
echo -e "  Service   : systemctl status ai-lab   (inside: pct enter ${VMID})"
echo
