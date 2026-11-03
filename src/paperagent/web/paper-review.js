/* Optional review artifacts and follow-ups inside the existing paper panel. */
let paperReviewRequest=0;
async function renderPaperReviews(projectId, hostId='paperReviewContext') {
  const host=$(hostId);
  if(!host || currentWorkspaceId()!==projectId)return;
  const request=++paperReviewRequest;
  const base=`/api/v1/projects/${encodeURIComponent(projectId)}/paper-reviews`;
  const statuses={open:'待处理',partial:'部分解决',resolved:'已复核解决',regressed:'问题再现',waived:'作者决定保留',not_verifiable:'尚无法核对'};
  const severity={critical:'关键风险',major:'主要问题',minor:'局部问题',note:'建议'};
  const certainty={supported:'有当前证据',tentative:'需进一步确认',unverified:'证据未核对'};
  const classes={validity:'科学有效性',reporting:'报告完整性',venue:'刊会／申报要求',preference:'偏好',prose:'语言表达'};
  const coverage={checked:'已检查',not_applicable:'不适用',outside_scope:'本次范围外',unavailable:'资料不可用'};
  try {
    const data=await api(base);
    if(request!==paperReviewRequest || !host.isConnected || currentWorkspaceId()!==projectId)return;
    host.replaceChildren();
    const panel=document.createElement('details');panel.id='paperReviewPanel';
    panel.innerHTML='<summary>'+ (hostId==='grantReviewContext'?'申请评审与复核':'论文评审与复核') +'</summary><p style="font-size:12px">直接在对话中提出评审请求，可按需保存报告。复核记录保留作者判断与来源；片段匹配不代表科学问题已被独立验证。</p><button class="secondary" id="refreshPaperReviews">刷新评审</button><div id="paperReviewReports"></div>';
    host.append(panel);
    $('refreshPaperReviews').onclick=async()=>{await renderPaperReviews(projectId,hostId);if($('paperReviewPanel'))$('paperReviewPanel').open=true;};
    if(!data.reports.length)$('paperReviewReports').textContent='尚无已保存的评审报告。';
    for(const report of [...data.reports].reverse()) {
      const card=document.createElement('details');card.dataset.reviewId=report.report_id;
      card.style.cssText='margin:10px 0;padding:10px;border:1px solid #ddcbbb;border-radius:8px';
      card.innerHTML=`<summary>${esc(report.title)} · ${esc(report.round_label)}${report.needs_review?' · 依据有变化':''}</summary>
        <p style="white-space:pre-wrap">${report.review_kind==='grant'?'申请评审':'论文评审'} · ${esc({scientific:'科学论证',readability:'可读性',criteria:'指南标准'}[report.review_mode||'scientific'])}\n${esc(report.scope)}\n${esc(report.summary)}${report.modules.length?'\n评审资源：'+esc(report.modules.join('；')):''}</p>
        <p style="font-size:12px;white-space:pre-wrap">${esc(report.artifacts.map(a=>a.filename+' · 版本 '+a.version.slice(0,12)).join('\n'))}\n${esc(report.review_issues.join('\n'))}</p>
        <p style="font-size:12px;white-space:pre-wrap">${esc(report.checks.map(c=>c.dimension+'：'+coverage[c.coverage]+'；'+c.basis).join('\n'))}</p>
        <button class="secondary" data-original>查看原报告</button><pre data-memo hidden style="white-space:pre-wrap;font-size:12px"></pre><div data-findings></div>`;
      card.querySelector('[data-original]').onclick=async()=>{
        const memo=card.querySelector('[data-memo]');
        if(memo.hidden){try{const response=await fetch(report.report_url);if(!response.ok)throw new Error('原报告无法读取，请刷新后检查');memo.textContent=await response.text();memo.hidden=false;}catch(e){toast(e.message);}}
        else memo.hidden=true;
      };
      for(const issue of report.findings){
        const item=document.createElement('div');item.dataset.issueId=issue.issue_id;
        item.style.cssText='border-top:1px solid #e7d7c8;margin-top:8px;padding-top:8px';
        const targets=report.artifacts.filter(a=>['manuscript','companion'].includes(a.role)&&issue.affected_artifacts.includes(a.filename));
        item.innerHTML=`<b>${esc(issue.title)} · ${esc(statuses[issue.status])}</b>
          <p style="font-size:12px">${esc(severity[issue.severity])} · ${esc(certainty[issue.evidence_status])} · ${esc(classes[issue.classification])}</p>
          <p style="white-space:pre-wrap">位置：${esc(issue.location)}\n${esc(issue.problem)}\n依据：${esc(issue.basis)}\n建议：${esc(issue.action_needed)}</p>
          ${issue.effective_followup?`<p style="white-space:pre-wrap">作者复核：${esc(issue.effective_followup.reason)}</p>`:''}
          <p style="font-size:12px;white-space:pre-wrap">${esc(issue.review_issues.join('\n'))}</p>
          <div data-proposals></div><details><summary>记录人工复核</summary>
          <form><label>当前判断 <select name="status">${Object.entries(statuses).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select></label>
          <label style="display:block">判断依据<textarea name="reason" rows="2" required style="width:100%;box-sizing:border-box"></textarea></label>
          <label style="display:block">核对稿件 <select name="filename">${targets.map(a=>`<option value="${esc(a.filename)}">${esc(a.filename)}</option>`).join('')}</select></label>
          <label style="display:block">章节或位置<input name="locator" style="width:100%;box-sizing:border-box"></label>
          <label style="display:block">稿件中已核对的原文片段<textarea name="quote" rows="2" style="width:100%;box-sizing:border-box"></textarea></label>
          <p style="font-size:12px">“已复核解决”需要受影响稿件中的定位片段。作者回复或“已处理”标记不能替代稿件证据。</p>
          <button class="secondary" type="submit">保存人工复核</button></form></details>`;
        item.querySelector('select[name="status"]').value=issue.status;
        for(const proposal of issue.proposals){
          const box=document.createElement('p');box.style.whiteSpace='pre-wrap';
          box.textContent=`助手待采纳判断：${statuses[proposal.status]}\n${proposal.reason}${proposal.needs_review?'\n依据已变化，请重新核对':''}`;
          const accept=document.createElement('button');accept.className='secondary';accept.textContent='采纳复核建议';accept.disabled=proposal.needs_review;
          accept.onclick=async()=>{const reason=prompt('采纳该复核判断的依据：');if(!reason?.trim())return;
            try{await api(`${base}/${report.report_id}/issues/${issue.issue_id}/adopt`,{method:'POST',body:JSON.stringify({expected_revision:issue.revision,followup_id:proposal.followup_id,reason})});await refresh();}catch(e){toast(e.message);}};
          box.append(accept);item.querySelector('[data-proposals]').append(box);
        }
        const refresh=async()=>{await renderPaperReviews(projectId,hostId);if($('paperReviewPanel'))$('paperReviewPanel').open=true;const next=document.querySelector(`[data-review-id="${report.report_id}"]`);if(next)next.open=true;};
        item.querySelector('form').onsubmit=async event=>{
          event.preventDefault();const form=event.currentTarget,values=new FormData(form),quote=String(values.get('quote')||'').trim();
          const payload={expected_revision:issue.revision,status:values.get('status'),reason:values.get('reason'),
            sources:[{kind:'user_note',key:String(values.get('reason'))}],verifications:[]};
          const button=form.querySelector('button[type="submit"]');button.disabled=true;
          try{
            if(quote){const filename=String(values.get('filename')||'');const snapshot=await api(base+'/basis?'+new URLSearchParams({filename}));payload.verifications.push({filename,expected_sha256:snapshot.version,locator:values.get('locator'),quote});}
            await api(`${base}/${report.report_id}/issues/${issue.issue_id}`,{method:'POST',body:JSON.stringify(payload)});
            await refresh();toast('复核判断已记录');
          }catch(e){toast(e.message);}finally{button.disabled=false;}
        };
        card.querySelector('[data-findings]').append(item);
      }
      $('paperReviewReports').append(card);
    }
  }catch(e){if(host.isConnected&&currentWorkspaceId()===projectId)toast('读取评审失败：'+e.message);}
}
