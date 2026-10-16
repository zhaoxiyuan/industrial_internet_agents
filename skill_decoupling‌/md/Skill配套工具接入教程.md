# Skill 配套 Python 工具接入

实现日期：2026-10-13。平台现在支持 Skill 携带工具声明与 Python 代码，经管理员配置和显式绑定后由 LangChain 调用。工具执行仍只支持本机已有 Linux Docker 容器。本文不表示当前机器 Docker 已可用；本轮环境没有连接到 Docker daemon，真实容器验收明确跳过。

## 开发事实和边界

查阅了 skills.py、skill_backend.py、langchain_tools.py、agents.py、external_cli.py、runtime.py、runtime_middleware.py、tool_registry.py 及现有框架、平台、协作测试。原先导入只保存内容快照，read_skill/read_file只读文件，LangChain仅包装已有绑定能力，这些背景事实属实。本次复用原工具提供者目录、bindings、Agents.execute/dispatch、StructuredTool、Docker CLI runner及middleware审计；没有添加独立HTTP服务、数据库服务、轮询守护进程或Docker服务。

未找到仓库 AGENTS.md。遵循现有 README 的 Docker-only 执行边界、显式授权、修订失效、参数契约、未知动作不盲目重放，以及 P_skill/P6/DEVELOPMENT.md 的共享目录、四态、租约与主Agent调度要求。保留原有未提交修改。本例不实现mock_player消费和完整P6/P7业务，也不改变旧业务Skill。

容器是执行安全边界。平台服务进程不import Skill模块。`-I -X utf8`子Python按声明加载入口，每次退出；Docker runner负责进程组、超时及输出上限。管理员须审阅代码和失败补偿入口，并选择仅挂载必要资源的容器。Python隔离模式不提供文件系统沙箱；同一容器里受审核代码仍可能访问该容器其他挂载及网络。不要给此容器平台数据库、密钥、Docker socket或未经授权的业务数据；模型参数校验不能约束恶意Python代码。需要不同信任域时，应使用各自已有的隔离容器。

## 1. 创建声明

完整例子：`examples/skills/shared-risk-demo/{SKILL.md,tools.json,scripts/queue.py}`。`tools.json`固定为JSON版本1，不扩展Skill YAML解析器，也不扫描并执行文件。

```json
{
  "version": 1,
  "tools": [{
    "name": "save",
    "description": "P6保存风险描述",
    "input_schema": {
      "type": "object",
      "properties": {"source_id": {"type": "string"}, "description": {"type": "string"}},
      "required": ["source_id", "description"],
      "additionalProperties": false
    },
    "output_schema": {
      "type": "object",
      "properties": {"record_id": {"type": "string"}, "status": {"type": "string"}},
      "required": ["record_id", "status"],
      "additionalProperties": false
    },
    "entry": "scripts/queue.py:save",
    "dependencies": ["sqlite3"],
    "storage": ["queue"],
    "timeout": 5
  }]
}
```

入口必须在scripts或references，形式为`相对文件.py:顶层函数`，签名为`def save(arguments, context)`，不支持装饰器、异步入口、可变参数和额外关键字参数。导入通过AST校验语法和入口存在，绝不执行模块。调用才检测依赖、核对整个导入清单的SHA256并执行。依赖是可导入模块名；依赖版本由管理员在容器环境固定，平台不自动pip install，也不接受模型指定安装命令。

`context`由平台注入：agent_id、固定job_id、run_id和逻辑storage映射。输入契约必须是`additionalProperties:false`的object，示例不提供SQL、宿主机路径或job_id参数。代码和业务数据目录完全分离，工具只返回成果ID及结构化结果。

同包领取工具可声明`"claim_failure":"fail"`，其结果必须包含`claim`对象；平台持久化此领取凭证。管理员必须同时授权领取和配套失败工具。失败工具接受`{claim,reason}`，应验证领取者及租约、幂等处理，并且不能回退completed。声明中的失败工具存储权限不得超过领取工具。失败工具没有新任务或重试权限。

## 2. 导入与准备共享目录

以下PowerShell在平台目录执行，平台保持本机管理API的原访问边界。先创建一个新的持久化目录，不复用或覆盖已有业务数据库。

```powershell
$base = 'http://127.0.0.1:8770/api'
$shared = Join-Path (Get-Location) 'shared_data_demo'
New-Item -ItemType Directory -Force -Path $shared | Out-Null
function Post-Platform($route, $value) {
  Invoke-RestMethod -Uri "$base/$route" -Method Post -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes(($value | ConvertTo-Json -Depth 30)))
}
$skill = Post-Platform 'skills' @{source='examples/skills/shared-risk-demo'}
Post-Platform 'skill-storage/p6_p7' @{directory=$shared; enabled=$true}
$skill.id
$skill.digest
$skill.path
```

