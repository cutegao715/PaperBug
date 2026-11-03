/* Live V6 workspace. API data is the source of all research content. */
'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const artwork = window.paperbugArtwork;
const app = {projects:[], detail:null, id:null, page:'home', tab:'result', discovery:'radar', mailFilter:'all', notifications:[], hits:[], opportunities:[], runs:new Map(), draft:null, editing:false, version:0, mode:'auto', config:{}, lifecycles:[], busy:false};
let toastTimer;
function showToast(text){$('toast').textContent=text;$('toast').classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').classList.remove('show'),3500);}
async function api(path,options={}){
  const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...options.headers}});
  if(!response.ok){let message;try{const data=await response.json();message=typeof data.detail==='string'?data.detail:JSON.stringify(data.detail||data);}catch{message=response.statusText;}throw new Error(message||'请求失败');}
  return response.json();
}
const post=(path,body={})=>api(path,{method:'POST',body:JSON.stringify(body)});
const base=id=>'/api/v1/projects/'+encodeURIComponent(id||app.id);
const project=()=>app.projects.find(p=>p.project_id===app.id);
const projectName=p=>p?.name||p?.goal||p?.project_id||'尚未选择研究';
const empty=(text,button='')=>`<div class="empty-state"><b>${esc(text)}</b>${button}</div>`;
const icon=name=>`<svg class="svg-sm"><use href="#${name}-ico"/></svg>`;
const status=value=>({finished:'已完成',error:'执行失败',cancelled:'已停止',running:'研究进行中',interrupted:'连接中断',budget_exhausted:'本轮预算已用完',idle:'尚未开始'}[value]||value||'待开始');
function safeUrl(value){try{const u=new URL(value);return ['http:','https:'].includes(u.protocol)?u.href:null;}catch{return null;}}
function stamp(value){if(!value)return '尚无日期';const d=new Date(value);return Number.isNaN(d.valueOf())?value:d.toLocaleDateString('zh-CN');}
function errorBanner(text){$('connectionError').hidden=!text;$('connectionErrorText').textContent=text||'';}
function guardEdit(){return !app.editing||confirm('当前草稿尚未保存，放弃本次编辑吗？');}
function remember(){try{localStorage.setItem('paperagent:lastProjectId',app.id);}catch{}}

