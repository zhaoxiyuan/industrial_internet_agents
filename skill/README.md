# Skill 管理平台本地原型

从仓库根目录执行 `python skill/app.py`；如果当前已在 `skill/` 目录，执行 `python app.py`。然后打开 <http://127.0.0.1:8765>，停止时按 `Ctrl+C`。也可在 `skill/` 目录执行 `docker compose up --build`。原型只监听本机地址；若需要跨机器访问，先接入平台认证和 HTTPS。

目前实现：SQLite 持久化、完整 Skill 目录/ZIP 导入与分层校验、Agent 创建、一个 Agent 绑定多个 Skill、绑定启停、Agent 模型 API 配置、通过 LangChain `create_agent` 调用模型、会话创建/删除和消息存储、计划配置及手动创建独立 run/thread、Docker 可用性检查、基础前端。每个计划运行都产生独立 threadId。

实际对话前安装 `python -m pip install -r skill/requirements-agent.txt`（若已进入 `skill` 目录则使用 `requirements-agent.txt`）。在“智能体 API”中直接填写模型名、兼容接口地址和 API Key，保存后点击“测试连接”，再到对话栏新建会话。API Key 使用密码输入框，保存后只显示“已配置”；留空保留旧密钥，填写新值则替换。当前本地原型将密钥存入本机 SQLite，不通过查询接口回传密钥；数据库文件尚未加密。旧环境变量配置仍兼容，无需新建环境变量。会话历史保存在 SQLite，服务重启后仍可继续；每次调用从数据库重建消息列表，目前未安装 LangGraph checkpointer。修改代码后需停止并重新启动服务。

目前**尚未实现**：部分 P8 工具远程入口、LangGraph checkpointer 和真正上下文压缩、自动调度执行、K8s 适配、飞书入口、用户认证与多租户。已接入声明式目录/Docker 服务控制、MCP 自动发现与 LangChain 工具注册；实际工具调用仍取决于对应现场服务、网络和权限。手动计划运行仍只创建待执行记录。不可当作生产运行平台。压缩接口返回 501。

Skill 仓库现在接收完整目录或 ZIP，不再接收旧版简化 JSON。安装依赖后，进入“Skill 仓库”，目录填写 `a_mac_skill`，点击“校验包”或“校验并导入”。ZIP 可将整个 `a_mac_skill` 目录打包上传。导入保存内容快照并默认停用，同版本不同内容拒绝导入。

```powershell
python -m pip install -r requirements-agent.txt
# 从仓库根目录执行离线校验：
python skill/package_validation.py a_mac_skill
# 编辑源包后重建内容锁；已发布版本应先提升版本号：
python skill/package_validation.py a_mac_skill --lock
```

校验覆盖平台 JSON Schema、SKILL.md 元信息/正文、引用文件边界、配置 Schema/作用域、工具契约与源码符号、服务依赖循环、版本/位置/健康/存储声明、Compose 构建文件、文件内容锁、常见凭据文件和 ZIP 路径/大小限制。`GET /api/skills/schema` 返回同一份清单标准。测试命令（仓库根目录）：`python -m unittest skill.test_package_validation skill.test_app`。

报告中的“通过”表示已验证项目；“失败”阻止导入；“待完成”表示现场配置、镜像摘要、工具适配器或现场运行检查尚未满足。启动开关调用包内控制文件检查服务，再进行 MCP 工具发现；托管 Docker 的生产资格仍须完成镜像摘要和现场验收。静态包校验不代表服务已经运行。

Skill 列表提供“删除”。已启用的包须先停用，已绑定 Agent 的包须先在“智能体管理”解除绑定。删除清除平台记录；项目内源目录和已保存的包快照继续保留，避免影响其他同摘要版本或现场数据。

`a_mac_skill` 0.3.2 在清单中声明 MCP URL、原有固定开发令牌、配置页面 URL，以及目录与 Docker 两种服务控制方式。平台前端仅提供运行方式和启动/停止开关，不展示 MCP 地址或令牌；平台从包声明读取固定令牌并注入服务。`AMAC_MCP_TOKEN` 环境变量可覆盖它。目录模式由 `deploy/service_control.py` 启动必要服务，Docker 模式由同一控制文件调用 Compose。两种模式从现场 `a_mac/.env`、`a_mac/gateway/.env` 和 Gateway 配置文件读取真实设置，可用 `AMAC_SITE_ROOT` 指定现场目录。启动后平台检查服务健康，并通过 MCP `tools/list` 验证工具。正式跨节点部署应改用 Secret 环境变量及平台可达的 TLS 地址，并提升包版本、重新生成内容锁。业务调用还需配置 `P6_BASE_URL`、`P8P9_BASE_URL`、`GATEWAY_BASE_URL` 及鉴权变量。生产部署仍须完成镜像 digest、现场连通与权限验收。
