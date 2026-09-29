# a/deploy — Docker 化部署手册

把 `a/`(P6-P9 子项目)打包成 Docker,部署到**有公网 IP 的 Ubuntu 22.04 服务器**,通过真实域名 + Let's Encrypt HTTPS + Nginx 单端口聚合对外服务。

**适用场景**:不想每次手 `python aaaseverstart.py` 起一堆进程,想用 systemd-like 方式管理 7 个服务 + 自带 HTTPS。

**当前部署目标**(2026-09-20):
- 服务器:`103.236.71.83` (Ubuntu 22.04)
- 域名:`jlkwihudbifhaej.dpdns.org` (dpdns 动态 DNS)
- 证书:Let's Encrypt + certbot 自动续期
- 鉴权:`/admin/*` Nginx BasicAuth

---

## 架构总览

```
Internet
  │
  │  HTTPS 443 / HTTP 80
  ▼
┌────────────────────────────────────────────────┐
│  Nginx (容器)                                  │
│  - 80 → 301 跳 443                              │
│  - /webhooks/feishu/  →  gateway:8787           │
│  - /admin/p6/         →  p6:5002  [BasicAuth]  │
│  - /admin/config/     →  app_config:5000 [BA]   │
│  - /admin/feishu-cfg/ →  feishu_cfg:5003 [BA]   │
└────────────┬───────────────────────────────────┘
             │ (内部 bridge 网络 + host 网络两种)
             ▼
┌────────────────────────────────────────────────┐
│  后端服务 (6 容器 + 1 宿主机进程)                │
│  - gateway        (Node.js,    :8787)          │
│  - p6             (Python,     :5002) FastAPI  │
│  - p8p9           (Python,     :8089) Flask     │
│  - app_config     (Python,     :5000) Flask     │
│  - feishu_cfg     (Python,     :5003) Flask     │
│  - webui          (Python,     :8080) host 网络 │
│  - chat_reply     (Python,     daemon) 宿主机跑  │
└────────────────────────────────────────────────┘
```

---

## 文件清单

| 文件 | 角色 |
|---|---|
| `docker-compose.yml` | 7 服务编排 + 内部网络 |
| `Dockerfile.base` | Python 3.12-slim + tini + curl 公共基础 |
| `Dockerfile.p6` | P6 监测 + A5/A6 + P7 调度镜像 |
| `Dockerfile.p8p9` | P8P9 状态机镜像 |
| `Dockerfile.app_config` | LLM/VL 配置 UI 镜像 |
| `Dockerfile.feishu_cfg` | 飞书配置 UI 镜像 |
| `Dockerfile.webui` | 飞书卡片回调入口镜像(host 网络) |
| `nginx/nginx.conf` | Nginx 主配置(events + http) |
| `nginx/conf.d/agent.conf` | server 块(443 HTTPS + 路由) |
| `nginx/htpasswd.example` | BasicAuth 文件模板 |
| `.env.example` | 环境变量占位 |
| `.gitignore` | 忽略 htpasswd / 缓存 |
| `README.md` | 本文件 |

**复用**:`a/gateway/Dockerfile`(Node.js Channel Gateway 自带 Dockerfile)

---

## 一次性部署步骤

### 1. 服务器环境准备

```bash
ssh user@103.236.71.83

# 基础工具
sudo apt update && sudo apt install -y docker.io docker-compose-v2 certbot apache2-utils
sudo systemctl enable --now docker

# 把当前用户加入 docker 组(避免每次 sudo)
sudo usermod -aG docker $USER
exit
# 重新登录让 group 生效
```

### 2. 上传项目

```bash
# 在本地 PowerShell / Git Bash
scp -r "C:\Users\13021\Desktop\agent-skill\industrial_internet_agents" \
    user@103.236.71.83:/opt/
```

### 3. 申请 TLS 证书

⚠ **80 端口必须空闲**(申请时 certbot 会临时占 80)

```bash
# 在服务器上
cd /opt/industrial_internet_agents/a/deploy
# 临时停可能占 80 的服务
sudo systemctl stop nginx 2>/dev/null || true
docker compose down 2>/dev/null || true

# standalone 模式申请
sudo certbot certonly --standalone \
    -d jlkwihudbifhaej.dpdns.org \
    --register-unsafely-without-email --agree-tos \
    -m your-email@example.com

# 验证证书已生成
sudo ls /etc/letsencrypt/live/jlkwihudbifhaej.dpdns.org/
```

