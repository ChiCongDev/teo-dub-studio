"""Read-only Page schedule status; never starts a scheduler, AI or browser."""
import time

from teodub.page_schedule import display_time


def summarize(policy, runs, schedules, *, clock=None, executor_ready=True, active_ids=None, ai_running=False,
              checks_enabled=True):
    clock=time.time() if clock is None else clock
    # Removed appointments remain durable duplicate/history evidence, but no
    # longer contribute to the Page's active schedule status.
    schedules=[r for r in schedules if r['state'] != 'removed']
    owned=[r for r in schedules if active_ids is None or r['id'] in active_ids]
    busy=[r for r in owned if r['state'] in {'preparing','submitting'}]
    verifying=[r for r in schedules if r['state']=='verifying' and checks_enabled]
    submitted=[r for r in schedules if r['state']=='submitted' or r['state']=='verifying' and not checks_enabled]
    checking=[r for r in verifying if r in owned and executor_ready]
    armed=[r for r in owned if r['state']=='armed']
    attention=[r for r in schedules if r['state']=='needs_attention']
    paused=[r for r in schedules if r['state']=='paused' or r['state']=='armed' and r not in owned]
    waiting=[r for r in schedules if r['state']=='scheduled']
    done=sum(r['state']=='published' for r in schedules)
    current=[r for r in runs if policy and r.get('revision')==policy.get('revision')]
    latest=current[0] if current else None
    next_ai=policy['next_at'] if policy and policy['enabled'] else None
    next_post=min((r['due_at'] for r in armed),default=None)
    state,label='off','Chưa bật lịch Page'
    reason='Chọn kế hoạch và bật lịch để Tèo xử lý.'
    if busy:
        submitting=any(r['state']=='submitting' for r in busy)
        state,label=('posting','Đang chạy — đang tải / gửi bài') if submitting else ('preparing','Đang chạy — chuẩn bị bài đăng')
        reason='Đang thực hiện lịch của Page này. Bài chỉ được xác nhận đã đăng khi có bằng chứng trên Facebook.'
    elif ai_running:
        state,label,reason='analyzing','Đang chạy — AI đang phân tích','AI đang lập kế hoạch; kết quả và thao tác sẽ được ghi vào lịch sử.'
    elif checking:
        state,label,reason='verifying','Đã gửi — đang xác minh','Đang đối chiếu bài trên Facebook. Các bài khác đến giờ vẫn được xử lý; không gửi lại bài đang xác minh.'
    elif next_post or next_ai:
        if executor_ready:
            state,label,reason='waiting','Đang chạy — chờ giờ xử lý','Lịch đang bật. Đến giờ Tèo sẽ xử lý; chưa cần thao tác ngay.'
            if next_post is not None and next_post<=clock:
                label,reason='Đang chạy — bài đã đến giờ, chờ lượt gửi','Bài đã đến giờ bắt đầu xử lý; xem từng lịch để biết bước chuẩn bị hoặc lượt gửi đang chờ.'
        else:
            state,label,reason='waiting_executor','Lịch đã bật — chờ Tèo xử lý','Chưa xác nhận được bộ xử lý trên máy; giữ Tèo mở và kiểm tra kết nối.'
    elif verifying:
        state,label,reason='waiting_executor','Đã gửi — chờ kiểm tra lại','Đã gửi tới Facebook nhưng chưa có bộ xử lý đang xác minh lượt này. Mở Tèo để tiếp tục kiểm tra; không gửi lại video.'
    elif attention or latest and latest['state']=='needs_attention':
        state,label='needs_attention','Dừng — cần kiểm tra lỗi'
        if attention and all(r.get('phase') in {'publish','verify'} for r in attention):
            label='Chưa xác minh đã đăng — cần kiểm tra'
        reason=(attention[0].get('message') if attention else latest.get('message')) or 'Mở lịch/lịch sử để xem nguyên nhân.'
    elif paused:
        state,label,reason='paused','Đã tạm dừng lịch Page','Bấm tiếp tục lịch đã lưu khi sẵn sàng; không tự đăng lại.'
    elif waiting:
        state,label,reason='pending','Đã lưu lịch — chưa bật','Lịch đã có nhưng chưa chạy. Duyệt và bật ở tab Lịch đăng.'
    elif submitted:
        state,label,reason='submitted','Đã gửi — chưa xác minh','Đã tạm dừng kiểm tra trạng thái sau khi gửi. Các bài tiếp theo vẫn chạy khi đến giờ; các lượt đã gửi không tự gửi lại.'
    elif latest and latest['state']=='done' and latest.get('plan_id') and not policy['config']['auto_publish']:
        state,label,reason='review','Chờ duyệt kế hoạch AI','AI đã lập kế hoạch; xem và duyệt ở tab Kế hoạch từ Codex.'
    elif done:
        state,label,reason='completed','Đã hoàn tất lịch đăng','Hiện không còn lượt xử lý đang bật cho Page này.'
    elif policy and not policy['enabled']:
        state,label,reason='off','Lịch Page đang tắt','Chưa có lịch đăng đang bật. Bật lịch AI hoặc lịch bài khi cần.'
    parts=[reason]
    if next_ai is not None:
        parts.append('AI tiếp theo: '+display_time(next_ai)+(' (đã đến giờ)' if next_ai<=clock else ''))
    if next_post is not None:
        parts.append('Bài tiếp theo: '+display_time(next_post)+(' (đã đến giờ)' if next_post<=clock else ''))
    parts.append(f'Chờ đăng: {len(armed)} · Đã gửi, chưa xác nhận: {len(verifying)} · Đã gửi, chưa xác minh: {len(submitted)} · Đã xác nhận đăng: {done} · Đã lưu, chưa bật: {len(waiting)} · Tạm dừng: {len(paused)} · Cần kiểm tra: {len(attention)}')
    return dict(state=state,label=label,detail='\n'.join(parts),next_ai_at=next_ai,next_post_at=next_post,
                waiting_posts=len(armed),verifying_posts=len(verifying),submitted_posts=len(submitted),published_posts=done,paused_posts=len(paused),error_posts=len(attention),checked_at=clock)


def read_page_status(data_dir, account, page, owner=None):
    from teodub.page_ai import PageAIStore
    from teodub.page_schedule import PageScheduleStore
    from teodub.page_publish_settings import result_checks_enabled
    store=PageAIStore(data_dir)
    policy=store.policy(account,page)
    runs=store.runs(account,page)
    schedules=PageScheduleStore(data_dir).list(account,page)
    bridge=getattr(owner,'page_bridge',None)
    controller=getattr(bridge,'ai_controller',None)
    pending=getattr(controller,'pending',None)
    ai_running=isinstance(pending,dict) and (pending.get('account_id'),pending.get('page_key'))==(account,page)
    scheduler=getattr(owner,'page_scheduler',None)
    ids=getattr(scheduler,'active',set())
    if not isinstance(ids,set):
        ids=set()
    ready=bool(bridge and getattr(bridge,'guard',None) and not getattr(bridge,'closed',False))
    return summarize(policy,runs,schedules,executor_ready=ready,active_ids=ids,ai_running=ai_running,
                     checks_enabled=result_checks_enabled(data_dir))
