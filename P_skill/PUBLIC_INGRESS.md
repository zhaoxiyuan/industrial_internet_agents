> 2026-10-15 当前上游已改为时间窗口单票流程：Main按时间调度，P6一次写入一份描述；本文的下游/界面机制继续适用。旧源领取及候选票补办流程不再适用；本轮真实验收以 LIVE_PLATFORM_ACCEPTANCE.md 为准。P10本轮不测试。


# Mac 公网入口与职责边界

更新：2026-10-13。公网域名为 `https://ladylike-overripe-resource.ngrok-free.dev`，由 ngrok 转发到 Mac 宿主 Nginx 的 `127.0.0.1:18080`。

平台负责 Agent、Skill、工具授权、账号和业务回执；Nginx 负责路径转发和现有管理页登录保护；Docker 提供播放器、Skill 执行容器和已有 P10 接入服务。不增加独立迁移服务或常驻配置服务。曾临时新增的飞书配置迁移脚本已移除；已登记的 P8P9 连接直接由平台飞书页面维护，凭据不进入本文件。

| 公网路径 | Mac 目标 | 说明 |
|---|---|---|
| `/skill/` | `127.0.0.1:18770/` | 当前 Skill 平台，沿用原管理员登录保护 |
| `/web/`、通用 `/api/` | `127.0.0.1:18770` | 平台使用的根路径静态资源、API 和流式回执，同样要求管理登录 |
| `/player/` | `127.0.0.1:18090/` | mock_player 前端和 API，沿用管理登录；前端支持后缀部署 |
| `/skill-webhooks/feishu/P8P9` | `127.0.0.1:18770/webhooks/feishu/P8P9` | 新平台飞书消息/卡片事件回调，无 Basic Auth，由平台校验 Token/签名 |
| `/risk-card`、`/risk-upload`、`/risk-upload/submit`、`/risk-material` | `127.0.0.1:18770` | 平台限时签名链接，仍由平台校验；普通管理接口不能通过这些路径访问 |
| `/sshws/` | `127.0.0.1:18082` | 保留原 SSH WebSocket 隧道及独立认证 |
| `/webhooks/feishu/`、`/feishu/card/callback` | `127.0.0.1:18081` | 保留原 Gateway 回调，未切换飞书后台前仍可使用 |
| `/admin/`、`/api/closure/`、`/api/feishu/` | `127.0.0.1:18081` | 保留原业务 Nginx 管理、闭环和配置入口 |

Nginx 源配置在 `P_skill/deploy/mac-public.conf`，Mac 实际加载位置为 `/Users/edge_security/ssh_remote/nginx.conf`。必须先改本地，再同步到 Mac，通过 `nginx -t` 后 reload；旧配置已在 Mac 备份。

## 飞书后台配置

需要接入新平台时，把对应应用的事件订阅回调 URL 设置为：

`https://ladylike-overripe-resource.ngrok-free.dev/skill-webhooks/feishu/P8P9`

如果该应用有独立卡片回调配置，新平台的卡片事件也使用该 URL。平台内部显示的 `/webhooks/feishu/P8P9` 是后端原始路径；公网由 Nginx 的 `/skill-webhooks/feishu/` 后缀转发。连接名称必须匹配平台中的 P8P9；新建其他连接时相应替换最后一段。

沿用原账号的 Verification Token 和 Encrypt Key（如配置）。本次只验证 challenge/错误 Token 的接入，不发送真实飞书消息。P8P9 真实连接保持接收模式，未设置 inbound Agent；测试连接和模拟卡片保持独立。原飞书后台配置未被本次操作自动修改；历史卡片和旧上传链接的业务状态没有迁移。

## 保留的部署

全部 9 个 `amac-skill-*` 容器保持运行，未删除容器、挂载或业务数据。另保留 `ssh-remote-tunnel` 和新工作流的 tools/player/p10 三个容器。停止旧服务仍需另行安排。

本机 `http://127.0.0.1:18770/` 继续通过 SSH 转发访问，后台转发已配置断线重连；公网 `/skill/` 不依赖该本机转发。

## 验证边界

Nginx 语法检查和 reload 通过；公网管理入口与旧管理入口未登录返回带认证提示的 HTTP 401。播放器后缀适配已构建进 Docker。公网新回调通过正确 Token challenge 和错误 Token 拒绝验证。管理员登录后的完整公网页面操作、飞书官方后台保存和真实消息接收仍需使用实际账号验证；本次没有凭空声称真实飞书联动已完成。


当前执行契约已更新为v5：完整窗口观测、可信人工决定关闭、超时撤销并回收领取、Main独立心跳仅对自身防重入。详见[当前作业与心跳契约](CURRENT_WORKFLOW.md)。文中带日期的验收记录保留当时事实，不作为现行工具约束。
