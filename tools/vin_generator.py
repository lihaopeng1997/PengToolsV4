# -*- coding: utf-8 -*-
"""离线测试用中国机动车模拟数据。

VIN 按 GB 16735 / ISO 3779（17 位、不含 I/O/Q、第 9 位校验）。
号牌按 GA 36：普通小型汽车 7 位，小型新能源 8 位（D 纯电 / F 非纯电）。
车辆大类/种类取机动车登记常用分类，仅用于测试，不落盘。
"""
from __future__ import annotations

import datetime
import random
import re

VIN_CHARS = '0123456789ABCDEFGHJKLMNPRSTUVWXYZ'
TRANSLITERATION = {
    **{str(i): i for i in range(10)},
    **dict(zip('ABCDEFGH', range(1, 9))),
    **dict(zip('JKLMN', range(1, 6))),
    **dict(zip('PR', range(7, 10))),
    **dict(zip('STUVWXYZ', range(2, 10))),
}
WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)
YEAR_CODES = '123456789ABCDEFGHJKLMNPRSTVWXY'
CHINA_WMIS = ('LHG', 'LSV', 'LFV', 'LGB', 'LVG', 'LDC', 'LZW', 'LVS', 'LBE', 'L6T')

# 号牌序号不含 I、O；发牌机关字母同样避开 I/O
PLATE_LETTERS = 'ABCDEFGHJKLMNPQRSTUVWXYZ'
PLATE_SERIAL = '0123456789ABCDEFGHJKLMNPQRSTUVWXYZ'
PROVINCE_PREFIXES = (
    '京', '津', '沪', '渝', '冀', '豫', '云', '辽', '黑', '湘',
    '皖', '鲁', '新', '苏', '浙', '赣', '鄂', '桂', '甘', '晋',
    '蒙', '陕', '吉', '闽', '贵', '粤', '川', '青', '藏', '琼', '宁',
)

# WMI → 品牌与常见在产车型（测试用，非完整目录）
WMI_PROFILES = {
    'LSV': {
        'brand': '上汽大众',
        'models': (
            ('帕萨特 330TSI', 'SVW7201CPD', '汽油', '载客汽车', '小型轿车'),
            ('途观 L 330TSI', 'SVW6474CPD', '汽油', '载客汽车', '小型普通客车'),
            ('ID.4 X', 'SVW7000BEV', '纯电', '载客汽车', '小型轿车'),
        ),
    },
    'LFV': {
        'brand': '一汽-大众',
        'models': (
            ('迈腾 330TSI', 'FV7201BBABG', '汽油', '载客汽车', '小型轿车'),
            ('探岳 330TSI', 'FV6470BBABG', '汽油', '载客汽车', '小型普通客车'),
            ('ID.4 CROZZ', 'FV7000BEV', '纯电', '载客汽车', '小型轿车'),
        ),
    },
    'LHG': {
        'brand': '广汽本田',
        'models': (
            ('雅阁 260TURBO', 'HG7241AAC', '汽油', '载客汽车', '小型轿车'),
            ('皓影 240TURBO', 'HG6471AAC', '汽油', '载客汽车', '小型普通客车'),
            ('e:NP2', 'HG7000BEV', '纯电', '载客汽车', '小型轿车'),
        ),
    },
    'LGB': {
        'brand': '东风日产',
        'models': (
            ('天籁 2.0T', 'DFL7201VAL7', '汽油', '载客汽车', '小型轿车'),
            ('奇骏 2.0L', 'DFL6470VAL2', '汽油', '载客汽车', '小型普通客车'),
            ('Ariya', 'DFL7000BEV', '纯电', '载客汽车', '小型轿车'),
        ),
    },
    'LVG': {
        'brand': '一汽丰田',
        'models': (
            ('凯美瑞 双擎', 'TV7251GL-GHEV', '油电混动', '载客汽车', '小型轿车'),
            ('荣放 2.0L', 'TV6470GL', '汽油', '载客汽车', '小型普通客车'),
            ('bZ4X', 'TV7000BEV', '纯电', '载客汽车', '小型轿车'),
        ),
    },
    'LDC': {
        'brand': '东风标致',
        'models': (
            ('标致 408 1.6T', 'DC7164LSY', '汽油', '载客汽车', '小型轿车'),
            ('标致 4008 1.6T', 'DC6466LSY', '汽油', '载客汽车', '小型普通客车'),
        ),
    },
    'LZW': {
        'brand': '上汽通用五菱',
        'models': (
            ('宏光 S 1.5L', 'LZW6432QY', '汽油', '载客汽车', '小型普通客车'),
            ('宏光 MINI EV', 'LZW7000BEV', '纯电', '载客汽车', '小型轿车'),
            ('荣光小卡 1.5L', 'LZW1029PSY', '汽油', '载货汽车', '轻型栏板货车'),
        ),
    },
    'LVS': {
        'brand': '长安福特',
        'models': (
            ('蒙迪欧 1.5T', 'CAF7201A6', '汽油', '载客汽车', '小型轿车'),
            ('锐界 2.0T', 'CAF6470A5', '汽油', '载客汽车', '小型普通客车'),
            ('Mustang Mach-E', 'CAF7000BEV', '纯电', '载客汽车', '小型轿车'),
        ),
    },
    'LBE': {
        'brand': '比亚迪',
        'models': (
            ('汉 EV', 'QCJ7000BEV', '纯电', '载客汽车', '小型轿车'),
            ('宋 PLUS DM-i', 'QCJ6460SHEV', '插电混动', '载客汽车', '小型普通客车'),
            ('秦 PLUS DM-i', 'QCJ7150SHEV', '插电混动', '载客汽车', '小型轿车'),
        ),
    },
    'L6T': {
        'brand': '特斯拉',
        'models': (
            ('Model 3 RWD', 'TSL7000BEV', '纯电', '载客汽车', '小型轿车'),
            ('Model Y RWD', 'TSL7001BEV', '纯电', '载客汽车', '小型普通客车'),
        ),
    },
}

