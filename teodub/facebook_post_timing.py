"""Shared bounded budgets for browser posting and the desktop watchdog."""
UPLOAD_WAIT_SECONDS = 15 * 60
BROWSER_ACTION_SECONDS = 6 * 60
VIDEO_PREPARATION_SECONDS = UPLOAD_WAIT_SECONDS + 5 * 60
PUBLISH_RESULT_SECONDS = 30 * 60
RESULT_INSPECTION_SECONDS = 90


def post_action_seconds(operation, kind):
    if operation not in {'prepare', 'publish', 'review', 'inspect_result', 'verify'}:
        raise ValueError('Thao tác đăng bài không hợp lệ.')
    if operation == 'inspect_result':
        return RESULT_INSPECTION_SECONDS
    if operation in {'publish', 'verify'} and kind == 'video':
        return PUBLISH_RESULT_SECONDS + 60
    return VIDEO_PREPARATION_SECONDS if operation == 'prepare' and kind == 'video' else BROWSER_ACTION_SECONDS


def post_timeout_message(request):
    seconds = request.get('timeout_seconds', BROWSER_ACTION_SECONDS)
    previous = str(request.get('message', '')).strip()
    stage = (' Bước cuối: '+previous[:500]) if request.get('state') == 'running' and previous else ''
    return (f'Quá thời gian thao tác trong Chrome ({seconds//60:g} phút).'+stage+
            ' Kiểm tra Page trước; Tèo đã dừng yêu cầu và không tự gửi lại.')
