# -*- coding: utf-8 -*-
import random

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
CHINA_WMIS = ('LHG', 'LSV', 'LFV', 'LGB', 'LVG', 'LDC', 'LZW', 'LVS')


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
