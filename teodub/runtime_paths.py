"""Where the Python that runs Tèo lives: the project's .venv, else the bundled runtime."""
import os
from pathlib import Path


def project_root():
    return Path(__file__).resolve().parents[1]


def python_roots(project):
    project = Path(project)
    if os.name == 'nt':
        return [(project/'.venv'/'Scripts', project/'.venv'),
                (project/'runtime'/'python', project/'runtime'/'python')]
    return [(project/'.venv'/'bin', project/'.venv')]


def find_python(project=None, *, windowed=False):
    """Return the interpreter path (pythonw.exe when asked and present), or None."""
    for folder, _prefix in python_roots(project or project_root()):
        names = ('pythonw.exe', 'python.exe') if windowed else ('python.exe',)
        if os.name != 'nt':
            names = ('python',)
        for name in names:
            candidate = folder/name
            if candidate.is_file():
                return candidate
    return None


def environment_prefix(project=None):
    """The sys.prefix of the interpreter chosen above, for 'am I already inside it' checks."""
    python = find_python(project)
    if python is None:
        return None
    for folder, prefix in python_roots(project or project_root()):
        if python.parent == folder:
            return prefix.resolve()
    return None
