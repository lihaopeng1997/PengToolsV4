# -*- coding: utf-8 -*-
"""零外部依赖的离线 PowerPoint (.pptx) 生成器。

纯 Python 标准库 (zipfile + OpenXML PresentationML) 生成标准 16:9 宽屏 .pptx，
原生兼容 Microsoft PowerPoint / WPS Office / LibreOffice，无需安装 python-pptx。
"""

from __future__ import annotations

import datetime
import os
import tempfile
import zipfile
from xml.sax.saxutils import escape as _xml_escape

MAX_PPT_SLIDES = 40
MAX_BULLETS_PER_SLIDE = 12

# 16:9 宽屏画布（单位 EMU: 1 inch = 914400 EMU）
_SLIDE_W = 12192000  # 13.333 in
_SLIDE_H = 6858000   # 7.5 in

PPT_THEMES: dict[str, dict[str, str]] = {
    'prism': {
        'bg': 'F6F5FC',
        'cover_bg': '262047',
        'accent': '6C58D9',
        'accent_light': 'EEE9FF',
        'title': '221E3A',
        'body': '3A3552',
        'muted': '706B8A',
        'cover_title': 'FFFFFF',
        'cover_sub': 'D9D2FF',
        'card_bg': 'FFFFFF',
        'card_border': 'DDD6F3',
    },
    'navy': {
        'bg': 'F4F7FB',
        'cover_bg': '0F2540',
        'accent': '1D5FA8',
        'accent_light': 'E2EEF9',
        'title': '10233C',
        'body': '2E4158',
        'muted': '647991',
        'cover_title': 'FFFFFF',
        'cover_sub': 'C4DCF4',
        'card_bg': 'FFFFFF',
        'card_border': 'D2DFEE',
    },
    'clean': {
        'bg': 'F7F7F5',
        'cover_bg': '1F2937',
        'accent': '059669',
        'accent_light': 'DCFCE7',
        'title': '111827',
        'body': '374151',
        'muted': '6B7280',
        'cover_title': 'FFFFFF',
        'cover_sub': 'D1FAE5',
        'card_bg': 'FFFFFF',
        'card_border': 'E5E7EB',
    },
}


def _x(text: str) -> str:
    """XML-safe text with control characters stripped."""
    cleaned = ''.join(ch for ch in str(text or '') if ch in ('\t', '\n', '\r') or ord(ch) >= 32)
    return _xml_escape(cleaned)


def _rect_shape(
    sp_id: int,
    name: str,
    x: int,
    y: int,
    cx: int,
    cy: int,
    fill_hex: str,
    line_hex: str | None = None,
    round_rect: bool = False,
) -> str:
    prst = 'roundRect' if round_rect else 'rect'
    ln = (
        f'<a:ln w="12700"><a:solidFill><a:srgbClr val="{line_hex}"/></a:solidFill></a:ln>'
        if line_hex
        else '<a:ln><a:noFill/></a:ln>'
    )
    return (
        f'<p:sp>'
        f'<p:nvSpPr><p:cNvPr id="{sp_id}" name="{_x(name)}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
        f'<p:spPr>'
        f'<a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        f'<a:prstGeom prst="{prst}"><a:avLst/></a:prstGeom>'
        f'<a:solidFill><a:srgbClr val="{fill_hex}"/></a:solidFill>'
        f'{ln}'
        f'</p:spPr>'
        f'<p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody>'
        f'</p:sp>'
    )