function navigate(page){
  if(page==='workspace'&&!app.id){openCreate();return;}
  if(app.page==='workspace'&&page!=='workspace'&&!guardEdit())return;
  if(page!=='workspace')app.editing=false;
  app.page=page;closeQuick();$('newProjectModal').hidden=true;
  document.querySelectorAll('main .page').forEach(el=>el.classList.toggle('active',el.id===page));
  document.querySelectorAll('.nav-button').forEach(el=>el.classList.toggle('active',el.dataset.nav===page||(page==='workspace'&&el.dataset.nav==='projects')));
  $('floatingBug').classList.toggle('visible',page!=='home');
  window.scrollTo(0,0);
  if(page==='home')renderHome();if(page==='projects')renderProjects();if(page==='discover')renderDiscover();if(page==='inbox')renderInbox();
  history.replaceState(null,'','#'+page+(page==='workspace'?'/'+encodeURIComponent(app.id):''));
}
function projectRow(p,i){return `<button class="project-row" data-project="${esc(p.project_id)}"><img class="project-thumb" src="${artwork.thumbs[i%artwork.thumbs.length]}" alt=""/><span class="project-desc"><span class="project-name">${esc(projectName(p))}</span><span class="project-sub">${p.references||0} 篇文献 · ${p.drafts||0} 份草稿</span></span><span class="project-last"><span class="status-dot"></span>${esc(stamp(p.created_at))}</span>${icon('chevron')}</button>`;}
function renderHome(){
  $('homeProjectList').innerHTML=app.projects.length?app.projects.slice(0,3).map(projectRow).join(''):empty('从第一个研究问题开始','<button class="button-light" data-create>新建研究</button>');
  $('homeUpdateList').innerHTML=app.notifications.slice().reverse().slice(0,3).map(n=>`<button class="update-row" data-mail="${esc(n.notification_id)}"><span class="update-icon">${icon('bell')}</span><span class="update-copy"><span class="update-name">${esc(n.title)}</span><span class="update-caption">${esc(n.body||'查看详情')}</span></span><span class="update-action">查看</span></button>`).join('')||empty('暂时没有重要更新');
  $('homeSuggestionText').textContent=nextStep();
  $('mailCount').hidden=!app.notifications.some(n=>!n.read);
  $('floatingBug').classList.toggle('has-mail',app.notifications.some(n=>!n.read));
  $('quickHint').textContent=nextStep();$('quickRecent').textContent=projectName(project());
}
function nextStep(){const d=app.detail;if(!app.projects.length)return '从一个想法开始，创建你的第一个研究。';if(!d)return '选择一个研究，继续上次的工作。';if(d.conversation?.can_resume)return '上次研究尚未完成，可以继续推进。';if(!d.materials?.length&&!d.references?.length)return '先导入一份材料，或和虫虫明确研究问题。';if(!d.drafts?.length)return '基于已有材料，梳理问题并形成第一版草稿。';return '继续完善已有草稿，核对论点与参考文献。';}
function renderProjects(){
  $('projectsList').innerHTML=app.projects.map((p,i)=>`<div class="research-row"><img src="${artwork.thumbs[i%artwork.thumbs.length]}" alt=""/><div class="copy"><b>${esc(projectName(p))}</b><p>${p.references||0} 篇文献 · ${p.drafts||0} 份草稿 · 创建于 ${esc(stamp(p.created_at))}</p></div><button class="button-light" data-project="${esc(p.project_id)}">打开研究 ${icon('arrow')}</button></div>`).join('')||empty('还没有研究项目');
}
async function loadProjects(){const data=await api('/api/v1/projects');app.projects=data.projects||[];if(!app.projects.some(p=>p.project_id===app.id))app.id=app.projects[0]?.project_id||null;renderHome();renderProjects();}
async function loadUpdates(){
  const results=await Promise.allSettled([api('/api/v1/notifications?limit=100'),api('/api/v1/radar/hits'),api('/api/v1/opportunities?limit=100')]);
  if(results[0].status==='fulfilled')app.notifications=results[0].value.notifications||[];
  if(results[1].status==='fulfilled')app.hits=results[1].value.hits||[];
  if(results[2].status==='fulfilled')app.opportunities=results[2].value.opportunities||[];
  const failed=results.filter(r=>r.status==='rejected');
  $('discoverError').textContent=failed.length?'部分更新未能载入，请稍后刷新。':'';
  renderHome();renderDiscover();renderInbox();
}
async function openProject(id){
  if(!guardEdit())return;
  app.editing=false;app.id=id;app.detail=null;app.draft=null;remember();
  $('conclusionBody').textContent='正在读取研究内容…';$('workTitle').textContent=projectName(project());
  navigate('workspace');workTab('result');await refreshDetail();
}
async function refreshDetail(){
  if(!app.id)return;
  const id=app.id,version=++app.version;
  try{
    const d=await api(base(id));if(id!==app.id||version!==app.version)return;
    app.detail=d;
    const p=project();if(p){p.references=d.references.length;p.drafts=d.drafts.length;}
    renderWorkspace();renderHome();
    const runtime=d.runtime||{};
    if(d.conversation?.running&&runtime.run_id&&!app.runs.has(runtime.run_id))attachRun(runtime.run_id,id,Object.values(d.cloud_authorizations||{}).some(c=>c.local_run_id===runtime.run_id)?'cloud':undefined);
  }catch(e){if(id===app.id){$('conclusionBody').textContent='读取失败：'+e.message;showToast(e.message);}}
}
function workTab(tab){
  if(app.editing&&tab!=='result'&&!guardEdit())return;
  app.tab=tab;
  document.querySelectorAll('[data-worktab]').forEach(el=>el.classList.toggle('active',el.dataset.worktab===tab));
  document.querySelectorAll('.work-view').forEach(el=>el.classList.toggle('active',el.id==='work-'+tab));
  if(tab==='advanced'){
    const frame=$('advancedFrame');if(frame.dataset.project!==app.id){frame.dataset.project=app.id;frame.src='/classic?project='+encodeURIComponent(app.id);}
  }
}
function referenceHTML(r,i){
  const url=safeUrl(r.url)||(r.doi?'https://doi.org/'+encodeURIComponent(r.doi):null);
  const evidence=(app.detail?.evidence||[]).filter(e=>e.ref_id===r.ref_id);
  const synthetic=r.source==='offline_stub'||String(r.title).includes('SYNTHETIC');
  return `<div class="reference-row"><span class="reference-num">${i+1}</span><div class="reference-info"><b>${esc(r.title)}</b><span>${esc((r.authors||[]).join('、'))} · ${esc(r.year||'年份未录入')} · ${esc(r.venue||r.source)}</span>${url?`<div><a href="${esc(url)}" target="_blank" rel="noopener">查看来源 ${icon('arrow')}</a></div>`:''}${evidence.length?`<details><summary>查看 ${evidence.length} 条证据</summary>${evidence.map(e=>`<p>${esc(e.quote)}${e.page?'\n第 '+e.page+' 页':''}</p>`).join('')}</details>`:''}</div><span class="quality-pill ${synthetic?'warn':''}">${synthetic?'离线合成样例':evidence.length?'已有证据':'待阅读'}</span></div>`;
}
function renderWorkspace(){
  const d=app.detail;if(!d)return;
  $('workTitle').textContent=projectName(project());
  $('workMeta').textContent=`${d.references.length} 篇文献 · ${d.materials.length} 份材料 · ${d.drafts.length} 份草稿${d.lifecycle?' · '+d.lifecycle.stage:''}`;
  $('referenceCount').textContent=d.references.length;
  $('keyReferenceList').innerHTML=d.references.slice(0,3).map(referenceHTML).join('')||empty('还没有参考文献');
  $('allReferences').innerHTML=d.references.map(referenceHTML).join('')||empty('导入材料或检索文献后，来源会出现在这里');
  $('materialsList').innerHTML=d.materials.map(m=>`<div class="reference-row"><span class="reference-num">${icon('file')}</span><div class="reference-info"><b>${esc(m.filename||m.path?.split(/[\\/]/).pop())}</b><span>${esc(({imported:'已解析',converted:'已转换',error:'解析失败',pending_conversion:'需要转换',processing:'处理中'}[m.status])||m.status)}</span>${m.error?`<p>${esc(m.error)}</p>`:''}</div></div>`).join('')||empty('还没有导入研究材料');
  $('nextStep').textContent=nextStep();
  const facts=[['材料与来源',`${d.references.length} 条文献记录、${d.materials.length} 份导入材料`],['证据积累',`${d.evidence.length} 条证据片段，仍需核对支撑关系`],['研究产出',`${d.drafts.length} 份草稿、${d.runs.length} 次研究记录`]];
  $('findingCards').innerHTML=facts.map(([label,text],i)=>`<div class="result-card"><small>0${i+1}</small><b>${label}</b><p>${text}</p></div>`).join('');
  const focused=d.conversation?.target?.filename;const draftName=app.draft?.projectId===app.id&&d.drafts.includes(app.draft.name)?app.draft.name:d.drafts.includes(focused)?focused:d.drafts[0];
  $('draftChoice').innerHTML=d.drafts.map(n=>`<option value="${esc(n)}">${esc(n)}</option>`).join('');
  $('draftChoice').disabled=!d.drafts.length;
  if(draftName){$('draftChoice').value=draftName;if(!app.editing)loadDraft(draftName);}
  else{app.draft=null;$('conclusionBody').textContent='还没有形成研究内容。可以导入已有文稿，或在下方描述研究问题。';$('editConclusion').disabled=true;$('exportDraft').disabled=true;}
  renderLifecycleControls();renderDiscussion();renderProcess();renderRunStatus();
  $('resumeRun').hidden=!d.conversation?.can_resume;
}
async function loadDraft(name,focus=false){
  if(!name)return;const id=app.id;
  try{const data=await api(base(id)+'/drafts/'+encodeURIComponent(name));if(id!==app.id||$('draftChoice').value!==name||app.editing)return;if(focus)await post(base(id)+'/conversation/focus',{filename:name});if(id!==app.id||$('draftChoice').value!==name)return;app.draft={...data,projectId:id};$('conclusionBody').textContent=data.rendered||data.raw;$('editConclusion').disabled=!!activeRun();$('exportDraft').disabled=false;}
  catch(e){if(id===app.id){$('conclusionBody').textContent='读取草稿失败：'+e.message;showToast(e.message);}}
}
async function toggleEditor(){
  if(!app.draft)return;
  if(!app.editing){app.editing=true;$('conclusionBody').hidden=true;$('draftEditor').hidden=false;$('draftEditor').value=app.draft.raw;$('editConclusion').textContent='保存';$('editTip').hidden=false;$('cancelEdit').hidden=false;$('draftChoice').disabled=true;$('draftEditor').focus();return;}
  const id=app.id,draft=app.draft;
  $('editConclusion').disabled=true;
  try{const saved=await api(base(id)+'/drafts/'+encodeURIComponent(draft.name),{method:'PUT',body:JSON.stringify({raw:$('draftEditor').value,expected_raw:draft.raw})});app.draft={...saved,projectId:id};endEdit();$('conclusionBody').textContent=saved.rendered;showToast('草稿已保存');}
  catch(e){showToast(e.message);}finally{$('editConclusion').disabled=false;}
}
function endEdit(){app.editing=false;$('conclusionBody').hidden=false;$('draftEditor').hidden=true;$('editTip').hidden=true;$('cancelEdit').hidden=true;$('editConclusion').innerHTML=icon('edit')+' 编辑';$('draftChoice').disabled=false;}
function renderDiscussion(){
  const messages=app.detail?.messages||[],cards=app.detail?.cloud_authorizations||{},pid=app.id;
  $('discussionList').innerHTML=messages.map(m=>{
    const card=m.role==='assistant'?cards[m.authorization_id]:null;let controls='';
    if(card?.status==='pending')controls=`<section class="cloud-consent" data-consent="${esc(card.id)}"><p><strong>本轮云端授权</strong></p><p>向 ${esc(card.binding.cloud_url)} 及其模型服务发送本轮需求、项目近期对话、参考文献、证据摘要、研究记录、工作流及按需工具结果。由本机读取本项目资料和运行检查，原文件不整份自动上传。</p><label><input type="checkbox" data-choice="allow_write"> 允许本轮修改项目稿件、整理记录和生成研究产物</label><label><input type="checkbox" data-choice="include_personal"> 同时使用个人记忆、偏好、提醒和关注信息</label>${card.available_roots?.length?`<label><input type="checkbox" data-choice="include_external"> 同时读取已导入的外部材料目录</label><details><summary>查看外部目录</summary><pre>${esc(card.available_roots.join('\n'))}</pre></details>`:''}<label><input type="checkbox" data-choice="allow_mcp"> 允许调用已配置的 MCP 外部工具（其访问范围由原工具配置决定，不限于本项目目录；同时需要勾选项目写入）</label><p class="small-muted">只对当前请求生效，最长 1 小时；可停止运行或撤销授权。未勾选项目写入时只读。</p><button class="button-primary" data-approve="${esc(card.id)}">授权并继续</button> <button class="button-light" data-reject="${esc(card.id)}">取消本轮</button><p role="status" data-consent-status></p></section>`;
    else if(card?.status==='setup_required')controls='<button class="button-light" data-cloud-setup>连接此项目的云端账户</button>';
    else if(card)controls=`<p class="small-muted">${esc(({rejected:'本轮已取消',finished:'本轮已结束',cancelled:'授权已撤销',approved:'已授权',running:'已授权，云端决策 / 本地执行'})[card.status]||card.status)}</p>${['approved','running'].includes(card.status)?`<button class="button-light" data-revoke-chat="${esc(card.id)}">撤销本轮授权</button>`:''}`;
    return `<div class="discussion-entry"><strong>${m.role==='user'?'我':'虫虫'}${m.execution_owner==='cloud'?' · 云端':''}</strong><p>${esc(m.content)}</p>${controls}</div>`;
  }).join('')||empty('直接告诉虫虫你想研究什么；默认由云端 Agent 决策，本机执行工具与验证');
  for(const button of $('discussionList').querySelectorAll('[data-approve],[data-reject]'))button.onclick=async()=>{
    const aid=button.dataset.approve||button.dataset.reject,section=button.closest('[data-consent]'),approved=!!button.dataset.approve;
    const body={approved};for(const input of section.querySelectorAll('[data-choice]'))body[input.dataset.choice]=input.checked;
    for(const b of section.querySelectorAll('button'))b.disabled=true;
    try{
      const reply=await post(base(pid)+'/messages/authorizations/'+aid,body);
      if(app.id!==pid)return;
      await refreshDetail();workTab('discussion');
      if(reply.run_id)attachRun(reply.run_id,pid,reply.mode);
    }catch(e){if(app.id===pid){section.querySelector('[data-consent-status]').textContent=e.message;for(const b of section.querySelectorAll('button'))b.disabled=false;}}
  };
  for(const button of $('discussionList').querySelectorAll('[data-cloud-setup]'))button.onclick=()=>openCloudSync(pid);
  for(const button of $('discussionList').querySelectorAll('[data-revoke-chat]'))button.onclick=async()=>{try{await api(base(pid)+'/messages/authorizations/'+button.dataset.revokeChat,{method:'DELETE'});await refreshDetail();}catch(e){showToast(e.message);}};
}
function renderProcess(){
  const runs=app.detail?.runs||[];
  $('processContent').innerHTML=`<div class="simple-paper"><div class="result-head"><h3>研究过程</h3><button class="text-link" data-refresh>刷新 ${icon('clock')}</button></div>${runs.map(r=>`<article class="process-run"><h4>${esc(r.goal||'研究记录')}</h4><div class="small-muted">${esc(status(r.status))} · ${r.iterations||0} 步</div><details><summary>核对执行记录与来源</summary><pre>${esc(JSON.stringify(r.steps,null,2))}</pre>${r.quality_report?`<pre>${esc(JSON.stringify(r.quality_report,null,2))}</pre>`:''}</details></article>`).join('')||empty('尚无运行记录')}</div>`;
}
function activeRun(){return [...app.runs.values()].find(r=>r.projectId===app.id&&r.running);}
function renderRunStatus(){const run=activeRun();$('runStatus').hidden=!run;$('runStatusText').textContent=run?.label||'';$('stopRun').disabled=!!run?.cancelling;$('workAsk').querySelector('button').disabled=!!run||app.busy;$('workQuestion').disabled=!!run||app.busy;if($('draftChoice'))$('draftChoice').disabled=!!run||app.editing||!app.detail?.drafts?.length;if($('editConclusion'))$('editConclusion').disabled=!!run||!app.draft;}
async function sendRequest(text,resume=false){
  if(!text?.trim()&&!resume)return;
  if(!app.id){$('projectName').value=text.slice(0,28);$('projectGoal').value=text;openCreate();return;}
  if(activeRun()||app.busy){showToast('当前研究正在进行');return;}
  if(!guardEdit())return;
  if(app.editing)endEdit();
  const id=app.id;if(!resume&&!await ensureCaseSelection())return;app.busy=true;renderRunStatus();
  try{
    const response=await post(base(id)+'/messages',{text:text||app.detail?.conversation?.request||'继续当前研究',mode:app.mode,resume,...(resume?{}:workflowRunFields())});
    if(response.status==='authorization_required'||response.status==='cloud_setup_required'){await refreshDetail();workTab('discussion');return true;}
    if(app.detail?.project_id===id){app.detail.messages.push({role:'user',content:text||'继续当前研究'});renderDiscussion();workTab('discussion');}
    attachRun(response.run_id,id,response.mode);
    return true;
  }catch(e){showToast('启动失败：'+e.message);return false;}finally{app.busy=false;renderRunStatus();}
}
const stepNames={search:'检索文献',read_pdf:'阅读材料',file_import:'导入材料',save_reference:'整理文献',save_evidence:'提取证据',write_draft:'更新草稿',validate:'核对引用',finish:'整理研究结果',chat:'正在回复',ask_user:'需要你补充信息'};
function attachRun(id,projectId,mode){
  if(app.runs.has(id))return;
  const run={id,projectId,running:true,label:mode==='offline'?'离线演示运行中，合成结果会明确标注':mode==='cloud'?'云端 Agent 决策中，本机等待工具指令':'本地 Agent 正在整理研究上下文',seen:new Set(),failures:0};app.runs.set(id,run);renderRunStatus();
  const stream=new EventSource('/api/v1/runs/'+encodeURIComponent(id)+'/events');run.stream=stream;
  stream.onmessage=event=>{
    run.failures=0;
    if(event.data==='[DONE]'){stream.close();if(run.running)finish('interrupted');return;}
    let data;try{data=JSON.parse(event.data);}catch{return;}
    if(data.type==='cloud_sync_pending'){run.label=data.message;if(projectId===app.id)renderRunStatus();}
    if(data.type==='step'){
      const key=JSON.stringify(data);if(run.seen.has(key))return;run.seen.add(key);
      const action=typeof data.action==='string'?data.action:data.action?.action;
      run.label=stepNames[action]||'正在推进研究';if(projectId===app.id)renderRunStatus();
    }
    if(data.type==='run_finished')finish(data.status,data.summary);
  };
  stream.onerror=()=>{if(!run.running)return;run.failures++;run.label='连接暂时中断，正在恢复…';if(projectId===app.id)renderRunStatus();if(run.failures>=3)finish('interrupted');};
  function finish(outcome,summary){run.running=false;stream.close();if(projectId===app.id){renderRunStatus();refreshDetail();showToast(summary||status(outcome));}loadProjects();}
}
async function askGlobal(text){if(!text.trim())return;if(!app.id){$('projectName').value=text.slice(0,28);$('projectGoal').value=text;openCreate();return;}if(app.page!=='workspace')await openProject(app.id);return await sendRequest(text);}
async function createProject(event){
  event.preventDefault();const button=$('newProjectForm').querySelector('[type=submit]');button.disabled=true;
  try{const result=await post('/api/v1/projects',{name:$('projectName').value.trim(),goal:$('projectGoal').value.trim(),lifecycle_type:$('projectLifecycle').value||null});await loadProjects();$('newProjectModal').hidden=true;$('newProjectForm').reset();await openProject(result.project_id);showToast('研究已创建');}
  catch(e){showToast(e.message);}finally{button.disabled=false;}
}
function openCreate(){$('newProjectModal').hidden=false;$('projectName').focus();}
async function uploadFiles(files){
  if(!files.length)return;
  if(!app.id){openCreate();showToast('先创建研究，再添加材料');return;}
  const id=app.id;let success=0;const failures=[];
  $('uploadStatus').textContent='正在导入材料…';
  for(const file of files){try{const response=await fetch(base(id)+'/imports/upload?filename='+encodeURIComponent(file.name),{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});if(!response.ok){const error=await response.json();throw new Error(error.detail||'上传失败');}success++;}catch(e){failures.push(file.name+'：'+e.message);}}
  $('uploadStatus').textContent='';await loadProjects();if(id===app.id){await refreshDetail();navigate('workspace');workTab('references');}
  if(failures.length)showDialog('材料导入结果',`成功 ${success} 份\n${failures.join('\n')}`);else showToast(`已导入 ${success} 份材料`);
}
async function importFolder(folder){
  if(!folder.trim())return;if(!app.id){openCreate();return;}
  const id=app.id;$('folderSubmit').disabled=true;$('uploadStatus').textContent='正在扫描文件夹…';
  try{const data=await post(base(id)+'/imports/folder',{folder,recursive:true});$('folderModal').hidden=true;await loadProjects();if(id===app.id){await refreshDetail();workTab('references');}showDialog('文件夹导入结果',`已导入 ${(data.imported||[]).length} 份材料\n失败 ${(data.errors||[]).length} 份，暂不支持 ${data.unsupported_count||0} 份`);}
  catch(e){showToast(e.message);}finally{$('folderSubmit').disabled=false;$('uploadStatus').textContent='';}
}
function renderDiscover(){
  const list=app.discovery==='radar'?app.hits:app.opportunities;
  $('discoverGrid').innerHTML=list.map((item,i)=>`<article class="discover-card"><small>${app.discovery==='radar'?'领域动态':'研究机会'} · ${esc(item.source||'')}</small><h3>${esc(item.title)}</h3><p>${esc(item.ai_evaluation?.rationale||item.ai_evaluation?.summary||item.payload?.summary||item.topic_query||'查看来源与当前研究的关系')}</p><div class="small-muted">${item.payload?.deadline?'截止：'+esc(item.payload.deadline):''}</div><button class="text-link" data-explore="${i}">查看详情 ${icon('arrow')}</button></article>`).join('')||empty(app.discovery==='radar'?'还没有领域动态，可先设置关注方向':'还没有发现可评估的机会');
  document.querySelectorAll('[data-discover]').forEach(el=>el.classList.toggle('active',el.dataset.discover===app.discovery));
}
function showDiscovery(index){
  const item=(app.discovery==='radar'?app.hits:app.opportunities)[index];if(!item)return;
  const reason=item.ai_evaluation?.rationale||item.payload?.summary||item.topic_query||'尚无匹配说明';
  showDialog(item.title,reason);
  const url=safeUrl(item.source_url||item.url);if(url){const a=document.createElement('a');a.href=url;a.target='_blank';a.rel='noopener';a.className='button-light';a.textContent='查看原始来源';$('dialogActions').append(a);}
  dialogAction('让虫虫分析',()=>{closeDialog();askGlobal('请基于来源分析这条线索与当前研究的关系，不确定的信息请注明：'+item.title+' '+(url||''));});
  dialogAction('放入研究计划',async()=>{if(!app.id){closeDialog();openCreate();return;}try{await post('/api/v1/tasks',{title:'评估：'+item.title,project_id:app.id,priority:2});showToast('已保存到研究计划');closeDialog();}catch(e){showToast(e.message);}});
}
async function followTopic(event){
  event.preventDefault();if(!app.id){openCreate();return;}
  try{await post('/api/v1/standing-intents',{description:$('followTopic').value,topic:$('followTopic').value,type:'literature',scope:{project_id:app.id},notify_policy:{channel:'in_app'}});$('followModal').hidden=true;showToast('已保存关注方向');}catch(e){showToast(e.message);}
}
function renderInbox(){
  const list=app.notifications.filter(n=>app.mailFilter==='all'||(app.mailFilter==='read'?n.read:!n.read));
  $('mailList').innerHTML=list.map(n=>`<div class="mail"><span class="update-icon">${icon('mail')}</span><div class="copy"><b>${esc(n.title)}</b><p>${esc(n.body||'')} · ${esc(stamp(n.created_at))}</p></div><button class="button-light" data-mail="${esc(n.notification_id)}">查看</button>${!n.read?`<button class="text-link" data-mark="${esc(n.notification_id)}">知道了</button>`:''}</div>`).join('')||empty('这里暂时没有消息');
  document.querySelectorAll('[data-mailfilter]').forEach(el=>el.classList.toggle('active',el.dataset.mailfilter===app.mailFilter));
}
async function markRead(id){try{await post('/api/v1/notifications/'+encodeURIComponent(id)+'/read');const n=app.notifications.find(n=>n.notification_id===id);if(n)n.read=true;renderHome();renderInbox();showToast('已标记为已处理');}catch(e){showToast(e.message);}}
function showMail(id){const n=app.notifications.find(n=>n.notification_id===id);if(!n)return;showDialog(n.title,n.body||'暂无详细内容');if(!n.read)dialogAction('知道了',async()=>{await markRead(id);closeDialog();});dialogAction('让虫虫研究',()=>{closeDialog();askGlobal('请分析这条消息对当前研究的影响：'+n.title+' '+(n.body||''));});}
function showDialog(title,text){app.dialogVersion=(app.dialogVersion||0)+1;$('detailTitle').textContent=title;$('detailText').textContent=text;$('dialogActions').replaceChildren();$('detailModal').hidden=false;}
function closeDialog(){$('detailModal').hidden=true;app.dialogVersion=(app.dialogVersion||0)+1;}
function dialogAction(label,handler){const button=document.createElement('button');button.className='button-light';button.textContent=label;button.onclick=handler;$('dialogActions').append(button);}
function closeQuick(){$('quickPanel').hidden=true;$('desktopQuick').hidden=true;}
function openQuick(){$('quickPanel').hidden=!$('quickPanel').hidden;renderHome();}
function desktopOpen(open){$('desktopMode').hidden=!open;$('desktopQuick').hidden=true;document.body.style.overflow=open?'hidden':'';closeQuick();}
function desktopQuick(){
  const host=$('desktopQuick');host.hidden=!host.hidden;if(host.hidden)return;
  host.innerHTML=`<div class="qp-head"><b>PaperBug</b><button class="qp-close" data-desktop-close aria-label="关闭">${icon('close')}</button></div><h2>有什么想研究的吗？</h2><p>${esc(nextStep())}</p><button class="qp-action" data-desktop-continue>继续当前研究 ${icon('arrow')}</button><form class="qp-input" id="desktopAsk"><input placeholder="告诉虫虫你的问题…" required/><button>发送</button></form>`;
  host.querySelector('[data-desktop-close]').onclick=()=>host.hidden=true;
  host.querySelector('[data-desktop-continue]').onclick=()=>{desktopOpen(false);app.id?openProject(app.id):openCreate();};
  $('desktopAsk').onsubmit=e=>{e.preventDefault();const text=e.target.querySelector('input').value;desktopOpen(false);askGlobal(text);};
}