ZIP导入沿用已有接口`POST /api/skills {archive:base64}`；包内必须有Skill同名根目录。导入保存声明、文件清单、哈希和不可变内容快照，不安装依赖、不运行代码。

同名内容更新保留Skill ID，换新digest及快照；旧绑定目录消失，旧LangChain对象执行时也会拒绝。旧快照保留，业务数据不删除。完全相同digest重复导入复用注册。

## 3. 准备已有容器和核验实际挂载

管理员应在部署配置中为现有工具容器准备以下挂载（不在平台中创建服务）：

| 宿主机目录 | 示例容器目标 | 权限 |
|---|---|---|
| 平台`data/skills`快照根目录 | `/skill-code` | 只读 |
| 上述`shared_data_demo`整个目录 | `/data/p6_p7` | 读写 |

仅挂载sqlite文件不够，WAL、SHM和executions归档必须一起持久化。共享目录限于同一台机器本地磁盘；跨机器同名目录不等于共享SQLite。P6、P7、主Agent可复用同一个容器，也可各自用已有容器，但都必须实际映射到同一宿主机目录。需要不同容器时，请分别导入不同名称的Skill包并分别配置runtime（同一个Skill ID目前只有一个runtime）。

先执行`docker inspect <已有容器名> --format '{{json .Mounts}}'`，核对Source、Destination、RW及容器Linux/running状态。缺少挂载时由管理员调整已有容器的部署挂载；平台不会通过Docker exec补挂目录或创建新容器。示例只需标准库sqlite3及Python3。其他依赖须管理员事先在容器中准备并固定版本。

假设`$skill.path`为`data/skills/<digest>/shared-risk-demo`：

```powershell
$codeTarget = '/skill-code/' + $skill.digest + '/shared-risk-demo'
Post-Platform "skills/$($skill.id)/runtime" @{
  container='已有工具容器名'
  python='python3'
  code_target=$codeTarget
  storage=@{queue=@{resource='p6_p7';target='/data/p6_p7'}}
  enabled=$true
}
```

`runtime`和`skill-storage`接口只保存管理员配置，**不代表挂载已经可用**。每次实际工具调用都会Docker inspect核对Mounts；支持精确挂载以及只读快照根挂载下的对应子目录，源/目标相对后缀必须一致。读写属性不符、挂错目录或代码哈希不符都拒绝启动入口。代码和数据容器挂载点不能重叠。

## 4. 显式授权

先将Skill ID加入每个Agent的skill_ids，再配置能力绑定。仅绑定Skill正文不授权任何配套代码。可以使用原Agent页面，能力类别新增“Skill配套工具”，勾选工具并输入可信作业ID；存储和runtime配置通过上述管理员API完成。

```powershell
# 先通过现有Agent页面建立P6/P7/主Agent，并在各Agent绑定Skill。
$p6Id = 'P6的真实Agent ID'
$p7Id = 'P7的真实Agent ID'
$mainId = '主Agent的真实Agent ID'
$source = 'skill:' + $skill.id
Post-Platform 'bindings' @{agent_id=$p6Id;source=$source;tools=@('save');job_id='job-demo'}
Post-Platform 'bindings' @{agent_id=$p7Id;source=$source;tools=@('claim','complete','fail');job_id='job-demo'}
Post-Platform 'bindings' @{agent_id=$mainId;source=$source;tools=@('query','retry');job_id='job-demo'}
```

ToolProviders目录会显示`skill:<id>`，发现/确认仅预览声明，不运行代码；Agent授权继续使用原bindings。LangChain工具名自动命名为`cap_<binding前12位>__<原工具名>`；不同绑定隔离命名，截断后的冲突明确返回TOOL_NAME_COLLISION。不要让P7或P6获得retry，除非管理员业务权限确实允许。

主Agent→P7的submit/status/result协作权限继续在原协作页面配置。P6不建立到P7的协作授权。主Agent依据保存回执或query明确提交P7任务；队列写入不隐式调度P7。

## 5. 实际调用和状态

已配置模型时，从原会话/任务入口调度；工具会通过create_agent、PlatformMiddleware、StructuredTool、Agents授权门禁和Docker runner执行。也可在管理员Python会话直接检查同一LangChain入口（以下需要已有Service对象`s`，并非直接import Skill）：

```python
from platform_core.langchain_tools import build_tools
agent = s.store.get('agents', p6_id)
tools, mapping = build_tools(s, agent, 'administrator-demo-run')
save_tool = next(t for t in tools if mapping[t.name]['tool'] == 'save')
receipt = save_tool.invoke({'source_id': 'demo-window-1', 'description': '当前窗口发现头盔缺失'})
print(receipt)
```

