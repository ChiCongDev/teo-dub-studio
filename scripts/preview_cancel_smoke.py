"""Real local preview cancellation/resume, using already-cached voice models."""
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from teodub.common import atomic_json, hash_file
from teodub.tts import validate_audio


def read_result(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def events(path):
    if not path.exists():
        return []
    result = []
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            result.append(json.loads(line))
        except ValueError:
            pass
    return result


def wait_exit(process, seconds):
    deadline = time.monotonic()+seconds
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(.05)
    return process.poll()


def stop_process(process):
    if process.poll() is None:
        process.terminate()
        if wait_exit(process, 3) is None:
            process.kill()
            process.wait(timeout=3)


def main():
    folder = ROOT/'work/cancel-smoke'/('job-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6])
    folder.mkdir(parents=True)
    config = dict(directory=str(folder), stop_file=str(folder/'stop.request'), mode='preview',
                  voice='Trúc Ly', input='', output_dir=str(folder/'output'), original_dir=str(folder/'original'),
                  subtitles='', speed=.9, original_gain=.1)
    config_path = folder/'config.json'
    atomic_json(config_path, config)
    env = os.environ.copy()
    env.update(PYTHONUTF8='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    command = [sys.executable, '-X', 'utf8', '-B', '-m', 'teodub.pipeline', '--config', str(config_path)]
    report = dict(directory=str(folder), voice=config['voice'], offline=True, passed=False)
    process = None
    try:
        with (folder/'first-worker.log').open('wb') as log:
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            deadline = time.monotonic()+40
            while time.monotonic() < deadline:
                seen = events(folder/'events.jsonl')
                if any('nạp bộ giọng' in row.get('message', '') for row in seen):
                    (folder/'stop.request').touch()
                    report['stop_requested_on_model_loading'] = True
                    print('Stop requested during real model loading.', flush=True)
                    break
                if process.poll() is not None:
                    raise RuntimeError('Preview ended before the model-loading stop checkpoint.')
                time.sleep(.025)
            else:
                raise RuntimeError('Model-loading event was not observed.')
            stopped = time.monotonic()
            if wait_exit(process, 8) is None:
                report['cancel_mode'] = 'forced_after_8_seconds'
                stop_process(process)
            else:
                report['cancel_mode'] = 'cooperative'
            report['stop_latency_s'] = round(time.monotonic()-stopped, 3)
            report['first_exit_code'] = process.returncode
            report['first_result'] = read_result(folder/'result.json')
            if (report['first_result'] or {}).get('status') == 'completed':
                raise RuntimeError('Stop checkpoint did not prevent preview completion.')
            if (folder/'preview.wav').exists():
                raise RuntimeError('Cancelled preview was published.')
        # Explicit smoke-test resume removes only this worker's own stop marker.
        (folder/'stop.request').unlink()
        report['request_sha256_before_resume'] = hash_file(folder/'request.json')
        print('Restarting the same frozen preview configuration.', flush=True)
        with (folder/'resume-worker.log').open('wb') as log:
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if wait_exit(process, 150) is None:
                raise RuntimeError('Resumed local preview timed out.')
        report['resume_exit_code'] = process.returncode
        report['result'] = read_result(folder/'result.json')
        if process.returncode or report['result'].get('status') != 'completed':
            raise RuntimeError('Resumed worker did not complete: '+str(report['result']))
        report['audio'] = validate_audio(report['result']['outputs']['audio'])
        if report['request_sha256_before_resume'] != hash_file(folder/'request.json'):
            raise RuntimeError('Request snapshot changed during resume.')
        report['passed'] = True
    except Exception as exc:
        report['error'] = str(exc)
        raise
    finally:
        if process is not None:
            stop_process(process)
        atomic_json(folder/'smoke-report.json', report)
        print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
