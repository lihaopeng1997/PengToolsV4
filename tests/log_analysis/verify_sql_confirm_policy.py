"""Intranet mode skips SQL confirmation only for one proven read-only statement."""
import os
import sys
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ssh_session'))
import _isolation  # noqa: E402,F401  (redirect HOME/config before imports)

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'{("PASS" if cond else "FAIL")}  {name}' + (f'  [{detail}]' if detail and not cond else ''))


from ui.data_center.manual_query import intranet_may_skip_sql_confirm

check('SELECT 免确认', intranet_may_skip_sql_confirm('SELECT 1 FROM dual'))
check('WITH 只读免确认', intranet_may_skip_sql_confirm(
    'WITH c AS (SELECT 1 AS n) SELECT n FROM c'))
check('SHOW 免确认', intranet_may_skip_sql_confirm('SHOW TABLES'))
check('DESC 免确认', intranet_may_skip_sql_confirm('DESC t'))
check('EXPLAIN 免确认', intranet_may_skip_sql_confirm('EXPLAIN SELECT 1 FROM dual'))
check('INSERT 需要确认', not intranet_may_skip_sql_confirm('INSERT INTO t (id) VALUES (1)'))
check('UPDATE 需要确认', not intranet_may_skip_sql_confirm('UPDATE t SET id = 1'))
check('DELETE 需要确认', not intranet_may_skip_sql_confirm('DELETE FROM t'))
check('MERGE 需要确认', not intranet_may_skip_sql_confirm(
    'MERGE INTO t USING s ON (t.id = s.id) WHEN MATCHED THEN UPDATE SET t.id = s.id'))
check('CREATE 需要确认', not intranet_may_skip_sql_confirm('CREATE TABLE t (id INT)'))
check('ALTER 需要确认', not intranet_may_skip_sql_confirm('ALTER TABLE t ADD c INT'))
check('DROP 需要确认', not intranet_may_skip_sql_confirm('DROP TABLE t'))
check('TRUNCATE 需要确认', not intranet_may_skip_sql_confirm('TRUNCATE TABLE t'))
check('GRANT 需要确认', not intranet_may_skip_sql_confirm('GRANT SELECT ON t TO reader'))
check('多语句需要确认', not intranet_may_skip_sql_confirm('SELECT 1 FROM dual; DELETE FROM t'))
check('WITH 写入需要确认', not intranet_may_skip_sql_confirm(
    'WITH c AS (SELECT 1 AS n) INSERT INTO t SELECT n FROM c'))
check('过程块需要确认', not intranet_may_skip_sql_confirm('BEGIN NULL; END;'))
check('空语句需要确认', not intranet_may_skip_sql_confirm(''))
check('无法识别需要确认', not intranet_may_skip_sql_confirm('not a statement'))

print()
try:
    _isolation.assert_untouched()
    check('未触碰真实数据目录与用户配置', True)
except AssertionError as exc:
    check('未触碰真实数据目录与用户配置', False, str(exc))

print(f'RESULT: {len(PASS)} passed, {len(FAIL)} failed')
if FAIL:
    print('FAILED:', FAIL)
sys.exit(1 if FAIL else 0)
