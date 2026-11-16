"""Keep these tests away from the machine's real settings, servers and keys.

Import this module *before* any panel/tool module. It redirects HOME, the
config directory and ``local_data_dir()`` to a throw-away tree, so
``load_servers`` / ``load_settings`` / ``secure_store`` / ``intranet_llm`` read
empty files instead of the operator's real ones.

It also snapshots file **metadata only** (name, size, mtime) for the repo's
``data/`` directory and the real per-user config locations, and offers
``assert_untouched()`` so a test can prove it left them alone.
"""
import atexit
import os
import shutil
import tempfile
from pathlib import Path


# --- metadata-only snapshot of the real locations (taken before redirect) ----
def _snapshot(roots):
    seen = {}
    for root in roots:
        try:
            if not root.exists():
                continue
            for entry in root.rglob('*'):
                try:
                    stat = entry.stat()
                except OSError:
                    continue
                seen[str(entry)] = (entry.is_dir(), stat.st_size, stat.st_mtime_ns)
        except OSError:
            continue
    return seen


_REAL_HOME = Path(os.environ.get('USERPROFILE') or os.environ.get('HOME') or '~')
_REPO_ROOT = Path(__file__).resolve().parents[2]
_WATCHED = (
    _REPO_ROOT / 'data',
    _REAL_HOME / '.pengtools',
    _REAL_HOME / 'AppData' / 'Roaming' / 'PengToolsHub',
    _REAL_HOME / '.config' / 'PengToolsHub',
)
_BASELINE = _snapshot(_WATCHED)


# --- redirect everything the app would write ---------------------------------
_TMP = tempfile.mkdtemp(prefix='pengtools-ssh-test-')
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

_TMP_DATA = os.path.join(_TMP, 'data')
os.makedirs(_TMP_DATA, exist_ok=True)
os.makedirs(os.path.join(_TMP_DATA, 'logs'), exist_ok=True)

for _name in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA'):
    _value = os.path.join(_TMP, _name.lower())
    os.makedirs(_value, exist_ok=True)
    os.environ[_name] = _value

import config  # noqa: E402  (imported only after HOME is redirected)

# Function-level redirect: modules that call ``local_data_dir()`` later
# (ui/log_analysis/workspace, tools/agent_diag) resolve this attribute at call
# time, so replacing it here is enough for them.
config.local_data_dir = lambda *args, **kwargs: _TMP_DATA
config.CONFIG_DIR = _TMP_DATA
os.makedirs(config.CONFIG_DIR, exist_ok=True)

for _attr in ('SETTINGS_FILE', 'OPS_SERVERS_FILE', 'OPS_LOG_SETTINGS_FILE',
              'OPS_SSH_KNOWN_HOSTS_FILE', 'SYSTEMS_FILE', 'CUSTOM_OPS_FILE',
              'AI_LOCAL_FILE', 'PRIVATE_KNOWLEDGE_FILE', 'DAILY_REPORTS_FILE',
              'DAILY_REPORT_SETTINGS_FILE', 'DAILY_REPORT_DRAFTS_FILE'):
    if hasattr(config, _attr):
        setattr(config, _attr,
                os.path.join(config.CONFIG_DIR, os.path.basename(getattr(config, _attr))))


def temp_dir() -> str:
    """The throw-away root, for tests that need to write something."""
    return _TMP


def assert_untouched() -> None:
    """Raise if the repo data/ or a real user config dir changed metadata."""
    after = _snapshot(_WATCHED)
    if after == _BASELINE:
        return
    added = sorted(set(after) - set(_BASELINE))[:5]
    removed = sorted(set(_BASELINE) - set(after))[:5]
    modified = [key for key in after if key in _BASELINE and after[key] != _BASELINE[key]][:5]
    raise AssertionError(
        f'real data untouched check failed: added={added} removed={removed} modified={modified}')