VEHICLE_COLUMNS = (
    'index', 'plate', 'vin', 'model', 'energy', 'engine_no',
    'category', 'kind', 'first_reg', 'valid',
)
VEHICLE_HEADERS_ZH = (
    '序号', '车牌号', 'VIN', '车辆型号', '能源', '发动机号',
    '车辆大类', '车辆种类', '初登日期', '校验',
)
VEHICLE_HEADERS_EN = (
    '#', 'Plate', 'VIN', 'Model', 'Energy', 'Engine No.',
    'Category', 'Kind', 'First registered', 'Valid',
)


def calculate_check_digit(first17):
    if len(first17) != 17 or any(ch not in TRANSLITERATION for ch in first17):
        raise ValueError('VIN must contain 17 valid characters')
    remainder = sum(TRANSLITERATION[ch] * weight for ch, weight in zip(first17, WEIGHTS)) % 11
    return 'X' if remainder == 10 else str(remainder)


def validate_vin(vin):
    vin = (vin or '').strip().upper()
    return (
        len(vin) == 17
        and vin.startswith('L')
        and all(ch in TRANSLITERATION for ch in vin)
        and vin[8] == calculate_check_digit(vin)
    )


def validate_plate(plate, energy=''):
    text = str(plate or '').strip().upper().replace('·', '')
    if energy == '纯电':
        return bool(re.fullmatch(r'[\u4e00-\u9fff][A-HJ-NP-Z]D\d{5}', text))
    if energy == '插电混动':
        return bool(re.fullmatch(r'[\u4e00-\u9fff][A-HJ-NP-Z]F\d{5}', text))
    return bool(re.fullmatch(r'[\u4e00-\u9fff][A-HJ-NP-Z][0-9A-HJ-NP-Z]{5}', text))


def _year_code(year):
    if not 2001 <= year <= 2030:
        raise ValueError('Model year must be between 2001 and 2030')
    return YEAR_CODES[year - 2001]


def generate_vin(year=2026, wmi=''):
    rng = random.SystemRandom()
    selected_wmi = wmi if wmi in CHINA_WMIS else rng.choice(CHINA_WMIS)
    vds = ''.join(rng.choice(VIN_CHARS) for _ in range(5))
    plant = rng.choice(VIN_CHARS)
    serial = ''.join(rng.choice('0123456789') for _ in range(6))
    vin = selected_wmi + vds + '0' + _year_code(year) + plant + serial
    return vin[:8] + calculate_check_digit(vin) + vin[9:]


def generate_vin_batch(count=10, year=2026, wmi=''):
    vins = set()
    while len(vins) < count:
        vins.add(generate_vin(year, wmi))
    return sorted(vins)


class VehicleFilterError(ValueError):
    """指定条件与车型库无交集。"""


def list_energy_options():
    return tuple(sorted({item[2] for profile in WMI_PROFILES.values() for item in profile['models']}))


def list_category_options():
    return tuple(sorted({item[3] for profile in WMI_PROFILES.values() for item in profile['models']}))


def list_kind_options(category=''):
    wanted = str(category or '').strip()
    return tuple(sorted({
        item[4]
        for profile in WMI_PROFILES.values()
        for item in profile['models']
        if not wanted or item[3] == wanted
    }))


