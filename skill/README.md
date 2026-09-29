# Skill 管理平台本地原型

从仓库根目录执行 `python skill/app.py`；如果当前已在 `skill/` 目录，执行 `python app.py`。然后打开 <http://127.0.0.1:8765>，停止时按 `Ctrl+C`。也可在 `skill/` 目录执行 `docker compose up --build`。原型只监听本机地址；若需要跨机器访问，先接入平台认证和 HTTPS。

目前实现：SQLite 持久化、完整 Skill 目录/ZIP 导入与分层校验、Agent 创建、一个 Agent 绑定多个 Skill、绑定启停、Agent 模型 API 配置、通过 LangChain `create_agent` 调用模型、会话创建/删除和消息存储、计划配置及手动创建独立 run/thread、Docker 可用性检查、基础前端。每个计划运行都产生独立 threadId。

实际对话前安装 `python -m pip install -r skill/requirements-agent.txt`（若已进入 `skill` 目录则使用 `requirements-agent.txt`）。在“智能体 API”中直接填写模型名、兼容接口地址和 API Key，保存后点击“测试连接”，再到对话栏新建会话。API Key 使用密码输入框，保存后只显示“已配置”；留空保留旧密钥，填写新值则替换。当前本地原型将密钥存入本机 SQLite，不通过查询接口回传密钥；数据库文件尚未加密。旧环境变量配置仍兼容，无需新建环境变量。会话历史保存在 SQLite，服务重启后仍可继续；每次调用从数据库重建消息列表，目前未安装 LangGraph checkpointer。修改代码后需停止并重新启动服务。

目前**尚未实现**：P8 的 8 个远程工具入口、LangGraph checkpointer 和真正上下文压缩、自动调度执行、外围 Docker 依赖安装、K8s 适配、飞书入口、用户认证与多租户。已接入 MCP 自动发现与 LangChain 工具注册；实际工具调用仍取决于对应现场服务、网络和权限。手动运行仍只创建待执行记录。不可当作生产运行平台。压缩接口返回 501。

Skill 仓库现在接收完整目录或 ZIP，不再接收旧版简化 JSON。安装依赖后，进入“Skill 仓库”，目录填写 `a_mac_skill`，点击“校验包”或“校验并导入”。ZIP 可将整个 `a_mac_skill` 目录打包上传。导入保存内容快照并默认停用，同版本不同内容拒绝导入。

```powershell
python -m pip install -r requirements-agent.txt
# 从仓库根目录执行离线校验：
python skill/package_validation.py a_mac_skill
# 编辑源包后重建内容锁；已发布版本应先提升版本号：
python skill/package_validation.py a_mac_skill --lock
```

校验覆盖平台 JSON Schema、SKILL.md 元信息/正文、引用文件边界、配置 Schema/作用域、工具契约与源码符号、服务依赖循环、版本/位置/健康/存储声明、Compose 构建文件、文件内容锁、常见凭据文件和 ZIP 路径/大小限制。`GET /api/skills/schema` 返回同一份清单标准。测试命令（仓库根目录）：`python -m unittest skill.test_package_validation skill.test_app`。

报告中的“通过”表示已验证项目；“失败”阻止导入；“待完成”表示现场配置、镜像摘要、工具适配器或现场运行检查尚未满足。MCP 包可在服务在线且至少一个声明工具匹配时部分启用；托管 Docker 部署仍须完成镜像及现场检查。当前尚无现场预检执行器，服务网络/健康/资源不会被静态检查冒充为已通过。

Skill 列表提供“删除”。已启用的包须先停用，已绑定 Agent 的包须先在“智能体管理”解除绑定。删除清除平台记录；项目内源目录和已保存的包快照继续保留，避免影响其他同摘要版本或现场数据。

`a_mac_skill` 0.2.1 增加固定开发令牌。十个已有 HTTP 操作自动发布为 MCP 工具；P8 八个工具仍保留声明，但未发布远程调用入口。本机运行时从 `a_mac_skill` 目录执行 `python -m scripts.mcp_server`，无需生成令牌。平台页面的现场配置 JSON 填 `{"mcpUrl":"http://127.0.0.1:8090/mcp"}`，在“MCP 访问令牌”密码框填入包内 `scripts/mcp_server.py` 的 `LOCAL_DEV_MCP_TOKEN` 值，再点击启用；平台自动发现工具，无需逐个配置。令牌保存后不回显，留空会保留已有值；本地原型将其存入 SQLite，数据库尚未加密。旧的 `mcpTokenEnv` 配置仍兼容。正式跨节点部署可设置 `AMAC_MCP_TOKEN` 覆盖默认开发令牌；若平台和 MCP 分别运行在 Docker 内，`127.0.0.1` 指向各自容器，需填写实际可达的服务地址。实际业务调用还需配置 `P6_BASE_URL`、`P8P9_BASE_URL`、`GATEWAY_BASE_URL` 及业务鉴权变量，具体见包内 `scripts/client.py`。原始 `a_mac` 不受影响。
