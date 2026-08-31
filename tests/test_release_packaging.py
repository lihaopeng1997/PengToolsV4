# -*- coding: utf-8 -*-
"""发布脚本保护测试（静态）：data 永不删除、legacy EXE 入锁、destructive 清理顺序正确。

Step 1C 增强：
- dist\PengToolsHub\data（frozen 用户数据，AppDir 会被整体重建）必须有 data guard；
- 所有被整体 rmdir 的程序目录，其 data 子目录必须已进入 $DataSafetyPaths；
- 执行顺序：data 检查 < 锁检查 < build_info 写入 < staging 复制 < destructive cleanup。
"""
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

    # ---------- 基础守护 ----------

    def test_1_no_rmdir_of_user_data(self):
        """构建脚本不得出现任何针对 data 目录的 rmdir 删除逻辑（按行）。"""
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

    def test_5_assert_uses_targets_array(self):
        """锁函数使用 string[]$Targets 参数，不按位置硬编码。"""
        self.assertIn('[string[]]$Targets', self.text)
        self.assertIn('Assert-ReleaseArtifactsUnlocked -Targets $LockTargets', self.text)

    # ---------- Step 1C：dist data 守护与 cleanup 对应 ----------

    def _safety_paths_block(self):
        """按行解析 $DataSafetyPaths 数组体（元素含括号，不能按第一个 ')' 截断）。"""
        start = self.line_no(r'DataSafetyPaths = @\(')
        self.assertGreater(start, 0, '未找到 $DataSafetyPaths 定义')
        body = []
        for line in self.lines[start:]:
            stripped = line.strip()
            if stripped == ')':
                break
            body.append(line)
        return '\n'.join(body)

    def test_dist_app_data_is_protected(self):
        """dist\PengToolsHub\data（frozen 用户数据）必须由 Join-Path $AppDir 'data' 守护。"""
        block = self._safety_paths_block()
        # 必须是 $AppDir 变量对应的 data 路径，而不是碰巧出现 data 字符串
        self.assertIn("Join-Path $AppDir 'data'", block,
                      '$DataSafetyPaths 未包含 dist\\PengToolsHub\\data 守护项')

    def test_cleanup_targets_have_data_guards(self):
        """被整体 rmdir 的每个程序目录，其 data 子目录必须已进入 $DataSafetyPaths。"""
        block = self._safety_paths_block()
        rmdir_vars = sorted(set(re.findall(
            r'rmdir /s /q `"\$(\w+)`"', self.text)))
        self.assertTrue(rmdir_vars, '未找到整体 rmdir 的程序目录')
        for var in rmdir_vars:
            guard = f"Join-Path ${var} 'data'"
            self.assertIn(guard, block,
                          f'目录 ${var} 会被整体 rmdir，但其 data 守护 {guard} 不在 $DataSafetyPaths')

    def test_safety_order_extended(self):
        """实际顺序：data 检查 < 锁检查 < build_info 写入 < staging 复制 < destructive cleanup。"""
        data_check = self.line_no(r'DataSafetyPaths')
        lock = self.line_no(r'Assert-ReleaseArtifactsUnlocked -Targets')
        build_info = self.line_no(r"import json,sys")
        staging = self.line_no(r"Copy-Item \$src \(Join-Path \$InstallerDir \$name\)")
        cleanup = self.line_no(r"清理旧产物，避免新旧产物混装")
        for name, no in (('data 检查', data_check), ('锁检查', lock),
                         ('build_info 写入', build_info), ('staging 复制', staging),
                         ('cleanup', cleanup)):
            self.assertGreater(no, 0, f'未定位到 {name}')
        self.assertLess(data_check, lock)
        self.assertLess(lock, build_info)
        self.assertLess(build_info, staging)
        self.assertLess(staging, cleanup)

    def test_zip_pre_data_guard(self):
        """Compress-Archive 前必须有 staging data 防御性复检，且位于其之前。"""
        guard = self.line_no(r'zipDataGuard')
        compress = self.line_no(r'Compress-Archive')
        self.assertGreater(guard, 0, '缺少 ZIP 前 data 防御性复检')
        self.assertLess(guard, compress, 'data 复检必须在 Compress-Archive 之前')

    # ---------- WebEngine Locales 守护 ----------

    def test_build_script_enforces_locales_directory_existence(self):
        """构建脚本必须在 localesDir 不存在时直接 throw，而不是跳过。"""
        self.assertIn("-not (Test-Path -LiteralPath $localesDir)", self.text)
        self.assertIn("WebEngine locales directory not found", self.text)

    def test_build_script_defines_keep_locales_contract(self):
        """构建脚本严格限定保留 zh-CN.pak 与 en-US.pak。"""
        self.assertIn("$keepLocales = @('zh-CN.pak', 'en-US.pak')", self.text)

    def test_locales_dir_missing_fails(self):
        """locales 目录缺失必须触发 fail。"""
        import tempfile
        missing_dir = os.path.join(tempfile.gettempdir(), 'non_existent_locales_test_dir')
        with self.assertRaises(RuntimeError) as ctx:
            verify_webengine_locales(missing_dir)
        self.assertIn('directory not found', str(ctx.exception))

    def test_locales_zh_cn_missing_fails(self):
        """缺失 zh-CN.pak 必须触发 fail。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'en-US.pak'), 'wb') as f:
                f.write(b'data')
            with self.assertRaises(RuntimeError) as ctx:
                verify_webengine_locales(tmp)
            self.assertIn('zh-CN.pak', str(ctx.exception))

    def test_locales_en_us_missing_fails(self):
        """缺失 en-US.pak 必须触发 fail。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'zh-CN.pak'), 'wb') as f:
                f.write(b'data')
            with self.assertRaises(RuntimeError) as ctx:
                verify_webengine_locales(tmp)
            self.assertIn('en-US.pak', str(ctx.exception))

    def test_locales_unexpected_locale_remains_fails(self):
        """非 KEEP 语言包残留必须触发 fail。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('zh-CN.pak', 'en-US.pak', 'ja.pak'):
                with open(os.path.join(tmp, name), 'wb') as f:
                    f.write(b'data')
            with self.assertRaises(RuntimeError) as ctx:
                verify_webengine_locales(tmp)
            self.assertIn('unexpected WebEngine locale', str(ctx.exception))
            self.assertIn('ja.pak', str(ctx.exception))

    def test_locales_correct_zh_cn_en_us_passes(self):
        """仅包含 zh-CN.pak 与 en-US.pak 时正常通过。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('zh-CN.pak', 'en-US.pak'):
                with open(os.path.join(tmp, name), 'wb') as f:
                    f.write(b'data')
            remaining = verify_webengine_locales(tmp)
            self.assertEqual(sorted(remaining), ['en-US.pak', 'zh-CN.pak'])


def verify_webengine_locales(locales_dir: str, keep_locales=('zh-CN.pak', 'en-US.pak')) -> list[str]:
    """验证 WebEngine 语言包后置条件：目录存在、KEEP 全部存在、非 KEEP 0 残留。"""
    if not os.path.isdir(locales_dir):
        raise RuntimeError(f"Post-condition failed: WebEngine locales directory not found: {locales_dir}")
    existing = [f for f in os.listdir(locales_dir) if f.endswith('.pak')]
    for k in keep_locales:
        if k not in existing:
            raise RuntimeError(f"Post-condition failed: required WebEngine locale '{k}' is missing in {locales_dir}")
    for r in existing:
        if r not in keep_locales:
            raise RuntimeError(f"Post-condition failed: unexpected WebEngine locale '{r}' remained in {locales_dir}")
    return existing


if __name__ == '__main__':
    unittest.main()