function installControls(){
  const error=document.createElement('div');error.id='connectionError';error.className='app-error';error.hidden=true;error.innerHTML='<span id="connectionErrorText"></span><button class="button-light" id="retryConnection">重试</button>';document.querySelector('main').before(error);
  const tools=document.createElement('div');tools.className='tools-row';tools.innerHTML='<button class="button-light" id="uploadMaterial">'+icon('paperclip')+' 导入材料</button><button class="button-light" id="folderImport">'+icon('folder')+' 打开文件夹</button><span id="uploadStatus" role="status" class="small-muted"></span>';document.querySelector('.work-tabs').before(tools);
  const drafts=document.createElement('div');drafts.className='tools-row';drafts.innerHTML='<label for="draftChoice">当前内容</label><select id="draftChoice" aria-label="当前草稿"></select><button class="button-light" id="exportDraft">导出 DOCX</button><button class="button-light" id="cancelEdit" hidden>取消编辑</button>';$('work-result').prepend(drafts);
  const editor=document.createElement('textarea');editor.id='draftEditor';editor.className='draft-editor';editor.hidden=true;editor.setAttribute('aria-label','编辑研究草稿');$('conclusionBody').after(editor);
  const materials=document.createElement('section');materials.className='result-section';materials.innerHTML='<h3>导入材料</h3><div id="materialsList"></div>';$('work-references').append(materials);
  const tab=document.createElement('button');tab.className='work-tab';tab.dataset.worktab='advanced';tab.textContent='研究设置与记录';document.querySelector('.work-tabs').append(tab);
  const advanced=document.createElement('div');advanced.id='work-advanced';advanced.className='work-view';advanced.innerHTML='<iframe id="advancedFrame" class="advanced-frame" title="研究设置、评审与项目记录" loading="lazy"></iframe>';$('workAsk').before(advanced);
  const run=document.createElement('div');run.id='runStatus';run.className='run-state';run.hidden=true;run.innerHTML='<span id="runStatusText" role="status"></span><button class="button-light" id="stopRun">停止</button>';$('workAsk').before(run);
  const resume=document.createElement('button');resume.id='resumeRun';resume.type='button';resume.className='button-light';resume.textContent='继续上次研究';resume.hidden=true;tools.append(resume);
  const discoveryTools=document.createElement('div');discoveryTools.className='tools-row';discoveryTools.innerHTML='<button class="button-light" id="followDirection">关注研究方向</button><button class="button-light" id="refreshDiscovery">刷新发现</button><span class="small-muted" id="discoverError"></span>';$('discoverGrid').before(discoveryTools);
  const field=document.createElement('label');field.innerHTML='研究类型<select id="projectLifecycle"><option value="">稍后设置</option></select>';$('projectGoal').after(field);
  document.body.insertAdjacentHTML('beforeend',`<div class="modal-layer" id="detailModal" hidden><section class="modal" role="dialog" aria-modal="true" aria-labelledby="detailTitle"><div class="dialog-heading"><h2 id="detailTitle"></h2><button class="icon-button" id="detailClose" aria-label="关闭">${icon('close')}</button></div><p id="detailText" class="dialog-copy"></p><div id="dialogActions" class="modal-actions"></div></section></div>
  <div class="modal-layer" id="folderModal" hidden><form class="modal" id="folderForm"><h2>打开研究文件夹</h2><label for="folderPath">本机文件夹完整路径</label><input id="folderPath" placeholder="E:\\我的研究" required/><div class="modal-actions"><button type="button" class="button-light" data-close="folderModal">取消</button><button id="folderSubmit" class="button-primary">导入材料</button></div></form></div>
  <div class="modal-layer" id="followModal" hidden><form class="modal" id="followForm"><h2>关注研究方向</h2><label for="followTopic">主题或关键词</label><input id="followTopic" required/><div class="modal-actions"><button type="button" class="button-light" data-close="followModal">取消</button><button class="button-primary">保存关注</button></div></form></div>
  <div class="modal-layer" id="settingsModal" hidden><section class="modal"><h2>运行设置</h2><p id="modelStatus"></p><label for="runMode">研究运行模式</label><select id="runMode"><option value="auto">云端 Agent（默认，本机执行工具）</option><option value="llm">本地 Agent（直接调用模型）</option><option value="offline">离线演示（合成数据）</option></select><div class="modal-actions"><button class="button-primary" data-close="settingsModal">完成</button></div></section></div>`);
  $('attachment').multiple=true;$('attachment').accept='.pdf,.doc,.docx,.wps,.txt,.md,.bib,.ris';
  document.querySelector('.suggestion-emoji').innerHTML=icon('spark');
  $('newProjectModal').querySelector('.modal').setAttribute('role','dialog');
}
function bindEvents(){
  document.addEventListener('click',event=>{
    const find=selector=>event.target.closest(selector);
    if(find('[data-nav]')){navigate(find('[data-nav]').dataset.nav);return;}
    if(find('[data-project]')){openProject(find('[data-project]').dataset.project);return;}
    if(find('[data-worktab]')){workTab(find('[data-worktab]').dataset.worktab);return;}
    if(find('[data-open-tab]')){workTab(find('[data-open-tab]').dataset.openTab);return;}
    if(find('[data-create]')){openCreate();return;}
    if(find('[data-refresh]')){refreshDetail();return;}
    if(find('[data-discover]')){app.discovery=find('[data-discover]').dataset.discover;renderDiscover();return;}
    if(find('[data-explore]')){showDiscovery(Number(find('[data-explore]').dataset.explore));return;}
    if(find('[data-mailfilter]')){app.mailFilter=find('[data-mailfilter]').dataset.mailfilter;renderInbox();return;}
    if(find('[data-mark]')){markRead(find('[data-mark]').dataset.mark);return;}
    if(find('[data-mail]')){showMail(find('[data-mail]').dataset.mail);return;}
    if(find('[data-close]')){$(find('[data-close]').dataset.close).hidden=true;}
  });
  $('homeAsk').onsubmit=async e=>{e.preventDefault();const text=$('homeQuestion').value;if(await askGlobal(text))$('homeQuestion').value='';};
  $('workAsk').onsubmit=async e=>{e.preventDefault();const text=$('workQuestion').value;if(await sendRequest(text))$('workQuestion').value='';};
  $('quickAsk').onsubmit=async e=>{e.preventDefault();if(await askGlobal($('quickQuestion').value)){$('quickQuestion').value='';closeQuick();}};
  $('homeSuggestion').onclick=()=>app.id?openProject(app.id):openCreate();
  $('homeAttach').onclick=$('uploadMaterial').onclick=()=>app.id?$('attachment').click():openCreate();
  $('attachment').onchange=e=>{uploadFiles([...e.target.files]);e.target.value='';};
  $('folderImport').onclick=()=>{$('folderModal').hidden=false;$('folderPath').focus();};
  $('folderForm').onsubmit=e=>{e.preventDefault();importFolder($('folderPath').value);};
  $('newProjectForm').onsubmit=createProject;$('addProject').onclick=openCreate;$('cancelProject').onclick=()=>$('newProjectModal').hidden=true;
  $('editConclusion').onclick=toggleEditor;$('cancelEdit').onclick=()=>{endEdit();$('conclusionBody').textContent=app.draft.rendered;};
  $('draftChoice').onchange=()=>loadDraft($('draftChoice').value,true);
  $('exportDraft').onclick=()=>{if(app.draft)window.location.assign(base()+'/drafts/'+encodeURIComponent(app.draft.name)+'/export?format=docx');};
  $('stopRun').onclick=async()=>{const run=activeRun();if(!run)return;try{await post('/api/v1/runs/'+run.id+'/cancel');run.cancelling=true;run.label='正在停止，已完成的内容会保留';renderRunStatus();}catch(e){showToast(e.message);}};
  $('resumeRun').onclick=()=>sendRequest('',true);
  $('followDirection').onclick=()=>{$('followModal').hidden=false;$('followTopic').focus();};$('followForm').onsubmit=followTopic;
  $('refreshDiscovery').onclick=loadUpdates;
  $('detailClose').onclick=closeDialog;
  $('searchQuick').onclick=$('floatingBug').onclick=openQuick;$('quickClose').onclick=closeQuick;$('quickContinue').onclick=()=>app.id?openProject(app.id):openCreate();
  $('desktopPreview').onclick=()=>desktopOpen(true);$('desktopExit').onclick=()=>desktopOpen(false);$('desktopBug').onclick=desktopQuick;
  document.querySelectorAll('[data-mascot]').forEach(el=>el.onclick=()=>{document.querySelectorAll('[data-mascot]').forEach(b=>b.classList.toggle('active',b===el));$('desktopBugImage').src=artwork.motions[el.dataset.mascot]||artwork.motions.idle;});
  $('settingsButton').onclick=()=>{$('settingsModal').hidden=false;$('modelStatus').textContent='默认由云端 Agent 决策，发送后在对话中授权。云连接不可用时会提示，不会自动降级。本地 Agent 和离线演示需明确选择。';};
  $('runMode').onchange=()=>app.mode=$('runMode').value;
  $('retryConnection').onclick=initialize;
  document.querySelectorAll('.modal-layer').forEach(layer=>layer.addEventListener('click',e=>{if(e.target===layer)layer.hidden=true;}));
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){document.querySelectorAll('.modal-layer').forEach(el=>el.hidden=true);desktopOpen(false);closeQuick();}if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();openQuick();}});
  window.addEventListener('beforeunload',e=>{if(app.editing){e.preventDefault();e.returnValue='';}});
  const workspace=$('workspace');workspace.addEventListener('dragover',e=>{if(e.dataTransfer.types.includes('Files')){e.preventDefault();workspace.classList.add('drop-active');}});workspace.addEventListener('dragleave',e=>{if(!workspace.contains(e.relatedTarget))workspace.classList.remove('drop-active');});workspace.addEventListener('drop',e=>{e.preventDefault();workspace.classList.remove('drop-active');uploadFiles([...e.dataTransfer.files]);});
}
async function initialize(){
  try{
    errorBanner('');await loadProjects();
    const remembered=localStorage.getItem('paperagent:lastProjectId');if(app.projects.some(p=>p.project_id===remembered))app.id=remembered;
    if(app.id)await refreshDetail();
    await loadUpdates();
    const results=await Promise.allSettled([api('/api/v1/config'),api('/api/v1/lifecycles')]);
    if(results[0].status==='fulfilled')app.config=results[0].value;
    if(results[1].status==='fulfilled'){app.lifecycles=results[1].value.lifecycles||[];$('projectLifecycle').innerHTML='<option value="">稍后设置</option>'+app.lifecycles.map(c=>`<option value="${esc(c.lifecycle_type)}">${esc(c.name)}</option>`).join('');}
    if(app.detail)renderLifecycleControls();renderHome();
  }catch(e){errorBanner('连接科研空间失败：'+e.message);}
}
installControls();bindEvents();renderHome();renderProjects();renderInbox();renderDiscover();
initialize().then(()=>{const parts=location.hash.slice(1).split('/');if(parts[0]==='workspace'&&parts[1]&&app.projects.some(p=>p.project_id===decodeURIComponent(parts[1])))openProject(decodeURIComponent(parts[1]));else if(['projects','discover','inbox'].includes(parts[0]))navigate(parts[0]);});


