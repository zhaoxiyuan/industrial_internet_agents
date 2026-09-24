# P8 附件上传 — 设计与实现（方案 B · 独立上传服务）

> 2026-09-20 新增；解决"飞书 Card 2.0 没有原生文件上传组件"的限制。

## 1. 问题

飞书 Card 2.0 的 input 组件只支持 `multiline_text`（文字）和 `image`（图片），**没有原生 file upload**。
P8 处置流程在 4 个态需要附带非图片附件（PDF / Word / Excel / PowerPoint / MP4）：
- `acknowledged`（接取时附说明材料）
- `materials_in_audit`（提交整改材料时附带证据）
- `waiting_human_review`（人工审核时附补充材料）
- `ready_to_close`（终审时附最终材料）

之前只有 `inline_textarea`，只能输入纯文字，无法上传文件。

## 2. 方案对比

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| A. 改用飞书图片 input | 卡片内嵌 | 仅支持图片；PDF/Word/Excel 不行 | ✗ |
| **B. 独立上传服务 + token** | 全格式支持；可复用 attachment link 机制；权限可控 | 需要新页面 + 跳转 | ✅ **采纳** |
| C. 改造 Gateway webhook | 复用现有通道 | 复杂；webhook 不能上传文件 | ✗ |

## 3. 方案 B 架构

```
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ 飞书 Card 2.0   │  │ Flask 路由       │  │ UploadService    │
│                 │  │                 │  │ + ClosureLink   │
│ "📎 上传附件"   │  │ GET /upload/new │  │                 │
│   link_button   │─▶│   创建 token    │─▶│ state.upload_   │
│   URL:          │  │   302 → /upload │  │   links[tk_xxx] │
│   /api/closure/ │  │                 │  │   uploads: []   │
│   upload/new?   │  │ GET /upload     │  └─────────────────┘
│   job_id&target │  │   渲染 HTML 表单 │
│   &open_id      │  │                 │  ┌─────────────────┐
│                 │  │ POST /upload    │  │ 磁盘 .bin 文件   │
│                 │  │   multipart     │─▶│ {base_dir}/      │
│                 │  │                 │  │  {job_id}/       │
│  业务表单        │  │                 │  │  uploads/        │
│  (带 upload_token│  │                 │  │  {upload_id}.bin │
│   在 value 里)  │  │                 │  └─────────────────┘
│        │        │  │                 │  ┌─────────────────┐
│        ▼        │  │ record_review   │  │ business_actions │
│  callback_router│─▶│   消费 token    │─▶│ _consume_uploads │
│                 │  │   拿 upload_ids │  │ 写 state.materials│
│                 │  │                 │  │ .submissions[].  │
│                 │  │                 │  │ uploads          │
└─────────────────┘  └─────────────────┘  └─────────────────┘
```

## 4. 数据模型

### 4.1 Token 格式

- **upload_token**：`tk_` + 8 字符 base62（共 10 字符）
- **upload_id**：`up_` + 8 字符 base62（共 10 字符）

### 4.2 State 字段

新增到 `state`：
```json
{
  "upload_links": {
    "tk_xxxxxxxx": {
      "token": "tk_xxxxxxxx",
      "kind": "upload",
      "job_id": "20260917000000003",
      "target": "materials_submission",  // 或 "risk_change"
      "created_by": "ou_xxx",
      "created_at": "2026-09-20T...",
      "expiry": "2026-09-20T...+30min",
      "max_consume_count": 1,
      "consumed_count": 0,
      "consumed_history": [],
      "uploads": [
        {
          "upload_id": "up_xxxxxxxx",
          "filename": "evidence.pdf",
          "mime_type": "application/pdf",
          "size_bytes": 12345,
          "uploaded_by": "ou_xxx",
          "uploaded_by_name": "张三",
          "uploaded_at": "2026-09-20T..."
        }
      ]
    }
  }
}
```

