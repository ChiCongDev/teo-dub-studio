"""Microsoft's Visual C++ runtime for the standalone bundle (12/11/2026). Bundles made before 12/11/2026 lack msvcp140.dll,
msvcp140_1.dll and vcomp140.dll; on a PC without the "Visual C++ Redistributable" every video then stops with "Could not find
module ctranslate2.dll". These copies travel with the code update (teodub/vc_redist) and are put next to the bundle's python.exe
when Tèo starts, so "Cập nhật phiên bản" alone fixes such a PC. Never raises; never replaces a file that is already there."""
import os
from pathlib import Path
import shutil
import sys

FILES = ('msvcp140.dll', 'msvcp140_1.dll', 'vcomp140.dll')
SOURCE = Path(__file__).resolve().parent/'vc_redist'


def bundle_python_dir(executable=None, project=None):
    """The bundle's runtime/python folder when Tèo runs from it; None for a developer .venv or a system Python."""
    folder = Path(executable or sys.executable).resolve().parent
    project = Path(project) if project else Path(__file__).resolve().parents[1]
    return folder if folder == (project/'runtime'/'python').resolve() else None


def ensure(executable=None, project=None, source=SOURCE):
    """Copy the files the bundle's Python folder lacks. Returns the names copied."""
    if os.name != 'nt':
        return []
    try:
        folder = bundle_python_dir(executable, project)
        if folder is None:
            return []
        copied = []
        for name in FILES:
            target = folder/name
            if target.exists() or not (Path(source)/name).is_file():
                continue
            temporary = folder/f'{name}.{os.getpid()}.tmp'
            shutil.copyfile(Path(source)/name, temporary)
            try:
                os.replace(temporary, target)
            except OSError:  # Another Tèo process put it there first and has it loaded.
                temporary.unlink(missing_ok=True)
                if not target.exists():
                    raise
                continue
            copied.append(name)
        return copied
    except OSError:
        try:  # A read-only folder: this process still finds the files where they are.
            os.add_dll_directory(str(source))
        except (OSError, AttributeError):
            pass
        return []
