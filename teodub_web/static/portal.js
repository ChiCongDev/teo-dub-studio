'use strict';
const csrf = document.querySelector('meta[name="csrf-token"]').content;
const form = document.querySelector('#job-form');
let refreshing = false;
let actionPending = false;
let lastSnapshot = '';
let formWrites = Promise.resolve();
let preferencesTimer;
let preferencesRevision = 0;
const optionNames = new Set(['voice','reading_style','voice_speed','speed','original_percent',
 'voice_percent','burn_subtitles','save_drive','subtitle_color','subtitle_weight','subtitle_size']);
function queueFormWrite(operation) {
 const result = formWrites.catch(()=>{}).then(operation);
 formWrites = result;
 return result;
}
function choices() {
 const fields=form.elements;
 return {voice:fields.voice.value,reading_style:fields.reading_style.value,
  voice_speed:Number(fields.voice_speed.value),speed:Number(fields.speed.value),
  original_gain:Number(fields.original_percent.value)/100,voice_gain:Number(fields.voice_percent.value)/100,
  subtitle_size:Number(fields.subtitle_size.value),subtitle_color:fields.subtitle_color.value,
  subtitle_weight:fields.subtitle_weight.value,burn_subtitles:fields.burn_subtitles.checked,
  save_drive:fields.save_drive.checked};
}
function choicesValid() {
 return Array.from(optionNames).every(name=>{
  const field=form.elements[name];return field.checkValidity() && (field.type!=='number'||field.value!=='');
 });
}
function savedChoices(revision) {
 if(revision!==preferencesRevision)return;
 document.getElementById('preferences-status').textContent='Đã nhớ lựa chọn riêng cho tài khoản này.';
 notice('preferences-error','');
}
function rememberChoices() {
 const revision=++preferencesRevision;
 clearTimeout(preferencesTimer);
 document.getElementById('preferences-status').textContent='Đang lưu lựa chọn…';
 preferencesTimer=setTimeout(()=>{
  if(!choicesValid()){
   document.getElementById('preferences-status').textContent='Điền đủ thông số hợp lệ để lưu lựa chọn.';return;
  }
  const values=choices();
  queueFormWrite(()=>api('/api/preferences',values)).then(()=>savedChoices(revision)).catch(error=>{
   if(revision===preferencesRevision){notice('preferences-error',error.message);document.getElementById('preferences-status').textContent='Chưa lưu được lựa chọn.';}
  });
 },450);
}
function text(tag, value, className) {const el=document.createElement(tag);el.textContent=value;if(className)el.className=className;return el;}
function notice(id, message) {const el=document.getElementById(id);el.textContent=message||'';el.classList.toggle('hidden',!message);}
async function api(url, body) {
 const response=await fetch(url,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json','X-CSRF-Token':csrf}:{},body:body?JSON.stringify(body):undefined,cache:'no-store'});
 const result=await response.json();if(!response.ok)throw new Error(result.error||'Chưa thực hiện được. Thử lại.');return result;
}
const cleaner=new window.TeoSourceCleaner(form,api,notice);
function actionButton(job, action, label) {const button=text('button',label,'secondary');button.type='button';button.addEventListener('click',async()=>{button.disabled=true;actionPending=true;try{await api(`/api/jobs/${job.id}/${action}`,{});actionPending=false;await refresh();}catch(e){notice('queue-error',e.message);}finally{actionPending=false;button.disabled=false;}});return button;}
function renderJob(job) {
 const node=document.getElementById('job-template').content.cloneNode(true);
 node.querySelector('article').dataset.jobId=job.id;
 const badge=node.querySelector('.badge');badge.textContent=job.label;badge.classList.add(job.state);
 node.querySelector('time').textContent=new Date(job.created*1000).toLocaleString('vi-VN',{hour:'2-digit',minute:'2-digit',day:'2-digit',month:'2-digit'});
 node.querySelector('h3').textContent=job.title;
 node.querySelector('.job-owner').textContent=job.owner_name+' · #'+job.id.slice(0,8);
 node.querySelector('.source-link').href=job.input;
 node.querySelector('.job-message').textContent=job.message;
 node.querySelector('.job-drive').textContent='Drive · '+job.drive;
 const progress=node.querySelector('progress');if(job.progress!==null){progress.value=job.progress;progress.classList.remove('hidden');progress.setAttribute('aria-label','Tiến độ xử lý');}
 const actions=node.querySelector('.job-actions');
 if(job.has_output){for(const [kind,label] of [['video','Tải video'],['subtitles','Tải phụ đề']]){const a=text('a',label,kind==='video'?'button primary':'button secondary');a.href=`/files/${job.id}/${kind}`;actions.append(a);}}
 if(job.can_stop)actions.append(actionButton(job,'stop','Dừng'));
 if(job.can_retry)actions.append(actionButton(job,'retry','Tiếp tục'));
 if(job.has_source&&job.can_clean){const button=text('button','Khoanh vùng chữ gốc','secondary');button.type='button';button.addEventListener('click',()=>cleaner.open(job.id));actions.append(button);}
 const spec=job.spec;node.querySelector('.job-settings p').textContent=`${spec.voice} · Giọng ${spec.voice_speed}× · Hình ${spec.speed}× · Tiếng gốc ${Math.round(spec.original_gain*100)}% · Giọng đọc ${Math.round(spec.voice_gain*100)}% · ${spec.burn_subtitles?'Có phụ đề trong hình':'Phụ đề rời'} · ${spec.translation_model}`;
 node.querySelector('.job-settings p').textContent+=job.prepare_only?' · Chỉ tải video gốc':spec.subtitle_clean?.enabled?' · Xóa chữ gốc bằng GPU':' · Không xóa chữ gốc';
 return node;
}
async function refresh() {
 if(refreshing||actionPending)return;refreshing=true;
 try {const data=await api('/api/jobs');notice('queue-error','');const jobs=data.jobs;document.getElementById('connection').textContent='● Máy xử lý đang kết nối';document.getElementById('job-count').textContent=jobs.length;
 const addressSummary=document.getElementById('address-summary');
 if(addressSummary&&data.public_address){addressSummary.textContent=data.public_address.mode==='fixed'?'Địa chỉ cố định · '+(data.public_address.state==='online'?'đang hoạt động':'chưa xác nhận kết nối'):'Link tạm của lần mở này';}
 document.getElementById('waiting-count').textContent=jobs.filter(j=>j.state==='queued').length;
 document.getElementById('running-count').textContent=jobs.filter(j=>['running','stopping'].includes(j.state)).length;
 document.getElementById('done-count').textContent=jobs.filter(j=>['completed','review_required'].includes(j.state)).length;
 const snapshot=JSON.stringify(jobs);
 if(jobs.length&&snapshot!==lastSnapshot&&!actionPending){const open=new Set(Array.from(document.querySelectorAll('.job details[open]')).map(el=>el.closest('article').dataset.jobId));const fragment=document.createDocumentFragment();jobs.forEach(job=>{const card=renderJob(job);if(open.has(job.id))card.querySelector('details').open=true;fragment.append(card);});document.getElementById('jobs').replaceChildren(fragment);lastSnapshot=snapshot;}
 else if(!jobs.length&&document.querySelector('#jobs .job')){const empty=text('div','Chưa có video nào. Gửi link đầu tiên để bắt đầu.','empty-state');document.getElementById('jobs').replaceChildren(empty);lastSnapshot=snapshot;}
 for(const [key,label] of Object.entries(data.model_labels)){document.querySelector(`[data-model-label="${key}"]`).textContent=label;}
 const select=document.getElementById('voice');const model=data.model_policy.tts_model;form.dataset.ttsModel=model;
 if(!data.voices[model].includes(select.value)||select.dataset.model!==model){const selected=select.value;select.replaceChildren(...data.voices[model].map(v=>{const option=text('option',v);option.value=v;return option;}));select.value=data.voices[model].includes(selected)?selected:'Adam';select.dataset.model=model;if(selected!==select.value)rememberChoices();}
 }catch(e){document.getElementById('connection').textContent='○ Chưa kết nối được máy xử lý';notice('queue-error',e.message==='Failed to fetch'?'Chưa kết nối được máy xử lý. Kiểm tra mạng rồi làm mới.':e.message);}finally{refreshing=false;}
}
async function submitJob(prepare=false){if(!form.reportValidity())return;const button=document.getElementById('submit-job');const prepareButton=document.getElementById('prepare-source');if(button.disabled||prepareButton.disabled)return;button.disabled=true;prepareButton.disabled=true;notice('form-error','');document.getElementById('submit-message').textContent='';
 clearTimeout(preferencesTimer);const revision=preferencesRevision;const values=choices();
 try {const body={...values,input:form.elements.input.value,title:form.elements.title.value,submit_key:form.elements.submit_key.value,...(prepare?{prepare_only:true}:cleaner.payload())};const data=await queueFormWrite(async()=>{
  await api('/api/preferences',values);savedChoices(revision);
  return api('/api/jobs',body);
 });form.elements.submit_key.value=data.submit_key;form.elements.input.value='';form.elements.title.value='';cleaner.reset();document.getElementById('submit-message').textContent=prepare?'Đã gửi bước tải. Khi tải xong, bấm Khoanh vùng chữ gốc ở video trong hàng đợi.':'Đã gửi lồng tiếng. Video sẽ chạy khi đến lượt.';await refresh();}catch(e){notice('form-error',e.message);await refresh();}finally{button.disabled=false;prepareButton.disabled=false;}
}
form.addEventListener('submit',event=>{event.preventDefault();submitJob();});
document.getElementById('prepare-source').addEventListener('click',()=>submitJob(true));
form.addEventListener('change',event=>{if(optionNames.has(event.target.name))rememberChoices();});
form.addEventListener('input',event=>{if(event.target.type==='number'&&optionNames.has(event.target.name))rememberChoices();});
document.getElementById('refresh').addEventListener('click',refresh);
refresh();setInterval(()=>{if(!document.hidden)refresh();},3000);
