"""Build an onedir Windows app; local models stay in their download cache.

Run explicitly after installing the project's dependencies and PyInstaller in
the project .venv. This script never starts the app or copies account settings.
"""
import argparse
from pathlib import Path
import subprocess
import sys


def build_command(project, name='TeoDubStudio'):
    project = Path(project).resolve()
    python = project/'.venv'/'Scripts'/'python.exe'
    if not python.is_file():
        raise ValueError('Cần cài môi trường .venv của Tèo Dub Studio trước khi đóng gói.')
    return [str(python), '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed',
            '--name', name, '--distpath', str(project/'dist'), '--workpath', str(project/'build'),
            '--specpath', str(project/'build'), '--collect-all', 'teodub', str(project/'launch.py')]


def main(args=None):
    parser = argparse.ArgumentParser(description='Đóng gói Tèo Dub Studio cho Windows')
    parser.add_argument('--name', default='TeoDubStudio')
    options = parser.parse_args(args)
    project = Path(__file__).resolve().parents[1]
    if sys.platform != 'win32':
        parser.error('Cần đóng gói trên Windows.')
    try:
        command = build_command(project, options.name)
    except ValueError as exc:
        parser.error(str(exc))
    return subprocess.call(command, cwd=str(project))


if __name__ == '__main__':
    raise SystemExit(main())
