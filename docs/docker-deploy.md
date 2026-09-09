# Docker 部署教程（Ubuntu / Debian / ARM64 NAS）

项目仓库：[sosove-shopline-dashboard](https://github.com/sosoveooo-bit/sosove-shopline-dashboard)

镜像：`ghcr.io/sosoveooo-bit/sosove-shopline-dashboard:latest`

适用：Debian 12/13、Ubuntu 22.04/24.04/26.04，使用 `docker compose` 插件。已安装的 Compose v2/v5 通过命令可用性检查后直接复用。容器中已安装 Python、GA4 依赖和网页代码；一键向导会使用宿主机 python3 配置 `.env`。ARM64 服务器使用源码原生构建，GHCR 预构建镜像目前仍为 amd64。

**镜像不包含你的 Shopline Token、GA4 私钥或订单。配置需要在 VPS 上单独提供。**

2026-09-09 发布前检查：现有 GHCR 的 `latest` 匿名拉取返回 `unauthorized`。在包所有者确认公开之前，建议直接使用[第 8 节源码构建](#8-不用-ghcr-登录从公开源码构建)，不需要 GHCR 密码；配置、访问和更新说明相同。

如果 VPS 已运行旧版 Python/systemd 面板或 Nginx，先在新的 `/opt/sosove-dashboard-docker` 目录、8000 端口测试。本教程不会自动停掉旧服务、替换 80 端口或迁移旧配置。

## 最简单：一条命令安装

在受支持的 Debian / Ubuntu 的 **root SSH 终端**执行下面一行。该命令下载并以 root 权限运行本仓库的[安装脚本](../deploy/install_docker.sh)，会安装缺少的 Docker/Compose、git、python3 辅助工具，并构建运行容器。可以先打开脚本查看内容。

```bash
(sosove_installer=$(mktemp) && curl -fsSL https://raw.githubusercontent.com/sosoveooo-bit/sosove-shopline-dashboard/main/deploy/install_docker.sh -o "$sosove_installer" && bash "$sosove_installer")
```

要求：Debian 12/13 或 Ubuntu 22.04/24.04/26.04，已有 curl，能访问 GitHub、Docker 官方源、Docker Hub 和 Python 包源。已经有正常 Docker 环境时直接复用；检测到不兼容的既有 Docker/containerd 时会停止提示，不自动卸载其他服务。

按提示输入：

1. Shopline 店铺域名，例如 `jp-sosove.myshopline.com`。
2. Shopline API Token，输入不回显。
3. 自己设置的面板登录密码，至少 16 位，输入不回显。
4. 访问端口，回车使用 `8000`。
5. 绑定地址：公网 IP 访问填 `0.0.0.0`；仅本机/Nginx/SSH 隧道访问回车即可。

公网模式安装完成后，浏览器打开 `http://你的VPS公网IP:8000/`（按实际端口替换），云安全组放行该端口并限制访问来源。HTTP 不加密密码和订单，长期使用应配置 HTTPS。仅本机模式下，脚本会显示 SSH 隧道命令。

一键安装使用源码构建，**不需要 GHCR 登录密码**。目录是 `/opt/sosove-dashboard-docker-source`；重复执行会更新干净的 main 分支、保留 `.env`、密钥和快照卷。不会自动停止旧面板或修改 Nginx。若端口已占用，脚本停止并提示更换端口，不杀掉其他程序。

GA4 不是安装必填项；已有 `.env` 中的 GA4 设置会保留，新安装先启用 Shopline。补充 GA4 时按第 4 节操作，**把示例中的部署目录改成 `/opt/sosove-dashboard-docker-source`**。一键脚本不会把 Windows 私钥自动上传到 VPS。

不使用一键脚本时，再按下面分步教程操作，两种方式选一种即可。

### Debian 12 ARM64 NAS（Docker 已安装）

如果 `docker version` 显示 `linux/arm64`，`docker info --format '{{.DockerRootDir}}'` 显示 `/vol1/docker`，且 `/vol1` 是有足够空间的数据盘，在 NAS 的 root SSH 终端执行：

```bash
(sosove_installer=$(mktemp) && curl -fsSL https://raw.githubusercontent.com/sosoveooo-bit/sosove-shopline-dashboard/main/deploy/install_docker.sh -o "$sosove_installer" && SOSOVE_INSTALL_DIR=/vol1/sosove-dashboard-docker SOSOVE_REUSE_DOCKER=1 bash "$sosove_installer")
```

- 项目目录：`/vol1/sosove-dashboard-docker`，不放到较小的系统分区。
- 镜像和快照卷仍由现有 Docker 管理，保留 `/vol1/docker`，无需迁移存储或重启 Docker。
- `SOSOVE_REUSE_DOCKER=1` 禁止安装、更改或启动 Docker 服务；Docker 不可用时停止提示，通过 NAS 容器管理界面处理。
- 如缺少 git/python3/curl，脚本只安装这些下载和配置工具，不升级系统或替换 Docker。
- 自动从 Docker 服务端识别 `arm64/aarch64` 并构建 `linux/arm64`，不会拉取本仓库的 amd64-only GHCR 镜像。
- 安装器保守预留项目盘 256 MiB、Docker 盘 2 GiB 构建空间；不足时停止，不自动清理数据。这是安装器的余量检查，不是固定镜像大小。

要从局域网打开，绑定地址填 `0.0.0.0`，默认端口 `8000`；用 **NAS 的局域网 IP** 访问 `http://NAS_IP:8000/`。系统信息中显示的公网地址可能是出口/代理地址，不能据此保证可以直连 NAS；公网访问需另行配置端口映射、内网穿透或 HTTPS 反向代理。不要暴露 NAS 管理端口。

GA4 文件放 `/vol1/sosove-dashboard-docker/secrets/ga.json`，`.env` 中仍填写容器路径 `/app/secrets/ga.json`。第 4 节的组权限设置和后续 Compose 命令均在这个项目目录执行。

全新 Debian 主机的 Docker 安装分支使用 [Docker Debian 官方源](https://docs.docker.com/engine/install/debian/)；下面第 1 节的手动安装命令只适用于 Ubuntu。NAS 上已有 Docker 时，不要重新运行系统级 Docker 安装命令。

## 1. 确认 Docker 已安装

通过 SSH 登录 VPS。后续 VPS 命令按 root 用户编写，普通用户需要相应的 sudo 权限。

```bash
docker version
docker compose version
```

两条命令都正常就跳到第 2 节。没有安装的 Ubuntu 新服务器，使用 Docker 官方 APT 源：

```bash
apt update
apt install -y ca-certificates curl openssl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
docker compose version
```

已有其他 Docker/containerd 安装时，不要直接卸载或重装，以免影响其他容器；先按[Docker 官方安装说明](https://docs.docker.com/engine/install/ubuntu/)处理兼容问题。

## 2. 下载部署文件

仅第一次部署执行；已有目录时不要覆盖旧 `.env`。

```bash
mkdir -p /opt/sosove-dashboard-docker
cd /opt/sosove-dashboard-docker
curl -fsSL https://raw.githubusercontent.com/sosoveooo-bit/sosove-shopline-dashboard/main/docker-compose.yml -o docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/sosoveooo-bit/sosove-shopline-dashboard/main/.env.example -o .env.example
test -f .env || cp .env.example .env
install -d -m 0750 -o root -g 10001 secrets
chmod 600 .env
```

生成一个面板登录令牌，记住结果，下一步填入 `DASHBOARD_ACCESS_TOKEN`：

```bash
openssl rand -hex 24
nano .env
```

这里的面板登录令牌由你自己生成，**不是 SSH 密码，也不是 Shopline Token**。

## 3. 填写 .env

保留 `.env.example` 的其余配置，重点修改以下值：

```dotenv
SHOPLINE_API_BASE_URL=https://你的店铺.myshopline.com
SHOPLINE_STORE_DOMAIN=你的店铺.myshopline.com
SHOPLINE_STOREFRONT_DOMAINS=你的商城域名
SHOPLINE_ACCESS_TOKEN=你的Shopline访问Token
SHOPLINE_ORDERS_ENDPOINT=/orders
SHOPLINE_PRODUCTS_ENDPOINT=/products
SHOPLINE_ORDER_ATTRIBUTION_ENDPOINT=/orders/order_attribution_info.json
SHOPLINE_DEFAULT_CURRENCY=JPY
SHOPLINE_TIMEZONE=Asia/Shanghai

DASHBOARD_ACCESS_TOKEN=上一步生成的随机字符串
DASHBOARD_ROLE=admin
DASHBOARD_BIND_IP=127.0.0.1
DASHBOARD_PORT=8000
DASHBOARD_IMAGE_TAG=latest
```

时区应与你要核对的 Shopline 报表时区一致。当前本地面板使用 `Asia/Shanghai`，迁移时不要随意改成其他时区。成本、运费和广告花费需要按业务填写，示例成本不是实际净利润依据。

GA4 可选；需要 GA4 转化率时再填写：

```dotenv
GA4_PROPERTY_ID=你的数字PropertyID
GA4_KEY_EVENT_NAME=purchase
GA4_CONVERSION_METRIC=userKeyEventRate
GA4_CONVERSION_MODE=key_event_rate
GA4_SERVICE_ACCOUNT_FILE=/app/secrets/ga.json
GA4_SERVICE_ACCOUNT_JSON=
```

暂时不用 GA4，就让 `GA4_PROPERTY_ID`、`GA4_SERVICE_ACCOUNT_FILE` 和 `GA4_SERVICE_ACCOUNT_JSON` 都留空。Shopline 订单仍可使用。

## 4. 上传 GA4 私钥（仅使用 GA4 时）

在你的 Windows PowerShell 中执行，替换本机文件和 VPS IP：

```powershell
scp "C:\path\to\ga.json" root@你的VPS公网IP:/opt/sosove-dashboard-docker/secrets/ga.json
```

回到 VPS，授予容器的非 root 用户读取权限：

```bash
cd /opt/sosove-dashboard-docker
chown root:10001 secrets secrets/ga.json
chmod 750 secrets
chmod 640 secrets/ga.json
```

路径对应关系：

| 位置 | 路径 |
| --- | --- |
| VPS 上的私钥 | `/opt/sosove-dashboard-docker/secrets/ga.json` |
| 容器读取的私钥 | `/app/secrets/ga.json` |
| `.env` 中应填写 | `GA4_SERVICE_ACCOUNT_FILE=/app/secrets/ga.json` |

不要把 `E:\ga4\ga.json` 等 Windows 路径填进 Linux 容器。服务账号必须在目标 GA4 属性中有读取权限。

## 5. 启动与检查

```bash
cd /opt/sosove-dashboard-docker
docker compose config --quiet
docker compose pull
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:8000/api/health
docker compose logs --tail=50 dashboard
```

健康接口返回 `"ok": true` 表示服务已启动。进入网页后用 `DASHBOARD_ACCESS_TOKEN` 的值登录；这一步不需要 Shopline Token。

首次查询没有缓存的日期会等待 Shopline/GA4 返回。后续打开先显示带原同步时间的快照，再后台更新；不要把快照当成刚抓取的数据。

### 安全测试：无需域名

默认仅绑定 VPS 的 `127.0.0.1:8000`。在 Windows PowerShell 打开 SSH 隧道并保持该窗口运行：

```powershell
ssh -N -L 18000:127.0.0.1:8000 root@你的VPS公网IP
```

在本机浏览器打开 [http://127.0.0.1:18000/](http://127.0.0.1:18000/)。这里看到的是 VPS 容器，不是本地 8787 面板。

### 直接用公网 IP 访问

需要 `http://VPS_IP:8000/` 时，把 `.env` 改为：

```dotenv
DASHBOARD_BIND_IP=0.0.0.0
DASHBOARD_PORT=8000
```

然后重新创建容器：

```bash
docker compose up -d --force-recreate
```

云安全组放行 TCP 8000，建议来源仅允许自己的公网 IP。**HTTP 不加密订单和登录令牌，仅用于受限制的临时测试；长期使用请配域名、Nginx/Caddy 和 HTTPS。** Docker 发布端口可能绕过 UFW 规则，不要只依赖 UFW。[Docker 防火墙说明](https://docs.docker.com/engine/install/ubuntu/#firewall-limitations)

### 原来已有 Nginx / 80 端口

保留 `DASHBOARD_BIND_IP=127.0.0.1`。确认容器数据正确、备份现有 Nginx 配置后，才把对应面板站点的上游改为：

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 150s;
}
```

执行 `nginx -t` 成功后再 `systemctl reload nginx`。不要停止整台服务器的 Nginx，也不要直接把容器绑到已占用的 80 端口。确认新面板稳定后，才考虑停用旧的 `sosove-dashboard.service`。

## 6. 后续更新、回退与数据保留

更新镜像：

```bash
cd /opt/sosove-dashboard-docker
docker compose pull
docker compose up -d
docker compose ps
```

**只运行 `restart` 不会加载新镜像或更新环境变量。** 改 `.env` 后使用 `docker compose up -d --force-recreate`。

每次成功发布同时提供完整 Git commit SHA 标签。要回退，在 `.env` 中将 `DASHBOARD_IMAGE_TAG` 改为上一个成功构建的完整提交 SHA，再执行 `pull` 和 `up -d`。本次之前的旧镜像使用不同的用户和配置，优先选择本版之后经过同样测试的标签。

快照保存到 Compose 命名卷 `dashboard-runtime`；`.env` 和 `secrets/ga.json` 保留在 VPS 部署目录。普通更新、重建容器及 `docker compose down` 不删除命名卷；**不要执行 `docker compose down -v`，除非确定要删除快照缓存**。部署目录名会影响命名卷的实际名称，后续保持同一个目录运行 Compose。

如需备份，可在安全位置使用 `tar -czf` 备份 `.env` 和 `secrets/`；备份含密钥，权限设为 `600`，不要放进 GitHub 或公开下载目录。订单的权威数据仍在 Shopline；快照不是订单数据库备份。

日志限制为每个文件 10 MB、最多 3 个文件。`restart: unless-stopped` 在进程退出和 Docker 重启后恢复容器，但手动停止的容器不会自行恢复；`unhealthy` 只是健康标记，Docker 本身不会仅因为该标记自动重启，先看日志再决定重启。[Compose 服务设置](https://docs.docker.com/reference/compose-file/services/)

## 7. 常见问题

| 报错 / 现象 | 处理 |
| --- | --- |
| `unauthorized` 拉取失败 | 先检查 GitHub Actions 本次发布是否成功。仓库 Public 不等于 GHCR 镜像 Public；打开[镜像设置](https://github.com/users/sosoveooo-bit/packages/container/package/sosove-shopline-dashboard)检查可见性。也可以直接使用第 8 节源码构建。 |
| 私有镜像需要密码 | `docker login ghcr.io -u sosoveooo-bit` 中填 GitHub PAT，权限为 `read:packages`，不是 GitHub/SSH 登录密码。公开镜像可匿名拉取。[GitHub 官方说明](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry) |
| `Set DASHBOARD_ACCESS_TOKEN` | `.env` 缺少面板令牌，先生成随机值并填写，不能留空。 |
| `bind source path does not exist` | 执行第 2 节创建 `secrets` 目录；不用 GA4 时也需要这个空目录。 |
| GA4 `FileNotFoundError` | 核对 VPS 文件、挂载和容器路径 `/app/secrets/ga.json`。 |
| GA4 `Permission denied` | 按第 4 节设置目录和文件的组为 `10001`，目录 `750`、文件 `640`。 |
| GA4 属性 `403` | 服务账号未获得该数字 Property ID 的权限，去 GA4 属性访问管理添加相应账号。 |
| `port is already allocated` | 检查占用者；改 `DASHBOARD_PORT=8001` 后重新 `up -d`，不要直接杀掉别的服务。 |
| `PermissionError: /app/app.py`，容器不断重启 | 旧 Dockerfile 没有统一 NAS 源码权限。新版在镜像内统一程序目录 `755`、程序文件 `644`。执行下面的更新修复命令，不要将整个目录设为 `777`，也不要改用 root 运行容器。 |
| 本机健康检查正常，公网打不开 | 检查是否仍绑定 `127.0.0.1`、云安全组和访问端口。使用 SSH 隧道时浏览器访问本机 `18000`。 |
| 没有真实订单 / 出现演示数据 | 填写有效的 Shopline API 地址和 Token，重新创建容器，再查看面板“接口测试”。 |
| `no matching manifest`（ARM VPS） | 当前预构建镜像为 amd64；ARM 使用下面的源码构建。 |

NAS 源码安装遇到 `/app/app.py` 权限错误时，在原部署目录更新并重建：

```bash
cd /vol1/sosove-dashboard-docker
git pull --ff-only
docker compose -f docker-compose.yml -f compose.build.yml build
docker compose -f docker-compose.yml -f compose.build.yml up -d --pull never --force-recreate
```

这些命令保留 `.env`、GA4 私钥和快照卷。原端口若是 `8001`，启动后用 `curl -fsS http://127.0.0.1:8001/api/health` 检查。没有更改依赖时无需使用 `--no-cache`，新增权限修复层会自动参与构建。

## 8. 不用 GHCR 登录：从公开源码构建

这条路径只需要能访问 GitHub、Docker Hub 和 Python 包源，不需要 GHCR 密码。

```bash
apt install -y git
git clone https://github.com/sosoveooo-bit/sosove-shopline-dashboard.git /opt/sosove-dashboard-docker-source
cd /opt/sosove-dashboard-docker-source
cp .env.example .env
install -d -m 0750 -o root -g 10001 secrets
chmod 600 .env
nano .env
```

按第 3-4 节填配置并上传私钥，然后：

```bash
docker compose -f docker-compose.yml -f compose.build.yml build --pull
docker compose -f docker-compose.yml -f compose.build.yml up -d --pull never
```

源码方式后续更新：

```bash
git pull --ff-only
docker compose -f docker-compose.yml -f compose.build.yml build --pull
docker compose -f docker-compose.yml -f compose.build.yml up -d --pull never
```

源码构建和镜像拉取选一种即可，后续继续使用同一种方式。