// Lifecycle cases preserve independent versions and approvals across chat runs.
function workflowRunFields(){
  const id=$('activeWorkflow')?.value;
  if(id==='off')return {workflow_mode:'off'};
  const selected=chosenCase(app.id), c=app.caseCache?.projectId===app.id?app.caseCache.cases.find(c=>c.case_id===selected):null;
  if(c&&c.status==='active'&&(!app.detail?.lifecycle||c.contract.lifecycle_types.includes(app.detail.lifecycle.lifecycle_type))&&(!id||c.workflow_id===id))return {workflow_id:c.workflow_id,workflow_options:{case_id:c.case_id,scope:c.scope}};
  return id?{workflow_id:id}:{};
}

function renderLifecycleControls(){
  const d=app.detail,panel=$('lifecycleControls');if(!d||!panel)return;
  const same=panel.dataset.lifecycleProject===app.id, selected=same?$('activeWorkflow')?.value:readPreference(app.id,'workflow',d.conversation?.workflow_id||'');
  const priorKind=same?$('activeLifecycle')?.value:null, priorStage=same?$('activeStage')?.value:null;
  panel.dataset.lifecycleProject=app.id;
  const kind=d.lifecycle?.lifecycle_type||'', catalog=app.lifecycles.find(c=>c.lifecycle_type===kind);
  panel.innerHTML=`<div class="lifecycle-fields"><label>生命周期 <select id="activeLifecycle"><option value="">请选择</option>${app.lifecycles.map(c=>`<option value="${c.lifecycle_type}">${esc(c.name)}</option>`).join('')}</select></label><label>现实阶段 <select id="activeStage"></select></label><button class="button-light" id="saveRealStage">保存阶段</button></div><div class="lifecycle-fields"><label>本次流程 <select id="activeWorkflow"><option value="">按请求判断</option><option value="off">本轮不使用流程</option>${(catalog?.workflows||[]).map(w=>`<option value="${w.id}">${w.code} · ${esc(w.name)}</option>`).join('')}</select></label><button class="button-light" id="startWorkflowCase">建立交付记录</button></div><p id="workflowDesign" class="small-muted"></p>`;
  $('activeLifecycle').value=priorKind||kind;
  function stages(){const c=app.lifecycles.find(c=>c.lifecycle_type===$('activeLifecycle').value);$('activeStage').innerHTML=(c?.stages||[]).map(s=>`<option value="${s.id}">${esc(s.name)}</option>`).join('');if(c?.lifecycle_type===kind)$('activeStage').value=d.lifecycle.stage;}
  stages();if(priorStage&&[...$('activeStage').options].some(o=>o.value===priorStage))$('activeStage').value=priorStage;$('activeLifecycle').onchange=stages;
  if([...$('activeWorkflow').options].some(o=>o.value===selected))$('activeWorkflow').value=selected;
  function describe(){const w=catalog?.workflows.find(w=>w.id===$('activeWorkflow').value);$('workflowDesign').textContent=w?`输入：${w.inputs.join('、')}。交付：${w.deliverables.join('、')}。可跳过、回访或从局部开始；正式版本需确认。`:(d.workflow_notice?'上次流程与当前背景不一致，请重新选择本次流程。':'');}
  describe();$('activeWorkflow').onchange=()=>{savePreference(app.id,'workflow',$('activeWorkflow').value);describe();$('workflowDesign').append(methods);};
  const methods=document.createElement('button');methods.className='button-light';methods.textContent='查看适用方法';methods.onclick=()=>openSkillMethods(app.id,$('activeWorkflow').value);$('workflowDesign').append(methods);
  $('saveRealStage').onclick=async()=>{try{await post(base()+'/lifecycle',{lifecycle_type:$('activeLifecycle').value,stage:$('activeStage').value,reason:'用户在生命周期面板设置现实阶段'});await openProject(app.id);showToast('现实阶段已保存');}catch(e){showToast(e.message);}};
  $('startWorkflowCase').onclick=()=>configureCase(app.id,$('activeWorkflow').value);
  const syncButton=document.createElement("button");syncButton.className="button-light";syncButton.textContent="云同步设置与阶段历史";syncButton.onclick=()=>openCloudSync(app.id);panel.append(syncButton);
  renderWorkflowCases();renderRelatedServices();
}
async function renderWorkflowCases(){
  const pid=app.id,panel=$('workflowCasePanel');if(!panel)return;
  const epoch=app.caseEpoch=(app.caseEpoch||0)+1;
  const same=panel.dataset.caseProject===pid, openCases=new Set(same?[...panel.querySelectorAll('details[open][data-case-id]')].map(e=>e.dataset.caseId):[]);
  if(!same)panel.replaceChildren();panel.dataset.caseProject=pid;
  try{
    const [{cases},{files}]=await Promise.all([api(base(pid)+'/workflow-cases'),api(base(pid)+'/file-options')]);
    if(app.id!==pid||epoch!==app.caseEpoch)return;
    app.caseCache={projectId:pid,cases,files};panel.replaceChildren();
    for(const c of cases){
      const box=document.createElement('details');box.className='workflow-case';box.dataset.caseId=c.case_id;box.dataset.revision=String(c.revision);box.open=openCases.has(c.case_id);
      const heading=document.createElement('summary');heading.textContent=`${c.code} · ${c.title} · ${{active:'进行中',paused:'已暂停',completed:'已完成',cancelled:'已取消'}[c.status]}${chosenCase(pid)===c.case_id?' · 当前对话使用':''}${c.needs_review?' · 版本需复核':''}`;box.append(heading);
      const note=document.createElement('p');note.className='small-muted';note.textContent=(c.scope==='partial'?'本次为局部任务。':'本次为完整交付。')+' 待交付：'+(c.required_deliverables.filter(l=>!c.outputs.some(o=>o.label===l)).join('、')||'均已绑定');box.append(note);
      const mutate=async(operation,data={},clearKey=null)=>{
        try{await api(base(pid)+'/workflow-cases/'+c.case_id,{method:'PATCH',body:JSON.stringify({operation,data,expected_revision:c.revision})});
          if(clearKey)formDrafts.delete(clearKey);if(app.id===pid)await renderWorkflowCases();return true;
        }catch(e){if(app.id===pid)showToast(e.message);return false;}
      };
      function button(text,handler){const b=document.createElement('button');b.type='button';b.className='button-light';b.textContent=text;b.onclick=async()=>{if(b.disabled)return;b.disabled=true;try{await handler();}finally{if(b.isConnected)b.disabled=false;}};box.append(b);return b;}
      if(c.status==='active'){
        button('暂停',()=>mutate('status',{status:'paused'}));
        const use=button('用于当前对话',()=>{selectCase(pid,c);return renderWorkflowCases();});use.disabled=!!app.detail?.lifecycle&&!c.contract.lifecycle_types.includes(app.detail.lifecycle.lifecycle_type);use.title=use.disabled?'这份记录属于另一个生命周期':'';
        button('取消本次交付',()=>mutate('status',{status:'cancelled'}));
      }else button('恢复 / 重新修订',async()=>{if(await mutate('status',{status:'active'})){selectCase(pid,c);await renderWorkflowCases();}});
      button('查看交付与资料',()=>openResearchFiles(pid,c));
      if(c.code==='P2'||c.code==='P3')button('投稿记录与材料',()=>openSubmissions(pid,c));
      if(c.code==='T4')button('答辩产物',()=>openDeliveryTools(pid,c,'presentation'));
      if(c.status==='active'){
        for(const step of c.contract.steps){const row=document.createElement('label');row.className='workflow-step';const span=document.createElement('span');span.textContent=step.name;const select=document.createElement('select');for(const [v,l] of Object.entries({pending:'待处理',active:'进行中',done:'已完成',skipped:'本次跳过'}))select.add(new Option(l,v));select.value=c.steps[step.id].status;select.onchange=()=>mutate('step',{step_id:step.id,status:select.value});row.append(span,select);box.append(row);}
        const bindings=document.createElement('div');bindings.className='lifecycle-fields';
        const labels=document.createElement('select');labels.setAttribute('aria-label','交付物类型');for(const label of c.required_deliverables)labels.add(new Option(label,label));
        const picker=document.createElement('select');picker.setAttribute('aria-label','交付文件');picker.add(new Option('请选择已保存的稿件或产物',''));for(const f of files.filter(f=>f.can_bind_output))picker.add(new Option(f.label,f.value));
        bindFormDraft(labels,`${pid}/${c.case_id}/outputLabel`,c.required_deliverables[0]);bindFormDraft(picker,`${pid}/${c.case_id}/outputFile`,'');
        const bind=document.createElement('button');bind.type='button';bind.className='button-light';bind.textContent='绑定交付版本';bind.onclick=()=>picker.value?mutate('output',{label:labels.value,path:picker.value}):showToast('请先选择一个交付文件');bindings.append(labels,picker,bind);box.append(bindings);
        for(const [key,decision] of Object.entries(c.decisions)){
          const row=document.createElement('label');row.className='workflow-step';const caption=document.createElement('span');caption.textContent=key;const value=document.createElement('textarea');value.placeholder='填写具体决定或真实情况；无调整时说明无调整';
          const draftKey=`${pid}/${c.case_id}/decision/${key}`;bindFormDraft(value,draftKey,decision.content);
          const save=document.createElement('button');save.type='button';save.className='button-light';save.textContent='保存内容';save.onclick=()=>mutate('decision',{subject:key,content:value.value,required:decision.required},draftKey);row.append(caption,value,save);box.append(row);
          if(c.pending_confirmations.includes(key)&&decision.content)button('确认：'+key,()=>mutate('approve',{subject:key,fingerprint:c.decision_fingerprints[key]}));
        }
        for(const issue of c.quality){const row=document.createElement('p');row.textContent=issue.level+'：'+issue.message;box.append(row);}
        button('登记检查结果',()=>openQualityCheck(pid,c));
        const ready=caseReadiness(c);
        if(!c.final_approved){const approve=button('确认当前正式交付版本',()=>mutate('approve',{subject:'final_delivery',fingerprint:c.fingerprint}));approve.disabled=ready.length>0;approve.title=ready.join('；');}
        else button('完成本次交付',()=>mutate('complete'));
        if(ready.length){const why=document.createElement('p');why.className='small-muted';why.textContent='正式交付前：'+ready.join('；');box.append(why);}
      }
      for(const output of c.outputs){const a=document.createElement('a');a.textContent='下载 '+output.label;a.href=base(pid)+'/workflow-cases/'+c.case_id+'/file?'+new URLSearchParams({label:output.label});a.setAttribute('download','');const row=document.createElement('p');row.append(a);box.append(row);}
      if(c.changed_sources.length){const text=document.createElement('p');text.textContent='资料或交付文件已变化，请重新选择并复核。';box.append(text);}
      panel.append(box);
    }
  }catch(e){if(app.id===pid&&epoch===app.caseEpoch)panel.textContent='交付记录读取失败：'+e.message;}
}