### 4. 生成 BasicAuth

```bash
# 创建 / 覆盖 htpasswd 文件(已有 htpasswd.example,直接覆盖)
sudo htpasswd -B -c nginx/htpasswd admin
# 输入两次密码

# 验证
cat nginx/htpasswd
# 应类似:admin:$2y$05$...
```

### 5. 准备 .env

```bash
# 复制模板
cp .env.example ../.env

# 编辑填入真实值(LLM API Key、飞书凭据等)
nano ../.env
```

### 6. 启动

```bash
cd /opt/industrial_internet_agents/a/deploy

# 构建镜像(首次 ~5-10 分钟;后续增量 < 1 分钟)
docker compose build

# 后台启动
docker compose up -d

# 看状态
docker compose ps
# 期望:7 个 service 都是 running / healthy
```

### 7. 启动宿主机 chat_reply(不进容器)

```bash
cd /opt/industrial_internet_agents/a
# chat_reply 通过 aaaseverstart.py 拉起
python aaaseverstart.py start chat_reply

# 用 systemd 兜底开机自启:
sudo tee /etc/systemd/system/chat-reply.service > /dev/null <<EOF
[Unit]
Description=a/ chat_reply daemon
After=network.target docker.service

[Service]
Type=simple
User=$USER
WorkingDirectory=/opt/industrial_internet_agents/a
ExecStart=/usr/bin/python3 aaaseverstart.py run chat_reply
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now chat-reply.service
```

---

## 验证清单

部署完成后,逐条跑通:

```bash
# 1. TLS 证书有效
curl -vI https://jlkwihudbifhaej.dpdns.org/ | grep -i "subject:"
# 期望:subject: ... CN = jlkwihudbifhaej.dpdns.org

# 2. 80 → 443 跳转
curl -I http://jlkwihudbifhaej.dpdns.org/
# 期望:301 + Location: https://...

# 3. 飞书 webhook 通(让飞书去 POST;此处用 GET 看网关响应)
curl -i https://jlkwihudbifhaej.dpdns.org/webhooks/feishu/P8
# 期望:401 / 405 / 200(gateway 响应即可)

# 4. BasicAuth 拦截
curl -i https://jlkwihudbifhaej.dpdns.org/admin/p6/
# 期望:401 + WWW-Authenticate: Basic

# 5. BasicAuth 通过后能进
curl -i -u admin:你的密码 https://jlkwihudbifhaej.dpdns.org/admin/p6/api/health
# 期望:200 + {"ok": true}

# 6. 三个 admin UI 浏览器打开
#    https://jlkwihudbifhaej.dpdns.org/admin/p6/
#    https://jlkwihudbifhaej.dpdns.org/admin/config/
#    https://jlkwihudbifhaej.dpdns.org/admin/feishu-cfg/

# 7. WebSocket 通(P6 实时推送用)
curl -i -u admin:你的密码 \
     -H "Upgrade: websocket" -H "Connection: Upgrade" \
     https://jlkwihudbifhaej.dpdns.org/admin/p6/ws
# 期望:101 Switching Protocols

# 8. 数据持久化
docker compose down
docker compose up -d
ls -la /opt/industrial_internet_agents/a/data/jobs/
# 期望:之前的 job 目录还在
```

---

## 日常运维

### 看日志

```bash
# 所有服务
docker compose logs -f --tail 100

# 单个服务
docker compose logs -f p6

# 实时跟踪 + grep
docker compose logs -f p8p9 | grep -i error
```

### 重启 / 停止

```bash
# 重启单个服务
docker compose restart p8p9

# 全部重启
docker compose restart

# 停掉全部(数据保留)
docker compose down

# 停掉并清卷(⚠ 会丢 data/)
docker compose down -v
```

### 更新代码后重建

```bash
# 只重建改了 Dockerfile 或 requirements 的服务
docker compose build p6
docker compose up -d p6

# 改了 Python 代码(p6 之类),但 Dockerfile 没变,Docker 会复用缓存,很快
```

### 证书自动续期

```bash
# 验证续期流程
sudo certbot renew --dry-run

# 真续期(成功后自动 reload nginx)
sudo certbot renew

# 自动续期 cron(每天凌晨 3 点检查)
echo '0 3 * * * root certbot renew --quiet --deploy-hook "docker exec a-nginx nginx -s reload"' \
    | sudo tee /etc/cron.d/certbot-renew
```

