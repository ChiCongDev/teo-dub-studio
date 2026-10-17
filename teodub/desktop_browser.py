"""Open the installed Cốc Cốc normally, without an automation session."""
import subprocess

from teodub.channel_browser import find_coccoc


def open_desktop_coccoc(text=''):
    url = None
    if text.strip():
        from teodub.channel_discovery import channel_url
        url = channel_url(text)
    executable = find_coccoc()
    if executable is None:
        raise ValueError('Chưa tìm thấy Cốc Cốc đã cài trên máy. Cài Cốc Cốc hoặc kiểm tra lại đường dẫn cài đặt.')
    command = [str(executable)]
    if url:
        command.append(url)
    try:
        subprocess.Popen(command, shell=False, cwd=str(executable.parent), close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise ValueError('Chưa gửi được lệnh mở Cốc Cốc trên máy: '+str(exc)) from exc
    return executable
