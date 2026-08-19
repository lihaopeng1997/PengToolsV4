# -*- coding: utf-8 -*-
import datetime
import random
import re

from tools.china_regions import all_district_codes, lookup_region


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


ETHNIC_GROUPS = (
    '汉族', '蒙古族', '回族', '藏族', '维吾尔族', '苗族', '彝族', '壮族', '布依族', '朝鲜族',
    '满族', '侗族', '瑶族', '白族', '土家族', '哈尼族', '哈萨克族', '傣族', '黎族', '傈僳族',
    '佤族', '畲族', '高山族', '拉祜族', '水族', '东乡族', '纳西族', '景颇族', '柯尔克孜族',
    '土族', '达斡尔族', '仫佬族', '羌族', '布朗族', '撒拉族', '毛南族', '仡佬族', '锡伯族',
    '阿昌族', '普米族', '塔吉克族', '怒族', '乌孜别克族', '俄罗斯族', '鄂温克族', '德昂族',
    '保安族', '裕固族', '京族', '塔塔尔族', '独龙族', '鄂伦春族', '赫哲族', '门巴族',
    '珞巴族', '基诺族',
)
MOBILE_PREFIXES = (
    '130', '131', '132', '133', '135', '136', '137', '138', '139',
    '150', '151', '152', '155', '156', '157', '158', '159',
    '166', '171', '172', '175', '176', '177', '178',
    '180', '181', '182', '183', '185', '186', '187', '188', '189',
    '191', '193', '195', '198', '199',
)
EMAIL_DOMAINS = ('163.com', '126.com', 'qq.com', 'sina.com', '139.com', 'yeah.net')
STREETS = ('人民路', '解放路', '中山路', '建设路', '和平路', '文化路', '花园路', '滨河路', '科技路')
POSTAL_PREFIX = {
    '11': '100', '12': '300', '13': '050', '14': '030', '15': '010',
    '21': '110', '22': '130', '23': '150', '31': '200', '32': '210',
    '33': '310', '34': '230', '35': '350', '36': '330', '37': '250',
    '41': '450', '42': '430', '43': '410', '44': '510', '45': '530',
    '46': '570', '50': '400', '51': '610', '52': '550', '53': '650',
    '54': '850', '61': '710', '62': '730', '63': '810', '64': '750', '65': '830',
}


def generate_mobile(rng=None):
    rng = rng or random.Random()
    return rng.choice(MOBILE_PREFIXES) + f'{rng.randint(0, 99999999):08d}'


def generate_email(name, rng=None):
    rng = rng or random.Random()
    local = f'{rng.choice("abcdefghijklmnopqrstuvwxyz")}{rng.randint(1000, 999999)}'
    return f'{local}@{rng.choice(EMAIL_DOMAINS)}'


def generate_postal_code(area_code='', rng=None):
    rng = rng or random.Random()
    prefix = POSTAL_PREFIX.get(str(area_code or '')[:2], f'{rng.randint(100, 859):03d}')
    return prefix + f'{rng.randint(0, 999):03d}'


def generate_cn_address(area_code='', rng=None):
    rng = rng or random.Random()
    province, city, district = lookup_region(area_code)
    if not province:
        province, city, district = lookup_region(rng.choice(AREA_CODES))
    if city in ('北京市', '天津市', '上海市', '重庆市') or city == province:
        city = ''
    street = rng.choice(STREETS)
    number = rng.randint(1, 888)
    room = rng.randint(101, 2599)
    return f'{province}{city}{district}{street}{number}号{room}室'


def _validity_for_kind(kind, age, term, today, rng):
    if term in (5, 10, 20):
        years = int(term)
        long_term = False
    elif term == 'long':
        years = 0
        long_term = True
    elif kind == 'resident_id':
        if age < 16:
            years, long_term = 5, False
        elif age <= 25:
            years, long_term = 10, False
        elif age <= 45:
            years, long_term = 20, False
        else:
            years, long_term = 0, True
    elif kind == 'passport':
        years, long_term = (5, False) if age < 16 else (10, False)
    else:
        years, long_term = 10, False
    start = today - datetime.timedelta(days=rng.randint(30, 800))
    if long_term:
        return start.isoformat(), '长期'
    end = _safe_year_shift(start, years)
    return start.isoformat(), end.isoformat()


def _issuer_for_kind(kind, area_code, rng):
    province, city, district = lookup_region(area_code)
    place = district or city or province or '本市'
    if kind == 'resident_id':
        return f'{place}公安局'
    if kind == 'passport':
        return '国家移民管理局'
    if kind == 'military_officer':
        return '中国人民解放军'
    if kind == 'armed_police':
        return '中国人民武装警察部队'
    return f'{place}公安局'


def generate_personal_record(kind, **options):
    rng = random.Random()
    area_code = options.get('area_code') or rng.choice(AREA_CODES)
    if kind == 'resident_id':
        number = generate_resident_id(
            area_code=area_code,
            min_age=options.get('min_age'),
            max_age=options.get('max_age'),
            gender=options.get('gender', 'random'),
        )
        age = resident_id_age(number)
        area_code = number[:6]
    else:
        generators = {
            'passport': generate_passport,
            'military_officer': generate_military_officer_card,
            'armed_police': generate_armed_police_document,
        }
        number = generators[kind]()
        age = rng.randint(18, 60)
    name = generate_person_name()
    ethnicity = options.get('ethnicity') or rng.choice(ETHNIC_GROUPS)
    start, end = _validity_for_kind(kind, age, options.get('valid_term'), datetime.date.today(), rng)
    return {
        'kind': kind,
        'name': name,
        'document': number,
        'ethnicity': ethnicity,
        'valid_from': start,
        'valid_to': end,
        'issuer': _issuer_for_kind(kind, area_code, rng),
        'mobile': generate_mobile(rng),
        'email': generate_email(name, rng),
        'postal_code': generate_postal_code(area_code, rng),
        'address': generate_cn_address(area_code, rng),
    }


def generate_personal_records(kind, count, **options):
    rows = []
    seen = set()
    while len(rows) < count:
        record = generate_personal_record(kind, **options)
        if record['document'] in seen:
            continue
        seen.add(record['document'])
        rows.append(record)
    return rows


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
