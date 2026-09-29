# a/deploy/COMMANDS.md — Docker 部署命令速查

> **使用前提**:所有命令默认在 `a/deploy/` 目录下执行(因为需要 `docker-compose.yml` 在当前目录)。
>
> ```bash
> cd /opt/industrial_internet_agents/a/deploy
> ```

---

## 0. 命令命名约定

| 形式 | 含义 |
|---|---|
| `<svc>` | 服务名占位符,可选值:`gateway` / `p6` / `p8p9` / `app_config` / `feishu_cfg` / `webui` / `nginx` |
| `-f` | `--follow` 持续跟踪日志 |
| `--tail N` | 只看最后 N 行 |
| `[shell]` | 进容器后的 shell 命令 |

---

## 1. 全栈命令(整组服务)

### 1.1 构建 + 启动

```bash
# 第一次部署:构建所有镜像 + 后台启动
docker compose build
docker compose up -d

# 一次到位(等价于上面两条)
docker compose up -d --build

# 看启动结果(列出 7 个容器状态)
docker compose ps
```

### 1.2 停止 / 重启

```bash
# 停掉全部容器(数据保留)
docker compose down

# 重启全部
docker compose restart

# 停掉并清卷(⚠ 会删 data/runtime 等持久化数据)
docker compose down -v
```

### 1.3 看日志

```bash
# 所有容器,持续跟踪
docker compose logs -f

# 所有容器,只看最后 100 行
docker compose logs --tail 100

# 只看某个服务
docker compose logs -f p6

# 多服务并发
docker compose logs -f p6 p8p9 gateway

# 带时间戳 + grep
docker compose logs -f --timestamps p8p9 | grep -i "error\|异常"
```

### 1.4 配置验证(不真起容器)

```bash
# 检查 docker-compose.yml 语法 + 合并 .env 后的最终配置
docker compose config

# 想看完整解析后的 YAML(看 nginx 卷路径等是否正确)
docker compose config | less
```

---

## 2. 单服务命令

> 7 个服务:`gateway`、`p6`、`p8p9`、`app_config`、`feishu_cfg`、`webui`、`nginx`
> 下面用 `<svc>` 代表其中任意一个。

### 2.1 启停 / 重启

```bash
# 启动(如果停了)
docker compose up -d <svc>

# 停止(保留容器实例,可 docker compose start 恢复)
docker compose stop <svc>

# 重启(等同 stop + start,配置改了不会重新加载代码)
docker compose restart <svc>

# 强制重建 + 重启(Dockerfile / requirements 改了必须这条)
docker compose up -d --build <svc>
```

### 2.2 看该服务日志

```bash
# 持续跟踪
docker compose logs -f <svc>

# 最近 200 行 + grep
docker compose logs --tail 200 <svc> | grep -i "warning"

# 看启动后 30 秒的输出(排查启动失败)
docker compose logs --tail 200 --since 30s <svc>
```

### 2.3 进容器调试

```bash
# 进交互 shell(默认 bash,alpine 用 sh)
docker compose exec <svc> sh

# nginx 容器是 alpine,没 bash
docker compose exec nginx sh

# 执行单条命令后退出
docker compose exec <svc> ls /srv/app/data/jobs/
docker compose exec <svc> cat /etc/nginx/conf.d/agent.conf
docker compose exec <svc> ps aux | grep python
```

### 2.4 重建单个镜像

```bash
# 改了 Dockerfile 或 requirements 后必须这条
docker compose build <svc>

# 不走缓存重建(强制重下层,排查诡异问题时用)
docker compose build --no-cache <svc>

# 重建 + 启起来
docker compose up -d --build <svc>
```

### 2.5 删除容器 + 镜像

```bash
# 删容器(下次 docker compose up 会自动重建)
docker compose rm <svc>

# 删镜像(要先停)
docker compose down
docker image rm a-<svc>:latest
```

---

## 3. 调试 / 排错命令

### 3.1 容器元信息

```bash
# 看容器详细配置(网络、卷、环境变量)
docker inspect a-<svc>

# 只看 mount(挂载点)
docker inspect a-<svc> | grep -A 10 Mounts

# 只看网络
docker inspect a-<svc> | grep -A 5 Networks

# 看实时资源占用(CPU/内存)
docker stats
```

### 3.2 容器内进程 / 网络

