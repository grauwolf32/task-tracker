// Run with Node's built-in test runner; no browser or frontend dependency install needed.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, 'public/app.js'), 'utf8');

const task = (id='T-1', extra={}) => ({
  data: {id, title: id, goal: 'Original', status: 'pending', priority: 'P2', epic: 'quality', depends_on: [], ...extra},
  raw: 'id: '+id, file: id+'.yml', version: '"v1"'
});
const snapshot = tasks => ({tasks, epics: [{id:'quality',title:'Quality'}], errors:[], source:'/tmp/tasks', project:'Test'});
const response = (data, status=200) => ({ok:status>=200&&status<300, status, headers:{get:()=> '"r1"'}, json:async()=>data});
const deferred = () => {let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};

async function app(tasks=[task()]) {
  const elements = new Map(), windowListeners = {}, documentListeners = {};
  let document;
  const get = selector => {
    if (!elements.has(selector)) {
      const classes = new Set();
      elements.set(selector, {
        id:selector.startsWith('#')?selector.slice(1):'', dataset:{}, style:{}, options:[{text:'All'}],
        value:'', innerHTML:'', textContent:'', hidden:false, disabled:false, scrollTop:0, tabIndex:0, isConnected:true,
        classList:{contains:k=>classes.has(k),toggle:(k,on)=>{if(on===undefined)on=!classes.has(k);if(on)classes.add(k);else classes.delete(k);}},
        focus(){document.activeElement=this;}, getClientRects(){return [{}];},
        setAttribute(name,value){this[name]=value;},
        closest(selector){return selector==='[data-task]'&&this.dataset.task?this:null;},
        querySelectorAll(){return [];},
        get elements(){return ['#edit-title','#edit-status','#edit-goal','#save-task','#reload-draft'].map(get);}
      });
    }
    return elements.get(selector);
  };
  document = {hidden:false, body:get('body'), activeElement:get('body'), querySelector:get,
    querySelectorAll:()=>[], getElementById:id=>get('#'+id),
    addEventListener:(name,handler)=>{documentListeners[name]=handler;}};
  const requests = [], handlers = [];
  const context = vm.createContext({document, window:{addEventListener:(name,handler)=>{windowListeners[name]=handler;}},
    location:{hash:'',pathname:'/'}, history:{replaceState(){}},localStorage:{getItem(){return null;},setItem(){}},
    navigator:{clipboard:{writeText:async()=>{}}}, confirm:()=>true, AbortController,
    setTimeout:()=>1, clearTimeout(){}, console,
    fetch:(url,options={})=>{requests.push({url,options});return handlers.length?handlers.shift()(url,options):Promise.resolve(response(snapshot(tasks)));}
  });
  vm.runInContext(source,context,{filename:'app.js'});
  const run = code => vm.runInContext(code,context);
  await run('currentRefresh');
  return {run,get,context,requests,handlers,windowListeners,documentListeners};
}

test('active filters survive removal of their last matching task', async()=>{
  const a=await app();
  a.run("state.epic='quality';state.status='pending';state.tasks=[];options();");
  assert.equal(a.run('state.epic'),'quality');
  assert.equal(a.run('state.status'),'pending');
  assert.match(a.get('#epic').innerHTML,/quality.*нет задач/);
});

test('sort direction and unknown prototype-like statuses render predictably', async()=>{
  const a=await app([task('T-1',{priority:'P3'}),task('T-2',{priority:'P0'}),task('T-3',{status:'__proto__'})]);
  a.run("state.sort='priority';state.descending=false;");
  assert.equal(a.run('filtered()[0].data.id'),'T-2');
  a.run('state.descending=true;');
  assert.equal(a.run('filtered()[0].data.id'),'T-1');
  a.run("state.view='board';render();");
  assert.match(a.get('#task-content').innerHTML,/Другие статусы/);
  assert.match(a.get('#task-content').innerHTML,/data-task="T-3"/);
});

test('failed explicit reload keeps the dirty draft and its original version', async()=>{
  const a=await app();
  a.run("openTask('T-1');state.tab='edit';detailBody();state.draft.values.goal='Unsaved draft';");
  a.handlers.push(()=>Promise.reject(new Error('offline')));
  await a.run('reloadDraft()');
  assert.equal(a.run('state.draft.values.goal'),'Unsaved draft');
  assert.equal(a.run('state.draft.version'),'"v1"');
  assert.equal(a.run('hasUnsaved()'),true);
  assert.equal(a.get('#edit-goal').disabled,false);
});

