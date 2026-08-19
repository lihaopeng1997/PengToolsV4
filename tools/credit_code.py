# -*- coding: utf-8 -*-
import random
import string

CHARSET = '0123456789ABCDEFGHJKLMNPQRTUWXY'
WEIGHTS = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28]
PROVINCES = {
    '11': '北京', '12': '天津', '13': '河北', '14': '山西',
    '15': '内蒙古', '21': '辽宁', '22': '吉林', '23': '黑龙江',
    '31': '上海', '32': '江苏', '33': '浙江', '34': '安徽',
    '35': '福建', '36': '江西', '37': '山东', '41': '河南',
    '42': '湖北', '43': '湖南', '44': '广东', '45': '广西',
    '46': '海南', '50': '重庆', '51': '四川', '52': '贵州',
    '53': '云南', '54': '西藏', '61': '陕西', '62': '甘肃',
    '63': '青海', '64': '宁夏', '65': '新疆'
}
ORG_TYPES = {
    '11': '企业', '12': '个体工商户',
    '21': '事业单位', '31': '社会团体',
    '51': '民办非企业单位', '91': '其他组织机构'
}
PREFIXES = ['鑫','润','源','恒','盛','瑞','达','泰','中','华','信','诚','和','德','安','通','博','丰','鼎','创','嘉','益','兴','佳','永','金','银','天','地','海','星','龙','凤','明']
INDUSTRIES = ['科技','信息','贸易','实业','投资','建设','医药','汽车','能源','电子','网络','大数据','人工智能','新能源','生物','环保','物流','教育','文化','餐饮','酒店','房地产','金融','保险']
LEGAL_FORMS = ['有限公司','股份有限公司','集团有限公司','有限责任公司','合伙企业']
CITIES = ['','北京','上海','广州','深圳','杭州','南京','武汉','成都','重庆','西安','郑州','济南','青岛','大连','厦门','苏州','无锡','东莞','佛山','宁波','福州']


def _char_map():
    return {c: i for i, c in enumerate(CHARSET)}

_CHAR_MAP = None
def _get_char_map():
    global _CHAR_MAP
    if _CHAR_MAP is None:
        _CHAR_MAP = _char_map()
    return _CHAR_MAP


def calc_check_digit(first17):
    cm = _get_char_map()
    total = sum(cm[first17[i]] * WEIGHTS[i] for i in range(17))
    return CHARSET[(31 - total % 31) % 31]


def validate_code(code):
    if not code or len(code) != 18:
        return False
    cm = _get_char_map()
    for ch in code:
        if ch not in cm:
            return False
    return code[17] == calc_check_digit(code[:17])


def generate_code(province='', city_district='', org_type=''):
    r = random.Random()
    # Position 1-2: registration authority (province code)
    if not province:
        prov = r.choice(list(PROVINCES.keys()))
    elif province in PROVINCES:
        prov = province
    else:
        prov = r.choice(list(PROVINCES.keys()))

    # Position 3-8: city/district code
    if city_district and len(city_district) == 6 and city_district.isdigit():
        district = city_district
    else:
        district = str(r.randint(0, 999999)).zfill(6)

    # Position 9: administration code (letter or digit)
    pos9_chars = 'ABCDEFGHJKLMNPQRTUWXY0123456789'
    pos9 = r.choice(pos9_chars)

    # Position 10-11: org type
    if not org_type:
        ot = r.choice(list(ORG_TYPES.keys()))
    elif org_type in ORG_TYPES:
        ot = org_type
    else:
        ot = r.choice(list(ORG_TYPES.keys()))

    # Position 12-17: serial number
    serial = ''.join(r.choice(CHARSET) for _ in range(6))

    first17 = prov + district + pos9 + ot + serial
    return first17 + calc_check_digit(first17)


def generate_batch(count, province='', city_district='', org_type=''):
    codes = set()
    max_attempts = count * 20
    attempts = 0
    while len(codes) < count and attempts < max_attempts:
        codes.add(generate_code(province, city_district, org_type))
        attempts += 1
    return list(codes)


def generate_company_name(province_code='', city_code='', org_type_code=''):
    r = random.Random()
    city = r.choice(CITIES)
    brand1 = r.choice(PREFIXES)
    brand2 = r.choice(PREFIXES)
    ind = r.choice(INDUSTRIES)
    form = r.choice(LEGAL_FORMS)
    return city + brand1 + brand2 + ind + form


