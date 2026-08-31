"""Shared database connection labels and default ports without I/O dependencies."""

from __future__ import annotations


DIALECTS = (
    ('oracle', 'Oracle'),
    ('mysql', 'MySQL'),
    ('oceanbase', 'OceanBase'),
    ('dameng', '达梦'),
    ('redis', 'Redis'),
    ('mongodb', 'MongoDB'),
)

DEFAULT_PORTS = {
    'oracle': 1521,
    'oceanbase': 2883,
    'mysql': 3306,
    'dameng': 5236,
    'redis': 6379,
    'mongodb': 27017,
}


def normalize_oceanbase_mode(mode: str | None) -> str:
    """OceanBase 兼容模式规约：仅 explicit 'mysql' 判定为 MySQL 兼容模式；
    历史遗留配置（'standalone', 'cluster', '', None 等）一律保持 Oracle 兼容模式。
    """
    m = str(mode or '').strip().lower()
    if m == 'mysql':
        return 'mysql'
    return 'oracle'
