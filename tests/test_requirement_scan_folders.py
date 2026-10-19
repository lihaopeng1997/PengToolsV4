# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.svn_workspace import scan_working_copies


class TestRequirementScanFolders(unittest.TestCase):
    def setUp(self):
        self.mock_wc_info = {
            'local_path': '',
            'svn_url': 'svn://mock/repo/trunk',
            'svn_root': 'svn://mock/repo',
            'svn_uuid': 'mock-uuid',
            'svn_revision': '100',
            'last_changed_revision': '99',
        }
        self.mock_status = {
            'clean': True,
            'changes': [],
            'text': '工作副本干净，无本地改动',
            'returncode': 0,
        }

    def _create_file(self, *parts, content='test'):
        path = os.path.join(*parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return path

    def test_root_month_structure(self):
        """原 root 月份结构：root/2026-09/REQ-100，选择 root 识别需求并提取月份。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            self._create_file(temp_dir, '2026-09', 'REQ-100', 'doc.docx')
            self._create_file(temp_dir, '2026-09', 'REQ-101', 'doc.docx')
            self._create_file(temp_dir, '2026-10', 'REQ-102', 'doc.docx')

            copies = scan_working_copies(temp_dir)
            self.assertEqual(len(copies), 3)

            by_rel = {item['relative_path'].replace('\\', '/'): item for item in copies}
            self.assertIn('2026-09/REQ-100', by_rel)
            self.assertIn('2026-09/REQ-101', by_rel)
            self.assertIn('2026-10/REQ-102', by_rel)

            self.assertEqual(by_rel['2026-09/REQ-100']['online_month'], '2026-09')
            self.assertEqual(by_rel['2026-09/REQ-100']['workspace_kind'], 'folder')
            self.assertEqual(by_rel['2026-10/REQ-102']['online_month'], '2026-10')

    def test_direct_month_selection(self):
        """直接选月份目录：base 为 2026-09，识别其下的普通需求目录并保留月份。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            month_dir = os.path.join(temp_dir, '2026-09')
            self._create_file(month_dir, 'REQ-100', 'doc.docx')
            self._create_file(month_dir, 'REQ-101', 'doc.docx')

            copies = scan_working_copies(month_dir)
            self.assertEqual(len(copies), 2)

            by_rel = {item['relative_path'].replace('\\', '/'): item for item in copies}
            self.assertIn('REQ-100', by_rel)
            self.assertIn('REQ-101', by_rel)
            self.assertEqual(by_rel['REQ-100']['online_month'], '2026-09')
            self.assertEqual(by_rel['REQ-101']['online_month'], '2026-09')
            self.assertEqual(by_rel['REQ-100']['workspace_kind'], 'folder')

    def test_single_requirement_with_files_and_attachments(self):
        """单个需求含直接文件+附件子目录：只导入自己为1条，不重复导入附件目录。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            req_dir = os.path.join(temp_dir, 'REQ-100')
            self._create_file(req_dir, '需求说明.docx')
            self._create_file(req_dir, 'attachments', 'screenshot.png')
            self._create_file(req_dir, 'attachments', 'sub', 'detail.txt')
            self._create_file(req_dir, 'sql', 'query.sql')

            copies = scan_working_copies(req_dir)
            self.assertEqual(len(copies), 1)
            item = copies[0]
            self.assertEqual(item['workspace_kind'], 'folder')
            self.assertEqual(item['file_count'], 4)
            self.assertEqual(item['relative_path'], 'REQ-100')

    def test_plain_root_without_month(self):
        """无月份 root 的需求直子目录：有限层级识别，月份未知为空而非伪造。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            self._create_file(temp_dir, 'REQ-100', 'spec.docx')
            self._create_file(temp_dir, 'REQ-101', 'spec.docx')

            copies = scan_working_copies(temp_dir)
            self.assertEqual(len(copies), 2)
            by_rel = {item['relative_path'].replace('\\', '/'): item for item in copies}
            self.assertIn('REQ-100', by_rel)
            self.assertIn('REQ-101', by_rel)
            self.assertEqual(by_rel['REQ-100']['online_month'], '')
            self.assertEqual(by_rel['REQ-101']['online_month'], '')

    def test_attachment_subfolders_not_duplicated_under_month(self):
        """月份目录下需求包含多层附件与代码子目录：只导入该需求，不递归拆分子目录。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            self._create_file(temp_dir, '2026-09', 'REQ-200', 'spec.docx')
            self._create_file(temp_dir, '2026-09', 'REQ-200', 'attachments', 'img.png')
            self._create_file(temp_dir, '2026-09', 'REQ-200', 'docs', 'manual.pdf')
            self._create_file(temp_dir, '2026-09', 'REQ-200', 'code', 'demo.py')

            copies = scan_working_copies(temp_dir)
            self.assertEqual(len(copies), 1)
            item = copies[0]
            self.assertEqual(item['relative_path'].replace('\\', '/'), '2026-09/REQ-200')
            self.assertEqual(item['file_count'], 4)
            self.assertEqual(item['online_month'], '2026-09')

    def test_empty_directory(self):
        """空目录或全空子目录给明确无结果。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertEqual(scan_working_copies(temp_dir), [])

            # 含有空子目录也无结果
            os.makedirs(os.path.join(temp_dir, 'empty_sub'), exist_ok=True)
            os.makedirs(os.path.join(temp_dir, '2026-09', 'empty_req'), exist_ok=True)
            self.assertEqual(scan_working_copies(temp_dir), [])

    def test_ignored_directories_and_files(self):
        """忽略 .git/.svn/node_modules/__pycache__/build/dist 及 ~$ 临时文件，不导入不污染统计。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            req_dir = os.path.join(temp_dir, 'REQ-300')
            self._create_file(req_dir, 'main.py')
            self._create_file(req_dir, '.git', 'config')
            self._create_file(req_dir, '.svn', 'entries')
            self._create_file(req_dir, 'node_modules', 'pkg', 'index.js')
            self._create_file(req_dir, '__pycache__', 'main.cpython-312.pyc')
            self._create_file(req_dir, 'build', 'output.bin')
            self._create_file(req_dir, 'dist', 'app.zip')
            self._create_file(req_dir, '~$temp_word.docx')

            # 根目录下也有被忽略的构建目录
            self._create_file(temp_dir, 'build', 'top_build.bin')
            self._create_file(temp_dir, 'dist', 'top_dist.zip')

            copies = scan_working_copies(temp_dir)
            self.assertEqual(len(copies), 1)
            item = copies[0]
            self.assertEqual(item['relative_path'], 'REQ-300')
            self.assertEqual(item['file_count'], 1)  # 仅 main.py

    @patch('tools.svn_workspace.working_copy_info')
    @patch('tools.svn_workspace.svn_status')
    def test_svn_ancestor_descendant_deduplication(self, mock_status_fn, mock_info_fn):
        """SVN 副本及祖孙排重：外层工作副本识别后，内层嵌套工作副本不重复计入。"""
        mock_status_fn.return_value = self.mock_status

        def _fake_info(path):
            info = dict(self.mock_wc_info)
            info['local_path'] = os.path.abspath(path)
            return info

        mock_info_fn.side_effect = _fake_info

        with tempfile.TemporaryDirectory() as temp_dir:
            wc_dir = os.path.join(temp_dir, '2026-09', 'REQ-SVN')
            self._create_file(wc_dir, '.svn', 'format')
            self._create_file(wc_dir, 'spec.docx')

            # 嵌套的子工作副本
            nested_wc = os.path.join(wc_dir, 'nested_sub_repo')
            self._create_file(nested_wc, '.svn', 'format')
            self._create_file(nested_wc, 'sub.txt')

            copies = scan_working_copies(temp_dir)
            # 祖孙排重，仅应识别出顶层 SVN 工作副本
            svn_copies = [c for c in copies if c['workspace_kind'] == 'svn']
            self.assertEqual(len(svn_copies), 1)
            self.assertEqual(svn_copies[0]['relative_path'].replace('\\', '/'), '2026-09/REQ-SVN')

    @patch('tools.svn_workspace.working_copy_info')
    @patch('tools.svn_workspace.svn_status')
    def test_svn_and_folder_deduplication(self, mock_status_fn, mock_info_fn):
        """SVN 副本与普通文件夹排重：已作为 SVN 导入的目录，不会被作为普通文件夹重复导入。"""
        mock_status_fn.return_value = self.mock_status

        def _fake_info(path):
            info = dict(self.mock_wc_info)
            info['local_path'] = os.path.abspath(path)
            return info

        mock_info_fn.side_effect = _fake_info

        with tempfile.TemporaryDirectory() as temp_dir:
            wc_dir = os.path.join(temp_dir, '2026-09', 'REQ-SVN')
            self._create_file(wc_dir, '.svn', 'format')
            self._create_file(wc_dir, 'spec.docx')

            copies = scan_working_copies(temp_dir)
            self.assertEqual(len(copies), 1)
            self.assertEqual(copies[0]['workspace_kind'], 'svn')

    def test_month_dir_with_direct_files_and_no_subdirs(self):
        """月份目录下直接放文件无子目录：base 也可作为候选。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            month_dir = os.path.join(temp_dir, '2026-11')
            self._create_file(month_dir, '月度汇总需求.docx')

            copies = scan_working_copies(month_dir)
            self.assertEqual(len(copies), 1)
            self.assertEqual(copies[0]['online_month'], '2026-11')
            self.assertEqual(copies[0]['file_count'], 1)

    def test_metadata_keys_and_sorting(self):
        """现有元数据键完整性与排序规则保持。"""
        expected_keys = {
            'local_path', 'relative_path', 'svn_url', 'svn_revision',
            'online_month', 'record_kind', 'svn_status', 'changed_count',
            'workspace_kind', 'file_count', 'source_modified_at',
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            self._create_file(temp_dir, '2026-08', 'REQ-01', 'file.txt')
            self._create_file(temp_dir, '2026-09', 'REQ-02', 'file.txt')

            copies = scan_working_copies(temp_dir)
            self.assertEqual(len(copies), 2)
            for item in copies:
                self.assertTrue(expected_keys.issubset(item.keys()))

            # 验证按 (online_month, source_modified_at, relative_path) 倒序
            self.assertGreaterEqual(copies[0]['online_month'], copies[1]['online_month'])

    def test_root_with_readme_and_month_structure(self):
        """root 即使包含 README 或索引文件，也优先保留 root/月份/REQ 布局，不将整个 root 当作单个需求。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            self._create_file(temp_dir, 'README.md', content='项目说明')
            self._create_file(temp_dir, 'index.xlsx', content='索引')
            self._create_file(temp_dir, '2026-09', 'REQ-100', 'spec.docx')
            self._create_file(temp_dir, '2026-09', 'REQ-101', 'spec.docx')

            copies = scan_working_copies(temp_dir)
            self.assertEqual(len(copies), 2)
            by_rel = {item['relative_path'].replace('\\', '/'): item for item in copies}
            self.assertIn('2026-09/REQ-100', by_rel)
            self.assertIn('2026-09/REQ-101', by_rel)
            self.assertEqual(by_rel['2026-09/REQ-100']['online_month'], '2026-09')

    def test_direct_month_with_index_file_and_multiple_reqs(self):
        """直接选月份目录若包含索引文件和多个 REQ 子目录，优先保留多个 REQ 子目录。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            month_dir = os.path.join(temp_dir, '2026-09')
            self._create_file(month_dir, 'index.xlsx', content='月度台账索引')
            self._create_file(month_dir, 'README.txt', content='说明')
            self._create_file(month_dir, 'REQ-100', 'spec.docx')
            self._create_file(month_dir, 'REQ-101', 'spec.docx')

            copies = scan_working_copies(month_dir)
            self.assertEqual(len(copies), 2)
            by_rel = {item['relative_path'].replace('\\', '/'): item for item in copies}
            self.assertIn('REQ-100', by_rel)
            self.assertIn('REQ-101', by_rel)
            self.assertEqual(by_rel['REQ-100']['online_month'], '2026-09')

    def test_selected_ignored_root_returns_empty(self):
        """base 本身是 .git/.svn/node_modules/__pycache__/build/dist 时返回空，不导入其直接文件。"""
        for ignored_name in ('.git', '.svn', 'node_modules', '__pycache__', 'build', 'dist'):
            with tempfile.TemporaryDirectory() as temp_dir:
                target_dir = os.path.join(temp_dir, ignored_name)
                self._create_file(target_dir, 'output.bin')
                self._create_file(target_dir, 'sub', 'nested.txt')

                copies = scan_working_copies(target_dir)
                self.assertEqual(copies, [], f'{ignored_name} 作为 base 时应返回空列表')


if __name__ == '__main__':
    unittest.main()
