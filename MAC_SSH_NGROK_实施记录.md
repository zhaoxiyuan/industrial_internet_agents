# Mac 单 ngrok 入口 SSH 接入实施记录

> 2026-09-29 更新：本记录前半部分描述旧的单层 Docker Nginx 架构。当前入口已拆为 `/Users/edge_security/ssh_remote/` 下的宿主机 Nginx、独立 SSH 隧道容器和 `a_mac_skill` 内部 Docker Nginx。网络拓扑及运行位置见 `ssh_remote/README.md`。Windows 客户端的 ngrok 地址、`/sshws/`、本地 `10022` 端口和私钥登录步骤保持不变。

日期：2026-09-24。所有新增部署文件位于根目录 `ngrok_ssh/`；`a_mac/` 原有代码与 Docker 配置未修改。Mac 项目目录为 `~/IndustrialAgents/a`，脚本同时支持本机副本的 `a_mac` 目录名。

## 链路

现有：公网 HTTPS → ngrok → Mac `127.0.0.1:18080` → Nginx 容器 `:18080` → 业务容器。

新增：同一个 ngrok 入口 `/sshws/` → Nginx 独立 Basic Auth → `ssh_tunnel` 容器 → **`host.docker.internal:22`，即 Mac 宿主机 SSH**。容器内的 `127.0.0.1:22` 是容器自身，不能用于访问 Mac。Compose 覆盖文件不发布 wstunnel 端口。

## 操作留痕

1. 曾短暂修改 `a_mac/deploy/docker-compose.mac.yml`、`a_mac/deploy/nginx/conf.d/mac-ngrok.conf` 和 `a_mac/.gitignore`，并在 `a_mac/deploy` 添加脚本。按用户要求已全部撤回；`git status` 不再显示 `a_mac/` 改动。
2. 新建 `ngrok_ssh/compose.override.yml`，叠加原 Mac Compose：替换 Nginx 容器内的单个配置挂载，增加独立认证文件挂载及 wstunnel 容器。原业务服务定义不变。
3. 将原 Nginx 路由复制到 `ngrok_ssh/mac-ngrok.conf`，只增加 `/sshws/` 路由。今后原文件路由有变化时，需同步更新此副本，避免覆盖新业务路由。
4. `ngrok_ssh/restrictions.yaml` 默认拒绝，只允许正向 TCP 到 `host.docker.internal:22`，不允许反向隧道或访问其他端口。
5. `ngrok_ssh/install.sh` 交互创建独立 bcrypt 密码文件并安装用户级 LaunchAgent；`ngrok_ssh/start.sh` 等待 Docker Desktop 后启动 Nginx 和隧道，执行 Nginx 语法检查及未认证访问检查。运行日志位于 `~/Library/Logs/industrial-agents-ngrok-ssh.log`。
6. `ngrok_ssh/.gitignore` 排除密码文件及日志。未写入密码、SSH 私钥或 ngrok token。
7. 2026-09-24：为 Windows 客户端验证增加 `.runtime/` 忽略规则；客户端二进制和本机认证头将放在此目录，不提交 Git。

## Mac 上安装与验证

前提：Mac 已开启远程登录，`127.0.0.1:22` 可连接，密钥登录已验证；Docker Desktop 设置登录后启动；有支持 `htpasswd -B` 的程序（可安装 Homebrew `httpd`）。这是**用户登录后**的 LaunchAgent，不是无人登录时也启动的系统守护进程。

```bash
cd ~/IndustrialAgents
bash ngrok_ssh/install.sh
docker compose -p industrial-agents-mac \
  -f a/deploy/docker-compose.mac.yml \
  -f ngrok_ssh/compose.override.yml ps
curl -i http://127.0.0.1:18080/sshws/  # 预期 401
tail -n 50 ~/Library/Logs/industrial-agents-ngrok-ssh.log
```

远端客户端安装同版本 wstunnel。当前 ngrok 地址为 `https://ladylike-overripe-resource.ngrok-free.dev`（以后若变化，以 Mac 上 `http://127.0.0.1:4040/api/tunnels` 为准）。客户端目标必须是 `host.docker.internal:22`，以通过服务端白名单。专用密码可在 Mac 上由 `edge_security` 用户读取 `~/.config/industrial-agents/ngrok-ssh-password`；不要发送到聊天或提交 Git。用权限为 `600` 的文件保存 HTTP 认证头，避免把密码写在命令行参数中：

