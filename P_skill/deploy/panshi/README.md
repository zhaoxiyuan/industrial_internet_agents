# 磐石云干净部署

服务器：103.236.71.83，部署目录：`/opt/industrial-agents`。
只打包源码白名单，排除本地/Mac 数据库、历史会话、任务、日志、播放器记录、虚拟环境和缓存。
nginx 认证哈希可单独迁移；不迁移平台数据库或 Agent 定义。

入口：`http://103.236.71.83:18080/skill/`，播放器：`http://103.236.71.83:18080/player/`。
沿用原 nginx 管理账号。平台和播放器仅监听 loopback，经宿主 nginx 公开。
现有 nginx 路由保留；旧业务 18081 和 SSH WebSocket 18082 的后端不属于此次两个项目的部署范围。
不安装 ngrok，不修改 Mac 数据或停止 Mac 服务。

生成代码包：`python P_skill/deploy/panshi/package_clean.py`。
输出位于被 Git 忽略的 `P_skill/.runtime/panshi/`，包含源文件 SHA256 清单。
Python 基础镜像通过 `docker.1ms.run/library/python:3.12-slim` 下载并标记为 `python:3.12-slim`。
Python 依赖仍使用项目锁定版本；构建时通过阿里云 PyPI 镜像获取。

服务器操作：

```sh
cd /opt/industrial-agents
DOCKER_BUILDKIT=0 docker compose build
docker compose up -d --no-build
docker compose ps
docker compose logs --tail 100 platform player
nginx -t && systemctl reload nginx
```

`platform/data`、`state/shared`、`state/player` 为服务器数据目录；`state/platform` 是指向 `platform/data` 的兼容符号链接。
平台容器的数据与共享存储使用宿主机相同绝对路径，满足 Docker 挂载来源校验；宿主机 `/usr/bin/docker` 只读挂载到平台容器。
平台最初为空；Skill ZIP 位于服务器 `P_skill/dist/`。
通用执行容器 `p-skill-python-executor`：只读 `/skills`、读写 `/business`。
未自动建立业务计划或运行模型；P10 原项目执行环境未启用。

2026-10-16 部署验证通过：三个容器运行；`pip check` 无冲突；平台 Docker SDK 可连接本机引擎；nginx 语法检查通过。
公网未认证访问返回 401。临时验证账号测试 `/skill/`、`/web/app.js`、`/api/overview`、`/player/` 和播放器数据状态接口均为 200，验证账号已移除。
Agent、Skill、会话、任务、计划及飞书连接数量均为 0，确认没有迁移历史运行数据。

## P_skill 加载（同日后续操作）

已导入 industrial-main（9 工具）、p6-monitor（4 工具）、p7-risk-assessment（6 工具）、p8p9-disposition（8 工具）。
参照 Mac 的配置白名单创建四个 Agent，配置模型、5 个能力绑定、4 项协作授权、采集源规则及 1 个飞书连接。
仅迁移环境与定义配置，未复制 Mac 数据库、作业票、会话、任务或播放器记录；保留用户在磐石云新建的 qwen Agent 及其会话/任务。
没有迁移或启动心跳计划。P10 保持未启用。飞书官方后台回调地址须由管理员更新为 `http://103.236.71.83:18080/skill-webhooks/feishu/P8P9`（以连接名称为准）。
`verify_loaded.py` 已通过平台管理员 CLI 实际执行 query_pipeline，返回 work_permit_required，业务作业数量为 0；未调用模型或发送飞书消息。
`load_skills.py` 仅在没有活动任务时写入配置；私有配置文件 `config-only.json` 权限 600，不进入源码包或 Git。
