'use strict';
const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels = {pending:'В очереди',in_progress:'В работе',completed:'Завершено',deferred:'Отложено',archived:'В архиве'};
const kinds = {feature:'Возможность',defect:'Ошибка',chore:'Улучшение'};
const statusIcons = {pending:'◌',in_progress:'◔',completed:'✓',deferred:'◷',archived:'▣'};
const names = {goal:'Цель',scope:'Границы задачи',in:'Входит в задачу',out:'Вне задачи',context:'Контекст',requirements:'Требования',acceptance_criteria:'Критерии приёмки',tests:'Проверки',failure_outcomes:'Исходы ошибок',cardinality:'Объёмы и ограничения',lifecycle:'Жизненный цикл',specification:'Спецификация',design:'Дизайн',proposal:'Предложение',finding:'Наблюдение',siblings:'Связанные пути',revalidated:'Повторная проверка',review:'Ревью',given:'Дано',when:'Когда',then:'Ожидаемый результат',text:'Описание',operation:'Операция',permanent:'Постоянная ошибка',retryable:'Повторная попытка',ambiguous:'Неопределённый исход',cancelled:'Отмена',timeout:'Тайм-аут',collection:'Коллекция',expected:'Ожидаемый объём',maximum:'Максимум',bound:'Ограничение',entity:'Сущность',delete:'Удаление',cascade:'Каскад',retention:'Хранение',notes:'Заметки',implementation_commit:'Коммит реализации',introduced_by:'Источник изменения',milestone:'Этап',languages:'Языки',covers:'Покрытие',command:'Команда'};
const state = {tasks:[],byId:new Map(),nav:'active',view:'list',page:1,size:40,search:'',status:'',priority:'',kind:'',epic:'',milestone:'',sort:'status',descending:false,epics:new Map(),selected:null,tab:'overview',errors:[]};
let previousFocus, previousTaskId;
const own = (object, key) => Object.hasOwn(object, key);
const lookup = (object, key, fallback) => own(object, key) ? object[key] : fallback;
function statusBadge(s){return `<span class="status ${esc(s)}">${lookup(statusIcons,s,'○')} ${esc(lookup(labels,s,s || 'Не указан'))}</span>`;}
function priority(p){return `<span class="priority ${esc(p)}"><b>${({P0:'▮▮▮',P1:'▮▮▯',P2:'▮▯▯',P3:'▯▯▯'})[p] || '—'}</b>${esc(p || '—')}</span>`;}
function dependencies(t){return Array.isArray(t.depends_on) ? [...new Set(t.depends_on.filter(id=>typeof id==='string'))] : [];}
function unmet(t){return dependencies(t).filter(id=>state.byId.get(id)?.data.status !== 'completed');}
function ready(t){return t.status === 'pending' && unmet(t).length === 0;}
function number(n){return n.toLocaleString('ru-RU');}
function updateTheme(){const dark=document.body.classList.contains('dark');$('#theme span').textContent=dark?'Светлая тема':'Тёмная тема';}
try{document.body.classList.toggle('dark',localStorage.getItem('taskline-theme')==='dark');}catch{}
updateTheme();
$('#theme').onclick=()=>{document.body.classList.toggle('dark');try{localStorage.setItem('taskline-theme',document.body.classList.contains('dark')?'dark':'light');}catch{}updateTheme();};
$('#mobile-theme').onclick=()=>$('#theme').click();
function matchesNav(t){switch(state.nav){case 'active':return !['completed','archived'].includes(t.status);case 'progress':return t.status==='in_progress';case 'ready':return ready(t);case 'completed':return t.status==='completed';default:return true;}}
function filtered(){const query=state.search.toLowerCase().trim();return state.tasks.filter(item=>{const t=item.data;return matchesNav(t)&&(!state.status||t.status===state.status)&&(!state.priority||t.priority===state.priority)&&(!state.kind||t.kind===state.kind)&&(!state.epic||t.epic===state.epic)&&(!state.milestone||t.milestone===state.milestone)&&(!query||item.search.includes(query));}).sort(compareTasks);}
function epicTitle(id){return state.epics.get(id)?.title || id || 'Без направления';}
function compareTasks(a,b){
    const x=a.data,y=b.data,ranks={in_progress:0,pending:1,deferred:2,completed:3,archived:4};
    let result=0;
    if(state.sort==='status')result=lookup(ranks,x.status,5)-lookup(ranks,y.status,5);
    else if(state.sort==='priority')result=String(x.priority||'P9').localeCompare(String(y.priority||'P9'));
    else result=epicTitle(x.epic).localeCompare(epicTitle(y.epic),'ru',{numeric:true});
    return (state.descending?-result:result)||String(x.priority||'P9').localeCompare(String(y.priority||'P9'))||y.id.localeCompare(x.id,undefined,{numeric:true});
}
function epicSummary(){
    const epic=state.epics.get(state.epic),el=$('#epic-summary');
    el.hidden=!state.epic;
    if(!state.epic)return;
    const tasks=state.tasks.filter(i=>i.data.epic===state.epic),done=tasks.filter(i=>i.data.status==='completed').length;
    el.innerHTML=`<div><strong>${esc(epicTitle(state.epic))}</strong><span>${esc(state.epic)} · ${done} / ${tasks.length} завершено</span></div>${epic?.scope?`<p>${esc(epic.scope)}</p>`:''}`;
}

