(()=>{'use strict';
 const label=document.getElementById('fb-page-run-label'),detail=document.getElementById('fb-page-run-detail');
 document.addEventListener('teo-facebook-state',event=>{
  const {snapshot,selectedAccount,selectedPage}=event.detail;
  const row=(snapshot.page_statuses||[]).find(p=>p.account_id===selectedAccount&&p.page_key===selectedPage);
  if(!selectedPage){label.textContent='Chọn Page để xem lịch có đang chạy.';detail.textContent='';return;}
  if(!snapshot.bridge.online){label.textContent='Mất kết nối với máy Tèo';detail.textContent=row?'Trạng thái gần nhất đã lưu: '+row.label+'\n'+row.detail:'Mở Tèo trên máy xử lý để cập nhật trạng thái.';label.style.color='#b45309';return;}
  label.textContent=row?.label||'Chưa nhận được trạng thái — mở bản Tèo mới trên máy xử lý';
  detail.textContent=row?.detail||'';
  label.style.color=['waiting','analyzing','posting','preparing','verifying'].includes(row?.state)?'#15803d':['paused','needs_attention','waiting_executor'].includes(row?.state)?'#b45309':'#475569';
 });
})();
