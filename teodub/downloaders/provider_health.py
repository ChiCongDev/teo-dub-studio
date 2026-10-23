"""Remember which public download services cannot be used right now, across worker processes.

A service that said "daily limit reached", is under maintenance or never answers will not answer the next
video either. Skipping it for a while saves minutes per video and never sends an extra request to it.
Only failures of the service itself are remembered; a problem with one video (not found, bad MP4) is not.
"""
import json
import math
import os
from pathlib import Path
import re
import time

from teodub.common import atomic_json

# Seconds a service is left alone after each kind of failure; a quota failure uses the time the site reports.
PAUSES = dict(daily_quota=6*3600, maintenance=3600, rate_limited=3600, verification_required=3600,
              blocked=3600, timeout=1800, provider_error=1200, form_not_ready=1200)
# Not here on purpose: Douyin refusing one video ('refused') or any problem of one video only.
MAX_PAUSE = 24*3600
_UNITS = dict(d=86400, h=3600, m=60, s=1)


def default_path():
    root = Path(os.environ.get('LOCALAPPDATA', Path.home()/'AppData'/'Local'))/'TeoDubStudio'
    return root/'download-provider-health.json'


def reported_seconds(text):
    """Seconds in a site's own wording such as "14h 11m"; None when there is nothing to read."""
    total = 0
    for number, unit in re.findall(r'(\d+)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b',
                                   str(text or ''), re.IGNORECASE):
        total += int(number)*_UNITS[unit[0].lower()]
    return total or None


class ProviderHealth:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else default_path()

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {key: value for key, value in data.items() if isinstance(value, dict)
                and isinstance(value.get('until'), (int, float)) and math.isfinite(value['until'])}

    def _write(self, data):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(self.path, data)
        except OSError:
            pass  # Memory is an optimisation; a failed save must never fail a download.

    def unavailable(self, provider, now=None):
        """(retry_at, reason) while the service should be left alone, else None."""
        now = time.time() if now is None else now
        entry = self._read().get(provider)
        if entry and entry['until'] > now:
            return entry['until'], str(entry.get('reason', ''))
        return None

    def record_failure(self, provider, reason, retry_text=None, now=None):
        pause = PAUSES.get(reason)
        if pause is None:
            return None  # Not the service's fault: this video only.
        now = time.time() if now is None else now
        if reason == 'daily_quota':
            pause = reported_seconds(retry_text) or pause
        until = now+min(pause, MAX_PAUSE)
        data = self._read()
        data[provider] = dict(until=until, reason=reason)
        self._write(data)
        return until

    def record_success(self, provider):
        data = self._read()
        if data.pop(provider, None) is not None:
            self._write(data)

    def earliest_retry(self, providers, now=None):
        """When the first of these services may be tried again, or None if one is usable already."""
        times = []
        for provider in providers:
            state = self.unavailable(provider, now)
            if state is None:
                return None
            times.append(state[0])
        return min(times) if times else None
