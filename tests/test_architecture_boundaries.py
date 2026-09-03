"""Import-direction guards for the PengToolsHub layered architecture."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


PROJECT_DIR = Path(__file__).resolve().parent.parent
UI_TOOL_PORTS = {
    'tools.code_folding',
    'tools.credit_code',
    'tools.daily_reports',
    'tools.db_connect',
    'tools.db_contracts',
    'tools.id_documents',
    'tools.intranet_llm',
    'tools.json_viewer',
    'tools.list_pin',
    'tools.ops_ssh_shell',
    'tools.personal_knowledge',
    'tools.pinyin_search',
    'tools.terminal_emulator',
    'tools.ticket_submit',
    'tools.vin_generator',
    'tools.xml_formatter',
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_tools_do_not_depend_on_ui_or_panels(self):
        violations = []
        for path in (PROJECT_DIR / 'tools').glob('*.py'):
            for module in _imports(path):
                if module == 'ui' or module.startswith('ui.') or module == 'panels' or module.startswith('panels.'):
                    violations.append(f'{path.name}: {module}')
        self.assertEqual(violations, [])

    def test_ui_uses_only_approved_tool_ports_and_never_panels(self):
        violations = []
        for path in (PROJECT_DIR / 'ui').glob('*.py'):
            for module in _imports(path):
                if module == 'panels' or module.startswith('panels.'):
                    violations.append(f'{path.name}: {module}')
                elif module == 'tools' or module.startswith('tools.'):
                    if module not in UI_TOOL_PORTS:
                        violations.append(f'{path.name}: {module}')
        self.assertEqual(violations, [])


if __name__ == '__main__':
    unittest.main()
