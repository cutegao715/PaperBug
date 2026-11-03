/* Lifecycle controls share the existing room and API helper. */
const skillCapabilityLabels = {general:'通用方法',lifecycle_specific:'生命周期专用'};
function skillCapabilityLabel(skill) { return skillCapabilityLabels[skill.capability_type] || '未分类'; }
let lifecycleCatalog = [];
let skillPreviewVersion = 0;
let workflowPreviewVersion = 0;

function renderSkillPalette(skills, projectId, preserveDetail=false) {
  if (!$('skillPalette')) return;
  const details=$('skillDetails');
  const keep=preserveDetail && details?.dataset.projectId===projectId && skills.some(s=>s.key===details.dataset.skillKey);
  if(!keep){
    skillPreviewVersion++;
    details?.replaceChildren();
    if(details){details.dataset.projectId='';details.dataset.skillKey='';}
  }
  const labels = {builtin:'系统共享',shared:'共享扩展',workspace:'本空间私有'};
  $('skillPalette').innerHTML = skills.map((s,i)=>{
    const name=(s.name && s.name!==s.id) ? s.name : ((s.triggers||[]).find(t=>/[\u4e00-\u9fff]/.test(t))||s.id);
    const advice=(s.workflow_recommendations||[]).map(r=>r.when).join('；');
    return `<button class="tinytag" data-skill-index="${i}" title="${esc(s.key+' '+advice)}">${esc(name)} · ${esc(skillCapabilityLabel(s))} · ${esc(labels[s.source_kind]||'共享')}${advice?' · 流程建议':''}</button>`;
  }).join('') || '专业能力将按对话内容匹配';
  for(const button of $('skillPalette').querySelectorAll('[data-skill-index]')) button.onclick=async()=>{
    const selected=skills[Number(button.dataset.skillIndex)], version=++skillPreviewVersion;
    details.dataset.projectId=projectId;details.dataset.skillKey=selected.key;
    try {
      const data=await api(`/api/v1/skills/${encodeURIComponent(selected.key)}?project_id=${encodeURIComponent(projectId)}`);
      if(currentWorkspaceId()!==projectId || version!==skillPreviewVersion || !details.isConnected || details.dataset.skillKey!==selected.key)return;
      const target=$('skillDetails');target.replaceChildren();
      const heading=document.createElement('p');heading.textContent=`${skillCapabilityLabel(data.skill)} · ${labels[data.skill.source_kind]} · ${data.skill.id} @ ${data.skill.version}`;
      target.append(heading);
      for(const [label,value] of [
        ['说明',data.skill.description],['适用线索',data.skill.triggers],
        ['流程建议条件',(selected.workflow_recommendations||[]).map(r=>r.when)],
        ['原则',data.skill.principles],['可选方法',data.skill.procedure],
        ['经验提示',data.skill.heuristics],['常见问题',data.skill.anti_patterns],
        ['检查要点',data.skill.checklist],
        ['使用说明',data.skill.instructions],
      ]) {
        if(!value || (Array.isArray(value)&&!value.length))continue;
        const title=document.createElement('b');title.textContent=label;
        const content=document.createElement('p');content.style.whiteSpace='pre-wrap';content.textContent=Array.isArray(value)?value.join('\n'):value;
        target.append(title,content);
      }
      if(selected.shadowed_sources?.length){
        const note=document.createElement('p');note.textContent='本空间或共享扩展提供了同标识、同版本的方法；原共享方法仍可通过目录中的来源标识读取。';target.append(note);
      }
      for(const resource of data.skill.resources||[]) {
        const button=document.createElement('button');button.className='secondary';
        button.textContent=resource.description||resource.id;button.title=resource.when||'';
        const content=document.createElement('pre');content.style.whiteSpace='pre-wrap';
        let offset=0, digest='';
        button.onclick=async()=>{
          button.disabled=true;
          try {
            const query=new URLSearchParams({project_id:projectId,resource_id:resource.id,offset:String(offset),expected_sha256:digest});
            const result=await api(`/api/v1/skills/${encodeURIComponent(selected.key)}/resource?${query}`);
            if(currentWorkspaceId()!==projectId||!button.isConnected)return;
            content.textContent+=result.content;digest=result.sha256;offset=result.next_offset;
            button.textContent=offset===null?'已读取：'+(resource.description||resource.id):'继续读取：'+(resource.description||resource.id);
          }catch(e){toast(e.message);}finally{button.disabled=offset===null;}
        };
        target.append(button,content);
      }
    } catch(e) { if(version===skillPreviewVersion)toast(e.message); }
  };
}

