(()=>{'use strict';
 const el=id=>document.getElementById('fb-ai-'+id),csrf=document.querySelector('meta[name="csrf-token"]').content;
 let state=null,pair='',dirty=false,busy=false,key=null,lastCampaigns='';
 const ready=()=>state?.snapshot.bridge.online&&state?.snapshot.bridge.enabled&&state.selectedPage&&!busy;
 function render(){
  if(!state)return;
  const next=state.selectedAccount+':'+state.selectedPage;
  if(pair!==next){pair=next;dirty=false;key=null;}
  const campaigns=state.snapshot.ai_campaigns||[],signature=JSON.stringify(campaigns);
  if(signature!==lastCampaigns){const selected=new Set([...el('campaigns').selectedOptions].map(o=>o.value));el('campaigns').replaceChildren();for(const c of campaigns){const o=new Option(c.name+' · '+c.completed+' video',c.id);o.selected=selected.has(c.id);el('campaigns').add(o);}lastCampaigns=signature;}
  const policy=(state.snapshot.ai_policies||[]).find(p=>p.account_id===state.selectedAccount&&p.page_key===state.selectedPage);
  if(!dirty){
   const c=policy?.config||{campaigns:[],model:'gpt-5.6-terra',effort:'low',days:7,interval_days:7,videos_per_day:2,texts_per_week:3,goal:'Phát triển nội dung hữu ích; phân tích kết quả và điều chỉnh lịch.',auto_publish:false};
   for(const [id,k] of [['model','model'],['effort','effort'],['days','days'],['interval','interval_days'],['videos','videos_per_day'],['texts','texts_per_week'],['goal','goal']])el(id).value=c[k];
   for(const o of el('campaigns').options)o.selected=c.campaigns.includes(o.value);
   el('enabled').checked=!!policy?.enabled;el('auto').checked=c.auto_publish;
   const date=new Date(((policy?.next_at||Date.now()/1000+86400)+7*3600)*1000);el('due').value=date.toISOString().slice(0,16);
  }
  el('save').disabled=!ready();el('pause').disabled=!ready()||!policy;
  el('history').textContent=policy?.history?.map(h=>h.at+' · '+h.actor+' · '+h.action+'\n'+h.detail).join('\n\n')||'Chưa có thao tác AI của Page này.';
  if(!busy){const latest=policy?.runs?.[0];el('status').textContent=(policy?.enabled?'Đã bật lịch AI. ':'Lịch AI đang tắt. ')+(latest?latest.state+' · '+latest.message:'Chọn nguồn và giờ để lưu cấu hình.');}
 }
 async function send(action){
  if(!ready())return;
  const data={action,account_id:state.selectedAccount,page_key:state.selectedPage};
  if(action==='save_ai_policy')data.config={campaigns:[...el('campaigns').selectedOptions].map(o=>o.value),model:el('model').value,effort:el('effort').value,days:Number(el('days').value),interval_days:Number(el('interval').value),videos_per_day:Number(el('videos').value),texts_per_week:Number(el('texts').value),goal:el('goal').value,auto_publish:el('auto').checked};
  if(action==='save_ai_policy'){const link=(state.snapshot.content_connections||[]).find(p=>p.account_id===state.selectedAccount&&p.page_key===state.selectedPage);data.thread_id=link?.thread_id||'';data.due_at=el('due').value.replace('T',' ');data.enabled=el('enabled').checked;}
  const fingerprint=JSON.stringify(data);if(!key||key.fingerprint!==fingerprint)key={fingerprint,id:crypto.randomUUID().replaceAll('-','')};data.request_key=key.id;
  busy=true;render();el('status').textContent='Đang gửi cấu hình tới desktop…';
  try{const r=await fetch('/api/facebook/commands',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)});const result=await r.json();if(!r.ok)throw new Error(result.error||'Chưa lưu được.');key=null;dirty=false;el('status').textContent='Đã gửi lệnh. Xem Tiến độ thao tác để xác nhận desktop đã lưu.';}
  catch(e){el('status').textContent=e.message;}finally{busy=false;el('save').disabled=!ready();el('pause').disabled=!ready();}
 }
 document.addEventListener('teo-facebook-state',e=>{state=e.detail;render();});
 el('form').addEventListener('input',()=>{dirty=true;key=null;});
 el('form').addEventListener('submit',e=>{e.preventDefault();send('save_ai_policy');});
 el('pause').addEventListener('click',()=>send('pause_ai_policy'));
})();
