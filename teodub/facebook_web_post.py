"""Visible Page composer only. No Graph endpoints, authentication data or retries.

The supported layout has one named Create post dialog, one textbox, an explicit
Page identity link, retained selected files and visible removable media previews.
The observed two-step Reel editor is supported only with the same retained video
preview, exact caption, selected Page actor and original in-memory tab binding.
Other layouts stop for manual inspection.
"""
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
import math
from pathlib import Path
import re
import time
from time import time as wall_time
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
import unicodedata

from teodub.common import Cancelled, check_cancel, hash_file
from teodub.facebook_accounts import facebook_url, facebook_post_url
from teodub.page_activity import PageActivity, now
from teodub.page_publish_settings import result_checks_enabled, result_checks_guard
from teodub.facebook_post_timing import UPLOAD_WAIT_SECONDS, PUBLISH_RESULT_SECONDS
from teodub.pipeline import _job_lock


CREATE_NAMES = re.compile(r'^(Create post|Create a post|Tạo bài viết)$', re.I)
PUBLISH_NAMES = re.compile(r'^(Post|Publish|Đăng|Đăng bài)$', re.I)
NEXT_NAMES = re.compile(r'^(Next|Tiếp)$', re.I)
REEL_EDIT_NAMES = re.compile(r'^(Edit reel|Chỉnh sửa thước phim)$', re.I)
REEL_SETTINGS_NAMES = re.compile(r'^(Reel settings|Cài đặt thước phim)$', re.I)
POST_SETTINGS_NAMES = re.compile(r'^(Post settings|Cài đặt bài viết)$', re.I)
PHOTO_NAMES = re.compile(r'^(Photo/video|Photos/videos|Ảnh/video|Ảnh/Video|Ảnh hoặc video)$', re.I)
SWITCH_NAMES = re.compile(r'^(Switch now|Chuyển ngay)$', re.I)
EXPAND_NAMES = re.compile(r'^(See more|Xem thêm)$', re.I)
CALL_PROMPT_NAMES = re.compile(
    r'^(Trò chuyện trực tiếp với mọi người|Talk directly to people|Speak with people directly)$', re.I)
CALL_PROMPT_TEXT = re.compile(r'Gọi ngay|Call now', re.I)
# Facebook shows this prompt right after Publish and waits for "Not now" before posting.
CALL_PROMPT_WAIT_SECONDS = 12
LATER_NAMES = re.compile(r'^(Lúc khác|Not now|Maybe later)$', re.I)
NOTIFICATION_NAMES = re.compile(r'^(Thông báo|Notifications)(?:,.*)?$', re.I)
OPEN_NAMES = re.compile(r"^(Create post|Create a post|Tạo bài viết|Bạn đang nghĩ gì(?: thế)?\??|.+ ơi, bạn đang nghĩ gì thế\??|What's on your mind(?:, .+)?\??)$", re.I)
_HOSTS = {'facebook.com', 'www.facebook.com', 'm.facebook.com'}
INSPECT_RESULT_WAIT_SECONDS = 20
INSPECT_RESULT_NAVIGATION_SECONDS = 15
FINISHED_TAB_SECONDS = 3*60  # A post tab is closed (at the next post's start) this long after its Publish click.

AUTH_SCRIPT = r"""() => {
  const visible = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
  const norm = s => (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g,'')
    .replace(/[đĐ]/g,'d').replace(/\s+/g,' ').trim().toLowerCase();
  const auth = [...document.querySelectorAll('input[type="password"],form[action*="login"]')].some(visible);
  const blocked = [...document.querySelectorAll('h1,h2,[role="heading"],[role="dialog"]')].filter(visible)
    .some(e => /^(security check|confirm your identity|xac nhan danh tinh|kiem tra bao mat|verify your account|xac minh tai khoan)/.test(norm(e.innerText)));
  return {auth, blocked};
}"""

COMPOSER_SCRIPT = r"""dialog => {
  const visible = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden'
    && !e.closest('[aria-hidden="true"],[inert]');
  const norm = s => (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g,'')
    .replace(/[đĐ]/g,'d').replace(/\s+/g,' ').trim().toLowerCase();
  const text = e => (e.innerText || e.getAttribute('aria-label') || '').replace(/\s+/g,' ').trim();
  const editors = [...dialog.querySelectorAll('[role="textbox"][contenteditable="true"],textarea')].filter(visible);
  const links = [...dialog.querySelectorAll('a[href]')].filter(visible).map(a => ({name:text(a),url:a.href}));
  const author_names = [...dialog.querySelectorAll('span,strong,b')].filter(e => visible(e)
    && !e.closest('[contenteditable="true"],textarea') && !e.querySelector('span,strong,b'))
    .map(text).filter(s=>s && s.length<=150);
  const files = [...dialog.querySelectorAll('input[type="file"]')].flatMap(i => [...(i.files || [])]
    .map(f => ({name:f.name,size:f.size,type:f.type,last_modified:f.lastModified})));
  const controls = [...dialog.querySelectorAll('button,[role="button"]')].filter(visible);
  const controlName = e => e.getAttribute('aria-label') || text(e);
  // Removal controls can receive their accessible name from a referenced label.
  // Only this composer's controls/labels may establish its attachment count.
  const removalScope = e => e.closest('[role="dialog"]') === dialog
    && !e.closest('[contenteditable="true"],textarea,[role="textbox"],[aria-hidden="true"],[inert]')
    && !e.querySelector('[contenteditable="true"],textarea,[role="textbox"]');
  const removalName = e => {
    if (e.hasAttribute('aria-labelledby')) {
      const ids = (e.getAttribute('aria-labelledby') || '').trim().split(/\s+/).filter(Boolean);
      if (!ids.length || new Set(ids).size !== ids.length) return '';
      const labels = ids.map(id => e.ownerDocument.getElementById(id));
      if (labels.some(label => !label || !removalScope(label)
        || label.querySelector('[aria-hidden="true"],[inert],[role="dialog"]')
        || visible(label) && [...label.querySelectorAll('*')]
          .some(child => !visible(child) && (child.textContent || '').trim()))) return '';
      // Explicitly referenced hidden labels contribute to native names, while
      // caption content, other dialogs and inert/aria-hidden labels never do.
      return labels.map(label => label.textContent || '').join(' ');
    }
    return controlName(e) || e.getAttribute('title') || '';
  };
  const remove = controls.filter(e => removalScope(e)
    && /^(remove|delete) (photo|image|video)( \d+)?$|^(go|xoa) (anh|video)( \d+)?$|^go file dinh kem trong bai viet$|^remove attachment from post$|^remove post attachment$/.test(norm(removalName(e))));
  const videos = [...dialog.querySelectorAll('video')].filter(visible).map(e=>({src:e.src||e.querySelector('source')?.src||e.currentSrc,poster:e.poster||'',
    duration:Number.isFinite(e.duration)?e.duration:0,width:e.videoWidth||0,height:e.videoHeight||0}));
  const outsideEditor = e => !e.closest('[contenteditable="true"],textarea,[role="textbox"]');
  const statusOnly = e => outsideEditor(e)
    && !e.querySelector('[contenteditable="true"],textarea,[role="textbox"]');
  const marker = [...dialog.querySelectorAll('[aria-label],[role="img"],img')].some(e=>visible(e) && statusOnly(e)
    && /^(da tai len xong|upload complete|uploaded)$/.test(norm(e.getAttribute('aria-label')||e.getAttribute('alt')||'')));
  // Facebook keeps its completed progress indicator visible. Treat it as idle
  // only alongside one actual video/removable preview and one enabled next step.
  // Caption text, a lone 100% badge or an enabled button never proves completion.
  const steps = controls.filter(e => outsideEditor(e)
    && /^(next|tiep|post|publish|dang|dang bai)$/.test(norm(controlName(e))));
  const enabledStep = steps.length === 1 && !steps[0].matches(':disabled')
    && steps[0].getAttribute('aria-disabled') !== 'true';
  const progress = [...dialog.querySelectorAll('[role="progressbar"]')].filter(e => visible(e) && statusOnly(e));
  const completeProgress = e => {
    const raw = e.getAttribute('aria-valuenow');
    if (raw !== null) {
      const number = s => s !== null && s.trim() !== '' ? Number(s) : NaN;
      const current = number(raw), min = e.hasAttribute('aria-valuemin') ? number(e.getAttribute('aria-valuemin')) : 0;
      const max = e.hasAttribute('aria-valuemax') ? number(e.getAttribute('aria-valuemax')) : 100;
      return Number.isFinite(current) && Number.isFinite(min) && Number.isFinite(max)
        && max > min && current === max;
    }
    // A progressbar without a numeric current value is indeterminate, even
    // when its nested text resembles a completed badge.
    return false;
  };
  const pendingProgress = progress.some(e => !completeProgress(e));
  const ariaBusy = [dialog,...dialog.querySelectorAll('[aria-busy="true"]')]
    .some(e => visible(e) && outsideEditor(e) && e.getAttribute('aria-busy') === 'true');
  const statusNodes = [...dialog.querySelectorAll('[role="status"],span,div,p')]
    .filter(e => visible(e) && statusOnly(e));
  const readyReel = statusNodes.some(e => /^your reel is safe to publish!?$/.test(norm(text(e))));
  const badges = statusNodes.filter(e => /^100\s*%$/.test(norm(text(e))) && !e.closest('[role="progressbar"]'));
  const badgeLeaves = badges.filter(e => !badges.some(child => child !== e && e.contains(child)));
  const upload_error = [...dialog.querySelectorAll('[role="alert"],[role="status"]')]
    .some(e => visible(e) && statusOnly(e)
      && /^(upload failed|video upload failed|error uploading(?: video)?|could(?: not|n't) upload|unable to upload|processing failed|video processing failed|khong the tai len|tai len (?:video )?(?:that bai|bi loi)|loi (?:khi )?tai len|xu ly video (?:that bai|bi loi))\b/.test(norm(text(e))));
  const corroborated = videos.length === 1 && !!videos[0].src && remove.length === 1 && enabledStep
    && !ariaBusy && !pendingProgress && !upload_error;
  const percentageComplete = corroborated
    && (progress.length > 0 && progress.every(completeProgress) || badgeLeaves.length === 1 && readyReel);
  const upload_complete = marker || percentageComplete;
  const busy = ariaBusy || pendingProgress || progress.length > 0 && !percentageComplete;
  return {editors:editors.length,body:editors.length === 1 ? (editors[0].value ?? editors[0].innerText ?? '') : '',
    links, author_names, files, preview_count:remove.length, busy, videos, upload_complete, upload_error,
    publish:controls.filter(e => /^(post|publish|dang|dang bai)$/.test(norm(controlName(e)))).length};
}"""

# Publication evidence must be a NEW visible success status containing its own
# concrete permalink, or a newly inserted Page-authored article with exact body.
# A disappearing dialog, unchanged feed, disabled button or click is not evidence.
RESULT_SCRIPT = r"""() => {
  const visible = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
  const norm = s => (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g,'')
    .replace(/[đĐ]/g,'d').replace(/\s+/g,' ').trim().toLowerCase();
  const link = a => ({url:a.href,name:(a.innerText || a.getAttribute('aria-label') || '').replace(/\s+/g,' ').trim()});
  const statuses = [...document.querySelectorAll('[role="status"],[role="alert"]')].filter(visible)
    .filter(e => /^(your (post|reel) (was published|is published|has been published)|post published|reel published|bai viet (da duoc dang|da dang)|da chia se bai viet cua ban|da dang thuoc phim|thuoc phim (da duoc dang|da dang))\b/.test(norm(e.innerText)))
    .map(e => ({text:e.innerText,links:[...e.querySelectorAll('a[href]')].filter(visible).map(link)}));
  const articles = [...document.querySelectorAll('[role="main"] [role="article"],main article')].filter(visible)
    .map(e => ({text:e.innerText,links:[...e.querySelectorAll('a[href]')].filter(visible).map(link)}));
  return {statuses,articles};
}"""

# Read only the published Reel's visible content, never API/authentication data.
# A success toast's root /reel/ link alone cannot prove which Page owns it.
REEL_RESULT_SCRIPT = r"""({body}) => {
  const visible = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden'
    && !e.closest('[aria-hidden="true"],[inert]');
  const norm = s => (s || '').normalize('NFC').replace(/\s+/g,' ').trim();
  const captionText = e => { let text=e.innerText||'';
    const controls=[...e.querySelectorAll('button,[role="button"]')];
    if(controls.some(c=>/^(Ẩn bớt|See less|Show less)$/i.test((c.innerText||'').trim())))
      text=text.replace(/\s*(Ẩn bớt|See less|Show less)\s*$/i,'');
    return text; };
  const excluded = '[role="navigation"],nav,[role="complementary"],aside,[data-testid*="comment"],'
    + '[aria-label*="Bình luận"],[aria-label*="bình luận"],[aria-label*="Comment"],[aria-label*="comment"]';
  const matches = [...document.querySelectorAll('div,span,p')].filter(e => visible(e)
    && !e.closest(excluded) && norm(captionText(e)) === norm(body));
  const captions = matches.filter(e => !matches.some(other => other !== e && e.contains(other)));
  return captions.map(caption => {
    const primary = caption.closest('[role="dialog"],[role="main"],main');
    // Prefer an actual player/content card over its whole main area. Without
    // such grouping, Python requires every observed profile owner to agree.
    const scope = caption.closest('article,[role="article"]')
      || caption.closest('[role="group"]') || primary;
    if (!primary || !scope || !primary.contains(scope)) return null;
    if (!scope) return null;
    const videos = [...scope.querySelectorAll('video')].filter(e => { const r=e.getBoundingClientRect();
      return visible(e) && !e.closest(excluded) && r.bottom>0 && r.top<innerHeight && r.right>0 && r.left<innerWidth; });
    const links = [...scope.querySelectorAll('a[href]')].filter(e => visible(e) && !e.closest(excluded)
      && !caption.contains(e) && e.closest('[role="dialog"],[role="main"],main') === primary)
      .map(a => ({name:norm(a.innerText || a.getAttribute('aria-label')),url:a.href}));
    const primary_links = [...primary.querySelectorAll('a[href]')].filter(e => visible(e) && !e.closest(excluded)
      && !captions.some(item=>item.contains(e)) && e.closest('[role="dialog"],[role="main"],main') === primary)
      .map(a=>({name:norm(a.innerText || a.getAttribute('aria-label')),url:a.href}));
    return {body:captionText(caption),video_count:videos.length,
      video_sources:videos.map(e=>e.src||e.querySelector('source')?.src||e.currentSrc||''),
      videos:videos.map(e=>({duration:Number.isFinite(e.duration)?e.duration:0,
        width:e.videoWidth||0,height:e.videoHeight||0})),links,primary_links};
  }).filter(Boolean);
}"""

# Read one currently visible notification panel in a single DOM observation.
# React can remount its links between Locator calls; per-link inner_text waits
# must not hold the submission observer or mistake a replacement notification.
NOTIFICATION_LINKS_SCRIPT = r"""panels => {
  const visible = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden'
    && !e.closest('[aria-hidden="true"],[inert]');
  const active = panels.filter(visible);
  if (active.length !== 1) return [];
  const panel = active[0];
  return [...panel.querySelectorAll('a[href],[role="link"][href]')]
    .filter(e => visible(e) && e.closest('[role="dialog"]') === panel)
    .map(e => ({url:e.href || new URL(e.getAttribute('href'), document.baseURI).href,
      text:(e.innerText || '').replace(/\s+/g,' ').trim()}));
}"""