async function renderRelatedServices(){
  const projectId=app.id,controls=$('lifecycleControls');if(!controls)return;
  const related=document.createElement('details');related.innerHTML=`<summary>关联研究与主动科研服务</summary><div class="lifecycle-fields"><label>复用来源 <select id="reuseProject">${app.projects.filter(p=>p.project_id!==projectId).map(p=>`<option value="${esc(p.project_id)}">${esc(projectName(p))}</option>`).join('')}</select></label><select id="reuseFilename" aria-label="来源稿件"><option value="">请选择来源稿件</option></select><button class="button-light" id="reuseSource">关联并保留来源版本</button></div><p id="reuseStatus" class="small-muted"></p><div class="lifecycle-fields"><label>主动服务 <select id="proactiveService"><option value="research_radar">A1 研究动态</option><option value="opportunity_radar">A2 申报与征稿机会</option><option value="progress_watcher">A3 进度与反馈</option></select></label><input id="proactiveQuery" placeholder="关注主题或机会检索词" aria-label="主动服务检索词"><input id="proactiveUrl" placeholder="机会通知来源网址（可选）" aria-label="机会来源网址"><button class="button-light" id="configureProactive">启用关注</button></div><p id="proactiveState" class="small-muted"></p><div id="configuredWatchers"></div>`;controls.append(related);
  let sourceRequest=0;async function sourceFiles(){const source=$('reuseProject').value,ticket=++sourceRequest;try{const data=await api(base(source));if(app.id!==projectId||!related.isConnected||ticket!==sourceRequest||$('reuseProject').value!==source)return;$('reuseFilename').replaceChildren(new Option('请选择来源稿件',''));for(const name of data.drafts)$('reuseFilename').add(new Option(name,name));}catch(e){if(app.id===projectId&&related.isConnected)showToast(e.message);}}
  $('reuseProject').onchange=sourceFiles;if($('reuseProject').value)sourceFiles();
  for(const id of ['proactiveService','proactiveQuery','proactiveUrl'])bindFormDraft($(id),`${projectId}/service/${id}`,$(id).value);
  $('reuseSource').onclick=async()=>{try{await post(base(projectId)+'/research-links',{source_project_id:$('reuseProject').value,filename:$('reuseFilename').value});showToast('已保存来源快照；本流程的审核状态独立');await openProject(projectId);}catch(e){showToast(e.message);}};
  async function load(){try{const [sources,service]=await Promise.all([api(base(projectId)+'/research-links'),api(base(projectId)+'/proactive')]);if(app.id!==projectId||!related.isConnected)return;$('reuseStatus').textContent=sources.links.map(l=>l.source_project_id+' / '+l.filename+(l.needs_review?'（来源已变更，需复核）':'（已固定版本）')).join('；');$('proactiveState').textContent=service.scheduler_running?'调度器已运行；仅有新结果或反馈时提醒。':'后台调度未运行；关注配置已保存，启动调度器后生效。';const list=$('configuredWatchers');list.replaceChildren();for(const w of service.watchers){const row=document.createElement('p');row.textContent=w.name+' · '+(w.enabled?'已启用':'已停用');if(w.enabled){const b=document.createElement('button');b.className='button-light';b.textContent='停用';b.onclick=async()=>{try{await api(base(projectId)+'/proactive/'+w.watcher_id,{method:'DELETE'});await load();}catch(e){showToast(e.message);}};row.append(b);}list.append(row);}}catch(e){if(app.id===projectId&&related.isConnected)$('proactiveState').textContent=e.message;}}
  $('configureProactive').onclick=async()=>{try{const url=$('proactiveUrl').value.trim();await post(base(projectId)+'/proactive',{service:$('proactiveService').value,query:$('proactiveQuery').value,urls:url?[url]:[]});await load();showToast('关注配置已保存');}catch(e){showToast(e.message);}};
  await load();
}

