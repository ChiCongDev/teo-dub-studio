'use strict';
(() => {
 const byId=id=>document.getElementById(id);
 const csrf=document.querySelector('meta[name="csrf-token"]').content;
 const labels={draft:'Chưa đăng',queued:'Đang chờ',pending:'Đang chờ',running:'Đang xử lý',preparing:'Đang chuẩn bị',prepared:'Chờ duyệt',review:'Chờ duyệt',scheduled:'Đã lên lịch',armed:'Chờ giờ đăng',paused:'Đã tạm dừng',needs_attention:'Cần xử lý',submitting:'Đang đăng',processing:'Đang kiểm tra kết quả',verifying:'Đã gửi — đang xác minh',submitted:'Đã gửi — chưa xác minh',published:'Đăng thành công',completed:'Hoàn tất',succeeded:'Hoàn tất',done:'Hoàn tất',failed:'Có lỗi',unknown:'Cần kiểm tra kết quả',rejected:'Chưa thực hiện',cancelled:'Đã hủy',stopped:'Đã dừng'};
 const actions={create_account:'Lưu tài khoản',add_account:'Lưu tài khoản',save_page:'Lưu Page',create_draft:'Lưu bản nháp',save_draft:'Lưu bản nháp',open_account:'Mở Facebook',load_pages:'Lấy và lưu Page',open_page:'Mở Page',prepare_post:'Chuẩn bị bài',review_post:'Đọc bản trong Chrome',publish_post:'Đăng bản đã duyệt',schedule_post:'Hẹn giờ bài chữ',sync_content_plan:'Nhận kế hoạch Codex',approve_content_plan:'Duyệt kế hoạch và bật lịch'};
 let snapshot={bridge:{online:false,enabled:false},accounts:[],posts:[],commands:[],assets:[],schedules:[]};
 let selectedAccount='',selectedPage='',selectedPost='',refreshing=false,busy=false,dirty=false;
 let currentReview='',scheduleApproval='',accountSnapshot='',postSnapshot='',commandSnapshot='',scheduleSnapshot='';
 const requestKeys=new Map();
 const pendingMutations=new Map();
 function element(tag,value,className){const el=document.createElement(tag);if(value!==undefined)el.textContent=value;if(className)el.className=className;return el;}
 function notice(id,message){const el=byId(id);el.textContent=message||'';el.classList.toggle('hidden',!message);}
 async function api(url,body){
  const response=await fetch(url,{method:body!==undefined?'POST':'GET',headers:body!==undefined?{'Content-Type':'application/json','X-CSRF-Token':csrf}:{},body:body!==undefined?JSON.stringify(body):undefined,cache:'no-store'});
  let result;try{result=await response.json();}catch(_){throw new Error('Chưa đọc được phản hồi. Làm mới rồi kiểm tra tiến độ trước khi gửi lại.');}
  if(!response.ok)throw new Error(result.error||'Chưa thực hiện được thao tác.');return result;
 }
 function account(){return snapshot.accounts.find(item=>item.id===selectedAccount);}
 function page(){return (account()?.pages||[]).find(item=>String(item.id||item.key||item.page_key)===selectedPage);}
 function post(){return snapshot.posts.find(item=>item.id===selectedPost&&item.account_id===selectedAccount);}
 function pageKey(item){return String(item.id||item.key||item.page_key||'');}
 function facebookUrl(value){try{const url=new URL(value);const host=url.hostname.toLowerCase();if(url.protocol==='https:'&&(host==='facebook.com'||host.endsWith('.facebook.com')))return url.href;}catch(_){}return '';}
 function linkValue(el,value,label){const safe=facebookUrl(value);el.classList.toggle('hidden',!safe);if(safe){el.href=safe;el.textContent=label||safe;}else{el.removeAttribute('href');el.textContent='';}}
 function mediaList(item){return Array.isArray(item?.media)?item.media:[];}
 function mediaName(item){return typeof item==='string'?item:String(item?.name||item?.filename||'Tệp đã gắn');}
 function mediaDescription(item){const name=mediaName(item);const size=Number(item?.size||item?.size_bytes||0);return size>0?`${name} · ${(size/1048576).toLocaleString('vi-VN',{maximumFractionDigits:1})} MB`:name;}
 function timeLabel(value){if(!value)return '';const date=new Date(typeof value==='number'?value*1000:value);return Number.isNaN(date.getTime())?String(value):date.toLocaleString('vi-VN');}
 function badge(state){const el=element('span',labels[state]||state||'Chưa đăng','badge');if(/^[a-z_]+$/.test(state||''))el.classList.add(state);return el;}
 function hasCommandFor(item){return snapshot.commands.some(command=>command.post_id===item?.id&&['queued','pending','running','processing'].includes(command.state));}
 function activeSchedule(item){return snapshot.schedules.find(schedule=>schedule.post_id===item?.id&&!['published','completed','succeeded','done','cancelled','stopped'].includes(schedule.state));}
 function canSchedule(item){return !!item&&item.kind==='text'&&mediaList(item).length===0&&['draft','failed'].includes(item.state)&&!dirty&&!hasCommandFor(item)&&!activeSchedule(item);}
 function canSend(){return !!snapshot.bridge.online&&!!snapshot.bridge.enabled;}
 function draftFingerprint(){return JSON.stringify({account_id:selectedAccount,page_key:selectedPage,body:byId('fb-body').value,kind:byId('fb-kind').value,job_id:byId('fb-asset').value});}
 function controls(){
  const selected=account(),target=page(),item=post();
  const canCommand=canSend()&&!busy;
  byId('fb-open-account').disabled=!selected||!canCommand;
  byId('fb-load-pages').disabled=!selected||!canCommand;
  byId('fb-open-page').disabled=!target||!canCommand;
  byId('fb-save-page').disabled=!selected||!canCommand||Array.from(pendingMutations.values()).some(item=>item.type==='page');
  byId('fb-save-draft').disabled=!target||!canCommand||Array.from(pendingMutations.values()).some(item=>item.type==='draft');
  byId('fb-add-account').disabled=!canCommand||Array.from(pendingMutations.values()).some(item=>item.type==='account');
  byId('fb-page').disabled=!selected||(selected.pages||[]).length===0||busy;
  byId('fb-kind').disabled=busy;
  byId('fb-asset').disabled=busy;
  byId('fb-body').disabled=busy;
  const blocked=!item||dirty||hasCommandFor(item)||activeSchedule(item)||['preparing','submitting','processing','published','unknown'].includes(item?.state);
  byId('fb-prepare-post').disabled=blocked||!canCommand;
  byId('fb-review-post').disabled=!item||dirty||hasCommandFor(item)||!canCommand||!item.browser_review||!['review','prepared'].includes(item.state);
  const publishReady=item&&!dirty&&!hasCommandFor(item)&&item.state==='review'&&item.browser_review&&item.review_key&&snapshot.bridge.online&&canCommand;
  byId('fb-confirm-review').disabled=!publishReady;
  byId('fb-publish-post').disabled=!publishReady||!byId('fb-confirm-review').checked;
  const scheduleReady=canSchedule(item)&&canCommand;
  byId('fb-confirm-schedule').disabled=!scheduleReady;
  byId('fb-schedule-post').disabled=!scheduleReady||!byId('fb-confirm-schedule').checked||!byId('fb-schedule-time').value||!byId('fb-schedule-time').checkValidity();
  byId('fb-schedule-time').disabled=!scheduleReady;
  document.dispatchEvent(new CustomEvent('teo-facebook-state',{detail:{snapshot,selectedAccount,selectedPage,busy}}));
 }
 function renderAccounts(){
  const signature=JSON.stringify({accounts:snapshot.accounts.map(item=>({id:item.id,name:item.name,pages:item.pages})),online:!!snapshot.bridge.online});
  if(signature!==accountSnapshot){
   const container=byId('fb-accounts');container.replaceChildren();
   if(!snapshot.accounts.length)container.append(element('p',snapshot.bridge.online?'Chưa có tài khoản. Thêm tên để tạo trình duyệt đăng nhập riêng.':'Chưa nhận danh sách tài khoản từ desktop. Mở bản Tèo mới trên máy xử lý để đồng bộ tài khoản đã lưu.','muted'));
   for(const item of snapshot.accounts){const button=element('button',undefined,'fb-account');button.type='button';button.dataset.accountId=item.id;button.append(element('b',item.name),element('span',`${(item.pages||[]).length} Page đã lưu`));button.addEventListener('click',()=>selectAccount(item.id));container.append(button);}accountSnapshot=signature;
  }
  for(const button of byId('fb-accounts').querySelectorAll('button'))button.setAttribute('aria-pressed',String(button.dataset.accountId===selectedAccount));
  const selected=account();byId('fb-account-title').textContent=selected?`Page của ${selected.name}`:'Chọn tài khoản';
  const options=(selected?.pages||[]).map(item=>({value:pageKey(item),name:item.name||item.url||'Page chưa có tên'}));
  const select=byId('fb-page');const signaturePage=JSON.stringify(options);
  if(select.dataset.options!==signaturePage){select.replaceChildren();if(!options.length){const option=element('option','Chưa có Page · lấy danh sách hoặc lưu bằng link');option.value='';select.append(option);}for(const item of options){const option=element('option',item.name);option.value=item.value;select.append(option);}select.dataset.options=signaturePage;}
  select.value=selectedPage;linkValue(byId('fb-page-link'),page()?.url,'Xem link Page ↗');
 }
 function renderAssets(){
  const select=byId('fb-asset');const signature=JSON.stringify(snapshot.assets);
  if(select.dataset.options===signature)return;
  const previous=select.value;select.replaceChildren();const empty=element('option','Chọn video đã hoàn tất');empty.value='';select.append(empty);
  for(const item of snapshot.assets){const option=element('option',`${item.title||'Video thành phẩm'} · ${item.filename||item.job_id.slice(0,8)}`);option.value=item.job_id;select.append(option);}
  if(snapshot.assets.some(item=>item.job_id===previous))select.value=previous;select.dataset.options=signature;
 }
 function renderReview(){
  const item=post(),review=item?.browser_review;
  const signature=item?.review_key&&review?JSON.stringify({id:item.id,key:item.review_key,review}):'';
  if(signature!==currentReview){byId('fb-confirm-review').checked=false;currentReview=signature;}
  byId('fb-review').classList.toggle('hidden',!review);
  if(review){
   byId('fb-review-title').textContent=item.state==='review'?'Bản thực tế đang chờ duyệt trong Chrome':'Bản xem trước đã lưu từ Chrome';
   byId('fb-review-account').textContent=review.account_name||account()?.name||'';
   byId('fb-review-page').textContent=`${review.target_name||item.target_name||''}${review.target_id?' · ID '+review.target_id:''}`;
   linkValue(byId('fb-review-url'),review.target_url,review.target_url);
   byId('fb-review-kind').textContent=review.kind==='video'?'Video':review.kind==='image'?'Ảnh':'Bài viết chữ';
   byId('fb-review-media').textContent=mediaList(review).length?mediaList(review).map(mediaDescription).join('\n'):'Không có tệp đính kèm';
   byId('fb-review-body').textContent=review.body||'';
  }
  if(item){const status=dirty?'Bạn đã sửa nội dung. Lưu bản nháp mới trước khi chuẩn bị hoặc đăng.':`${labels[item.state]||item.state} · ${item.target_name||page()?.name||'Page'}${item.message?' · '+item.message:''}`;byId('fb-draft-status').textContent=status;}
  else if(!dirty)byId('fb-draft-status').textContent='Chưa lưu bản nháp.';
  renderSchedule();controls();
 }
 function vietnamInputTime(date){const parts=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Ho_Chi_Minh',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(date);const values=Object.fromEntries(parts.map(part=>[part.type,part.value]));return `${values.year}-${values.month}-${values.day}T${values.hour}:${values.minute}`;}
 function renderSchedule(){
  const item=post();const visible=!!item&&item.kind==='text'&&mediaList(item).length===0;byId('fb-schedule').classList.toggle('hidden',!visible);
  if(!visible){byId('fb-confirm-schedule').checked=false;scheduleApproval='';return;}
  const due=byId('fb-schedule-time');due.min=vietnamInputTime(new Date(Date.now()+60000));if(!due.value)due.value=vietnamInputTime(new Date(Date.now()+30*60000));
  const target=(account()?.pages||[]).find(candidate=>pageKey(candidate)===String(item.page_key));
  const identity={id:item.id,body:item.body,target_name:item.target_name||target?.name,target_url:item.target_url||target?.url,target_id:item.target_id||target?.page_id,kind:item.kind,media:mediaList(item),due_at:due.value};
  const signature=JSON.stringify(identity);if(signature!==scheduleApproval){byId('fb-confirm-schedule').checked=false;scheduleApproval=signature;}
  byId('fb-schedule-account').textContent=account()?.name||'';byId('fb-schedule-page').textContent=`${identity.target_name||''}${identity.target_id?' · ID '+identity.target_id:''}`;
  linkValue(byId('fb-schedule-url'),identity.target_url,identity.target_url);byId('fb-schedule-body').textContent=item.body||'';byId('fb-schedule-due').textContent=due.value.replace('T',' ')+' · giờ Việt Nam (UTC+7)';
  const scheduled=activeSchedule(item);byId('fb-schedule-status').textContent=dirty?'Bạn đã sửa lời bài viết. Lưu bản nháp mới trước khi hẹn giờ.':scheduled?`Bài này đã có lịch: ${labels[scheduled.state]||scheduled.state}. ${scheduled.message||'Kiểm tra lịch trên desktop trước khi tạo lại.'}`:'Lịch chỉ áp dụng cho đúng bản nháp chữ đang xem; Tèo tự đăng khi đến giờ bạn cho phép.';
 }
 function renderPosts(){
  const items=snapshot.posts.filter(item=>item.account_id===selectedAccount);byId('fb-post-count').textContent=items.length;
  const signature=JSON.stringify({items,selectedPost});if(signature===postSnapshot)return;
  const container=byId('fb-posts');container.replaceChildren();
  if(!items.length)container.append(element('p','Chưa có bản nháp cho tài khoản này. Chọn Page rồi soạn bài ở phía trên.','muted'));
  for(const item of items){
   const row=element('article',undefined,'fb-post'+(item.id===selectedPost?' active':''));const heading=element('div',undefined,'fb-post-top');heading.append(element('h3',item.target_name||'Page'),badge(item.state));row.append(heading);
   row.append(element('p',item.body||'(Không có lời caption)','fb-post-body'));
   if(mediaList(item).length)row.append(element('p',mediaList(item).map(mediaName).join(' · '),'form-note'));
   if(item.message)row.append(element('p',item.message));
   const timestamp=element('time',timeLabel(item.updated_at||item.created_at));row.append(timestamp);
   const actionsBox=element('div',undefined,'fb-actions');const button=element('button',item.id===selectedPost?'Đang xem':'Xem bản nháp','secondary');button.type='button';button.addEventListener('click',()=>selectPost(item.id));actionsBox.append(button);
   const url=facebookUrl(item.external_url);if(url){const link=element('a','Xem bài trên Facebook ↗','button secondary');link.href=url;link.target='_blank';link.rel='noopener noreferrer';actionsBox.append(link);}row.append(actionsBox);container.append(row);
  }postSnapshot=signature;
 }
 function renderCommands(){
  const items=snapshot.commands.filter(item=>!selectedAccount||item.account_id===selectedAccount).slice(0,20);const signature=JSON.stringify(items);if(signature===commandSnapshot)return;
  const container=byId('fb-commands');container.replaceChildren();if(!items.length)container.append(element('p','Chưa gửi lệnh nào cho tài khoản này.','muted'));
  for(const item of items){const row=element('article',undefined,'fb-command');const heading=element('div',undefined,'fb-command-top');heading.append(element('b',actions[item.action]||item.action),badge(item.state));row.append(heading,element('p',item.message||'Đang chờ máy Tèo xử lý.'),element('time',timeLabel(item.created_at)));container.append(row);}commandSnapshot=signature;
 }
 function renderSchedules(){
  const items=snapshot.schedules.filter(item=>!selectedAccount||item.account_id===selectedAccount);byId('fb-schedule-count').textContent=items.length;const signature=JSON.stringify(items);if(signature===scheduleSnapshot)return;
  const container=byId('fb-schedules');container.replaceChildren();if(!items.length)container.append(element('p','Chưa có lịch đăng.','muted'));
  for(const item of items){const target=(account()?.pages||[]).find(candidate=>pageKey(candidate)===String(item.page_key));const row=element('article',undefined,'fb-command');const heading=element('div',undefined,'fb-command-top');heading.append(element('b',target?.name||'Bài chữ'),badge(item.state));row.append(heading,element('p',typeof item.due_at==='string'?item.due_at.replace('T',' ')+' · UTC+7':new Date(item.due_at*1000).toLocaleString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh'})+' · UTC+7'),element('p',item.message||'Theo dõi lịch trên máy Tèo.'));container.append(row);}scheduleSnapshot=signature;
 }
 function render(){
  const bridge=snapshot.bridge||{};byId('fb-connection').textContent=bridge.online?'● Máy Tèo đang kết nối':bridge.enabled?'○ Chờ máy Tèo kết nối':'○ Chưa bật nhận lệnh';byId('fb-connection').classList.toggle('fb-connection-offline',!bridge.online);
  byId('fb-bridge-message').textContent=(bridge.message||(bridge.online?'Sẵn sàng nhận lệnh và mở trình duyệt trên máy Tèo.':'Mở Tèo desktop và bật nhận lệnh từ web trong phần Quản lý Page.'))+(!bridge.online?' Danh sách bên dưới là dữ liệu đã nhận trước đó; chưa gửi được lệnh mới.':'');
  renderAccounts();renderAssets();renderPosts();renderCommands();renderSchedules();renderReview();
 }
 async function refresh(){
  if(refreshing||busy)return;refreshing=true;
  try{const data=await api('/api/facebook');snapshot={bridge:data.bridge||{},accounts:data.accounts||[],posts:data.posts||[],commands:data.commands||[],assets:data.assets||[],schedules:data.schedules||[],content_connections:data.content_connections||[],content_plans:data.content_plans||[]};
   if(!account()){selectedAccount=snapshot.accounts[0]?.id||'';selectedPage='';selectedPost='';currentReview='';}
   if(!page())selectedPage=pageKey(account()?.pages?.[0]||{});
   if(selectedPost&&!post()){selectedPost='';currentReview='';}
   followMutations();render();notice('fb-poll-error','');
  }catch(error){snapshot.bridge.online=false;byId('fb-connection').textContent='○ Chưa đọc được trạng thái';byId('fb-connection').classList.add('fb-connection-offline');controls();notice('fb-poll-error',error.message==='Failed to fetch'?'Chưa kết nối được máy Tèo. Kiểm tra mạng rồi làm mới.':error.message);}finally{refreshing=false;}
 }
 function resetDraft(){selectedPost='';dirty=false;currentReview='';scheduleApproval='';byId('fb-body').value='';byId('fb-asset').value='';byId('fb-kind').value='text';byId('fb-asset-label').classList.add('hidden');byId('fb-saved-media').classList.add('hidden');byId('fb-confirm-review').checked=false;byId('fb-confirm-schedule').checked=false;}
 function selectAccount(id){if(selectedAccount===id)return;selectedAccount=id;selectedPage=pageKey(account()?.pages?.[0]||{});resetDraft();postSnapshot='';commandSnapshot='';render();}
 function selectPost(id){
  const item=snapshot.posts.find(candidate=>candidate.id===id&&candidate.account_id===selectedAccount);if(!item)return;
  selectedPost=id;selectedPage=String(item.page_key);dirty=false;currentReview='';byId('fb-body').value=item.body||'';byId('fb-kind').value=item.kind==='video'?'video':'text';byId('fb-asset').value=item.job_id||item.video_job_id||'';
  byId('fb-asset-label').classList.toggle('hidden',item.kind!=='video');const media=mediaList(item);byId('fb-saved-media').classList.toggle('hidden',!media.length);byId('fb-saved-media').textContent=media.length?'Tệp đã gắn với bản nháp: '+media.map(mediaName).join(' · '):'';
  postSnapshot='';render();byId('fb-draft-form').scrollIntoView({behavior:'smooth',block:'center'});
 }
 async function perform(operation){if(busy)return;if(!canSend()){notice('fb-error','Máy Tèo chưa sẵn sàng nhận lệnh. Mở Tèo desktop và bật nhận lệnh từ web; các nội dung bạn nhập vẫn được giữ.');return;}busy=true;controls();notice('fb-error','');notice('fb-message','');try{await operation();}catch(error){notice('fb-error',error.message);}finally{busy=false;controls();await refresh();}}
 async function queuedWrite(url,body,type,form){
  const fingerprint=JSON.stringify({url,body});if(!requestKeys.has(fingerprint))requestKeys.set(fingerprint,crypto.randomUUID().replaceAll('-',''));
  const result=await api(url,{...body,request_key:requestKeys.get(fingerprint)});
  const queued=result.command;if(!queued?.id)throw new Error('Chưa nhận được mã lệnh. Kiểm tra tiến độ trên máy Tèo trước khi gửi lại.');
  requestKeys.delete(fingerprint);
  pendingMutations.set(queued.id,{type,accountId:selectedAccount,pageKey:selectedPage,draft:draftFingerprint(),form,fields:form?JSON.stringify(Array.from(form.elements).filter(el=>el.name).map(el=>[el.name,el.value])):''});
  notice('fb-message',queued.message||'Đã gửi yêu cầu lưu về máy Tèo. Theo dõi tiến độ ở bên dưới; danh sách sẽ cập nhật khi máy xử lý xong.');
 }
 function followMutations(){
  for(const [id,pending] of pendingMutations){const command=snapshot.commands.find(item=>item.id===id);if(!command||['queued','pending','running','processing'].includes(command.state))continue;
   pendingMutations.delete(id);if(!['succeeded','completed','done'].includes(command.state)){notice('fb-error',command.message||'Máy Tèo chưa lưu được yêu cầu. Nội dung bạn nhập vẫn được giữ.');continue;}
   if(pending.type==='account'&&draftFingerprint()===pending.draft&&command.account_id&&snapshot.accounts.some(item=>item.id===command.account_id)){selectedAccount=command.account_id;selectedPage=pageKey(account()?.pages?.[0]||{});resetDraft();}
   if(pending.type==='page'&&draftFingerprint()===pending.draft&&selectedAccount===pending.accountId&&command.page_key){selectedPage=String(command.page_key);selectedPost='';currentReview='';dirty=!!byId('fb-body').value;}
   if(pending.type==='draft'&&draftFingerprint()===pending.draft&&command.post_id){selectedPost=command.post_id;dirty=false;currentReview='';}
   if(pending.form&&JSON.stringify(Array.from(pending.form.elements).filter(el=>el.name).map(el=>[el.name,el.value]))===pending.fields)pending.form.reset();
   notice('fb-message',command.message||'Máy Tèo đã lưu xong. Danh sách đã được cập nhật.');
  }
 }
 async function command(action){
  const item=post();const body={action,account_id:selectedAccount};if(page())body.page_key=selectedPage;if(['prepare_post','review_post','publish_post','schedule_post'].includes(action)){if(!item||dirty)throw new Error('Chọn bản nháp đã lưu trước khi gửi lệnh.');body.post_id=item.id;body.page_key=item.page_key;}
  if(action==='publish_post'){
   if(!byId('fb-confirm-review').checked||!item?.review_key||!item.browser_review)throw new Error('Đọc và xác nhận bản xem trước thực tế trước khi đăng.');
   body.review_key=item.review_key;
  }
  if(action==='schedule_post'){
   if(!canSchedule(item)||!byId('fb-confirm-schedule').checked||!byId('fb-schedule-time').value||!byId('fb-schedule-time').reportValidity())throw new Error('Chọn giờ sắp tới và xác nhận đúng Page, lời bài viết trước khi hẹn giờ.');
   body.due_at=byId('fb-schedule-time').value.replace('T',' ');
  }
  const fingerprint=JSON.stringify(body);if(!requestKeys.has(fingerprint))requestKeys.set(fingerprint,crypto.randomUUID().replaceAll('-',''));body.request_key=requestKeys.get(fingerprint);
  const result=await api('/api/facebook/commands',body);requestKeys.delete(fingerprint);
  if(action==='publish_post')byId('fb-confirm-review').checked=false;if(action==='schedule_post')byId('fb-confirm-schedule').checked=false;
  notice('fb-message',result.message||`Đã gửi lệnh “${actions[action]}” tới máy Tèo. Theo dõi tiến độ ở bên dưới.`);
 }
 byId('fb-account-form').addEventListener('submit',event=>{event.preventDefault();const form=event.currentTarget;if(!form.reportValidity()||byId('fb-add-account').disabled)return;perform(()=>queuedWrite('/api/facebook/accounts',{name:form.elements.name.value.trim()},'account',form));});
 byId('fb-page-form').addEventListener('submit',event=>{event.preventDefault();const form=event.currentTarget;if(!form.reportValidity()||byId('fb-save-page').disabled)return;perform(()=>queuedWrite('/api/facebook/pages',{account_id:selectedAccount,name:form.elements.name.value.trim(),url:form.elements.url.value.trim(),page_id:form.elements.page_id.value.trim()},'page',form));});
 byId('fb-draft-form').addEventListener('submit',event=>{event.preventDefault();const form=event.currentTarget;if(!form.reportValidity()||byId('fb-save-draft').disabled)return;if(byId('fb-kind').value==='video'&&!byId('fb-asset').value){notice('fb-error','Chọn video thành phẩm để lưu bản nháp mới. Các bản đã lưu vẫn giữ tệp riêng.');return;}perform(()=>{const body={account_id:selectedAccount,page_key:selectedPage,body:byId('fb-body').value};if(byId('fb-kind').value==='video')body.job_id=byId('fb-asset').value;return queuedWrite('/api/facebook/drafts',body,'draft');});});
 byId('fb-page').addEventListener('change',event=>{selectedPage=event.target.value;resetDraft();render();});
 for(const [id,action] of [['fb-open-account','open_account'],['fb-load-pages','load_pages'],['fb-open-page','open_page'],['fb-prepare-post','prepare_post'],['fb-review-post','review_post'],['fb-publish-post','publish_post'],['fb-schedule-post','schedule_post']])byId(id).addEventListener('click',()=>perform(()=>command(action)));
 for(const id of ['fb-body','fb-kind','fb-asset'])byId(id).addEventListener(id==='fb-body'?'input':'change',()=>{dirty=true;byId('fb-confirm-review').checked=false;byId('fb-confirm-schedule').checked=false;if(id==='fb-kind')byId('fb-asset-label').classList.toggle('hidden',byId('fb-kind').value!=='video');renderReview();});
 byId('fb-confirm-review').addEventListener('change',controls);
 byId('fb-confirm-schedule').addEventListener('change',controls);
 byId('fb-schedule-time').addEventListener('change',()=>{renderSchedule();controls();});
 byId('fb-refresh').addEventListener('click',refresh);
 refresh();setInterval(()=>{if(!document.hidden)refresh();},3000);
})();
