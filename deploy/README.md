# Proxmox LXC deployment

`proxmox-lxc.sh` spins the app up inside a Proxmox **LXC container**, running it
**natively** (gunicorn under systemd — no Docker, no nesting). It is interactive
(whiptail) and uses **DHCP**, so the only thing you supply afterwards is your
external Ollama URL in the Setup Wizard.

## Requirements

- A **Proxmox VE** host (run the script there, as `root`).
- Internet access from the host (to fetch the Debian 12 template) and from the
  container (to `git clone` the repo + `pip install`).
- An **Ollama** server reachable from the container's network (this deployment
  does not provide one).

## Usage

On the Proxmox host:

```bash
# grab just the script…
curl -fsSL https://raw.githubusercontent.com/H0ppo/ai-lab/main/deploy/proxmox-lxc.sh -o proxmox-lxc.sh
bash proxmox-lxc.sh

# …or clone the repo and run it
git clone https://github.com/H0ppo/ai-lab
bash ai-lab/deploy/proxmox-lxc.sh
```

You'll be prompted for VMID, hostname, cores/RAM/disk, storage pool, bridge,
and the repo/branch (all with sensible defaults). The script then:

1. Downloads the latest `debian-12-standard` template if needed.
2. Creates an **unprivileged** LXC with DHCP and starts it.
3. Installs Python + git, clones the repo, builds a venv, installs requirements.
4. Writes `.env.local` (`HOST=0.0.0.0`), installs the `ai-lab` systemd service,
   and enables it.
5. Prints the container's IP and the URLs:
   `http://<lxc-ip>:5000` and `http://<lxc-ip>:5000/setup`.

## Managing the container

```bash
pct enter <vmid>                 # shell into the container
systemctl status ai-lab          # service status
journalctl -u ai-lab -f          # live logs
systemctl restart ai-lab         # restart after changes
```

## Updating the app

```bash
pct exec <vmid> -- bash -lc '
  cd /opt/ai-lab && git pull &&
  .venv/bin/pip install -r requirements.txt &&
  systemctl restart ai-lab'
```

Persistent state (`setup.json`, `metrics.db`) lives in `/var/lib/ai-lab` and
survives app updates.

## Removing it

```bash
pct stop <vmid> && pct destroy <vmid>
```

## Notes

- **Native, not Docker:** the container runs gunicorn directly. If you'd rather
  run the bundled `docker-compose.yml` inside the LXC instead, you'd need a
  container with Docker installed and `nesting=1` (already set) — but the native
  path here is lighter and needs no Docker.
- **Security:** config endpoints are reachable on the container IP by design.
  Set `ADMIN_TOKEN` in `/opt/ai-lab/.env.local` (then `systemctl restart ai-lab`)
  to require a token for saving config on an untrusted network.
- A random root password is generated and printed at the end; change it with
  `passwd` inside the container if you intend to keep it.
