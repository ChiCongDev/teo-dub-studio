"""Small visible-DOM summaries for an unverified Insights result; no auth data."""
SCRIPT = r"""({body, owner}) => {
  const visible=e=>!!e.getClientRects().length && getComputedStyle(e).visibility!=='hidden'
    && !e.closest('[aria-hidden="true"],[inert]');
  const norm=s=>(s||'').normalize('NFC').replace(/\s+/g,' ').trim();
  const captions=[...document.querySelectorAll('div,span,p')]
    .filter(e=>visible(e) && norm(e.innerText)===norm(body));
  const leaves=captions.filter(e=>!captions.some(c=>c!==e && e.contains(c)));
  const descriptor=e=>e?{tag:e.tagName,role:e.getAttribute('role'),label:e.getAttribute('aria-label')}:null;
  const action=s=>norm(s).normalize('NFD').replace(/[\u0300-\u036f]/g,'')
    .replace(/[đĐ]/g,'d').toLowerCase();
  const actionNames=e=>[action(e.innerText),action(e.getAttribute('aria-label'))]
    .filter(s=>['like','thich','comment','leave a comment','binh luan','share','chia se'].includes(s));
  const excluded='nav,[role="navigation"],aside,[role="complementary"],[contenteditable="true"],'
    +'[role="textbox"],textarea,[data-testid*="comment"],[aria-label*="Comment"],'
    +'[aria-label*="comment"],[aria-label*="Bình luận"],[aria-label*="bình luận"]';
  return {caption_count:leaves.length,
    headings:[...document.querySelectorAll('h1,h2,[role="heading"]')].filter(visible)
      .slice(0,12).map(e=>({text:norm(e.innerText),excluded:descriptor(e.closest(excluded))})),
    caption_scopes:leaves.slice(0,2).map(e=>({primary:descriptor(e.closest('main,[role="main"],[role="dialog"]')),
      semantic:descriptor(e.closest('article,[role="article"],[role="group"]')),
      excluded:descriptor(e.closest(excluded))})),
    videos:[...document.querySelectorAll('video')].filter(visible).slice(0,5).map(e=>({
      duration:Number.isFinite(e.duration)?e.duration:0,width:e.videoWidth||0,height:e.videoHeight||0})),
    owner_links:[...document.querySelectorAll('a[href]')].filter(e=>visible(e)
      && norm(e.innerText||e.getAttribute('aria-label'))===norm(owner)).slice(0,5).map(e=>{
        const u=new URL(e.href);return {path:u.pathname,page_id:u.pathname==='/profile.php'?u.searchParams.get('id'):null};}),
    ancestors:leaves.slice(0,2).map(c=>{const rows=[];
      for(let e=c.parentElement;e && rows.length<48;e=e.parentElement)
        rows.push({tag:e.tagName,role:e.getAttribute('role'),label:e.getAttribute('aria-label'),
          excluded:e.matches(excluded),videos:e.querySelectorAll('video').length,
          owners:[...e.querySelectorAll('a[href]')].filter(a=>visible(a)
            && norm(a.innerText||a.getAttribute('aria-label'))===norm(owner)).length,
          actions:[...new Set([...e.querySelectorAll('button,[role="button"]')]
            .filter(visible).flatMap(actionNames))],
          links:e.querySelectorAll('a[href]').length,buttons:e.querySelectorAll('button,[role="button"]').length});
      return rows;})};
}"""