P6 save生成pending；P7 claim原子写processing、worker/token/expires。P7 complete验证有效租约，保存研判并completed。重复source_id+相同描述返回原记录，不同描述报WRITE_CONFLICT；重复complete相同凭证/内容幂等，不同内容报COMPLETION_CONFLICT。

模型返回`status:failed/error`或执行异常后，Runtime使用保存的领取凭证调用fail。模型SDK timeout记录model_timeout；工具runner timeout记录tool_timeout；任务总期限、取消及进程正常关闭分别记录task_timeout、task_cancelled、execution_interrupted。模型退出但未提交研判记录assessment_not_committed；不会把缺少研判伪装completed。所有终态补偿都不会回退已完成记录。

failed只能由主Agent的retry明确恢复pending，并保留失败历史；示例最多3次领取尝试。过期worker不能提交；下次成功的队列调用检查过期租约并记录lease_expired。无需后台数据库服务。若平台硬崩溃、领取回执未返回、Docker连接中断或失败补偿本身不可用，不能保证立即落库：平台保留未关闭skill_claims和补偿错误审计，下一次队列调用通过过期租约落failed。租约超期不能冒充模型超时；无人调用时无后台更新。

错误码包括SKILL_TOOLS_INVALID、SKILL_ENTRY_INVALID、SKILL_DEPENDENCY_MISSING、SKILL_CODE_CHANGED、SKILL_STORAGE_MISSING/DISABLED、BINDING_DISABLED/STALE、CLI_MOUNT_CHANGED、CLI_TIMEOUT、CLI_EXIT_ERROR、CLI_OUTPUT_INVALID、CLI_TERMINATION_FAILED、CLI_RESULT_UNKNOWN、SKILL_OUTPUT_INVALID、LEASE_INVALID、RECORD_FORBIDDEN。CLI_RESULT_UNKNOWN表示无法确认执行结果，不自动重放业务写入。业务工具须有自己的幂等回执。

本例按平台可信Agent身份及固定job_id执行范围检查。声明中的逻辑存储目录不会成为模型可传入的参数；模型不能切换宿主机路径、容器、入口或作业范围。

## 6. 更新、停用、卸载和恢复

解绑Skill、停用Skill/Agent/runtime、停用或修订能力绑定都会影响新目录及已创建的工具对象。内容digest、runtime、共享存储配置或job_id变更后旧授权失效。更新Skill后核对新的digest路径，重新配置code_target并重新授权工具。审计保留旧digest。

卸载仍按原平台要求先从所有Agent解绑；删除只移除Skill注册，不删除快照或共享业务数据。旧binding记录不再可执行，可以通过原DELETE bindings接口清理。已经持久化的业务数据与归档不随Skill删除。

平台终态清理使用运行开始时保存的失败工具快照，目的仅是关闭已领取的原凭证；即使解绑或修订后也不重新领取或重试。该内部清理只使用原来已批准的context，不向模型公开额外权限。存储停用时补偿也被拒绝，并保留错误。重新授权不应自动重发未知业务动作。

## 7. 验证

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
```

test_skill_tools.py的PortableChild是**仅用于测试**的适配器：真实子Python运行生产LOADER，真实SQLite和归档，但替代Docker传输。生产没有宿主机Skill执行后端。模型协议测试启动原测试套件的短时本机fixture，使用真实HTTP SDK和LangGraph；响应是测试数据，不是付费模型推理。

可选真实Docker回归要求已有容器将`tests/.docker-code`只读挂到`/skill-code`，`tests/.docker-data`读写挂到`/skill-data`，容器内有python3。管理员先准备两个空测试根目录并调整现有测试容器挂载，再运行：

```powershell
$env:PLATFORM_SKILL_DOCKER_TEST_CONTAINER = '已有测试容器名'
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p test_skill_tools.py -v
```

可选测试会通过生产Docker SDK和CLI、真实LangChain调用验证P6/P7/主Agent共享库和归档，测试仅清理自己创建的临时子目录。容器未配置时明确skip，不将模拟挂载校验报告为真实挂载可用。本轮实际日志在`test/Skill配套工具测试日志.md`。

真实模型回归与Docker回归分开启用：

```powershell
$env:PLATFORM_SKILL_REAL_MODEL_TEST = '1'
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p test_skill_tools.py -k real_model -v
```

该测试只读现有`data/platform.db`中第一个已配置且启用的模型及凭据，在隔离数据库中建立测试Agent，不修改实际定义、不打印密钥。主Agent经现有submit_task调度P7，真实模型须通过LangChain调用claim和complete，断言成果已completed。工具进程仍采用PortableChild，因此真实模型测试通过也不能替代真实Docker挂载验收。本轮普通沙箱请求因OpenAIConnectionError失败，解除网络限制后该真实模型测试实际通过。
