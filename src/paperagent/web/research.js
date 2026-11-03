/* Evidence and deliveries are views over the workspace, not a task board. */
let researchViewRequest=0;
function renderResearchOutputs(detail, force=false) {
  const host=$('researchOutputs'), projectId=detail?.project_id;
  if(!host || !projectId || projectId!==currentWorkspaceId())return;
  if(!force && host.dataset.projectId===projectId && host.childElementCount)return;
  const version=++researchViewRequest;
  host.dataset.projectId=projectId;
  host.innerHTML='<details style="padding:12px 16px;border-top:1px solid #e7d7c8"><summary>文献证据与科研产物</summary><p>按需查看文献综合视图、演示稿和投稿包；来源关联不代表科学核验或刊会合规。</p><button class="secondary" data-refresh>刷新产物</button><button class="secondary" data-matrix>查看文献证据</button><div data-matrix-view></div><div data-outputs></div></details>';
  const base=`/api/v1/projects/${encodeURIComponent(projectId)}`;
  const valid=()=>version===researchViewRequest && currentWorkspaceId()===projectId && host.isConnected;
  let outputOffset=0;
  async function loadOutputs(append=false){try{
    const data=await api(base+'/research-outputs?'+new URLSearchParams({offset:append?outputOffset:0}));
    if(!valid())return;
    const target=host.querySelector('[data-outputs]');if(!append)target.replaceChildren();
    target.querySelector('[data-more]')?.remove();
    for(const row of data.outputs){
      const card=document.createElement('details');
      const title=document.createElement('summary');title.textContent=({presentation:'科研演示',submission:'投稿包'}[row.kind]||row.kind)+' · '+row.output_id+(row.needs_review?' · 需要复核':' · 文件版本未变');card.append(title);
      const note=document.createElement('p');note.style.whiteSpace='pre-wrap';note.textContent=[...(row.issues||[]),...(row.warnings||[])].join('\n');card.append(note);
      for(const name of row.files){const link=document.createElement('a');link.textContent='下载 '+name;link.href=base+'/research-outputs/'+encodeURIComponent(row.output_id)+'/file?'+new URLSearchParams({name});link.style.display='block';card.append(link);}
      target.append(card);
    }
    if(!data.total)target.textContent='尚无科研演示或投稿包。可在对话中要求基于已有稿件和资料生成。';
    outputOffset=data.next_offset;
    if(outputOffset!==null){const more=document.createElement('button');more.dataset.more='';more.textContent='更多产物';more.onclick=()=>loadOutputs(true);target.append(more);}
  }catch(e){if(valid())toast(e.message);}}
  let matrixOffset=0;
  async function loadMatrix(append=false){try{
    const data=await api(base+'/literature-matrix?'+new URLSearchParams({offset:append?matrixOffset:0}));
    if(!valid())return;
    const target=host.querySelector('[data-matrix-view]');if(!append)target.replaceChildren();target.querySelector('[data-more]')?.remove();
    for(const row of data.rows){
      const card=document.createElement('details'),title=document.createElement('summary');title.textContent=row.reference.title+' · '+(row.coverage==='metadata_only'?'仅文献记录':'已有证据标注');card.append(title);
      for(const ev of row.evidence){const p=document.createElement('p');p.style.whiteSpace='pre-wrap';p.textContent=[ev.evidence_id+' · '+({supports:'支持',contradicts:'反驳',context:'背景'}[ev.relation]||ev.relation)+' · '+({full_text:'全文',partial:'部分内容',abstract_only:'仅摘要',unknown:'阅读范围未说明'}[ev.source_coverage]||ev.source_coverage),ev.quote,ev.finding,ev.method,ev.population,(ev.limitations||[]).join('；'),'来源状态：'+({current:'版本未变',changed:'已变化',unavailable:'不可访问',untracked:'未跟踪版本'}[ev.source_freshness]||ev.source_freshness)+'；标注尚未独立核验'].filter(Boolean).join('\n');card.append(p);}if(row.omitted_evidence){const more=document.createElement('p');more.textContent='另有 '+row.omitted_evidence+' 条证据，可在对话中按文献或证据标识读取。';card.append(more);}target.append(card);
    }
    if(!data.total)target.textContent='还没有已保存的参考文献。';
    matrixOffset=data.next_offset;
    if(matrixOffset!==null){const more=document.createElement('button');more.dataset.more='';more.textContent='更多文献';more.onclick=()=>loadMatrix(true);target.append(more);}
  }catch(e){if(valid())toast(e.message);}}
  host.querySelector('[data-refresh]').onclick=()=>loadOutputs();
  host.querySelector('[data-matrix]').onclick=()=>loadMatrix();
  host.querySelector('details').addEventListener('toggle',e=>{if(e.target===host.querySelector('details') && e.target.open)loadOutputs();});
  if(detail.lifecycle?.lifecycle_type==='project'){
    const reviews=document.createElement('div');reviews.id='grantReviewContext';host.append(reviews);renderPaperReviews(projectId,'grantReviewContext');
  }
}