```bash
# 容器内的进程列表
docker compose top <svc>

# 容器内的网络监听端口(看 Flask/FastAPI 是否真的 bind 了 0.0.0.0)
docker compose exec <svc> sh -c "ss -tlnp 2>/dev/null || netstat -tlnp"

# 容器内 curl 测试自身(看 /api/health 是否响应)
docker compose exec <svc> curl -i http://127.0.0.1:<port>/api/health
```

### 3.3 网络连通性

```bash
# 从 nginx 容器测后端服务是否可达
docker compose exec nginx wget -q -O- http://p6:5002/api/health
docker compose exec nginx wget -q -O- http://gateway:8787/healthz

# DNS 解析检查
docker compose exec p6 nslookup p8p9  # 或 dig / getent hosts p8p9

# 看 internal 网络的容器成员
docker network inspect industrial-agents-a_internal
```

### 3.4 镜像 / 卷占用

```bash
# 镜像清单(看哪些占空间)
docker images | grep "a-"

# 容器磁盘占用(看谁写日志写爆了)
docker system df

# 清理未用镜像 / 容器 / 网络(谨慎)
docker system prune -a --volumes    # ⚠ -a 会删所有未用镜像
```

---

## 4. 数据持久化 / 备份

### 4.1 备份

```bash
# 全量备份 a/data 目录(停服保证一致性更好)
cd /opt/industrial_internet_agents
tar czf a-data-$(date +%Y%m%d).tar.gz a/data/

# 只备份 jobs(用户业务数据)
tar czf jobs-$(date +%Y%m%d).tar.gz a/data/jobs/

# agent_config(LLM/VL 配置 + 飞书账户)
tar czf agent-config-$(date +%Y%m%d).tar.gz a/agent_config/ a/.env
```

### 4.2 恢复

```bash
# 解压到原路径
cd /opt/industrial_internet_agents
tar xzf a-data-20260920.tar.gz

# 重启容器让新数据生效
cd a/deploy
docker compose restart
```

### 4.3 查看 / 清理运行时

```bash
# 看运行时目录(日志、pid、card 索引)
ls -la /opt/industrial_internet_agents/a/data/runtime/

# 清某个服务的运行时 log
> /opt/industrial_internet_agents/a/data/runtime/p8p9.log

# 清 A5/logs(每个 job 单独目录)
ls /opt/industrial_internet_agents/a/A5/logs/
rm -rf /opt/industrial_internet_agents/a/A5/logs/<job_id>/
```

---

## 5. Nginx 操作

```bash
# 测试配置(改 nginx 配置后必跑)
docker compose exec nginx nginx -t

# 热加载(改 conf.d/agent.conf 后用这条,不中断连接)
docker compose exec nginx nginx -s reload

# 看 nginx 错误日志
docker compose logs nginx | grep error

# 改 htpasswd 后不用重启 nginx(文件每次请求都重读)
cd /opt/industrial_internet_agents/a/deploy
htpasswd -B nginx/htpasswd <username>
```

---

## 6. chat_reply 宿主机管理(Docker 之外的进程)

> chat_reply 在 Docker 之外的宿主机跑,通过 `aaaseverstart.py` 管理。

```bash
# 启动 chat_reply
cd /opt/industrial_internet_agents/a
python aaaseverstart.py start chat_reply

# 查看状态
python aaaseverstart.py status chat_reply

# 停止
python aaaseverstart.py stop chat_reply

# 重启
python aaaseverstart.py restart chat_reply

# 看 chat_reply 日志
tail -f data/runtime/chat_reply.log
```

systemd 接管(开机自启):

```bash
sudo systemctl status chat-reply        # 状态
sudo systemctl restart chat-reply       # 重启
sudo journalctl -u chat-reply -f        # 跟日志
sudo systemctl disable chat-reply       # 取消开机自启
```

---

## 7. 证书 / BasicAuth

### 7.1 Let's Encrypt 证书

```bash
# 验证续期流程(不实际续)
sudo certbot renew --dry-run

# 真续期(成功后 certbot 的 deploy-hook 会自动 reload nginx)
sudo certbot renew

# 手动 reload nginx(如果 deploy-hook 没触发)
docker compose exec nginx nginx -s reload

# 看证书过期时间
sudo certbot certificates

# 续期 cron 检查
cat /etc/cron.d/certbot-renew
```

### 7.2 BasicAuth 账号

