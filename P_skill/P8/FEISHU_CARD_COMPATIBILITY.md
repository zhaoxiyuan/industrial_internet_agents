> 2026-10-15 当前上游已改为时间窗口单票流程：Main按时间调度，P6一次写入一份描述；本文的下游/界面机制继续适用。旧源领取及候选票补办流程不再适用；本轮真实验收以 LIVE_PLATFORM_ACCEPTANCE.md 为准。P10本轮不测试。


> 当前事件推送契约：[持续风险通知与同一卡片维护](../LIVE_EVENT_NOTIFICATIONS.md)。开放事件也需要由Main调度P8推送，最终归档仍在结束后生成。旧“结束后才告警”约束已替换。

# 平台飞书卡片迁移：a_mac实现兼容约定

更新：2026-10-13。用途：将用户已验证的a_mac卡片能力集成进平台飞书模块。本轮已将卡片构造与协议接入平台，使用本地模拟HTTP校验；未发送真实飞书卡片；实现以列出的代码及既有测试为迁移基线，不以旧设计文档或过时注释覆盖实际执行路径。

## 1. 归属和源码清单

卡片构造、飞书协议、连接凭据、发送、原位更新、回调接收均归平台能力；P8/P9 Skill只有业务编排和获授权的业务读写工具。禁止在Skill tools.json定义feishu_send_card等通道工具，禁止从Skill调用a_mac发送模块。

| 原实现 | 迁移时复用内容 |
|---|---|
| [cards.py](../../a_mac/P8P9/cards.py) | build_job_card、状态模板、普通按钮、inline_form、输入框和上传入口 |
| [models.py](../../a_mac/P8P9/models.py) | 卡片schema、状态模板映射、等级颜色、表单/业务校验常量 |
| [callback_router.py](../../a_mac/P8P9/services/callback_router.py) | 平铺/嵌套载荷、value解析、action路由及表单字段映射 |
| [web_server.py](../../a_mac/P8P9/web_server.py) | /feishu/card/callback实际响应、raw卡片回传和业务错误toast |
| [card_render.py](../../a_mac/P8P9/services/card_render.py) | 回调上下文、延迟更新队列、节流、每事件渲染及CardKit序号 |
| [feishu_card.py](../../a_mac/feishu_gateway_cli/feishu_card.py) | 创建/全量更新实体、账号解析、token缓存、alert_id索引 |
| [feishu_sender.py](../../a_mac/feishu_gateway_cli/feishu_sender.py) | interactive卡片实体引用、先存card_id再发送、补齐message_id |
| [upload_routes.py](../../a_mac/P8P9/upload_routes.py)、[upload_link_signing.py](../../a_mac/P8P9/upload_link_signing.py)、[links.py](../../a_mac/P8P9/links.py) | 签名入口、上传token、材料绑定及消费校验 |
| [business_actions.py](../../a_mac/P8P9/business_actions.py) | 接取、提交、退接、升级、降级及人工终审的业务校验 |

迁移保持模块边界：平台通道负责飞书协议，处置适配器负责领域状态；不能把整套a_mac Agent和模型运行时嵌进平台通道。

## 2. Card 2.0构造约定

- 顶层保持`schema: "2.0"`、`config: {update_multi: true}`、header和body.elements，保证群成员看到一致更新。
- 普通callback_button的value由json.dumps序列化为JSON字符串。回调解析兼容dict、单层JSON字符串和双层JSON字符串，不强行统一成只接受字符串。
- inline_form使用真实`tag: "form"`容器；按钮`action_type: "form_submit"`，按钮value保留对象。这里不能套用普通按钮的字符串规则。
- 表单按钮不添加behaviors callback，否则旧实现已遇到表单值不按form_submit返回的问题。保留当前inline value方式。
- 输入框`tag: "input"`、`input_type: "multiline_text"`、required、max_length、rows、auto_resize；不使用textarea、form_action或form_action_type等旧错误字段。
- 长度下限由业务层验证，不能把min_length写入飞书输入组件。旧helper的min_length参数只映射required。
- 整张卡片元素name必须唯一；保留`<action>_<form_name>`提交按钮命名，批准与驳回输入分别为approved_comment与rejection_reason。
- URL按钮沿用`behaviors: [{type: "open_url", default_url: ...}]`，不重新发明URL字段。

迁移应使用cards.py当前被_BUILDERS调用的inline_form路径；文件里尚存在但已被替代的callback_button_with_form不能因“函数存在”就恢复使用。

## 3. 保持当前用户操作方式