业务字段（materials.submissions + risk_changes）：
```json
{
  "materials": {
    "submissions": [
      {
        "review_text": "...",
        "submissions": [...],
        "event_ids": [...],
        "submitted_by": "ou_xxx",
        "submitted_at": "...",
        "uploads": [<upload metadata>]    // ← 新增
      }
    ]
  },
  "risk_changes": [
    {
      ...
      "uploads": [<upload metadata>]    // ← 新增
    }
  ]
}
```

## 5. 关键约束

| 约束 | 值 | 来源 |
|---|---|---|
| 单文件 ≤ 20MB | `UPLOAD_MAX_FILE_SIZE` | 飞书 bot 文件上传限制 |
| 单 job 累计 ≤ 100MB | `UPLOAD_MAX_JOB_SIZE` | 兜底，避免 job 状态文件过大 |
| 允许扩展名 | jpg / jpeg / png / pdf / doc(x) / xls(x) / ppt(x) / mp4 | `UPLOAD_ALLOWED_EXTENSIONS` |
| 允许 MIME | 与扩展名匹配的 11 种 | `UPLOAD_ALLOWED_MIME_TYPES` |
| Token TTL | 30 分钟 | `UPLOAD_TOKEN_TTL_MINUTES` |
| 消费次数 | 1 次（一次性） | `max_consume_count=1` 默认 |

降级要求 ≥ 1 evidence_ids 的硬约束**保持不变**（附件是补充，不是替代）。

## 6. 业务流程时序

### 6.1 接取后附材料（acknowledged 态）

```
1. 用户在 acknowledged 卡看到 "📎 上传附件" 按钮（link_button）
   ↓
2. 点击 → 浏览器 GET /api/closure/upload/new?job_id=X&target=materials_submission&open_id=ou_y
   ↓
3. 服务端校验：accepted_by.open_id == ou_y（否则 403 actor_mismatch）
4. 服务端创建 tk_xxx，302 → /api/closure/upload?token=tk_xxx
   ↓
5. 浏览器渲染上传页（多文件选择 + 上传按钮 + 完成按钮）
   ↓
6. 用户选文件 → 点击上传 → 浏览器 POST /api/closure/upload?token=tk_xxx (multipart)
   ↓
7. 服务端校验扩展名 / 大小 / 配额 → 写 .bin → append_upload_to_token
   ↓
8. 上传完成后用户点 "完成并返回"
   ↓
9. 浏览器 GET /api/closure/upload/done?ids=up_a,up_b → 显示 ID 列表
   ↓
10. 用户回到飞书卡片，在提交表单的 upload_ids 字段里填 tk_xxx
   ↓
11. 飞书 callback → business_actions.submit_rectification_materials(upload_token=tk_xxx)
   ↓
12. _consume_uploads → consume_upload_token → 拿 uploads → 写 state.materials.submissions[-1].uploads
```

### 6.2 审核通过 + 附件（materials_in_audit / ready_to_close 态）

流程同 6.1，但 target=`materials_submission` 或 `risk_change`：
- materials_in_audit → materials_submission（提交补充材料）
- waiting_human_review / ready_to_close → risk_change（升级/降级附证据）

## 7. 路由清单

| 方法 | 路径 | 用途 | 鉴权 |
|---|---|---|---|
| GET | `/api/closure/upload/new?job_id=X&target=Y&open_id=Z` | 创建 token，302 → /upload?token=tk_xxx | accepted_by 校验（materials_submission） |
| GET | `/api/closure/upload?token=tk_xxx` | 上传页 HTML | token 合法性 |
| POST | `/api/closure/upload?token=tk_xxx` (multipart) | 单文件上传，返回 upload_id | token 合法性 + 大小 + 扩展名 + 配额 |
| GET | `/api/closure/upload/done?ids=up_a,up_b` | 完成页 HTML（提示回飞书） | 无（公开） |