function currentWorkspaceId() {
  const project = selectedProject();
  return project ? (project.backendId || project.id) : null;
}
function lifecycleCreateFields() {
  const select = document.getElementById('createLifecycle');
  return select && $('createModal').classList.contains('show') && select.value
    ? {lifecycle_type: select.value, stage: 'topic'} : {};
}
function lifecycleRunFields() {
  const fields = {};
  const workflow = document.getElementById('workflowChoice');
  if (workflow?.value === 'off') fields.workflow_mode = 'off';
  else if (workflow?.value) fields.workflow_id = workflow.value;
  const scope = $('projectScope')?.value, delivery = $('projectDeliveryMode')?.value;
  if ($('projectScope') && !$('projectWorkflowOptions')?.hidden) {
    const claim_policy=$('projectClaimPolicy')?.value, report_date=$('projectReportDate')?.value;
    const options = {...(scope ? {scope} : {}), ...(delivery ? {delivery} : {}), ...(claim_policy?{claim_policy}:{}), ...(report_date?{report_date}:{})};
    if(Object.keys(options).length) fields.workflow_options = options;
  }
  return fields;
}
function clearRunPreferences() {
  for(const id of ['workflowChoice','projectScope','projectDeliveryMode','projectClaimPolicy','projectReportDate']) {
    if($(id)) $(id).value='';
  }
}

