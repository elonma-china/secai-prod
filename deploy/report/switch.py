#!/usr/bin/env python3
"""Deploy/rollback only the independent report Docker service."""
import argparse
import fcntl
import json
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path('/home/ict/secai-report-devspace')
COMPOSE = ROOT / 'docker-report/compose.json'


def run(*args):
    subprocess.run(args, check=True)


def ready():
    key = (ROOT / 'report-api.key').read_text().strip()
    for _ in range(45):
        try:
            request = urllib.request.Request('http://127.0.0.1:18183/ai/v2/reports/templates',
                                            headers={'X-API-Key': key})
            with urllib.request.urlopen(request, timeout=3) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(1)
    raise RuntimeError('Report API did not become ready')


def compose(*args):
    run('docker', 'compose', '-f', str(COMPOSE), *args)


def fallback():
    compose('stop', 'reports')
    run('systemctl', '--user', 'enable', '--now', 'secai-report-devspace.service')
    ready()
    print('Rollback complete: previous systemd report service uses the same report DB.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['apply', 'rollback'])
    options = parser.parse_args()
    with (ROOT / '.report-docker.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        existing = subprocess.run(['docker', 'inspect', 'secai-report-backend'],
                                  capture_output=True, text=True)
        if existing.returncode == 0:
            container = json.loads(existing.stdout)[0]
            if container['Config']['Labels'].get('com.docker.compose.project') != 'secai-report-devspace':
                raise RuntimeError('Report container is owned by another compose project')
        if options.mode == 'rollback':
            fallback()
            return
        with sqlite3.connect(ROOT / 'data/reports.db') as source, \
                sqlite3.connect(ROOT / 'docker-report' / ('reports-before-' + str(time.time_ns()) + '.db')) as backup:
            source.backup(backup)
        compose('config', '--quiet')
        run('systemctl', '--user', 'stop', 'secai-report-devspace.service')
        try:
            compose('up', '-d', '--no-deps', '--no-build', 'reports')
            ready()
            run('systemctl', '--user', 'disable', 'secai-report-devspace.service')
        except Exception:
            fallback()
            raise
        print('Report Docker deployment passed; gateway 8131 is outside this compose project.')


if __name__ == '__main__':
    main()
