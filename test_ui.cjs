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
const snapshot = tasks => ({tasks, epics: [{id:'quality',title:'Quality'}], errors:[], source:'/tmp/tasks', project:'Test',creation:{suggested_id:'T-003',epics:[{id:'quality',title:'Quality',scope:'Testing and reliability'}],max_dependencies:100,id_pattern:'[A-Z][A-Z0-9]*-[0-9]{3,}'}});
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
        get elements(){return (selector==='#create-form'?['#create-id','#create-title','#create-goal','#create-epic','#create-priority','#create-kind','#create-criteria','#create-tests','#create-dependency-search','#create-dependency-more','#create-submit','#create-recheck']:['#edit-title','#edit-status','#edit-goal','#save-task','#reload-draft']).map(get);}
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
    navigator:{clipboard:{writeText:async()=>{}}}, confirm:()=>true, AbortController, TextEncoder,
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

function fillCreation(a,values={}) {
  a.run('openCreate()');
  for(const [name,value] of Object.entries({title:'New work',goal:'Expected outcome',epic:'quality',...values})){
    a.get('#create-form').oninput({target:{name,value}});
  }
}
const createdTask = (payload,extra={}) => task(payload.id,{...payload,status:'pending',
  acceptance_criteria:payload.acceptance_criteria.map((text,i)=>({id:'A'+(i+1),text})),
  tests:payload.tests.map(command=>({command,covers:payload.acceptance_criteria.map((_,i)=>'A'+(i+1))})),...extra});

test('creation submits chosen fields and checks once, locks inputs, opens the saved task',async()=>{
  const a=await app([task('T-001'),task('T-002',{status:'completed'})]);
  fillCreation(a,{id:'T-010',title:'  New work  ',goal:'Description',priority:'P1',kind:'defect',criteria:'First criterion\n\n Second criterion ',tests:'npm test\n npm run lint'});
  a.run("toggleCreateDependency('T-002',true)");
  const post=deferred();let payload;
  a.handlers.push((url,options)=>{assert.equal(url,'/api/tasks');assert.equal(options.method,'POST');payload=JSON.parse(options.body);return post.promise;},()=>Promise.resolve(response(snapshot([createdTask(payload)]))));
  const saving=a.run('createTask({preventDefault(){}})');
  await a.run('createTask({preventDefault(){}})');
  assert.equal(a.requests.filter(r=>r.options.method==='POST').length,1);
  assert.equal(a.get('#create-title').disabled,true);
  assert.deepEqual(payload,{id:'T-010',title:'New work',goal:'Description',epic:'quality',priority:'P1',kind:'defect',depends_on:['T-002'],acceptance_criteria:['First criterion','Second criterion'],tests:['npm test','npm run lint']});
  post.resolve(response({id:'T-010',file:'T-010.yml'},201));
  await saving;
  assert.equal(a.run('state.selected'),'T-010');
  assert.equal(a.run('state.creating'),false);
  assert.equal(a.run('state.createDraft'),null);
  assert.equal(a.get('#drawer').hidden,false);
});

