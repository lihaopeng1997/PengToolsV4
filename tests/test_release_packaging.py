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

    # ---------- Qt Translations 守护 ----------

    def test_build_script_enforces_translations_directory_existence(self):
        """构建脚本必须在 transDir 不存在时直接 throw，而不是跳过。"""
        self.assertIn("-not (Test-Path -LiteralPath $transDir)", self.text)
        self.assertIn("Qt translations directory not found", self.text)

    def test_build_script_defines_keep_qm_contract(self):
        """构建脚本严格限定保留 qt_zh_CN.qm 与 qtbase_zh_CN.qm。"""
        self.assertIn("$keepQm = @('qt_zh_CN.qm', 'qtbase_zh_CN.qm')", self.text)

    def test_translations_dir_missing_fails(self):
        """translations 目录缺失必须触发 fail。"""
        import tempfile
        missing_dir = os.path.join(tempfile.gettempdir(), 'non_existent_trans_test_dir')
        with self.assertRaises(RuntimeError) as ctx:
            verify_qt_translations(missing_dir)
        self.assertIn('directory not found', str(ctx.exception))

    def test_translations_missing_required_qm_fails(self):
        """缺失 qtbase_zh_CN.qm 必须触发 fail。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'qt_zh_CN.qm'), 'wb') as f:
                f.write(b'data')
            with self.assertRaises(RuntimeError) as ctx:
                verify_qt_translations(tmp)
            self.assertIn('qtbase_zh_CN.qm', str(ctx.exception))

    def test_translations_unexpected_qm_remains_fails(self):
        """非 KEEP 翻译包残留（如 qt_de.qm）必须触发 fail。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('qt_zh_CN.qm', 'qtbase_zh_CN.qm', 'qt_de.qm'):
                with open(os.path.join(tmp, name), 'wb') as f:
                    f.write(b'data')
            with self.assertRaises(RuntimeError) as ctx:
                verify_qt_translations(tmp)
            self.assertIn('unexpected Qt translation', str(ctx.exception))
            self.assertIn('qt_de.qm', str(ctx.exception))

    def test_translations_correct_keep_passes(self):
        """仅包含 qt_zh_CN.qm 与 qtbase_zh_CN.qm 时正常通过。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('qt_zh_CN.qm', 'qtbase_zh_CN.qm'):
                with open(os.path.join(tmp, name), 'wb') as f:
                    f.write(b'data')
            remaining = verify_qt_translations(tmp)
            self.assertEqual(sorted(remaining), ['qt_zh_CN.qm', 'qtbase_zh_CN.qm'])

    # ---------- Qt Multimedia 守护 ----------

    def test_build_script_prunes_multimedia_runtime(self):
        """构建脚本必须包含 multimediaBinFiles 与 multimediaPluginDir 裁剪逻辑。"""
        self.assertIn("avcodec-61.dll", self.text)
        self.assertIn("Qt6Multimedia.dll", self.text)
        self.assertIn("multimediaPluginDir", self.text)

    def test_build_script_enforces_required_binaries_postcondition(self):
        """构建脚本必须确保 WebEngine 与 QtQuick 核心二进制完好。"""
        self.assertIn("$requiredBinaries = @('Qt6WebEngineCore.dll', 'Qt6WebEngineQuick.dll', 'Qt6Quick.dll', 'Qt6Qml.dll', 'Qt6Widgets.dll', 'Qt6Gui.dll', 'Qt6Core.dll')", self.text)

    def test_multimedia_residual_fails(self):
        """多媒体二进制残留必须触发 fail。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, 'bin')
            plugin_dir = os.path.join(tmp, 'plugins', 'multimedia')
            os.makedirs(bin_dir)
            for req in ('Qt6WebEngineCore.dll', 'Qt6WebEngineQuick.dll', 'Qt6Quick.dll', 'Qt6Qml.dll', 'Qt6Widgets.dll', 'Qt6Gui.dll', 'Qt6Core.dll'):
                with open(os.path.join(bin_dir, req), 'wb') as f:
                    f.write(b'data')
            with open(os.path.join(bin_dir, 'avcodec-61.dll'), 'wb') as f:
                f.write(b'data')
            with self.assertRaises(RuntimeError) as ctx:
                verify_qt_multimedia_pruned(bin_dir, plugin_dir)
            self.assertIn('avcodec-61.dll', str(ctx.exception))

    def test_multimedia_clean_and_required_present_passes(self):
        """多媒体 0 残留且核心二进制存在时正常通过。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, 'bin')
            plugin_dir = os.path.join(tmp, 'plugins', 'multimedia')
            os.makedirs(bin_dir)
            for req in ('Qt6WebEngineCore.dll', 'Qt6WebEngineQuick.dll', 'Qt6Quick.dll', 'Qt6Qml.dll', 'Qt6Widgets.dll', 'Qt6Gui.dll', 'Qt6Core.dll'):
                with open(os.path.join(bin_dir, req), 'wb') as f:
                    f.write(b'data')
            verify_qt_multimedia_pruned(bin_dir, plugin_dir)

    # ---------- Qt 3D / Quick3D 守护 ----------

    def test_build_script_prunes_quick3d_runtime(self):
        """构建脚本必须包含 quick3dBinFiles 与 quick3dPluginDirs 裁剪逻辑。"""
        self.assertIn("Qt6Quick3DRuntimeRender.dll", self.text)
        self.assertIn("Qt6ShaderTools.dll", self.text)
        self.assertIn("quick3dPluginDirs", self.text)

    def test_build_script_enforces_3d_contract_binaries_postcondition(self):
        """构建脚本必须确保 WebEngine、QtQuick、QmlModels、QmlMeta、OpenGL 核心完好。"""
        self.assertIn("Qt6QmlModels.dll", self.text)
        self.assertIn("Qt6QmlMeta.dll", self.text)
        self.assertIn("Qt6OpenGL.dll", self.text)

    def test_quick3d_residual_fails(self):
        """Quick3D 二进制残留必须触发 fail。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, 'bin')
            plugins_dir = os.path.join(tmp, 'plugins')
            os.makedirs(bin_dir)
            os.makedirs(plugins_dir)
            for req in ('Qt6WebEngineCore.dll', 'Qt6WebEngineQuick.dll', 'Qt6Quick.dll', 'Qt6Qml.dll',
                        'Qt6QmlModels.dll', 'Qt6QmlMeta.dll', 'Qt6OpenGL.dll', 'Qt6Widgets.dll',
                        'Qt6Gui.dll', 'Qt6Core.dll'):
                with open(os.path.join(bin_dir, req), 'wb') as f:
                    f.write(b'data')
            with open(os.path.join(bin_dir, 'Qt6Quick3D.dll'), 'wb') as f:
                f.write(b'data')
            with self.assertRaises(RuntimeError) as ctx:
                verify_qt_3d_pruned(bin_dir, plugins_dir)
            self.assertIn('Qt6Quick3D.dll', str(ctx.exception))

    def test_quick3d_clean_and_required_present_passes(self):
        """Quick3D 0 残留且核心二进制完好时正常通过。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, 'bin')
            plugins_dir = os.path.join(tmp, 'plugins')
            os.makedirs(bin_dir)
            os.makedirs(plugins_dir)
            for req in ('Qt6WebEngineCore.dll', 'Qt6WebEngineQuick.dll', 'Qt6Quick.dll', 'Qt6Qml.dll',
                        'Qt6QmlModels.dll', 'Qt6QmlMeta.dll', 'Qt6OpenGL.dll', 'Qt6Widgets.dll',
                        'Qt6Gui.dll', 'Qt6Core.dll'):
                with open(os.path.join(bin_dir, req), 'wb') as f:
                    f.write(b'data')
            verify_qt_3d_pruned(bin_dir, plugins_dir)

    # ---------- Qt Designer + SQL Drivers 守护 ----------

    def test_build_script_prunes_designer_and_sqldrivers(self):
        """构建脚本必须包含 Qt6Designer.dll 与 sqldrivers 目录裁剪逻辑。"""
        self.assertIn("Qt6Designer.dll", self.text)
        self.assertIn("sqldriversPluginDir", self.text)

    def test_designer_residual_fails(self):
        """Qt6Designer.dll 残留必须触发 fail。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, 'bin')
            plugins_dir = os.path.join(tmp, 'plugins')
            os.makedirs(bin_dir)
            os.makedirs(plugins_dir)
            for req in ('Qt6WebEngineCore.dll', 'Qt6WebEngineQuick.dll', 'Qt6Quick.dll', 'Qt6Qml.dll',
                        'Qt6QmlModels.dll', 'Qt6QmlMeta.dll', 'Qt6OpenGL.dll', 'Qt6Widgets.dll',
                        'Qt6Gui.dll', 'Qt6Core.dll'):
                with open(os.path.join(bin_dir, req), 'wb') as f:
                    f.write(b'data')
            with open(os.path.join(bin_dir, 'Qt6Designer.dll'), 'wb') as f:
                f.write(b'data')
            with self.assertRaises(RuntimeError) as ctx:
                verify_qt_designer_sql_pruned(bin_dir, plugins_dir)
            self.assertIn('Qt6Designer.dll', str(ctx.exception))

    def test_sqldrivers_residual_fails(self):
        """sqldrivers 目录残留必须触发 fail。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, 'bin')
            plugins_dir = os.path.join(tmp, 'plugins')
            sqldrivers_dir = os.path.join(plugins_dir, 'sqldrivers')
            os.makedirs(bin_dir)
            os.makedirs(sqldrivers_dir)
            for req in ('Qt6WebEngineCore.dll', 'Qt6WebEngineQuick.dll', 'Qt6Quick.dll', 'Qt6Qml.dll',
                        'Qt6QmlModels.dll', 'Qt6QmlMeta.dll', 'Qt6OpenGL.dll', 'Qt6Widgets.dll',
                        'Qt6Gui.dll', 'Qt6Core.dll'):
                with open(os.path.join(bin_dir, req), 'wb') as f:
                    f.write(b'data')
            with self.assertRaises(RuntimeError) as ctx:
                verify_qt_designer_sql_pruned(bin_dir, plugins_dir)
            self.assertIn('sqldrivers', str(ctx.exception))

    def test_designer_sql_clean_and_required_present_passes(self):
        """Designer 与 sqldrivers 0 残留且核心二进制存在时正常通过。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, 'bin')
            plugins_dir = os.path.join(tmp, 'plugins')
            os.makedirs(bin_dir)
            os.makedirs(plugins_dir)
            for req in ('Qt6WebEngineCore.dll', 'Qt6WebEngineQuick.dll', 'Qt6Quick.dll', 'Qt6Qml.dll',
                        'Qt6QmlModels.dll', 'Qt6QmlMeta.dll', 'Qt6OpenGL.dll', 'Qt6Widgets.dll',
                        'Qt6Gui.dll', 'Qt6Core.dll'):
                with open(os.path.join(bin_dir, req), 'wb') as f:
                    f.write(b'data')
            verify_qt_designer_sql_pruned(bin_dir, plugins_dir)


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