def generate_names_for_codes(count, province='', city='', org_type=''):
    return [generate_company_name(province, city, org_type) for _ in range(count)]


def company_name_abbrev(name: str) -> str:
    """企业名称去掉行政区划和公司形态后的拼音首字母，如 北京鑫润科技有限公司 → xrkj。"""
    text = str(name or '').strip()
    for city in sorted((item for item in CITIES if item), key=len, reverse=True):
        if text.startswith(city):
            text = text[len(city):]
            break
    for form in sorted(LEGAL_FORMS, key=len, reverse=True):
        if text.endswith(form):
            text = text[:-len(form)]
            break
    letters = ''
    try:
        from pypinyin import Style, lazy_pinyin
        letters = ''.join(lazy_pinyin(text, style=Style.FIRST_LETTER))
    except Exception:
        letters = ''.join(ch.lower() for ch in text if 'a' <= ch.lower() <= 'z')
    cleaned = ''.join(ch for ch in letters.lower() if 'a' <= ch <= 'z')
    return cleaned or 'corp'


def company_email(name: str, rng=None) -> str:
    rng = rng or random.Random()
    abbr = company_name_abbrev(name)
    domain = rng.choice(('com', 'com.cn', 'cn'))
    return f'{abbr}@{abbr}.{domain}'


BUSINESS_SCOPES = (
    '软件开发；信息技术咨询；信息系统集成服务。',
    '货物进出口；技术进出口；国内贸易代理。',
    '房屋建筑和市政基础设施工程施工。',
    '汽车销售；汽车配件零售；机动车维修。',
    '药品零售；医疗器械销售；健康咨询服务（不含诊疗）。',
    '普通货物道路运输；仓储服务；装卸搬运。',
    '教育咨询；职业技能培训（不含发证）。',
    '餐饮服务；食品销售；会议及展览服务。',
    '房地产开发经营；物业管理；房地产中介。',
    '新能源技术研发；节能环保工程；合同能源管理。',
)
AREA_CODES_PHONE = {
    '11': '010', '12': '022', '31': '021', '44': '020', '32': '025',
    '33': '0571', '42': '027', '51': '028', '50': '023', '37': '0531',
    '41': '0371', '61': '029', '21': '024', '35': '0591',
}


def generate_unit_record(province='', org_type='', business='', valid_term=None):
    import datetime
    rng = random.Random()
    code = generate_code(province, org_type=org_type)
    name = generate_company_name(province, org_type_code=org_type)
    scope = business if business in BUSINESS_SCOPES else rng.choice(BUSINESS_SCOPES)
    start = datetime.date.today() - datetime.timedelta(days=rng.randint(60, 3600))
    if valid_term == 'long':
        end = '长期'
    elif valid_term in (1, 3, 5, 10):
        try:
            end = start.replace(year=start.year + int(valid_term)).isoformat()
        except ValueError:
            end = start.replace(year=start.year + int(valid_term), day=28).isoformat()
    else:
        if rng.random() < 0.25:
            end = '长期'
        else:
            years = rng.choice((3, 5, 10, 20))
            try:
                end = start.replace(year=start.year + years).isoformat()
            except ValueError:
                end = start.replace(year=start.year + years, day=28).isoformat()
    area = AREA_CODES_PHONE.get(str(province or code[:2]), f'0{rng.randint(10, 29)}')
    phone = f'{area}-{rng.randint(20000000, 88999999)}'
    email = company_email(name, rng)
    postal = f'{rng.randint(100000, 859999)}'
    street = rng.choice(('创业路', '科技大道', '工业园路', '商务中心路', '滨江大道'))
    address = f'{PROVINCES.get(str(province or code[:2]), "")}{street}{rng.randint(1, 288)}号'
    return {
        'kind': 'credit_code',
        'name': name,
        'document': code,
        'valid_from': start.isoformat(),
        'valid_to': end,
        'business_scope': scope,
        'phone': phone,
        'email': email,
        'postal_code': postal,
        'address': address,
    }


def generate_unit_records(count, province='', org_type='', business='', valid_term=None):
    rows = []
    seen = set()
    while len(rows) < count:
        record = generate_unit_record(province, org_type, business, valid_term)
        if record['document'] in seen:
            continue
        seen.add(record['document'])
        rows.append(record)
    return rows
