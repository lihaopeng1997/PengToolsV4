# -*- coding: utf-8 -*-
"""发布脚本保护测试（静态）：data 永不删除、legacy EXE 入锁、destructive 清理顺序正确。"""
import os
import re
import unittest

SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'scripts', 'build_release.ps1',
)


class ReleasePackagingGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(SCRIPT, encoding='utf-8') as fh:
            cls.lines = fh.read().splitlines()
        cls.text = '\n'.join(cls.lines)

    def line_no(self, pattern):
        for i, line in enumerate(self.lines, start=1):
            if re.search(pattern, line):
                return i
        return -1

    def test_1_no_rmdir_of_user_data(self):
        """构建脚本不得出现任何针对 data 目录的 rmdir 删除逻辑。"""
        hits = [line for line in self.lines
                if re.search(r'rmdir', line, re.I) and re.search(r'data', line, re.I)]
        self.assertEqual(hits, [], '发现删除 data 的 rmdir 逻辑：%s' % hits)

    def test_2_no_destructive_data_variable(self):
        """旧 $InstallerDataDir/$LegacyDataDir 删除变量应已移除。"""
        self.assertNotIn('$InstallerDataDir', self.text)
        self.assertNotIn('$LegacyDataDir', self.text)

    def test_3_legacy_installer_exe_in_lock_targets(self):
        """Installer\PengToolsHub.exe（旧 onefile）必须定义为锁目标并进入 $LockTargets。"""
        self.assertIn("Join-Path $InstallerDir 'PengToolsHub.exe'", self.text)
        m = re.search(r'\$LockTargets = @\(([^)]*)\)', self.text, re.S)
        self.assertIsNotNone(m, '未找到 $LockTargets 定义')
        for var in ('$ExePath', '$InstallerExePath', '$LegacyOnefileExe',
                    '$LegacyInstallerExe', '$LegacyPrivateExe', '$LegacyPrivateOnefileExe'):
            self.assertIn(var, m.group(1), f'锁目标缺少 {var}')

    def test_4_cleanup_order(self):
        """顺序：data 安全检查 < 锁检查 < destructive cleanup。"""
        data_check = self.line_no(r'DataSafetyPaths')
        lock = self.line_no(r'Assert-ReleaseArtifactsUnlocked -Targets')
        cleanup = self.line_no(r"锁校验通过后清理旧产物")
        self.assertGreater(data_check, 0)
        self.assertGreater(lock, data_check, '锁检查必须发生在 data 安全检查之后')
        self.assertGreater(cleanup, lock, 'destructive cleanup 必须发生在锁检查之后')

    def test_5_assert_uses_targets_array(self):
        """锁函数使用 string[]$Targets 参数，不按位置硬编码。"""
        self.assertIn('[string[]]$Targets', self.text)
        self.assertIn('Assert-ReleaseArtifactsUnlocked -Targets $LockTargets', self.text)


if __name__ == '__main__':
    unittest.main()
