"""UI/worker entry point shared by Python and the standalone executable."""
import multiprocessing
from pathlib import Path
import subprocess
import sys


def bootstrap_environment(args, project=None):
    """Reopen source launches in the project's installed runtime, before imports."""
    if getattr(sys, 'frozen', False):
        return None
    project = Path(project or __file__).resolve()
    if project.is_file():
        project = project.parent
    environment = project/'.venv'
    if Path(sys.prefix).resolve() == environment:
        return None
    console = environment/'Scripts'/'python.exe'
    windowed = environment/'Scripts'/'pythonw.exe'
    if not console.is_file():
        return None
    worker = any(flag in args for flag in ('--worker', '--campaign-worker', '--campaign-batch-worker'))
    executable = console if worker or not windowed.is_file() else windowed
    command = [str(executable), '-X', 'utf8', '-B', str(project/'launch.py'), *args]
    if worker:
        return subprocess.run(command, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).returncode
    subprocess.Popen(command, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return 0


def main(args=None):
    args = list(sys.argv[1:] if args is None else args)
    redirected = bootstrap_environment(args)
    if redirected is not None:
        return redirected
    if '--campaign-batch-worker' in args:
        args.remove('--campaign-batch-worker')
        from teodub.campaign_batch import main as batch_main
        return batch_main(args)
    if '--campaign-worker' in args:
        args.remove('--campaign-worker')
        from teodub.campaign import main as campaign_main
        return campaign_main(args)
    if '--worker' in args:
        args.remove('--worker')
        from teodub.pipeline import main as worker_main
        return worker_main(args)
    from teodub.app import main as ui_main
    return ui_main(args)


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