# An Insights notification is a reference, never a publication receipt. Only
# explicit visible public links beside the exact caption and saved Page owner
# may resolve it; no Reel URL is manufactured from Facebook's opaque content_id.
INSIGHTS_RESULT_SCRIPT = r"""({body}) => {
  const visible = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden'
    && !e.closest('[aria-hidden="true"],[inert]');
  const norm = s => (s || '').normalize('NFC').replace(/\s+/g,' ').trim();
  const captionText = e => { let text=e.innerText || '';
    if ([...e.querySelectorAll('button,[role="button"]')].filter(visible).some(control=>
      /^(Ẩn bớt|See less|Show less)$/i.test((control.innerText || '').trim())))
      text=text.replace(/\s*(Ẩn bớt|See less|Show less)\s*$/i,'');
    return text; };
  const structuralExcluded = '[contenteditable="true"],[role="textbox"],textarea,[data-testid*="comment"],'
    + '[aria-label*="Comment"],[aria-label*="comment"],[aria-label*="Bình luận"],[aria-label*="bình luận"]';
  const insightsHeadings=[...document.querySelectorAll('h1,h2,[role="heading"]')]
    .filter(e=>visible(e) && !e.closest(structuralExcluded)
      && ['post insights','thông tin chi tiết bài viết'].includes(norm(e.innerText).toLowerCase()));
  const headingLeaves=insightsHeadings.filter(e=>!insightsHeadings.some(other=>other!==e && e.contains(other)));
  const recognizedInsights=location.pathname.replace(/\/$/,'')==='/content/insights'
    && new URL(location.href).searchParams.getAll('content_id').length===1 && headingLeaves.length===1;
  // Facebook renders the published preview in a complementary sidebar beside
  // the statistics main. Only this explicitly recognized Insights view may
  // use that preview or a card outside main; ordinary sidebars stay excluded.
  const excluded=structuralExcluded+(recognizedInsights ? '' : ',nav,[role="navigation"],aside,[role="complementary"]');
  const matches = [...document.querySelectorAll('div,span,p')]
    .filter(e => visible(e) && !e.closest(excluded)
      && !e.querySelector('[contenteditable="true"],[role="textbox"],textarea')
      && norm(captionText(e)) === norm(body));
  const leaves = matches.filter(e => !matches.some(other => other !== e && e.contains(other)));
  const primary = e => e.closest('main,[role="main"],[role="dialog"]'
      +(recognizedInsights ? ',nav,[role="navigation"],aside,[role="complementary"]' : ''))
    || (recognizedInsights ? document.body : null);
  const inPrimary=(e,root)=>root===document.body
    ? !e.closest('[role="dialog"],nav,[role="navigation"],aside,[role="complementary"]') : primary(e)===root;
  // role=group commonly wraps only caption text or player controls in this
  // layout. An article remains a hard content boundary; groups must establish
  // their actual shared caption/player/owner card rather than truncate it.
  const semantic = e => e.closest('article,[role="article"]');
  const action=s=>norm(s).normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/[đĐ]/g,'d').toLowerCase();
  const publicationControls=controls=>controls.some(s=>['like','thich'].includes(s))
    && controls.some(s=>['comment','leave a comment','binh luan'].includes(s))
    && controls.some(s=>['share','chia se'].includes(s));
  const videoVisible = e => { const r=e.getBoundingClientRect();
    return visible(e) && !e.closest(excluded) && r.bottom>0 && r.top<innerHeight
      && r.right>0 && r.left<innerWidth; };
  const profile = e => { try { const u=new URL(e.href);
    return u.protocol==='https:' && ['facebook.com','www.facebook.com','m.facebook.com'].includes(u.hostname)
      && !u.username && !u.password && (!u.port || u.port==='443')
      && (/^\/[0-9]+\/?$/.test(u.pathname) || /^\/pages\/[^/]+\/[0-9]+\/?$/.test(u.pathname)
        || u.pathname==='/profile.php' && /^[0-9]+$/.test(u.searchParams.get('id')||''));
    } catch { return false; } };
  const controlNames=e=>[action(e.innerText),action(e.getAttribute('aria-label'))].filter(Boolean);
  const validControl=(e,root,card)=>visible(e) && !e.parentElement?.closest(excluded)
    && !e.matches('nav,[role="navigation"],[contenteditable="true"],'
      + '[role="textbox"],textarea,[data-testid*="comment"]')
    && inPrimary(e,root) && semantic(e)===(card || null)
    && !leaves.some(caption=>caption.contains(e));
  const content = caption => {
    const root=primary(caption), card=semantic(caption);
    let ownedMediaScope=null;
    if (!root) return null;
    if (card) return primary(card)===root ? card : null;
    // Insights uses ordinary divs for the left post card. Start at the
    // caption and stop at its smallest shared player/owner ancestor; never
    // climb to main and collect navigation or another card's identity.
    for(let scope=caption.parentElement;scope && (scope!==root
      || recognizedInsights && root.matches('aside,[role="complementary"]'));scope=scope.parentElement) {
      if (scope.matches(excluded)) return null;
      const inside=e=>visible(e) && !e.closest(excluded) && inPrimary(e,root) && !semantic(e);
      if ([...scope.querySelectorAll('video')].some(e=>inside(e) && videoVisible(e))) {
        if (ownedMediaScope && [...scope.querySelectorAll('video,a[href],article,[role="article"]')]
          .some(e=>!ownedMediaScope.contains(e) && inside(e)
            && !(e.matches('a[role="button"]') && validControl(e,root,null)
              && controlNames(e).some(name=>['like','thich','comment','leave a comment','binh luan','share','chia se'].includes(name)))))
          return null;
        const ownsVideo=[...scope.querySelectorAll('a[href]')].some(e=>inside(e) && profile(e));
        // A media-only wrapper contains the player controls but omits the
        // published post header/footer. In recognized Insights, stop at the
        // first complete post action card, then require its own numeric owner.
        // An unowned complete card cannot climb farther to borrow a sibling.
        if (recognizedInsights && !publicationControls([...scope.querySelectorAll('button,[role="button"]')]
          .filter(e=>validControl(e,root,null)).flatMap(controlNames))) {
          if (ownsVideo && !ownedMediaScope) ownedMediaScope=scope;
          continue;
        }
        return ownsVideo ? scope : null;
      }
      if (scope===root) break;
    }
    return null;
  };
  const scopes = [...new Set(leaves.map(e => {
    const scope=content(e);
    return scope && primary(scope) && primary(e)===primary(scope) ? scope : null;
  }).filter(Boolean))];
  return scopes.map(scope => {
    const root=primary(scope), card=semantic(scope);
    const inside=e=>visible(e) && !e.closest(excluded) && inPrimary(e,root)
      && semantic(e)===(card || null) && !leaves.some(caption=>caption.contains(e));
    const videos=[...scope.querySelectorAll('video')].filter(e=>inside(e) && videoVisible(e));
    // A real Comment action has that word in its own accessible label. Exclude
    // comment/editor ancestors while allowing this one action's own label.
    const controlInside=e=>validControl(e,root,card);
    const readControls=node=>[...node.querySelectorAll('button,[role="button"]')].filter(controlInside)
      .flatMap(controlNames);
    let controls=readControls(scope);
    if (!card && !publicationControls(controls)) {
      // Facebook may put the action footer just outside the content wrapper.
      // Ascend only while no additional player, profile, card or ordinary link
      // is introduced; such siblings cannot donate publication evidence.
      for(let parent=scope.parentElement;parent && parent!==root;parent=parent.parentElement) {
        const extra=[...parent.querySelectorAll('video,a[href],article,[role="article"]')]
          .filter(e=>!scope.contains(e) && visible(e) && !e.closest(excluded)
            && !(e.matches('a[role="button"]') && controlInside(e) && controlNames(e).some(name=>
              ['like','thich','comment','leave a comment','binh luan','share','chia se'].includes(name))));
        if (extra.length) break;
        const expanded=readControls(parent);
        if (publicationControls(expanded)) { controls=expanded; break; }
      }
    }
    const unfinished=[...root.querySelectorAll('h1,h2,[role="heading"],[role="status"],[role="alert"]')]
      .filter(e=>visible(e) && !e.closest(excluded) && !leaves.some(caption=>caption.contains(e)))
      .some(e=>/^(draft|unpublished|not published|scheduled|uploading|processing|ban nhap|chua dang|da len lich|dang tai len|dang xu ly)\b/.test(action(e.innerText)));
    const composer_open=[...document.querySelectorAll('[role="dialog"]')].filter(visible).some(e=>
      /^(create (?:a )?post|tao bai viet|edit reel|chinh sua thuoc phim|reel settings|cai dat thuoc phim|post settings|cai dat bai viet)$/.test(action(e.getAttribute('aria-label')))
      || !!e.querySelector('[contenteditable="true"],[role="textbox"],textarea'));
    return {body,video_count:videos.length,
      videos:videos.map(e=>({src:e.src||e.querySelector('source')?.src||e.currentSrc||'',
        duration:Number.isFinite(e.duration)?e.duration:0,width:e.videoWidth||0,height:e.videoHeight||0})),
      controls,unfinished,composer_open,
      times:[...scope.querySelectorAll('time[datetime],abbr[data-utime]')].filter(inside)
        .map(e=>({datetime:e.getAttribute('datetime'),utime:e.getAttribute('data-utime')})),
      primary_video_count:[...document.querySelectorAll('video')]
        .filter(e=>visible(e) && !e.closest(excluded) && primary(e)).length,
      primary_links:[...root.querySelectorAll('a[href]')].filter(e=>visible(e) && !e.closest(excluded)
        && inPrimary(e,root) && !leaves.some(caption=>caption.contains(e)))
        .map(e=>({url:e.href,name:norm(e.innerText || e.getAttribute('aria-label'))})),
      links:[...scope.querySelectorAll('a[href]')].filter(inside)
        .map(e=>({url:e.href,name:norm(e.innerText || e.getAttribute('aria-label'))}))};
  });
}"""

# The stopped-attempt inspector has its own observation only. It never invokes
# the composer, uploads a file, clicks Publish, or follows discovered references.
RECONCILE_RESULT_SCRIPT = r"""({body}) => {
  const visible = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden'
    && !e.closest('[aria-hidden="true"],[inert]');
  const norm = s => (s || '').normalize('NFC').replace(/\s+/g,' ').trim();
  const actionName = s => norm(s).normalize('NFD').replace(/[\u0300-\u036f]/g,'')
    .replace(/[đĐ]/g,'d').toLowerCase();
  const composerNames = new Set(['create post','create a post','tao bai viet','edit reel',
    'reel settings','chinh sua thuoc phim','cai dat thuoc phim','post settings','cai dat bai viet']);
  const composer_open = [...document.querySelectorAll('[role="dialog"]')].filter(visible).some(e =>
    composerNames.has(actionName(e.getAttribute('aria-label')))
    || [...e.querySelectorAll('h1,h2,[role="heading"]')].filter(visible)
      .some(heading => composerNames.has(actionName(heading.innerText))));
  const excluded = 'nav,[role="navigation"],aside,[role="complementary"],'
    + '[contenteditable="true"],[role="textbox"],textarea,[data-testid*="comment"],'
    + '[aria-label*="Comment"],[aria-label*="comment"],[aria-label*="Bình luận"],[aria-label*="bình luận"]';
  const captionText = e => { let text=e.innerText || '';
    if ([...e.querySelectorAll('button,[role="button"]')].some(c =>
      /^(Ẩn bớt|See less|Show less)$/i.test((c.innerText || '').trim())))
      text=text.replace(/\s*(Ẩn bớt|See less|Show less)\s*$/i,'');
    return text; };
  const matches = [...document.querySelectorAll('div,span,p')].filter(e => visible(e)
    && !e.closest(excluded) && !e.querySelector('[contenteditable="true"],[role="textbox"],textarea')
    && norm(captionText(e)) === norm(body));
  const captions = matches.filter(e => !matches.some(other => other !== e && e.contains(other)));
  // A matching recommendation card cannot establish the permalink of a
  // different primary player. Ambiguous content anywhere in the result's
  // primary areas is left for inspection, including offscreen player cards.
  const primaryContent = e => visible(e) && !e.closest(excluded)
    && !!e.closest('main,[role="main"],[role="dialog"]');
  const primary_video_count = [...document.querySelectorAll('video')].filter(primaryContent).length;
  const primary_links = [...document.querySelectorAll('a[href]')].filter(e => primaryContent(e)
    && !captions.some(caption => caption.contains(e))).map(e => ({url:e.href,name:norm(e.innerText || e.getAttribute('aria-label'))}));
  const panels = captions.map(caption => {
    const primary = caption.closest('main,[role="main"],[role="dialog"]');
    const scope = caption.closest('article,[role="article"]') || caption.closest('[role="group"]') || primary;
    if (!primary || !scope || !primary.contains(scope)) return null;
    const inScope = e => visible(e) && !e.closest(excluded)
      && e.closest('main,[role="main"],[role="dialog"]') === primary;
    const videos = [...scope.querySelectorAll('video')].filter(e => { const r=e.getBoundingClientRect();
      return inScope(e) && r.bottom>0 && r.top<innerHeight && r.right>0 && r.left<innerWidth; });
    const links = [...scope.querySelectorAll('a[href]')].filter(e => inScope(e) && !caption.contains(e))
      .map(e => ({url:e.href,name:norm(e.innerText || e.getAttribute('aria-label'))}));
    const times = [...scope.querySelectorAll('time[datetime],abbr[data-utime]')]
      .filter(e => inScope(e) && !caption.contains(e))
      .map(e => ({datetime:e.getAttribute('datetime'),utime:e.getAttribute('data-utime')}));
    return {body:captionText(caption),links,times,videos:videos.map(e => ({
      src:e.src || e.querySelector('source')?.src || e.currentSrc || '',
      duration:Number.isFinite(e.duration) ? e.duration : 0,
      width:e.videoWidth || 0,height:e.videoHeight || 0}))};
  }).filter(Boolean);
  return {composer_open,primary_video_count,primary_links,panels};
}"""


def _name(value):
    return ' '.join(unicodedata.normalize('NFC', str(value)).split()).casefold()


def _body(value):
    return str(value).replace('\r\n', '\n').replace('\r', '\n').rstrip('\n')


def _same_body(first, second):
    # Contenteditable renders paragraph breaks as additional visible newlines.
    # Compare ALL text, preserving words/case/punctuation, ignoring only spacing.
    return ' '.join(unicodedata.normalize('NFC', _body(first)).split()) == ' '.join(
        unicodedata.normalize('NFC', _body(second)).split())


def _receipt_url_parts(value):
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or parsed.hostname not in _HOSTS
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443)):
            return None
        return parsed
    except ValueError:
        return None


def _canonical_reel_url(value):
    parsed = _receipt_url_parts(value)
    if parsed and re.fullmatch(r'/reel/[0-9]+/?', parsed.path):
        return 'https://www.facebook.com'+parsed.path.rstrip('/')
    return ''


def _canonical_insights_reference(value):
    parsed = _receipt_url_parts(value)
    if not parsed or parsed.path.rstrip('/') != '/content/insights':
        return ''
    identifiers = parse_qs(parsed.query).get('content_id', [])
    # Observed content IDs are encoded opaque metadata (UzpfST...), not numeric
    # Reel IDs. Keep the value without decoding or assuming what it identifies.
    if (len(identifiers) != 1 or not identifiers[0] or len(identifiers[0]) > 2048
            or any(ord(char) <= 32 or ord(char) == 127 for char in identifiers[0])):
        return ''
    return urlunsplit(('https', 'www.facebook.com', '/content/insights/',
                      urlencode({'content_id': identifiers[0]}), ''))


def _canonical_inspection_url(value):
    """Only a directly supplied/observed public post permalink is inspectable."""
    parsed = _receipt_url_parts(value)
    if not parsed:
        return ''
    reel = _canonical_reel_url(value)
    if reel:
        return reel
    if re.fullmatch(r'/[^/]+/(?:posts|videos)/[0-9]+/?', parsed.path):
        return 'https://www.facebook.com'+parsed.path.rstrip('/')
    if parsed.path in {'/permalink.php', '/story.php'}:
        query = parse_qs(parsed.query)
        if all(len(query.get(key, [])) == 1 and re.fullmatch(r'[0-9]+', query[key][0])
               for key in ('id', 'story_fbid')):
            return urlunsplit(('https','www.facebook.com',parsed.path,
                              urlencode({key:query[key][0] for key in ('id','story_fbid')}),''))
    return ''


def _inspection_publication_time(observations):
    dates = set()
    for observation in observations:
        try:
            if observation.get('datetime') is not None:
                date = datetime.fromisoformat(observation['datetime'].replace('Z', '+00:00'))
                if date.tzinfo is None or date.utcoffset() is None:
                    raise ValueError('Ngày đăng hiển thị chưa có múi giờ rõ ràng.')
            elif re.fullmatch(r'[0-9]+', str(observation.get('utime') or '')):
                date = datetime.fromtimestamp(int(observation['utime']), timezone.utc)
            else:
                raise ValueError('Chưa đọc được thời điểm đăng cụ thể trong video.')
            dates.add(date.astimezone(timezone.utc))
        except (ValueError, OverflowError, OSError) as exc:
            raise ValueError('Chưa đọc được thời điểm đăng cụ thể trong video.') from exc
    if len(dates) > 1:
        raise ValueError('Có nhiều thời điểm đăng trong vùng video; chưa xác định đúng lượt cũ.')
    return next(iter(dates)).isoformat() if dates else ''


def _navigation_interrupted(error):
    """Only a destroyed/replaced DOM is retryable, and only for observation.

    In particular this never authorizes a second publish click or file upload.
    """
    from playwright.sync_api import Error
    return isinstance(error, Error) and any(message in str(error) for message in (
        'Execution context was destroyed', 'Cannot find context with specified id',
        'most likely because of a navigation'))


def _same_video_preview(actual, expected, *, transition=False):
    if len(actual) != 1 or len(expected) != 1 or not actual[0].get('src') or not expected[0].get('src'):
        return False
    current, original = actual[0], expected[0]
    # Poster thumbnails change as Facebook decodes a video; they are not media
    # identity. A recreated player can also receive a new blob after Next.
    if current['src'] == original['src']:
        return True
    parsed = urlsplit(current['src'])
    processed_source = current['src'].startswith('blob:') or (parsed.scheme == 'https'
        and (parsed.hostname or '').endswith('.fbcdn.net'))
    return (transition and processed_source and original['src'].startswith('blob:')
        and original.get('width', 0) > 0 and original.get('height', 0) > 0
        and original.get('duration', 0) > 0
        and current.get('width') == original['width'] and current.get('height') == original['height']
        and abs(current.get('duration', 0)-original['duration']) < .05)


def _shown_name(name):
    """The whole accessible name, any letter case: Facebook may capitalise a Page name saved in lower case."""
    return re.compile(r'^\s*' + r'\s+'.join(map(re.escape, str(name).split())) + r'\s*$', re.I)


def _page_id(url):
    parsed = urlsplit(url)
    if parsed.hostname not in _HOSTS:
        return ''
    bits = parsed.path.strip('/').split('/')
    if parsed.path == '/profile.php':
        value = parse_qs(parsed.query).get('id', [''])[0]
    elif len(bits) == 1 or len(bits) == 3 and bits[0] == 'pages':
        value = bits[-1]
    else:
        value = ''
    return value if re.fullmatch(r'[0-9]+', value) else ''


