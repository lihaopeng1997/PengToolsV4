"""Intranet proof, outbound grants, and the model-scrub default (offscreen)."""
import os
import sys
import time
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ssh_session'))
import _isolation  # noqa: E402,F401  (redirect HOME/config before imports)

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'{("PASS" if cond else "FAIL")}  {name}' + (f'  [{detail}]' if detail and not cond else ''))


from tools.model_boundary import classify_endpoint, classify_private_config

private_ip = classify_endpoint('http://10.1.2.3:8080/v1')
check('私网 IP 分类为内网', private_ip.kind == 'intranet' and private_ip.is_intranet)

public_ip = classify_endpoint('https://8.8.8.8/v1')
check('公网 IP 分类为外网', public_ip.kind == 'internet' and not public_ip.is_intranet)

domain = classify_endpoint('https://models.example.com/v1')
check('域名分类为未知', domain.kind == 'unknown' and not domain.is_intranet)

empty = classify_endpoint('')
check('空地址分类为未知', empty.kind == 'unknown' and empty.host == '' and not empty.is_intranet)

private_cfg = classify_private_config({'base_url': 'http://10.1.2.3/v1'})
check('私网配置 is_intranet', private_cfg is not None and private_cfg.is_intranet)
public_cfg = classify_private_config({'endpoint': 'https://8.8.8.8/v1'})
check('公网配置不是内网', public_cfg is not None and not public_cfg.is_intranet)
domain_cfg = classify_private_config({'url': 'https://models.example.com/v1'})
check('域名配置不是内网', domain_cfg is not None and not domain_cfg.is_intranet)
check('空配置判断不了', classify_private_config({'base_url': ''}) is None)
check('缺少地址判断不了', classify_private_config({}) is None)

import tools.outbound_authorization as outbound

outbound.clear()
issued = outbound.issue(
    'run-boundary',
    model_config_id='model-a',
    host='10.1.2.3',
    materials=['a.log'],
    allowed_tools=['read_file'],
    boundary_kind='intranet',
)
same = dict(model_config_id='model-a', host='10.1.2.3', materials=['a.log'], allowed_tools=['read_file'])
try:
    grant = outbound.check('run-boundary', **same)
    check('授权范围未变时通过', grant is issued)
except outbound.OutboundNotAuthorized:
    check('授权范围未变时通过', False)


def rejects(name, **overrides):
    kwargs = dict(same)
    kwargs.update(overrides)
    try:
        outbound.check('run-boundary', **kwargs)
    except outbound.OutboundNotAuthorized:
        check(name, True)
    else:
        check(name, False)


rejects('材料变化拒绝', materials=['b.log'])
rejects('地址变化拒绝', host='203.0.113.10')
rejects('工具变化拒绝', allowed_tools=['search_code'])

original_now = outbound.time.time
outbound.time.time = lambda: issued.issued_at + outbound.GRANT_TTL_SECONDS + 5
try:
    rejects('过期拒绝')
finally:
    outbound.time.time = original_now
    outbound.clear()

os.environ['PENGTOOLS_INTRANET'] = '1'
from tools.intranet_mode import reset_cache
reset_cache()
from tools.log_analysis.service import LogService

service = LogService()
try:
    check('LogService 默认清洗开启', service.scrubber.enabled is True)
finally:
    service.close()
    service.join(2)

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