```bash
mkdir -p ~/.config/wstunnel && chmod 700 ~/.config/wstunnel
read -r -s -p 'Tunnel password: ' TUNNEL_PASSWORD; echo
printf 'Authorization: Basic %s\n' "$(printf 'ssh-tunnel:%s' "$TUNNEL_PASSWORD" | base64 | tr -d '\n')" > ~/.config/wstunnel/mac-headers
unset TUNNEL_PASSWORD
chmod 600 ~/.config/wstunnel/mac-headers
wstunnel client --http-headers-file ~/.config/wstunnel/mac-headers \
  --http-upgrade-path-prefix sshws \
  -L tcp://127.0.0.1:10022:host.docker.internal:22 \
  wss://<当前ngrok域名>
ssh -p 10022 <Mac用户名>@127.0.0.1
```

Windows 验证：从 [wstunnel v10.5.1 官方发布页](https://github.com/erebe/wstunnel/releases/tag/v10.5.1)下载 `wstunnel_10.5.1_windows_amd64.tar.gz` 并解压出 `wstunnel.exe`。先通过当前已能直连的 SSH 读取 Mac 上的专用密码，按上面的格式生成本机 `mac-headers`（勿将密码放入命令行参数）。在一个 PowerShell 窗口运行：

```powershell
.\wstunnel.exe client --http-headers-file $env:USERPROFILE\.config\wstunnel\mac-headers --http-upgrade-path-prefix sshws -L tcp://127.0.0.1:10022:host.docker.internal:22 wss://ladylike-overripe-resource.ngrok-free.dev
```

另一个 PowerShell 窗口运行：

```powershell
ssh -i C:\Users\13021\.ssh\codex_ed25519 -o IdentitiesOnly=yes -p 10022 edge_security@127.0.0.1
```

### Windows 隧道停止后的完整连接步骤

本机可直接使用以下两段 PowerShell。**窗口 1 保持打开**，窗口 2 才能连接。`ngrok_ssh/.runtime/` 已备好 `wstunnel.exe`、`mac-headers` 和 `codex_ed25519`，整个目录被 Git 忽略。另一台 Windows 电脑若要复用，需通过可信的离线方式复制此目录，并将 `$dir` 改成新电脑上的实际目录；Git 克隆不会包含这些文件。

窗口 1：

```powershell
$dir = 'C:\Users\13021\Desktop\agent-skill\industrial_internet_agents\ngrok_ssh\.runtime'
& "$dir\wstunnel.exe" client `
  --http-headers-file "$dir\mac-headers" `
  --http-upgrade-path-prefix sshws `
  -L tcp://127.0.0.1:10022:host.docker.internal:22 `
  wss://ladylike-overripe-resource.ngrok-free.dev
```

窗口 2：

```powershell
$dir = 'C:\Users\13021\Desktop\agent-skill\industrial_internet_agents\ngrok_ssh\.runtime'
ssh -i "$dir\codex_ed25519" -o IdentitiesOnly=yes -p 10022 edge_security@127.0.0.1
```

私钥和 `mac-headers` 都是可用于访问 Mac 的敏感文件，不要提交 Git、发送聊天或放进公开共享盘。复制到其他 Windows 电脑后，检查私钥只允许该电脑的当前用户读取；如果 OpenSSH 报私钥权限过宽，先收紧文件 ACL。当前 ngrok URL 若变化，也需更新窗口 1 的地址。

还应实测：错误认证返回 401；目标改为其他端口或反向隧道被拒绝；飞书 webhook 与现有管理页面仍可用。SSH 公钥免密不代表服务端已禁用密码登录，需另外核实 Mac 的有效 SSH 配置。

## 本机验证与限制

- 原 Mac Compose 与 `ngrok_ssh/compose.override.yml` 的组合已通过 `docker compose config --quiet` 静态解析；已检查最终挂载：Nginx 的 `/etc/nginx/conf.d/agent.conf` 来自 `ngrok_ssh/mac-ngrok.conf`，独立认证文件来自 `ngrok_ssh/htpasswd.ssh`。本机 Docker 读取用户配置时提示权限警告。
- 对比确认 `ngrok_ssh/mac-ngrok.conf` 与原文件相比只新增 `/sshws/` 路由；`git diff -- a_mac` 为空，`a_mac/` 已完全回滚。
- 2026-09-24：通过用户提供的免密 SSH 连接到 Mac，确认 ngrok 正在运行、Docker Desktop 已启动、现有八个 Compose 服务均为 running。Mac 中 Compose 与 Nginx 原文件的 SHA-256 与本机副本一致。随后修正脚本以兼容 Mac 的 `a/` 目录名及 Docker CLI 的实际路径。Docker 镜像拉取、wstunnel 白名单解析、WebSocket 端到端连接和 LaunchAgent 尚待本轮启动验证。
- 2026-09-24：已将 `ngrok_ssh/` 复制到 Mac 的 `~/IndustrialAgents/`；在 Mac 生成随机专用密码，明文仅存于 `~/.config/industrial-agents/ngrok-ssh-password`，bcrypt 哈希存于 `~/IndustrialAgents/ngrok_ssh/htpasswd.ssh`，均为仅用户可读。安装 LaunchAgent 后首次启动因非交互环境缺少 `docker-credential-desktop` 的 `PATH` 而无法拉取镜像；现有 Nginx 仍在运行。已更新 `start.sh` 加入 Docker Desktop 二进制目录，准备重试。
- 2026-09-24：重新触发 LaunchAgent 后拉取 `ghcr.io/erebe/wstunnel:v10.5.1`；Mac `linux/arm64` 镜像压缩总大小约 34.5 MiB。拉取完成后 `a-ssh-tunnel` 与 `a-nginx` 运行，`nginx -t` 通过，未认证 `/sshws/` 返回 401。Compose 同时重建了若干依赖容器；复查 `app_config`、`chat_reply`、`feishu_cfg`、`gateway`、`nginx`、`p6`、`p8p9`、`ssh_tunnel`、`webui` 均为 running。原管理路由未认证返回 401；GET 飞书 webhook 返回 404，此方法不是飞书回调业务验收。
- 2026-09-24：用临时客户端经公网 ngrok 地址连接，wstunnel 服务端日志显示命中 `mac-ssh-only` 限制并连接 `host.docker.internal:22`；SSH 客户端读到 `OpenSSH_10.3` 并进入认证阶段。测试客户端无该 Mac 的私钥，因此登录被拒绝。错误的 Nginx 密码返回 401；目标改成 `host.docker.internal:80` 后服务端日志明确显示 `Rejecting connection with not allowed destination`。临时测试容器已清理。
- 2026-09-24：SSH 握手显示服务端仍提供 `publickey,password,keyboard-interactive` 三种认证方式。现有公钥连接已验证可用；本次未改动系统 SSH 配置。若要关闭密码与交互式认证，应单独检查 macOS 实际 sshd 配置与账号恢复路径后操作。
- 2026-09-24：最终复查 LaunchAgent `com.industrial-agents.ngrok-ssh` 已加载且上次退出码为 0；`a-nginx`、`a-ssh-tunnel` 持续运行；未认证 SSH 路由与原管理路由均返回 401；临时测试容器已清理。
- 2026-09-24：用户要求代执行 Windows 客户端验证。已将官方 `wstunnel_10.5.1_windows_amd64.tar.gz` 下载到 `ngrok_ssh/.runtime/`，用同一发布页的 `checksums.txt` 校验 SHA-256：`EF644F54A42C67A33C651BC80ECA73393A35D4E538657ADDD77C9C9EF920916F`，解压得到 `wstunnel.exe`。`.runtime/` 已加入 `ngrok_ssh/.gitignore`。
- 2026-09-24：通过现有 SSH 从 Mac 读取专用密码，仅在 Windows 本机生成 `ngrok_ssh/.runtime/mac-headers`，未输出密码。Windows ACL 已移除继承，只允许用户 `13021`、SYSTEM 和 Administrators 读取。后台启动 Windows wstunnel 客户端，进程 PID 为 19044，本机 `127.0.0.1:10022` 正在监听。通过该端口使用提供的 `codex_ed25519` 私钥实际登录成功，远端返回 `Darwin` 和 `edge_security`。

Windows 隧道曾由后台进程维持；停止或重启 Windows 后需重新启动客户端。客户端运行时可用以下命令验证：

```powershell
ssh -i C:\Users\13021\.ssh\codex_ed25519 -o IdentitiesOnly=yes -p 10022 edge_security@127.0.0.1
```

- 2026-09-24：用户准备切换网络环境。此前的验证用 SSH 命令已退出；按要求停止 Windows 后台 wstunnel 客户端 PID 19044，并确认 `127.0.0.1:10022` 不再监听。Mac 上的 ngrok、Nginx、wstunnel 服务保持运行，供换网后重连。
- 2026-09-24 17:09 CST：用户换网后要求重试。重新启动 Windows 后台 wstunnel 客户端，PID 12484，确认 `127.0.0.1:10022` 监听。仅通过该本地隧道使用 `codex_ed25519` 登录 Mac 成功，远端返回 `Darwin`、`edge_security`、`2026-09-24T17:09:21+0800`。测试 SSH 命令已退出，wstunnel 客户端继续运行，供用户交互验证。
- 2026-09-24：按用户要求补充上述完整 PowerShell 步骤，并将 `C:\Users\13021\.ssh\codex_ed25519` 复制为 `ngrok_ssh/.runtime/codex_ed25519`，用于手动迁移到其他电脑。副本 ACL 已移除继承，仅用户 `13021`、SYSTEM 和 Administrators 可读取；原私钥未移动或修改。
- 2026-09-24：`git check-ignore` 确认私钥副本、认证头及客户端程序均被 `.runtime/` 规则排除。使用复制后的私钥经当前 `127.0.0.1:10022` 隧道再次登录成功，返回 `Darwin / edge_security`。
- 2026-09-24：针对“单独复制文件夹到另一台电脑是否足够”的问题，确认此前 `.runtime/` 缺少接入说明与启动脚本。新增 `ngrok_ssh/connect-windows.ps1` 和 `ngrok_ssh/PORTABLE-WINDOWS.md`，并复制到 `.runtime/` 作为 `connect-windows.ps1`、`README.md`；脚本按所在目录读取 `wstunnel.exe`、`mac-headers`、`codex_ed25519`，启动隧道并连接 SSH，SSH 退出后停止该隧道。已做 PowerShell 语法解析和文件齐全检查。Mac ED25519 主机指纹通过运行中的 ngrok SSH 隧道读取并写入便携说明：`SHA256:TtNILh6pZig5h4600zbi0gHJpi5ECPkh2pJwy+iLSz4`。换网后直连主机名 `bianyuaacstudio` 已不可解析，隧道连接仍成功。
- 2026-09-24：用户要求结束 wstunnel，已确认 Windows 本地客户端停止。随后按用户要求检查并推送 `a_mac/`：该目录没有未提交改动或未跟踪代码；`git fetch origin a` 后本地 `a` 与 `origin/a` 均为 `031ca9a`；`git push origin a` 返回 `Everything up-to-date`。本次未创建空提交，也未将根目录的隧道文件或文档纳入 `a_mac/` 提交。
- wstunnel 官方镜像的入口是 `dumb-init`，覆盖文件显式执行 `/home/app/wstunnel server`，避免把 `server` 当作可执行文件。
- `ngrok_ssh/mac-ngrok.conf` 是原文件的副本，原项目路由更新后必须同步。脚本不会修改或重新安装现有 ngrok LaunchAgent。

## 回滚

在 Mac 执行：

```bash
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.industrial-agents.ngrok-ssh.plist"
cd ~/IndustrialAgents
/Applications/Docker.app/Contents/Resources/bin/docker compose -p industrial-agents-mac -f a/deploy/docker-compose.mac.yml up -d nginx
docker rm -f a-ssh-tunnel
```

原 `a_mac/` 配置与 ngrok LaunchAgent 不受本方案文件修改。

参考：[wstunnel 官方仓库](https://github.com/erebe/wstunnel)、[限制规则](https://github.com/erebe/wstunnel/blob/main/restrictions.yaml)、[Nginx WebSocket 文档](https://nginx.org/en/docs/http/websocket.html)。
