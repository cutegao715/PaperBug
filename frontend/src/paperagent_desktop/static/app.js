const $ = id => document.getElementById(id);
let token = sessionStorage.getItem('paperagent_token') || '';
let endpoint = sessionStorage.getItem('paperagent_endpoint') || 'http://127.0.0.1:8765';
let selected = '';
let pending = null;
let previewed = null;
let catalogLoaded = false;
const workflowSelect = document.createElement('select');
const skillSelect = document.createElement('select');
workflowSelect.setAttribute('aria-label','科研流程');
skillSelect.setAttribute('aria-label','科研方法');
$('taskGoal').before(workflowSelect, skillSelect);
function fillChoices(select, items, label){select.replaceChildren();const automatic=document.createElement('option');automatic.value='';automatic.textContent=label;select.append(automatic);for(const item of items){const option=document.createElement('option');option.value=item.id;option.textContent=item.name;select.append(option);}}
$('api').value = endpoint;
async function api(path, options = {}) {
  const response = await fetch(endpoint + path, {...options, headers: {'Content-Type':'application/json', ...(token ? {Authorization:'Bearer '+token} : {}), ...options.headers}});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请求失败');
  return data;
}
const post = (path, body) => api(path, {method:'POST', body:JSON.stringify(body)});
function status(error) { $('status').textContent = error ? (error.message || String(error)) : ''; }
async function authenticate(action) {
  try {
    endpoint = $('api').value.trim().replace(/\/$/, '');
    const url = new URL(endpoint);
    if (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost','127.0.0.1'].includes(url.hostname))) throw Error('远程云端地址需要 HTTPS');
    const data = await post('/api/v1/auth/'+action, {username:$('username').value, password:$('password').value});
    token=data.token; sessionStorage.setItem('paperagent_token', token); sessionStorage.setItem('paperagent_endpoint',endpoint); $('password').value=''; status(); await showWorkspace();
  } catch(e) { status(e); }
}
async function showWorkspace() {
  const spaces = await api('/api/v1/spaces'); $('login').hidden=true; $('workspace').hidden=false;
  if(!catalogLoaded){const catalog=await api('/api/v1/research/capabilities');fillChoices(workflowSelect,catalog.workflows,'不指定流程');fillChoices(skillSelect,catalog.skills,'自动选择科研方法');catalogLoaded=true;}
  $('spaces').replaceChildren();
  for (const s of spaces) { const b=document.createElement('button'); b.textContent=s.name; b.className='space'+(s.id===selected?' active':''); b.onclick=()=>openSpace(s.id,s.name); $('spaces').append(b); }
}
async function openSpace(id,name) { try { selected=id; pending=null; $('title').textContent=name; $('send').disabled=false; await showWorkspace(); await refreshMessages(); } catch(e){status(e);} }
async function refreshMessages() {
  const rows=await api('/api/v1/spaces/'+selected+'/messages'); $('messages').replaceChildren();
  for (const row of rows) { const div=document.createElement('div'); div.className='message'; const label=document.createElement('strong'); label.textContent=row.role==='user'?'我':'科研助手'; const content=document.createElement('span'); content.textContent=row.content+(row.status==='failed'?'（发送失败，可重新发送）':row.status==='pending'?'（处理中）':''); div.append(label,content); $('messages').append(div); }
  $('messages').scrollTop=$('messages').scrollHeight;
}
$('signIn').onclick=()=>authenticate('login'); $('register').onclick=()=>authenticate('register');
$('logout').onclick=async()=>{try{await post('/api/v1/auth/logout',{});}catch(_){} token=''; selected=''; previewed=null; catalogLoaded=false; sessionStorage.removeItem('paperagent_token'); sessionStorage.removeItem('paperagent_device'); $('workspace').hidden=true; $('login').hidden=false; $('messages').replaceChildren(); $('fileList').replaceChildren(); $('files').value='';};
$('newSpace').onsubmit=async e=>{e.preventDefault();try{const space=await post('/api/v1/spaces',{name:$('spaceName').value});$('spaceName').value='';await openSpace(space.id,space.name);status();}catch(err){status(err);}};
$('messageForm').onsubmit=async e=>{e.preventDefault();const text=$('text').value.trim();if(!selected||!text)return;const space=selected; if(!pending||pending.text!==text||pending.space!==space) pending={text,space,id:crypto.randomUUID()}; $('send').disabled=true;try{await post('/api/v1/spaces/'+space+'/messages',{text,request_id:pending.id});pending=null;$('text').value='';status();if(selected===space)await refreshMessages();}catch(err){status(err);if(selected===space)await refreshMessages().catch(()=>{});}finally{$('send').disabled=!selected;}};
$('files').onchange=()=>{ previewed=null; $('runTask').disabled=true; $('materialPreview').textContent=''; $('fileList').replaceChildren(); for(const file of $('files').files){const li=document.createElement('li');li.textContent=file.name;$('fileList').append(li);} };
async function local(path,body){const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw Error(data.detail||'本地操作失败');return data;}
function toBase64(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(Error('读取文件失败'));reader.readAsDataURL(file);});}
$('previewMaterial').onclick=async()=>{try{const file=$('files').files[0];if(!file||!selected)throw Error('请先选择研究空间和一个文件');if(file.size>5000000)throw Error('文件超过 5 MB');const data=await toBase64(file);const result=await local('/local/material/read',{filename:file.name,data});previewed={space:selected,filename:file.name,data,result};$('materialPreview').textContent=result.pages.map(p=>`${result.locator_kind==='page'?'第 '+p.page+' 页':'文本片段'}${p.truncated?'（已截断）':''}\n${p.text}`).join('\n\n')+(result.pages_truncated?'\n其余页未提取':'');$('runTask').disabled=false;status();}catch(e){status(e);}};
$('runTask').onclick=async()=>{if(!previewed||previewed.space!==selected)return;const goal=$('taskGoal').value.trim();if(!goal){status('请输入任务目标');return;}const snapshot=previewed; $('runTask').disabled=true;try{
  $('taskProgress').textContent='正在授权本机材料并准备任务';
  const material=await local('/local/materials',{filename:snapshot.filename,data:snapshot.data,allow_cloud:true});
  let device=JSON.parse(sessionStorage.getItem('paperagent_device')||'null');
  if(!device){device=await post('/api/v1/devices',{name:'本地工作空间'});sessionStorage.setItem('paperagent_device',JSON.stringify(device));}
  const task=await post('/api/v1/spaces/'+snapshot.space+'/agent-tasks',{goal,request_id:crypto.randomUUID()});
  const input={file_ref:material.file_ref,sha256:material.sha256,device_id:device.id,workflow_id:workflowSelect.value||null,skill_id:skillSelect.value||null};
  for(let i=0;i<10;i++){
    $('taskProgress').textContent='云端正在决定下一步';
    const step=await post('/api/v1/agent-tasks/'+task.id+'/advance',input);
    if(step.status==='succeeded'){$('taskProgress').textContent=step.answer;previewed=null;return;}
    if(step.status==='queued')continue;
    if(step.status!=='waiting_local')throw Error('任务状态无法继续');
    const op=step.operation;
    await local('/local/queue',{operation_id:op.id,file_ref:op.file_ref,sha256:op.sha256});
    const lease=await api('/api/v1/agent-operations/'+op.id+'/claim',{method:'POST',headers:{'X-Device-Token':device.device_token}});
    $('taskProgress').textContent='正在本机读取并回传已确认的内容';
    const result=await local('/local/queue/'+op.id+'/execute',{});
    await api('/api/v1/agent-operations/'+op.id+'/result',{method:'POST',headers:{'X-Device-Token':device.device_token,'X-Lease-Token':lease.lease_token},body:JSON.stringify({file_ref:op.file_ref,sha256:result.sha256,pages:result.pages,total_pages:result.total_pages,pages_truncated:result.pages_truncated,locator_kind:result.locator_kind})});
    await local('/local/queue/'+op.id+'/ack',{});
  }
  throw Error('任务达到步骤上限');
}catch(e){$('taskProgress').textContent='任务未完成：'+e.message;status(e);}finally{$('runTask').disabled=!previewed;}};
if(token) showWorkspace().catch(()=>{token='';sessionStorage.removeItem('paperagent_token');status('请重新登录');});