// Bind behavior to the existing controls and detail dialog. Prototype markup,
// page structure, styles and artwork are intentionally unchanged.
const formDrafts=new Map();
function bindFormDraft(control,key,initial){
  control.value=formDrafts.has(key)?formDrafts.get(key):initial;
  const changed=()=>{if(control.value===initial)formDrafts.delete(key);else formDrafts.set(key,control.value);if(key.includes('/decision/')){const box=control.closest('.workflow-case');const button=[...box.querySelectorAll('button')].find(b=>b.textContent==='确认当前正式交付版本');if(button){button.disabled=true;button.title='请先保存当前决定内容，再确认正式版本';}}};
  control.addEventListener('input',changed);control.addEventListener('change',changed);
}
function readPreference(pid,key,fallback=''){try{return JSON.parse(localStorage.getItem('paperagent:binding:'+pid)||'{}')[key]??fallback;}catch{return fallback;}}
function savePreference(pid,key,value){try{const data=JSON.parse(localStorage.getItem('paperagent:binding:'+pid)||'{}');data[key]=value;localStorage.setItem('paperagent:binding:'+pid,JSON.stringify(data));}catch{}}
function chosenCase(pid){return readPreference(pid,'case_id','');}
function selectCase(pid,c){savePreference(pid,'case_id',c.case_id);savePreference(pid,'workflow',c.workflow_id);if(pid===app.id&&$('activeWorkflow'))$('activeWorkflow').value=c.workflow_id;}
function caseReadiness(c){
  const reasons=[];
  if(c.required_deliverables.some(l=>!c.outputs.some(o=>o.label===l)))reasons.push('绑定全部约定交付文件');
  if(c.pending_confirmations.length)reasons.push('确认'+c.pending_confirmations.join('、'));
  if(!c.quality_reviewed)reasons.push('登记当前版本的检查结果');
  if(c.quality.some(i=>i.level==='ERROR'))reasons.push('处理未解决的错误');
  if(c.changed_sources.length)reasons.push('重新绑定已变更的文件');
  if([...formDrafts.keys()].some(k=>k.startsWith(`${app.id}/${c.case_id}/decision/`)))reasons.push('保存尚未提交的决定内容');
  return reasons;
}
function formDialog(title,fields,submit,onSave){
  showDialog(title,'');const version=app.dialogVersion,pid=app.id,form=document.createElement('form');form.id='connectionForm';
  for(const f of fields){
    const label=document.createElement('label');label.textContent=f.label;
    let input;
    if(f.type==='select'||f.type==='multi'){
      input=document.createElement('select');input.multiple=f.type==='multi';
      for(const option of f.options||[])input.add(new Option(option.label??option.value,option.value));
      if(f.type==='multi'){for(const option of input.options)option.selected=(f.value||[]).includes(option.value);}
      else input.value=f.value??input.options[0]?.value??'';
    }else{input=document.createElement(f.type==='textarea'?'textarea':'input');if(f.type!=='textarea')input.type=f.type||'text';input.value=f.value||'';}
    input.id='connection-'+f.name;input.name=f.name;input.required=!!f.required;input.setAttribute('aria-label',f.label);
    if(f.type==='checkbox')input.checked=!!f.value;
    if(f.placeholder)input.placeholder=f.placeholder;
    if(f.disabled)input.disabled=true;
    label.append(input);form.append(label);
    if(f.note){const note=document.createElement('small');note.textContent=f.note;form.append(note);}
  }
  $('detailText').append(form);
  const save=document.createElement('button');save.type='submit';save.setAttribute('form','connectionForm');save.className='button-primary';save.textContent=submit;$('dialogActions').append(save);
  dialogAction('取消',closeDialog);
  form.onsubmit=async e=>{
    e.preventDefault();if(save.disabled||!form.reportValidity()||app.id!==pid)return;
    const data={};for(const f of fields){const input=form.elements.namedItem(f.name);data[f.name]=f.type==='multi'?[...input.selectedOptions].map(o=>o.value):f.type==='checkbox'?input.checked:input.value;}
    save.disabled=true;
    try{await onSave(data,()=>app.id===pid&&version===app.dialogVersion&&!$('detailModal').hidden);}
    catch(error){if(app.id===pid&&version===app.dialogVersion)showToast(error.message);}
    finally{if(save.isConnected)save.disabled=false;}
  };
  return form;
}
function loadingDialog(title){showDialog(title,'正在读取…');const pid=app.id,version=app.dialogVersion;return ()=>pid===app.id&&version===app.dialogVersion&&!$('detailModal').hidden;}
async function configureCase(pid,workflowId){
  const spec=app.lifecycles.flatMap(c=>c.workflows).find(w=>w.id===workflowId);
  if(!spec){showToast('请先选择一个交付流程');return;}
  const valid=loadingDialog('设置本次交付范围');
  try{
    const {files}=await api(base(pid)+'/file-options');if(!valid())return;
    const form=formDialog(spec.code+' · '+spec.name,[
      {name:'scope',label:'本次范围',type:'select',options:[{value:'full',label:'完整交付'},{value:'partial',label:'局部任务'}]},
      {name:'title',label:'交付记录名称',value:spec.name,required:true},
      {name:'outputs',label:'本次交付物',type:'multi',options:spec.deliverables.map(value=>({value})),value:spec.deliverables,required:true,note:'可选择本次需要的文件；局部任务不会强制要求整套材料。'},
      {name:'custom',label:'补充交付物（可选，每行一项）',type:'textarea'},
      {name:'inputs',label:'参考资料（可选，可多选）',type:'multi',options:files.map(f=>({value:f.value,label:f.label})),value:[]}
    ],'建立记录',async(data,current)=>{
      const required=[...data.outputs,...data.custom.split('\n').map(s=>s.trim()).filter(Boolean)];
      if(!required.length)throw new Error('至少选择或填写一项交付物');
      const c=await post(base(pid)+'/workflow-cases',{workflow_id:spec.id,title:data.title,scope:data.scope,required_deliverables:required,inputs:data.inputs});
      selectCase(pid,c);if(current()){closeDialog();await renderWorkflowCases();showToast('已建立记录，对话将使用本次范围');}
    });
    form.elements.scope.onchange=()=>{const partial=form.elements.scope.value==='partial';[...form.elements.outputs.options].forEach((o,i)=>o.selected=!partial||i===0);};
    dialogAction('查看适用方法',()=>openSkillMethods(pid,spec.id));
  }catch(e){if(valid())showToast(e.message);}
}
async function ensureCaseSelection(){
  const id=$('activeWorkflow')?.value;if(!id||id==='off')return true;
  const cache=app.caseCache?.projectId===app.id?app.caseCache:null;
  const all=cache?.cases.filter(c=>c.workflow_id===id&&c.status==='active')||[];
  if(all.some(c=>c.case_id===chosenCase(app.id)))return true;
  if(all.length===1){selectCase(app.id,all[0]);return true;}
  if(all.length>1){
    formDialog('选择本次对话的交付记录',[{name:'case',label:'交付记录',type:'select',options:all.map(c=>({value:c.case_id,label:c.title+' · '+(c.scope==='partial'?'局部任务':'完整交付')}))}],
      '使用此记录',async(data,current)=>{selectCase(app.id,all.find(c=>c.case_id===data.case));if(current()){closeDialog();await renderWorkflowCases();showToast('已选定交付记录，请发送刚才的消息');}});
    return false;
  }
  return true;
}
async function openQualityCheck(pid,c){
  formDialog('登记当前版本检查结果',[
    {name:'issues',label:'未解决的问题（每行一项，无问题可留空）',type:'textarea',value:c.quality.map(i=>({ERROR:'错误',WARNING:'警告',SUGGESTION:'建议'}[i.level])+'：'+i.message).join('\n'),note:'使用“错误：”“警告：”或“建议：”说明程度；已有问题解决后再移除。'},
    {name:'reviewed',label:'我已核对当前交付版本及其依据',type:'checkbox',required:true}
  ],'保存检查结果',async(data,current)=>{
    const issues=data.issues.split('\n').map(s=>s.trim()).filter(Boolean).map(line=>{
      const match=line.match(/^(错误|警告|建议)[：:]\s*(.+)$/);if(!match)throw new Error('每项问题请以“错误：”“警告：”或“建议：”开头');
      return {level:{错误:'ERROR',警告:'WARNING',建议:'SUGGESTION'}[match[1]],message:match[2]};});
    await api(base(pid)+'/workflow-cases/'+c.case_id,{method:'PATCH',body:JSON.stringify({operation:'quality',data:{issues},expected_revision:c.revision})});
    if(current()){closeDialog();await renderWorkflowCases();}
  });
}
async function openResearchFiles(pid,c=null){
  const valid=loadingDialog('研究文件与交付版本');
  try{
    const [{files},{outputs}]=await Promise.all([api(base(pid)+'/file-options'),api(base(pid)+'/research-outputs')]);if(!valid())return;
    showDialog('研究文件与交付版本','');const host=$('detailText');
    for(const f of files){const row=document.createElement('p'),a=document.createElement('a');a.textContent=f.label;a.href=base(pid)+'/files/download?'+new URLSearchParams({value:f.value});a.setAttribute('download','');row.append(a);host.append(row);}
    if(!files.length)host.textContent='暂无已保存文件，可先导入材料或在对话中生成。';
    for(const output of outputs){const p=document.createElement('p');p.textContent=({presentation:'演示与讲稿',submission:'投稿包'}[output.kind]||output.kind)+'：'+(output.needs_review?'来源或文件已变化，需复核':'版本未变')+(output.warnings.length?'。'+output.warnings.join('；'):'');host.append(p);}
    if(c&&c.status==='active')dialogAction('设置参考资料',()=>openInputBindings(pid,c));
    dialogAction('准备投稿包',()=>openDeliveryTools(pid,c,'submission'));
    dialogAction('准备演示与讲稿',()=>openDeliveryTools(pid,c,'presentation'));
  }catch(e){if(valid())showToast(e.message);}
}
async function openInputBindings(pid,c){
  const valid=loadingDialog('选择参考资料');
  try{const {files}=await api(base(pid)+'/file-options');if(!valid())return;
    formDialog('选择本次流程的参考资料',[{name:'inputs',label:'参考文件',type:'multi',options:files.map(f=>({value:f.value,label:f.label})),value:files.filter(f=>c.inputs.some(i=>i.path.replaceAll('\\','/').endsWith('/'+f.value)||i.path===f.value)).map(f=>f.value)}],
      '绑定当前资料版本',async(data,current)=>{await api(base(pid)+'/workflow-cases/'+c.case_id,{method:'PATCH',body:JSON.stringify({operation:'inputs',data:{paths:data.inputs},expected_revision:c.revision})});if(current()){closeDialog();await renderWorkflowCases();}});
  }catch(e){if(valid())showToast(e.message);}
}
async function openDeliveryTools(pid,c,kind){
  const valid=loadingDialog(kind==='submission'?'准备投稿包':'准备演示与讲稿');
  try{const {files}=await api(base(pid)+'/file-options');if(!valid())return;
    const drafts=files.filter(f=>f.kind==='draft'),options=drafts.map(f=>({value:f.value.slice('drafts/'.length),label:f.label}));
    if(!options.length){showDialog('尚无可用稿件','请先导入或保存稿件。');return;}
    const fields=[{name:'filename',label:kind==='submission'?'主稿件':'已保存的演示结构稿',type:'select',options,required:true}];
    if(kind==='submission')fields.push({name:'companions',label:'附加稿件（可多选）',type:'multi',options,value:[]});
    else fields.push({name:'title',label:'演示标题（可选）',value:c?.title||''});
    formDialog(kind==='submission'?'生成本地投稿材料包':'生成可编辑演示与讲稿',fields,'生成文件',async(data,current)=>{
      if(data.companions)data.companions=data.companions.filter(n=>n!==data.filename);
      await post(base(pid)+'/deliveries/'+(kind==='submission'?'submission-package':'presentation'),data);
      if(current()){await openResearchFiles(pid,c);await renderWorkflowCases();}
    });
    if(kind==='presentation')dialogAction('让虫虫准备演示结构',()=>{if(c)selectCase(pid,c);closeDialog();sendRequest('请基于当前论文和已有材料准备答辩演示、讲稿和问答。先保存含 title、slides 数组的演示结构 JSON 稿件（.md 文件），每页含 title、points、notes 和 sources，再使用 presentation_build 生成文件。仅使用真实研究结果。');});
  }catch(e){if(valid())showToast(e.message);}
}
async function openSkillMethods(pid,id){
  const valid=loadingDialog('适用科研方法');
  try{
    const result=id&&id!=='off'?await post(base(pid)+'/workflows/preview',{goal:'查看当前流程适用方法',workflow_id:id}):await api('/api/v1/skills?'+new URLSearchParams({project_id:pid}));
    if(!valid())return;showDialog('适用科研方法','');const host=$('detailText');
    for(const skill of result.workflow_skills||result.skills||[]){const b=document.createElement('button');b.className='button-light';b.textContent=skill.name||skill.id;b.onclick=()=>openSkillDetail(pid,skill);host.append(b);}
  }catch(e){if(valid())showToast(e.message);}
}
async function openSkillDetail(pid,skill){
  const valid=loadingDialog(skill.name||skill.id);
  try{const {skill:method}=await api('/api/v1/skills/'+encodeURIComponent(skill.key)+'?'+new URLSearchParams({project_id:pid}));if(!valid())return;
    showDialog(method.name||method.id,[method.description,...method.principles,...method.procedure].filter(Boolean).join('\n\n'));
    for(const resource of method.resources||[])dialogAction(resource.description||resource.id,async()=>{
      const current=loadingDialog(resource.description||resource.id);try{const page=await api('/api/v1/skills/'+encodeURIComponent(skill.key)+'/resource?'+new URLSearchParams({project_id:pid,resource_id:resource.id,max_chars:'20000'}));if(current()){showDialog(resource.description||resource.id,page.content);if(page.next_offset!==null)appendSkillResourcePage(pid,skill,resource,page);}}catch(e){if(current())showToast(e.message);}});
  }catch(e){if(valid())showToast(e.message);}
}
function appendSkillResourcePage(pid,skill,resource,page){
  dialogAction('继续读取',async()=>{const version=app.dialogVersion,valid=()=>app.id===pid&&version===app.dialogVersion&&!$('detailModal').hidden;try{const next=await api('/api/v1/skills/'+encodeURIComponent(skill.key)+'/resource?'+new URLSearchParams({project_id:pid,resource_id:resource.id,offset:String(page.next_offset),expected_sha256:page.sha256,max_chars:'20000'}));if(valid()){showDialog(resource.description||resource.id,next.content);if(next.next_offset!==null)appendSkillResourcePage(pid,skill,resource,next);}}catch(e){if(valid())showToast(e.message);}});
}

