"""Render the real settings page and exercise its save-domain boundary safely.

The preview uses an offscreen Qt application and patches every external store.
It writes only screenshots and a metrics JSON beside the requested output.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ['PENGTOOLS_DISABLE_MOTION'] = '1'


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--output-dir',
        default=str(ROOT / 'scripts' / 'diagnostics' / 'settings_save_repair_preview'),
    )
    args = parser.parse_args()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    from PyQt6.QtCore import QTime
    from PyQt6.QtGui import QFontDatabase
    from PyQt6.QtWidgets import QApplication, QWidget

    app = QApplication.instance() or QApplication([])
    font_path = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/msyh.ttc'
    if font_path.exists():
        QFontDatabase.addApplicationFont(str(font_path))

    import config
    from panels.settings_panel import SettingsPanel
    from ui.theme_manager import ThemeManager

    ThemeManager.instance().apply(app, 'calm', font_size=12)

    metrics = {
        'contract': {'max_width': 1040, 'category_width': 180, 'category_gap': 24, 'row_min_height': 72},
        'screenshots': [],
    }
    with tempfile.TemporaryDirectory(prefix='pengtools-settings-preview-'):
        # The constructor normally refreshes external stores. Keep this run
        # isolated from the user's data and session while using real widgets.
        with patch.object(SettingsPanel, '_load_ai_local_values'), \
                patch.object(SettingsPanel, '_load_reminder_values'), \
                patch.object(SettingsPanel, '_refresh_oracle_status'):
            panel = SettingsPanel(dict(config.DEFAULT_SETTINGS), 'zh')
        try:
            panel.resize(1040, 740)
            panel.apply_layout_mode('standard', False)
            panel.show()
            for _ in range(3):
                app.processEvents()

            for index in range(panel.sections_stack.count()):
                panel.section_nav.setCurrentRow(index)
                app.processEvents()
                panel.resize(1040, 740)
                app.processEvents()
                filename = output_dir / f'settings_tab_{index}_1040x740.png'
                panel.grab().save(str(filename))
                metrics['screenshots'].append(str(filename))

            workspace = panel.findChild(QWidget, 'settings-workspace')
            rows = panel.findChildren(QWidget, 'settings-form-row')
            metrics['wide_geometry'] = {
                'workspace_width': workspace.width() if workspace is not None else None,
                'workspace_max_width': workspace.maximumWidth() if workspace is not None else None,
                'category_width': panel.section_nav.width(),
                'body_spacing': panel._settings_body_layout.spacing(),
                'row_count': len(rows),
                'row_minimums': [row.minimumHeight() for row in rows],
            }

            panel.resize(856, 458)
            panel.apply_layout_mode('narrow', False)
            panel.section_picker.setCurrentIndex(2)
            app.processEvents()
            narrow_file = output_dir / 'settings_tab_2_856x458.png'
            panel.grab().save(str(narrow_file))
            metrics['screenshots'].append(str(narrow_file))
            workspace = panel.findChild(QWidget, 'settings-workspace')
            rows = panel.findChildren(QWidget, 'settings-form-row')

            # Exercise the delayed general-save callback with in-memory stores.
            captured = {}

            def fake_persist(*, notify_ui=True, show_toast=False, draft=None):
                captured['reminder'] = dict(draft or {})
                return {'enabled': bool((draft or {}).get('enabled')), 'time': str((draft or {}).get('time') or '')}

            def fake_apply(settings):
                captured['settings'] = dict(settings)
                panel._set_settings_save_result(True)
                return True

            panel.settings_changed.connect(fake_apply)
            panel.font_size.setValue(15)
            panel.reminder_enabled.setChecked(False)
            panel.reminder_time.setTime(QTime(8, 15))
            with patch.object(panel, '_persist_reminder_settings', side_effect=fake_persist):
                panel._save()
                # Prove that the next-turn callback uses the click snapshot.
                panel.font_size.setValue(16)
                panel.reminder_time.setTime(QTime(9, 20))
                app.processEvents()
            metrics['save_simulation'] = {
                'saved_font_size': captured['settings']['font_size'],
                'saved_reminder': captured['reminder'],
                'post_click_font_size': panel.font_size.value(),
                'post_click_reminder': panel.reminder_time.time().toString('HH:mm'),
                'feedback_state': panel._save_loading._state,
            }

            metrics['narrow_geometry'] = {
                'workspace_width': workspace.width() if workspace is not None else None,
                'workspace_max_width': workspace.maximumWidth() if workspace is not None else None,
                'category_width': panel.section_nav.width(),
                'body_spacing': panel._settings_body_layout.spacing(),
                'row_count': len(rows),
                'row_minimums': [row.minimumHeight() for row in rows],
            }
        finally:
            panel.close()
            panel.deleteLater()
            app.processEvents()

    metrics_file = output_dir / 'settings_save_repair_metrics.json'
    metrics_file.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'metrics': str(metrics_file), **metrics}, ensure_ascii=False))


if __name__ == '__main__':
    main()