test('save locks inputs, acknowledges successful writes separately from failed refreshes', async()=>{
  const a=await app();
  a.run("openTask('T-1');state.tab='edit';detailBody();state.draft.values.goal='Submitted';");
  const put=deferred();
  a.handlers.push(()=>put.promise,()=>Promise.reject(new Error('read failed')));
  const saving=a.run('saveDraft({preventDefault(){}})');
  assert.equal(a.get('#edit-goal').disabled,true);
  assert.equal(JSON.parse(a.requests.at(-1).options.body).goal,'Submitted');
  put.resolve(response({file:'T-1.yml'}));
  await saving;
  assert.equal(a.run('hasUnsaved()'),false);
  assert.equal(a.run('state.savedTask.id'),'T-1');
  assert.match(a.get('#edit-notice').textContent,/Изменения сохранены/);
  assert.equal(a.get('#save-task').disabled,true);
  assert.equal(a.get('#edit-goal').disabled,true);
  assert.equal(a.get('#reload-draft').disabled,false);
});

test('a read started before save cannot discard the draft or acknowledge fresh data', async()=>{
  const a=await app();
  a.run("openTask('T-1');state.tab='edit';detailBody();state.draft.values.goal='Submitted';");
  const stale=deferred(),put=deferred();
  a.handlers.push(()=>stale.promise,()=>put.promise,()=>Promise.reject(new Error('fresh read failed')));
  const reading=a.run('refresh(true)');
  const saving=a.run('saveDraft({preventDefault(){}})');
  put.resolve(response({file:'T-1.yml'}));
  await Promise.resolve();await Promise.resolve();
  stale.resolve(response(snapshot([task()])));
  await reading;await saving;
  assert.equal(a.run('state.tab'),'edit');
  assert.equal(a.run('state.draft.values.goal'),'Submitted');
  assert.equal(a.run('state.savedTask.id'),'T-1');
  assert.match(a.get('#edit-notice').textContent,/Изменения сохранены/);
});

test('deleted source stays recoverable from a dirty draft outside the edit tab', async()=>{
  const a=await app();
  a.run("openTask('T-1');state.tab='edit';detailBody();state.draft.values.goal='Keep me';state.tab='relations';detailBody();");
  a.handlers.push(()=>Promise.resolve(response(snapshot([]))));
  await a.run('refresh(true)');
  assert.equal(a.run('state.selected'),'T-1');
  assert.equal(a.get('#detail-warning').hidden,false);
  a.run("state.tab='edit';detailBody();");
  assert.match(a.get('#detail-body').innerHTML,/Keep me/);
  assert.equal(a.get('#save-task').disabled,true);
});

test('browser navigation is guarded for dirty drafts and in-flight writes', async()=>{
  const a=await app();
  const event=()=>({prevented:false,preventDefault(){this.prevented=true;}});
  let e=event();a.windowListeners.beforeunload(e);assert.equal(e.prevented,false);
  a.run("openTask('T-1');draftFor(state.selectedItem).values.goal='Dirty';");
  e=event();a.windowListeners.beforeunload(e);assert.equal(e.prevented,true);
  a.run('state.draft=null;state.saving=true;');
  e=event();a.windowListeners.beforeunload(e);assert.equal(e.prevented,true);
});