def matching_models(wmi='', energy='', category='', kind=''):
    wanted_wmi = str(wmi or '').strip()
    wanted_energy = str(energy or '').strip()
    wanted_category = str(category or '').strip()
    wanted_kind = str(kind or '').strip()
    hits = []
    for code, profile in WMI_PROFILES.items():
        if wanted_wmi and code != wanted_wmi:
            continue
        for model in profile['models']:
            _name, _code, model_energy, model_category, model_kind = model
            if wanted_energy and model_energy != wanted_energy:
                continue
            if wanted_category and model_category != wanted_category:
                continue
            if wanted_kind and model_kind != wanted_kind:
                continue
            hits.append((code, profile['brand'], model))
    return hits


def _pick_profile(wmi, rng, *, energy='', category='', kind=''):
    hits = matching_models(wmi, energy, category, kind)
    if not hits:
        raise VehicleFilterError('没有同时满足这些条件的车型，请放宽条件')
    return rng.choice(hits)


def _generate_plate(energy, rng, used, province=''):
    prefix = province if province in PROVINCE_PREFIXES else ''
    for _ in range(80):
        chosen = prefix or rng.choice(PROVINCE_PREFIXES)
        office = rng.choice(PLATE_LETTERS)
        if energy == '纯电':
            plate = f'{chosen}{office}D{rng.randint(0, 99999):05d}'
        elif energy == '插电混动':
            plate = f'{chosen}{office}F{rng.randint(0, 99999):05d}'
        else:
            serial = ''.join(rng.choice(PLATE_SERIAL) for _ in range(5))
            plate = f'{chosen}{office}{serial}'
        if plate not in used and validate_plate(plate, energy):
            used.add(plate)
            return plate
    raise RuntimeError('unable to allocate unique plate')


def _generate_engine_no(energy, rng):
    if energy == '纯电':
        return 'TZ' + rng.choice(('180', '200', '220')) + 'XSA' + f'{rng.randint(0, 999999):06d}'
    if energy == '柴油':
        return 'D4D' + ''.join(rng.choice('0123456789ABCDEFGHJKLMNPRSTUVWXYZ') for _ in range(8))
    prefix = rng.choice(('4G15S', 'EA888', '4B11', 'GW4B15'))
    return prefix + ''.join(rng.choice('0123456789ABCDEFGHJKLMNPRSTUVWXYZ') for _ in range(7))


def _first_reg_date(year, rng, today=None):
    today = today or datetime.date.today()
    start = datetime.date(int(year), 1, 1)
    if start > today:
        return start.isoformat()
    end = min(today, datetime.date(min(int(year) + 1, today.year), 12, 31))
    if end < start:
        end = today
    span = (end - start).days
    return (start + datetime.timedelta(days=rng.randint(0, max(span, 0)))).isoformat()


def generate_vehicle_record(
    year=2026, wmi='', *, energy='', category='', kind='', plate_province='',
    used_vins=None, used_plates=None,
):
    rng = random.SystemRandom()
    used_vins = used_vins if used_vins is not None else set()
    used_plates = used_plates if used_plates is not None else set()
    selected_wmi, brand, model = _pick_profile(
        wmi, rng, energy=energy, category=category, kind=kind,
    )
    name, model_code, energy, category, kind = model
    vin = generate_vin(year, selected_wmi)
    while vin in used_vins:
        vin = generate_vin(year, selected_wmi)
    used_vins.add(vin)
    plate = _generate_plate(energy, rng, used_plates, plate_province)
    return {
        'vin': vin,
        'wmi': selected_wmi,
        'plate': plate,
        'brand': brand,
        'model': f'{brand} {name}',
        'model_code': model_code,
        'energy': energy,
        'engine_no': _generate_engine_no(energy, rng),
        'category': category,
        'kind': kind,
        'first_reg': _first_reg_date(year, rng),
        'year_code': vin[9],
        'valid': validate_vin(vin),
    }


def generate_vehicle_batch(
    count=10, year=2026, wmi='', *, energy='', category='', kind='', plate_province='',
):
    used_vins = set()
    used_plates = set()
    rows = []
    for _ in range(max(1, int(count))):
        rows.append(generate_vehicle_record(
            year, wmi, energy=energy, category=category, kind=kind,
            plate_province=plate_province, used_vins=used_vins, used_plates=used_plates,
        ))
    return rows


def vehicle_row_values(record, index):
    return (
        str(index),
        record.get('plate') or '',
        record.get('vin') or '',
        record.get('model') or '',
        record.get('energy') or '',
        record.get('engine_no') or '',
        record.get('category') or '',
        record.get('kind') or '',
        record.get('first_reg') or '',
        '✓' if record.get('valid') else '×',
    )
