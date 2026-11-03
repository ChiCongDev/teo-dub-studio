"""Phát hành một phiên bản mới của Tèo lên GitHub (chỉ chạy trên máy của chủ dự án).

    python scripts/release.py [--bump patch|minor|major | --version X.Y.Z] [--min-from X.Y.Z]
                              [--notes-file ghi-chu.txt] [--dry-run] [--yes] [--set-token]

Các bước: kiểm tra thư mục sạch -> chạy toàn bộ kiểm thử (lỗi là dừng) -> hỏi ghi chú tiếng Việt -> ghi version.json ->
commit với tên trung tính -> đẩy lên GitHub -> tạo Release có teo-code-X.Y.Z.zip và .sha256 -> tải lại để kiểm tra.
Mã GitHub chỉ nằm trên máy này, được Windows mã hóa (DPAPI); máy của người dùng không bao giờ cần nó."""
import argparse
import getpass
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
REPO = 'ChiCongDev/teo-dub-studio'
ITEMS = ('teodub', 'launch.py', 'version.json', 'requirements-lock.txt')
TRACKED = ITEMS+('tests', 'scripts')
IDENTITY = ('Teo Dub Studio', 'teo-dub-studio@users.noreply.github.com')  # neutral: no personal name or email
TOKEN_FILE = Path(os.environ.get('LOCALAPPDATA', str(Path.home())))/'TeoDubStudio'/'release'/'github_token.bin'
ENTROPY = b'Teo Dub Studio release token'
ALLOWED_HOSTS = ('https://api.github.com/', 'https://uploads.github.com/')
KNOWN_FAILURES = Path(__file__).resolve().parent/'known_failures.txt'


class ReleaseError(Exception):
    pass


def parse_version(text):
    parts = str(text).split('.')
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ReleaseError(f'Số phiên bản "{text}" không đúng dạng X.Y.Z.')
    return tuple(int(part) for part in parts)


def bump(current, kind):
    major, minor, patch = parse_version(current)
    if kind == 'major':
        return f'{major+1}.0.0'
    if kind == 'minor':
        return f'{major}.{minor+1}.0'
    return f'{major}.{minor}.{patch+1}'


# ---- token (Windows DPAPI; never written as text) --------------------------------------------------------------
def save_token(token, path=TOKEN_FILE):
    import win32crypt
    blob = win32crypt.CryptProtectData(token.encode('utf-8'), 'Teo release token', ENTROPY, None, None, 0x01)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(blob)


def load_token(path=TOKEN_FILE):
    import win32crypt
    try:
        _, plain = win32crypt.CryptUnprotectData(Path(path).read_bytes(), ENTROPY, None, None, 0x01)
    except Exception:
        raise ReleaseError('Chưa có mã GitHub trên máy này (hoặc không mở được). Chạy lại với --set-token để dán mã vào.') from None
    return plain.decode('utf-8').strip()


# ---- git ---------------------------------------------------------------------------------------------------------
def git(project, *args, check=True, binary=False, identity=False):
    command = ['git', '-C', str(project)]
    if identity:
        command += ['-c', f'user.name={IDENTITY[0]}', '-c', f'user.email={IDENTITY[1]}']
    done = subprocess.run(command+list(args), capture_output=True)
    if check and done.returncode:
        raise ReleaseError('git '+' '.join(args[:2])+' lỗi: '+done.stderr.decode('utf-8', 'replace').strip()[-300:])
    return done.stdout if binary else done.stdout.decode('utf-8', 'replace').strip()


def dirty_paths(project):
    out = git(project, 'status', '--porcelain', '--', *TRACKED)
    return [line[3:] for line in out.splitlines() if line.strip()]


def check_repository(project):
    problems = []
    if git(project, 'rev-parse', '--abbrev-ref', 'HEAD') != 'main':
        problems.append('Phải đứng ở nhánh main.')
    if REPO.lower() not in git(project, 'remote', 'get-url', 'origin').lower():
        problems.append(f'Kho origin không phải {REPO}.')
    dirty = dirty_paths(project)
    if dirty:
        problems.append('Còn thay đổi chưa commit trong phần sẽ phát hành, hãy commit trước: '+', '.join(dirty[:6]))
    return problems


# ---- tests -------------------------------------------------------------------------------------------------------
def load_known_failures(path=KNOWN_FAILURES):
    try:
        lines = Path(path).read_text(encoding='utf-8').splitlines()
    except OSError:
        return set()
    return {line.strip() for line in lines if line.strip() and not line.startswith('#')}


def run_group(python, group, project=PROJECT):
    done = subprocess.run([str(python), '-X', 'utf8', '-B', str(Path(project)/'scripts'/'run_tests.py'), group],
                          capture_output=True, text=True, errors='replace', cwd=str(project))
    for line in reversed(done.stdout.splitlines()):
        if line.startswith('{'):
            return json.loads(line)
    raise ReleaseError(f'Không chạy được kiểm thử nhóm {group}: '+(done.stderr or done.stdout)[-300:])


