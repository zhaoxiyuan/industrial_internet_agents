# 部署状态

这是源代码 Skill 包，使用 external profile 对接现有 a_mac；包结构校验通过不等于现场依赖已就绪。

deploy/compose.skill.yaml 为本地开发模板，继承现有 mac Compose 并移除固定 container_name、使用 0.1.0 开发标签、移除 chat_reply 的固定 initial-sequence。平台生产托管前需构建、验证镜像并逐服务写入真实 digest；当前 verifiedDigest 为 null，校验明确阻止托管部署。

原始基础镜像 a-deploy-base 需要先按 deploy/Dockerfile.base 构建。启动前建立 .env、gateway/config/config.feishu.local.json、deploy/nginx/htpasswd 等现场文件；均不放入分发包。配置前端仍写现场 .env，authority 为 service-owned，尚未接入平台统一配置读回。持久目录 data、agent_config、A5/logs 由现场初始化；卸载默认保留。

K8s profile 未提供，不能选为可部署模式。资源声明是初始建议值，目标节点架构、资源、模型、网络、凭据和健康均需现场预检。前端和 chat_reply 可按场景选配；工具所需的服务必须就绪。不得直接部署未经检查的原始 Compose 到生产。

修改包文件后，在平台项目运行 python skill/package_validation.py a_mac_skill --lock 更新内容锁文件，再重新校验；同版本发布后需提升版本再导入。
