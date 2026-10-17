'use strict';
(() => {
 const el=id=>document.getElementById(id);
 const csrf=document.querySelector('meta[name="csrf-token"]').content;
 const labels={proposed:'Chưa duyệt',staged:'Đã kiểm tra, chưa bật',approved:'Đã duyệt',scheduled:'Chưa bật lịch',armed:'Chờ giờ đăng',paused:'Tạm dừng',preparing:'Đang chuẩn bị',submitting:'Đang đăng',published:'Đã đăng',needs_attention:'Cần kiểm tra',cancelled:'Đã bỏ lịch'};
 let state=null, selected='', signature='', approvedSignature='', busy=false;
 const requestKeys=new Map();
 function target(){return state?.snapshot.accounts.find(x=>x.id===state.selectedAccount)?.pages?.find(x=>x.id===state.selectedPage);}
 function connection(){return state?.snapshot.content_connections?.find(x=>x.account_id===state.selectedAccount&&x.page_key===state.selectedPage);}
 function plans(){return (state?.snapshot.content_plans||[]).filter(x=>x.account_id===state.selectedAccount&&x.page_key===state.selectedPage&&x.thread_id===connection()?.thread_id);}
 function plan(){return plans().find(x=>x.plan_id===selected);}
 function ready(){return !!state?.snapshot.bridge.online&&!!state?.snapshot.bridge.enabled&&!busy&&!state?.busy;}
 function controls(){
  const item=plan();el('fb-content-sync').disabled=!ready()||!connection();
  el('fb-content-plan').disabled=busy||!plans().length;
  el('fb-content-approve').disabled=!ready()||!item||!['proposed','staged'].includes(item.state)||!el('fb-content-confirm').checked||approvedSignature!==item.digest;
 }
 function render(){
  const link=connection();el('fb-content-link').textContent=link?'Đã liên kết: '+link.title+(link.auto_sync?' · tự nhận mỗi phút':'')+(link.last_error?' · '+link.last_error:''):'Page này chưa liên kết cuộc trò chuyện. Mở Quản lý Page → Kế hoạch từ Codex trên desktop để chọn.';
  const list=plans();if(!list.some(x=>x.plan_id===selected))selected=list[0]?.plan_id||'';
  const sig=JSON.stringify(list.map(x=>[x.plan_id,x.title,x.digest,x.state]));
  if(sig!==signature){el('fb-content-plan').replaceChildren();for(const item of list){const option=document.createElement('option');option.value=item.plan_id;option.textContent=item.title+' · '+(labels[item.state]||item.state);el('fb-content-plan').append(option);}if(!list.length){const option=document.createElement('option');option.textContent='Chưa có kế hoạch';el('fb-content-plan').append(option);}signature=sig;}
  el('fb-content-plan').value=selected;
  const item=plan(), page=item?.target||target();const binding=JSON.stringify([state?.selectedAccount,state?.selectedPage,item?.plan_id,item?.digest]);
  if(el('fb-content-preview').dataset.binding!==binding){el('fb-content-preview').dataset.binding=binding;el('fb-content-confirm').checked=false;approvedSignature='';}
  el('fb-content-preview').textContent=item&&page?['Page: '+page.name,'Link Page: '+page.url,'Page ID: '+page.page_id,'Kế hoạch: '+item.title,'',...item.items.flatMap((x,index)=>[(index+1)+'. '+(x.kind==='video'?'VIDEO · '+x.video_key:'BÀI VIẾT'),'Giờ đăng: '+new Date(x.due_at*1000).toLocaleString('vi-VN',{timeZone:'Asia/Bangkok'})+' (UTC+7)','Trạng thái: '+(labels[x.state]||x.state||'Chưa duyệt'),x.body,'Mục tiêu: '+(x.goal||''),x.message||'',''])].join('\n'):'Chưa có kế hoạch của Page này. Nhận câu trả lời hoàn tất chứa kế hoạch từ cuộc trò chuyện đã liên kết.';
  controls();
 }
 async function send(action){
  if(!ready())return;
  const item=plan(),link=connection(),page=target();
  if(!page)return;
  if(action==='approve_content_plan'&&(!item||!el('fb-content-confirm').checked||approvedSignature!==item.digest))return;
  const body={action,account_id:state.selectedAccount,page_key:state.selectedPage};
  if(action==='sync_content_plan'){if(!link)return;body.thread_id=link.thread_id;}else{body.plan_id=item.plan_id;body.plan_digest=item.digest;}
  const fingerprint=JSON.stringify(body);if(!requestKeys.has(fingerprint))requestKeys.set(fingerprint,crypto.randomUUID().replaceAll('-',''));
  body.request_key=requestKeys.get(fingerprint);busy=true;controls();el('fb-content-status').textContent='Đang gửi lệnh tới máy Tèo…';
  try{const response=await fetch('/api/facebook/commands',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(body)});let data;try{data=await response.json();}catch(_){throw new Error('Chưa rõ kết quả gửi. Kiểm tra tiến độ rồi thử lại.');}if(!response.ok)throw new Error(data.error||'Chưa gửi được lệnh.');requestKeys.delete(fingerprint);el('fb-content-status').textContent='Đã gửi yêu cầu. Xem Tiến độ thao tác để biết kết quả; desktop thực hiện công việc.';el('fb-content-confirm').checked=false;approvedSignature='';}
  catch(error){el('fb-content-status').textContent=error.message==='Failed to fetch'?'Chưa kết nối được máy Tèo. Nội dung và kế hoạch vẫn được giữ.':error.message;}
  finally{busy=false;controls();}
 }
 document.addEventListener('teo-facebook-state',event=>{state=event.detail;render();});
 el('fb-content-plan').addEventListener('change',()=>{selected=el('fb-content-plan').value;render();});
 el('fb-content-confirm').addEventListener('change',()=>{approvedSignature=el('fb-content-confirm').checked?plan()?.digest||'':'';controls();});
 el('fb-content-sync').addEventListener('click',()=>send('sync_content_plan'));
 el('fb-content-approve').addEventListener('click',()=>send('approve_content_plan'));
})();