| 展示状态 | 当前交互，迁移应保留 |
|---|---|
| open | 接取任务callback + 查看详情 |
| acknowledged | 卡片内填写review_text并提交；卡片内填写reason退接；附件另开上传页 |
| rectifying | 保留驳回意见，恢复上述提交/退接/上传操作 |
| materials_in_audit | 显示P9意见，人工“审核通过·关闭”和“驳回·退回整改”两个独立表单 |
| waiting_human_review / ready_to_close | 按现有对应模板呈现终审操作，不由模型意见擅自推进 |
| closed | 灰色标题，关闭摘要、材料/报告查看；不再呈现整改提交动作 |

内部可分开disposition_status、closure_status、human_review_status，但向现有模板提供明确单事件投影视图。P7 occurrence_status=closed不映射为灰卡；只有该事件的P9 closure_status=closed才映射closed模板。

保留以下action及含义，不另起同义动作名替换已验证路由：acknowledge_disposition、submit_rectification_materials、relinquish_job、escalate_risk、downgrade_risk、record_closure_review、download_attachment。未来新增动作另做协议版本化。

表单字段保留review_text、reason、approved_comment、rejection_reason、decision、expected_version。路由优先读value.action，再读已有兼容字段/name；批准/驳回输入映射为业务comment，移除表单专用字段后调用业务函数，避免多余kwargs导致TypeError。input_value兼容路径保留并用原测试载荷验证。

## 4. 回调响应与原位更新

以web_server.py实际函数为准：成功响应HTTP 200，响应体顶层为toast以及可选card，不附带status、error或内部业务结果。原实现备注记录额外字段曾导致200672响应体格式错误。

```json
{
  "toast": {"type": "success", "content": "已记录您的处置"},
  "card": {"type": "raw", "data": {"schema": "2.0", "config": {"update_multi": true}, "header": {}, "body": {"elements": []}}}
}
```

这是响应形状示意，header及elements由真实模板构造，不能发送该空模板。card.data是对象，不是JSON字符串，也不是type=card_json。只有拿到已提交状态投影才回传新卡片；业务失败沿用warning/error toast，不返回伪成功。

保留“同步响应点击者raw卡片、回调结束后更新群卡片实体”的两段处理：回调内不等待LLM或CardKit网络。card_render.py通过card_callback_context记录延迟队列，not_before延后0.5秒，原节流常量为0.2秒；这些是现有实现值，迁移保持行为并回归验证，不把它们写成通用飞书限频承诺。

平台持久化卡片更新任务，消费时读最新业务投影并合并过期更新；业务写成功而更新失败，只恢复展示，不回退业务。该平台队列不是Skill内后台线程，也不触发P7→P8或P9模型。web_server.py的实际raw响应优先于feishu_card.py顶部旧注释中“只返回toast”的过时描述。

## 5. CardKit实体和消息发送

按当前feishu_card.py及feishu_sender.py保留两步语义：

1. 创建实体：POST `/open-apis/cardkit/v1/cards`，body为`{type: "card_json", data: "序列化Card 2.0 JSON"}`，取得card_id并持久化。
2. 发消息：msg_type为interactive，content是`{"type":"card","data":{"card_id":"..."}}`序列化字符串。不能只传`{"card_id":"..."}`，不能用文本feishu_send代替。
3. 全量原位更新：PUT `/open-apis/cardkit/v1/cards/{card_id}`，body含`card: {type: "card_json", data: "..."}`、uuid及严格递增sequence。这里是PUT，不按旧文档写成PATCH。

旧发送实现第二步经Gateway，新平台已有直接飞书连接；平台适配可改变内部传输入口，但必须保持interactive实体引用、账号选择、回执及错误语义，不能顺便改变卡片协议。token/账号解析全部迁到平台，不把旧.env读写逻辑下放Skill。

平台索引至少保存(connection_ref, alert_id)→card_id、message_id、chat_id、sequence、最新投影版本。保留原稳定alert_id规则`P8P9-{job_id}-{risk_event_id}`或维护显式无损迁移映射；不能将risk_event_id、ERP job_id、message_id互相替代。发送成功补message_id时保留已存sequence，不能重置为0。

sequence为通道更新序号，expected_version为业务乐观锁，互不替代。平台按卡片串行化/原子分配序号并保留重试操作身份。首次发送幂等键采用稳定report_id/alert_id，不照搬旧resend/init键每次加入当前时间的方式；结果unknown时核对原操作，不以新键重复发送。

## 6. 附件上传与提交

沿用现有“卡片说明表单+独立附件页”流程，不将全部交互改成Web表单：