function renderStats(){const all=state.tasks.map(i=>i.data),active=all.filter(t=>!['completed','archived'].includes(t.status)).length,progress=all.filter(t=>t.status==='in_progress').length,done=all.filter(t=>t.status==='completed').length,available=all.filter(ready).length;
const stats=[['Открытые задачи',active,'▤','В очереди, в работе и отложенные','active'],['В работе',progress,'◔','Текущий фокус команды','progress'],['Завершено',done,'✓',`<b>${all.length?Math.round(done/all.length*100):0}%</b> от всех задач проекта`,'completed'],['Готовы к работе',available,'◇','Все зависимости выполнены','ready']];
$('#stats').innerHTML=stats.map(([name,n,icon,sub,nav])=>`<button class="stat" data-nav="${nav}"><span class="stat-top">${name}<span class="stat-symbol">${icon}</span></span><span class="stat-value">${number(n)}</span><span class="stat-sub">${sub}</span></button>`).join('');
for(const [id,n] of [['nav-count',active],['progress-count',progress],['ready-count',available],['done-count',done],['all-count',all.length]])$('#'+id).textContent=number(n);
}
function options(){
    for(const key of ['status','priority','kind','epic','milestone']){
        const el=$('#'+key),first=el.options[0].text;
        const available=new Set(state.tasks.map(i=>i.data[key]).filter(v=>typeof v==='string'));
        // Keep an active filter even if its last matching task was removed or changed.
        const values=[...new Set([...available,...(state[key]?[state[key]]:[])])].sort();
        el.innerHTML=`<option value="">${first}</option>`+values.map(v=>{
            const title=key==='status'?lookup(labels,v,v):key==='kind'?lookup(kinds,v,v):key==='epic'?epicTitle(v):v;
            return `<option value="${esc(v)}">${esc(title)}${available.has(v)?'':' (нет задач)'}</option>`;
        }).join('');
        el.value=state[key];
    }
}

