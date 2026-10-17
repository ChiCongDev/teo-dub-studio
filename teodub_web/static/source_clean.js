'use strict';
// Per-video selection is deliberately separate from personal preferences.
window.TeoSourceCleaner = class {
 constructor(form, api, notice) {
  this.form=form;this.api=api;this.notice=notice;this.selected=null;this.candidate=null;this.region=null;this.generation=0;this.saving=false;
  this.dialog=document.getElementById('clean-dialog');this.video=document.getElementById('clean-source-video');
  this.canvas=document.getElementById('clean-canvas');this.image=document.createElement('canvas');
  this.check=document.getElementById('clean-enabled');this.save=document.getElementById('clean-save');
  document.getElementById('clean-cancel').addEventListener('click',()=>this.dialog.close());
  this.dialog.addEventListener('close',()=>{this.generation++;this.video.pause();this.video.removeAttribute('src');this.video.load();});
  document.getElementById('clean-capture').addEventListener('click',()=>this.capture());
  this.video.addEventListener('loadeddata',()=>this.capture());
  this.video.addEventListener('error',()=>this.notice('clean-error','Chưa mở được video gốc. Tải lại trang hoặc kiểm tra video đã tải.'));
  document.getElementById('edit-clean').addEventListener('click',()=>{if(this.selected)this.open(this.selected.id);});
  this.check.addEventListener('change',()=>this.summary());
  form.elements.input.addEventListener('input',()=>this.reset());
  this.canvas.addEventListener('pointerdown',event=>{
   if(this.saving)return;
   event.preventDefault();this.begin=this.point(event);this.region=null;this.canvas.setPointerCapture(event.pointerId);this.paint();
  });
  this.canvas.addEventListener('pointermove',event=>{if(this.begin)this.drag(event);});
  this.canvas.addEventListener('pointerup',event=>{if(this.begin){this.drag(event);this.begin=null;}});
  this.canvas.addEventListener('pointercancel',()=>{this.begin=null;this.region=null;this.paint();});
  this.save.addEventListener('click',async()=>{
   if(this.saving||!this.valid()||!this.candidate)return;
   const candidate={...this.candidate,region:[...this.region]};const generation=this.generation;this.saving=true;this.save.disabled=true;
   try {
    const result=await this.api(`/api/jobs/${candidate.id}/source`,{subtitle_clean:{enabled:true,region:candidate.region}});
    if(!this.dialog.open||generation!==this.generation)return;
    this.selected={...candidate,region:result.region};this.check.disabled=false;this.check.checked=true;
    form.elements.input.value=this.selected.input;form.elements.title.value=this.selected.title;
    this.summary();this.dialog.close();form.scrollIntoView({behavior:'smooth',block:'start'});
   }catch(error){if(generation===this.generation)this.notice('clean-error',error.message);}
   finally{this.saving=false;this.save.disabled=!this.valid();}
  });
 }
 reset(){this.selected=null;this.check.checked=false;this.check.disabled=true;this.summary();}
 summary(){
  document.getElementById('edit-clean').classList.toggle('hidden',!this.selected);
  document.getElementById('clean-selection').textContent=this.selected?
   `${this.check.checked?'Đã bật GPU':'Đang tắt xóa chữ'} · ${this.selected.title} · vùng đã lưu cho đúng video này.`:
   'Chưa chọn vùng xóa cho video này.';
 }
 payload(){
  if(!this.selected)return {subtitle_clean:{enabled:false}};
  if(this.form.elements.input.value.trim()!==this.selected.input){this.reset();throw new Error('Link đã đổi. Hãy khoanh vùng lại đúng video.');}
  return {source_job_id:this.selected.id,subtitle_clean:this.check.checked?
   {enabled:true,region:[...this.selected.region]}:{enabled:false}};
 }
 async open(ident){
  try {
   const info=await this.api(`/api/jobs/${ident}/source`);
   this.generation++;this.candidate=info;this.region=this.selected?.id===ident?[...this.selected.region]:info.region;
   this.begin=null;this.save.disabled=true;this.canvas.classList.add('hidden');this.notice('clean-error','');
   document.getElementById('clean-source-title').textContent=info.title;
   document.getElementById('clean-region-label').textContent='Đang mở video…';
   this.video.src=info.url;this.dialog.showModal();
  }catch(error){this.notice('form-error',error.message);}
 }
 capture(){
  if(this.saving)return;
  if(this.video.readyState<2||!this.video.videoWidth){this.notice('clean-error','Đợi video tải hình rồi thử lại.');return;}
  this.video.pause();const scale=Math.min(1,1000/this.video.videoWidth,480/this.video.videoHeight);
  this.canvas.width=this.image.width=Math.round(this.video.videoWidth*scale);
  this.canvas.height=this.image.height=Math.round(this.video.videoHeight*scale);
  this.image.getContext('2d').drawImage(this.video,0,0,this.image.width,this.image.height);
  this.canvas.classList.remove('hidden');this.notice('clean-error','');this.paint();
 }
 point(event){const r=this.canvas.getBoundingClientRect();return [Math.max(0,Math.min(1,(event.clientX-r.left)/r.width)),Math.max(0,Math.min(1,(event.clientY-r.top)/r.height))];}
 drag(event){const p=this.point(event);this.region=[Math.min(p[0],this.begin[0]),Math.min(p[1],this.begin[1]),Math.max(p[0],this.begin[0]),Math.max(p[1],this.begin[1])];this.paint();}
 valid(){const r=this.region;return r&&r[2]>r[0]&&r[3]>r[1]&&(r[2]-r[0])*(r[3]-r[1])<=.25;}
 paint(){
  const ctx=this.canvas.getContext('2d');ctx.drawImage(this.image,0,0);const r=this.region;
  if(r){ctx.strokeStyle='#ffe34f';ctx.fillStyle='#ffe34f33';ctx.lineWidth=3;const box=[r[0]*this.canvas.width,r[1]*this.canvas.height,(r[2]-r[0])*this.canvas.width,(r[3]-r[1])*this.canvas.height];ctx.fillRect(...box);ctx.strokeRect(...box);}
  this.save.disabled=!this.valid();
  document.getElementById('clean-region-label').textContent=!r?'Kéo trên ảnh để chọn vùng có chữ.':!this.valid()?
   'Vùng cần có diện tích và không lớn hơn 25% hình. Khoanh lại nhỏ hơn.':
   `Vùng xóa chiếm ${((r[2]-r[0])*(r[3]-r[1])*100).toFixed(1)}% hình. Chữ Việt được chèn sau khi xóa.`;
 }
};
