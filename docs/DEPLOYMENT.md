# 部署、迁移和回退

## 原生 Linux

需要 Python 3.11+、systemd。可选 UID 路由需要 iproute2。安装脚本不自动安装 Docker，也不下载并执行第三方安装脚本。

```bash
sudo ./scripts/install.sh
sudo cat /var/lib/natter-gui/bootstrap-password.txt
systemctl status natter-gui --no-pager
journalctl -u natter-gui -n 100 --no-pager
```

安装代码写入 `/opt/natter-gui/releases/<时间戳>`，`/opt/natter-gui/current` 指向当前版本。状态保存在 `/var/lib/natter-gui`。重复执行安装脚本会保留状态并切换到新的代码目录。

默认监听设备所有 IPv4 地址的 9080 端口。可以在 root 管理的 systemd override 中改为指定 LAN 地址或 `127.0.0.1`，再通过 HTTPS 反向代理访问。HTTPS 管理访问配合 `--secure-cookie`。

### 主路由与旁路由

```bash
sudo ./scripts/install.sh --gateway 192.168.1.1 --interface end0
```

只影响 `natter-gui` 账户。面板和它启动的 Natter / NatterCheck 都使用这条出口；主机其他账户沿用原路由。配置在 `/etc/natter-gui-route.env`，策略优先级 13457、表 34568。

网关变更后重新执行带参数的安装脚本，或停止面板和路由单元、修改配置后重新启动。已有该选项的原生安装，在后续不带网络参数升级时继续保留配置。关闭专用路由时：

```bash
sudo systemctl stop natter-gui natter-gui-route
sudo mv /etc/systemd/system/natter-gui.service.d/route.conf /etc/systemd/system/natter-gui.service.d/route.conf.disabled
sudo systemctl daemon-reload
sudo systemctl start natter-gui
```

为设备配置 DHCP 保留地址。主路由需支持 UPnP，或者手动建立到设备绑定端口的转发。多层 NAT 需要分别满足网络条件；面板不自动接管主路由登录。

## 从已有 Natter 部署迁移

首版管理自己的子进程，不直接修改现有 `natter@xxx.service`。迁移时先保存旧配置、启用状态和外网地址。

1. 在面板新增对应目标 IP、端口和协议，先取消“保存后启用”。
2. 记录旧服务的绑定端口，在旧部署中停止并取消对应实例的开机启动。
3. 在面板启用新服务，从移动网络验证映射。
4. Plex / Emby 客户端按新地址连接。没有自动修改媒体服务器或 Tracker 配置。
5. 若需要回退，先停止面板中的映射，再启动旧实例；避免同一端口被两个服务占用。

删除一个 GUI 服务会终止它的 Worker 并清理对应配置。原版 UPnP 映射依靠短租期自然回收，不承诺停止时立刻删除主路由条目。关闭 GUI 的主服务会停止全部子进程，配置仍保留。

## Docker

使用 Linux host 网络，数据卷保存管理员与映射配置。Docker Desktop 在其他系统上的网络模型与 Linux 原生不同，应在目标 Linux / NAS 环境验收。

```bash
docker compose up -d --build
docker compose exec natter-gui cat /data/bootstrap-password.txt
docker compose logs --tail=100 natter-gui
```

绑定现有宿主机目录时，目录需允许容器 UID 10001 写入。命名卷第一次初始化会继承镜像数据目录的所有权。

升级：

```bash
docker compose pull
docker compose up -d --no-build
```

镜像版本固定为 `sha-...` 时可按旧镜像标签回退，保留同一状态卷。数据结构升级必须在未来版本提供备份和迁移机制；当前配置格式为 schema_version 1。

## 原生版本回退

先停止面板，备份状态目录，再把 `/opt/natter-gui/current` 改为之前保留的版本目录，重新启动。管理员应检查两个版本的配置格式兼容性。

## 应用层验证

- Emby：客户端手动添加当前映射地址；检查账户的远程访问权限。
- Plex：浏览器使用新入口；需要客户端自动发现时，在服务端 Network 的 Custom server access URLs 中追加新地址。
- qBittorrent：映射 BT 监听端口，TCP 与 UDP 分别确认；公网端口变化时需正确向 Tracker 宣告。下载客户端的出站代理配置由应用管理。
- socket 转发不会保留远端来源 IP。目标软件将看到转发设备地址，需确认信任该 LAN 地址的免认证设置。

## 密码恢复

停止面板，以其服务账户运行交互式重置，再启动面板：

```bash
sudo systemctl stop natter-gui
cd /opt/natter-gui/current
sudo -u natter-gui python3 -m natter_gui --state-dir /var/lib/natter-gui --reset-password
sudo systemctl start natter-gui
```

不要删除状态目录来重置密码；那会删除全部服务配置。