## 8. 文件布局

```
data/jobs/{job_id}/
├── closure_state.json         # 业务状态
└── uploads/
    ├── up_aaa11111.bin        # 附件二进制
    ├── up_bbb22222.bin
    └── ...
```

- 文件名：`{upload_id}.bin`（用 upload_id 而不是原文件名，避免路径注入）
- 原子写：`tempfile + os.replace + fsync`（与 ClosureService 同模式）
- 元数据：**不在文件名里**；metadata 全部在 state.closure_state.json

## 9. 模块清单

| 文件 | 角色 |
|---|---|
| `a/P8P9/models.py` | 9 个 upload 常量（UPLOAD_MAX_FILE_SIZE / UPLOAD_MAX_JOB_SIZE / UPLOAD_ALLOWED_EXTENSIONS / UPLOAD_ALLOWED_MIME_TYPES / UPLOAD_ID_PREFIX / UPLOAD_ID_LEN / UPLOAD_TOKEN_PREFIX / UPLOAD_TOKEN_TTL_MINUTES / ATTACHMENT_DOWNLOAD_TTL_MINUTES） |
| `a/P8P9/upload_service.py` | UploadService（路径 / ID / 校验 / atomic_write / read / delete / build_metadata） |
| `a/P8P9/links.py` | ClosureLinkService 新增 create_upload_token / consume_upload_token / append_upload_to_token + _find_upload_token |
| `a/P8P9/upload_routes.py` | Flask Blueprint：3 路由（/upload/new /upload /upload/done） |
| `a/P8P9/business_actions.py` | 4 个业务动作加 `upload_token` 可选参数 + `_consume_uploads` 辅助 |
| `a/P8P9/cards.py` | 4 模板加"📎 上传" link_button；closed 模板加附件列表 |
| `a/P8P9/templates/upload.html` | 上传页 HTML（多文件选择 + JS 上传） |
| `a/P8P9/templates/upload_done.html` | 完成页 HTML（ID 列表 + 复制按钮） |
| `a/P8P9/web_server.py` | 注册 upload_bp |
| `a/P8P9/tests/test_upload.py` | 44 个单元测试 |

## 10. 已知限制 & 未来工作

| 限制 | 说明 |
|---|---|
| 仅磁盘存储 | 当前实现是 `data/jobs/{job_id}/uploads/*.bin`。生产环境建议接 OSS，对接 ClosureLinkService 现有 attachment link 机制 |
| Token 一次性 | max_consume_count=1 默认。session 中允许任意次 `append_upload_to_token`（每次 +1 file），但只能 commit 一次 |
| 无断点续传 | 单文件超 20MB 拒收；大文件应改用 OSS multipart |
| actor 校验 | materials_submission 严格校验 accepted_by；risk_change 不校验（review decider 身份由 record_closure_review 业务上下文决定） |
| LangChain tool | 不暴露 @tool（不是 LLM tool） |

## 11. 验证

### 11.1 单元测试

```bash
PYTHONPATH=a python -m unittest P8P9.tests.test_upload -v
# Ran 44 tests in 1.174s
# OK
```

### 11.2 集成测试

```bash
PYTHONPATH=a python -c "
from P8P9.web_server import app
with app.test_client() as c:
    resp = c.get('/api/closure/upload/new?job_id=X&target=materials_submission&open_id=ou_x')
    print(resp.status_code, resp.headers.get('Location'))
"
```

### 11.3 手工 E2E（建议）

1. 启动 web_server（`python -m P8P9.web_server --port 8089`）
2. 用 P8 流程创建一个 acknowledged job
3. 浏览器访问 `/api/closure/upload/new?job_id=<job_id>&target=materials_submission&open_id=<accepted_by>`
4. 上传 PDF → 拿到 upload_id
5. 在飞书卡片提交整改材料（带 upload_token）→ materials.submissions[-1].uploads 应包含 upload_id