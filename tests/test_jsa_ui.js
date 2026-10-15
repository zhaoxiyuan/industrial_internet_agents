// Run with node tests/test_jsa_ui.js from the repository root.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const source = fs.readFileSync('web/app.js', 'utf8');
const nodes = new Map();
const document = {getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, {
        style: {}, dataset: {}, textContent: '', innerHTML: '', disabled: false,
        removeAttribute(key) { delete this[key]; },
        classList: {contains() { return true; }},
    });
    return nodes.get(id);
}};
const context = vm.createContext({console, document, state: {}});
for (const name of ['firstValue', 'escapeHtml', 'displayValue', 'permitField', 'normalizeHazards', 'displayMissingField', 'renderPermitTable',
    'jsaDownloadUrl', 'renderJsaReport', 'renderP1Review', 'renderHistoryDocument', 'renderP1Approval']) {
    const start = source.indexOf('function ' + name + '(');
    assert(start >= 0, name);
    const end = source.indexOf('\nfunction ', start + 1);
    vm.runInContext(source.slice(start, end < 0 ? undefined : end), context);
}
const jsa = {
    source:'ai', job_name:'管道焊接', report_path:'/internal/report.docx',
    risk_summary:'动火风险概述', pre_checks:['检查隔离'], special_sections:[{title:'动火专项',items:['看火监护']}],
    emergency:'撤离处置', final_checklist:['现场确认'],
    steps:[{step:'焊接', hazard:'<script>x</script>', consequence:'灼伤',level:'高',control:'清理可燃物'}],
};
context.detail = {job_id:'20261013123456789', permit:{jsa_result:jsa},
    p1_result:{jsa_result:jsa,jsa_info_lines:['作业地点：装置区'],pending_confirmation:{type:'jsa_approval',approval_id:'review-jsa'}}};
const report = vm.runInContext('renderP1Review(detail)', context);
for(const text of ['作业基本信息','装置区','作业风险概述','作业前安全确认要求','主要危险','焊接','灼伤','清理可燃物','动火专项','应急处置要求','作业前最终确认清单']) assert(report.includes(text),text);
assert(!report.includes('<script>x</script>'));
assert(!report.includes('下载 JSA 报告'));
assert.equal(document.getElementById('jsa-download-button').style.display,'');
assert.equal(document.getElementById('jsa-download-button').href,'api/workflow/permit-docx?artifact=jsa&job_id=20261013123456789');
assert.equal(document.getElementById('permit-download-button').style.display,'none');
assert.equal(document.getElementById('hitl-approve-button').disabled,false);
assert.equal(document.getElementById('hitl-modal').dataset.approvalId,'review-jsa');

context.detail.permit.permit_content={permit_rows:[[{text:'票面起点'}]]};
context.detail.p1_result.pending_confirmation={type:'permit_final_approval',approval_id:'review-ticket'};
const ticket=vm.runInContext('renderP1Review(detail)',context);
assert(ticket.includes('票面起点'));
assert(!ticket.includes('下载 JSA 报告'));
assert.equal(document.getElementById('jsa-download-button').style.display,'none');
assert.equal(document.getElementById('permit-download-button').style.display,'');
assert.equal(document.getElementById('hitl-modal').dataset.approvalId,'review-ticket');



context.detail.permit.jsa_result = context.detail.p1_result.jsa_result = {source:'unavailable',error:'model failed'};
context.detail.p1_result.pending_confirmation={type:'jsa_approval',approval_id:'failed-jsa'};
assert(vm.runInContext('renderP1Review(detail)',context).includes('JSA 分析未完成'));
assert.equal(document.getElementById('hitl-approve-button').disabled,true);
assert.equal(document.getElementById('jsa-download-button').style.display,'none');
context.detail.permit.jsa_result = context.detail.p1_result.jsa_result = jsa;
context.state.historyPermitDetail=context.detail;
vm.runInContext('renderHistoryDocument(false)',context);
assert(document.getElementById('history-permit-content').innerHTML.includes('票面起点'));
assert.equal(document.getElementById('history-jsa-download').style.display,'none');
vm.runInContext('renderHistoryDocument(true)',context);
assert(document.getElementById('history-permit-content').innerHTML.includes('作业风险概述'));
assert(!document.getElementById('history-permit-content').innerHTML.includes('票面起点'));
assert.equal(document.getElementById('history-jsa-download').style.display,'');
assert.equal(source.split("fetch('/api/").length-1,26);
for(const key of ['WS_STATUS_PORT','WS_LOGS_PORT']) assert.equal(source.split('ws://localhost:${'+key+'}').length-1,1);
for(const base of ['http://localhost:8080/index.html','http://localhost:3000/jobmon/index.html']) {
 const url=new URL('api/workflow/permit-docx?artifact=jsa&job_id=20261013123456789',base);
 assert(url.pathname.endsWith('/api/workflow/permit-docx'));
 if(base.includes('/jobmon/')) assert(url.pathname.startsWith('/jobmon/'));
}
console.log('Separate JSA/ticket previews, footer downloads, approval IDs, escaping and integration counts: OK');

// The console HTML probe accepts this fixed script address.
const htmlSource=fs.readFileSync('web/index.html','utf8');
assert(/<script\b[^>]*\bsrc=["'](?:\.\/)?app\.js["']/.test(htmlSource));
