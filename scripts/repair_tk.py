"""Replace only the verified Tcl/Tk subsystem; no policy changes or user-data edits.

Run with the existing portable Python, without importing tkinter in this process.
The manifest and payload are shipped beside this file. All replacements are
backed up and a failed child-process GUI smoke test rolls the replacements back.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import uuid

UNITS = ('DLLs/_tkinter.pyd', 'DLLs/tcl86t.dll', 'DLLs/tk86t.dll',
         'DLLs/zlib1.dll', 'Lib/tkinter', 'tcl')
SMOKE = ("import tkinter as tk;from tkinter import ttk;"
         "r=tk.Tk();r.withdraw();ttk.Label(r,text='Tèo').pack();r.update();"
         "print('TK_OK',tk.TclVersion,tk.TkVersion,r.tk.call('info','patchlevel'));r.destroy()")


def run_ps(script):
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                             '-EncodedCommand', encoded], capture_output=True,
                            encoding='utf-8', errors='replace', timeout=60)
    if result.returncode:
        raise ValueError('Windows verification failed: '+result.stderr[-1200:])
    return result.stdout.lstrip('\ufeff').strip()


def quote_ps(value):
    return "'"+str(value).replace("'", "''")+"'"


def signatures(paths):
    literal = ','.join(quote_ps(p) for p in paths)
    script = ("[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
              "$ErrorActionPreference='Stop';$r=@(foreach($p in @("+literal+")){"
              "$s=Get-AuthenticodeSignature -LiteralPath $p;"
              "[pscustomobject]@{path=$p;status=[string]$s.Status;signer=$s.SignerCertificate.Subject}});"
              "ConvertTo-Json -InputObject $r -Compress")
    rows = json.loads(run_ps(script))
    if len(rows) != len(paths) or any(r['status'] != 'Valid' or
            'Python Software Foundation' not in (r['signer'] or '') for r in rows):
        raise ValueError('Payload contains a binary without a valid Python Software Foundation signature.')
    return rows


def check_idle(project):
    script = ("[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
              "$ErrorActionPreference='Stop';$r=@(Get-CimInstance Win32_Process | "
              "Where-Object {($_.Name -match '^pythonw?\\.exe$' -or $_.Name -like '*Teo*.exe') "
              "-and $_.ProcessId -ne "+str(os.getpid())+" -and $_.CommandLine -and "
              "$_.CommandLine.IndexOf("+quote_ps(project)+",[StringComparison]::OrdinalIgnoreCase) -ge 0} | "
              "Select-Object -ExpandProperty ProcessId);ConvertTo-Json -InputObject $r -Compress")
    if json.loads(run_ps(script)):
        raise ValueError('Tèo/worker is still running. Finish work and close Tèo safely first.')


def plain_path(path):
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        raise ValueError('Refusing a linked/junction path: '+str(path))


def load_payload(folder):
    folder = Path(folder).resolve()
    manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8-sig'))
    if manifest.get('format') != 'teo-signed-tk-v1' or manifest.get('units') != list(UNITS):
        raise ValueError('Unsupported repair manifest.')
    payload = folder/'payload'
    plain_path(payload)
    for node in payload.rglob('*'):
        plain_path(node)
    expected = manifest['files']
    actual = {p.relative_to(payload).as_posix() for p in payload.rglob('*') if p.is_file()}
    if actual != set(expected):
        raise ValueError('Missing or unexpected payload files.')
    for name, digest in expected.items():
        path = PurePosixPath(name)
        if (path.is_absolute() or '..' in path.parts or ':' in name or '\\' in name or
                not any(name == unit or name.startswith(unit+'/') for unit in UNITS)):
            raise ValueError('Unsafe payload path: '+name)
        if hashlib.sha256((payload/name).read_bytes()).hexdigest() != digest:
            raise ValueError('Damaged payload: '+name)
    for unit in UNITS:
        if not (payload/unit).exists():
            raise ValueError('Incomplete Tk subsystem: '+unit)
    return payload, manifest


def verify_runtime(python):
    result = subprocess.run([str(python), '-I', '-S', '-c',
        "import sys,struct,json,sysconfig;print(json.dumps([list(sys.version_info[:2]),struct.calcsize('P')*8,bool(sysconfig.get_config_var('Py_GIL_DISABLED'))]))"],
        capture_output=True, text=True, timeout=15)
    if result.returncode or json.loads(result.stdout) != [[3, 13], 64, False]:
        raise ValueError('This repair requires standard 64-bit Python 3.13.')


def smoke(python):
    result = subprocess.run([str(python), '-I', '-B', '-c', SMOKE],
                            capture_output=True, text=True, encoding='utf-8', timeout=30)
    if result.returncode or 'TK_OK' not in result.stdout:
        raise ValueError('GUI test failed: '+(result.stderr or result.stdout)[-2000:])
    return result.stdout.strip()


def apply(project, package, *, verify=signatures, idle=check_idle,
          runtime_check=verify_runtime, gui_test=smoke, check_only=False, require_project=True):
    project = Path(project).absolute()
    if require_project and not (project/'launch.py').is_file():
        raise ValueError('Choose the Tèo folder containing launch.py.')
    runtime = project/'runtime'/'python'
    for path in (project, project/'runtime', runtime, runtime/'DLLs', runtime/'Lib'):
        plain_path(path)
    runtime = runtime.resolve(strict=True)
    if not runtime.is_relative_to(project.resolve()):
        raise ValueError('Runtime is outside the selected project.')
    python = runtime/'python.exe'
    payload, manifest = load_payload(package)
    runtime_check(python)
    idle(project)
    binaries = [p for p in payload.rglob('*') if p.suffix.lower() in ('.dll', '.pyd', '.exe')]
    verify(binaries)
    for unit in UNITS:
        target = runtime/unit
        plain_path(target)
        if target.is_dir():
            for node in target.rglob('*'):
                plain_path(node)
    if check_only:
        return dict(status='checked', files=len(manifest['files']))
    # All paths below are anchored under this explicitly selected runtime.
    backup = runtime.parent/'tk-repair-backups'/uuid.uuid4().hex
    backup.mkdir(parents=True)
    staged = backup/'staged'
    shutil.copytree(payload, staged)
    idle(project)  # Recheck after staging, before replacing anything.
    replaced = []
    try:
        for unit in UNITS:
            target, old, new = runtime/unit, backup/'original'/unit, staged/unit
            old.parent.mkdir(parents=True, exist_ok=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            present = target.exists()
            if present:
                os.replace(target, old)
            replaced.append((unit, present))
            os.replace(new, target)
        for name, digest in manifest['files'].items():
            if hashlib.sha256((runtime/name).read_bytes()).hexdigest() != digest:
                raise ValueError('Installed file checksum mismatch: '+name)
        output = gui_test(python)
    except BaseException:
        for unit, present in reversed(replaced):
            target, old = runtime/unit, backup/'original'/unit
            failed = backup/'failed'/unit
            failed.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                os.replace(target, failed)
            if present:
                os.replace(old, target)
        (backup/'result.json').write_text(json.dumps(dict(status='rolled_back')), encoding='utf-8')
        raise
    result = dict(status='repaired', backup=str(backup), smoke=output,
                  user_data_changed=False, policies_changed=False)
    (backup/'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', required=True)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    try:
        result = apply(args.project, Path(__file__).resolve().parent, check_only=args.check_only)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print('Open Teo normally to check. No AI calls or Facebook posts were made by this repair.')
        return 0
    except Exception as exc:
        print('REPAIR STOPPED: '+str(exc), file=sys.stderr)
        print('Keep this message. Do not disable Windows security.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