def verify_qt_translations(trans_dir: str, keep_qm=('qt_zh_CN.qm', 'qtbase_zh_CN.qm')) -> list[str]:
    """验证 Qt 翻译包后置条件：目录存在、KEEP 全部存在、非 KEEP 0 残留。"""
    if not os.path.isdir(trans_dir):
        raise RuntimeError(f"Post-condition failed: Qt translations directory not found: {trans_dir}")
    existing = [f for f in os.listdir(trans_dir) if f.endswith('.qm')]
    for k in keep_qm:
        if k not in existing:
            raise RuntimeError(f"Post-condition failed: required Qt translation '{k}' is missing in {trans_dir}")
    for r in existing:
        if r not in keep_qm:
            raise RuntimeError(f"Post-condition failed: unexpected Qt translation '{r}' remained in {trans_dir}")
    return existing


def verify_qt_multimedia_pruned(bin_dir: str, plugin_dir: str,
                                multimedia_files=('avcodec-61.dll', 'avformat-61.dll', 'Qt6Multimedia.dll',
                                                  'avutil-59.dll', 'swscale-8.dll', 'Qt6MultimediaQuick.dll',
                                                  'swresample-5.dll', 'Qt6MultimediaWidgets.dll'),
                                required_binaries=('Qt6WebEngineCore.dll', 'Qt6WebEngineQuick.dll',
                                                   'Qt6Quick.dll', 'Qt6Qml.dll', 'Qt6Widgets.dll',
                                                   'Qt6Gui.dll', 'Qt6Core.dll')) -> None:
    """验证 Qt Multimedia 运行时裁剪后置条件：0 残留且核心二进制存在。"""
    for m in multimedia_files:
        if os.path.exists(os.path.join(bin_dir, m)):
            raise RuntimeError(f"Post-condition failed: multimedia binary '{m}' remained in {bin_dir}")
    if os.path.exists(plugin_dir):
        raise RuntimeError(f"Post-condition failed: multimedia plugin directory remained in {plugin_dir}")
    for req in required_binaries:
        if not os.path.exists(os.path.join(bin_dir, req)):
            raise RuntimeError(f"Post-condition failed: required binary '{req}' is missing in {bin_dir}")