function row(item){const t=item.data,blocked=unmet(t).length;return `<tr data-task="${esc(t.id)}"><td><span class="task-id">${esc(t.id)}</span></td><td><button class="title-button" data-task="${esc(t.id)}">${esc(t.title)}</button><div class="row-sub"><span class="kind-${esc(t.kind)}"><i class="kind-dot"></i>${esc(lookup(kinds,t.kind,t.kind||'Задача'))}</span>${t.languages?.length?`<span>·</span><span>${esc(Array.isArray(t.languages)?t.languages.join(', '):t.languages)}</span>`:''}</div></td><td>${statusBadge(t.status)}</td><td>${priority(t.priority)}</td><td><span class="epic-tag">${esc(epicTitle(t.epic))}</span></td><td><span class="deps ${blocked?'blocked':ready(t)?'ready':''}" title="${blocked?'Незавершённые зависимости':'Зависимости завершены'}">${blocked?'◇ '+blocked:dependencies(t).length?'✓ '+dependencies(t).length:'—'}</span></td></tr>`;}
function card(item){const t=item.data;return `<button class="board-card" data-task="${esc(t.id)}"><span class="task-id">${esc(t.id)}</span><strong>${esc(t.title)}</strong><footer>${priority(t.priority)}<span class="epic-tag">${esc(epicTitle(t.epic))}</span></footer></button>`;}
function render(){renderStats();epicSummary();const list=filtered(),pages=Math.max(1,Math.ceil(list.length/state.size));state.page=Math.min(state.page,pages);const titles={active:'Все задачи',progress:'В работе',ready:'Готовы к работе',completed:'Завершённые',all:'Весь архив'};$('#page-title').innerHTML=esc(titles[state.nav])+'<span class="heading-dot">.</span>';$('#page-subtitle').textContent=state.nav==='ready'?'Задачи без незавершённых зависимостей. Можно начинать.':state.nav==='all'?'Вся история проекта, включая завершённые и архивные задачи.':'От общей картины к конкретным шагам.';document.querySelectorAll('#navigation .nav').forEach(b=>b.classList.toggle('active',b.dataset.nav===state.nav));$('#result-count').textContent=`Найдено: ${number(list.length)}`;
if(!list.length){$('#task-content').innerHTML='<div class="empty"><strong>Задач не найдено</strong>Попробуйте другой запрос или сбросьте фильтры.</div>';}
else if(state.view==='list'){$('#task-content').innerHTML=`<div class="table-wrap"><table><thead><tr><th>ID задачи</th><th>Название</th><th>Статус</th><th>Приоритет</th><th>Направление</th><th>Зависимости</th></tr></thead><tbody>${list.slice((state.page-1)*state.size,state.page*state.size).map(row).join('')}</tbody></table></div>`;}
else{$('#task-content').innerHTML=`<div class="board">${Object.entries(labels).map(([s,label])=>{const group=list.filter(i=>i.data.status===s);return `<section><div class="column-heading"><span>${statusIcons[s]} ${label}</span><span>${group.length}</span></div>${group.slice((state.page-1)*state.size,state.page*state.size).map(card).join('')}${group.length>state.page*state.size?'<p class="board-more">Продолжение на следующей странице →</p>':''}</section>`;}).join('')}${list.some(i=>!own(labels,i.data.status))?`<section><div class="column-heading">Другие статусы</div>${list.filter(i=>!own(labels,i.data.status)).slice((state.page-1)*state.size,state.page*state.size).map(card).join('')}</section>`:''}</div>`;}
const boardPages=Math.max(1,...[...Object.keys(labels),'other'].map(s=>Math.ceil(list.filter(i=>s==='other'?!own(labels,i.data.status):i.data.status===s).length/state.size)));const actualPages=state.view==='board'?boardPages:pages;if(state.page>actualPages){state.page=actualPages;render();return;}
$('#range').textContent=state.view==='list'?`${list.length?(state.page-1)*state.size+1:0}–${Math.min(state.page*state.size,list.length)} из ${number(list.length)} задач`:`До ${state.size} задач на колонку · ${number(list.length)} всего`;$('#page-number').textContent=`${state.page} / ${actualPages}`;$('#previous').disabled=state.page===1;$('#next').disabled=state.page>=actualPages;document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('selected',b.dataset.view===state.view));}
function reset(){for(const key of ['search','status','priority','kind','epic','milestone']){state[key]='';$('#'+key).value='';}state.page=1;render();}
function valueHTML(v){if(v===null||v===undefined)return '<span class="field-name">—</span>';if(Array.isArray(v))return `<ul>${v.map(i=>`<li>${valueHTML(i)}</li>`).join('')}</ul>`;if(typeof v==='object')return Object.entries(v).map(([k,x])=>`<div><span class="field-name">${esc(names[k]||k)}</span>${typeof x==='object'?valueHTML(x):`<span>${esc(x)}</span>`}</div>`).join('');return esc(v);}
function section(name,v){if(v===undefined||v===null)return '';return `<h3>${esc(name)}</h3>${typeof v==='string'?`<p>${esc(v)}</p>`:valueHTML(v)}`;}
function requirements(items){if(!Array.isArray(items))return valueHTML(items);return items.map(i=>`<div class="requirement">${typeof i==='object'&&i!==null?`<strong>${esc(i.id||'')}</strong>${Object.entries(i).filter(([k])=>k!=='id').map(([k,v])=>`<p>${k==='text'?'':`<span class="field-name">${esc(names[k]||k)}</span>`}${valueHTML(v)}</p>`).join('')}`:esc(i)}</div>`).join('');}

