"""Run the test suite in two groups and print the failures as JSON, so the release tool can compare them with
scripts/known_failures.txt. Usage: python scripts/run_tests.py main|web

main: every test module except test_web_*  (run with the project's .venv)
web : only test_web_* modules             (run with .venv-web, which has Flask)"""
import json
from pathlib import Path
import sys
import unittest

PROJECT = Path(__file__).resolve().parents[1]


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def module_of(test):
    name = type(test).__module__
    if name == 'unittest.loader':  # an import failure: its id is the module name
        name = test.id().split('.')[-1]
    return name.split('.')[-1]


def load(group):
    sys.path.insert(0, str(PROJECT))
    discovered = unittest.TestLoader().discover(str(PROJECT/'tests'), pattern='test_*.py', top_level_dir=str(PROJECT/'tests'))
    wanted = [test for test in flatten(discovered) if module_of(test).startswith('test_web_') == (group == 'web')]
    return unittest.TestSuite(wanted)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    group = argv[0] if argv else 'main'
    if group not in ('main', 'web'):
        raise SystemExit('Dùng: run_tests.py main|web')
    suite = load(group)
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=0).run(suite)
    failed = sorted({test.id() for test, _ in result.failures + result.errors})
    print(json.dumps(dict(group=group, ran=result.testsRun, failed=failed), ensure_ascii=False))
    return 0 if not failed else 1


if __name__ == '__main__':
    raise SystemExit(main())