def verify_qt_3d_pruned(bin_dir: str, plugins_dir: str,
                         quick3d_files=('Qt6Quick3DRuntimeRender.dll', 'Qt6ShaderTools.dll',
                                        'Qt6Quick3DPhysics.dll', 'Qt6Quick3DParticles.dll',
                                        'Qt6Quick3D.dll', 'Qt6Quick3DXr.dll', 'Qt6Quick3DHelpers.dll',
                                        'Qt6Quick3DHelpersImpl.dll', 'Qt6Quick3DUtils.dll',
                                        'Qt6Quick3DEffects.dll', 'Qt6Quick3DAssetUtils.dll',
                                        'Qt6Quick3DGlslParser.dll', 'Qt6Quick3DSpatialAudio.dll',
                                        'Qt6Quick3DIblBaker.dll', 'Qt6Quick3DAssetImport.dll',
                                        'Qt6Quick3DPhysicsHelpers.dll'),
                         plugin_dirs=('assetimporters', 'sceneparsers', 'renderers', 'geometryloaders'),
                         required_binaries=('Qt6WebEngineCore.dll', 'Qt6WebEngineQuick.dll',
                                            'Qt6Quick.dll', 'Qt6Qml.dll', 'Qt6QmlModels.dll',
                                            'Qt6QmlMeta.dll', 'Qt6OpenGL.dll', 'Qt6Widgets.dll',
                                            'Qt6Gui.dll', 'Qt6Core.dll')) -> None:
    """验证 Qt 3D / Quick3D 运行时裁剪后置条件：0 残留且核心二进制与 Qml/OpenGL 存在。"""
    for q3d in quick3d_files:
        if os.path.exists(os.path.join(bin_dir, q3d)):
            raise RuntimeError(f"Post-condition failed: Quick3D binary '{q3d}' remained in {bin_dir}")
    for pdir in plugin_dirs:
        if os.path.exists(os.path.join(plugins_dir, pdir)):
            raise RuntimeError(f"Post-condition failed: Quick3D plugin directory '{pdir}' remained in {plugins_dir}")
    for req in required_binaries:
        if not os.path.exists(os.path.join(bin_dir, req)):
            raise RuntimeError(f"Post-condition failed: required binary '{req}' is missing in {bin_dir}")


def verify_qt_designer_sql_pruned(bin_dir: str, plugins_dir: str,
                                   required_binaries=('Qt6WebEngineCore.dll', 'Qt6WebEngineQuick.dll',
                                                      'Qt6Quick.dll', 'Qt6Qml.dll', 'Qt6QmlModels.dll',
                                                      'Qt6QmlMeta.dll', 'Qt6OpenGL.dll', 'Qt6Widgets.dll',
                                                      'Qt6Gui.dll', 'Qt6Core.dll')) -> None:
    """验证 Qt Designer 与 SQL 驱动插件裁剪后置条件：0 残留且核心二进制完好。"""
    if os.path.exists(os.path.join(bin_dir, 'Qt6Designer.dll')):
        raise RuntimeError(f"Post-condition failed: Qt6Designer.dll remained in {bin_dir}")
    if os.path.exists(os.path.join(plugins_dir, 'sqldrivers')):
        raise RuntimeError(f"Post-condition failed: sqldrivers plugin directory remained in {plugins_dir}")
    for req in required_binaries:
        if not os.path.exists(os.path.join(bin_dir, req)):
            raise RuntimeError(f"Post-condition failed: required binary '{req}' is missing in {bin_dir}")


if __name__ == '__main__':
    unittest.main()