function hasUnsaved(){return Boolean(state.draft && JSON.stringify(state.draft.values)!==JSON.stringify(state.draft.original));}
function discardAllowed(){return !state.saving && !state.reloading && (!hasUnsaved()||confirm('Закрыть несохранённый черновик?'));}
function draftFor(item){
    if(!state.draft || state.draft.id!==item.data.id){
        const original={title:item.data.title,goal:String(item.data.goal||''),status:item.data.status||'pending'};
        state.draft={id:item.data.id,version:item.version,original,values:{...original}};
    }
    return state.draft;
}
function editorNotice(message,kind='operation'){
    if(state.draft)state.draft.notice={message,kind};
    const notice=$('#edit-notice');
    if(notice){notice.hidden=false;notice.textContent=message;}
}
function setEditorBusy(busy){
    const form=$('#edit-form');
    if(!form)return;
    for(const control of form.elements)control.disabled=busy || state.savedTask?.id===state.selected;
    if(!busy && $('#reload-draft'))$('#reload-draft').disabled=false;
    if(!busy && $('#save-task'))$('#save-task').disabled=Boolean(state.savedTask?.id===state.selected || !state.byId.has(state.selected));
}
function syncEditorSource(){
    const item=state.byId.get(state.selected),draft=state.draft;
    if(!draft)return;
    const kind=draft.notice?.kind;
    if(state.savedTask?.id===state.selected){
        editorNotice(item?'Изменения сохранены. Ожидаем обновлённый файл.':'Изменения сохранены, но исходная задача теперь отсутствует или повреждена. Показан отправленный текст.','saved');
    }else if(kind!=='operation' && kind!=='saved'){
        if(!item)editorNotice('Задача удалена или YAML повреждён. Черновик сохранён.','source');
        else if(item.version!==draft.version)editorNotice('YAML изменился. Черновик сохранён; перед записью нужно перечитать задачу.','version');
        else if(kind==='source' || kind==='version'){
            delete draft.notice;
            const notice=$('#edit-notice');
            if(notice){notice.hidden=true;notice.textContent='';}
        }
    }
    setEditorBusy(Boolean(state.saving || state.reloading));
}
async function refreshFresh(){
    // A read already in flight may predate the write or reload request.
    if(currentRefresh)await currentRefresh;
    revision='';
    return refresh();
}
async function reloadDraft(){
    if(!discardAllowed())return;
    const id=state.selected;
    state.reloading=true;
    setEditorBusy(true);
    try{
        const updated=await refreshFresh();
        if(!updated)return; // Retain the draft until a fresh read succeeds.
        const item=state.byId.get(id);
        if(!item){editorNotice('Задача удалена или YAML повреждён. Черновик сохранён.','source');return;}
        if(state.selected===id){state.draft=null;renderEditor(item);}
    }finally{state.reloading=false;setEditorBusy(false);}
}
async function saveDraft(event){
    event.preventDefault();
    if(state.saving || state.reloading || state.savedTask?.id===state.selected || !state.draft)return;
    const draft=state.draft,submitted={...draft.values};
    state.saving=true;
    // Prevent edits being accepted by the form after the submitted snapshot was taken.
    setEditorBusy(true);
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
    try{
        const response=await fetch('/api/tasks/'+encodeURIComponent(draft.id),{
            method:'PUT',headers:{'Content-Type':'application/json','If-Match':draft.version},
            body:JSON.stringify(submitted),signal:controller.signal
        });
        const result=await response.json();
        if(!response.ok)throw new Error(result.error||'Не удалось сохранить');
        draft.original={...submitted};
        state.savedTask={id:draft.id,afterRead:readSequence};
        const updated=await refreshFresh();
        if(!updated)editorNotice('Изменения сохранены, но обновлённый файл пока не прочитан. Подключение восстановится автоматически.','saved');
    }catch(error){
        editorNotice(error.name==='AbortError'
            ?'Ответ не получен. Запись могла сохраниться; перечитайте файл перед повторной попыткой.'
            :error.message);
    }finally{clearTimeout(timer);state.saving=false;setEditorBusy(false);}
}
function renderEditor(item){
    const draft=draftFor(item),v=draft.values;
    $('#detail-body').innerHTML=`<form id="edit-form"><div id="edit-notice" role="status" class="edit-notice" hidden></div><label class="edit-label" for="edit-title">Название</label><input class="edit-input" id="edit-title" name="title" required maxlength="500" value="${esc(v.title)}"><label class="edit-label" for="edit-status">Статус</label><select class="edit-input" id="edit-status" name="status">${Object.entries(labels).map(([id,label])=>`<option value="${id}" ${v.status===id?'selected':''}>${label}</option>`).join('')}</select><label class="edit-label" for="edit-goal">Описание / цель</label><textarea class="edit-input" id="edit-goal" name="goal" rows="12" maxlength="64000">${esc(v.goal)}</textarea><p class="edit-help">Изменения записываются в YAML. Завершённые и архивные задачи перемещаются в done/.</p><div class="edit-actions"><button class="button primary" type="submit" id="save-task">Сохранить изменения</button><button class="button" type="button" id="reload-draft">Перечитать файл</button><span id="save-result" role="status"></span></div></form>`;
    $('#edit-form').oninput=e=>{if(own(draft.values,e.target.name))draft.values[e.target.name]=e.target.value;};
    $('#edit-form').onchange=e=>{if(own(draft.values,e.target.name))draft.values[e.target.name]=e.target.value;};
    $('#reload-draft').onclick=reloadDraft;
    $('#edit-form').onsubmit=saveDraft;
    if(draft.notice)editorNotice(draft.notice.message,draft.notice.kind);
    syncEditorSource();
}
function relationCard(id,current=false){
    const t=state.byId.get(id)?.data;
    return `<button class="relation-card ${current?'current':''}" data-task="${esc(id)}" ${t?'':'disabled'}><span class="task-id">${esc(id)}</span><strong>${esc(t?.title||'Задача не найдена')}</strong>${t?statusBadge(t.status):'<span class="missing-task">Зависимость отсутствует</span>'}</button>`;
}
function renderRelations(item){
    const t=item.data,upstream=dependencies(t),downstream=state.tasks.filter(i=>dependencies(i.data).includes(t.id));
    const blocked=unmet(t).length;
    $('#detail-body').innerHTML=`<div class="relation-intro"><strong>${blocked?'Незавершённых зависимостей: '+blocked:'Все зависимости выполнены'}</strong><p>Стрелки показывают порядок работы. Нажмите на задачу, чтобы посмотреть её связи.</p></div><div class="relations"><section><h3>Зависит от · ${upstream.length}</h3>${upstream.slice(0,100).map(id=>relationCard(id)).join('')||'<p class="edit-help">Нет зависимостей</p>'}${upstream.length>100?'<p class="edit-help">Показаны первые 100 зависимостей. Полный список — во вкладке YAML.</p>':''}</section><span class="relation-arrow" aria-hidden="true">→</span><section><h3>Эта задача</h3>${relationCard(t.id,true)}</section><span class="relation-arrow" aria-hidden="true">→</span><section><h3>Разблокирует · ${downstream.length}</h3>${downstream.slice(0,100).map(i=>relationCard(i.data.id)).join('')||'<p class="edit-help">Нет зависимых задач</p>'}${downstream.length>100?'<p class="edit-help">Показаны первые 100. Остальные можно найти по ID этой задачи через поиск.</p>':''}</section></div>`;
}
function detailBody(){const item=state.byId.get(state.selected)||state.selectedItem;if(!item)return;const t=item.data;
if(state.tab==='edit'){renderEditor(item);return;}
if(state.tab==='relations'){renderRelations(item);return;}
if(state.tab==='yaml'){$('#detail-body').innerHTML=`<button class="copy-button" id="copy">Копировать YAML</button><h3>Исходный файл</h3><pre><code>${esc(item.raw)}</code></pre>`;$('#copy').onclick=async e=>{try{await navigator.clipboard.writeText(item.raw);e.target.textContent='Скопировано';}catch{e.target.textContent='Выделите текст для копирования';}};return;}
if(state.tab==='checks'){$('#detail-body').innerHTML=`<h3>Требования</h3>${requirements(t.requirements||[])}<h3>Критерии приёмки</h3>${requirements(t.acceptance_criteria||[])}<h3>Проверки</h3>${Array.isArray(t.tests)?t.tests.map(x=>`<div class="requirement"><pre><code>${esc(typeof x==='string'?x:x?.command||'Команда не указана')}</code></pre>${Array.isArray(x?.covers)?`<p><span class="field-name">Покрытие</span>${esc(x.covers.join(', '))}</p>`:''}</div>`).join(''):valueHTML(t.tests||'Не указаны')}`;return;}
const deps=dependencies(t);const omitted=new Set(['id','title','status','priority','epic','kind','goal','scope','context','depends_on','requirements','acceptance_criteria','tests','schema_version']);$('#detail-body').innerHTML=section('Цель',t.goal)+`<button class="button" data-tab="relations">◇ Показать связи · ${deps.length} входящих / ${state.tasks.filter(i=>dependencies(i.data).includes(t.id)).length} исходящих</button>`+(deps.length?`<h3>Зависимости · ${deps.length}</h3>${deps.slice(0,100).map(id=>{const d=state.byId.get(id)?.data;return `<button class="dependency" data-task="${esc(id)}"><span class="task-id">${esc(id)}</span><span>${esc(d?.title||'Задача отсутствует в источнике')}</span>${d?statusBadge(d.status):'?'}</button>`;}).join('')}${deps.length>100?'<p class="edit-help">Показаны первые 100 зависимостей. Полный список — во вкладке YAML.</p>':''}`:'<h3>Зависимости</h3><p>Нет зависимостей</p>')+section('Границы задачи',t.scope)+section('Контекст',t.context)+Object.entries(t).filter(([k])=>!omitted.has(k)).map(([k,v])=>section(names[k]||k,v)).join('')+`<div class="detail-path">Файл: ${esc(item.file)}</div>`;}
function updateDetailWarning(){
    const warning=$('#detail-warning');
    if(!warning)return;
    const item=state.byId.get(state.selected);
    const missing=!item;
    warning.hidden=!missing;
    warning.textContent=missing?'Исходная задача удалена или YAML повреждён. Показана последняя прочитанная версия; черновик сохранён.':'';
}
function openTask(id,preserve=false){
    const item=state.byId.get(id);
    if(!item)return;
    if(!preserve && state.selected!==id && !discardAllowed())return;
    if(!preserve && state.selected!==id){state.draft=null;state.savedTask=null;}
    state.selectedItem=item;
    if(preserve && state.tab==='edit'){
        updateDetailWarning();
        syncEditorSource();
        return;
    }
    const scroll=preserve?$('#detail-body')?.scrollTop||0:0;
    const active=preserve?document.activeElement:null;
    const focusId=active?.id,focusTab=active?.dataset?.tab,focusTask=active?.dataset?.task;
    if(!preserve && !state.selected){previousFocus=document.activeElement;previousTaskId=previousFocus?.dataset?.task;}
    state.selected=id;
    if(!preserve)state.tab='overview';
    const t=item.data;
    $('#drawer').innerHTML=`<div class="drawer-header"><div><span class="task-id">${esc(id)}</span>${statusBadge(t.status)}</div><button id="close" aria-label="Закрыть задачу">×</button></div><div class="detail-head"><h2 id="detail-title">${esc(t.title)}</h2><div class="detail-meta">${priority(t.priority)}<span class="epic-tag">${esc(epicTitle(t.epic))}</span><span class="field-name">${esc(lookup(kinds,t.kind,t.kind||'Задача'))}</span></div><div id="detail-warning" class="edit-notice" role="status" hidden></div><div class="tabs" role="group" aria-label="Разделы карточки"><button data-tab="overview" class="selected">Обзор</button><button data-tab="checks">Требования и проверки</button><button data-tab="relations">Связи</button><button data-tab="yaml">YAML</button><button data-tab="edit">Изменить</button></div></div><div id="detail-body" class="detail-body"></div>`;
    $('#drawer').hidden=false;$('#overlay').hidden=false;
    document.body.style.overflow='hidden';$('.layout').inert=true;
    document.querySelectorAll('.tabs [data-tab]').forEach(b=>b.classList.toggle('selected',b.dataset.tab===state.tab));
    detailBody();updateDetailWarning();$('#detail-body').scrollTop=scroll;$('#close').onclick=closeTask;
    if(!preserve)$('#close').focus();
    else if(focusTab)$(`.tabs [data-tab="${focusTab}"]`)?.focus({preventScroll:true});
    else if(focusId)document.getElementById(focusId)?.focus({preventScroll:true});
    else if(focusTask)[...$('#drawer').querySelectorAll('[data-task]')].find(b=>b.dataset.task===focusTask)?.focus({preventScroll:true});
    history.replaceState(null,'','#'+encodeURIComponent(id));
}
function closeTask(){
    if(!discardAllowed())return false;
    state.draft=null;state.savedTask=null;state.selected=null;state.selectedItem=null;
    $('#drawer').hidden=true;$('#overlay').hidden=true;
    document.body.style.overflow='';$('.layout').inert=false;
    history.replaceState(null,'',location.pathname);
    if(previousFocus?.isConnected)previousFocus.focus();
    else ([...document.querySelectorAll('.title-button,.board-card')].find(b=>b.dataset.task===previousTaskId)||$('#search')).focus({preventScroll:true});
    return true;
}
let refreshTimer, inFlight=false, revision='', retryDelay=2000, currentRefresh=null, readSequence=0;
function refresh(automatic=false){if(currentRefresh)return currentRefresh;currentRefresh=refreshNow(automatic).finally(()=>{currentRefresh=null;});return currentRefresh;}
function scheduleRefresh(){clearTimeout(refreshTimer);if(!document.hidden)refreshTimer=setTimeout(()=>refresh(true),retryDelay);}
async function refreshNow(automatic=false){
    if(inFlight)return;
    clearTimeout(refreshTimer);inFlight=true;
    const readNumber=++readSequence;
    const button=$('#refresh');button.disabled=true;
    if(!automatic)$('#last-sync').textContent='Читаем файлы…';
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),10000);
    try{
        const response=await fetch('/api/tasks',{headers:revision?{'If-None-Match':revision}:{},signal:controller.signal,cache:'no-cache'});
        if(response.status===304){retryDelay=2000;$('#last-sync').textContent='Автообновление включено';return true;}
        const data=await response.json();
        if(!response.ok)throw new Error(data.error||'Не удалось прочитать задачи');
        revision=response.headers.get('ETag')||'';
        retryDelay=2000;
        state.tasks=data.tasks.map(i=>({...i,search:JSON.stringify(i.data).toLowerCase()}));
        state.byId=new Map(state.tasks.map(i=>[i.data.id,i]));
        state.epics=new Map((data.epics||[]).map(e=>[e.id,e]));
        state.errors=data.errors;
        $('#project').textContent=data.project;$('#breadcrumb').textContent=data.project;
        $('#source-path').textContent=data.source;$('#total-files').textContent=number(data.tasks.length)+' YAML-файлов';
        $('#error').hidden=!data.errors.length;
        $('#error').innerHTML=data.errors.length?`Не удалось прочитать ${data.errors.length} файлов. Остальные задачи показаны.<details><summary>Подробности</summary><div class="error-list">${data.errors.map(e=>`<div>${esc(e.file)}: ${esc(e.message)}</div>`).join('')}</div></details>`:'';
        $('#last-sync').textContent='Обновлено в '+new Date().toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
        const activeTask=document.activeElement?.closest('[data-task]')?.dataset.task;
        options();render();
        if(state.savedTask && readNumber>state.savedTask.afterRead && state.byId.has(state.savedTask.id)){
            if(state.selected===state.savedTask.id){state.draft=null;state.tab='overview';}
            state.savedTask=null;
        }
        if(state.selected){const id=state.selected;if(state.byId.has(id))openTask(id,true);else if(state.draft){updateDetailWarning();if(state.tab==='edit')syncEditorSource();}else closeTask();}
        else if(location.hash){let id;try{id=decodeURIComponent(location.hash.slice(1));}catch{}if(id)openTask(id);}
        else if(activeTask){[...document.querySelectorAll('.title-button,.board-card')].find(b=>b.dataset.task===activeTask)?.focus({preventScroll:true});}
        return true;
    }catch(error){
        // The last successful snapshot stays visible until the source recovers.
        retryDelay=Math.min(retryDelay*2,32000);revision='';
        $('#error').hidden=false;$('#error').textContent=(error.name==='AbortError'?'Превышено время ожидания':error.message)+'. Повторим автоматически.';
        $('#last-sync').textContent='Нет связи · повторное подключение';
        if(!state.tasks.length)$('#task-content').innerHTML='<div class="empty"><strong>Источник недоступен</strong>Проверьте папку задач. Подключение восстановится автоматически.</div>';
        return false;
    }finally{clearTimeout(timeout);button.disabled=false;inFlight=false;scheduleRefresh();}
}
document.addEventListener('visibilitychange',()=>{clearTimeout(refreshTimer);if(!document.hidden)refresh(true);});
window.addEventListener('online',()=>refresh(true));
window.addEventListener('beforeunload',event=>{
    if(hasUnsaved() || state.saving || state.reloading){event.preventDefault();event.returnValue='';}
});
window.addEventListener('hashchange',()=>{
    let id;
    try{id=decodeURIComponent(location.hash.slice(1));}catch{return;}
    if(id===state.selected)return;
    if(id && state.byId.has(id))openTask(id);
    else if(!id)closeTask();
    if(state.selected)history.replaceState(null,'','#'+encodeURIComponent(state.selected));
});
document.addEventListener('click',event=>{const task=event.target.closest('[data-task]');if(task){openTask(task.dataset.task);return;}const nav=event.target.closest('[data-nav]');if(nav){state.nav=nav.dataset.nav;reset();return;}const view=event.target.closest('[data-view]');if(view){state.view=view.dataset.view;state.page=1;render();return;}const tab=event.target.closest('[data-tab]');if(tab){if(state.saving||state.reloading)return;state.tab=tab.dataset.tab;document.querySelectorAll('.tabs [data-tab]').forEach(b=>b.classList.toggle('selected',b.dataset.tab===state.tab));detailBody();}});
for(const key of ['status','priority','kind','epic','milestone'])$('#'+key).onchange=e=>{state[key]=e.target.value;state.page=1;render();};$('#sort').onchange=e=>{state.sort=e.target.value;state.page=1;render();};$('#sort-direction').onclick=()=>{state.descending=!state.descending;$('#sort-direction').textContent=state.descending?'↓':'↑';$('#sort-direction').setAttribute('aria-pressed',String(state.descending));state.page=1;render();};let searchTimer;$('#search').oninput=e=>{clearTimeout(searchTimer);state.search=e.target.value;searchTimer=setTimeout(()=>{state.page=1;render();},120);};$('#reset').onclick=reset;$('#refresh').onclick=()=>refresh();$('#previous').onclick=()=>{state.page--;render();};$('#next').onclick=()=>{state.page++;render();};$('#overlay').onclick=closeTask;
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&state.selected)closeTask();if(event.key==='/'&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)&&!state.selected){event.preventDefault();$('#search').focus();}if(event.key==='Tab'&&state.selected){const controls=[...$('#drawer').querySelectorAll('button,input,textarea,select,a[href],[tabindex]')].filter(e=>!e.disabled&&e.tabIndex>=0&&e.getClientRects().length);const first=controls[0],last=controls.at(-1);if(!controls.length){event.preventDefault();return;}if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}});
refresh();
