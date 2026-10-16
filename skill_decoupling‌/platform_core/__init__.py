"""独立 Skill 平台的核心包。

模块按职责拆分：skills 管内容，feishu 管连接与业务，mcp 管协议和进程，
agents 管授权与模型循环，operations 管后台执行，storage 管本机持久化。
导入本包不启动服务、不读取旧项目配置，也不主动连接网络。
"""