function renderLifecycleContext(detail) {
  renderResearchOutputs(detail);
  const panel = $('lifecycleContext');
  if (!panel || !detail || detail.project_id !== currentWorkspaceId()) return;
  ++workflowPreviewVersion;
  const saved = detail.lifecycle;
  const selectedKind = saved?.lifecycle_type || '';
  const same = panel.dataset.projectId===detail.project_id && panel.dataset.kind===selectedKind;
  const openPanels = same ? [...panel.querySelectorAll('details')].map(d=>d.open) : [];
  const controls = same ? Object.fromEntries(['workflowChoice','projectScope','projectDeliveryMode','projectClaimPolicy','projectReportDate'].map(id=>[id,$(id)?.value])) : {};
  panel.dataset.projectId=detail.project_id; panel.dataset.kind=selectedKind;
  panel.innerHTML = `<details style="padding:10px 16px;border-top:1px solid #e7d7c8">
    <summary>研究背景与资料 · ${esc(lifecycleCatalog.find(c=>c.lifecycle_type===selectedKind)?.name || '尚未设置')}</summary>
    <div class="field"><label for="lifecycleKind">生命周期</label><select id="lifecycleKind"><option value="">请选择</option>${lifecycleCatalog.map(c=>`<option value="${c.lifecycle_type}">${esc(c.name)}</option>`).join('')}</select></div>
    <div class="field"><label for="lifecycleStage">现实阶段</label><select id="lifecycleStage"></select><button class="secondary" id="saveLifecycle">保存阶段</button></div>
    <div class="field"><label for="conversationFocus">当前稿件</label><select id="conversationFocus"><option value="">暂不指定稿件</option>${(detail.drafts||[]).map(f=>`<option value="${esc(f)}">${esc(f)}</option>`).join('')}</select></div>
    <div class="field"><label for="workflowChoice">工作流（可选）</label><select id="workflowChoice"></select></div>
    <p style="font-size:12px">流程按本空间背景推荐专业能力；可跳过、换用或直接使用 Skill，没有固定调用顺序。</p>
    <p id="workflowDescription" style="font-size:12px;white-space:pre-wrap"></p>
    ${selectedKind==='project'?`<div id="projectWorkflowOptions"><div class="field"><label for="projectScope">本次范围</label><select id="projectScope"><option value="">根据请求判断</option><option value="partial">局部修改</option><option value="full">完整文档</option></select><label for="projectDeliveryMode">交付要求</label><select id="projectDeliveryMode"><option value="">根据请求判断</option><option value="draft">工作草稿（允许待补信息）</option><option value="ready">提交准备（检查适用要求）</option></select></div>`:''}
    ${selectedKind==='project'?'<div class="field"><label for="projectClaimPolicy">关键声明核查</label><select id="projectClaimPolicy"><option value="">按交付要求判断</option><option value="advisory">仅提示</option><option value="traceable">关键声明有来源</option><option value="reviewed">逐条人工核查</option></select><label for="projectReportDate">报告截止日期（可选）</label><input id="projectReportDate" type="date"></div>':''}
    ${selectedKind==='project'?'</div>':''}
    <p id="conversationNote" style="font-size:12px;line-height:1.6"></p>
    ${detail.conversation?.can_resume?'<button class="secondary" id="resumeConversation">继续上次未完成的工作</button>':''}
    <details><summary>项目记忆与已确认决定</summary><div id="memoryRecords"></div><button class="secondary" id="addProjectMemory">记录决定</button></details>
    ${selectedKind==='project'?'<details id="projectDossierPanel"><summary>申请、中期与结题的衔接资料</summary><div id="projectDossier"></div></details>':''}
  </details>`;
  $('lifecycleKind').value = selectedKind;
  function fillStages() {
    const spec = lifecycleCatalog.find(c=>c.lifecycle_type===$('lifecycleKind').value);
    $('lifecycleStage').innerHTML = (spec?.stages||[]).map(s=>`<option value="${s.id}">${esc(s.name)}</option>`).join('');
    $('workflowChoice').innerHTML = '<option value="">按需使用，日常交流直接处理</option><option value="off">本轮不使用交付流程</option>' + (spec?.workflows||[]).map(s=>`<option value="${s.id}">${esc(s.name)}</option>`).join('');
    const desired=controls.workflowChoice!==undefined?controls.workflowChoice:(detail.conversation?.workflow_mode==='off'?'off':detail.conversation?.workflow_id||'');
    if(desired && desired!=='off' && !(spec?.workflows||[]).some(w=>w.id===desired)){
      const legacy=(spec?.stages||[]).find(s=>s.workflow_id===desired);
      if(legacy)$('workflowChoice').insertAdjacentHTML('beforeend',`<option value="${esc(desired)}">历史配置 · ${esc(legacy.name)}（兼容）</option>`);
    }
    if([...$('workflowChoice').options].some(o=>o.value===desired))$('workflowChoice').value=desired;
    if (saved && spec?.lifecycle_type===saved.lifecycle_type) $('lifecycleStage').value=saved.stage;
  }
  $('lifecycleKind').onchange=fillStages;
  fillStages();
  for(const [id,value] of Object.entries(controls)) if($(id) && value!==undefined) $(id).value=value;
  [...panel.querySelectorAll('details')].forEach((d,i)=>{d.open=!!openPanels[i];});
  const conversation=detail.conversation||{};
  $('conversationFocus').value=conversation.target?.filename||'';
  $('conversationFocus').onchange=async()=>{
    try{await api(`/api/v1/projects/${encodeURIComponent(detail.project_id)}/conversation/focus`,{method:'POST',body:JSON.stringify({filename:$('conversationFocus').value||null})});await refreshProjectDetail();}
    catch(e){toast(e.message);}
  };
  $('conversationNote').textContent=conversation.can_resume
    ? '上次运行尚未完成。可以继续，也可以直接讨论其他问题；原稿和执行记录已保留。'
    : '直接对话即可开始。专业能力按需调用；写作完成不会自动改变现实阶段。';
  if($('resumeConversation')) $('resumeConversation').onclick=()=>startAgentRun('继续',{resume:true});
  renderSkillPalette(detail.skills||[], detail.project_id);
  function describeWorkflow(workflow=null){
    const spec=(lifecycleCatalog.find(c=>c.lifecycle_type===$('lifecycleKind').value)?.workflows||[]).find(w=>w.id===$('workflowChoice').value);
    const capabilities=workflow?.capability_status||spec||{};
    $('workflowDescription').textContent=[workflow?.description||spec?.description||'',
      capabilities.available_capabilities?.length?'当前可用：'+capabilities.available_capabilities.join('；'):'',
      capabilities.planned_capabilities?.length?'后续接入：'+capabilities.planned_capabilities.join('；'):''].filter(Boolean).join('\n');
    if($('projectWorkflowOptions'))$('projectWorkflowOptions').hidden=spec?.mode==='discovery';
  }
  async function previewSkills() {
    const choice=$('workflowChoice').value, version=++workflowPreviewVersion;
    describeWorkflow();
    try {
      const body={goal:conversation.request||detail.goal||'', workflow_mode:choice==='off'?'off':'auto',
        ...(choice && choice!=='off'?{workflow_id:choice}:{})};
      const data=await api(`/api/v1/projects/${encodeURIComponent(detail.project_id)}/workflows/preview`,{method:'POST',body:JSON.stringify(body)});
      if(currentWorkspaceId()===detail.project_id && version===workflowPreviewVersion && $('workflowChoice')?.value===choice){
        const skills=[...new Map([...(data.skills||[]),...(data.workflow_skills||[])].map(s=>[s.key,s])).values()];
        renderSkillPalette(skills,detail.project_id,true);describeWorkflow(data.workflow);
      }
    }catch(e){if(version===workflowPreviewVersion)toast(e.message);}
  }
  $('workflowChoice').onchange=previewSkills;
  describeWorkflow();
  if($('workflowChoice').value)previewSkills();
  $('saveLifecycle').onclick=async()=>{
    const kind=$('lifecycleKind').value, stage=$('lifecycleStage').value;
    if(!kind||!stage){toast('请选择生命周期与阶段');return;}
    const reason=prompt('这次阶段设置或变更的原因：','更新实际研究进展');
    if(!reason?.trim())return;
    try {
      await api(`/api/v1/projects/${encodeURIComponent(detail.project_id)}/lifecycle`,{method:'POST',body:JSON.stringify({lifecycle_type:kind,stage,reason})});
      await refreshProjectDetail(); toast('阶段已保存');
    } catch(e){toast(e.message);}
  };
  $('addProjectMemory').onclick=async()=>{
    const content=prompt('记录已确认的项目决定或要求：');
    if(!content?.trim())return;
    try{
      await api('/api/v1/memories',{method:'POST',body:JSON.stringify({layer:'project',kind:'decision',content,scope:{project_id:detail.project_id},pinned:true,confirmation:'provided',source:{type:'user',surface:'project_memory'}})});
      await renderProjectMemories(detail.project_id);
    }catch(e){toast(e.message);}
  };
  renderProjectMemories(detail.project_id);
  if(selectedKind==='project') renderProjectDossier(detail);
  renderManuscriptContext(detail);
}

