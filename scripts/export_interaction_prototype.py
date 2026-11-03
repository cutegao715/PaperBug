"""Bundle the current UI and a sandbox API into one offline HTML prototype."""
import base64
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'src' / 'paperagent' / 'web'
OUT = ROOT / 'exports' / 'PaperBug-interaction-prototype.html'

SANDBOX = r'''
<script>
// All prototype requests stay in memory; no real workspace is accessed.
(() => {
  const project = {project_id:'demo-research',goal:'AI 智能体与组织协作研究',created_at:'2026-10-27',references:2,drafts:1};
  const makeDetail = p => ({project_id:p.project_id,goal:p.goal,
    references:[{ref_id:'demo-method',title:'人机协作机制：研究材料示例',authors:['示例作者'],year:2026,source:'prototype',venue:'演示材料'},
      {ref_id:'demo-framework',title:'组织角色与智能体：理论框架示例',authors:['示例团队'],year:2026,source:'prototype',venue:'演示材料'}],
    evidence:[{evidence_id:'demo-e1',ref_id:'demo-method',quote:'演示证据：分析任务分工、协作边界与研究决策之间的关系。',page:3}],
    drafts:['第三章研究设计.md'],materials:[],runs:[],messages:[{role:'assistant',content:'这里是交互原型中的演示项目。可以切换页面、查看草稿或输入一条需求体验执行过程。'}]});
  const projects=[project,{...project,project_id:'demo-grant',goal:'智能教育研究课题',references:0,drafts:0}];
  const details=Object.fromEntries(projects.map(p=>[p.project_id,makeDetail(p)]));
  let notifications=[{notification_id:'demo-n1',type:'radar',title:'协作边界主题有新的研究线索',body:'演示消息：建议先阅读相关材料，再决定是否纳入研究。',priority:1,read:false},
    {notification_id:'demo-n2',type:'deadline',title:'研究设计讨论准备',body:'演示消息：准备研究问题和方法的一页摘要。',priority:2,read:false}];
  const opportunities=[{opportunity_id:'demo-o1',opportunity_type:'journal',title:'人机协作专题征稿（演示）',source:'交互原型',match_score:.87,matched:true,ai_evaluation:{summary:'用于体验机会评估与计划操作。',rationale:'现有理论框架可复用，需要补充研究方法。',reuse:'理论框架',gap:'方法说明',effort:'约 20%',risk:'待评估'},payload:{deadline:'演示期限'}}];
  const runs=new Map();
  window.fetch=async (input,options={})=>{
    const url=new URL(String(input),'https://prototype.invalid');
    const path=decodeURIComponent(url.pathname),method=options.method||'GET';
    let body={};try{body=JSON.parse(options.body||'{}');}catch{}
    let data;
    const match=path.match(/^\/api\/projects\/([^/]+)(.*)$/);
    if(path==='/api/projects'&&method==='POST'){
      const p={...project,project_id:'demo-'+Date.now(),goal:body.goal||body.name};projects.push(p);details[p.project_id]=makeDetail(p);data={project_id:p.project_id};
    }else if(path==='/api/projects')data={projects};
    else if(path==='/api/config')data={llm_configured:false};
    else if(path==='/api/lifecycles')data={lifecycles:[]};
    else if(path==='/api/notifications')data={notifications};
    else if(path.startsWith('/api/notifications/')){
      const id=path.split('/')[3];notifications=notifications.filter(n=>!path.endsWith('/dismiss')||n.notification_id!==id);notifications.forEach(n=>{if(n.notification_id===id)n.read=true;});data={status:'ok'};
    }else if(path==='/api/opportunities')data={opportunities};
    else if(path==='/api/today')data={unread_notifications:notifications.filter(n=>!n.read).length,deadlines:[],open_tasks:[]};
    else if(match){
      const [,id,tail]=match,d=details[id];
      if(!d)return new Response(JSON.stringify({detail:'演示项目不存在'}),{status:404});
      if(!tail)data=d;
      else if(tail==='/messages'){
        const run_id='preview-'+Date.now();runs.set(run_id,{d,text:body.text});d.messages.push({role:'user',content:body.text});data={run_id,mode:'prototype'};
      }else if(tail.startsWith('/drafts/'))data={name:tail.split('/')[2],raw:'# 研究设计\n\n这是可编辑设计用的演示草稿。',rendered:'第三章 研究设计\n\n本节展示研究问题、方法选择、样本设计与证据关联。\n\n以上为交互演示内容。'};
      else if(tail==='/memories')data={memories:[]};
      else if(tail==='/paper-reviews')data={reports:[]};
      else if(tail==='/research-outputs')data={outputs:[],total:0,next_offset:null};
      else if(tail==='/literature-matrix')data={rows:d.references.map(reference=>({reference,evidence:[],coverage:'metadata_only'})),total:d.references.length,next_offset:null};
      else if(tail.startsWith('/imports/')){
        d.materials.push({filename:body.path||body.folder||'演示材料.pdf',suffix:'.pdf',status:'imported',references:1,evidence:1,drafts:[]});data={project:d,imported:[{}],errors:[]};
      }else if(tail==='/conversation/focus')data={status:'ok'};
      else return new Response(JSON.stringify({detail:'此扩展功能留作设计讨论，原型未执行真实操作。'}),{status:422});
    }else if(method!=='GET')data={status:'ok',saved:true};
    else data={hits:[],tasks:[],deadlines:[],skills:[],memories:[]};
    return new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}});
  };
  window.EventSource=class {
    constructor(url){this.closed=false;this.timers=[];const id=String(url).split('/')[3],run=runs.get(id);if(!run)return;
      const emit=(event,delay)=>this.timers.push(setTimeout(()=>{if(!this.closed)this.onmessage?.({data:typeof event==='string'?event:JSON.stringify(event)});},delay));
      emit({type:'run_started',mode:'prototype'},100);
      ['read_pdf','save_evidence','write_draft'].forEach((action,i)=>emit({type:'step',iteration:i+1,action:{action}},600+i*650));
      this.timers.push(setTimeout(()=>{if(this.closed)return;run.d.messages.push({role:'assistant',content:'演示处理完成：已梳理研究问题与下一步，可继续调整页面和操作流程。'});run.d.runs.unshift({trace_id:id,status:'finished',goal:run.text,iterations:3,steps:[]});},2450));
      emit({type:'run_finished',status:'finished',summary:'演示处理完成：研究问题、材料与草稿已经关联。'},2500);emit('[DONE]',2700);
    }
    close(){this.closed=true;this.timers.forEach(clearTimeout);}
  };
})();
</script>
'''

