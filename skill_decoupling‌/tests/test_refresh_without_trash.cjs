const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname,'../web/app.js'),'utf8');
const extract = (start,end) => source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start)));
const elements = Object.fromEntries(['session-select','chat-agent','session-delete-confirmation','session-name',
  'session-rename','session-delete','chat-status','chat-send','health'].map(id=>[id,{value:'',dataset:{},hidden:false}]));
elements['chat-agent'].value='agent';elements['session-select'].value='session';
let loads=0;
const snapshot={sessions:[{id:'session',agent_id:'agent',name:'Current conversation'}],tasks:[],scheduler:{}};
const context={state:{},refreshing:false,byId:id=>{
  assert.ok(elements[id],`Unexpected missing DOM element: ${id}`);return elements[id];
},api:async()=>snapshot,loadMessages:async()=>{loads++;}};
vm.createContext(context);
vm.runInContext(extract('function agentSessions(','function renderSessionOptions(')+
  extract('function renderSessionControls(','function messageBubble(')+
  extract('async function refresh()','/** 各模块列表'),context);
context.render=()=>context.renderSessionControls();
(async()=>{
  // A detail action calls refresh too; both refreshes must reach message loading.
  await context.refresh();await context.refresh();
  assert.equal(loads,2);assert.equal(context.refreshing,false);
  assert.equal(elements['session-name'].value,'Current conversation');
  assert.equal(elements['chat-send'].disabled,false);
  elements['session-select'].value='';snapshot.sessions=[];
  await context.refresh();
  assert.equal(loads,3);assert.equal(elements['session-delete'].disabled,true);
  assert.equal(elements['session-name'].value,'');
  console.log('Refresh and conversation loading pass without a recycle-bin DOM');
})().catch(error=>{console.error(error);process.exitCode=1;});