```text
卡片上传附件按钮
 → /api/closure/upload/new 的签名链接
 → 校验签名、有效期、版本和接取人范围
 → 签发tk_上传token并302至/api/closure/upload?token=...
 → multipart上传，保存upload_id及元数据
 → 完成页提示回卡片
 → submit_rectification_materials提交说明与upload_token关联的附件
```

upload_link_signing.py的HMAC绑定job_id、target、open_id、version、expires；当前入口签名有效期常量7天，具体上传token为30分钟，两者不能混写成同一个期限。materials_submission入口还核对accepted_by.open_id。平台适配新增逐事件范围时将risk_event_id/disposition_task_id纳入验证后的映射或版本化签名，不能只加未签名查询参数。

正式提交通过consume_upload_token验证有效期、actor及job_id后取得uploads，上传本身不代表材料已提交。需要保留upload_token从上传页到提交动作的传递/关联；若当前所选调用路径未携带它，应列为迁移联调缺口，不能假设“最近上传的文件”自动属于当前事件。

签名URL是bearer能力，不等于浏览器实名登录；以服务器签名的来源主体记录上传，最终卡片提交的actor来自验证后的飞书事件。下载沿用附件ID与受控短链，不把文件系统路径或长期密钥写进卡片。

## 7. 必要适配与禁止静默变化

| 项目 | 迁移处理 |
|---|---|
| 同作业多个事件的卡片动作 | 保持现有交互组件与action，扩展业务路由为单事件上下文；新卡增加事件/任务标识，旧卡按可信索引解析，歧义拒绝 |
| 卡片点击审核通过·关闭 | 保留一次人工动作关闭当前事件；不能扩大为整个ERP作业关闭，作业关闭使用单独明确确认 |
| 旧job级card_binding及更新队列 | 改为按alert_id/事件索引，避免操作某个事件刷新或关闭另一个事件 |
| 回调新增schema_version/nonce | 是平台适配扩展，不能宣称旧载荷必有；兼容旧字段解析并通过平台去重/索引核验 |
| 旧业务校验 | 默认保留当前代码值与行为；包括复核理由至少10字、退接限制和上传人校验，不凭旧README不同数值重写 |
| approved评论含“驳回” | 当前代码会翻转为rejected；迁移先保留并审计原请求/最终决定，若要改为拒绝并重选须单独对齐，模型意见不得成为此comment |
| 驳回清除materials当前视图 | 可保留清空当前待审核视图的交互，持久化提交历史不能删除，供P9/P10追溯 |
| P9高confidence文案“审核已通过” | 不改按钮协议；建议展示为“模型意见已生成，待人工终审”，避免误认人工结果，实施时作为文案修正明确记录 |
| 卡片写死“30分钟自动升级” | 沿用布局；只有真实配置且调度已实现时才显示承诺，不把模板文字当成自动升级能力 |

## 8. 迁移验收依据

复用并适配a_mac/P8P9/tests中的test_cards.py、test_callback_router.py、test_button_callback.py、test_card_callback_response.py、test_card_account_resolution.py、test_upload.py及test_business_actions.py。test_send_card_real.py作为后续明确安排的真实通道联调依据，本轮不发送消息。

重点检查普通与form_submit的不同value、name唯一、input/form字段回传、approved_comment映射、平铺与双层JSON回调、raw响应无额外顶层字段、群内持久更新、sequence不归零、同事件不重发、跨事件操作拒绝、附件token消费和版本过期。新平台真实飞书回归通过后才能标为迁移完成，不能仅凭旧系统验证过就宣称新接入也已验证。

## 当前实现映射

平台使用feishu_sync_risk_card合并发送、更新和回执读取。原cards.py/models.py保留不改；平台投影在等待人工审核时使用已验证materials_in_audit独立form模板，避免旧waiting模板重复comment字段。回调兼容普通及双层编码value、已验证网关平铺载荷、approved_comment/rejection_reason及旧comment，成功返回仅含toast与raw card。详情/附件路径为/risk-card、/risk-upload、/risk-material；签名范围增加report_id。上传入口与卡片提交按钮共享签名batch_id，提交核验连接、事件、版本、actor与材料有效期并消费材料，禁止按最近文件关联。7天入口/批次签名与30分钟实际上传令牌分别校验。退接理由至少10字、最多2次，保留现有业务常量。具体实现与测试边界见[IMPLEMENTATION.md](../IMPLEMENTATION.md)。


当前执行契约已更新为v5：完整窗口观测、可信人工决定关闭、超时撤销并回收领取、Main独立心跳仅对自身防重入。详见[当前作业与心跳契约](../CURRENT_WORKFLOW.md)。文中带日期的验收记录保留当时事实，不作为现行工具约束。