def _text_box(
    sp_id: int,
    name: str,
    x: int,
    y: int,
    cx: int,
    cy: int,
    paragraphs: list[tuple[str, int, str, bool]],
    *,
    align: str = 'l',
    line_spc_pct: int = 125000,
) -> str:
    """paragraphs: list of (text, font_pt, color_hex, bold)."""
    p_xml_parts: list[str] = []
    for text, pt, color_hex, bold in paragraphs:
        sz = max(900, int(pt * 100))
        b_attr = ' b="1"' if bold else ''
        p_xml_parts.append(
            f'<a:p>'
            f'<a:pPr algn="{align}" marL="0"><a:lnSpc><a:spcPct val="{line_spc_pct}"/></a:lnSpc>'
            f'<a:spcAft><a:spcPts val="800"/></a:spcAft></a:pPr>'
            f'<a:r>'
            f'<a:rPr lang="zh-CN" sz="{sz}"{b_attr} dirty="0">'
            f'<a:solidFill><a:srgbClr val="{color_hex}"/></a:solidFill>'
            f'<a:latin typeface="Microsoft YaHei UI"/>'
            f'<a:ea typeface="Microsoft YaHei UI"/>'
            f'</a:rPr>'
            f'<a:t>{_x(text)}</a:t>'
            f'</a:r>'
            f'</a:p>'
        )
    body = ''.join(p_xml_parts) or '<a:p/>'
    return (
        f'<p:sp>'
        f'<p:nvSpPr><p:cNvPr id="{sp_id}" name="{_x(name)}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
        f'<p:spPr>'
        f'<a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        f'<a:noFill/><a:ln><a:noFill/></a:ln>'
        f'</p:spPr>'
        f'<p:txBody><a:bodyPr wrap="square" lIns="91440" tIns="45720" rIns="91440" bIns="45720"/>'
        f'<a:lstStyle/>{body}</p:txBody>'
        f'</p:sp>'
    )


def _slide_xml(bg_hex: str, shapes_xml: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        '<p:cSld>'
        f'<p:bg><p:bgPr><a:solidFill><a:srgbClr val="{bg_hex}"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>'
        '<p:spTree>'
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        f'{shapes_xml}'
        '</p:spTree>'
        '</p:cSld>'
        '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>'
        '</p:sld>'
    )


def _build_cover_slide(title: str, subtitle: str, footer: str, pal: dict[str, str]) -> str:
    shapes = [
        # 左侧装饰亮条
        _rect_shape(2, 'AccentBar', 0, 0, 260000, _SLIDE_H, pal['accent']),
        # 底部细装饰条
        _rect_shape(3, 'BottomBar', 780000, 5600000, 1800000, 75000, pal['accent']),
        # 主标题
        _text_box(
            4, 'CoverTitle', 780000, 1950000, 10400000, 1800000,
            [(title or '演示文稿', 40, pal['cover_title'], True)],
            line_spc_pct=115000,
        ),
    ]
    if subtitle:
        shapes.append(
            _text_box(
                5, 'CoverSubtitle', 780000, 3850000, 10200000, 1100000,
                [(subtitle, 20, pal['cover_sub'], False)],
                line_spc_pct=130000,
            )
        )
    if footer:
        shapes.append(
            _text_box(
                6, 'CoverFooter', 780000, 5850000, 10200000, 500000,
                [(footer, 13, pal['cover_sub'], False)],
            )
        )
    return _slide_xml(pal['cover_bg'], ''.join(shapes))