async function renderProjectMemories(projectId) {
  try {
    const data=await api(`/api/v1/projects/${encodeURIComponent(projectId)}/memories`);
    if(currentWorkspaceId()!==projectId || !$('memoryRecords'))return;
    const records=data.memories||[];
    const container=$('memoryRecords');container.replaceChildren();
    if(!records.length)container.textContent='尚无已记录的项目记忆。';
    for(const memory of records){
      const row=document.createElement('div');row.className='research-card';
      const text=document.createElement('p');text.textContent=memory.content;row.appendChild(text);
      const source=document.createElement('small');source.textContent=`${memory.kind} · ${memory.confirmation} · ${memory.source?.type||'旧记录'}`;row.appendChild(source);
      const edit=document.createElement('button');edit.textContent='更正';edit.className='secondary';
      edit.onclick=async()=>{const content=prompt('更正后内容（保留替代历史）：',memory.content);if(!content?.trim())return;try{await api(`/api/v1/memories/${memory.memory_id}`,{method:'PATCH',body:JSON.stringify({content,source:{type:'user',surface:'project_memory'}})});await renderProjectMemories(projectId);}catch(e){toast(e.message);}};
      const remove=document.createElement('button');remove.textContent='移出记忆';remove.className='secondary';
      remove.onclick=async()=>{try{await api(`/api/v1/memories/${memory.memory_id}`,{method:'DELETE'});await renderProjectMemories(projectId);}catch(e){toast(e.message);}};
      row.append(edit,remove);container.appendChild(row);
    }
  } catch(e){toast('读取项目记忆失败：'+e.message);}
}

