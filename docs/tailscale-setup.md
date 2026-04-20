# Tailscale Setup for Knarr

Tailscale provides secure access to the Knarr Matrix homeserver and Kafka UI
from any device (phone, laptop, etc.) without port forwarding or public DNS.

## Why Tailscale

The k3d cluster runs on a local machine (M1 Mac) and is only reachable on
the LAN by default. Tailscale creates a WireGuard mesh VPN ("tailnet") that
makes all your devices reachable to each other regardless of network — home
LAN, mobile data, hotel wifi, etc.

With Tailscale:
- Your phone reaches `http://<mac-tailscale-ip>:80` (Traefik → Synapse)
- No `/etc/hosts` entries needed on the phone
- No public exposure, no router port forwarding
- Works from anywhere

## Install on macOS

**Use the Mac App Store version, not Homebrew.** The Homebrew package is
CLI-only, lacks the tray icon, and fights with macOS LaunchDaemons (`sudo`
required, ownership warnings, no auto-start at login).

If you already installed via Homebrew:
```bash
sudo brew services stop tailscale
brew uninstall tailscale
# Clean any leftover Cellar files:
ls /opt/homebrew/Cellar/tailscale 2>/dev/null && sudo rm -rf /opt/homebrew/Cellar/tailscale
```

Then install from the Mac App Store (search "Tailscale") or download the
`.pkg` from https://tailscale.com/download.

The App Store version:
- Tray icon with status, device list, exit node toggle
- Proper macOS system extension for the VPN
- Auto-starts at login
- No sudo needed

## Install on Phone

Install the Tailscale app from the App Store (iOS) or Play Store (Android).
Log in with the same Tailscale account. The phone joins the tailnet
automatically.

## Admin Console

Manage devices at https://login.tailscale.com/admin/machines. From here you
can:
- See all devices on the tailnet and their Tailscale IPs
- Remove stale devices (e.g. a CLI-installed Mac that was replaced by the
  App Store version)
- Enable MagicDNS (optional — gives devices names like `mac.tailnet-name.ts.net`)

## Expose Synapse via Tailscale Serve

The k3d cluster's Traefik LoadBalancer lives on an OrbStack virtual network
(e.g. `192.168.97.2`) that's only reachable from the Mac itself. Tailscale
Serve bridges this gap by proxying traffic from the tailnet to localhost.

### Prerequisites: k3d port mapping

Traefik must be reachable on `localhost:80`. If not already configured:

```bash
k3d cluster edit nordri-test --port-add "80:80@loadbalancer" --port-add "443:443@loadbalancer"
```

Verify: `curl -s http://localhost:80/health -H "Host: matrix.knarr.local"` → `OK`

### Set up Tailscale Serve

```bash
# macOS App Store version — CLI is at:
/Applications/Tailscale.app/Contents/MacOS/Tailscale serve --bg --http 8008 http://localhost:80
```

This creates a persistent proxy: `http://<machine-name>.<tailnet>.ts.net:8008`
→ `localhost:80` → Traefik → Synapse. Survives terminal closes; runs as long
as the Tailscale daemon runs.

Check status:
```bash
/Applications/Tailscale.app/Contents/MacOS/Tailscale serve status
```

Disable:
```bash
/Applications/Tailscale.app/Contents/MacOS/Tailscale serve --http=8008 off
```

### Clean up the MagicDNS name

The default machine name (e.g. `rasmuss-macbook-pro-2`) makes an ugly URL.
Rename in the Tailscale admin console:

1. Go to https://login.tailscale.com/admin/machines
2. Click the three dots next to your Mac → "Edit machine name"
3. Change to something short (e.g. `knarr-hub`)
4. URL becomes: `http://knarr-hub.<tailnet>.ts.net:8008`

### Important: use the MagicDNS name, not the raw IP

Tailscale Serve only proxies correctly when accessed via the MagicDNS
hostname. Requests to the raw Tailscale IP (e.g. `100.x.y.z:8008`) bypass
Tailscale Serve and return 404 from Traefik.

## Connect Element to Matrix

1. On your phone, open Element X
2. Sign in → custom homeserver → `http://<machine-name>.<tailnet>.ts.net:8008`
3. Login with your personal user (e.g. `cervator`) — keep `admin` for ops only
4. You should see `#social-watch` with Reddit alerts and any bridged Discord
   rooms

To create a personal user:
```bash
kubectl exec -n knarr deploy/synapse -- register_new_matrix_user \
  -c /config/homeserver.yaml -u <username> -p <password> --no-admin http://localhost:8008
```

## Connect to Kafka UI

Kafka UI uses Traefik host-based routing (`kafka-ui.knarr.local`), which
requires a hosts file entry. This is easy on a Mac/PC but impractical on
phones. Options:

- **From a Mac/PC on the tailnet:** Add `127.0.0.1 kafka-ui.knarr.local`
  to `/etc/hosts`, then open `http://kafka-ui.knarr.local` in a browser
- **From a phone:** Use the Mac's browser via remote desktop, or skip —
  Kafka UI is an ops tool, not a daily-use interface
- **Future:** Add a second Tailscale Serve rule on a different port, or
  add a PathPrefix-based IngressRoute for Kafka UI

## Future: Tailscale Kubernetes Operator

Tailscale has a Kubernetes operator that can expose individual Services
directly onto the tailnet. This is the path for exposing Synapse (and other
Knarr services) on GKE without public ingress:

- https://tailscale.com/kb/1185/kubernetes/
- Each exposed service gets its own Tailscale IP
- ACLs can restrict which tailnet users can reach which services
- No Traefik host routing needed — each service has a unique IP

This becomes relevant when moving from local k3d to GKE production.

## Gotchas

- **Homebrew vs App Store:** Don't use both — they conflict. The Homebrew
  version registers as a separate device on the tailnet. If you switch,
  delete the stale device in the admin console.
- **Tailscale IP vs LAN IP:** Other devices on your LAN still use the LAN
  IP (`192.168.x.x`). The Tailscale IP (`100.x.y.z`) works from anywhere
  but only for devices on the same tailnet.
- **Element X vs Element Classic:** Element X is recommended (newer, faster).
  Falls back to Element Classic if you need features Element X doesn't
  support yet.
- **HTTP not HTTPS:** The Matrix homeserver runs plain HTTP behind Traefik.
  Tailscale's WireGuard tunnel encrypts the traffic in transit, so this is
  safe. For production, add TLS termination at Traefik.
