# -*- coding: utf-8 -*-
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.svn_workspace import (
    SvnError, add_text_file, checkout, commit_working_copy, lock_file,
    run_svn, unlock_file, update_working_copy,
)


@unittest.skipUnless(shutil.which('svn'), 'SVN command line is unavailable')
class SvnLockTests(unittest.TestCase):
    def test_lock_blocks_other_working_copy_until_unlock(self):
        svnadmin = os.path.join(os.path.dirname(shutil.which('svn')), 'svnadmin.exe')
        if not os.path.isfile(svnadmin):
            self.skipTest('svnadmin is unavailable')
        with tempfile.TemporaryDirectory() as temp:
            repository = os.path.join(temp, 'repository')
            subprocess.run([svnadmin, 'create', repository], check=True)
            url = Path(repository).as_uri()
            run_svn(['mkdir', f'{url}/trunk', '-m', 'init'])
            first = os.path.join(temp, 'first')
            second = os.path.join(temp, 'second')
            checkout(f'{url}/trunk', first)
            checkout(f'{url}/trunk', second)

            first_file = add_text_file(first, 'shared.txt', 'initial')
            commit_working_copy(first, 'add shared file')
            update_working_copy(second)
            second_file = os.path.join(second, 'shared.txt')

            result = lock_file(first_file, 'developer one is editing')
            self.assertIn('locked', result['output'].casefold())
            with open(second_file, 'w', encoding='utf-8') as stream:
                stream.write('changed by developer two')
            with self.assertRaises(SvnError):
                commit_working_copy(second, 'conflicting change')

            result = unlock_file(first_file)
            self.assertIn('unlocked', result['output'].casefold())
            committed = commit_working_copy(second, 'change after unlock')
            self.assertIn('Committed revision', committed['output'])


if __name__ == '__main__':
    unittest.main()