test('dependency graph bounds both directions and handles malformed test descriptions', async()=>{
  const tasks=[task('T-1',{depends_on:Array.from({length:250},(_,i)=>'UP-'+i), tests:[null,{command:'check',covers:'bad'}]})];
  for(let i=0;i<250;i++)tasks.push(task('DOWN-'+i,{depends_on:['T-1']}));
  const a=await app(tasks);
  a.run("openTask('T-1');state.tab='relations';detailBody();");
  assert.equal((a.get('#detail-body').innerHTML.match(/class="relation-card/g)||[]).length,201);
  assert.match(a.get('#detail-body').innerHTML,/Показаны первые 100 зависимостей/);
  a.run("state.tab='checks';detailBody();");
  assert.match(a.get('#detail-body').innerHTML,/Команда не указана/);
});

test('acknowledged save followed by missing source retains submitted text and warning', async()=>{
  const a=await app();
  a.run("openTask('T-1');state.tab='edit';detailBody();state.draft.values.goal='Submitted before deletion';");
  a.handlers.push(()=>Promise.resolve(response({file:'T-1.yml'})),()=>Promise.resolve(response(snapshot([]))));
  await a.run('saveDraft({preventDefault(){}})');
  assert.equal(a.run('state.selected'),'T-1');
  assert.equal(a.run('state.draft.values.goal'),'Submitted before deletion');
  assert.equal(a.run('state.savedTask.id'),'T-1');
  assert.equal(a.get('#detail-warning').hidden,false);
  assert.match(a.get('#edit-notice').textContent,/Изменения сохранены.*отсутствует/);
  assert.equal(a.get('#save-task').disabled,true);
  assert.equal(a.run('closeTask()'),true);
});

test('closing an acknowledged but unread save does not lock editing another task', async()=>{
  const a=await app([task('T-1'),task('T-2')]);
  a.run("openTask('T-1');state.tab='edit';detailBody();state.draft.values.goal='Submitted';");
  a.handlers.push(()=>Promise.resolve(response({file:'T-1.yml'})),()=>Promise.reject(new Error('offline')));
  await a.run('saveDraft({preventDefault(){}})');
  assert.equal(a.run('closeTask()'),true);
  a.run("openTask('T-2');state.tab='edit';detailBody();");
  assert.equal(a.get('#edit-goal').disabled,false);
  assert.equal(a.get('#save-task').disabled,false);
});

test('restoring the same YAML version re-enables saving and clears only source notices', async()=>{
  const original=task();
  const a=await app([original]);
  a.run("openTask('T-1');state.tab='edit';detailBody();state.draft.values.goal='Unsaved text';");
  a.handlers.push(()=>Promise.resolve(response(snapshot([]))));
  await a.run('refresh(true)');
  assert.equal(a.get('#save-task').disabled,true);
  assert.equal(a.run('state.draft.notice.kind'),'source');
  a.handlers.push(()=>Promise.resolve(response(snapshot([original]))));
  await a.run('refresh(true)');
  assert.equal(a.get('#save-task').disabled,false);
  assert.equal(a.get('#detail-warning').hidden,true);
  assert.equal(a.get('#edit-notice').hidden,true);
  assert.equal(a.run('state.draft.values.goal'),'Unsaved text');
});

test('restoring a different YAML version replaces deleted-source notice with version conflict', async()=>{
  const a=await app();
  a.run("openTask('T-1');state.tab='edit';detailBody();state.draft.values.goal='Unsaved text';");
  a.handlers.push(()=>Promise.resolve(response(snapshot([]))));
  await a.run('refresh(true)');
  const changed=task('T-1',{goal:'External edit'});changed.version='"v2"';
  a.handlers.push(()=>Promise.resolve(response(snapshot([changed]))));
  await a.run('refresh(true)');
  assert.equal(a.get('#save-task').disabled,false);
  assert.equal(a.get('#detail-warning').hidden,true);
  assert.match(a.get('#edit-notice').textContent,/YAML изменился/);
  assert.equal(a.run('state.draft.notice.kind'),'version');
  assert.equal(a.run('state.draft.version'),'"v1"');
  assert.equal(a.run('state.draft.values.goal'),'Unsaved text');
});

test('source restoration does not erase a write error', async()=>{
  const a=await app();
  a.run("openTask('T-1');state.tab='edit';detailBody();state.draft.values.goal='Unsaved text';");
  a.handlers.push(()=>Promise.resolve(response({error:'Write conflict: reload required'},409)));
  await a.run('saveDraft({preventDefault(){}})');
  a.handlers.push(()=>Promise.resolve(response(snapshot([]))));
  await a.run('refresh(true)');
  a.handlers.push(()=>Promise.resolve(response(snapshot([task()]))));
  await a.run('refresh(true)');
  assert.equal(a.get('#save-task').disabled,false);
  assert.equal(a.get('#edit-notice').hidden,false);
  assert.equal(a.get('#edit-notice').textContent,'Write conflict: reload required');
  assert.equal(a.run('state.draft.notice.kind'),'operation');
});