def test_name(name):
    """The same test can be reported as 'tests.test_x.C.t' when another module imports it through the package."""
    return name[len('tests.'):] if name.startswith('tests.') else name


def unexpected_failures(results, known):
    return sorted({test_name(name) for result in results for name in result['failed']} - {test_name(name) for name in known})


# ---- building the package ------------------------------------------------------------------------------------------
def version_json_bytes(version, min_from=None, today=None):
    data = dict(version=version, released=today or time.strftime('%Y-%m-%d'))
    if min_from:
        parse_version(min_from)
        data['min_from'] = min_from
    return (json.dumps(data, ensure_ascii=False, indent=2)+'\n').encode('utf-8')


def build_zip(project, out_zip, version_bytes):
    """teo-code zip from the committed files (git HEAD), with version.json replaced by `version_bytes`."""
    archive = git(project, 'archive', '--format=tar', 'HEAD', '--', *ITEMS, binary=True)
    out_zip = Path(out_zip)
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    names = set()
    with tarfile.open(fileobj=io.BytesIO(archive)) as source, zipfile.ZipFile(out_zip, 'w', zipfile.ZIP_DEFLATED) as target:
        for member in source:
            if not member.isfile() or '__pycache__' in member.name:
                continue
            data = version_bytes if member.name == 'version.json' else source.extractfile(member).read()
            target.writestr(member.name, data)
            names.add(member.name)
        if 'version.json' not in names:
            target.writestr('version.json', version_bytes)
    digest = hashlib.sha256(out_zip.read_bytes()).hexdigest()
    sha_file = out_zip.with_name(out_zip.name+'.sha256')
    sha_file.write_text(f'{digest}  {out_zip.name}\n', encoding='utf-8')
    return digest, sha_file


# ---- GitHub ------------------------------------------------------------------------------------------------------
def api(method, url, token=None, body=None, content_type='application/json', opener=urllib.request.urlopen, timeout=120):
    if not url.startswith(ALLOWED_HOSTS):
        raise ReleaseError('Địa chỉ không thuộc GitHub: '+url)
    headers = {'User-Agent': 'TeoDubStudio-Release', 'Accept': 'application/vnd.github+json'}
    if token:
        headers['Authorization'] = 'Bearer '+token
    data = body if isinstance(body, bytes) else (json.dumps(body).encode('utf-8') if body is not None else None)
    if data is not None:
        headers['Content-Type'] = content_type
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with opener(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw.decode('utf-8')) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', 'replace')[:300] if hasattr(exc, 'read') else ''
        raise ReleaseError(f'GitHub trả lỗi {exc.code}: {detail}') from None
    except OSError as exc:
        raise ReleaseError('Không nối được GitHub: '+str(exc)) from None


def latest_published_version(opener=urllib.request.urlopen):
    try:
        data = api('GET', f'https://api.github.com/repos/{REPO}/releases/latest', opener=opener, timeout=20)
    except ReleaseError as exc:
        if ' 404' in str(exc):
            return None
        raise
    return str(data.get('tag_name', '')).lstrip('vV') or None


def create_release(token, version, notes, commit, opener=urllib.request.urlopen):
    return api('POST', f'https://api.github.com/repos/{REPO}/releases', token, dict(
        tag_name='v'+version, target_commitish=commit, name='Tèo '+version, body=notes, draft=False, prerelease=False),
        opener=opener)


def upload_asset(token, release, path, content_type, opener=urllib.request.urlopen):
    base = str(release['upload_url']).split('{')[0]
    url = f'{base}?name={urllib.request.quote(Path(path).name)}'
    return api('POST', url, token, Path(path).read_bytes(), content_type, opener=opener, timeout=600)


def verify_published(version, expected_sha, opener=urllib.request.urlopen):
    data = api('GET', f'https://api.github.com/repos/{REPO}/releases/latest', opener=opener, timeout=30)
    names = {item['name']: item['browser_download_url'] for item in data.get('assets', [])}
    zip_name = f'teo-code-{version}.zip'
    if str(data.get('tag_name', '')).lstrip('vV') != version or zip_name not in names or zip_name+'.sha256' not in names:
        raise ReleaseError('Release vừa tạo chưa đủ tệp hoặc sai số phiên bản trên GitHub.')
    request = urllib.request.Request(names[zip_name+'.sha256'], headers={'User-Agent': 'TeoDubStudio-Release'})
    with opener(request, timeout=60) as response:
        published = re.search(r'[0-9a-f]{64}', response.read(4096).decode('utf-8', 'replace'))
    if not published or published.group(0) != expected_sha:
        raise ReleaseError('Mã SHA256 trên GitHub không khớp với tệp vừa đóng gói.')
    return True