const submissionLabels={submitted:'已投稿',with_editor:'编辑处理中',under_review:'同行评审中',major_revision:'大修',minor_revision:'小修',accepted:'已录用',published:'已发表',rejected:'已拒稿',withdrawn:'已撤稿'};
async function openSubmissions(pid,c=null){
  const valid=loadingDialog('投稿记录与材料');
  try{const {submissions}=await api(base(pid)+'/submissions');if(!valid())return;
    showDialog('投稿记录与材料','这里只登记已有投稿及其来源，不执行对外投稿。');const host=$('detailText');
    for(const sub of submissions){
      const row=document.createElement('p');row.textContent=`${sub.title} · ${sub.journal_name||'刊会待补'} · ${sub.manuscript_id||'编号待补'} · ${submissionLabels[sub.status]} · 第 ${sub.revision_round} 轮返修${sub.submitted_at?' · '+sub.submitted_at:''}`;
      const view=document.createElement('button');view.className='button-light';view.textContent='查看与处理';view.onclick=()=>openSubmissionDetail(pid,sub,c);row.append(view);host.append(row);
    }
    if(!submissions.length){const empty=document.createElement('p');empty.textContent='尚无本研究的投稿记录。';host.append(empty);}
    dialogAction('登记已有投稿',()=>createSubmissionForm(pid,c));
    dialogAction('导入投稿邮件内容',()=>submissionEmailForm(pid,c));
    dialogAction('生成投稿材料包',()=>openDeliveryTools(pid,c,'submission'));
    dialogAction('下载已有文件',()=>openResearchFiles(pid,c));
  }catch(e){if(valid())showToast(e.message);}
}
function createSubmissionForm(pid,c=null,previous=null){
  formDialog(previous?'登记改投后的新投稿':'登记已有投稿',[
    {name:'title',label:'论文标题',value:previous?.title||app.draft?.name||'',required:true},
    {name:'journal_name',label:'期刊或会议',required:true},
    {name:'manuscript_id',label:'稿件编号（可稍后补充）'},
    {name:'submitted_at',label:'实际投稿日期（未知可留空）',type:'date'},
    {name:'status',label:'当前有依据的状态',type:'select',options:Object.entries(submissionLabels).map(([value,label])=>({value,label})),value:'submitted'}
  ],'保存投稿记录',async(data,current)=>{
    if(!data.submitted_at)data.submitted_at=null;if(previous)data.previous_submission_id=previous.submission_id;
    const sub=await post(base(pid)+'/submissions',data);
    if(c?.code==='P3'&&c.status==='active')await linkCaseSubmission(pid,c,sub.submission_id);
    if(current()){await openSubmissions(pid,c?await api(base(pid)+'/workflow-cases/'+c.case_id):null);await renderWorkflowCases();await loadUpdates();}
  });
}
async function linkCaseSubmission(pid,c,submissionId){
  const fresh=await api(base(pid)+'/workflow-cases/'+c.case_id);
  return api(base(pid)+'/workflow-cases/'+c.case_id,{method:'PATCH',body:JSON.stringify({operation:'submission',data:{submission_id:submissionId},expected_revision:fresh.revision})});
}
function openSubmissionDetail(pid,sub,c){
  showDialog(sub.title,`${sub.journal_name}\n稿件编号：${sub.manuscript_id||'待补充'}\n状态：${submissionLabels[sub.status]}\n实际投稿日期：${sub.submitted_at||'待补充'}\n返修轮次：${sub.revision_round}\n\n`+
    sub.status_history.map(h=>`${stamp(h.at)} · ${submissionLabels[h.status]}\n依据：${h.note||h.source}`).join('\n\n'));
  if(sub.allowed_statuses.length)dialogAction('记录新的编辑决定',()=>{
    formDialog('按真实来源更新投稿状态',[
      {name:'status',label:'新状态',type:'select',options:sub.allowed_statuses.map(value=>({value,label:submissionLabels[value]}))},
      {name:'source_note',label:'决定依据或邮件内容摘要',type:'textarea',required:true}
    ],'保存状态',async(data,current)=>{
      const result=await post(base(pid)+'/submissions/'+sub.submission_id+'/status',{...data,expected_status:sub.status});
      if(current()){openSubmissionDetail(pid,result,c);await renderWorkflowCases();await loadUpdates();}
    });
  });
  dialogAction('按本轮意见准备修改与回复',async()=>{
    try{if(c?.code==='P3'&&c.status==='active'){const linked=await linkCaseSubmission(pid,c,sub.submission_id);if(app.id!==pid)return;selectCase(pid,linked);await renderWorkflowCases();}
    else{savePreference(pid,'case_id','');savePreference(pid,'workflow','submission_tracking');$('activeWorkflow').value='submission_tracking';}
    closeDialog();await sendRequest('请处理本研究投稿记录 '+sub.submission_id+'（'+sub.journal_name+'，稿件编号 '+sub.manuscript_id+'，当前 '+submissionLabels[sub.status]+'，返修轮次 '+sub.revision_round+'）。依据已提供的真实审稿意见制定修改策略，准备修改稿与逐条回复；如尚未提供意见，先请求意见原文。不得声称已经完成未做的修改。');}catch(e){if(app.id===pid)showToast(e.message);}
  });
  if(sub.status==='rejected'||sub.status==='withdrawn'){
    dialogAction('重新匹配投稿目标',()=>{savePreference(pid,'case_id','');savePreference(pid,'workflow','venue_matching');$('activeWorkflow').value='venue_matching';closeDialog();sendRequest('原投稿 '+sub.submission_id+' 已'+submissionLabels[sub.status]+'。请依据当前论文、原决定和作者约束重新匹配 3—5 个期刊或会议目标，核对当前官方指南，不执行投稿。');});
    dialogAction('登记实际改投记录',()=>createSubmissionForm(pid,c,sub));
  }
  if(c?.code==='P3'&&c.status==='active')dialogAction('关联到这份交付记录',async()=>{try{await linkCaseSubmission(pid,c,sub.submission_id);if(app.id===pid){showToast('已关联投稿记录，后续对话可读取状态与轮次');await renderWorkflowCases();}}catch(e){showToast(e.message);}});
  dialogAction('返回投稿列表',()=>openSubmissions(pid,c));
}
function submissionEmailForm(pid,c){
  formDialog('读取用户提供的投稿邮件',[
    {name:'subject',label:'邮件主题'},
    {name:'text',label:'邮件正文',type:'textarea',required:true}
  ],'解析并核对',async(data,current)=>{
    const {parsed}=await post(base(pid)+'/submission-email',data);if(!current())return;
    showDialog('核对邮件解析结果',`稿件编号：${parsed.manuscript_id||'未识别'}\n刊会：${parsed.journal_name||'未识别'}\n状态：${submissionLabels[parsed.status]||'未明确识别'}\n\n只有确认匹配实际投稿后才应用；不会连接或发送邮件。`);
    if(parsed.manuscript_id&&parsed.status)dialogAction('确认应用到本研究',async()=>{try{
      const {submission}=await post(base(pid)+'/submission-email',{...data,apply:true});
      if(c?.code==='P3'&&c.status==='active')await linkCaseSubmission(pid,c,submission.submission_id);
      if(app.id===pid){await openSubmissions(pid,c);await renderWorkflowCases();await loadUpdates();}
    }catch(e){showToast(e.message);}});
    dialogAction('返回投稿列表',()=>openSubmissions(pid,c));
  });
}