```bash
# 加账号(交互式输密码)
cd /opt/industrial_internet_agents/a/deploy
htpasswd -B nginx/htpasswd <username>

# 删账号
htpasswd -D nginx/htpasswd <username>

# 查看所有账号(密码显示为 hash)
cat nginx/htpasswd

# 验证 htpasswd 文件可用
docker compose exec nginx sh -c "cat /etc/nginx/htpasswd"
```

---

## 8. 端到端排错清单

```bash
# Step 1:容器都活了吗?
docker compose ps
# 期望 7 个 RUNNING / healthy
# 任何 exited / unhealthy → 看该容器日志

# Step 2:证书有效吗?
curl -vI https://jlkwihudbifhaej.dpdns.org/ 2>&1 | grep -i "subject:\|expire"

# Step 3:nginx 能反代吗?
docker compose exec nginx wget -q -O- http://p6:5002/api/health
# 期望 {"ok":true}
# 任何 timeout / refused → 看 internal 网络 + 容器是否真在监听

# Step 4:从公网访问试试
curl -i https://jlkwihudbifhaej.dpdns.org/webhooks/feishu/P8
# 期望 401 / 405 / 200(gateway 响应)
curl -i https://jlkwihudbifhaej.dpdns.org/admin/p6/
# 期望 401 + WWW-Authenticate: Basic

# Step 5:BasicAuth 通过后能进?
curl -i -u admin:你的密码 https://jlkwihudbifhaej.dpdns.org/admin/p6/api/health
# 期望 200 + {"ok":true}

# Step 6:数据在吗?
ls /opt/industrial_internet_agents/a/data/jobs/
docker compose exec p8p9 ls /srv/app/data/jobs/
# 两边应该看到一样的 job 目录(挂载生效)

# Step 7:webui(host 网络)转发通吗?
sudo ss -tlnp | grep 8080       # 宿主机 8080 应被 a-webui 占用
docker logs a-webui | tail -50  # 看转发日志
```

---

## 9. 常用服务端口速查

| 服务 | 容器内端口 | 宿主机端口 | 协议 | 健康检查 |
|---|---|---|---|---|
| nginx | 80, 443 | **80, 443** | TCP | 外部 curl |
| gateway | 8787 | — (内网) | TCP | `GET /healthz` |
| p6 | 5002 | — (内网) | TCP + WebSocket | `GET /api/health` |
| p8p9 | 8089 | — (内网) | TCP | `GET /api/health` |
| app_config | 5000 | — (内网) | TCP | `GET /api/health` |
| feishu_cfg | 5003 | — (内网) | TCP | `GET /api/health` |
| webui | 8080 | **8080**(host 网络) | TCP | `GET /api/health` |
| chat_reply | (daemon) | (daemon) | — | — |

---

## 10. 紧急操作

```bash
# 全部重启(治百病的银弹,虽然糙)
docker compose restart

# 全部重建(配置大改 / 升级)
docker compose down
docker compose build
docker compose up -d

# 完全清空重来(⚠ 会丢所有未挂卷的容器内文件)
docker compose down -v --remove-orphans
docker compose up -d --build

# 服务器 reboot 后自启
# Docker 容器 restart: unless-stopped 已经覆盖
# chat_reply 需要 systemd(见 §6)
sudo systemctl status chat-reply
```

---

## 附录:命令快查表(可打印贴在服务器)

```
# ======= 日常 =======
dc ps                                 # 看状态
dc logs -f --tail 100                 # 看日志
dc restart <svc>                      # 重启单服务
dc up -d --build <svc>                # 改 Dockerfile 后重建

# ======= 排错 =======
dc config                             # 验配置
dc exec <svc> sh                      # 进容器
dc exec <svc> curl localhost:<port>/api/health
dc logs <svc> --tail 200 | grep -i error

# ======= 数据 =======
tar czf backup.tar.gz a/data/         # 备份
ls a/data/jobs/                       # 看作业

# ======= nginx =======
dc exec nginx nginx -t                # 测配置
dc exec nginx nginx -s reload         # 热加载
htpasswd -B nginx/htpasswd user       # 加 BasicAuth

# ======= chat_reply(宿主机) =======
cd /opt/industrial_internet_agents/a
python aaaseverstart.py {start|stop|status|restart} chat_reply

# (dc = docker compose,服务器上建议设别名:alias dc='docker compose')
```
