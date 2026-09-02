# Claude Code 项目指导

本目录包含 Claude Code 的本地项目配置和指导规则。

## 规则文件

| 文件 | 说明 |
|------|------|
| `CLAUDE.md` | 本文件，Claude Code 工作规则 |
| `settings.local.json` | 本地权限设置 |

## 工作规则

### 1. 代码修改规则

**每次修改代码后，必须验证语法：**
```bash
python -m py_compile <修改的文件路径>
```

**涉及多文件修改时（如 import 变更），必须验证导入：**
```bash
python -c "from <模块> import <函数>; print('OK')"
```

### 2. Git 规则

**提交前必须确认：**
- 运行 `git status` 查看变更文件
- 运行 `git diff` 确认变更内容
- 变更描述准确反映修改目的和内容
- 不要提交敏感信息（如 API Key、凭证）

**提交代码流程（当用户说"提交代码"时）：**
1. `git add .` - 暂存所有变更
2. `git commit` - 根据变更内容生成总结作为 comment
3. `git push` - 推送到远程

**-destructive 操作需确认：**
- `git reset --hard`、`git clean -f`、`git push --force` 等操作前必须询问用户

### 3. 文档更新规则

**代码变更涉及架构或接口时，必须同步更新文档：**
- `docs/architecture.md` - 技术架构文档
- `docs/agents/*.md` - Agent 说明文档
- `docs/index.md` - 执行链路文档

**文档与代码不一致时，以代码为准更新文档。**

### 4. 工作流程规则

**使用 EnterPlanMode 的场景：**
- 新功能实现（影响多个文件）
- 架构重构
- 多阶段 Agent 协作逻辑变更
- 不确定最佳实现方案时

**直接执行的场景：**
- 简单的 bug fix（单文件、少许改动）
- 文档更新
- 代码重构但方案明确

### 5. 测试验证规则

**每次完成代码修改后，必须进行基本验证：**
1. 语法检查 `python -m py_compile`
2. 导入检查 `python -c "from <module> import ..."`
3. 相关联文件的联动检查

**涉及 Web/前端修改时：**
- 确认 server.py 语法正确
- 确认 app.js 语法正确（通过 IDE 诊断或手动检查）

### 6. Agent HITL 相关规则

**P1-P10 各阶段使用两层 HumanInTheLoop 机制：**

1. **Workflow 层**：阶段执行完后检查 `pending_confirmation`，暂停等待确认
2. **Agent 层**：使用 `HumanInTheLoopMiddleware` 在工具调用前中断

**修改 HITL 相关逻辑时需注意：**
- `execute_pX()` 函数负责阶段执行和检查点管理
- `confirm_and_continue()` 负责确认后恢复执行
- `is_agent_interrupted(thread_id)` 检查 Agent 是否处于中断状态
- 中断恢复通过 `agent.invoke(None, config)` 实现

### 7. 文件路径规则

**路径引用：**
- 项目根目录：`/Users/zhaohong/Documents/中石油数智工作学习/5. repository/industrial_internet_agents`
- 作业数据目录：`data/jobs/{job_id}/`
- 阶段结果文件：`data/jobs/{job_id}/p{n}_result.json`

**涉及路径操作时使用绝对路径，避免相对路径歧义。**