async function openCloudSync(pid){
  const valid=loadingDialog('云同步设置与阶段历史');
  try{
    const data=await api(base(pid)+'/sync');if(!valid())return;
    const binding=data.binding;
    showDialog('云同步设置与阶段历史',binding?`本地项目：${pid}\n云端空间：${binding.space_id}\n云地址：${binding.cloud_url}\n同步：${binding.sync_mode==='cloud_sync'?'已启用':'仅本地'}\n待同步事件：${data.pending}`:'当前项目尚未绑定云端空间。绑定仅同步生命周期状态与阶段历史，不上传稿件和文件。请先在本机配置云地址、登录令牌和设备凭据。');
    if(!binding){
      dialogAction('绑定此项目',()=>formDialog('绑定云端空间',[
        {name:'cloud_url',label:'云地址',required:true},
        {name:'space_id',label:'空间 ID',required:true},
        {name:'device_id',label:'设备 ID',required:true}
      ],'获取绑定确认',async values=>{
        const challenge=await post(base(pid)+'/sync/bind',values);
        if(!confirm(`确认将本地项目 ${pid} 绑定到 ${values.cloud_url} 的空间 ${values.space_id}？绑定后仍默认仅本地。`))return;
        await post(base(pid)+'/sync/bind',{...values,challenge:challenge.challenge,confirmed:true});
        await openCloudSync(pid);
      }));
    }else{
      dialogAction('云端研究任务与监测',()=>openCloudTasks(pid));
      dialogAction(binding.sync_mode==='cloud_sync'?'关闭云同步':'启用云同步',async()=>{
        const mode=binding.sync_mode==='cloud_sync'?'local_only':'cloud_sync';
        if(mode==='cloud_sync'&&!confirm('启用后将上传本项目的生命周期状态、原因和阶段历史，并拉取其他设备的阶段变更。确认启用？'))return;
        try{await post(base(pid)+'/sync/mode',{sync_mode:mode,confirmed:mode==='cloud_sync'});await openCloudSync(pid);}catch(e){showToast(e.message);}
      });
      dialogAction('关注与通知同步范围',()=>{
        const pref=binding.sync_preferences||{};
        formDialog('关注与通知同步范围',[
          {name:'intents',label:'同步此项目的关注意图',type:'checkbox',value:!!pref.intents},
          {name:'profile_intents',label:'同时同步全局个人关注意图',type:'checkbox',value:!!pref.profile_intents},
          {name:'notifications',label:'转发现有及后续本机通知（可能含跨项目摘要；每台本机仅选一个转发空间）',type:'checkbox',value:!!pref.notifications}
        ],'保存同步范围',async values=>{
          if((values.intents||values.profile_intents||values.notifications)&&!confirm('确认按勾选范围上传关注设置或本机通知？云同步总开关关闭时不会上传。'))return;
          await post(base(pid)+'/sync/preferences',{...values,confirmed:true});await openCloudSync(pid);
        });
      });
      if(data.proactive_sync?.pending)dialogAction(`待转发通知 ${data.proactive_sync.pending} 条（失败后自动退避）`,()=>showToast('本地通知已保存；联网后在后台重试，不会重复投递。'));
      if(binding.cloud_mode_pending)dialogAction('云端关闭未确认：重试关闭',async()=>{try{await post(base(pid)+'/sync/mode',{sync_mode:'local_only'});await openCloudSync(pid);}catch(e){showToast(e.message);}});
      dialogAction('立即同步',async()=>{try{await post(base(pid)+'/sync/run');await openProject(pid);await openCloudSync(pid);}catch(e){showToast(e.message);}});
    }
    dialogAction('查看全部阶段历史',()=>showDialog('阶段历史（按服务器接收序号，未同步事件在后）',data.history.map(h=>`${h.server_seq?'#'+h.server_seq:'本地'} ${h.after?.stage||''} · ${h.reason}${h.conflict?' [与前序状态冲突，已保留]':''}`).join('\n')||'暂无阶段历史'));
  }catch(e){if(valid())showToast(e.message);}
}


async function openCloudTasks(pid){
  const valid=loadingDialog('云端研究任务与监测');
  try{
    const data=await api(base(pid)+'/cloud-tasks');if(!valid())return;
    showDialog('云端研究任务与监测',`本机执行与接收：${data.enabled?'已启用':'已暂停'}\n云端决定下一步，本机仅执行已授权文件的读取与检查。离线时任务等待本机上线。此开关独立于阶段同步。`);
    dialogAction('回到对话提出需求',()=>{closeDialog();workTab('discussion');$('workQuestion').focus();});
    dialogAction('添加云端公开文献监测',()=>formDialog('公开文献监测',[
      {name:'topic',label:'发送到公开文献源的检索主题',required:true}
    ],'确认每天监测',async(values,current)=>{
      if(!confirm('此主题将发送到云端及公开文献源；本机关机时云端仍会监测，上线后接收提醒。'))return;
      await post(base(pid)+'/cloud-monitors',{...values,confirmed:true});if(current())await openCloudTasks(pid);
    }));
    dialogAction(data.enabled?'暂停本机执行与接收':'恢复本机执行与接收',async()=>{try{await post(base(pid)+'/cloud-tasks/enabled?enabled='+!data.enabled);await openCloudTasks(pid);}catch(e){showToast(e.message);}});
    dialogAction('立即检查进度',async()=>{try{await post(base(pid)+'/cloud-tasks/poll');await loadUpdates();await openCloudTasks(pid);}catch(e){showToast(e.message);}});
    for(const [id,task] of Object.entries(data.tasks))dialogAction(`任务 ${id.slice(0,8)} · ${task.status}`,async()=>{
      const current=loadingDialog('云端任务');try{const result=await api(base(pid)+'/cloud-tasks/'+id);if(!current())return;showDialog('云端任务',`${result.status}\n${result.answer||'等待后续执行'}`);
      if(!['succeeded','failed','cancelled'].includes(result.status))dialogAction('取消任务',async()=>{try{await post(base(pid)+'/cloud-tasks/'+id+'/cancel');await openCloudTasks(pid);}catch(e){showToast(e.message);}});
      dialogAction('返回任务列表',()=>openCloudTasks(pid));}catch(e){if(current())showToast(e.message);}
    });
    for(const [id,topic] of Object.entries(data.monitors||{}))dialogAction('停止监测：'+topic,async()=>{try{await api(base(pid)+'/cloud-monitors/'+id,{method:'DELETE'});showToast('云端监测已停止');}catch(e){showToast(e.message);}});
    for(const id of Object.keys(data.grants))dialogAction('撤销材料授权 '+id.slice(0,8),async()=>{try{await api(base(pid)+'/cloud-grants/'+id,{method:'DELETE'});await openCloudTasks(pid);}catch(e){showToast(e.message);}});
    dialogAction('审阅稿件提案',()=>openCloudProposals(pid));
  }catch(e){if(valid())showToast(e.message);}
}
async function openCloudProposals(pid){
  const valid=loadingDialog('稿件提案');try{
    const data=await api(base(pid)+'/cloud-proposals');if(!valid())return;
    showDialog('稿件提案','提案保存在本机；审阅全文后才能应用。撤回只在稿件未被继续修改时执行。');
    for(const proposal of data.proposals)dialogAction(proposal.id+' · '+proposal.status,()=>{
      showDialog('审阅提案',proposal.proposed);
      if(proposal.status==='ready'||proposal.status==='applying')dialogAction('确认应用此提案',async()=>{try{if(!confirm('确认用此提案替换授权版本的稿件？'))return;await post(base(pid)+'/cloud-proposals/'+proposal.id+'/apply?confirmed=true');await openCloudProposals(pid);}catch(e){showToast(e.message);}});
      if(proposal.status==='applied'||proposal.status==='undoing')dialogAction('撤回此提案',async()=>{try{if(!confirm('确认恢复应用前的稿件？'))return;await post(base(pid)+'/cloud-proposals/'+proposal.id+'/undo?confirmed=true');await openCloudProposals(pid);}catch(e){showToast(e.message);}});
      dialogAction('返回提案列表',()=>openCloudProposals(pid));
    });
    dialogAction('返回任务列表',()=>openCloudTasks(pid));
  }catch(e){if(valid())showToast(e.message);}
}
