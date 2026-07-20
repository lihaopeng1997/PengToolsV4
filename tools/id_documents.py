# -*- coding: utf-8 -*-
import datetime
import random
import re

from tools.china_regions import all_district_codes


DOCUMENT_TYPES = {
    'resident_id': ('居民身份证', 'Resident Identity Card'),
    'passport': ('中国普通护照', 'Chinese Ordinary Passport'),
    'military_officer': ('军官证', 'Military Officer Card'),
    'armed_police': ('武警身份证件', 'Armed Police Identity Document'),
}

AREA_CODES = all_district_codes()
ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
ID_CHECK_CODES = '10X98765432'
SURNAMES = '赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦许何吕施张孔曹严华金魏陶姜谢邹喻柏水窦章云苏潘葛范彭郎鲁韦昌马苗凤花方俞任袁柳唐罗薛雷贺倪汤滕殷毕郝安常乐于傅皮卞齐康伍余元顾孟黄穆萧尹姚邵汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯管卢莫房裘缪解应宗丁宣邓郁单杭洪包左石崔吉龚程嵇邢裴陆荣翁荀羊甄曲封芮储靳汲邴糜松井段富巫乌焦巴弓牧隗山谷车侯宓蓬全班仰秋仲伊宫宁仇栾暴甘厉戎祖武符刘景詹束龙叶司黎乔苍双闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍桑桂濮牛寿通边扈燕冀郏浦尚农温别庄晏柴瞿阎充慕连茹习艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷辛阚那简饶空曾毋沙养鞠须丰巢关蒯相查后荆红游竺权盖益桓公'
GIVEN_CHARS = '伟芳娜敏静秀英丽强磊军洋勇艳杰娟涛明超秀兰霞平刚桂英建华玉兰鹏红梅鑫波斌宇浩凯婷雪晨睿嘉欣怡子轩梓涵雨桐思远俊熙佳宁天佑文博一诺'


def _resident_id_check(first17):
    total = sum(int(first17[index]) * ID_WEIGHTS[index] for index in range(17))
    return ID_CHECK_CODES[total % 11]


def _safe_year_shift(value, years):
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)


def _birthday_for_age_range(min_age, max_age, rng):
    today = datetime.date.today()
    earliest = _safe_year_shift(today, -(max_age + 1)) + datetime.timedelta(days=1)
    latest = _safe_year_shift(today, -min_age)
    return earliest + datetime.timedelta(days=rng.randint(0, (latest - earliest).days))


def generate_resident_id(area_code='', min_age=None, max_age=None, gender='random'):
    rng = random.Random()
    if area_code not in AREA_CODES:
        area_code = rng.choice(AREA_CODES)
    if min_age is None or max_age is None:
        min_age, max_age = 0, 100
    min_age = max(0, min(120, int(min_age)))
    max_age = max(min_age, min(120, int(max_age)))
    birthday = _birthday_for_age_range(min_age, max_age, rng)
    if gender == 'male':
        sequence_number = rng.randrange(1, 1000, 2)
    elif gender == 'female':
        sequence_number = rng.randrange(2, 999, 2)
    else:
        sequence_number = rng.randint(1, 999)
    first17 = area_code + birthday.strftime('%Y%m%d') + f'{sequence_number:03d}'
    return first17 + _resident_id_check(first17)


def validate_resident_id(number):
    if not re.fullmatch(r'\d{17}[0-9X]', number or ''):
        return False
    try:
        datetime.datetime.strptime(number[6:14], '%Y%m%d')
    except ValueError:
        return False
    return number[:6] in AREA_CODES and number[-1] == _resident_id_check(number[:17])


def resident_id_age(number, reference_date=None):
    birthday = datetime.datetime.strptime(number[6:14], '%Y%m%d').date()
    today = reference_date or datetime.date.today()
    return today.year - birthday.year - ((today.month, today.day) < (birthday.month, birthday.day))


def resident_id_gender(number):
    return 'male' if int(number[16]) % 2 else 'female'


def generate_passport():
    return 'E' + f'{random.randint(1, 99999999):08d}'


def generate_military_officer_card():
    return f'军字第{random.randint(1, 99999999):08d}号'


def generate_armed_police_document():
    return f'武字第{random.randint(1, 99999999):08d}号'


def validate_personal_document(kind, number):
    if kind == 'resident_id':
        return validate_resident_id(number)
    if kind == 'passport':
        return bool(re.fullmatch(r'E\d{8}', number or ''))
    if kind == 'military_officer':
        return bool(re.fullmatch(r'军字第\d{8}号', number or ''))
    if kind == 'armed_police':
        return bool(re.fullmatch(r'武字第\d{8}号', number or ''))
    return False


def generate_person_name():
    rng = random.Random()
    length = rng.choice((1, 2))
    return rng.choice(SURNAMES) + ''.join(rng.choice(GIVEN_CHARS) for _ in range(length))


def generate_personal_batch(kind, count, **options):
    generators = {
        'resident_id': generate_resident_id,
        'passport': generate_passport,
        'military_officer': generate_military_officer_card,
        'armed_police': generate_armed_police_document,
    }
    if kind not in generators:
        raise ValueError(f'Unsupported document type: {kind}')
    results = set()
    while len(results) < count:
        if kind == 'resident_id':
            results.add(generators[kind](
                area_code=options.get('area_code', ''),
                min_age=options.get('min_age'),
                max_age=options.get('max_age'),
                gender=options.get('gender', 'random'),
            ))
        else:
            results.add(generators[kind]())
    return list(results)
