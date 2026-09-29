"""Diagnose config.feishu.local.json — does it contain literal ${VAR} placeholders
that gateway won't resolve because the corresponding env vars are missing?
Prints lengths and placeholder-flag only — never secret bytes."""
import json
from pathlib import Path

p = Path("/Users/edge_security/IndustrialAgents/a/gateway/config/config.feishu.local.json")
cfg = json.loads(p.read_text())
a = cfg["channels"]["feishu"]["accounts"]["P8P9"]
print("=== P8P9 account fields ===")
for k in ["domain", "appId", "appSecret", "verificationToken"]:
    v = a[k]
    is_placeholder = isinstance(v, str) and v.startswith("${") and v.endswith("}")
    print(f"  {k}: len={len(v) if isinstance(v, str) else '?'}  placeholder={is_placeholder}")

# count literal ${ in raw file (in case anywhere else)
raw = p.read_text()
print(f"\n=== raw file: ${'{'}-count = {raw.count('${')} ===")

# also dump .env keys that look like FEISHU_P8P9_*
env = Path("/Users/edge_security/IndustrialAgents/a/.env")
if env.exists():
    matched = []
    for line in env.read_text().splitlines():
        s = line.strip()
        if s.startswith("FEISHU_P8P9_") and "=" in s:
            matched.append(s.split("=", 1)[0])
    print(f"\n=== .env FEISHU_P8P9_* keys: {len(matched)} ===")
    for k in matched:
        print(f"  {k}")
