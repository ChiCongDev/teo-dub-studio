"""Keep separate Tèo windows from opening the same isolated scan profile."""
from contextlib import ExitStack, contextmanager
from pathlib import Path

from teodub.pipeline import _job_lock


@contextmanager
def channel_scan_guard(profile_dir):
    """Hold an OS lock for the whole scan; a crashed process releases it."""
    directory = Path(profile_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with ExitStack() as resources:
        try:
            resources.enter_context(_job_lock(directory))
        except ValueError as exc:
            raise ValueError(
                'Một cửa sổ Tèo khác đang dùng cùng hồ sơ trình duyệt để đăng nhập hoặc đọc kênh. '
                'Chờ lượt đó hoàn tất, đóng cửa sổ đăng nhập hoặc bấm Dừng rồi thử lại.'
            ) from exc
        # Keep errors from reading the channel intact: only acquisition is relabeled.
        yield
