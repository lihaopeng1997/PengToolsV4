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