def _build_content_slide(
    index: int,
    total: int,
    title: str,
    bullets: list[str],
    subtitle: str,
    pal: dict[str, str],
) -> str:
    shapes = [
        # 顶部细主题条
        _rect_shape(2, 'TopStrip', 0, 0, _SLIDE_W, 95000, pal['accent']),
        # 内容白卡片
        _rect_shape(
            3, 'ContentCard', 640000, 1450000, 10912000, 4760000,
            pal['card_bg'], line_hex=pal['card_border'], round_rect=True,
        ),
        # 页面标题
        _text_box(
            4, 'SlideTitle', 640000, 340000, 9800000, 680000,
            [(title or f'第 {index} 页', 26, pal['title'], True)],
        ),
        # 页码角标
        _text_box(
            5, 'PageNum', 10700000, 6320000, 900000, 360000,
            [(f'{index} / {total}', 11, pal['muted'], False)],
            align='r',
        ),
    ]
    if subtitle:
        shapes.append(
            _text_box(
                6, 'SlideSub', 640000, 960000, 10600000, 400000,
                [(subtitle, 13, pal['accent'], True)],
            )
        )
    clean_bullets = [str(b or '').strip() for b in (bullets or []) if str(b or '').strip()][:MAX_BULLETS_PER_SLIDE]
    font_pt = 17 if len(clean_bullets) <= 5 else (15 if len(clean_bullets) <= 8 else 13)
    paras = [(f'●  {line}', font_pt, pal['body'], False) for line in clean_bullets]
    if not paras:
        paras = [('（暂无要点）', 15, pal['muted'], False)]
    shapes.append(
        _text_box(
            7, 'SlideBody', 920000, 1740000, 10350000, 4220000,
            paras,
            line_spc_pct=140000,
        )
    )
    return _slide_xml(pal['bg'], ''.join(shapes))


def _content_types_xml(slide_count: int) -> str:
    overrides = [
        '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
        '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>',
        '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>',
        '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>',
    ]
    for i in range(1, slide_count + 1):
        overrides.append(
            f'<Override PartName="/ppt/slides/slide{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'{"".join(overrides)}'
        '</Types>'
    )


_ROOT_RELS_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
    '</Relationships>'
)

_SLIDE_REL_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
    '</Relationships>'
)

_SLIDE_LAYOUT_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" type="blank" preserve="1">'
    '<p:cSld name="Blank"><p:spTree>'
    '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
    '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
    '</p:spTree></p:cSld>'
    '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>'
    '</p:sldLayout>'
)

_SLIDE_LAYOUT_RELS_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>'
    '</Relationships>'
)

_SLIDE_MASTER_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
    '<p:cSld><p:spTree>'
    '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
    '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
    '</p:spTree></p:cSld>'
    '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" '
    'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
    '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
    '</p:sldMaster>'
)

_SLIDE_MASTER_RELS_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>'
    '</Relationships>'
)

_THEME_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="PengTools">'
    '<a:themeElements>'
    '<a:clrScheme name="PengTools">'
    '<a:dk1><a:srgbClr val="221E3A"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>'
    '<a:dk2><a:srgbClr val="3A3552"/></a:dk2><a:lt2><a:srgbClr val="F6F5FC"/></a:lt2>'
    '<a:accent1><a:srgbClr val="6C58D9"/></a:accent1><a:accent2><a:srgbClr val="0891B2"/></a:accent2>'
    '<a:accent3><a:srgbClr val="059669"/></a:accent3><a:accent4><a:srgbClr val="D97706"/></a:accent4>'
    '<a:accent5><a:srgbClr val="2563EB"/></a:accent5><a:accent6><a:srgbClr val="DC2626"/></a:accent6>'
    '<a:hlink><a:srgbClr val="6C58D9"/></a:hlink><a:folHlink><a:srgbClr val="503DBE"/></a:folHlink>'
    '</a:clrScheme>'
    '<a:fontScheme name="PengTools">'
    '<a:majorFont><a:latin typeface="Microsoft YaHei UI"/><a:ea typeface="Microsoft YaHei UI"/><a:cs typeface=""/></a:majorFont>'
    '<a:minorFont><a:latin typeface="Microsoft YaHei UI"/><a:ea typeface="Microsoft YaHei UI"/><a:cs typeface=""/></a:minorFont>'
    '</a:fontScheme>'
    '<a:fmtScheme name="PengTools">'
    '<a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>'
    '<a:lnStyleLst><a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln><a:ln w="12700"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln><a:ln w="19050"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>'
    '<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>'
    '<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst>'
    '</a:fmtScheme>'
    '</a:themeElements></a:theme>'
)


