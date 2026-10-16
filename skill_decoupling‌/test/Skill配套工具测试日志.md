# Skill配套工具实际测试日志

日期：2026-10-13，Asia/Shanghai。工作目录：`C:\Users\13021\Desktop\agent-skill\industrial_internet_agents\skill_decoupling‌`。解释器：项目`.venv/Scripts/python.exe`，启用UTF-8。测试在隔离临时数据库、共享目录及短时测试子进程中运行；没有删除或写入现有业务库，没有新建常驻HTTP/数据库/Docker服务。

## 最终结果

最终完整回归命令：`.venv/Scripts/python.exe -X utf8 -m unittest discover -s tests -v`。实际运行79项，耗时119.168秒；76项通过，3项显式跳过，0项失败或错误。

三项跳过分别是原有真实Docker测试、新增真实Skill Docker测试、默认关闭的真实模型测试。真实模型另行显式启用运行，解除网络沙箱限制后：实际1项通过，耗时15.347秒。Docker两项没有实际通过，不计入通过数量。

`node --check web/binding-controls.js`通过；Python编译检查及`git diff --check`通过。Git仅提示既有文件的LF/CRLF转换，不是测试失败。

## 实际通过项及证据

| 项目 | 本轮真实执行与结果 |
|---|---|
| 导入不执行代码 | 导入、runtime配置、存储配置、授权后，测试执行器调用次数仍为0；导入保存6个结构化工具及文件哈希。 |
| 声明与代码入口 | 越界路径、缺失函数、重复工具名、非封闭输入契约被拒绝；Python字节码缓存包被拒绝。入口代码通过AST而非执行校验。 |
| 平台目录及授权 | Skill绑定但未工具授权的Agent看不到工具；提供者发现/确认复用原目录，配套claim/fail必须一起授权。新增Skill工具类别和固定job_id表单。 |
| LangChain真实调用 | 实际StructuredTool.invoke经Agents门禁及生产LOADER启动子Python，写入真实SQLite与归档；不是直接在平台进程import Skill。Docker传输在此测试中被PortableChild替代。 |
| 参数与范围 | 额外宿主机path参数被拒绝；空作业ID被拒绝；job_id授权变化使旧工具对象失效；新job范围无法读取原job记录；平台私有目录不能注册为共享存储。 |
| 多Agent共享 | P6保存、P7领取/完成、主Agent查询/重试使用各自绑定身份访问同一目录及P6_P7.sqlite3，进程退出后另一个子进程仍能查询。 |
| 原子领取 | 四个并发子进程领取一条pending记录，仅一个得到processing，其余no_work。SQLite使用BEGIN IMMEDIATE和租约凭证。 |
| 租约栅栏 | 领取过期后的完成被LEASE_INVALID拒绝；后续查询持久化lease_expired；显式重试产生新token，旧token不能提交。 |
| 成功路径 | pending→processing→completed；主Agent查询取得研判成果及完整历史。终态补偿不回退completed。 |
| 失败及显式重试 | 领取后平台调用失败补偿，持久化failed和model_failed；普通claim不消费failed；主Agentretry才恢复pending，失败历史保留。 |
| 工具执行超时 | 实际测试子Python睡眠5秒，执行层1秒期限终止，返回CLI_TIMEOUT；平台捕获后对本run已有领取凭证落库failed/error=tool_timeout。该项验证PortableChild的子进程执行期限及生产补偿逻辑，不能声称已验证Linux Docker进程组终止。 |
| 模型SDK超时 | 真实ChatOpenAI HTTP SDK访问本机测试模型接口：先领取，下一轮接口延迟11秒、model_timeout=10秒；SDK实际抛出超时，平台任务MODEL_TIMEOUT，队列failed/error=model_timeout。这是网络SDK超时，和工具超时分开测试；响应不是付费模型推理。 |
| 模型返回失败 | 本机模型协议响应最终status=failed，Runtime自动使用已保存claim调用失败工具，无需模型再次输出工具调用。 |
| 主Agent调度 | 主Agent经原submit_task工具安排P7，LangGraph完成领取、研判保存及任务成果；断言子任务caller_agent_id为主Agent；P6目录没有submit_task。 |
| 幂等性 | 同来源/同描述重复保存返回原record_id；内容变化WRITE_CONFLICT；相同complete重复成功，不增加完成历史；不同内容COMPLETION_CONFLICT。 |
| 生命周期 | Skill解绑、停用，能力绑定停用/恢复，内容更新、共享存储配置更新后旧目录或旧对象失效；同名更新保留Skill ID但换digest；先解绑后卸载，数据库及归档仍存在。 |
| 依赖及执行错误 | 缺失声明依赖SKILL_DEPENDENCY_MISSING；实际子进程退出7得到CLI_EXIT_ERROR；非JSON输出CLI_OUTPUT_INVALID；输出不符合契约SKILL_OUTPUT_INVALID；快照篡改SKILL_CODE_CHANGED。 |
| 挂载门禁 | 使用Docker SDK/CLI测试替身构造错误实际Mounts，CLI_MOUNT_CHANGED且未启动子命令；只读快照父挂载下相对目录映射校验通过，错误RW或路径后缀被拒绝。进程退出未确认的runner回执返回CLI_TERMINATION_FAILED。以上是门禁逻辑测试，不是真实挂载验收。 |
| 现有功能回归 | 原有CLI权限/修订、MCP真实本机HTTP协议、直连HTTP、飞书测试接口、授权撤销、协作任务/等待恢复、成果引用和框架检查点回归通过。没有将公网飞书或真实Docker替换测试报告为实际通过。 |

