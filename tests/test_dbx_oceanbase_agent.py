# -*- coding: utf-8 -*-
"""OceanBase Java Agent 只在已保存 JAR 时接管连接。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.dbx_oceanbase_agent import java_major


class OceanBaseAgentTests(unittest.TestCase):
    def test_java_major_reads_modern_and_legacy_versions(self):
        with patch('tools.dbx_oceanbase_agent.subprocess.run') as run:
            run.return_value.stderr = 'java version "21.0.2" 2024-01-16 LTS'
            run.return_value.stdout = ''
            self.assertEqual(java_major('java'), 21)
            run.return_value.stderr = 'java version "1.8.0_144"'
            self.assertEqual(java_major('java'), 8)

    def test_saved_jar_is_required_before_skipping_odbc(self):
        from tools.db_connect import _connect_oceanbase_oracle

        with patch('tools.dbx_oceanbase_agent.java_agent_usable', return_value=False), \
                patch('tools.db_connect.require_oceanbase_oracle_odbc_config', side_effect=RuntimeError('odbc-gate')):
            with self.assertRaises(RuntimeError) as caught:
                _connect_oceanbase_oracle({'host': '10.0.0.1', 'database': 'APP', 'username': 'u'})
        self.assertEqual(str(caught.exception), 'odbc-gate')

    def test_java_17_does_not_count_as_usable(self):
        from tools.dbx_oceanbase_agent import java_agent_usable
        with patch('tools.dbx_oceanbase_agent.configured_agent_paths', return_value=('C:\\agent.jar', 'java')), \
                patch('tools.dbx_oceanbase_agent.agent_jar_ready', return_value=True), \
                patch('tools.dbx_oceanbase_agent.java_major', return_value=17):
            self.assertFalse(java_agent_usable())
