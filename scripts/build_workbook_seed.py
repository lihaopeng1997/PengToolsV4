# -*- coding: utf-8 -*-
"""Build the private structured Excel seed without storing its password."""
import argparse
import json
import os
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from tools.personal_knowledge import extract_workbook_entries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('workbooks', nargs='+')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    password = os.environ.get('PENGTOOLS_SEED_PASSWORD')
    if not password:
        raise SystemExit('PENGTOOLS_SEED_PASSWORD is required')
    entries = []
    for workbook in args.workbooks:
        entries.extend(extract_workbook_entries(workbook, password, builtin=True))
    with open(args.output, 'w', encoding='utf-8') as stream:
        json.dump(entries, stream, ensure_ascii=False, separators=(',', ':'))
    print(f'Created {len(entries)} worksheet entries.')


if __name__ == '__main__':
    main()
