"""Child uses the original Tèo environment, with the desktop's execution lock."""
import argparse
import json
from pathlib import Path

from teodub.common import atomic_json, protect_worker
from teodub.pipeline import _job_lock, run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding='utf-8'))
    protect_worker()
    execution = Path(config['execution_lock']).resolve()
    execution.mkdir(parents=True, exist_ok=True)
    try:
        with _job_lock(execution):
            try:
                result = run(config)
            except Exception:
                atomic_json(Path(config['directory'])/'result.json',
                            {'status': 'failed', 'message': 'Chưa chạy được phiên. Quản trị viên kiểm tra nhật ký trên máy.'})
                raise
        return 0 if result['status'] == 'completed' else 2
    except ValueError as exc:
        if 'tiến trình khác' in str(exc):
            print('Máy đang xử lý video khác.', flush=True)
            return 75
        raise


if __name__ == '__main__':
    raise SystemExit(main())
