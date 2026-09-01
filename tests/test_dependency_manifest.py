# -*- coding: utf-8 -*-
"""发布依赖清单的可复现性检查。"""

from __future__ import annotations

import os
import unittest


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class DependencyManifestTests(unittest.TestCase):
    def test_direct_runtime_dependencies_are_pinned(self):
        """安装包所需的顶层依赖必须有确定版本，避免离线构建漂移。"""
        path = os.path.join(PROJECT_DIR, "requirements.txt")
        with open(path, "r", encoding="utf-8") as stream:
            requirements = {
                line.strip()
                for line in stream
                if line.strip() and not line.lstrip().startswith("#")
            }

        expected = {
            "PyQt6==6.11.0",
            "PyQt6-Qt6==6.11.2",
            "PyQt6-sip==13.11.1",
            "PyQt6-WebEngine==6.11.0",
            "PyQt6-WebEngine-Qt6==6.11.2",
            "mitmproxy==12.2.3",
            "typing-extensions==4.14.0",
            "oracledb==4.0.2",
            "pymysql==1.2.0",
            "redis==8.1.0",
            "pymongo==4.17.0",
        }
        self.assertTrue(expected.issubset(requirements))


if __name__ == "__main__":
    unittest.main()
