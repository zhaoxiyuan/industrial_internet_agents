# Mac 公网入口

此目录部署到 `/Users/edge_security/ssh_remote/`，独立于业务项目 `a/` 和 Skill 包。Mac 上的 ngrok 仍指向 `http://localhost:18080`。

```text
ngrok → 宿主机 Nginx 127.0.0.1:18080
  /sshws/ → ssh-remote-tunnel 127.0.0.1:18082 → Mac SSH :22
  /skill/ → 宿主机 Skill 平台 127.0.0.1:8765
  其他路径 → a_mac_skill Docker Nginx 127.0.0.1:18081 → 业务容器
```

`ssh-remote-tunnel` 由本目录的 `compose.yaml` 运行；宿主机 Nginx 由 `com.industrial-agents.edge-nginx` LaunchAgent 运行。平台由 `~/IndustrialAgents/skill/com.industrial-agents.skill-platform` LaunchAgent 运行，MCP 通过仅在宿主机发布的 `127.0.0.1:8090` 访问。Docker Nginx 只负责业务路由。

`htpasswd.ssh` 和 `htpasswd.admin` 只保存在 Mac，不能提交。SSH 密码与原 Windows `mac-headers` 文件保持一致；Windows 客户端仍使用原 ngrok 地址、`/sshws/`、本地 `10022` 端口与 `edge_security` 的私钥。宿主机 Nginx 的 SSH 密码文件使用 APR1 哈希，密码本身未改变。

新入口验证：`curl -i http://127.0.0.1:18080/sshws/`、`/skill/` 与 `/admin/config/` 在未认证时都应返回 401；再从外网按原步骤实际登录 SSH。旧 `com.industrial-agents.ngrok-ssh` LaunchAgent 已禁用，旧 `a-nginx`、`a-ssh-tunnel` 容器已停止并取消自动重启。若需回退，先停宿主机 Nginx、恢复旧容器和旧 LaunchAgent，再检查 `18080` 与公网 SSH。