def _presentation_xml(slide_count: int) -> str:
    sld_ids = ''.join(
        f'<p:sldId id="{256 + i}" r:id="rId{i + 1}"/>'
        for i in range(1, slide_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
        f'<p:sldIdLst>{sld_ids}</p:sldIdLst>'
        f'<p:sldSz cx="{_SLIDE_W}" cy="{_SLIDE_H}"/>'
        '<p:notesSz cx="6858000" cy="9144000"/>'
        '</p:presentation>'
    )


def _presentation_rels_xml(slide_count: int) -> str:
    rels = [
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>'
    ]
    for i in range(1, slide_count + 1):
        rels.append(
            f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{i}.xml"/>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'{"".join(rels)}'
        '</Relationships>'
    )


def build_pptx(
    output_path: str,
    *,
    title: str,
    subtitle: str = '',
    slides: list[dict] | None = None,
    theme: str = 'prism',
    author: str = 'PengToolsHub',
) -> dict:
    """生成标准 .pptx 文件（原子替换写入），返回摘要字典。"""
    target = os.path.abspath(str(output_path or '').strip())
    if not target.lower().endswith('.pptx'):
        raise ValueError('输出路径必须以 .pptx 结尾')
    pal = PPT_THEMES.get(str(theme or 'prism').strip().lower(), PPT_THEMES['prism'])
    clean_title = str(title or '').strip() or '演示文稿'
    clean_sub = str(subtitle or '').strip()
    slide_specs = [s for s in (slides or []) if isinstance(s, dict)][:MAX_PPT_SLIDES - 1]
    total_slides = 1 + len(slide_specs)
    today_str = datetime.date.today().isoformat()
    footer = f'{author}  ·  {today_str}' if author else today_str

    slide_xmls: list[str] = [_build_cover_slide(clean_title, clean_sub, footer, pal)]
    for idx, spec in enumerate(slide_specs, start=2):
        st = str(spec.get('title') or f'第 {idx} 页').strip()
        ssub = str(spec.get('subtitle') or '').strip()
        raw_bullets = spec.get('bullets') or []
        if isinstance(raw_bullets, str):
            bullets = [line.strip() for line in raw_bullets.splitlines() if line.strip()]
        else:
            bullets = [str(x).strip() for x in raw_bullets if str(x).strip()]
        slide_xmls.append(_build_content_slide(idx, total_slides, st, bullets, ssub, pal))

    parent = os.path.dirname(target) or '.'
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix='.ppt-', suffix='.tmp', dir=parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('[Content_Types].xml', _content_types_xml(total_slides))
            zf.writestr('_rels/.rels', _ROOT_RELS_XML)
            zf.writestr('ppt/presentation.xml', _presentation_xml(total_slides))
            zf.writestr('ppt/_rels/presentation.xml.rels', _presentation_rels_xml(total_slides))
            zf.writestr('ppt/slideMasters/slideMaster1.xml', _SLIDE_MASTER_XML)
            zf.writestr('ppt/slideMasters/_rels/slideMaster1.xml.rels', _SLIDE_MASTER_RELS_XML)
            zf.writestr('ppt/slideLayouts/slideLayout1.xml', _SLIDE_LAYOUT_XML)
            zf.writestr('ppt/slideLayouts/_rels/slideLayout1.xml.rels', _SLIDE_LAYOUT_RELS_XML)
            zf.writestr('ppt/theme/theme1.xml', _THEME_XML)
            for idx, sxml in enumerate(slide_xmls, start=1):
                zf.writestr(f'ppt/slides/slide{idx}.xml', sxml)
                zf.writestr(f'ppt/slides/_rels/slide{idx}.xml.rels', _SLIDE_REL_XML)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return {
        'ok': True,
        'path': target,
        'title': clean_title,
        'slide_count': total_slides,
        'theme': str(theme or 'prism').strip().lower() if str(theme or '').strip().lower() in PPT_THEMES else 'prism',
        'size': os.path.getsize(target),
    }


__all__ = ['MAX_PPT_SLIDES', 'PPT_THEMES', 'build_pptx']
