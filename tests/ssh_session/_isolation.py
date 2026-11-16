"""Keep these tests away from the machine's real settings, servers and keys.

Import this module *before* any panel/tool module: it redirects HOME and the
config directory to a throw-away tree, so ``load_servers`` / ``load_settings``
read empty files instead of the operator's real ones.
"""
import atexit
import os
import shutil
import tempfile

_TMP = tempfile.mkdtemp(prefix='pengtools-ssh-test-')
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

for _name in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA'):
    _value = os.path.join(_TMP, _name.lower())
    os.makedirs(_value, exist_ok=True)
    os.environ[_name] = _value

import config  # noqa: E402  (imported only after HOME is redirected)

config.CONFIG_DIR = os.path.join(_TMP, 'config')
os.makedirs(config.CONFIG_DIR, exist_ok=True)

for _attr in ('SETTINGS_FILE', 'OPS_SERVERS_FILE', 'OPS_LOG_SETTINGS_FILE',
              'OPS_SSH_KNOWN_HOSTS_FILE', 'SYSTEMS_FILE', 'CUSTOM_OPS_FILE'):
    if hasattr(config, _attr):
        setattr(config, _attr,
                os.path.join(config.CONFIG_DIR, os.path.basename(getattr(config, _attr))))


def temp_dir() -> str:
    """The throw-away root, for tests that need to write something."""
    return _TMP