def read_notes(path=None, stream=None):
    if path:
        text = Path(path).read_text(encoding='utf-8')
    else:
        print('Nhập ghi chú thay đổi bằng tiếng Việt, mỗi dòng một ý. Dòng trống để kết thúc:')
        lines = []
        for line in (stream or sys.stdin):
            if not line.strip():
                break
            lines.append(line.rstrip('\n'))
        text = '\n'.join(lines)
    text = text.strip()
    if len(text) < 10:
        raise ReleaseError('Ghi chú thay đổi đang trống hoặc quá ngắn; người dùng sẽ đọc nó trước khi cập nhật.')
    if len(text) > 3500:
        raise ReleaseError('Ghi chú quá dài (tối đa khoảng 3500 ký tự).')
    return text


def choose_version(current, latest, requested=None, kind='patch'):
    target = requested or bump(current, kind)
    parse_version(target)
    if latest and parse_version(target) <= parse_version(latest):
        raise ReleaseError(f'Bản {target} không mới hơn bản đã phát hành trên GitHub ({latest}).')
    if parse_version(target) < parse_version(current):
        raise ReleaseError(f'Bản {target} thấp hơn bản đang có trong thư mục ({current}).')
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description='Phát hành phiên bản mới của Tèo.')
    parser.add_argument('--bump', choices=('patch', 'minor', 'major'), default='patch')
    parser.add_argument('--version')
    parser.add_argument('--min-from')
    parser.add_argument('--notes-file')
    parser.add_argument('--dry-run', action='store_true', help='Chạy kiểm thử và đóng gói nhưng không commit, không đăng.')
    parser.add_argument('--skip-tests', action='store_true', help='Chỉ dùng với --dry-run.')
    parser.add_argument('--yes', action='store_true', help='Không hỏi xác nhận cuối.')
    parser.add_argument('--set-token', action='store_true')
    parser.add_argument('--out', default=str(PROJECT/'dist-release'))
    options = parser.parse_args(argv)
    try:
        if options.set_token:
            save_token(getpass.getpass('Dán mã GitHub (không hiện lên màn hình): ').strip())
            print('Đã lưu mã, được Windows mã hóa trên máy này.')
            return 0
        if options.skip_tests and not options.dry_run:
            raise ReleaseError('--skip-tests chỉ dùng được cùng --dry-run.')
        problems = check_repository(PROJECT)
        if problems:
            raise ReleaseError('\n'.join(problems))
        token = None if options.dry_run else load_token()
        current = json.loads((PROJECT/'version.json').read_text(encoding='utf-8'))['version']
        latest = latest_published_version()
        version = choose_version(current, latest, options.version, options.bump)
        print(f'Đang có {current}; bản đã đăng: {latest or "chưa có"}; sẽ phát hành {version}.')
        notes = read_notes(options.notes_file)
        if not options.skip_tests:
            known = load_known_failures()
            results = []
            for group, folder in (('main', '.venv'), ('web', '.venv-web')):
                python = PROJECT/folder/'Scripts'/'python.exe'
                if not python.is_file():
                    raise ReleaseError(f'Thiếu {folder}; không chạy được kiểm thử nhóm {group}.')
                print(f'Chạy kiểm thử nhóm {group} (có thể mất 15 phút)…')
                results.append(run_group(python, group))
            bad = unexpected_failures(results, known)
            print(f'Đã chạy {sum(r["ran"] for r in results)} bài.')
            if bad:
                raise ReleaseError('Kiểm thử lỗi, dừng phát hành:\n- '+'\n- '.join(bad[:15]))
        version_bytes = version_json_bytes(version, options.min_from)
        out = Path(options.out)
        if options.dry_run:
            # Build from HEAD with the new version.json in memory; nothing in the repository changes.
            digest, sha_file = build_zip(PROJECT, out/f'teo-code-{version}.zip', version_bytes)
            print(f'[chạy thử] Đã đóng gói {out/("teo-code-"+version+".zip")} (SHA256 {digest[:12]}…). Chưa commit, chưa đăng gì.')
            return 0
        if not options.yes and input(f'Gõ PHAT HANH để đăng bản {version} lên GitHub: ').strip() != 'PHAT HANH':
            raise ReleaseError('Đã hủy, chưa đăng gì.')
        if (PROJECT/'version.json').read_bytes() != version_bytes:
            (PROJECT/'version.json').write_bytes(version_bytes)
            git(PROJECT, 'add', 'version.json')
            git(PROJECT, 'commit', '-m', f'Phiên bản {version}', identity=True)
        git(PROJECT, 'push', 'origin', 'HEAD')
        digest, sha_file = build_zip(PROJECT, out/f'teo-code-{version}.zip', (PROJECT/'version.json').read_bytes())
        release = create_release(token, version, notes, git(PROJECT, 'rev-parse', 'HEAD'))
        zip_path = out/f'teo-code-{version}.zip'
        upload_asset(token, release, zip_path, 'application/zip')
        upload_asset(token, release, sha_file, 'text/plain')
        verify_published(version, digest)
        print(f'Đã phát hành {version}. Các máy sẽ thấy "Có bản mới" khi mở Tèo.')
        return 0
    except ReleaseError as exc:
        print('DỪNG: '+str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
