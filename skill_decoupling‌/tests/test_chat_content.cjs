const {test}=require('node:test');
const assert=require('node:assert/strict');
const chat=require('../web/chat-content.js');

test('think is hidden through every streamed tag boundary and historical variants',()=>{
  const source='<think>PRIVATE_REASONING</think>\n## 回复\n**完成**';
  for(let i=1;i<=source.length;i++)assert.ok(!chat.hideThink(source.slice(0,i)).includes('PRIVATE_REASONING'));
  assert.equal(chat.hideThink('<THINK>private</THINK>answer'),'answer');
  assert.equal(chat.hideThink('before<think>one<think>two</think>three</think>after'),'beforeafter');
  assert.equal(chat.hideThink('before<think>unfinished'),'before');
});
test('tables, alignment, lists and fenced code render as structured content',()=>{
  const html=chat.markdown('## 状态\n\n| Agent | 状态 |\n| :--- | ---: |\n| P6 | **completed** |\n| P7 | pending |\n\n- 第一步\n  - 子步骤\n- 第二步\n\n```json\n{"status":"completed"}\n```');
  assert.match(html,/<table>/);assert.match(html,/<th style="text-align:right">状态/);
  assert.match(html,/<strong>completed/);assert.match(html,/<ul><li><p>子步骤/);
  assert.match(html,/<pre><code>\{&quot;status/);
  assert.match(chat.markdown('| 字段 | 值 |\n|--|--|\n| path | `a|b` |'),/<td[^>]*><code>a\|b<\/code>/);
});
test('HTML and dangerous links cannot become executable DOM',()=>{
  const html=chat.markdown('<img src=x onerror="alert(1)">\n\n[bad](javascript:alert)\n\n[good](https://example.org)\n\n```html\n<script>alert(1)</script>\n```');
  assert.ok(!html.includes('<img'));assert.ok(!html.includes('<script>'));
  assert.ok(!html.includes('href="javascript:'));
  assert.ok(html.includes('rel="noopener noreferrer"'));
});
