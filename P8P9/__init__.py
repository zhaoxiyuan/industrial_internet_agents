# P8P9/ — 独立 P8 处置 + P9 审核项目
#
# 设计依据：docs/风险处置卡片交互设计.md
# 架构：1 状态机 + 7 业务动作 + 6 卡片模板 + 3 service + 1 agent 接口
#
# 不 import langchain / LLM SDK；纯 Python 文件 I/O + 飞书 CardKit。