def _target_url(url):
    """Validate Page-only public URL shapes; a feed/post/login URL is not a Page."""
    url = facebook_url(url, page=True)
    bits = urlsplit(url).path.strip('/').split('/')
    excluded = {'pages', 'login', 'login.php', 'checkpoint', 'recover', 'friends', 'me', 'settings',
                'messages', 'notifications', 'groups', 'watch', 'marketplace', 'gaming', 'reel',
                'reels', 'ads', 'ad_center', 'business', 'help', 'privacy', 'policies', 'bookmarks',
                'home.php', 'photo', 'photo.php', 'stories', 'profile_switcher', 'share'}
    if (urlsplit(url).path != '/profile.php' and not (len(bits) == 1 and bits[0]
            and bits[0].casefold() not in excluded and not bits[0].endswith('.php'))
            and not (len(bits) == 3 and bits[0] == 'pages' and bits[-1].isdigit())):
        raise ValueError('Link đã lưu không phải địa chỉ Page; sửa đúng Page trước khi chuẩn bị.')
    return url


def _one_visible(locator, label):
    found = [locator.nth(i) for i in range(locator.count()) if locator.nth(i).is_visible()]
    if len(found) != 1:
        raise ValueError(f'Không xác định duy nhất {label} ({len(found)} mục). Chrome giữ nguyên để kiểm tra; chưa bấm Đăng.')
    return found[0]


EDITOR_SELECTOR = '[role="textbox"][contenteditable="true"],textarea'
# The same notion of a visible editor as COMPOSER_SCRIPT, which reads the caption that is verified before Publish.
EXPOSED_EDITOR = '''e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden'
    && !e.closest('[aria-hidden="true"],[inert]')'''
# What a stop reports about each candidate editor, for diagnosis: never the caption itself.
EDITOR_DETAIL = '''e => {
    const r = e.getBoundingClientRect(), text = (e.value ?? e.innerText ?? '').trim();
    const label = e.getAttribute('aria-label') || e.getAttribute('placeholder') || e.getAttribute('aria-placeholder') || '';
    const hidden = e.closest('[aria-hidden="true"]') ? ' ẩn(aria-hidden)' : e.closest('[inert]') ? ' ẩn(inert)' : '';
    return `${e.tagName.toLowerCase()} "${label.slice(0, 40)}" ${Math.round(r.width)}x${Math.round(r.height)} ${text.length} ký tự${hidden}`;
}'''


def _editor_details(dialog):
    editors = dialog.locator(EDITOR_SELECTOR)
    return '; '.join(editors.nth(i).evaluate(EDITOR_DETAIL) for i in range(editors.count()) if editors.nth(i).is_visible())


def _caption_editor(dialog, label):
    """The one caption editor that COMPOSER_SCRIPT also sees (22/10/2026: Facebook sometimes keeps a second reel
    editor rendered inside an aria-hidden part of the wizard; the plain visibility check counted two, so videos 13 and
    15 stopped although the verified caption editor was unique). Anything else stops before Publish and names the
    candidates for diagnosis."""
    editors = dialog.locator(EDITOR_SELECTOR)
    found = [editors.nth(i) for i in range(editors.count())
             if editors.nth(i).is_visible() and editors.nth(i).evaluate(EXPOSED_EDITOR)]
    if len(found) != 1:
        raise ValueError(f'Không xác định duy nhất {label} ({len(found)} mục: {_editor_details(dialog)}). '
                         'Chrome giữ nguyên để kiểm tra; chưa bấm Đăng.')
    return found[0]


def _page_main(tab, target):
    """Anchor to the unique visible Page heading, not the global main count.

    Facebook can expose a sidebar main or nested mains alongside the profile.
    The closest main around the exact heading scopes the Page controls.
    """
    # Current Page headers use a named button; feed authors can repeat headings.
    # Use only a unique non-feed header button on the exact numeric Page URL.
    shown = _shown_name(target['name'])
    buttons = tab.get_by_role('main').get_by_role('button', name=shown)
    headers = [buttons.nth(i) for i in range(buttons.count()) if buttons.nth(i).is_visible()
               and buttons.nth(i).evaluate('e => !e.closest("[role=article],article,[role=dialog],[role=navigation],nav,aside,[role=complementary]")')]
    if _page_id(tab.url) == target['page_id'] and headers:
        if len(headers) != 1:
            raise ValueError('Không xác định duy nhất tên Page trong đầu trang; chưa mở hộp bài viết.')
        return _one_visible(headers[0].locator('xpath=ancestor::*[self::main or @role="main"][1]'),
                            'vùng đầu trang mang tên Page')
    heading = _one_visible(tab.get_by_role('main').get_by_role('heading', name=shown),
                           'tên Page trong nội dung chính')
    if _name(heading.inner_text()) != _name(target['name']):
        raise ValueError('Tên Page đang hiển thị khác bản nháp; chưa mở hộp bài viết.')
    return _one_visible(heading.locator('xpath=ancestor::*[self::main or @role="main"][1]'),
                        'vùng nội dung mang tên Page')


def _page_openers(tab, target, main):
    def controls(scope):
        buttons = scope.get_by_role('button', name=OPEN_NAMES)
        return [buttons.nth(i) for i in range(buttons.count()) if buttons.nth(i).is_visible()
                and buttons.nth(i).evaluate('e => !e.closest("[role=article],article,[role=dialog],[role=navigation],nav,aside,[role=complementary]")')]
    found = controls(main)
    if found:
        return found
    # A nested profile header can have the composer beside it in its parent main.
    parents = main.locator('xpath=ancestor::*[self::main or @role="main"]')
    for i in reversed(range(parents.count())):
        found = controls(parents.nth(i))
        if found:
            return found
    # For separate header/feed mains, additionally require the selected actor
    # and numeric Page URL. Only one visible non-article composer is accepted.
    actor = getattr(tab, '_teodub_page_actor', None)
    if actor and actor.get('page_id') == target['page_id'] and _page_id(tab.url) == target['page_id']:
        return controls(tab.get_by_role('main'))
    return []


