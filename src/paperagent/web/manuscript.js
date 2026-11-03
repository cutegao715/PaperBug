/* Paper knowledge reuses workspace sources and the existing conversation. */
let manuscriptViewRequest=0;
async function renderManuscriptContext(detail, force=false) {
  const host=$('manuscriptContext'), projectId=detail?.project_id;
  if(!host || !projectId || projectId!==currentWorkspaceId())return;
  if(!['paper_conference','thesis'].includes(detail.lifecycle?.lifecycle_type)) {
    ++manuscriptViewRequest;
    host.replaceChildren();host.dataset.projectId='';return;
  }
  if(!force && host.dataset.projectId===projectId && host.childElementCount)return;
  const request=++manuscriptViewRequest;
  try {
    const base=`/api/v1/projects/${encodeURIComponent(projectId)}/manuscript`;
    const data=await api(base);
    if(request!==manuscriptViewRequest || currentWorkspaceId()!==projectId)return;
    const wasOpen=force && host.querySelector('details')?.open;
    host.dataset.projectId=projectId;
    const profile=data.profile;
    const input=(id,label,value='')=>`<label style="display:block;margin:8px 0">${label}<input id="${id}" value="${esc(value)}" style="display:block;width:100%;box-sizing:border-box"></label>`;
    const area=(id,label,value='')=>`<label style="display:block;margin:8px 0">${label}<textarea id="${id}" rows="2" style="display:block;width:100%;box-sizing:border-box">${esc(value)}</textarea></label>`;
    host.innerHTML=`<details style="padding:12px 16px;border-top:1px solid #e7d7c8" ${wasOpen?'open':''}>
      <summary>论文知识 · 术语、论证与刊会要求</summary>
      <p style="font-size:12px">按需记录即可开始写作。已采纳表示作者确认使用，不等于证据已经独立核验。</p>
      <button class="secondary" id="reloadManuscript">刷新记录</button>
      <details><summary>论文与目标刊会</summary>
        ${input('paperLanguage','语言／变体（如 en、en-GB）',profile.language)}
        ${input('paperArticleType','文章类型',profile.article_type)}
        ${input('paperDesigns','研究设计（逗号分隔）',profile.study_designs.join(', '))}
        <label>刊会类型 <select id="paperVenueType"><option value="unspecified">未指定</option><option value="journal">期刊</option><option value="conference">会议</option></select></label>
        ${input('paperVenue','目标名称／会议届次',profile.venue_name)}
        ${input('paperTrack','栏目／Track',profile.track)}
        ${input('paperPublicationStage','出版阶段（可自定义）',profile.publication_stage)}
        <button class="secondary" id="savePaperProfile">保存背景</button>
      </details>
      <div id="manuscriptRecords"></div>
      <details id="manuscriptEditor"><summary>添加或更正记录</summary>
        <p id="paperEditLabel">新记录</p>
        <label>类别 <select id="paperRecordKind"><option value="term">术语</option><option value="argument">论证关联</option><option value="venue_rule">刊会要求／偏好</option></select></label>
        ${input('paperRecordTitle','概念／问题／要求标题')}${area('paperRecordContent','补充说明或要求原文')}
        <div id="paperTermFields">${input('paperPreferred','首选术语')}${area('paperDefinition','概念定义')}${input('paperAllowed','允许变体（逗号分隔）')}${input('paperDiscouraged','需核对的变体（逗号分隔）')}</div>
        <div id="paperArgumentFields">${[['question','研究问题'],['method','方法'],['evidence','证据及定位'],['result','实际结果（未知可留空）'],['interpretation','解释'],['boundary','推断边界'],['contribution','贡献']].map(([id,label])=>area('arg_'+id,label)).join('')}${area('arg_nonclaims','明确不作出的主张（每行一条）')}</div>
        <div id="paperVenueFields"><label>规则类别 <select id="paperRuleClass"><option value="official_requirement">官方要求</option><option value="example">已发表示例</option><option value="preference">作者／团队偏好</option></select></label>
          ${input('paperRuleURL','来源网址')}${input('paperRuleDate','核对日期（YYYY-MM-DD）')}
          <label>来源核对 <select id="paperRuleVerification"><option value="unverified">尚未核对</option><option value="checked">我已核对该来源</option></select></label>
          ${input('paperRuleScope','适用阶段／文件（逗号分隔）')}
        </div>
        ${input('paperArtifacts','仅适用这些稿件（文件名，逗号分隔；留空表示本空间）')}
        <label>关联已保存记录（可多选）<select multiple id="paperRelated" style="width:100%">${data.active_records.map(r=>`<option value="${esc(r.record_id)}">${esc(r.title)}</option>`).join('')}</select></label>
        <label>来源 <select id="paperSourceKind"><option value="user_note">作者说明</option><option value="draft">本空间稿件</option><option value="material">已导入材料</option><option value="evidence">证据卡</option></select></label>
        ${area('paperSourceKey','作者原话／稿件名／材料或证据 ID')}${input('paperSourceLocator','章节、页码或位置')}${input('paperRecordReason','记录或更正原因')}
        <button class="secondary" id="savePaperRecord">保存并采纳</button><button class="secondary" id="resetPaperRecord">开始新记录</button>
      </details>
      <div style="margin-top:12px"><select id="paperCheckFile">${(detail.drafts||[]).map(f=>`<option value="${esc(f)}">${esc(f)}</option>`).join('')}</select>
        <button class="secondary" id="checkPaperContext">核对术语与记录状态</button><div id="paperCheckResult" style="white-space:pre-wrap;font-size:12px"></div></div>
      <div id="paperReviewContext" style="margin-top:12px"></div>
    </details>`;
    renderPaperReviews(projectId);
    $('paperVenueType').value=profile.venue_type;
    const reload=async()=>renderManuscriptContext(await api(`/api/v1/projects/${encodeURIComponent(projectId)}`),true);
    $('reloadManuscript').onclick=reload;
    $('savePaperProfile').onclick=async()=>{
      try {
        await api(base+'/profile',{method:'PUT',body:JSON.stringify({expected_revision:data.revision,reason:'作者更新论文背景',profile:{
          language:$('paperLanguage').value,article_type:$('paperArticleType').value,study_designs:split($('paperDesigns').value),
          venue_type:$('paperVenueType').value,venue_name:$('paperVenue').value,track:$('paperTrack').value,publication_stage:$('paperPublicationStage').value}})});
        await reload();await refreshProjectDetail();toast('论文背景已保存');
      }catch(e){toast(e.message);}
    };
    function split(value){return value.split(/[,，\n]/).map(x=>x.trim()).filter(Boolean);}
    let editing=null;
    function kindFields(){for(const [id,kind] of [['paperTermFields','term'],['paperArgumentFields','argument'],['paperVenueFields','venue_rule']])$(id).hidden=$('paperRecordKind').value!==kind;}
    $('paperRecordKind').onchange=kindFields;kindFields();
    $('resetPaperRecord').onclick=()=>{editing=null;$('paperEditLabel').textContent='新记录';$('paperRecordKind').disabled=false;for(const input of $('manuscriptEditor').querySelectorAll('input,textarea'))input.value='';for(const opt of $('paperRelated').options){opt.selected=false;opt.disabled=false;}};
    $('savePaperRecord').onclick=async()=>{
      const kind=$('paperRecordKind').value;
      const payload={kind,title:$('paperRecordTitle').value,content:$('paperRecordContent').value,
        sources:[{kind:$('paperSourceKind').value,key:$('paperSourceKey').value,locator:$('paperSourceLocator').value},...(editing?.sources||[]).slice(1).map(s=>({kind:s.kind,key:s.key,locator:s.locator||''}))],
        artifact_names:split($('paperArtifacts').value),relates_to:[...$('paperRelated').selectedOptions].map(o=>o.value),reason:$('paperRecordReason').value,
        ...(editing?{record_id:editing.record_id,expected_revision:editing.revision}:{})};
      if(kind==='term')payload.term={preferred:$('paperPreferred').value,definition:$('paperDefinition').value,allowed_variants:split($('paperAllowed').value),discouraged_variants:split($('paperDiscouraged').value)};
      if(kind==='argument')payload.argument={...Object.fromEntries(['question','method','evidence','result','interpretation','boundary','contribution'].map(k=>[k,$('arg_'+k).value])),nonclaims:$('arg_nonclaims').value.split('\n').map(x=>x.trim()).filter(Boolean)};
      if(kind==='venue_rule')payload.venue_rule={classification:$('paperRuleClass').value,source_url:$('paperRuleURL').value,checked_on:$('paperRuleDate').value||null,verification:$('paperRuleVerification').value,applies_to:split($('paperRuleScope').value)};
      try{await api(base+'/records',{method:'POST',body:JSON.stringify(payload)});await reload();toast('论文记录已保存');}catch(e){toast(e.message);}
    };
    const labels={term:'术语',argument:'论证关联',venue_rule:'刊会要求'};
    const sourceLabels={user_note:'作者说明',draft:'稿件',material:'导入材料',evidence:'证据卡'};
    const argumentLabels={question:'研究问题',method:'方法',evidence:'证据',result:'结果',interpretation:'解释',boundary:'推断边界',contribution:'贡献',nonclaims:'不作出的主张'};
    const ruleLabels={official_requirement:'官方要求',example:'已发表示例',preference:'作者／团队偏好'};
    for(const row of data.active_records){
      const card=document.createElement('div');card.style.cssText='margin:10px 0;padding:10px;border:1px solid #ddcbbb;border-radius:8px';
      const heading=document.createElement('b');heading.textContent=`${labels[row.kind]} · ${row.title} · ${row.status==='proposed'?'待采纳':'已采纳'} · v${row.revision}`;
      const body=document.createElement('p');body.style.whiteSpace='pre-wrap';
      body.textContent=[row.content,row.term?[`${row.term.preferred}：${row.term.definition}`,row.term.allowed_variants.length?'允许变体：'+row.term.allowed_variants.join('；'):'',row.term.discouraged_variants.length?'需核对变体：'+row.term.discouraged_variants.join('；'):''].filter(Boolean).join('\n'):'',row.argument?Object.entries(row.argument).filter(([,v])=>v?.length).map(([k,v])=>`${argumentLabels[k]}：${Array.isArray(v)?v.join('；'):v}`).join('\n'):'',row.venue_rule?`${ruleLabels[row.venue_rule.classification]} · ${row.venue_rule.verification==='checked'?'作者已核对来源':'来源尚未核对'}\n适用目标：${[row.venue_profile?.venue_name,row.venue_profile?.track,row.venue_profile?.publication_stage].filter(Boolean).join(' · ')}\n${row.venue_rule.source_url}\n${row.venue_rule.checked_on?'核对日期：'+row.venue_rule.checked_on:''}\n${row.venue_rule.applies_to.length?'适用说明：'+row.venue_rule.applies_to.join('；'):''}`:'',...row.issues].filter(Boolean).join('\n');
      const source=document.createElement('p');source.textContent='来源：'+row.sources.map(s=>`${sourceLabels[s.kind]} · ${s.key} ${s.locator||''}`).join('；');source.style.fontSize='12px';
      card.append(heading,body,source);
      const edit=document.createElement('button');edit.className='secondary';edit.textContent='更正／复核';edit.onclick=()=>{
        editing=row;$('manuscriptEditor').open=true;$('paperEditLabel').textContent=`更正 ${row.title} · v${row.revision}`;
        $('paperRecordKind').value=row.kind;$('paperRecordKind').disabled=true;kindFields();
        for(const [id,value] of Object.entries({paperRecordTitle:row.title,paperRecordContent:row.content,paperPreferred:row.term?.preferred||'',paperDefinition:row.term?.definition||'',paperAllowed:(row.term?.allowed_variants||[]).join(', '),paperDiscouraged:(row.term?.discouraged_variants||[]).join(', '),paperArtifacts:row.artifact_names.join(', '),paperSourceKind:row.sources[0].kind,paperSourceKey:row.sources[0].key,paperSourceLocator:row.sources[0].locator||'',paperRecordReason:'',paperRuleClass:row.venue_rule?.classification||'official_requirement',paperRuleURL:row.venue_rule?.source_url||'',paperRuleDate:row.venue_rule?.checked_on||'',paperRuleVerification:row.venue_rule?.verification||'unverified',paperRuleScope:(row.venue_rule?.applies_to||[]).join(', ')}))$(id).value=value;
        for(const key of ['question','method','evidence','result','interpretation','boundary','contribution'])$('arg_'+key).value=row.argument?.[key]||'';
        $('arg_nonclaims').value=(row.argument?.nonclaims||[]).join('\n');
        for(const opt of $('paperRelated').options){opt.selected=row.relates_to.includes(opt.value);opt.disabled=opt.value===row.record_id;}
      };card.append(edit);
      for(const status of row.status==='proposed'?['confirmed','withdrawn']:['withdrawn']){
        const button=document.createElement('button');button.className='secondary';button.textContent=status==='confirmed'?'采纳':'撤回';
        button.onclick=async()=>{const reason=prompt(status==='confirmed'?'采纳的依据：':'撤回原因：');if(!reason?.trim())return;try{await api(base+`/records/${encodeURIComponent(row.record_id)}/review`,{method:'POST',body:JSON.stringify({expected_revision:row.revision,status,reason})});await reload();}catch(e){toast(e.message);}};card.append(button);
      }
      $('manuscriptRecords').append(card);
    }
    $('checkPaperContext').onclick=async()=>{try{
      const result=await api(base+'/check',{method:'POST',body:JSON.stringify({filename:$('paperCheckFile').value})});
      if(currentWorkspaceId()!==projectId)return;
      $('paperCheckResult').textContent=(result.findings.length?result.findings.map(f=>`${f.line?'第 '+f.line+' 行：':''}${f.message}`).join('\n'):'未发现已登记规则的字面匹配问题。')+'\n本次仅检查术语模式和记录状态；尚未验证科学论证、引用支持、刊会合规或排版。';
    }catch(e){toast(e.message);}};
  }catch(e){if(currentWorkspaceId()===projectId)toast('读取论文记录失败：'+e.message);}
}