## 真实模型独立测试

只读检查现有platform.db发现2个已配置并启用的模型Agent，不输出凭据。真实模型测试从现有配置取一个模型，在临时平台中建立P6/P7/主Agent，使用临时共享数据库；现有定义、任务和业务数据不被修改。

第一次在普通网络沙箱运行，OpenAIConnectionError，任务失败，该次真实推理未通过。经允许解除网络限制后重跑，真实模型通过LangGraph和LangChain调用claim及complete；工具审计存在这两次调用，SQLite记录completed及非空研判，主Agent能读到成果。最终显式测试实际通过。

明确边界：此项是真实远端模型推理，但Skill执行采用测试专用PortableChild，不是Docker。因此真实模型＋真实Docker的组合尚未验收。模型返回失败和模型超时的精确故障注入使用本机协议fixture，未人为让付费模型超时，也未把fixture输出冒充真实模型结果。

## 失败项、修复与环境限制

开发期间发现并修复三类问题：停用绑定错误码被新修订指纹提前覆盖（恢复BINDING_DISABLED兼容语义）；Windows子进程中文输出未固定UTF-8（使用-X utf8）；默认Python入口加载器读写字节码缓存导致后续哈希错误（改为在子进程直接编译已校验源码，并拒绝导入pyc/pyo及__pycache__）。修复后针对Skill测试及上述完整回归均通过。

模型超时fixture在SDK已关闭连接后尝试写回响应，出现预期ConnectionAbortedError/WinError 10053；测试断言及失败落库均通过。这是测试接口的断开日志，不是后台业务处理失败。

本机Docker普通检查和解除沙箱限制后的docker ps检查都失败。后者明确提示`dockerDesktopLinuxEngine`命名管道不存在，daemon不可连接。没有启动Docker Desktop、创建容器、调整现有容器挂载或新增服务。因此当前环境无法确认真实Source/Destination/RW、Linux进程组终止、真实Docker退出后数据留存。平台代码在调用时强制Docker inspect校验，不会把保存配置当成挂载可用；可选RealSkillDockerTests已保留，需管理员提供教程所述已有测试容器后运行。

平台硬崩溃、未返回领取回执、Docker失联或失败补偿本身不可用时，不能保证立即写入failed；保留未关闭领取回执及补偿错误审计，下一次队列调用检查过期租约，记录lease_expired。无调用时没有后台进程更新状态。容器是审核后代码的信任边界，不宣称Python隔离模式能防恶意代码读取其他容器挂载。

## 修改文件和清理

本轮修改：app.py；platform_core/skills.py、skill_tools.py（新增）、agents.py、service.py、tool_registry.py、external_cli.py、runtime.py；web/index.html、binding-controls.js；README.md；md/Skill配套工具接入教程.md（新增）；examples/skills/shared-risk-demo的SKILL.md、tools.json、scripts/queue.py（新增）；tests/test_skill_tools.py（必要回归测试，新增）；本test目录文字日志（新增）；P_skill/P6/DEVELOPMENT.md的机制验收补充。

仓库原有测试保留。测试业务JSON、数据库及临时联动目录由TemporaryDirectory清理，临时命令生成器未保存为脚本；临时原始测试输出TXT在整理本文后删除。tools.json是交付声明，不能作为临时测试报告删除。未删除用户现有业务数据，未覆盖既有未提交修改。

结论：平台代码已经支持“Skill携带工具，管理员显式授权后经原LangChain调用链执行”；进程/SQLite/框架及真实模型联动已验证。真实Docker及实际共享挂载仍受daemon不可用限制，尚未完成部署环境验收。