async function renderProjectDossier(detail) {
  const projectId=detail.project_id, base=`/api/v1/projects/${encodeURIComponent(projectId)}/dossier`;
  const previousCheck={stage:$('projectCheckStage')?.value,filename:$('projectCheckFile')?.value};
  try {
    const data=await api(base);
    if(currentWorkspaceId()!==projectId || !$('projectDossier'))return;
    const root=$('projectDossier'), records=data.active_records||[];
    const stages={application:'项目申请',midterm:'中期进展',final:'项目结题'};
    const kinds={objective:'目标与预期成果',progress:'实际进展',output:'实际成果',decision:'决定与调整',issue:'问题与限制',next_plan:'后续计划',budget:'经费情况'};
    const roles={application:'申请材料',baseline:'申请/任务书基线',midterm:'中期报告',final:'结题报告',template_application:'申请模板',template_midterm:'中期模板',template_final:'结题模板'};
    const opts=items=>Object.entries(items).map(([key,label])=>`<option value="${key}">${esc(label)}</option>`).join('');
    const drafts=(detail.drafts||[]).map(name=>`<option value="${esc(name)}">${esc(name)}</option>`).join('');
    root.innerHTML=`<p style="font-size:12px">固定采用的材料版本会跨阶段保留。采纳表示你认可这条记录，不代表外部审批或独立核验。目标更正后，相关进展需重新核对。</p>
      <div id="projectBindings"></div>
      <div class="field"><label>材料用途</label><select id="projectBindingRole">${opts(roles)}</select><label>文档</label><select id="projectBindingFile">${drafts}</select><label>模板要求的章节（每行一项，可留空）</label><textarea id="projectRequiredSections" rows="2"></textarea><button class="secondary" id="bindProjectArtifact">采用此版本</button></div>
      <div id="projectRecordList"></div>
      <details><summary id="projectRecordEditorTitle">新增项目记录</summary><div class="field">
      <label>阶段</label><select id="projectRecordStage">${opts(stages)}</select><label>记录类型</label><select id="projectRecordKind">${opts(kinds)}</select>
      <label>事实、决定或问题</label><textarea id="projectRecordContent" rows="3"></textarea>
      <label>来源类别</label><select id="projectRecordSourceChoice"><option value="note">用户说明</option>${(detail.materials||[]).map(m=>`<option value="material:${esc(m.material_id)}">材料：${esc(m.filename)}</option>`).join('')}${(detail.drafts||[]).map(f=>`<option value="draft:${esc(f)}">草稿版本：${esc(f)}</option>`).join('')}</select>
      <label>来源说明（原话、文件和页码等）</label><textarea id="projectRecordSource" rows="2"></textarea>
      <label>关联目标或前期记录（可多选）</label><select id="projectRecordRelations" multiple size="3">${records.map(r=>`<option value="${r.record_id}">${esc(r.content.slice(0,70))}</option>`).join('')}</select>
      <details id="projectCriteriaFields"><summary>目标验收条件（按需填写）</summary><label>成果类型</label><input id="projectOutputType"><label>目标数量</label><input id="projectTargetQuantity" type="number" min="0" step="any"><label>单位</label><input id="projectTargetUnit"><label>期限</label><input id="projectDueDate" type="date"><label>验收说明</label><textarea id="projectAcceptanceText" rows="2"></textarea><label>依据要求</label><select id="projectEvidenceRequirement"><option value="any_source">允许用户说明或材料</option><option value="documented">需要材料或证据卡</option></select></details>
      <details id="projectMeasurementFields"><summary>本次累计观测（按需填写，不重复相加）</summary><label>对应目标</label><select id="projectMeasuredGoal"><option value="">不填写</option>${records.filter(r=>r.kind==='objective'&&r.status==='confirmed').map(r=>`<option value="${r.record_id}">${esc(r.content.slice(0,60))}</option>`).join('')}</select><label>截至日期</label><input id="projectMeasuredDate" type="date"><label>累计数量</label><input id="projectMeasuredQuantity" type="number" min="0" step="any"><label>单位</label><input id="projectMeasuredUnit"><label>实际状态</label><select id="projectCompletion"><option value="in_progress">进行中</option><option value="not_started">尚未开始</option><option value="completed">已完成</option><option value="not_completed">未完成</option><option value="not_applicable">不适用</option></select><label>状态说明（未完成/不适用时必填）</label><textarea id="projectMeasurementNote" rows="2"></textarea></details>
      <label>记录或更正原因</label><input id="projectRecordReason" value="用户补充项目资料">
      <button class="secondary" id="saveProjectRecord">保存并采纳</button><button class="secondary" id="resetProjectRecord">清空</button></div></details>
      <div class="field"><label>检查文档</label><select id="projectCheckFile">${drafts}</select><label>适用阶段</label><select id="projectCheckStage">${opts(stages)}</select><button class="secondary" id="checkProjectDocument">按本次范围与交付要求检查</button><button class="secondary" id="saveProjectAssessment">保存核查清单</button><div id="projectCheckResult" style="white-space:pre-wrap;font-size:12px"></div><div id="projectAcceptanceMatrix"></div><div id="projectClaimsList"></div></div>
      <small>已保存 ${data.deliveries.length} 次文档交付；${data.records.length} 条记录版本。原记录和旧绑定保留供追溯。</small>`;
    const stage=stages[detail.lifecycle?.stage]?detail.lifecycle.stage:'application';
    $('projectRecordStage').value=stage;$('projectCheckStage').value=stage;
    if(stages[previousCheck.stage])$('projectCheckStage').value=previousCheck.stage;
    if((detail.drafts||[]).includes(previousCheck.filename))$('projectCheckFile').value=previousCheck.filename;
    $('projectBindingRole').value=stage==='application'?'baseline':stage;
    for(const [role,b] of Object.entries(data.current_bindings||{})){
      const p=document.createElement('p');p.style.fontSize='12px';
      p.textContent=`${roles[role]}：${b.filename} · ${b.version.slice(0,8)}${b.working_copy_changed?'（工作副本已改动，继续引用绑定版本）':''}`;
      const release=document.createElement('button');release.className='secondary';release.textContent='解除绑定';
      release.onclick=()=>{const reason=prompt('解除此用途绑定的原因（文件和历史仍保留）：');if(reason?.trim())run(`/bindings/${role}/release`,{expected_binding_id:b.binding_id,reason});};p.appendChild(release);
      $('projectBindings').appendChild(p);
    }
    let editing=null;
    function showStructuredFields(){const kind=$('projectRecordKind').value;$('projectCriteriaFields').hidden=kind!=='objective';$('projectMeasurementFields').hidden=!['progress','output'].includes(kind);}
    $('projectRecordKind').onchange=showStructuredFields;showStructuredFields();
    const run=async(path,body)=>{try{await api(base+path,{method:'POST',body:JSON.stringify(body)});await renderProjectDossier(detail);}catch(e){toast(e.message);}};
    $('bindProjectArtifact').onclick=()=>{
      if(!$('projectBindingFile').value){toast('请先导入或撰写文档');return;}
      const reason=prompt('采用此版本的原因（作为基线不等于已经获批）：','采用当前材料作为后续工作的依据');if(!reason?.trim())return;
      const role=$('projectBindingRole').value;
      run('/bindings',{role,filename:$('projectBindingFile').value,reason,required_sections:role.startsWith('template_')?$('projectRequiredSections').value.split('\n').map(s=>s.trim()).filter(Boolean):[]});
    };
    for(const row of records){
      const card=document.createElement('div');card.className='research-card';
      const p=document.createElement('p');p.textContent=row.content;
      const note=document.createElement('small');note.textContent=`${stages[row.stage]} · ${kinds[row.kind]} · ${row.status==='confirmed'?'已采纳':'待采纳'} · v${row.revision}`;
      const sources=document.createElement('p');sources.style.fontSize='12px';sources.textContent='来源：'+row.sources.map(s=>`${s.key} ${s.locator||''}`).join('；');
      const edit=document.createElement('button');edit.className='secondary';edit.textContent='更正 / 复核关联';
      edit.onclick=()=>{editing=row;$('projectRecordEditorTitle').parentElement.open=true;$('projectRecordEditorTitle').textContent='更正项目记录';$('projectRecordStage').value=row.stage;$('projectRecordKind').value=row.kind;$('projectRecordContent').value=row.content;$('projectRecordSource').value='';$('projectRecordSourceChoice').value='note';$('projectRecordReason').value='核对并更正项目记录';for(const o of $('projectRecordRelations').options)o.selected=row.relates_to.includes(o.value);
        const c=row.criteria||{},m=(row.measurements||[])[0]||{};
        for(const [id,value] of Object.entries({projectOutputType:c.output_type,projectTargetQuantity:c.quantity,projectTargetUnit:c.unit,projectDueDate:c.due_date,projectAcceptanceText:c.acceptance,projectEvidenceRequirement:c.evidence_requirement||'any_source',projectMeasuredGoal:m.objective_id,projectMeasuredDate:m.as_of,projectMeasuredQuantity:m.quantity,projectMeasuredUnit:m.unit,projectCompletion:m.completion||'in_progress',projectMeasurementNote:m.explanation}))$(id).value=value??'';
        showStructuredFields();};
      card.append(p,note,sources,edit);
      if(row.status==='proposed'){
        const adopt=document.createElement('button');adopt.className='secondary';adopt.textContent='采纳';adopt.onclick=()=>run(`/records/${row.record_id}/review`,{expected_revision:row.revision,status:'confirmed',reason:'用户核对并采纳候选记录'});card.appendChild(adopt);
      }
      const remove=document.createElement('button');remove.className='secondary';remove.textContent='撤回';remove.onclick=()=>{const reason=prompt('撤回原因：');if(reason?.trim())run(`/records/${row.record_id}/review`,{expected_revision:row.revision,status:'withdrawn',reason});};card.appendChild(remove);
      $('projectRecordList').appendChild(card);
    }
    $('resetProjectRecord').onclick=()=>renderProjectDossier(detail);
    $('saveProjectRecord').onclick=()=>{
      const content=$('projectRecordContent').value.trim(), source=$('projectRecordSource').value.trim(), reason=$('projectRecordReason').value.trim();
      const sourceChoice=$('projectRecordSourceChoice').value;
      if(!content||(!source&&!editing&&sourceChoice==='note')||!reason){toast('请填写内容、来源说明和原因');return;}
      const split=sourceChoice.indexOf(':');
      const sources=sourceChoice!=='note'?[{kind:sourceChoice.slice(0,split),key:sourceChoice.slice(split+1),locator:source}]:source?[{kind:'user_note',key:source}]:editing.sources.map(s=>({kind:s.kind,key:s.key,locator:s.locator||''}));
      const kind=$('projectRecordKind').value, relations=Array.from($('projectRecordRelations').selectedOptions,o=>o.value);
      const optionalNumber=id=>$(id).value===''?null:Number($(id).value);
      let criteria=null,measurements=[];
      if(kind==='objective')criteria={output_type:$('projectOutputType').value,quantity:optionalNumber('projectTargetQuantity'),unit:$('projectTargetUnit').value,due_date:$('projectDueDate').value||null,acceptance:$('projectAcceptanceText').value,evidence_requirement:$('projectEvidenceRequirement').value};
      const objective_id=$('projectMeasuredGoal').value;
      if(['progress','output'].includes(kind)&&objective_id){
        if(!$('projectMeasuredDate').value){toast('请填写累计观测的截至日期');return;}
        if(!relations.includes(objective_id))relations.push(objective_id);
        measurements=[...(editing?.measurements||[]).filter(m=>m.objective_id!==objective_id),{objective_id,as_of:$('projectMeasuredDate').value,quantity:optionalNumber('projectMeasuredQuantity'),unit:$('projectMeasuredUnit').value,completion:$('projectCompletion').value,explanation:$('projectMeasurementNote').value}];
      }
      run('/records',{kind,stage:$('projectRecordStage').value,content,sources,reason,relates_to:relations,criteria,measurements,...(editing?{record_id:editing.record_id,expected_revision:editing.revision}:{})});
    };
    const checkDocument=async(save_report=false)=>{
      const ids={application:'grant_application',midterm:'project_midterm_report',final:'project_final_report'};
      try{
        const options=lifecycleRunFields().workflow_options||{};
        const result=await api(base+'/check',{method:'POST',body:JSON.stringify({filename:$('projectCheckFile').value,workflow_id:ids[$('projectCheckStage').value],workflow_options:options,save_report})});
        if(currentWorkspaceId()!==projectId)return;
        $('projectCheckResult').textContent=[`检查模式：${result.contract.scope==='partial'?'局部':'全文'} / ${result.contract.delivery==='ready'?'提交准备':'草稿'}`,...result.report.errors.map(x=>'需处理：'+x),...result.report.warnings.map(x=>'提示：'+x)].join('\n');
        renderProjectAssessment(result,records,base,()=>checkDocument(false));
        if(save_report)toast('核查清单已保存，可在检查结果中下载');
      }catch(e){toast(e.message);}
    };
    $('checkProjectDocument').onclick=()=>checkDocument(false);
    $('saveProjectAssessment').onclick=()=>checkDocument(true);
  }catch(e){toast('读取阶段衔接资料失败：'+e.message);}
}

