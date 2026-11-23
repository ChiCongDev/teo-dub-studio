"""Xóa tài khoản (owner 23/11/2026): forget one Facebook account in Tèo. Nothing is changed on Facebook.
* refused while its Chrome is open (the desktop checks) or one of its posts is being prepared, sent or checked;
* its schedules never sent are cancelled ("Bỏ lịch chưa gửi": history and video kept); those already sent stay as history;
* the AI auto-planning of its Pages is switched off;
* the account and its Pages leave the list; its Chrome profile (the Facebook sign-in on this machine) is deleted;
* post history and numbers stay on disk (no longer shown)."""
import os

from teodub.facebook_accounts import FacebookAccounts
from teodub.page_ai import PageAIStore

BUSY = {'preparing', 'submitting', 'verifying'}
UNSENT = {'scheduled', 'armed', 'paused', 'needs_attention'}  # Only those never sent (no attempt) are cancelled.


def _size_mb(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total/1e6


def removal_plan(data_dir, account_id, schedule_store):
    """Read only: what deleting the account would do."""
    registry = FacebookAccounts(data_dir)
    value = registry.load()
    account = next((row for row in value['accounts'] if row['id'] == account_id), None)
    if account is None:
        raise ValueError('Không còn tài khoản này trong danh sách. Bấm Làm mới.')
    pages = [page for page in value['pages'] if page['account_id'] == account_id]
    rows = schedule_store.list(account_id)
    keys = {page['id'] for page in pages}
    ai = sorted({row['page_key'] for row in PageAIStore(data_dir).policies()
                 if row['account_id'] == account_id and row['page_key'] in keys})
    profile = registry.directory/'browser-profiles'/account_id
    return dict(account=account, pages=pages, ai=ai,
                busy=sum(row['state'] in BUSY for row in rows),
                unsent=[row['id'] for row in rows if row['state'] in UNSENT and not row['attempted_at']],
                profile_mb=_size_mb(profile) if profile.exists() else 0.0)


def confirm_text(plan):
    lines = [f'Xóa tài khoản "{plan["account"]["name"]}" khỏi Tèo?', '',
             f'• {len(plan["pages"])} Page của tài khoản này bị bỏ khỏi danh sách.']
    if plan['unsent']:
        lines.append(f'• {len(plan["unsent"])} lịch chưa đăng sẽ bị hủy (không đăng nữa).')
    if plan['ai']:
        lines.append(f'• AI tự lên lịch của {len(plan["ai"])} Page sẽ tắt.')
    lines.append(f'• Xóa phiên đăng nhập Facebook của tài khoản này trên máy ({plan["profile_mb"]:.0f} MB).')
    lines += ['', 'Không xóa gì trên Facebook. Lịch sử bài đã đăng vẫn giữ trên máy.', 'Không hoàn tác được.']
    return '\n'.join(lines)


def remove(controller, data_dir, account_id, plan):
    """Delete the account as planned. Raises ValueError (and leaves the account) when a post of it is being sent or a schedule could
    not be cancelled. Returns dict(cancelled, ai, profile_failed)."""
    store = controller.store
    if any(row['state'] in BUSY for row in store.list(account_id)):
        raise ValueError('Tài khoản đang có bài chuẩn bị/gửi/xác minh. Đợi xong rồi xóa.')
    cancelled, failed = 0, []
    for ident in plan['unsent']:
        try:
            row = store.get(ident)
        except ValueError:
            continue  # Gone meanwhile.
        if row['state'] not in UNSENT or row['attempted_at']:
            continue  # Sent meanwhile: it stays as history.
        try:
            controller.cancel(ident)
            cancelled += 1
        except (OSError, ValueError, RuntimeError) as exc:
            failed.append(str(exc))
    if failed:
        raise ValueError(f'Chưa hủy được {len(failed)} lịch nên chưa xóa tài khoản: '+failed[0])
    ai = PageAIStore(data_dir)
    for page in plan['ai']:
        try:
            ai.pause(account_id, page)
        except (OSError, ValueError):
            pass  # Its Page is about to leave the list: the planner skips it anyway.
    registry = FacebookAccounts(data_dir)
    registry.remove_account(account_id)
    return dict(cancelled=cancelled, ai=len(plan['ai']), profile_failed=registry.remove_profile(account_id))


def done_text(plan, result):
    text = f'Đã xóa tài khoản "{plan["account"]["name"]}" khỏi Tèo ({len(plan["pages"])} Page'
    if result['cancelled']:
        text += f', hủy {result["cancelled"]} lịch chưa đăng'
    if result['ai']:
        text += f', tắt AI tự lên lịch của {result["ai"]} Page'
    text += '). Không có gì bị xóa trên Facebook.'
    if result['profile_failed']:
        text += ' Còn vài tệp đăng nhập đang bị Chrome giữ; tắt hẳn Chrome rồi xóa tay thư mục browser-profiles của tài khoản.'
    return text