def main():
    html=(WEB/'index.html').read_text(encoding='utf-8')
    html=html.replace('<title>PaperBug · 科研房间</title>','<title>PaperBug · 独立交互原型</title>')
    html=html.replace('<head>','<head>'+SANDBOX,1)
    html=re.sub(r'<script src="/([^"/]+\.js)"></script>',lambda m:'<script>\n'+(WEB/m[1]).read_text(encoding='utf-8').replace('</script','<\\/script')+'\n</script>',html)
    names={'idle':'10_30_52-1','wave':'10_30_53-3','love':'10_30_54-4','fly':'10_30_55-5'}
    for action,name in names.items():
        image=next((ROOT/'characters').glob('*'+name+'.png'))
        uri='data:image/png;base64,'+base64.b64encode(image.read_bytes()).decode('ascii')
        html=html.replace('/characters/'+action+'.png',uri)
    # Downloads are simulated locally, too; never navigate to a server route.
    html=html.replace("window.location.href=`/api/projects/${encodeURIComponent(pid)}/drafts/${encodeURIComponent(name)}/export?format=${encodeURIComponent(format)}`;", "toast('交互原型：导出 '+name+'（'+format+'），未生成真实文档');")
    html=html.replace('const LAST_PROJECT_KEY =', 'const LAST_PROJECT_KEY =')
    html=html.replace("'paperbug:lastProjectId'", "'paperbug:prototype:lastProjectId'")
    html=html.replace('</body>','<div style="position:fixed;left:12px;bottom:10px;z-index:50;font:11px sans-serif;background:#fff;color:#514b46;padding:6px 10px;border:1px solid #ddd;border-radius:4px;pointer-events:none">交互原型 · 演示数据 · 离线运行</div></body>')
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(html,encoding='utf-8')
    print(str(OUT))
    print(f'{OUT.stat().st_size/1024/1024:.1f} MB')

if __name__=='__main__':
    main()
