# -*- coding: utf-8 -*-
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from tools.dialog_paths import get_dialog_save_path, get_dialog_start_dir, remember_dialog_path


class DialogPathsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix='pengtools_dialog_test_')
        self.choices_store = {}

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _mock_load_last_choices(self):
        return dict(self.choices_store)

    def _mock_update_last_choices(self, **sections):
        for key, value in sections.items():
            if isinstance(value, dict) and isinstance(self.choices_store.get(key), dict):
                merged = dict(self.choices_store[key])
                merged.update(value)
                self.choices_store[key] = merged
            else:
                self.choices_store[key] = value
        return self.choices_store

    def test_first_use_returns_fallback_or_home(self):
        with patch('config.load_last_choices', side_effect=self._mock_load_last_choices), \
             patch('config.update_last_choices', side_effect=self._mock_update_last_choices):
            fallback_dir = os.path.join(self.temp_dir, 'my_fallback')
            os.makedirs(fallback_dir, exist_ok=True)

            start = get_dialog_start_dir('test_purpose_1', fallback=fallback_dir)
            self.assertEqual(os.path.abspath(start), os.path.abspath(fallback_dir))

    def test_successful_choice_is_persisted_and_returned_next_time(self):
        with patch('config.load_last_choices', side_effect=self._mock_load_last_choices), \
             patch('config.update_last_choices', side_effect=self._mock_update_last_choices):
            selected_file = os.path.join(self.temp_dir, 'subfolder', 'test.docx')
            os.makedirs(os.path.dirname(selected_file), exist_ok=True)
            with open(selected_file, 'w', encoding='utf-8') as f:
                f.write('dummy')

            # 首次打开，无历史
            self.assertNotEqual(get_dialog_start_dir('docx_doc'), os.path.dirname(selected_file))

            # 用户成功选择文件
            saved = remember_dialog_path('docx_doc', selected_file, is_directory=False)
            self.assertEqual(saved, os.path.abspath(os.path.dirname(selected_file)))

            # 下次再次打开
            next_start = get_dialog_start_dir('docx_doc')
            self.assertEqual(next_start, os.path.abspath(os.path.dirname(selected_file)))

    def test_cancel_does_not_overwrite_existing_record(self):
        with patch('config.load_last_choices', side_effect=self._mock_load_last_choices), \
             patch('config.update_last_choices', side_effect=self._mock_update_last_choices):
            valid_dir = os.path.join(self.temp_dir, 'persisted_folder')
            os.makedirs(valid_dir, exist_ok=True)

            # 先持久化一条记录
            remember_dialog_path('requirement_import', valid_dir, is_directory=True)
            self.assertEqual(get_dialog_start_dir('requirement_import'), os.path.abspath(valid_dir))

            # 用户 Cancel（空字符串 / None / 空列表）
            remember_dialog_path('requirement_import', '')
            remember_dialog_path('requirement_import', None)
            remember_dialog_path('requirement_import', [])

            # 历史记录保持不变
            self.assertEqual(get_dialog_start_dir('requirement_import'), os.path.abspath(valid_dir))

    def test_different_purposes_are_strictly_isolated(self):
        with patch('config.load_last_choices', side_effect=self._mock_load_last_choices), \
             patch('config.update_last_choices', side_effect=self._mock_update_last_choices):
            dir_a = os.path.join(self.temp_dir, 'dir_a')
            dir_b = os.path.join(self.temp_dir, 'dir_b')
            os.makedirs(dir_a, exist_ok=True)
            os.makedirs(dir_b, exist_ok=True)

            remember_dialog_path('purpose_a', dir_a, is_directory=True)
            remember_dialog_path('purpose_b', dir_b, is_directory=True)

            self.assertEqual(get_dialog_start_dir('purpose_a'), os.path.abspath(dir_a))
            self.assertEqual(get_dialog_start_dir('purpose_b'), os.path.abspath(dir_b))

    def test_invalid_historical_directory_falls_back_safely(self):
        with patch('config.load_last_choices', side_effect=self._mock_load_last_choices), \
             patch('config.update_last_choices', side_effect=self._mock_update_last_choices):
            missing_dir = os.path.join(self.temp_dir, 'deleted_folder')
            fallback_dir = os.path.join(self.temp_dir, 'valid_fallback')
            os.makedirs(fallback_dir, exist_ok=True)

            # 写入不存在的目录到持久化
            self.choices_store = {'dialog_paths': {'test_missing': missing_dir}}

            # 应自动 fallback 到有效 fallback_dir
            start = get_dialog_start_dir('test_missing', fallback=fallback_dir)
            self.assertEqual(start, os.path.abspath(fallback_dir))

    def test_file_selection_remembers_parent_directory(self):
        with patch('config.load_last_choices', side_effect=self._mock_load_last_choices), \
             patch('config.update_last_choices', side_effect=self._mock_update_last_choices):
            doc_file = os.path.join(self.temp_dir, 'docs', 'report.docx')
            os.makedirs(os.path.dirname(doc_file), exist_ok=True)
            with open(doc_file, 'w') as f:
                f.write('content')

            saved = remember_dialog_path('file_purpose', doc_file, is_directory=False)
            self.assertEqual(saved, os.path.abspath(os.path.dirname(doc_file)))

    def test_folder_selection_remembers_folder_itself(self):
        with patch('config.load_last_choices', side_effect=self._mock_load_last_choices), \
             patch('config.update_last_choices', side_effect=self._mock_update_last_choices):
            target_dir = os.path.join(self.temp_dir, 'my_workspace')
            os.makedirs(target_dir, exist_ok=True)

            saved = remember_dialog_path('folder_purpose', target_dir, is_directory=True)
            self.assertEqual(saved, os.path.abspath(target_dir))

    def test_save_dialog_path_combines_default_filename(self):
        with patch('config.load_last_choices', side_effect=self._mock_load_last_choices), \
             patch('config.update_last_choices', side_effect=self._mock_update_last_choices):
            target_dir = os.path.join(self.temp_dir, 'exports')
            os.makedirs(target_dir, exist_ok=True)
            remember_dialog_path('export_key', target_dir, is_directory=True)

            save_path = get_dialog_save_path('export_key', 'my_report.xlsx')
            self.assertEqual(save_path, os.path.join(os.path.abspath(target_dir), 'my_report.xlsx'))


if __name__ == '__main__':
    unittest.main()
