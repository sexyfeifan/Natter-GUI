#!/bin/sh
# Native Linux installation; state remains outside the versioned application tree.
set -eu
if [ "$(id -u)" != 0 ]; then echo 'Run this installer with sudo.' >&2; exit 1; fi
if [ "$(uname -s)" != Linux ]; then echo 'Native installation requires Linux/systemd.' >&2; exit 1; fi
command -v python3 >/dev/null
command -v systemctl >/dev/null
/usr/bin/python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11 or newer is required"'
task_gateway=''
task_interface=''
while [ "$#" -gt 0 ]; do
  case "$1" in
    --gateway) task_gateway=$2; shift 2;;
    --interface) task_interface=$2; shift 2;;
    *) echo 'Usage: sudo ./scripts/install.sh [--gateway IPv4 --interface end0]' >&2; exit 1;;
  esac
done
if [ -n "$task_gateway" ] || [ -n "$task_interface" ]; then
  python3 -c 'import ipaddress,re,sys; ipaddress.IPv4Address(sys.argv[1]); assert re.fullmatch(r"[A-Za-z0-9_.:-]{1,32}",sys.argv[2])' "$task_gateway" "$task_interface"
fi
task_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python3 "$task_root/scripts/sync_upstream.py" --verify
if ! id natter-gui >/dev/null 2>&1; then
  useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin natter-gui
fi
task_release="/opt/natter-gui/releases/$(date -u +%Y%m%dT%H%M%SZ)-$$"
install -d -m 755 "$task_release" /opt/natter-gui/releases
cp -R "$task_root/natter_gui" "$task_root/vendor" "$task_root/scripts" "$task_release/"
cp "$task_root/upstream.lock.json" "$task_root/LICENSE" "$task_root/README.md" "$task_release/"
# Archives extracted by root may inherit a restrictive umask. The dedicated
# service account must be able to read public application files and traverse dirs.
chmod -R a+rX "$task_release"
install -d -m 700 -o natter-gui -g natter-gui /var/lib/natter-gui
systemctl stop natter-gui.service 2>/dev/null || true
ln -s "$task_release" /opt/natter-gui/current.next
mv -Tf /opt/natter-gui/current.next /opt/natter-gui/current
cat > /etc/systemd/system/natter-gui.service <<'UNIT'
[Unit]
Description=Natter GUI management panel
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=natter-gui
Group=natter-gui
WorkingDirectory=/opt/natter-gui/current
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/usr/bin/python3 -m natter_gui --host 0.0.0.0 --port 9080 --state-dir /var/lib/natter-gui
Restart=on-failure
RestartSec=10
TimeoutStopSec=30
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=/var/lib/natter-gui
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
UMask=0077

[Install]
WantedBy=multi-user.target
UNIT
if [ -n "$task_gateway" ]; then
  install -d -m 755 /etc/systemd/system/natter-gui.service.d
  printf 'MAIN_GATEWAY=%s\nHOST_INTERFACE=%s\n' "$task_gateway" "$task_interface" > /etc/natter-gui-route.env
  chmod 600 /etc/natter-gui-route.env
  cat > /etc/systemd/system/natter-gui-route.service <<'UNIT'
[Unit]
Description=Dedicated UID route for Natter GUI
Wants=network-online.target
After=network-online.target
StopWhenUnneeded=yes

[Service]
Type=oneshot
RemainAfterExit=yes
EnvironmentFile=/etc/natter-gui-route.env
ExecStart=/usr/bin/python3 /opt/natter-gui/current/scripts/route_helper.py start --gateway ${MAIN_GATEWAY} --interface ${HOST_INTERFACE}
ExecStop=/usr/bin/python3 /opt/natter-gui/current/scripts/route_helper.py stop --gateway ${MAIN_GATEWAY} --interface ${HOST_INTERFACE}
UNIT
  cat > /etc/systemd/system/natter-gui.service.d/route.conf <<'UNIT'
[Unit]
Requires=natter-gui-route.service
After=natter-gui-route.service
UNIT
fi
systemctl daemon-reload
if [ -n "$task_gateway" ]; then systemctl restart natter-gui-route.service; fi
systemctl enable --now natter-gui.service
echo 'Panel: http://DEVICE_IP:9080'
echo 'Initial password file: /var/lib/natter-gui/bootstrap-password.txt'
echo 'The state directory is preserved on upgrades. Existing per-port Natter units are not migrated automatically.'