function renderProjectAssessment(result,records,base,refresh){
  const states={unreported:'尚未记录',qualitative_review:'需定性核查',meets_quantity:'已记录数量达到目标',shortfall:'已记录数量未达目标',needs_review:'需复核',not_applicable:'不适用',sourced:'已有来源，待核含义',supported:'用户核对支持',not_factual:'非成果事实',needs_evidence:'缺少依据',disputed:'存在争议'};
  const matrix=$('projectAcceptanceMatrix');matrix.replaceChildren();
  const title=document.createElement('h4');title.textContent='目标—进展—成果对照';matrix.appendChild(title);
  for(const item of result.acceptance.items){
    const row=document.createElement('div');row.className='research-card';
    const name=document.createElement('b');name.textContent=item.objective;
    const c=item.criteria,m=item.measurement||{};
    const values=document.createElement('p');values.textContent=`约定：${c.quantity??'未设数量'} ${c.unit||''}；截至 ${m.as_of||'未记录'}：${m.quantity??'未记录数量'} ${m.unit||''}。${states[item.state]||item.state}${item.past_due?'；约定期限已过':''}`;
    const note=document.createElement('small');note.textContent=[...item.blockers,...item.issues,c.acceptance||''].join('；');row.append(name,values,note);matrix.appendChild(row);
  }
  if(!result.acceptance.items.length){const empty=document.createElement('p');empty.textContent='尚无已采纳的项目目标，可先起草或按需补充。';matrix.appendChild(empty);}
  const list=$('projectClaimsList');list.replaceChildren();
  for(const file of result.exports||[]){if(file.download_url){const link=document.createElement('a');link.href=file.download_url;link.textContent=file.format==='review_markdown'?'下载核查清单':'下载核查数据';link.style.marginRight='12px';list.appendChild(link);}}
  const heading=document.createElement('h4');heading.textContent='关键声明核查';list.appendChild(heading);
  const limitation=document.createElement('p');limitation.style.fontSize='12px';limitation.textContent=result.claims.limitation;list.appendChild(limitation);
  for(const claim of result.claims.claims){
    const card=document.createElement('div');card.className='research-card';
    const content=document.createElement('p');content.textContent=`第 ${claim.line} 行：${claim.text}`;
    const status=document.createElement('small');status.textContent=states[claim.state]||claim.state;
    const sources=document.createElement('p');sources.style.fontSize='12px';sources.textContent=claim.source_summary.map(s=>`${s.content}（${s.sources.map(x=>x.key).join('；')}）`).join('\n')||'尚未关联可核对的项目记录';
    const select=document.createElement('select');select.multiple=true;select.size=3;select.setAttribute('aria-label','声明依据');
    for(const record of records.filter(r=>r.status==='confirmed')){const option=document.createElement('option');option.value=record.record_id;option.textContent=record.content;option.selected=claim.record_ids.includes(record.record_id);select.appendChild(option);}
    const verdict=document.createElement('select');verdict.setAttribute('aria-label','核查结论');
    for(const [value,label] of Object.entries({supported:'依据支持这条声明',needs_evidence:'需要补充依据',disputed:'声明与依据存在争议',not_factual:'不是成果事实（如计划或建议）'})){const option=document.createElement('option');option.value=value;option.textContent=label;verdict.appendChild(option);}
    verdict.value=claim.review?.verdict||'needs_evidence';
    const reason=document.createElement('input');reason.placeholder='核对说明（必填）';reason.setAttribute('aria-label','核对说明');
    const save=document.createElement('button');save.textContent='保存核查结论';save.className='secondary';
    save.onclick=async()=>{if(!reason.value.trim()){toast('请填写核对说明');return;}try{await api(base+'/claims/review',{method:'POST',body:JSON.stringify({filename:result.claims.filename,draft_version:result.claims.draft_version,claim_id:claim.claim_id,verdict:verdict.value,record_ids:Array.from(select.selectedOptions,o=>o.value),reason:reason.value})});await refresh();}catch(e){toast(e.message);}};
    card.append(content,status,sources,select,verdict,reason,save);list.appendChild(card);
  }
}

const refreshProjectDetailBeforeLifecycle = refreshProjectDetail;
refreshProjectDetail = async function(){
  await refreshProjectDetailBeforeLifecycle();
  renderLifecycleContext(state.projectDetail);
};
(async function initializeLifecycleControls(){
  try {
    lifecycleCatalog=(await api('/api/v1/lifecycles')).lifecycles;
    const field=document.createElement('div');field.className='field';
    field.innerHTML='<label for="createLifecycle">生命周期</label><select id="createLifecycle"><option value="">稍后设置</option>'+lifecycleCatalog.map(c=>`<option value="${c.lifecycle_type}">${esc(c.name)}</option>`).join('')+'</select>';
    $('blankForm').before(field);
    renderLifecycleContext(state.projectDetail);
  }catch(e){toast('加载生命周期目录失败：'+e.message);}
})();