### 加新 BasicAuth 账号

```bash
cd /opt/industrial_internet_agents/a/deploy
htpasswd -B nginx/htpasswd newuser
# 密码输两次
# 不用重启 nginx,文件每次请求都重读
```

---

## 故障排查

### 容器起不来

```bash
docker compose ps          # 看哪个 exited
docker compose logs p6     # 看具体错
# 常见:
#   - .env 缺字段 → 看 .env 是否完整
#   - 端口冲突 → lsof -i :80 / lsof -i :443
#   - 镜像 build 失败 → docker compose build --no-cache p6
```

### nginx 502 Bad Gateway

某个 upstream 服务没起来。检查:

```bash
docker compose ps                      # 哪个 exited?
docker compose logs p6                 # 看 p6 错
docker exec a-nginx wget -q -O- http://p6:5002/api/health
# 直连验证 p6 是不是真的在 5002 响应
```

### 飞书回调按钮无响应

链路:飞书 → Nginx → gateway → webui → p8p9

```bash
# 1. gateway 有没有收到
docker compose logs gateway | grep webhook

# 2. webui(host 网络)有没有转发
sudo ss -tlnp | grep 8080   # 宿主机 8080 应被 a-webui 占用
docker logs a-webui | tail -50

# 3. p8p9 有没有处理
docker compose logs p8p9 | grep feishu_card_callback
```

### 数据丢失 / 容器重启后空白

数据持久化靠 volume bind:`./data` → `/srv/app/data`。
如果 bind 路径写错,容器写的就是镜像内 ephemeral 层,重启即丢。

```bash
docker inspect a-p8p9 | grep -A 10 Mounts
# 应看到 Source: /opt/industrial_internet_agents/a/data
```

### 子路径 404 (admin/p6/xxx 静态资源加载失败)

某些前端页面用绝对路径 `/static/...` 引用,在子路径下变 404。
**临时修复**:在浏览器 F12 → Network 找 404 资源。
**长期修复**:
- 改 HTML 用相对路径,或
- Nginx `sub_filter` 把响应里的 `/static/` 替换成 `/admin/p6/static/`

---

## 与本地开发的差异

| 项 | 本地 | Docker |
|---|---|---|
| 启动方式 | `python frontend/start_all.py` | `docker compose up -d` |
| 服务进程数 | 5(P1-P5 用不到) | 6 容器 + 1 宿主机 |
| .env 路径 | `a/.env` | 同上(docker 读它) |
| 数据目录 | `a/data/jobs/` | 同上(volume bind) |
| 日志 | `aaaseverstart.py start` 自动 | `docker compose logs` |
| 改代码后 | 重启 python | `docker compose restart <svc>` |
| 改依赖后 | pip install | 改 Dockerfile + `docker compose build` |

**改业务代码后**:
1. 单文件改:直接 `docker compose restart <svc>`(Python 不支持热重载)
2. 改 `requirements.txt` / Dockerfile:需要 `docker compose build <svc>` + `up -d`
3. 改 gateway(Node.js):需要 `docker compose build gateway` + `up -d`

---

## 已知的妥协

| 妥协 | 原因 | 长期方案 |
|---|---|---|
| `a/web/server.py` 用 `network_mode: host` | 代码写死 127.0.0.1,没 CLI 参数 | 改 web/server.py 支持 `--host`,然后换回 bridge |
| `chat_reply` 不进容器 | 进程结构复杂,宿主机跑更稳 | 容器化要重写 systemd-style supervision |
| `feishu_cfg` 容器挂出多目录 | 它要同时写 a/.env + a/gateway/.env + a/gateway/config/*.json | 改 feishu_config_app.py 走单一挂载点 |
| 4 个服务没现成 `/api/health` | 历史代码 | **本部署已加上(每处 2 行)** |
| `a/requirements.txt` 缺 flask/fastapi/uvicorn | 历史遗漏 | Dockerfile 里补 `pip install` |

---

## 相关文档

- 主项目 CLAUDE.md: `../CLAUDE.md`(整体项目规范)
- a/ README: `../README.md`(a/ 子项目说明)
- P6-P8 设计文档: `../../docs/`
- Nginx 配置: `./nginx/conf.d/agent.conf`(带详细注释)
