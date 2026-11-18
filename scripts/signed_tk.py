"""Verified official Tcl/Tk donor for CPython 3.13 x64 portable builds.

3.13.18 is source-only. Use the PSF-signed Tk subsystem from 3.13.17,
without replacing/downgrading the 3.13.18 interpreter or its security fixes.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import urllib.request
import uuid

import repair_tk

URL = 'https://www.python.org/ftp/python/3.13.17/amd64/tcltk.msi'
SHA256 = 'ae3e5f9dd998b36d14655cd3444d0af906c1849c60aee40994eeb1e460a3cabf'


def prepare(work):
    work = Path(work).resolve()/'signed-tk-3.13'
    work.mkdir(parents=True, exist_ok=True)
    msi = work/'tcltk.msi'
    if not msi.exists():
        with urllib.request.urlopen(URL, timeout=60) as source, msi.open('wb') as out:
            shutil.copyfileobj(source, out)
    if hashlib.sha256(msi.read_bytes()).hexdigest() != SHA256:
        raise ValueError('Official Tcl/Tk MSI checksum mismatch; refusing to use it.')
    repair_tk.signatures([msi])
    extracted = work/'official'
    if not (extracted/'DLLs'/'_tkinter.pyd').exists():
        # msiexec's property parser requires quotes around the property value,
        # rather than around the whole TARGETDIR=... argument.
        command = ('msiexec.exe /a '+subprocess.list2cmdline([str(msi)])+
                   ' /qn TARGETDIR="'+str(extracted)+'" /L*v '+
                   subprocess.list2cmdline([str(work/'extract.log')]))
        result = subprocess.run(command, timeout=120)
        if result.returncode:
            raise ValueError('Official Tcl/Tk extraction failed: '+str(result.returncode))
    package = work/('package-'+uuid.uuid4().hex[:8])
    payload = package/'payload'
    for unit in repair_tk.UNITS:
        source, target = extracted/unit, payload/unit
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            # nmake is a developer build helper, not part of the Tk runtime.
            # The official package includes an unsigned helper EXE there.
            shutil.copytree(source, target, ignore=shutil.ignore_patterns('nmake'))
        else:
            shutil.copy2(source, target)
    binaries = [p for p in payload.rglob('*') if p.suffix.lower() in ('.dll', '.pyd', '.exe')]
    signed = repair_tk.signatures(binaries)
    manifest = dict(format='teo-signed-tk-v1', units=list(repair_tk.UNITS),
        source_url=URL, source_sha256=SHA256, donor_version='3.13.17',
        files={p.relative_to(payload).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted(payload.rglob('*')) if p.is_file()})
    (package/'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    (work/'signatures.json').write_text(json.dumps(signed, indent=2), encoding='utf-8')
    shutil.copy2(Path(repair_tk.__file__), package/'repair_tk.py')
    repair_tk.load_payload(package)
    return package


def install(work):
    package = prepare(work)
    return repair_tk.apply(work, package, require_project=False)