test('dependency picker reaches later pages and preserves selected IDs through searches and refreshes',async()=>{
  const tasks=Array.from({length:132},(_,i)=>task('T-'+String(i+1).padStart(3,'0'),{status:i%2?'pending':'completed'}));
  const a=await app(tasks);fillCreation(a,{id:'T-200'});
  assert.equal((a.get('#create-dependency-results').innerHTML.match(/type="checkbox"/g)||[]).length,50);
  assert.doesNotMatch(a.get('#create-dependency-results').innerHTML,/T-120/);
  a.get('#create-dependency-more').onclick();a.get('#create-dependency-more').onclick();
  assert.match(a.get('#create-dependency-results').innerHTML,/T-120/);
  a.run("toggleCreateDependency('T-120',true)");
  a.get('#create-dependency-search').oninput({target:{value:'T-001'}});
  assert.match(a.get('#create-selected').innerHTML,/T-120/);
  a.handlers.push(()=>Promise.resolve(response({...snapshot(tasks),creation:{...snapshot(tasks).creation,suggested_id:'T-999'}})));
  await a.run('refresh(true)');
  assert.equal(a.run('state.createDraft.values.id'),'T-200');
  assert.equal(a.run('state.createDraft.query'),'T-001');
  assert.deepEqual(JSON.parse(a.run('JSON.stringify(state.createDraft.values.depends_on)')),['T-120']);
  assert.match(a.get('#create-selected').innerHTML,/T-120/);
  a.get('#create-dependency-search').oninput({target:{value:'T-120'}});
  assert.match(a.get('#create-dependency-results').innerHTML,/T-120" checked/);
});

test('dependency selections stop at 100 and missing selections are retained and rejected',async()=>{
  const tasks=Array.from({length:110},(_,i)=>task('T-'+String(i+1).padStart(3,'0')));
  const a=await app(tasks);fillCreation(a,{id:'T-200'});
  a.run("for(const item of state.tasks)toggleCreateDependency(item.data.id,true)");
  assert.equal(a.run('state.createDraft.values.depends_on.length'),100);
  a.handlers.push(()=>Promise.resolve(response(snapshot(tasks.slice(1)))));
  await a.run('refresh(true)');
  assert.equal(a.run('state.createDraft.values.depends_on[0]'),'T-001');
  assert.match(a.get('#create-selected').innerHTML,/T-001 · отсутствует/);
  await a.run('createTask({preventDefault(){}})');
  assert.match(a.get('#create-notice').textContent,/зависимостей больше не найдена/);
  assert.equal(a.requests.filter(r=>r.options.method==='POST').length,0);
});

test('empty epic catalog and one-sided checks block creation without inventing fields',async()=>{
  const a=await app();fillCreation(a);
  a.run('state.creation.epics=[];renderCreateEpics();setCreateBusy()');
  assert.equal(a.get('#create-submit').disabled,true);
  assert.match(a.get('#create-epic-help').textContent,/нет эпиков/);
  await a.run('createTask({preventDefault(){}})');
  assert.equal(a.requests.filter(r=>r.options.method==='POST').length,0);
  a.run("state.creation.epics=[{id:'quality',title:'Quality'}];state.createDraft.values.criteria='Criterion';");
  await a.run('createTask({preventDefault(){}})');
  assert.match(a.get('#create-notice').textContent,/и критерии, и команды/);
  a.run("state.createDraft.values.criteria='';");
  assert.equal(a.run('creationProblem()'),'');
  assert.deepEqual(JSON.parse(a.run('JSON.stringify(createPayload().tests)')),[]);
});

test('creation draft guards close and browser unload, then survives normal polling',async()=>{
  const a=await app();fillCreation(a,{title:'Keep title',goal:'Keep description'});
  a.context.confirm=()=>false;
  assert.equal(a.run('closeTask()'),false);
  const event={prevented:false,preventDefault(){this.prevented=true;}};
  a.windowListeners.beforeunload(event);assert.equal(event.prevented,true);
  await a.run('refresh(true)');
  assert.equal(a.run('state.creating'),true);
  assert.equal(a.run('state.createDraft.values.goal'),'Keep description');
  assert.equal(a.run('state.createDraft.values.title'),'Keep title');
  a.context.confirm=()=>true;
  assert.equal(a.run('closeTask()'),true);
  assert.equal(a.run('state.createDraft'),null);
});

test('definite creation conflict keeps editable draft and allows correcting its ID',async()=>{
  const a=await app();fillCreation(a);
  a.handlers.push(()=>Promise.resolve(response({error:'ID already exists'},409)));
  await a.run('createTask({preventDefault(){}})');
  assert.equal(a.run('state.creatingBusy'),false);
  assert.equal(a.get('#create-id').disabled,false);
  assert.equal(a.run('state.createDraft.values.id'),'T-003');
  assert.match(a.get('#create-notice').textContent,/ID already exists/);
  a.get('#create-form').oninput({target:{name:'id',value:'T-004'}});
  assert.equal(a.run('state.createDraft.values.id'),'T-004');
});

test('acknowledged creation and failed read keep recovery state without a second POST',async()=>{
  const a=await app();fillCreation(a);const payload=JSON.parse(a.run('JSON.stringify(createPayload())'));
  a.handlers.push(()=>Promise.resolve(response({id:payload.id,file:payload.id+'.yml'},201)),()=>Promise.reject(new Error('offline')));
  await a.run('createTask({preventDefault(){}})');
  assert.equal(a.run('state.selected'),null);
  assert.equal(a.run('state.createDraft.pending.acknowledged'),true);
  assert.match(a.get('#create-notice').textContent,/T-003 создана.*пока не прочитан/);
  assert.equal(a.get('#create-submit').disabled,true);
  assert.equal(a.get('#create-id').disabled,true);
  assert.equal(a.get('#create-recheck').disabled,false);
  await a.run('createTask({preventDefault(){}})');
  assert.equal(a.requests.filter(r=>r.options.method==='POST').length,1);
  a.handlers.push(()=>Promise.resolve(response(snapshot([createdTask(payload)]))));
  await a.run('recheckCreation()');
  assert.equal(a.run('state.selected'),'T-003');
  assert.equal(a.run('state.createDraft'),null);
});

test('a pre-write read cannot acknowledge creation when the required fresh read fails',async()=>{
  const a=await app();fillCreation(a);const payload=JSON.parse(a.run('JSON.stringify(createPayload())'));
  const stale=deferred(),post=deferred();
  a.handlers.push(()=>stale.promise,()=>post.promise,()=>Promise.reject(new Error('fresh read failed')));
  const reading=a.run('refresh(true)'),saving=a.run('createTask({preventDefault(){}})');
  post.resolve(response({id:payload.id,file:payload.id+'.yml'},201));
  await Promise.resolve();await Promise.resolve();
  stale.resolve(response(snapshot([createdTask(payload)])));
  await reading;await saving;
  assert.equal(a.run('state.selected'),null);
  assert.equal(a.run('state.creating'),true);
  assert.equal(a.run('state.createDraft.pending.acknowledged'),true);
  assert.match(a.get('#create-notice').textContent,/пока не прочитан/);
});

test('lost POST response reconciles only the exact submitted payload without retrying',async()=>{
  const a=await app();fillCreation(a,{criteria:'Expected result',tests:'npm test'});
  const payload=JSON.parse(a.run('JSON.stringify(createPayload())'));
  a.handlers.push(()=>Promise.reject(new Error('connection reset')),()=>Promise.resolve(response(snapshot([createdTask(payload)]))));
  await a.run('createTask({preventDefault(){}})');
  assert.equal(a.run('state.selected'),payload.id);
  assert.equal(a.requests.filter(r=>r.options.method==='POST').length,1);
});

test('ambiguous creation with a different existing payload never claims success or permits overwrite',async()=>{
  const a=await app();fillCreation(a);const payload=JSON.parse(a.run('JSON.stringify(createPayload())'));
  a.handlers.push(()=>Promise.reject(new Error('connection reset')),()=>Promise.resolve(response(snapshot([createdTask(payload,{title:'Someone else'})]))));
  await a.run('createTask({preventDefault(){}})');
  assert.equal(a.run('state.creating'),true);
  assert.equal(a.run('state.selected'),null);
  assert.equal(a.get('#create-submit').disabled,true);
  assert.match(a.get('#create-notice').textContent,/содержимое отличается/);
  assert.equal(a.run('state.createDraft.values.title'),'New work');
});

test('ambiguous absent task allows only an explicit retry with the identical payload and ID',async()=>{
  const a=await app();fillCreation(a);const payload=JSON.parse(a.run('JSON.stringify(createPayload())'));
  a.handlers.push(()=>Promise.reject(new Error('connection reset')),()=>Promise.resolve(response(snapshot([]))));
  await a.run('createTask({preventDefault(){}})');
  assert.equal(a.requests.filter(r=>r.options.method==='POST').length,1);
  assert.equal(a.run('state.createDraft.pending.canRetry'),true);
  assert.equal(a.get('#create-id').disabled,true);
  assert.match(a.get('#create-submit').textContent,/тем же ID/);
  a.get('#create-form').oninput({target:{name:'id',value:'T-999'}});
  a.handlers.push((url,options)=>{assert.deepEqual(JSON.parse(options.body),payload);return Promise.resolve(response({id:payload.id,file:payload.id+'.yml'},201));},()=>Promise.resolve(response(snapshot([createdTask(payload)]))));
  await a.run('createTask({preventDefault(){}})');
  assert.equal(a.run('state.selected'),payload.id);
  assert.equal(a.requests.filter(r=>r.options.method==='POST').length,2);
});


test('creation requires a nonempty description in both form and request validation',async()=>{
  const a=await app();fillCreation(a,{goal:'   \n  '});
  assert.match(a.get('#drawer').innerHTML,/id="create-goal" name="goal" required/);
  await a.run('createTask({preventDefault(){}})');
  assert.match(a.get('#create-notice').textContent,/Укажите описание или цель/);
  assert.equal(a.requests.filter(r=>r.options.method==='POST').length,0);
  assert.equal(a.get('#create-goal').disabled,false);
  a.get('#create-form').oninput({target:{name:'goal',value:'An expected result'}});
  assert.equal(a.run('creationProblem()'),'');
});

test('creation rejects total ASCII and multibyte payloads over 256 KiB before POST',async()=>{
  for(const line of ['a'.repeat(4000),'界'.repeat(1400)]){
    const a=await app();fillCreation(a,{criteria:Array(70).fill(line).join('\n'),tests:'check'});
    const encoded=a.run('JSON.stringify(createPayload())');
    assert.ok(Buffer.byteLength(encoded,'utf8')>256*1024);
    if(line.startsWith('界'))assert.ok(encoded.length<256*1024,'multibyte request is below the limit measured as characters');
    await a.run('createTask({preventDefault(){}})');
    assert.match(a.get('#create-notice').textContent,/Общий размер данных превышает 256 КиБ/);
    assert.equal(a.requests.filter(r=>r.options.method==='POST').length,0);
    assert.equal(a.get('#create-title').disabled,false);
    assert.equal(a.run('state.createDraft.values.criteria.split("\\n").length'),70);
  }
});

test('editor applies the same encoded payload limit and retains the editable draft',async()=>{
  const a=await app();
  a.run("openTask('T-1');state.tab='edit';detailBody();state.draft.values.goal='\\u0000'.repeat(64000);");
  const encoded=a.run('JSON.stringify(state.draft.values)');
  assert.ok(Buffer.byteLength(encoded,'utf8')>256*1024);
  await a.run('saveDraft({preventDefault(){}})');
  assert.match(a.get('#edit-notice').textContent,/Общий размер данных превышает 256 КиБ/);
  assert.equal(a.requests.filter(r=>r.options.method==='PUT').length,0);
  assert.equal(a.get('#edit-goal').disabled,false);
  assert.equal(a.run('state.draft.values.goal.length'),64000);
  a.run("state.draft.values.goal='Smaller description'");
  assert.equal(a.run('writeSizeProblem(state.draft.values)'),'');
});

test('encoded request size accepts exactly 256 KiB and rejects the next byte',async()=>{
  const a=await app();
  const payload={goal:'a'.repeat(256*1024-Buffer.byteLength(JSON.stringify({goal:''})))};
  assert.equal(Buffer.byteLength(JSON.stringify(payload)),256*1024);
  a.context.sizePayload=payload;
  assert.equal(a.run('writeSizeProblem(sizePayload)'),'');
  payload.goal+='a';
  assert.match(a.run('writeSizeProblem(sizePayload)'),/256 КиБ/);
});

test('epic catalog includes zero-task and uncatalogued directions with truthful mixed-status progress',async()=>{
  const a=await app([
    task('T-1',{status:'completed'}),task('T-2',{status:'archived'}),task('T-3',{status:'pending'}),
    task('T-4',{status:'in_progress'}),task('T-5',{status:'deferred'}),task('T-6',{status:'custom'}),
    task('T-7',{epic:'outside'}),task('T-8',{epic:''})
  ]);
  a.run("state.epics.set('empty',{id:'empty',title:'Empty',scope:'No work yet'});openEpics()");
  assert.equal(a.run('epicGroups().size'),3);
  assert.equal(a.run("epicGroups().get('quality').total"),6);
  assert.equal(a.run("epicGroups().get('quality').counts.get('completed')"),1);
  assert.match(a.get('#epics-content').innerHTML,/1 \/ 6 завершено/);
  assert.match(a.get('#epics-content').innerHTML,/max="100" value="17"/);
  assert.doesNotMatch(a.get('#epics-content').innerHTML,/style=/);
  assert.match(a.get('#epics-content').innerHTML,/0 \/ 0 завершено/);
  assert.match(a.get('#epics-content').innerHTML,/Нет в каталоге/);
  assert.match(a.get('#epics-unassigned').textContent,/Задач без направления: 1/);
  assert.equal(a.get('#tasks-area').hidden,true);
  assert.equal(a.get('#epics-area').hidden,false);
});

test('epic catalog search and pagination reach every result without changing task filters',async()=>{
  const a=await app();
  a.run("state.epics=new Map(Array.from({length:55},(_,i)=>['E'+i,{id:'E'+i,title:'Area '+String(i).padStart(2,'0'),scope:i===54?'Searchable scope':'Scope'}]));state.status='pending';openEpics()");
  assert.equal((a.get('#epics-content').innerHTML.match(/class="epic-card"/g)||[]).length,24);
  assert.equal(a.get('#epics-page').textContent,'1 / 3');
  a.get('#epics-next').onclick();a.get('#epics-next').onclick();
  assert.match(a.get('#epics-content').innerHTML,/Area 54/);
  assert.equal(a.get('#epics-next').disabled,true);
  a.get('#epics-search').oninput({target:{value:'Searchable scope'}});
  assert.equal(a.get('#epics-page').textContent,'1 / 1');
  assert.equal((a.get('#epics-content').innerHTML.match(/class="epic-card"/g)||[]).length,1);
  assert.equal(a.run('state.status'),'pending');
  a.run('reset()');assert.equal(a.run('state.epicSearch'),'');
});

test('epic detail includes completed and archived tasks, retains scoped filter after reset',async()=>{
  const a=await app([task('T-1',{status:'completed'}),task('T-2',{status:'archived'}),task('T-3',{epic:'another'})]);
  a.run("state.epics.set('quality',{id:'quality',title:'Quality',scope:'Full scope',specs:['docs/spec.md']});openEpic('quality')");
  assert.equal(a.run('filtered().length'),2);
  assert.equal(a.get('#epic').disabled,true);
  assert.match(a.get('#epic-summary').innerHTML,/1 \/ 2 завершено/);
  assert.match(a.get('#epic-summary').innerHTML,/docs\/spec.md/);
  a.run("state.status='completed';render()");
  assert.equal(a.run('filtered().length'),1);
  assert.match(a.get('#epic-summary').innerHTML,/1 \/ 2 завершено/);
  a.run('reset()');assert.equal(a.run('state.epic'),'quality');assert.equal(a.run('filtered().length'),2);
  a.run('openEpics()');assert.equal(a.run('state.nav'),'epics');
});

test('epic routes, task drawer and create drawer preserve the underlying epic context',async()=>{
  const a=await app();
  const writes=[];a.context.history.replaceState=(_a,_b,url)=>{writes.push(url);a.context.location.hash=url.startsWith('#')?url:'';};
  a.context.location.hash='#epic/quality';a.windowListeners.hashchange();
  assert.equal(a.run('state.nav'),'epic');assert.equal(a.run('state.epicId'),'quality');
  assert.equal(a.context.document.activeElement,a.get('#page-title'));
  a.run("state.status='pending';state.search='Original';openTask('T-1')");
  assert.equal(writes.at(-1),'#T-1');
  a.run('closeTask()');assert.equal(writes.at(-1),'#epic/quality');
  assert.equal(a.run('state.status'),'pending');assert.equal(a.run('state.search'),'Original');
  a.run('openCreate();closeTask()');assert.equal(writes.at(-1),'#epic/quality');
  a.context.location.hash='#epics';a.windowListeners.hashchange();
  assert.equal(a.run('state.nav'),'epics');
  assert.equal(a.context.document.activeElement,a.get('#page-title'));
  a.context.location.hash='#T-1';a.windowListeners.hashchange();assert.equal(a.run('state.selected'),'T-1');
  a.run('closeTask()');assert.equal(writes.at(-1),'#epics');
});

test('initial epic deep link is applied after the first successful snapshot',async()=>{
  const a=await app();
  a.context.location.hash='#epic/quality';
  await a.run('refresh(true)');assert.equal(a.run('state.nav'),'epic');assert.equal(a.run('state.epicId'),'quality');
  a.run("state.status='pending';state.page=1");
  await a.run('refresh(true)');assert.equal(a.run('state.status'),'pending');
});

test('epic refresh updates metadata and counts, retains missing detail and keeps catalog search',async()=>{
  const a=await app([task('T-1')]);
  a.run("openEpics();state.epicSearch='qual';state.epicPage=1;openEpic('quality');state.status='pending'");
  a.handlers.push(()=>Promise.resolve(response({...snapshot([task('T-1',{status:'completed'})]),epics:[{id:'quality',title:'Renamed',scope:'Changed scope',specs:[]}]})));
  await a.run('refresh(true)');
  assert.match(a.get('#page-title').innerHTML,/Renamed/);assert.match(a.get('#epic-summary').innerHTML,/1 \/ 1 завершено/);
  assert.equal(a.run('state.status'),'pending');assert.equal(a.run('filtered().length'),0);
  a.handlers.push(()=>Promise.resolve(response({...snapshot([]),epics:[]})));
  await a.run('refresh(true)');
  assert.equal(a.run('state.epicId'),'quality');assert.match(a.get('#epic-summary').innerHTML,/Эпик не найден/);
  a.run('openEpics()');assert.equal(a.run('state.epicSearch'),'qual');
});

test('epic navigation cannot discard dirty task or creation drafts without confirmation',async()=>{
  const a=await app();
  a.context.confirm=()=>false;
  a.run("openTask('T-1');draftFor(state.selectedItem).values.goal='Keep edit'");
  assert.equal(a.run("openEpic('quality')"),false);assert.equal(a.run('openEpics()'),false);
  assert.equal(a.run('state.selected'),'T-1');assert.equal(a.run('state.draft.values.goal'),'Keep edit');
  a.context.confirm=()=>true;a.run('closeTask();openCreate();state.createDraft.values.title="Keep create"');a.context.confirm=()=>false;
  assert.equal(a.run("navigateTasks('epics')"),false);assert.equal(a.run('state.createDraft.values.title'),'Keep create');
  a.run('state.creatingBusy=true');a.context.confirm=()=>true;assert.equal(a.run('openEpics()'),false);
});

test('epic display escapes metadata and encoded IDs, and keeps file errors visible outside task area',async()=>{
  const a=await app();
  a.run("state.epics=new Map([['a/ b#',{id:'a/ b#',title:'<img src=x>',scope:'<script>alert(1)</script>',specs:['<unsafe>.md']}]]);openEpics()");
  assert.match(a.get('#epics-content').innerHTML,/&lt;img src=x&gt;/);assert.doesNotMatch(a.get('#epics-content').innerHTML,/<script>/);
  a.run("openEpic('a/ b#')");assert.equal(a.run('baseRoute()'),'#epic/a%2F%20b%23');
  assert.match(a.get('#epic-summary').innerHTML,/&lt;unsafe&gt;.md/);
  a.context.location.hash='#epic/a%2F%20b%23';a.windowListeners.hashchange();assert.equal(a.run('state.epicId'),'a/ b#');
  const html=fs.readFileSync(path.join(__dirname,'public/index.html'),'utf8');
  assert.ok(html.indexOf('id="error"')<html.indexOf('id="epics-area"'));
  assert.match(html,/data-nav="epics".*aria-label="Эпики"/);
});

test('blank epic routes never render the entire task list as an unnamed epic',async()=>{
  const a=await app();
  a.run("openEpic('quality')");
  assert.equal(a.run("openEpic('')"),false);assert.equal(a.run("openEpic('   ')"),false);
  a.context.location.hash='#epic/%20';a.windowListeners.hashchange();
  assert.equal(a.run('state.epicId'),'quality');assert.equal(a.run('state.epic'),'quality');
});

test('changed snapshots restore focused task buttons while on an epic route',async()=>{
  const a=await app();a.run("openEpic('quality')");a.context.location.hash='#epic/quality';
  const button=a.get('task-button');button.dataset.task='T-1';button.focus();
  a.context.document.querySelectorAll=selector=>selector==='.title-button,.board-card'?[button]:[];
  let markup='';Object.defineProperty(a.get('#task-content'),'innerHTML',{get:()=>markup,set:value=>{markup=value;a.context.document.activeElement=a.get('body');}});
  await a.run('refresh(true)');
  assert.equal(a.context.document.activeElement,button);
});

test('epic document disclosure keeps expansion, scroll and keyboard focus on refresh',async()=>{
  const a=await app();a.run("state.epics.set('quality',{id:'quality',title:'Quality',specs:['a.md','b.md']});openEpic('quality')");
  a.get('#epic-specs').open=true;a.get('#epic-specs-list').scrollTop=67;a.get('#epic-specs-toggle').focus();
  a.run('render()');
  assert.match(a.get('#epic-summary').innerHTML,/<details id="epic-specs" open>/);
  assert.equal(a.get('#epic-specs-list').scrollTop,67);
  assert.equal(a.context.document.activeElement,a.get('#epic-specs-toggle'));
});
