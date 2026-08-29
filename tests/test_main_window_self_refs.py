# -*- coding: utf-8 -*-
"""main_window 自引用回归测试：self 方法调用/引用必须真实存在。

来历：V2 壳改造把 _create_sidebar 改名为 _create_legacy_sidebar 时漏改调用点，
语法检查通过但运行时 AttributeError。本测试用 AST 静态扫描防回归——
任何 self.<name>(...) 调用与 self.<name> 引用，都必须是类中定义的方法，
或实例属性赋值，或 QMainWindow 继承成员。
"""
import ast
import io
import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QMainWindow

SOURCE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'main_window.py')

INHERITED = set(dir(QMainWindow))


def _load_class():
    source = io.open(SOURCE_PATH, encoding='utf-8').read()
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == 'MainWindow':
            return source, node
    raise AssertionError('MainWindow class not found in main_window.py')


class MainWindowSelfReferenceTest(unittest.TestCase):
    def setUp(self):
        self.source, self.cls = _load_class()
        self.methods = {
            node.name for node in self.cls.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        # 类体内直接赋值的属性（self.x = ...）
        self.attributes = set()
        for node in ast.walk(self.cls):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) \
                            and target.value.id == 'self':
                        self.attributes.add(target.attr)
            elif isinstance(node, ast.AnnAssign):
                target = node.target
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) \
                        and target.value.id == 'self':
                    self.attributes.add(target.attr)

    def _self_attr_nodes(self):
        """收集 (attr, node)：self.<attr> 的调用与方法/属性引用。"""
        found = []
        for node in ast.walk(self.cls):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == 'self':
                found.append((node.func.attr, node))
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                    and node.value.id == 'self' and isinstance(node.ctx, ast.Load):
                found.append((node.attr, node))
        return found

    def test_self_method_calls_exist(self):
        missing = sorted({
            name for name, _ in self._self_attr_nodes()
            if name.startswith('_') and name not in self.methods
            and name not in self.attributes and name not in INHERITED
        })
        self.assertEqual(missing, [], 'self.<方法> 引用了不存在的成员（改名漏改？）: %s' % missing)

    def test_renamed_sidebar_helper_consistency(self):
        # 历史事故点：定义与调用必须同名成对
        self.assertIn('_create_legacy_sidebar', self.methods)
        self.assertNotIn('_create_sidebar', self.methods)


if __name__ == '__main__':
    unittest.main()
