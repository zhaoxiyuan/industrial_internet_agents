"""从 gateway/.env 删除与根 .env 重复的 CG_API_KEY / CHANNEL_GATEWAY_API_KEY,
防止 docker compose env_file 列表后一个 .env 覆盖前一个,导致 chat_reply 鉴权失败。

gateway/.env 应只放 feishu 账号专属 keys(FEISHU_P8P9_*),共享 keys 走根 .env。"""
from pathlib import Path

p = Path("/Users/edge_security/IndustrialAgents/a/gateway/.env")
lines = p.read_text().splitlines()
removed = []
kept = []
for line in lines:
    s = line.strip()
    if s.startswith("CG_API_KEY=") or s.startswith("CHANNEL_GATEWAY_API_KEY="):
        removed.append(s.split("=", 1)[0])
        continue
    kept.append(line)
p.write_text("\n".join(kept) + "\n")
print(f"removed: {removed}")
print(f"remaining keys:")
for line in kept:
    s = line.strip()
    if s and not s.startswith("#"):
        print(f"  {s.split('=', 1)[0]}")