class BrowserPagePublisher:
    def __init__(self, data_dir):
        self.store = PageActivity(data_dir)

    def _result_checks_enabled(self):
        return result_checks_enabled(self.store.accounts.directory.parent)

    def _result_checks_guard(self):
        return result_checks_guard(self.store.accounts.directory.parent)

    def _dismiss_call_prompt(self, tab, cancel, finished, progress, *, seconds):
        """Press only "Not now" on Facebook's "add a Call Now button" prompt.

        Best effort and bounded: it never clicks Publish, Add Button or anything outside that
        one dialog, and a failure here must not change the recorded one-click submission.
        """
        deadline = time.monotonic()+seconds if seconds else None  # One look needs no clock.
        while True:
            self._cancel(cancel, finished)
            try:
                prompts = tab.get_by_role('dialog', name=CALL_PROMPT_NAMES)
                visible = [prompts.nth(i) for i in range(prompts.count()) if prompts.nth(i).is_visible()]
                if len(visible) == 1 and CALL_PROMPT_TEXT.search(visible[0].inner_text()):
                    later = visible[0].get_by_role('button', name=LATER_NAMES)
                    buttons = [later.nth(i) for i in range(later.count()) if later.nth(i).is_visible()]
                    if len(buttons) == 1:
                        self._cancel(cancel, finished)
                        buttons[0].click(timeout=5000)
                        if progress:
                            progress('Đã bỏ qua gợi ý thêm nút gọi điện của Facebook (Not now).')
                        return True
            except Exception:
                pass  # The page may be navigating; observe again until the deadline.
            if deadline is None or time.monotonic() >= deadline:
                return False
            try:
                tab.wait_for_timeout(500)
            except Exception:
                time.sleep(0.5)

    def _pause_result_verification(self, account_id, page_key, post_id):
        """Keep the one-click record without claiming a Facebook publication."""
        post = self.store.post(account_id, page_key, post_id)
        if post['state'] == 'published':
            return post
        if post['state'] in {'processing', 'unknown'}:
            post = self.store.transition(account_id, page_key, post_id, 'unknown',
                'Đã gửi — chưa xác minh. Kiểm tra trạng thái đang tạm tắt; không tự gửi lại.',
                expected={'processing', 'unknown'})
        return dict(post, verification_paused=True)

    @contextmanager
    def _lock(self, account_id, page_key, post_id):
        self.store.post(account_id, page_key, post_id)
        if not re.fullmatch(r'[0-9a-f]{32}', str(post_id)):
            raise ValueError('Mã bản nháp không hợp lệ.')
        directory = self.store.accounts.directory/'post-locks'/post_id
        directory.mkdir(parents=True, exist_ok=True)
        with _job_lock(directory):
            yield

    @staticmethod
    def _bindings(context):
        # This object exists only in this worker/context. Durable review data never
        # authorizes recreating a lost composer or uploading the attachments again.
        if not hasattr(context, '_teodub_page_post_tabs'):
            context._teodub_page_post_tabs = {}
        return context._teodub_page_post_tabs

    def _key(self, account_id, page_key, post_id):
        return str(self.store.path.resolve()), account_id, page_key, post_id

    @staticmethod
    def _post_tabs(context):
        # Tabs this worker opened for a post attempt. Each attempt opens its own; on 26/10 nineteen were still
        # open (7.8 GB) when one crashed and another stopped answering. Only these are ever closed by Tèo.
        if not hasattr(context, '_teodub_attempt_tabs'):
            context._teodub_attempt_tabs = []
        return context._teodub_attempt_tabs

    def _remember_tab(self, context, tab, account_id, page_key, post_id, attempt):
        self._post_tabs(context).append(dict(tab=tab, key=self._key(account_id, page_key, post_id), attempt=attempt))

    def _attempt_finished(self, entry):
        """The attempt stopped before anything was typed, or its one Publish click is FINISHED_TAB_SECONDS old."""
        _, account_id, page_key, post_id = entry['key']
        try:
            post = self.store.post(account_id, page_key, post_id)
        except (KeyError, ValueError):
            return False
        if post['attempt'] != entry['attempt'] or post['state'] in {'draft', 'failed'}:
            return True
        submitted = (post.get('browser_tracking') or {}).get('submitted_at')
        return (post['state'] in {'unknown', 'published'} and isinstance(submitted, (int, float))
                and not isinstance(submitted, bool) and wall_time()-submitted >= FINISHED_TAB_SECONDS)

    def _close_finished_tabs(self, context):
        """Close earlier attempts' own tabs that are done. A tab that shows an editor (someone may be writing
        there), does not answer, or belongs to an unfinished or uncertain attempt stays open."""
        from teodub.facebook_page_identity import open_editor
        store = str(self.store.path.resolve())
        kept = []
        for entry in self._post_tabs(context):
            tab = entry['tab']
            try:
                if tab.is_closed():
                    continue
                if entry['key'][0] != store or not self._attempt_finished(entry) or open_editor(tab) is not False:
                    kept.append(entry)
                    continue
                tab.close()
            except Exception:
                pass  # Housekeeping only: it never stops the next post.
            if not tab.is_closed():
                kept.append(entry)
                continue
            binding = self._bindings(context).get(entry['key'])
            if binding and binding['tab'] is tab:
                del self._bindings(context)[entry['key']]
        self._post_tabs(context)[:] = kept

    def inspect_result(self, context, account_id, page_key, post_id, *, result_url='',
                       expected_reconcile=None, confirmed_attempt=False,
                       cancel=None, finished=None, progress=None):
        """Observe an old submission's public result; never prepare or send it again.

        The backend rechecks the complete frozen attempt under its own locks.
        Do not hold the publisher's non-reentrant post lock around that commit.
        """
        schedule_id = ''
        if isinstance(expected_reconcile, dict):
            row = expected_reconcile.get('schedule', {})
            if isinstance(row, dict):
                schedule_id = row.get('id', '')
        scope = dict(id=post_id, account_id=account_id, page_key=page_key, schedule_id=schedule_id)
        def unverified(message):
            return dict(scope, state='unverified', message='Chưa xác minh: '+str(message))
        try:
            if not self._result_checks_enabled():
                return dict(unverified('Kiểm tra trạng thái đang tạm tắt; chưa đọc Facebook.'), verification_paused=True)
            from teodub.page_browser_reconcile import preview, commit_verified
            from teodub.page_schedule import PageScheduleStore
            self._cancel(cancel, finished)
            if confirmed_attempt is not True:
                raise ValueError('Cần xác nhận link này là kết quả của đúng lượt đã gửi; chưa cập nhật dữ liệu.')
            schedules = PageScheduleStore(self.store.accounts.directory.parent)
            capture = preview(schedules, account_id, page_key, schedule_id)
            if (capture != expected_reconcile or capture['post']['id'] != post_id
                    or capture['post']['account_id'] != account_id or capture['post']['page_key'] != page_key):
                raise ValueError('Lượt đã gửi đã thay đổi; xem trước lại trước khi kiểm tra kết quả.')
            explicit = _canonical_inspection_url(result_url) if result_url else ''
            if result_url and not explicit:
                raise ValueError('Cần link HTTPS trực tiếp của Reel/bài video Facebook; link Page hoặc Insights chưa đủ.')
            self._cancel(cancel, finished)
            tabs = [tab for tab in context.pages if not tab.is_closed()]
            candidates = [tab for tab in tabs if _canonical_inspection_url(tab.url)
                          and (not explicit or _canonical_inspection_url(tab.url) == explicit)]
            if len(candidates) > 1:
                raise ValueError('Có nhiều tab kết quả; giữ một tab hoặc cung cấp link trực tiếp rõ ràng.')
            if candidates:
                tab = candidates[0]
                url = explicit or _canonical_inspection_url(tab.url)
            elif explicit:
                # Bound only tabs created by this read-only operation. User tabs
                # are never closed, rewritten or reused as upload composers.
                owned = [tab for tab in getattr(context, '_teodub_result_inspection_tabs', []) if not tab.is_closed()]
                if len(owned) >= 4:
                    raise ValueError('Đã mở bốn tab kiểm tra; đóng tab kết quả cũ rồi kiểm tra lại.')
                tab = context.new_page()
                context._teodub_result_inspection_tabs = owned+[tab]
                url = explicit
                self._cancel(cancel, finished)
                tab.goto(url, wait_until='domcontentloaded', timeout=INSPECT_RESULT_NAVIGATION_SECONDS*1000)
            else:
                raise ValueError('Chưa có tab kết quả duy nhất; dán link Reel/bài video thực tế để kiểm tra.')
            if progress:
                progress('Đang đọc bài video thực tế và đối chiếu lượt đã gửi; không tải hoặc đăng lại.')
            deadline = time.monotonic()+INSPECT_RESULT_WAIT_SECONDS
            last = 'Chưa thấy đủ đúng Page, toàn bộ caption và video thực tế trong cùng vùng nội dung.'
            while True:
                if not self._result_checks_enabled():
                    return dict(unverified('Kiểm tra trạng thái đang tạm tắt; đã dừng đọc Facebook.'), verification_paused=True)
                self._cancel(cancel, finished)
                if _canonical_inspection_url(tab.url) != url:
                    raise ValueError('Tab đã chuyển khỏi link kết quả đã chọn; dữ liệu được giữ nguyên.')
                try:
                    auth = tab.evaluate(AUTH_SCRIPT)
                    if auth.get('auth') or auth.get('blocked'):
                        raise ValueError('Facebook yêu cầu đăng nhập/xác minh; chưa cập nhật kết quả.')
                    observed = tab.evaluate(RECONCILE_RESULT_SCRIPT, dict(body=capture['post']['body']))
                    if observed['composer_open']:
                        raise ValueError('Đây còn là hộp soạn bài; chưa phải bằng chứng bài đã đăng.')
                    evidence, last = self._inspection_evidence(observed, url, capture, explicit, confirmed_attempt)
                    if evidence:
                        # Revalidate the DOM and URL after backend hashing and
                        # immediately before commit, including user navigation.
                        def still_current():
                            if not self._result_checks_enabled():
                                raise ValueError('Kiểm tra trạng thái đang tạm tắt; chưa ghi xác nhận đã đăng.')
                            self._cancel(cancel, finished)
                            if _canonical_inspection_url(tab.url) != url:
                                raise ValueError('Tab đã đổi trong lúc kiểm tra; chưa lưu kết quả.')
                            auth_now = tab.evaluate(AUTH_SCRIPT)
                            if auth_now.get('auth') or auth_now.get('blocked'):
                                raise ValueError('Phiên Facebook đã thay đổi; chưa lưu kết quả.')
                            current = tab.evaluate(RECONCILE_RESULT_SCRIPT, dict(body=capture['post']['body']))
                            fresh, _ = self._inspection_evidence(current, url, capture, explicit, confirmed_attempt)
                            if current != observed or not fresh or {key:value for key,value in fresh.items() if key != 'checked_at'} != {
                                    key:value for key,value in evidence.items() if key != 'checked_at'}:
                                raise ValueError('Nội dung kết quả đã đổi trong lúc kiểm tra; chưa lưu kết quả.')
                            self._cancel(cancel, finished)
                            if _canonical_inspection_url(tab.url) != url:
                                raise ValueError('Tab đã đổi trước khi lưu; chưa cập nhật kết quả.')
                        still_current()
                        with self._result_checks_guard() as checks_enabled:
                            if not checks_enabled:
                                return dict(unverified(
                                    'Kiểm tra trạng thái đang tạm tắt; chưa ghi xác nhận đã đăng.'),
                                    verification_paused=True)
                            result = commit_verified(schedules, capture, evidence, check_cancel=still_current)
                        return dict(result, **scope)
                except Exception as exc:
                    if not _navigation_interrupted(exc):
                        raise
                    last = 'Facebook đang thay nội dung trang; chưa đủ bằng chứng kết quả.'
                if time.monotonic() >= deadline:
                    if not self._result_checks_enabled():
                        return dict(unverified(
                            'Kiểm tra trạng thái đang tạm tắt; đã dừng đọc Facebook.'),
                            verification_paused=True)
                    return unverified(last)
                tab.wait_for_timeout(min(250, max(1, int((deadline-time.monotonic())*1000))))
        except Exception as exc:
            if not self._result_checks_enabled():
                return dict(unverified(
                    'Kiểm tra trạng thái đang tạm tắt; chưa ghi xác nhận đã đăng.'),
                    verification_paused=True)
            return unverified(str(exc))

    @staticmethod
    def _inspection_evidence(observed, url, capture, explicit, confirmed_attempt):
        """Bind one visible primary player to its full caption and Page owner."""
        from teodub.page_browser_reconcile import association_matches
        if observed.get('composer_open'):
            raise ValueError('Hộp soạn bài còn mở; chưa xác minh bài đã đăng.')
        target, post, expected = capture['target'], capture['post'], capture['expected_video']
        if observed.get('primary_video_count') != 1:
            return None, 'Có nhiều hoặc chưa có player duy nhất trong kết quả; không lấy video đề xuất làm bài đã chọn.'
        for link in observed.get('primary_links', []):
            try:
                owner = _target_url(link['url']) if _receipt_url_parts(link['url']) else ''
            except ValueError:
                continue
            if owner and _page_id(owner) != target['page_id']:
                return None, 'Có danh tính Page khác trong nội dung kết quả; chưa xác định đúng bài đã chọn.'
        valid = []
        for panel in observed.get('panels', []):
            if not _same_body(panel.get('body', ''), post['body']) or len(panel.get('videos', [])) != 1:
                continue
            video = panel['videos'][0]
            if (not video.get('src') or not all(isinstance(video.get(key), (int,float))
                    and not isinstance(video.get(key), bool) and math.isfinite(video[key]) and video[key]>0
                    for key in ('duration','width','height'))):
                continue
            if (abs(video['duration']-expected['duration'])>2
                    or abs((video['width']/video['height'])/(expected['width']/expected['height'])-1)>.03):
                continue
            owners, authors = [], []
            for link in panel.get('links', []):
                if not _receipt_url_parts(link['url']):
                    continue
                try:
                    owner = _target_url(link['url'])
                except ValueError:
                    continue
                owners.append(owner)
                if (_page_id(owner) == target['page_id'] and _name(re.sub(
                        r',\s*see owner profile$', '', link['name'], flags=re.I)) == _name(target['name'])):
                    authors.append(link)
            if not authors or any(_page_id(owner) != target['page_id'] for owner in owners):
                continue
            published_at = _inspection_publication_time(panel.get('times', []))
            valid.append((video, authors[0], published_at))
        if not valid or len({(v['src'],v['duration'],v['width'],v['height'],date) for v,_,date in valid}) != 1:
            return None, 'Chưa thấy duy nhất video có đúng Page, toàn bộ caption, thời lượng và tỷ lệ đã duyệt.'
        video, author, published_at = valid[0]
        evidence = dict(source='visible_dom', url=url, checked_at=now(), preview_key=capture['preview_key'],
            target_id=target['page_id'], body=post['body'], owner_url=author['url'], visible_video=True,
            video_verified=True, attempt_confirmed=True, observed_duration=video['duration'],
            observed_width=video['width'], observed_height=video['height'])
        if published_at:
            if not association_matches(capture, published_at):
                raise ValueError('Ngày đăng hiển thị không xác định đúng lượt đã gửi này; chưa lưu kết quả.')
            evidence.update(association_mode='visible_time', timestamp_source='visible_dom', published_at=published_at)
        elif len(capture['attempt_candidates']) == 1 and explicit and confirmed_attempt is True:
            evidence.update(association_mode='unique_attempt_user_link', confirmed_attempt=True, explicit_result_url=explicit)
        else:
            return None, ('Không có thời điểm đăng rõ ràng: cần một lượt gửi duy nhất và link do bạn xác nhận; '
                          'nếu có nhiều lượt giống nhau cần ngày đăng để đối chiếu.')
        return evidence, ''

    @staticmethod
    def _cancel(cancel, finished):
        check_cancel(cancel)
        if finished is not None and finished.is_set():
            raise Cancelled('Đã dừng thao tác bài để đóng Chrome. Kiểm tra bài trên Page trước khi làm tiếp.')

    def _post_target(self, account_id, page_key, post_id):
        target = self.store.page(account_id, page_key)
        post = self.store.post(account_id, page_key, post_id)
        if post['delivery_mode'] != 'browser':
            raise ValueError('Bản nháp này chọn API. Tạo/lưu bản nháp qua trình duyệt trước khi chuẩn bị.')
        page_id = str(target.get('page_id', ''))
        if not re.fullmatch(r'[0-9]+', page_id):
            raise ValueError('Cần lưu Page ID bằng số và đúng tên Page trước khi dùng trình duyệt; Tèo không đoán danh tính.')
        saved_url = _target_url(target['url'])
        if (_page_id(saved_url) and _page_id(saved_url) != page_id or post['target_id'] != page_id
                or _target_url(post['target_url']) != saved_url or _name(post['target_name']) != _name(target['name'])):
            raise ValueError('Tên/link/Page ID đã đổi so với bản nháp; chưa chuẩn bị hoặc đăng. Tạo bản nháp mới theo Page hiện tại.')
        if post['archived'] or post['external_id'] or post['external_url']:
            raise ValueError('Bài đã bỏ khỏi danh sách hoặc đã có kết quả; không gửi lại.')
        return post, target

    def _safe_page(self, tab, target):
        parsed = urlsplit(tab.url)
        if (parsed.hostname not in _HOSTS or parsed.path.startswith(('/login', '/checkpoint', '/recover', '/two_step_verification'))):
            raise ValueError('Facebook yêu cầu đăng nhập/xác minh hoặc đã chuyển khỏi Page. Bạn tự thao tác trong Chrome; Tèo dừng bài này.')
        auth = tab.evaluate(AUTH_SCRIPT)
        if auth.get('auth') or auth.get('blocked'):
            raise ValueError('Facebook đang yêu cầu đăng nhập/xác minh. Bạn tự thao tác trong Chrome; chưa chuẩn bị/đăng bài.')
        current = _target_url(tab.url)
        current_id = _page_id(current)
        if current != _target_url(target['url']) and current_id != target['page_id']:
            raise ValueError('Tab đã chuyển sang Page khác; chưa bấm Đăng. Kiểm tra danh tính trong Chrome.')
        if current_id and current_id != target['page_id']:
            raise ValueError('Page ID trên thanh địa chỉ khác bản nháp; chưa bấm Đăng.')

    def _dialog(self, tab):
        dialogs = tab.get_by_role('dialog').filter(has=tab.get_by_role('heading', name=CREATE_NAMES))
        # Facebook may use aria-label for the dialog instead of a nested heading.
        if not any(dialogs.nth(i).is_visible() for i in range(dialogs.count())):
            dialogs = tab.get_by_role('dialog', name=CREATE_NAMES)
        # Current Facebook nests the titled header dialog in the full composer.
        # Resolve the editable composer; a header-only wrapper is not another post.
        candidates = []
        for i in range(dialogs.count()):
            candidate = dialogs.nth(i)
            if not candidate.is_visible():
                continue
            editors = candidate.locator('[role="textbox"][contenteditable="true"],textarea')
            if sum(editors.nth(j).is_visible() for j in range(editors.count())) == 1:
                candidates.append(candidate)
        leaves = []
        for candidate in candidates:
            nested = False
            for other in candidates:
                handle = other.element_handle()
                try:
                    if candidate.evaluate('(e, child) => e !== child && e.contains(child)', handle):
                        nested = True
                        break
                finally:
                    if handle is not None:
                        handle.dispose()
            if not nested:
                leaves.append(candidate)
        if len(leaves) != 1:
            raise ValueError(f'Không xác định duy nhất hộp Tạo bài viết ({len(leaves)} mục). '
                             'Chrome giữ nguyên để kiểm tra; chưa bấm Đăng.')
        return leaves[0]

    def _observe(self, tab, target):
        self._safe_page(tab, target)
        dialog = self._dialog(tab)
        data = dialog.evaluate(COMPOSER_SCRIPT)
        if data['editors'] != 1:
            raise ValueError('Hộp bài viết cần đúng một ô nội dung. Giao diện này chưa được hỗ trợ; giữ Chrome để kiểm tra.')
        identities = []
        for link in data['links']:
            if _name(link['name']) != _name(target['name']):
                continue
            try:
                url = _target_url(link['url'])
            except ValueError:
                continue
            linked_id = _page_id(url)
            if linked_id and linked_id != target['page_id']:
                raise ValueError('Danh tính người đăng trong hộp bài viết có Page ID khác; chưa bấm Đăng.')
            # The explicit author link must match the saved Page, with a numeric
            # ID observed either in that same link or the current Page URL.
            if ((url == _target_url(target['url']) or linked_id == target['page_id'])
                    and (linked_id == target['page_id'] or _page_id(tab.url) == target['page_id'])):
                identities.append(dict(page_id=target['page_id'], name=link['name'], url=url))
        unique = {(x['page_id'], _name(x['name']), x['url']) for x in identities}
        actor = getattr(tab, '_teodub_page_actor', None)
        if not unique and actor and actor['page_id'] == target['page_id'] and _page_id(tab.url) == target['page_id']:
            # Current Facebook can render the author as a plain span. Its name
            # alone never proves identity: require the explicit selected profile
            # checked before preparation, numeric Page URL and visible author.
            if any(_name(name) == _name(target['name']) for name in data.get('author_names', [])):
                identities = [dict(page_id=target['page_id'],name=target['name'],url=_target_url(target['url']),
                                   source='visible_profile_selector_and_composer')]
                unique = {(target['page_id'], _name(target['name']), _target_url(target['url']))}
        if len(unique) != 1:
            raise ValueError('Chưa xác minh được đúng tên và Page ID của người đăng ngay trong hộp bài viết. '
                             'Tèo dừng; bạn có thể tiếp tục thủ công trong Chrome.')
        data['identity'] = identities[0]
        return dialog, data

    @staticmethod
    def _files(post):
        PageActivity.verify_media(post)
        return [dict(saved, sha256=hash_file(saved['path'])) for saved in post['media']]

    @staticmethod
    def _check_files(data, files):
        if files and data.get('upload_error'):
            raise ValueError('Facebook đang hiện cảnh báo/lỗi trong hộp tải tệp; giữ Chrome để kiểm tra, chưa đăng.')
        expected = [(Path(x['path']).name, x['size'], x['mtime_ns']//1000000) for x in files]
        observed = [(x['name'], x['size'], x['last_modified']) for x in data['files']]
        if data['preview_count'] != len(files):
            raise ValueError(f'Chưa xác nhận đúng số tệp đính kèm trong hộp bài viết '
                             f'({data["preview_count"]}/{len(files)}). '
                             'Giữ Chrome để kiểm tra, không tự tải lại.')
        if data['busy']:
            raise ValueError('Tệp vẫn đang tải/xử lý; chưa sẵn sàng đăng.')
        if observed != expected:
            if files and not observed:
                reasons = {
                    'processing': 'Facebook vẫn đang tải hoặc xử lý video/ảnh.',
                    'completion': 'Chưa đủ bằng chứng video/ảnh đã tải xong sau khi ô chọn tệp được làm mới.',
                    'preview': 'Số tệp xem trước không khớp bản nháp.',
                    'identity': 'Danh tính Page của phần xem trước đã thay đổi.',
                    'body': 'Nội dung bài viết của phần xem trước đã thay đổi.',
                    'video': 'Video xem trước đã thay đổi so với tệp đã chọn.',
                }
                detail = reasons.get(data.get('input_release_wait'),
                                     'Chưa đọc lại được dữ liệu tệp đã chọn trong hộp bài viết.')
                raise ValueError(detail+' Giữ Chrome để kiểm tra, không tự tải lại.')
            raise ValueError('Tên, dung lượng hoặc thời gian sửa của tệp trong hộp bài viết khác bản nháp. '
                             'Giữ Chrome để kiểm tra, không tự tải lại.')

    def _existing(self, context, account_id, page_key, post_id, post, target):
        binding = self._bindings(context).get(self._key(account_id, page_key, post_id))
        if not binding or binding['tab'].is_closed() or binding['snapshot'] != post['browser_review']:
            raise ValueError('Tab chuẩn bị gốc không còn hoặc Chrome đã khởi động lại. Kiểm tra Page thủ công; '
                             'Tèo không tạo lại hộp bài/tải lại/gửi lại bản nháp này.')
        tab = binding['tab']
        if post['browser_review'].get('composer', {}).get('layout') == 'post_settings':
            _, data = self._observe_post_settings(tab, target, binding['snapshot'])
            return tab, data
        if post['browser_review'].get('composer', {}).get('layout') == 'reel_settings':
            _, data = self._observe_reel(tab, target, REEL_SETTINGS_NAMES, binding['snapshot']['composer']['video_preview'])
            if not _same_body(data['body'], binding['snapshot']['body']):
                raise ValueError('Lời thước phim đã đổi sau khi duyệt; chưa bấm Đăng.')
            return tab, data
        _, data = self._observe(tab, target)
        snapshot = post['browser_review']
        if (data['identity'] != snapshot['identity'] or not _same_body(data['body'], snapshot['body'])):
            raise ValueError('Danh tính/nội dung trong Chrome đã đổi sau bản xem trước. Kiểm tra thủ công; chưa bấm Đăng.')
        self._check_files(data, snapshot['media'])
        return tab, data

    def _observe_post_settings(self, tab, target, snapshot):
        """The text-post Next step has no file picker and may be read-only.

        It is usable only in the original tab after checking the entire body in
        Create post. If Facebook presents the body/author again, check them too.
        Selected Page is reverified independently immediately before publishing.
        """
        self._safe_page(tab, target)
        actor = getattr(tab, '_teodub_page_actor', None)
        if not actor or actor.get('page_id') != target['page_id']:
            raise ValueError('Chưa giữ được danh tính Page ở bước cài đặt bài viết.')
        heading = _one_visible(tab.get_by_role('heading', name=POST_SETTINGS_NAMES), 'bước cài đặt bài viết')
        # The observed Facebook header is itself a nested dialog. Use its
        # nearest enclosing dialog containing Post, not the header-only wrapper.
        ancestors = heading.locator('xpath=ancestor::*[@role="dialog"]')
        dialog = None
        for index in reversed(range(ancestors.count())):
            candidate = ancestors.nth(index)
            buttons = candidate.get_by_role('button', name=PUBLISH_NAMES)
            if any(buttons.nth(i).is_visible() for i in range(buttons.count())):
                _one_visible(buttons, 'nút Đăng ở cài đặt bài viết')
                dialog = candidate
                break
        if dialog is None:
            raise ValueError('Chưa tìm thấy nút Đăng trong cài đặt bài viết.')
        data = dialog.evaluate(COMPOSER_SCRIPT)
        if data['busy'] or data['files'] or data['preview_count'] or data['videos'] or snapshot['kind'] != 'text':
            raise ValueError('Cài đặt bài chữ xuất hiện tệp hoặc đang xử lý; chưa đăng.')
        if data['editors'] > 1 or data['editors'] == 1 and not _same_body(data['body'], snapshot['body']):
            raise ValueError('Nội dung bài chữ đã đổi sau bước Tiếp; chưa đăng.')
        if data['editors'] == 0:
            matches = dialog.evaluate(r'''(e, body) => {
                const norm=s=>(s||'').normalize('NFC').replace(/\s+/g,' ').trim();
                return [...e.querySelectorAll('div,span,p')].some(n=>n.getClientRects().length
                    && getComputedStyle(n).visibility!=='hidden' && !n.closest('[aria-hidden="true"],button,[role="button"]')
                    && norm(n.innerText)===norm(body));
            }''', snapshot['body'])
            if not matches:
                raise ValueError('Bản xem trước bài chữ không còn đầy đủ nội dung đã duyệt; chưa đăng.')
        for link in data['links']:
            if _name(link['name']) == _name(target['name']) and _page_id(link['url']) not in ('', target['page_id']):
                raise ValueError('Danh tính Page trong cài đặt bài viết đã đổi; chưa đăng.')
        data.update(identity=deepcopy(snapshot['identity']), body=snapshot['body'])
        return dialog, data

    def _advance_text(self, tab, target, dialog, initial, post, *, cancel=None, finished=None):
        # Images retain the existing direct-Post path; no missing attachment may
        # be silently accepted as a text-only settings transition.
        if post['kind'] != 'text' or initial['files'] or initial['preview_count'] or not _same_body(initial['body'], post['body']):
            raise ValueError('Nội dung trước bước Tiếp chưa khớp bài chữ đã duyệt.')
        actor = getattr(tab, '_teodub_page_actor', None)
        if not actor or actor.get('page_id') != target['page_id']:
            raise ValueError('Bài chữ cần danh tính Page đã xác minh trước bước Tiếp.')
        button = _one_visible(dialog.get_by_role('button', name=NEXT_NAMES), 'nút Tiếp của bài chữ')
        self._cancel(cancel, finished)
        button.click(timeout=5000)
        snapshot = dict(kind='text', body=post['body'], identity=initial['identity'])
        deadline = time.monotonic()+20
        while True:
            self._cancel(cancel, finished)
            try:
                final, data = self._observe_post_settings(tab, target, snapshot)
                return final, data
            except ValueError:
                if time.monotonic() >= deadline:
                    raise
                tab.wait_for_timeout(100)

    def _observe_reel(self, tab, target, heading_names, preview, *, transition=False):
        self._safe_page(tab, target)
        actor = getattr(tab, '_teodub_page_actor', None)
        if not actor or actor.get('page_id') != target['page_id'] or _name(actor.get('name')) != _name(target['name']):
            raise ValueError('Chưa giữ được danh tính Page đã xác minh cho thước phim; chưa bấm Đăng.')
        heading = _one_visible(tab.get_by_role('heading', name=heading_names), 'bước thước phim')
        dialog = _one_visible(heading.locator('xpath=ancestor::*[@role="dialog"][1]'), 'hộp thước phim')
        data = dialog.evaluate(COMPOSER_SCRIPT)
        if not data['videos']:
            # Locator traversal also sees open shadow-root players which the
            # composer's querySelectorAll cannot reach.
            players = dialog.locator('video')
            data['videos'] = [players.nth(i).evaluate('''e=>({src:e.src||e.currentSrc,
                poster:e.poster||'',duration:Number.isFinite(e.duration)?e.duration:0,
                width:e.videoWidth||0,height:e.videoHeight||0})''')
                for i in range(players.count()) if players.nth(i).is_visible()]
        if not data['videos'] and len(preview) == 1 and preview[0].get('src'):
            # The Reel editor also renders the uploaded clip onto a canvas.
            # Keep the original upload identity through this same-tab wizard;
            # require its unique labelled preview and actual playback controls.
            panes = dialog.get_by_role('complementary', name=re.compile(r'^(Xem trước|Preview)$', re.I))
            panes = [panes.nth(i) for i in range(panes.count()) if panes.nth(i).is_visible()]
            if len(panes) == 1:
                surfaces = panes[0].locator('canvas')
                surfaces = [surfaces.nth(i) for i in range(surfaces.count()) if surfaces.nth(i).is_visible()
                    and surfaces.nth(i).evaluate('e=>e.width>0&&e.height>0&&e.getBoundingClientRect().width>40&&e.getBoundingClientRect().height>40')]
                play = panes[0].get_by_role('button', name=re.compile(r'^(Tạm dừng video|Phát video|Pause video|Play video)$',re.I))
                positions = panes[0].get_by_role('slider', name=re.compile(r'^(Change Position|Thay đổi vị trí)$',re.I))
                if (len(surfaces) == 1 and sum(play.nth(i).is_visible() for i in range(play.count())) == 1
                        and sum(positions.nth(i).is_visible() for i in range(positions.count())) == 1):
                    data['videos'] = deepcopy(preview)
                    data['preview_observation'] = 'canvas_player_retained_upload'
        if data.get('upload_error'):
            raise ValueError('Facebook đang hiện cảnh báo/lỗi trong bước thước phim; giữ Chrome để kiểm tra, chưa đăng.')
        if not _same_video_preview(data['videos'], preview, transition=transition) or data['busy']:
            reason = ('Chưa đọc được khung phát video trong giao diện Reel.' if not data['videos'] else
                      'Facebook đang xử lý video.' if data['busy'] else 'Khung xem trước chưa khớp video đã tải.')
            raise ValueError('Video xem trước của thước phim chưa sẵn sàng. '+reason+
                             ' Giữ Chrome để kiểm tra; Tèo không tải hay đăng lại.')
        if heading_names is REEL_SETTINGS_NAMES and data['editors'] != 1:
            raise ValueError(f'Không xác định duy nhất lời thước phim ở bước Đăng ({data["editors"]} ô: '
                             f'{_editor_details(dialog)}); giữ Chrome để kiểm tra.')
        return dialog, data

    def _wait_reel(self, tab, target, names, preview, *, cancel=None, finished=None):
        deadline = time.monotonic()+20
        while True:
            self._cancel(cancel, finished)
            try:
                return self._observe_reel(tab, target, names, preview, transition=True)
            except ValueError:
                if time.monotonic() >= deadline:
                    raise
                tab.wait_for_timeout(100)

    def _advance_reel(self, tab, target, dialog, initial, *, cancel=None, finished=None):
        preview = initial['videos']
        already_editing = initial.get('reel_stage') == 'edit'
        if (not already_editing and not initial['upload_complete']) or len(preview) != 1 or not preview[0]['src']:
            raise ValueError('Chưa có bằng chứng video tải xong và bản xem trước duy nhất; chưa chuyển bước thước phim.')
        actor = getattr(tab, '_teodub_page_actor', None)
        if not actor or actor.get('page_id') != target['page_id']:
            raise ValueError('Thước phim cần danh tính Page đã xác minh; giữ Chrome để kiểm tra.')
        self._cancel(cancel, finished)
        if not already_editing:
            _one_visible(dialog.get_by_role('button', name=NEXT_NAMES), 'nút Tiếp của video đã tải').click(timeout=5000)
        edit, edited = self._wait_reel(tab, target, REEL_EDIT_NAMES, preview, cancel=cancel, finished=finished)
        self._cancel(cancel, finished)
        _one_visible(edit.get_by_role('button', name=NEXT_NAMES), 'nút Tiếp của bước chỉnh thước phim').click(timeout=5000)
        final, data = self._wait_reel(tab, target, REEL_SETTINGS_NAMES, edited['videos'], cancel=cancel, finished=finished)
        # File input may disappear in this wizard, so retain its actually observed
        # metadata plus the unchanged preview URI; no new upload or tab is allowed.
        data.update(identity=initial['identity'], files=initial['files'], preview_count=initial['preview_count'])
        return final, data

    def _observe_upload_stage(self, tab, target, retained, *, cancel=None, finished=None):
        deadline = time.monotonic()+20
        while True:
            self._cancel(cancel, finished)
            try:
                dialog, data = self._observe(tab, target)
                # Facebook clears its file picker after uploading. It is not a
                # lost attachment when the same observed video remains, with an
                # explicit upload-complete marker and its removable preview.
                if (retained is not None and not data['files'] and data['upload_complete']
                        and not data['busy'] and _same_video_preview(data['videos'], retained['videos'])
                        and data['preview_count'] == retained['preview_count'] == 1
                        and data['identity'] == retained['identity']
                        and _same_body(data['body'], retained['body'])):
                    data['files'] = deepcopy(retained['files'])
                    data['input_released_after_upload'] = True
                elif retained is not None and not data['files']:
                    # Explain a rejected release without accepting a different
                    # actor, caption or video, or changing the retained binding.
                    checks = (
                        ('processing', data['busy']),
                        ('completion', not data['upload_complete']),
                        ('preview', data['preview_count'] != retained['preview_count'] or data['preview_count'] != 1),
                        ('identity', data['identity'] != retained['identity']),
                        ('body', not _same_body(data['body'], retained['body'])),
                        ('video', not _same_video_preview(data['videos'], retained['videos'])),
                    )
                    data['input_release_wait'] = next((reason for reason, rejected in checks if rejected), '')
                return dialog, data
            except ValueError as exc:
                if retained is None or 'hộp Tạo bài viết (0 mục)' not in str(exc):
                    raise
                try:
                    # Retained metadata came from the selected input in THIS
                    # composer. Only its same actor/video may advance to Edit.
                    dialog, data = self._observe_reel(tab, target, REEL_EDIT_NAMES, retained['videos'], transition=True)
                    data.update({key: retained[key] for key in ('identity', 'files', 'preview_count', 'body')})
                    data['reel_stage'] = 'edit'
                    return dialog, data
                except ValueError:
                    if time.monotonic() >= deadline:
                        raise
                # React can temporarily detach the composer while uploading.
                # Re-observe both stages; do not click/reopen/reupload anything.
                tab.wait_for_timeout(100)

    def review(self, account_id, page_key, post_id):
        post = self.store.post(account_id, page_key, post_id)
        if post['delivery_mode'] != 'browser' or not post['browser_review']:
            raise ValueError('Chưa có bản xem trước nội dung thực tế từ Chrome.')
        return deepcopy(post['browser_review'])

    def record_manual_result(self, account_id, page_key, post_id, url):
        with self._lock(account_id, page_key, post_id):
            return self.store.record_manual_result(account_id, page_key, post_id, url)

    def focus_review(self, context, account_id, page_key, post_id, *, cancel=None, finished=None, progress=None):
        """Only raise the bound original tab; never reload or create a composer."""
        with self._lock(account_id, page_key, post_id):
            post, target = self._post_target(account_id, page_key, post_id)
            if post['state'] != 'review':
                raise ValueError('Bài không còn ở bước xem trước; kiểm tra kết quả trên Page, không tạo lại hộp bài viết.')
            self._cancel(cancel, finished)
            try:
                tab, _ = self._existing(context, account_id, page_key, post_id, post, target)
                tab.bring_to_front()
                return post
            except Exception as exc:
                self.store.transition(account_id, page_key, post_id, 'unknown', str(exc), expected={'review'})
                raise

    def _wait_composer(self, tab, target, *, cancel=None, finished=None, timeout=10):
        deadline = time.monotonic()+timeout
        while True:
            self._cancel(cancel, finished)
            try:
                return self._observe(tab, target)
            except ValueError:
                if time.monotonic() >= deadline:
                    raise
                tab.wait_for_timeout(100)

    def _page_ready(self, tab, target, *, cancel=None, finished=None, timeout=12):
        """The saved Page's main column, read again after any reload Facebook makes (26/10/2026: the reload
        after a profile switch failed a post with "Execution context was destroyed")."""
        from teodub.facebook_page_identity import _reloading
        deadline = time.monotonic()+timeout
        while True:
            self._cancel(cancel, finished)
            try:
                self._safe_page(tab, target)  # Its own refusal (login, another Page) stops at once.
                main = None
                try:
                    main = _page_main(tab, target)
                except ValueError:
                    if time.monotonic() >= deadline:
                        raise
                if main is not None:
                    return main
            except Exception as exc:
                if not _navigation_interrupted(exc) or time.monotonic() >= deadline:
                    raise
                _reloading(tab, deadline)
                continue
            tab.wait_for_timeout(100)

    def _open_composer(self, tab, target, *, cancel=None, finished=None):
        from teodub.facebook_page_identity import PROFILE_NAMES, _reloading, ensure_page_actor
        main = self._page_ready(tab, target, cancel=cancel, finished=finished)
        profile = tab.get_by_role('button', name=PROFILE_NAMES)
        if any(profile.nth(i).is_visible() for i in range(profile.count())):
            tab._teodub_page_actor = ensure_page_actor(tab.context, tab, target, cancel=cancel, finished=finished)
            if tab._teodub_page_actor['switched']:
                deadline = time.monotonic()+45
                while True:
                    try:
                        tab.goto(target['url'], wait_until='domcontentloaded', timeout=45000)
                        break
                    except Exception as exc:
                        # Facebook's own reload after the switch may still land; open the Page again after it.
                        if (not (_navigation_interrupted(exc) or 'interrupted by another navigation' in str(exc))
                                or time.monotonic() >= deadline):
                            raise
                    _reloading(tab, deadline)
            main = self._page_ready(tab, target, cancel=cancel, finished=finished)
        visible = _page_openers(tab, target, main)
        if not visible:
            # Supported Page-identity switch is a single named button beside the
            # exact Page heading on its observed numeric URL. Never select any
            # profile/account from an account menu or an unknown switch dialog.
            if _page_id(tab.url) != target['page_id']:
                raise ValueError('Chưa có nút Tạo bài viết hoặc Page ID trực tiếp để xác minh Chuyển ngay. Dừng để kiểm tra thủ công.')
            switch = _one_visible(main.get_by_role('button', name=SWITCH_NAMES), 'nút Chuyển ngay trên đúng Page')
            self._cancel(cancel, finished)
            switch.click(timeout=5000)
            deadline = time.monotonic()+10
            while True:
                self._cancel(cancel, finished)
                self._safe_page(tab, target)
                main = _page_main(tab, target)
                visible = _page_openers(tab, target, main)
                if visible or time.monotonic() >= deadline:
                    break
                # A switch dialog requires a manual decision; do not operate it.
                if any(tab.get_by_role('dialog').nth(i).is_visible() for i in range(tab.get_by_role('dialog').count())):
                    raise ValueError('Facebook mở hộp chuyển danh tính khác mẫu. Bạn tự kiểm tra trong Chrome; Tèo dừng.')
                tab.wait_for_timeout(100)
        if len(visible) != 1:
            raise ValueError(f'Không xác định duy nhất nút Tạo bài viết của Page ({len(visible)} mục). '
                             'Chrome giữ nguyên để kiểm tra; chưa nhập hoặc bấm Đăng.')
        opener = visible[0]
        self._cancel(cancel, finished)
        opener.click(timeout=5000)
        return self._wait_composer(tab, target, cancel=cancel, finished=finished)

    def prepare(self, context, account_id, page_key, post_id, *, cancel=None, finished=None, progress=None):
        return self._run_steps(self.prepare_steps(context, account_id, page_key, post_id,
            cancel=cancel, finished=finished, progress=progress))

    @staticmethod
    def _run_steps(steps):
        """Synchronous compatibility for callers outside the account worker."""
        while True:
            try:
                next(steps)
            except StopIteration as done:
                return done.value

    def prepare_steps(self, context, account_id, page_key, post_id, *, cancel=None, finished=None, progress=None):
        with self._lock(account_id, page_key, post_id):
            post, target = self._post_target(account_id, page_key, post_id)
            if post['state'] == 'review':
                try:
                    self._cancel(cancel, finished)
                    tab, _ = self._existing(context, account_id, page_key, post_id, post, target)
                    tab.bring_to_front()
                    return post
                except Exception as exc:
                    self.store.transition(account_id, page_key, post_id, 'unknown', str(exc), expected={'review'})
                    raise
            if post['state'] in {'preparing', 'submitting'}:
                self.store.transition(account_id, page_key, post_id, 'unknown',
                    'Lượt trước bị gián đoạn và không còn giữ khóa. Kiểm tra Chrome/Page; không tự làm lại.',
                    expected={post['state']})
                raise ValueError('Lượt trước chưa rõ kết quả; kiểm tra Page thủ công, không gửi lại.')
            if post['state'] not in {'draft', 'failed'}:
                raise ValueError('Bài đã được chuẩn bị/gửi hoặc chưa rõ kết quả; không tạo lại hộp bài viết.')
            self._cancel(cancel, finished)
            files = self._files(post)
            attempt = self.store.transition(account_id, page_key, post_id, 'preparing',
                'Đang chuẩn bị hộp bài viết trong Chrome; chưa bấm Đăng.', expected={'draft', 'failed'})['attempt']
            upload_started = False
            composer_changed = False
            try:
                if progress:
                    progress('Đang mở tab riêng tới đúng Page và kiểm tra danh tính…')
                self._close_finished_tabs(context)
                tab = context.new_page()
                self._remember_tab(context, tab, account_id, page_key, post_id, attempt)
                tab.goto(target['url'], wait_until='domcontentloaded', timeout=45000)
                tab.bring_to_front()
                self._cancel(cancel, finished)
                self._safe_page(tab, target)
                dialog, data = self._open_composer(tab, target, cancel=cancel, finished=finished)
                if _body(data['body']) or data['files'] or data['preview_count']:
                    raise ValueError('Hộp bài viết có nội dung/tệp từ trước. Giữ nguyên để bạn kiểm tra; Tèo không ghi đè.')
                editor = _one_visible(dialog.locator('[role="textbox"][contenteditable="true"],textarea'), 'ô nội dung bài viết')
                self._cancel(cancel, finished)
                composer_changed = True
                editor.fill(post['body'], timeout=5000)
                if files:
                    if progress:
                        progress('Đang tải tệp bạn đã duyệt vào hộp bài viết. Chưa đăng công khai…')
                    inputs = dialog.locator('input[type="file"]')
                    # Selection is intentionally conservative: only an input scoped to
                    # this composer, not a hidden uploader elsewhere on the Page.
                    if inputs.count() == 0:
                        photo = _one_visible(dialog.get_by_role('button', name=PHOTO_NAMES), 'nút Ảnh/video trong hộp bài viết')
                        self._cancel(cancel, finished)
                        photo.click(timeout=5000)
                        dialog, data = self._observe(tab, target)
                        inputs = dialog.locator('input[type="file"]')
                    suitable = []
                    for i in range(inputs.count()):
                        candidate = inputs.nth(i)
                        accept = (candidate.get_attribute('accept') or '').lower()
                        media_type = 'image' if post['kind'] == 'image' else 'video'
                        if not accept or media_type in accept or all(Path(f['path']).suffix.lower() in accept for f in files):
                            suitable.append(candidate)
                    if len(suitable) != 1:
                        raise ValueError('Không xác định được duy nhất ô tải ảnh/video của hộp bài viết; chưa tải tệp.')
                    if len(files) > 1 and suitable[0].get_attribute('multiple') is None:
                        raise ValueError('Ô tải tệp hiện tại không nhận nhiều ảnh. Giữ tab để tiếp tục thủ công.')
                    self._cancel(cancel, finished)
                    upload_started = True  # Persisted preparing plus OS lock protects an interrupted upload.
                    upload_input = suitable[0].element_handle()
                    if upload_input is None:
                        raise ValueError('Ô tải tệp vừa được Facebook thay đổi; chưa chọn lại tệp.')
                    try:
                        # Capture the real selected File metadata during the input
                        # event, before React may clear/remove the file picker.
                        upload_input.evaluate(r'''e => {
                            e.__teoSelectedFiles = [];
                            e.addEventListener('input', () => {
                                e.__teoSelectedFiles = [...(e.files || [])].map(f => ({
                                    name:f.name,size:f.size,type:f.type,last_modified:f.lastModified}));
                            }, {once:true,capture:true});
                        }''')
                        upload_input.set_input_files([f['path'] for f in files], timeout=5000)
                        selected_files = upload_input.evaluate('e => e.__teoSelectedFiles || []')
                    finally:
                        upload_input.dispose()
                    expected = [(Path(x['path']).name, x['size'], x['mtime_ns']//1000000) for x in files]
                    witnessed = [(x['name'], x['size'], x['last_modified']) for x in selected_files]
                    if witnessed != expected:
                        raise ValueError('Chưa xác nhận được tệp đã chọn đúng với video lưu trong lịch; không tự tải lại.')
                    if progress:
                        progress(f'Đã chọn tệp đã duyệt; đang chờ Facebook tải/xử lý xong '
                                 f'(tối đa {UPLOAD_WAIT_SECONDS//60} phút). Chưa bấm Đăng.')
                    deadline = time.monotonic()+UPLOAD_WAIT_SECONDS
                    retained_upload = None
                    last_upload_wait = None
                    while True:
                        self._cancel(cancel, finished)
                        dialog, data = self._observe_upload_stage(tab, target, retained_upload,
                            cancel=cancel, finished=finished)
                        if (retained_upload is None and post['kind'] == 'video' and not data.get('reel_stage')
                                and _same_body(data['body'], post['body'])
                                and len(data['videos']) == 1 and data['videos'][0]['src']):
                            expected = [(Path(x['path']).name, x['size'], x['mtime_ns']//1000000) for x in files]
                            observed = [(x['name'], x['size'], x['last_modified']) for x in data['files']]
                            if (expected == observed or not observed) and data['preview_count'] == len(files):
                                # Freeze the first preview bound to the witnessed
                                # input. A later empty picker must never rebind a
                                # replacement video to the original selected file.
                                retained_upload = deepcopy(data)
                                retained_upload['files'] = deepcopy(selected_files)
                        try:
                            self._check_files(data, files)
                            if post['kind'] == 'video':
                                videos = data.get('videos', [])
                                if len(videos) != 1 or not all(isinstance(videos[0].get(key), (int, float))
                                        and not isinstance(videos[0].get(key), bool)
                                        and math.isfinite(videos[0][key]) and videos[0][key] > 0
                                        for key in ('duration', 'width', 'height')):
                                    raise ValueError('Chờ đọc thời lượng và kích thước video đã tải để xác minh đúng bài về sau.')
                            break
                        except ValueError as exc:
                            detail = str(exc)
                            if progress and detail != last_upload_wait:
                                progress('Đang chờ Facebook: '+detail)
                                last_upload_wait = detail
                            if time.monotonic() >= deadline:
                                raise ValueError(f'Tệp chưa đủ bằng chứng tải/xử lý xong sau '
                                    f'{UPLOAD_WAIT_SECONDS//60} phút. '+str(exc)) from exc
                            yield dict(phase='preparing')
                            tab.wait_for_timeout(250)
                if data.get('reel_stage') == 'edit':
                    self._observe_reel(tab, target, REEL_EDIT_NAMES, data['videos'])
                elif files and post['kind'] == 'video':
                    dialog, data = self._observe_upload_stage(tab, target, retained_upload,
                        cancel=cancel, finished=finished)
                else:
                    dialog, data = self._observe(tab, target)
                if not _same_body(data['body'], post['body']):
                    raise ValueError('Nội dung thực tế trong Chrome khác bản nháp; giữ tab để kiểm tra, chưa đăng.')
                self._check_files(data, files)
                reel = False
                post_settings = False
                if post['kind'] == 'video' and not data['publish']:
                    dialog, data = self._advance_reel(tab, target, dialog, data, cancel=cancel, finished=finished)
                    reel = True
                    # Facebook's wizard can omit or flatten the earlier caption.
                    # This is our original composer with the verified same video;
                    # explicitly enter the frozen caption in its final editor.
                    self._cancel(cancel, finished)
                    editor = _caption_editor(dialog, 'ô mô tả thước phim cuối cùng')
                    editor.fill(post['body'], timeout=5000)
                    retained = {key: data[key] for key in ('identity', 'files', 'preview_count')}
                    dialog, data = self._observe_reel(tab, target, REEL_SETTINGS_NAMES, data['videos'])
                    data.update(retained)
                    if not _same_body(data['body'], post['body']):
                        raise ValueError('Lời thước phim khác bản nháp; chưa bấm Đăng.')
                elif post['kind'] == 'text' and not data['publish']:
                    if progress:
                        progress('Đang chuyển từ nội dung bài chữ sang cài đặt đăng bài…')
                    dialog, data = self._advance_text(tab, target, dialog, data, post,
                        cancel=cancel, finished=finished)
                    post_settings = True
                # All supported types end at the same single, unambiguous final button.
                button = _one_visible(dialog.get_by_role('button', name=PUBLISH_NAMES), 'nút Đăng cuối cùng trong hộp bài viết')
                if not button.is_enabled():
                    raise ValueError('Nút Đăng chưa sẵn sàng; video có thể cần bước khác. Tèo dừng để bạn kiểm tra thủ công.')
                self._cancel(cancel, finished)
                accounts = self.store.accounts.load()['accounts']
                snapshot = dict(version=1, account_id=account_id, account_name=next(x['name'] for x in accounts if x['id'] == account_id),
                    page_key=page_key, post_id=post_id, prepared_at=now(), target_name=post['target_name'],
                    target_url=post['target_url'], target_id=post['target_id'], body=_body(post['body']), kind=post['kind'],
                    media=files, observed_files=data['files'], identity=data['identity'], browser_finish_mode=post['browser_finish_mode'],
                    composer=dict(url=tab.url, body=_body(post['body']), observed_body=_body(data['body']),
                        attachment_count=len(data['files']), preview_count=data['preview_count']))
                if post['kind'] == 'video':
                    snapshot['composer']['video_preview'] = deepcopy(data['videos'])
                if reel:
                    snapshot['composer'].update(layout='reel_settings', video_preview=data['videos'])
                    if data.get('preview_observation'):
                        snapshot['composer']['preview_observation'] = data['preview_observation']
                elif post_settings:
                    snapshot['composer']['layout'] = 'post_settings'
                result = self.store.save_browser_review(account_id, page_key, post_id, snapshot)
                self._bindings(context)[self._key(account_id, page_key, post_id)] = dict(tab=tab, snapshot=deepcopy(snapshot))
                if progress:
                    progress('Đã tự đối chiếu đúng Page, nội dung và video; sẵn sàng đăng.' if post['browser_finish_mode'] == 'tool'
                        else 'Đã lưu bản xem trước thực tế. Chrome giữ hộp bài viết mở để bạn bấm Đăng.')
                return result
            except BaseException as exc:
                if self.store.post(account_id, page_key, post_id)['state'] == 'preparing':
                    self.store.transition(account_id, page_key, post_id, 'unknown' if upload_started or composer_changed else 'failed',
                                          str(exc) or 'Đã dừng chuẩn bị; kiểm tra Chrome trước khi thao tác lại.', expected={'preparing'})
                raise

    def publish(self, context, account_id, page_key, post_id, *, cancel=None, finished=None, progress=None):
        return self._run_steps(self.publish_steps(context, account_id, page_key, post_id,
            cancel=cancel, finished=finished, progress=progress))

    def publish_steps(self, context, account_id, page_key, post_id, *, cancel=None, finished=None, progress=None):
        """Publish an approved draft or armed appointment after automatic review."""
        with self._lock(account_id, page_key, post_id):
            post, target = self._post_target(account_id, page_key, post_id)
            if post['state'] in {'preparing', 'submitting'}:
                self.store.transition(account_id, page_key, post_id, 'unknown',
                    'Lượt trước bị gián đoạn; kiểm tra Chrome/Page trước. Tèo không gửi lại.', expected={post['state']})
                raise ValueError('Lượt trước bị gián đoạn; không tự bấm Đăng lại.')
            if post['state'] != 'review' or not post['browser_review']:
                raise ValueError('Cần chuẩn bị và duyệt bản xem trước thực tế trước khi đăng; không tự gửi lại.')
            if post['manual_url']:
                raise ValueError('Bạn đã ghi link bài kiểm tra thủ công. Tèo không bấm Đăng lại; mở link này để kiểm tra kết quả.')
            if post['browser_finish_mode'] != 'tool' or post['browser_review']['browser_finish_mode'] != 'tool':
                raise ValueError('Bài này chọn bạn tự bấm Đăng trong Chrome. Tèo không bấm nút Đăng.')
            submitted = False
            try:
                self._cancel(cancel, finished)
                actual_files = self._files(post)
                if actual_files != post['browser_review']['media']:
                    raise ValueError('Tệp trên máy đã đổi sau bản xem trước; chưa bấm Đăng.')
                binding = self._bindings(context).get(self._key(account_id, page_key, post_id))
                if binding and getattr(binding['tab'], '_teodub_page_actor', None):
                    from teodub.facebook_page_identity import verify_page_actor
                    verify_page_actor(context, target, cancel=cancel, finished=finished)
                tab, _ = self._existing(context, account_id, page_key, post_id, post, target)
                tab.bring_to_front()
                if post['browser_review']['composer'].get('layout') == 'reel_settings':
                    dialog, _ = self._observe_reel(tab, target, REEL_SETTINGS_NAMES, post['browser_review']['composer']['video_preview'])
                elif post['browser_review']['composer'].get('layout') == 'post_settings':
                    dialog, _ = self._observe_post_settings(tab, target, post['browser_review'])
                else:
                    dialog = self._dialog(tab)
                button = _one_visible(dialog.get_by_role('button', name=PUBLISH_NAMES), 'nút Đăng cuối cùng')
                if not button.is_enabled():
                    raise ValueError('Nút Đăng không còn sẵn sàng; giữ Chrome để kiểm tra.')
                baseline = (tab.evaluate(RESULT_SCRIPT) if self._result_checks_enabled()
                            else dict(statuses=[], articles=[]))
                self._cancel(cancel, finished)
                self.store.transition(account_id, page_key, post_id, 'submitting',
                    'Đang bấm Đăng đúng bản xem trước đã duyệt. Không tự bấm lại dù chưa rõ kết quả.', expected={'review'})
                if progress:
                    progress('Đang gửi bài đã duyệt. Không bấm/gửi lại khi chưa rõ kết quả…')
                submitted = True
                submitted_at = wall_time()
                tracking = dict(version=1, account_id=account_id, page_key=page_key, post_id=post_id,
                    submitted_at=submitted_at, deadline_at=submitted_at+(PUBLISH_RESULT_SECONDS if post['kind']=='video' else 90),
                    review=deepcopy(post['browser_review']), baseline=baseline, tab_url=tab.url, references=[], notifications={})
                self.store.save_browser_tracking(account_id, page_key, post_id, tracking)
                try:
                    button.click(timeout=5000)
                except Exception as exc:
                    if not _navigation_interrupted(exc):
                        raise
                    # Dispatch may already have succeeded before the document
                    # was replaced. Observe the result; NEVER repeat this click.
                self.store.transition(account_id, page_key, post_id, 'processing',
                    'Đã bấm Đăng một lần; chưa có bằng chứng bài đã đăng.', expected={'submitting'})
                # Facebook holds the post behind this prompt until it is dismissed; this never re-clicks Publish.
                # With result checks on, the observer below keeps looking for it inside its own deadline;
                # only the paused path has no observer, so it alone waits here.
                if not self._result_checks_enabled():
                    self._dismiss_call_prompt(tab, cancel, finished, progress, seconds=CALL_PROMPT_WAIT_SECONDS)
                    return self._pause_result_verification(account_id, page_key, post_id)
                if progress:
                    progress('Đã bấm Đăng; Tèo đang chờ Facebook xử lý và tự xác nhận kết quả…')
                return (yield from self._observe_publication_steps(context, tab, account_id, page_key, post_id,
                    post, target, tracking, cancel=cancel, finished=finished, progress=progress))
            except BaseException as exc:
                current = self.store.post(account_id, page_key, post_id)
                expected = {'submitting', 'processing'} if submitted else {'review'}
                if current['state'] in expected:
                    self.store.transition(account_id, page_key, post_id, 'unknown', (str(exc) or 'Đã dừng theo dõi kết quả.')+' Kiểm tra Chrome/Page; không tự đăng lại.', expected=expected)
                raise

    def verify(self, context, account_id, page_key, post_id, **kwargs):
        return self._run_steps(self.verify_steps(context, account_id, page_key, post_id, **kwargs))

    def verify_steps(self, context, account_id, page_key, post_id, *, cancel=None, finished=None, progress=None):
        """Reattach read-only observation; this path cannot upload or click Publish."""
        if not self._result_checks_enabled():
            raise ValueError('Kiểm tra trạng thái đang tạm tắt; chưa đọc Facebook hoặc video trên máy.')
        with self._lock(account_id, page_key, post_id):
            post, target = self._post_target(account_id, page_key, post_id)
            if post['state'] == 'published' and post.get('browser_receipt'):
                return post
            if post['state'] not in {'processing', 'unknown'} or not post.get('browser_review'):
                raise ValueError('Chỉ theo dõi lượt đã gửi; không tạo hoặc đăng lại bản nháp.')
            self._cancel(cancel, finished)
            if not self._result_checks_enabled():
                return self._pause_result_verification(account_id, page_key, post_id)
            if self._files(post) != post['browser_review'].get('media'):
                raise ValueError('Tệp đã thay đổi so với lượt đã gửi; chưa tự xác nhận kết quả.')
            if not self._result_checks_enabled():
                return self._pause_result_verification(account_id, page_key, post_id)
            tracking = deepcopy(post.get('browser_tracking') or {})
            if not tracking:
                events = self.store.events(account_id, page_key, post_id)
                submitted = [event for event in events if event['state'] == 'processing']
                if not submitted:
                    raise ValueError('Lượt cũ chưa có bằng chứng đã bấm Đăng; chỉ kiểm tra bằng link bạn xác nhận.')
                submitted_at = datetime.fromisoformat(submitted[-1]['at']).timestamp()
                tracking = dict(version=1, account_id=account_id, page_key=page_key, post_id=post_id,
                    submitted_at=submitted_at, deadline_at=wall_time()+INSPECT_RESULT_WAIT_SECONDS+70,
                    review=deepcopy(post['browser_review']), baseline=dict(statuses=[], articles=[]),
                    tab_url=target['url'], references=[], notifications={})
            if (tracking.get('review') != post['browser_review']
                    or any(tracking.get(key) != value for key, value in
                        (('account_id', account_id), ('page_key', page_key), ('post_id', post_id)))):
                raise ValueError('Dữ liệu theo dõi không khớp đúng lượt đã gửi.')
            # An explicit read-only recheck may inspect a later Facebook result
            # without altering the original submission/deadline descriptor.
            recheck_deadline = wall_time()+90
            # Existing result tabs are observations only; never reuse a composer.
            if not self._result_checks_enabled():
                return self._pause_result_verification(account_id, page_key, post_id)
            for page in context.pages:
                if page.is_closed():
                    continue
                url = _canonical_insights_reference(page.url) or _canonical_inspection_url(page.url)
                if url and url not in tracking['references'] and len(tracking['references']) < 8:
                    tracking['references'].append(url)
            self.store.save_browser_tracking(account_id, page_key, post_id, tracking)
            if not self._result_checks_enabled():
                return self._pause_result_verification(account_id, page_key, post_id)
            tab = context.new_page()
            self._remember_tab(context, tab, account_id, page_key, post_id, post['attempt'])
            if not self._result_checks_enabled():
                tab.close()
                return self._pause_result_verification(account_id, page_key, post_id)
            tab.goto(target['url'], wait_until='domcontentloaded', timeout=15000)
            self._cancel(cancel, finished)
            if not self._result_checks_enabled():
                return self._pause_result_verification(account_id, page_key, post_id)
            if post['state'] == 'unknown':
                self.store.transition(account_id, page_key, post_id, 'processing',
                    'Đang kiểm tra lại bài đã gửi; không tải hoặc bấm Đăng lại.', expected={'unknown'})
            try:
                return (yield from self._observe_publication_steps(context, tab, account_id, page_key, post_id,
                    post, target, tracking, cancel=cancel, finished=finished, progress=progress,
                    observation_deadline=recheck_deadline, trusted_origin=False))
            except BaseException as exc:
                current = self.store.post(account_id, page_key, post_id)
                if current['state'] == 'processing':
                    self.store.transition(account_id, page_key, post_id, 'unknown',
                        (str(exc) or 'Đã dừng theo dõi.')+' Chưa xác minh kết quả; không gửi lại.', expected={'processing'})
                raise

    def _observe_publication_steps(self, context, tab, account_id, page_key, post_id, post, target, tracking,
                                   *, cancel=None, finished=None, progress=None, observation_deadline=None,
                                   trusted_origin=True):
        if not self._result_checks_enabled():
            return self._pause_result_verification(account_id, page_key, post_id)
        submitted_at = tracking['submitted_at']
        baseline = tracking['baseline']
        deadline = time.monotonic()+max(0, (observation_deadline or tracking['deadline_at'])-wall_time())
        result_tabs = {}
        def result_tab_for(url):
            # Receipt discovery is read-only and bounded even when the
            # notification panel contains many unrelated new videos.
            if not self._result_checks_enabled():
                return None
            if url in result_tabs:
                return result_tabs[url]
            if len(result_tabs) >= 8 or time.monotonic() >= deadline:
                return None
            self._cancel(cancel, finished)
            existing = [page for page in context.pages if not page.is_closed() and page.url == url]
            if not self._result_checks_enabled():
                return None
            result_tab = existing[0] if len(existing) == 1 else context.new_page()
            if len(existing) != 1:
                self._remember_tab(context, result_tab, account_id, page_key, post_id, post['attempt'])
            result_tabs[url] = result_tab
            if url not in tracking['references']:
                tracking['references'].append(url)
                self.store.save_browser_tracking(account_id, page_key, post_id, tracking)
            if result_tab.url == url:
                return result_tab
            if not self._result_checks_enabled():
                if not existing:
                    result_tab.close()
                return None
            remaining = max(1, int((deadline-time.monotonic())*1000))
            result_tab.goto(url, wait_until='domcontentloaded', timeout=min(5000, remaining))
            if not self._result_checks_enabled():
                if not existing:
                    result_tab.close()
                return None
            return result_tab
        dismissed_call_prompt = False
        last_progress_at = 0.0
        notification_probe_at = 0.0
        diagnostic_at = {}
        yield dict(phase='verifying')
        while time.monotonic() < deadline:
            try:
                if not self._result_checks_enabled():
                    return self._pause_result_verification(account_id, page_key, post_id)
                self._cancel(cancel, finished)
                parsed = urlsplit(tab.url)
                if parsed.hostname not in _HOSTS or parsed.path.startswith(('/login', '/checkpoint', '/recover')):
                    raise ValueError('Tab chuyển khỏi Facebook hoặc tới đăng nhập sau khi gửi; kết quả chưa rõ.')
                auth = tab.evaluate(AUTH_SCRIPT)
                if auth.get('auth') or auth.get('blocked'):
                    raise ValueError('Facebook yêu cầu đăng nhập/xác minh sau khi bấm Đăng; kết quả chưa rõ.')
                if not dismissed_call_prompt:
                    dismissed_call_prompt = self._dismiss_call_prompt(tab, cancel, finished, progress, seconds=0)
                observed = tab.evaluate(RESULT_SCRIPT)
                evidence = (self._publication_evidence(observed, baseline, target, post['browser_review'])
                            if trusted_origin else None)
                # Video success toasts are candidates, not media identity.
                # Read the actual player before attributing concurrent results.
                direct_candidate = evidence if evidence and post['kind'] == 'video' else None
                if direct_candidate:
                    evidence = None
                if not evidence and post['kind'] == 'video':
                    candidates = self._new_reel_links(observed, baseline) if trusted_origin else []
                    for candidate in candidates:
                        candidate['provenance'] = 'direct_status'
                    if direct_candidate and not any(item['url'] == direct_candidate['url'] for item in candidates):
                        candidates.append(dict(direct_candidate, provenance='direct_status'))
                    for reference in tracking.get('references', []):
                        if not any(item['url'] == reference for item in candidates):
                            candidate = dict(url=reference)
                            candidate.update(tracking.get('reference_sources', {}).get(reference, {}))
                            if _canonical_insights_reference(reference):
                                candidate['kind'] = 'insights_reference'
                                candidate['notified_at'] = tracking.get('notifications', {}).get(reference)
                            candidates.append(candidate)
                    # A success toast can vanish during Facebook's automatic
                    # navigation. Processed-Reel notifications already carry an
                    # independent submission-time bound; do not gate on a toast.
                    def merge_notices():
                        for notice in self._notification_reels(tab, submitted_at):
                            current = next((item for item in candidates if item['url'] == notice['url']), None)
                            if current is None:
                                candidates.append(notice)
                            else:
                                current.update(notice)
                    merge_notices()
                    if wall_time()-submitted_at >= 3 and time.monotonic() >= notification_probe_at:
                        panels = tab.get_by_role('dialog', name=re.compile(r'^(Thông báo|Notifications)$', re.I))
                        panel_open = any(panels.nth(i).is_visible() for i in range(panels.count()))
                        forms = tab.get_by_role('dialog', name=CREATE_NAMES)
                        form_open = any(forms.nth(i).is_visible() for i in range(forms.count()))
                        if not panel_open and not form_open:
                            bells = tab.get_by_role('button', name=NOTIFICATION_NAMES)
                            bells = [bells.nth(i) for i in range(bells.count()) if bells.nth(i).is_visible()]
                            if len(bells) == 1:
                                self._cancel(cancel, finished)
                                bells[0].click(timeout=5000)
                        notification_probe_at = time.monotonic()+15
                        merge_notices()
                    for candidate in candidates:
                        if time.monotonic() >= deadline:
                            break
                        yield dict(phase='verifying')
                        if not self._result_checks_enabled():
                            return self._pause_result_verification(account_id, page_key, post_id)
                        self._cancel(cancel, finished)
                        url = candidate['url']
                        if candidate.get('notified_at'):
                            tracking.setdefault('notifications', {})[url] = candidate['notified_at']
                            candidate['provenance'] = 'processed_notice'
                        if candidate.get('provenance'):
                            source = {key: candidate[key] for key in ('provenance', 'notified_at') if key in candidate}
                            if tracking.setdefault('reference_sources', {}).get(url) != source:
                                tracking['reference_sources'][url] = source
                                self.store.save_browser_tracking(account_id, page_key, post_id, tracking)
                        # A pre-existing manually opened video is not a result
                        # of this submission merely because its content matches.
                        if (candidate.get('kind') != 'insights_reference'
                                and candidate.get('provenance') not in {'direct_status', 'processed_notice'}):
                            continue
                        new_result_tab = url not in result_tabs
                        result_tab = result_tab_for(url)
                        if result_tab is None:
                            continue
                        if progress and new_result_tab:
                            progress('Facebook báo đã đăng thước phim; Tèo đang lấy link thật và đối chiếu Page/nội dung…')
                        bound = candidate.get('provenance') in {'direct_status', 'processed_notice'}
                        # Following an old Insights tab to its old public Reel
                        # does not make it a fresh result of this submission.
                        resolved = (self._insights_reel_links(result_tab, url, target, post['browser_review'])
                                    if candidate.get('kind') == 'insights_reference' and bound else
                                    [] if candidate.get('kind') == 'insights_reference' else [url])
                        for reel_url in resolved:
                            if time.monotonic() >= deadline:
                                break
                            yield dict(phase='verifying')
                            if not self._result_checks_enabled():
                                return self._pause_result_verification(account_id, page_key, post_id)
                            self._cancel(cancel, finished)
                            reel_tab = (result_tab if _canonical_inspection_url(result_tab.url) == reel_url
                                        else result_tab_for(reel_url))
                            if reel_tab is None:
                                continue
                            evidence = self._reel_evidence(reel_tab, reel_url, target, post['browser_review'])
                            if evidence:
                                if candidate.get('notified_at'):
                                    evidence['notification_at'] = candidate['notified_at']
                                if candidate.get('kind') == 'insights_reference':
                                    evidence['notification_reference'] = url
                                break
                        if not evidence and candidate.get('kind') == 'insights_reference':
                            insights_decision = {}
                            evidence = self._insights_evidence(result_tab, url, target, post['browser_review'],
                                submitted_at=submitted_at, notified_at=candidate.get('notified_at'),
                                diagnostics=insights_decision)
                            if not evidence and time.monotonic()-diagnostic_at.get(url, 0) >= 15:
                                from teodub.facebook_result_diagnostics import SCRIPT
                                try:
                                    summary = result_tab.evaluate(SCRIPT, dict(body=post['body'], owner=target['name']))
                                    if isinstance(summary, dict):
                                        summary['decision'] = insights_decision
                                        panels = result_tab.evaluate(INSIGHTS_RESULT_SCRIPT, dict(body=post['body']))
                                        summary['panels'] = [{key:panel.get(key) for key in
                                            ('video_count','primary_video_count','controls','unfinished','composer_open','times')}
                                            for panel in panels[:4]]
                                        tracking['last_insights_check'] = dict(url=url, checked_at=now(), **summary)
                                        checks = tracking.setdefault('insights_checks', {})
                                        checks[url] = tracking['last_insights_check']
                                        while len(checks) > 4:
                                            del checks[next(iter(checks))]
                                        self.store.save_browser_tracking(account_id, page_key, post_id, tracking)
                                except Exception:
                                    pass  # Diagnostics cannot change the observation's outcome.
                                diagnostic_at[url] = time.monotonic()
                        if evidence:
                            break
                if not self._result_checks_enabled():
                    return self._pause_result_verification(account_id, page_key, post_id)
                if evidence and not self._unambiguous_attempt(account_id, page_key, post_id, post):
                    evidence = None
                if evidence:
                    with self._result_checks_guard() as checks_enabled:
                        if not checks_enabled:
                            return self._pause_result_verification(account_id, page_key, post_id)
                        self._cancel(cancel, finished)
                        return self.store.confirm_browser_publication(account_id, page_key, post_id,
                            dict(source='visible_dom', checked_at=now(), **evidence), expected_review=post['browser_review'])
                if progress and time.monotonic()-last_progress_at >= 15:
                    elapsed = max(0, int(wall_time()-submitted_at))
                    progress(f'Đã gửi — đang xác minh ({elapsed//60} phút {elapsed%60} giây). '
                        'Chưa đủ bằng chứng Page/caption/video và link kết quả; bài tiếp theo vẫn được xử lý.')
                    last_progress_at = time.monotonic()
                yield dict(phase='verifying')
                tab.wait_for_timeout(250)
            except Exception as exc:
                if not _navigation_interrupted(exc) or tab.is_closed():
                    raise
                if progress:
                    progress('Facebook đang chuyển trang sau khi gửi; Tèo tiếp tục xác nhận, không bấm Đăng lại…')
                # Bounded by the original result deadline and cancellation.
                yield dict(phase='verifying')
                time.sleep(0.25)
        if not self._result_checks_enabled():
            return self._pause_result_verification(account_id, page_key, post_id)
        return self.store.transition(account_id, page_key, post_id, 'unknown',
            'Đã gửi; hết thời gian theo dõi nhưng chưa đủ bằng chứng xác nhận đã đăng. '
            'Kiểm tra Page trong Chrome rồi ghi link bạn thấy; Tèo không tự gửi lại.', expected={'processing'})

    @staticmethod
    def _notification_reels(tab, submitted_at):
        """Read processed-Reel notices, then require full owner/caption evidence.

        The observed Facebook notification href carries a microsecond creation
        timestamp. Missing/old timestamps are deliberately not eligible: an old
        identical caption must not turn an unconfirmed submission into success.
        """
        panels = tab.get_by_role('dialog', name=re.compile(r'^(Thông báo|Notifications)$', re.I))
        links = panels.evaluate_all(NOTIFICATION_LINKS_SCRIPT)
        result = []
        seen = set()
        for link in links:
            raw = link['url']
            parsed = _receipt_url_parts(raw)
            if not parsed:
                continue
            query = parse_qs(parsed.query)
            stamps = query.get('notif_id', [])
            text = link['text']
            if (query.get('notif_t') != ['fb_shorts_video_processed']
                    or not re.search(r'Đã đăng thước phim|\breel (?:was |is |has been )?published\b', text, re.I)
                    or len(stamps) != 1 or not stamps[0].isdigit() or len(stamps[0]) > 24
                    or not submitted_at-2 <= int(stamps[0])/1_000_000 <= wall_time()+5):
                continue
            url = _canonical_reel_url(raw)
            reference = _canonical_insights_reference(raw) if not url else ''
            url = url or reference
            if not url or url in seen:
                continue
            seen.add(url)
            candidate = dict(url=url, text=text[:2000], notified_at=int(stamps[0])/1_000_000)
            if reference:
                candidate['kind'] = 'insights_reference'
            result.append(candidate)
        return result

    def _unambiguous_attempt(self, account_id, page_key, post_id, post):
        """A global notification must never confirm two indistinguishable uploads."""
        review = post.get('browser_review') or {}
        expected = review.get('composer', {}).get('video_preview', [])
        tracking = self.store.post(account_id, page_key, post_id).get('browser_tracking') or {}
        submitted_at = tracking.get('submitted_at', 0)
        for other in self.store.posts(account_id, page_key, include_archived=True):
            if (other['id'] == post_id or not other.get('attempt') or other['kind'] != post['kind']
                    or not _same_body(other['body'], post['body'])):
                continue
            events = self.store.events(account_id, page_key, other['id'])
            if not any(event['state'] in {'submitting', 'processing', 'published'} for event in events):
                continue  # A prepared composer has never claimed a publication.
            if other['state'] == 'published' and submitted_at:
                confirmed = [event for event in events if event['state'] == 'published']
                if confirmed:
                    try:
                        if datetime.fromisoformat(confirmed[-1]['at']).timestamp() < submitted_at-2:
                            continue
                    except (ValueError, TypeError):
                        pass
            receipt = other.get('browser_receipt') or {}
            stamp = receipt.get('published_at') or receipt.get('notification_at')
            if stamp and submitted_at:
                try:
                    observed_at = (float(stamp) if isinstance(stamp, (int, float))
                                   else datetime.fromisoformat(stamp).timestamp())
                    if observed_at < submitted_at-2:
                        continue
                except (ValueError, TypeError):
                    pass
            actual = (other.get('browser_review') or {}).get('composer', {}).get('video_preview', [])
            if len(expected) == len(actual) == 1:
                a, b = actual[0], expected[0]
                if (a.get('duration', 0) > 0 and b.get('duration', 0) > 0
                        and abs(a['duration']-b['duration']) > 2):
                    continue
                if all(video.get('width', 0) > 0 and video.get('height', 0) > 0 for video in (a, b)):
                    if abs((a['width']/a['height'])/(b['width']/b['height'])-1) > .03:
                        continue
            return False
        return True

    def _expand_insights_caption(self, tab, target, snapshot):
        """Expand one truncated post caption, never a comment or another card."""
        more = tab.get_by_role('button', name=EXPAND_NAMES)
        candidates = [more.nth(index) for index in range(more.count()) if more.nth(index).is_visible()
            and more.nth(index).evaluate(r'''(button, expected) => {
              const visible=e=>!!e.getClientRects().length && getComputedStyle(e).visibility!=='hidden'
                && !e.closest('[aria-hidden="true"],[inert]');
              const norm=s=>(s||'').normalize('NFC').replace(/\s+/g,' ').trim();
              const structuralExcluded='[contenteditable="true"],[role="textbox"],textarea,[data-testid*="comment"],'
                + '[aria-label*="Comment"],[aria-label*="comment"],[aria-label*="Bình luận"],[aria-label*="bình luận"]';
              const headings=[...document.querySelectorAll('h1,h2,[role="heading"]')].filter(e=>visible(e)
                && !e.closest(structuralExcluded) && ['post insights','thông tin chi tiết bài viết'].includes(norm(e.innerText).toLowerCase()));
              const headingLeaves=headings.filter(e=>!headings.some(other=>other!==e && e.contains(other)));
              const recognized=location.pathname.replace(/\/$/,'')==='/content/insights'
                && new URL(location.href).searchParams.getAll('content_id').length===1 && headingLeaves.length===1;
              const excluded=structuralExcluded+(recognized ? '' : ',nav,[role="navigation"],aside,[role="complementary"]');
              if(button.closest(excluded)) return false;
              const primary=button.closest('main,[role="main"],[role="dialog"]'
                +(recognized ? ',nav,[role="navigation"],aside,[role="complementary"]' : ''))
                || (recognized ? document.body : null);
              if(!primary) return false;
              const body=norm(expected.body);
              let prefix=null;
              for(let node=button.parentElement;node && node!==primary;node=node.parentElement) {
                const text=norm(node.innerText).replace(/\s*(See more|Xem thêm)\s*$/i,'')
                  .replace(/(?:…|\.{3})\s*$/,'').trim();
                if(text.length>=Math.min(12,body.length) && text.length<body.length && body.startsWith(text)) {
                  prefix=node;break;
                }
              }
              if(!prefix) return false;
              const semantic=button.closest('article,[role="article"]');
              for(let scope=semantic || prefix;scope && scope!==primary;scope=semantic ? null : scope.parentElement) {
                if(scope.closest(excluded)) return false;
                const inside=e=>visible(e) && !e.closest(excluded)
                  && (primary===document.body ? !e.closest('[role="dialog"],nav,[role="navigation"],aside,[role="complementary"]')
                    : e.closest('main,[role="main"],[role="dialog"]'
                      +(recognized ? ',nav,[role="navigation"],aside,[role="complementary"]' : ''))===primary);
                const videos=[...scope.querySelectorAll('video')].filter(e=>{const r=e.getBoundingClientRect();
                  return inside(e) && r.bottom>0 && r.top<innerHeight && r.right>0 && r.left<innerWidth;});
                if(!videos.length) continue;
                if(videos.length!==1) return false;
                if(recognized && !semantic) {
                  const action=s=>norm(s).normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/[đĐ]/g,'d').toLowerCase();
                  const names=[...scope.querySelectorAll('button,[role="button"]')].filter(e=>visible(e)
                    && !e.parentElement?.closest(excluded) && !prefix.contains(e))
                    .flatMap(e=>[action(e.innerText),action(e.getAttribute('aria-label'))]);
                  if(!names.some(s=>['like','thich'].includes(s))
                    || !names.some(s=>['comment','leave a comment','binh luan'].includes(s))
                    || !names.some(s=>['share','chia se'].includes(s))) continue;
                }
                const owners=[...scope.querySelectorAll('a[href]')].filter(e=>inside(e) && !prefix.contains(e))
                  .map(e=>{try {const u=new URL(e.href);if(u.protocol!=='https:' || u.username || u.password
                      || u.port && u.port!=='443' || !['facebook.com','www.facebook.com','m.facebook.com'].includes(u.hostname)) return null;
                    const ids=u.searchParams.getAll('id');
                    const id=u.pathname==='/profile.php' && ids.length===1 ? ids[0]
                      : /^\/[0-9]+\/?$/.test(u.pathname) ? u.pathname.split('/')[1]
                      : /^\/pages\/[^/]+\/[0-9]+\/?$/.test(u.pathname) ? u.pathname.replace(/\/$/,'').split('/').pop() : '';
                    return /^[0-9]+$/.test(id) ? {id,name:norm(e.innerText || e.getAttribute('aria-label'))
                      .replace(/,\s*see owner profile$/i,'')} : null;
                    } catch {return null;}}).filter(Boolean);
                if(owners.length && owners.every(owner=>owner.id===expected.page_id)
                  && owners.some(owner=>owner.name.toLowerCase()===norm(expected.name).toLowerCase())) return true;
                return false;
              }
              return false;
            }''', dict(body=snapshot['body'], page_id=target['page_id'], name=target['name']))]
        if len(candidates) == 1:
            candidates[0].click(timeout=3000)

    def _insights_reel_links(self, tab, reference, target, snapshot):
        """Resolve only a real redirect or a visible, target-bound permalink.

        Both semantic cards and Insights' generic left content card must show
        one primary video beside the exact caption and numeric Page owner.
        A notification/content_id never stands in for the actual public link.
        """
        if snapshot['kind'] != 'video' or not snapshot['body']:
            return []
        redirected = _canonical_inspection_url(tab.url)
        if redirected:
            return [redirected]
        source = _canonical_insights_reference(reference)
        if not source or _canonical_insights_reference(tab.url) != source:
            return []
        auth = tab.evaluate(AUTH_SCRIPT)
        if auth.get('auth') or auth.get('blocked'):
            return []
        self._expand_insights_caption(tab, target, snapshot)
        panels = tab.evaluate(INSIGHTS_RESULT_SCRIPT, dict(body=snapshot['body']))
        urls = set()
        for panel in panels:
            if panel['video_count'] != 1 or panel['primary_video_count'] != 1:
                continue
            authors = [link for link in panel['links']
                       if _name(re.sub(r',\s*see owner profile$', '', link['name'], flags=re.I)) == _name(target['name'])
                       and _receipt_url_parts(link['url'])
                       and _page_id(link['url']) == target['page_id']]
            if not authors:
                continue
            owners = []
            for link in panel.get('primary_links', panel['links']):
                try:
                    if _receipt_url_parts(link['url']):
                        owners.append(_target_url(link['url']))
                except ValueError:
                    continue
            if any(_page_id(owner) != target['page_id'] for owner in owners):
                continue
            urls.update(url for link in panel['links'] if (url := _canonical_inspection_url(link['url'])))
        return sorted(urls) if len(urls) == 1 else []

    def _insights_evidence(self, tab, reference, target, snapshot, *, submitted_at, notified_at=None,
                           diagnostics=None):
        """Verify the published Insights card without inventing a public URL.

        This requires actual loaded media identity and publication controls in
        the same content card. The caller additionally excludes another saved
        attempt with the same Page/body/media and claims this reference once.
        A fresh processed notification can bind the observation to this send;
        its timestamp is never used as the actual publication timestamp.
        """
        diagnostic = diagnostics if isinstance(diagnostics, dict) else None
        if diagnostic is not None:
            diagnostic.clear()
            diagnostic.update(verified=False, reason='not_checked', panels=[])
        def reject(reason):
            if diagnostic is not None:
                diagnostic['reason'] = reason
            return None
        source = _canonical_insights_reference(reference)
        if (not source or _canonical_insights_reference(tab.url) != source
                or snapshot.get('kind') != 'video' or not snapshot.get('body')
                or not isinstance(submitted_at, (int, float)) or isinstance(submitted_at, bool)
                or not math.isfinite(submitted_at) or submitted_at <= 0):
            return reject('reference_or_snapshot_or_submission_invalid')
        positive = lambda value: isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0
        fresh_notice = (positive(notified_at) and submitted_at <= notified_at <= wall_time()+5)
        if diagnostic is not None:
            diagnostic.update(reference_matches=True, fresh_notice=bool(fresh_notice),
                notice_supplied=notified_at is not None)
        if notified_at is not None and not fresh_notice:
            return reject('notification_not_fresh')
        expected = snapshot.get('composer', {}).get('video_preview', [])
        if (not isinstance(expected, list) or len(expected) != 1 or not isinstance(expected[0], dict)
                or not all(positive(expected[0].get(key)) for key in ('duration', 'width', 'height'))):
            return reject('frozen_video_metadata_missing')
        expected = expected[0]
        if diagnostic is not None:
            diagnostic['expected_video'] = {key:expected[key] for key in ('duration', 'width', 'height')}
        auth = tab.evaluate(AUTH_SCRIPT)
        if auth.get('auth') or auth.get('blocked'):
            return reject('authentication_or_checkpoint')
        self._expand_insights_caption(tab, target, snapshot)
        verified = []
        for panel in tab.evaluate(INSIGHTS_RESULT_SCRIPT, dict(body=snapshot['body'])):
            detail = dict(video_count=panel.get('video_count'),primary_video_count=panel.get('primary_video_count'),
                unfinished=bool(panel.get('unfinished')),composer_open=bool(panel.get('composer_open')),
                body_matches=_same_body(panel.get('body', ''), snapshot['body']), reason='not_checked')
            if diagnostic is not None and len(diagnostic['panels']) < 4:
                diagnostic['panels'].append(detail)
            if (panel.get('video_count') != 1 or panel.get('primary_video_count') != 1
                    or panel.get('unfinished') or panel.get('composer_open')
                    or not _same_body(panel.get('body', ''), snapshot['body'])):
                detail['reason'] = 'panel_structure_or_body'
                continue
            controls = set(panel.get('controls', []))
            detail['publication_actions'] = sorted(controls & {
                'like', 'thich', 'comment', 'leave a comment', 'binh luan', 'share', 'chia se'})
            if not (controls & {'like', 'thich'} and controls & {'comment', 'leave a comment', 'binh luan'}
                    and controls & {'share', 'chia se'}):
                detail['reason'] = 'publication_actions_missing'
                continue
            videos = panel.get('videos', [])
            if len(videos) != 1:
                detail['reason'] = 'video_observations_not_unique'
                continue
            video = videos[0]
            detail['video'] = dict(has_src=bool(video.get('src')), **{
                key:video.get(key) if positive(video.get(key)) else 0 for key in ('duration', 'width', 'height')})
            if (not video.get('src') or not all(positive(video.get(key)) for key in ('duration', 'width', 'height'))
                    or abs(video['duration']-expected['duration']) > 2
                    or abs((video['width']/video['height'])/(expected['width']/expected['height'])-1) > .03):
                detail['reason'] = 'video_metadata_or_identity_mismatch'
                continue
            owners, authors = [], []
            detail['owners'] = []
            for link in panel.get('primary_links', panel['links']):
                try:
                    if not _receipt_url_parts(link['url']):
                        continue
                    owner = _target_url(link['url'])
                except ValueError:
                    continue
                owners.append(owner)
                if len(detail['owners']) < 12:
                    detail['owners'].append(dict(path=urlsplit(owner).path, page_id=_page_id(owner),
                        name=str(link.get('name', ''))[:100], local=link in panel['links']))
                if (link in panel['links'] and _page_id(owner) == target['page_id'] and _name(re.sub(
                        r',\s*see owner profile$', '', link['name'], flags=re.I)) == _name(target['name'])):
                    authors.append(link)
            if not authors or any(_page_id(owner) != target['page_id'] for owner in owners):
                detail.update(reason='author_missing_or_conflicting_owner', author_count=len(authors),
                    owner_count=len(owners),conflicting_owner=any(_page_id(owner) != target['page_id'] for owner in owners))
                continue
            try:
                published_at = _inspection_publication_time(panel.get('times', []))
            except ValueError:
                detail['reason'] = 'publication_time_invalid_or_ambiguous'
                continue
            if published_at:
                published_stamp = datetime.fromisoformat(published_at).timestamp()
                if not submitted_at-2 <= published_stamp <= wall_time()+5:
                    detail['reason'] = 'publication_time_outside_submission'
                    continue
            elif not fresh_notice:
                detail['reason'] = 'publication_time_and_fresh_notice_missing'
                continue
            detail['reason'] = 'verified'
            verified.append((video, authors[0], published_at))
        if (not verified or len({(video['src'], video['duration'], video['width'], video['height'], stamp)
                for video, _, stamp in verified}) != 1):
            return reject('no_verified_panel' if not verified else 'multiple_verified_media')
        video, author, published_at = verified[0]
        evidence = dict(url=source, verification_url=source, public_url='', kind='published_insights',
            owner_url=author['url'], target_id=target['page_id'], body=snapshot['body'],
            visible_video=True, video_verified=True, observed_duration=video['duration'],
            observed_width=video['width'], observed_height=video['height'], published_at=published_at,
            association_mode='visible_time' if published_at else 'fresh_notification')
        if fresh_notice:
            evidence['notification_at'] = datetime.fromtimestamp(notified_at, timezone.utc).isoformat()
        if diagnostic is not None:
            diagnostic.update(verified=True,reason='verified')
        return evidence

    @staticmethod
    def _new_reel_links(observed, baseline):
        def canonical(value):
            parsed = urlsplit(value)
            if (parsed.hostname in _HOSTS and parsed.scheme == 'https'
                    and re.fullmatch(r'/reel/[0-9]+/?', parsed.path)):
                return 'https://www.facebook.com'+parsed.path.rstrip('/')
            return ''
        old_urls = {canonical(a['url']) for group in baseline.values() for item in group for a in item.get('links', [])}
        result = []
        seen = set()
        for status in observed.get('statuses', []):
            if status in baseline.get('statuses', []):
                continue
            for link in status.get('links', []):
                url = canonical(link['url'])
                if url and url not in old_urls:
                    if url not in seen:
                        seen.add(url)
                        result.append(dict(url=url, text=status['text'][:2000]))
        return result

    def _reel_evidence(self, tab, url, target, snapshot):
        expected = _canonical_inspection_url(url)
        if not expected or _canonical_inspection_url(tab.url) != expected:
            return None
        parsed = urlsplit(expected)
        bits = parsed.path.strip('/').split('/')
        owner_id = (bits[0] if len(bits) == 3 and bits[1] in {'posts', 'videos'} and bits[0].isdigit()
                    else parse_qs(parsed.query).get('id', [''])[0] if parsed.path in {'/permalink.php', '/story.php'} else '')
        if owner_id and owner_id != target['page_id']:
            return None
        auth = tab.evaluate(AUTH_SCRIPT)
        if auth.get('auth') or auth.get('blocked') or snapshot['kind'] != 'video' or not snapshot['body']:
            return None
        # Expand the one caption belonging to this visible Reel, if needed.
        more = tab.get_by_role('button', name=EXPAND_NAMES)
        candidates = [more.nth(i) for i in range(more.count()) if more.nth(i).is_visible()
            and more.nth(i).evaluate(r'''(e, body) => {
                const norm=s=>(s||'').normalize('NFC').replace(/\s+/g,' ').trim();
                if(e.closest('nav,[role=navigation],[data-testid*="comment"],[aria-label*="Comment"],[aria-label*="Bình luận"]')) return false;
                for(let node=e.parentElement;node && !node.matches('main,[role=main],[role=dialog],body');node=node.parentElement)
                    if(norm(node.innerText).includes(norm(body).slice(0,12))) return true;
                return false;
            }''', snapshot['body'])]
        if len(candidates) > 1:
            # The observed Reel renders the same caption in the player and its
            # side panel. Prefer the player caption; never expand comments.
            candidates = [item for item in candidates if item.evaluate('e => !!e.closest("main,[role=main]")')][:1]
        if len(candidates) == 1:
            candidates[0].click(timeout=3000)
        panels = tab.evaluate(REEL_RESULT_SCRIPT, dict(body=snapshot['body']))
        panels = [panel for panel in panels if panel['video_count'] == 1 and panel['video_sources'][0]]
        if not panels:
            return None
        verified = []
        preview = snapshot.get('composer', {}).get('video_preview', [])
        metadata_keys = ('duration', 'width', 'height')
        usable = lambda video: isinstance(video, dict) and all(isinstance(video.get(key), (int, float))
            and not isinstance(video.get(key), bool) and math.isfinite(video[key]) and video[key] > 0
            for key in metadata_keys)
        expected_video = preview[0] if isinstance(preview, list) and len(preview) == 1 and usable(preview[0]) else None
        if not expected_video:
            return None
        for panel in panels:
            if expected_video:
                video = panel['videos'][0]
                if (not usable(video) or abs(video['duration']-expected_video['duration']) > 2
                        or abs((video['width']/video['height'])/(expected_video['width']/expected_video['height'])-1) > .03):
                    continue
            authors = [link for link in panel['links']
                       if _name(re.sub(r',\s*see owner profile$', '', link['name'], flags=re.I)) == _name(target['name'])
                       and _receipt_url_parts(link['url'])
                       and _page_id(link['url']) == target['page_id']]
            if not authors:
                continue
            profile_links = []
            for link in panel.get('primary_links', panel['links']):
                try:
                    if _receipt_url_parts(link['url']):
                        profile_links.append(_target_url(link['url']))
                except ValueError:
                    continue
            # A Page link elsewhere in main must not label a foreign primary
            # player as ours. Ambiguous profile owners fail closed, including
            # otherwise matching captions in recommendations.
            if any(_page_id(url) != target['page_id'] for url in profile_links):
                continue
            verified.append((panel, authors[0]))
        if not verified or len({panel['video_sources'][0] for panel, _ in verified}) != 1:
            return None
        _, author = verified[0]
        evidence = dict(url=expected, kind='verified_published_reel', owner_url=author['url'],
            target_id=target['page_id'], body=snapshot['body'], visible_video=True)
        if expected_video:
            video = verified[0][0]['videos'][0]
            evidence.update(video_verified=True, observed_duration=video['duration'],
                observed_width=video['width'], observed_height=video['height'])
        return evidence

    @staticmethod
    def _publication_evidence(observed, baseline, target, snapshot):
        def permalink(link):
            try:
                url = facebook_post_url(link['url'])
            except ValueError:
                return ''
            parsed = urlsplit(url)
            bits = parsed.path.strip('/').split('/')
            # The common URL validator also admits Page paths; impose actual post
            # syntax here, then require the target numeric Page ID/vanity owner.
            query = parse_qs(parsed.query)
            if parsed.path in {'/permalink.php', '/story.php'}:
                return url if query.get('id') == [target['page_id']] and query.get('story_fbid') else ''
            if len(bits) == 3 and bits[1] in {'posts', 'videos'} and bits[2]:
                owner = bits[0]
                saved_owner = urlsplit(target['url']).path.strip('/')
                return url if owner == target['page_id'] or '/' not in saved_owner and owner == saved_owner else ''
            return ''
        old_urls = {a['url'] for group in baseline.values() for item in group for a in item.get('links', [])}
        for item in observed.get('statuses', []):
            if item in baseline.get('statuses', []):
                continue
            for link in item.get('links', []):
                url = permalink(link)
                if url and link['url'] not in old_urls:
                    return dict(url=url, kind='success_status', text=item['text'][:2000])
        # Text-only article verification: exact visible body must be a separate
        # line/block; broad substring checks could match comments or older posts.
        if snapshot['kind'] != 'text' or not snapshot['body']:
            return None
        for article in observed.get('articles', []):
            if article in baseline.get('articles', []):
                continue
            if snapshot['body'] not in [_body(x) for x in article['text'].split('\n\n')]:
                continue
            author = any(_name(a['name']) == _name(target['name']) and _page_id(a['url']) == target['page_id']
                         for a in article.get('links', []))
            if not author:
                continue
            for link in article.get('links', []):
                url = permalink(link)
                if url and link['url'] not in old_urls:
                    return dict(url=url, kind='new_page_article', text=article['text'][:2000])
        return None
