# -*- coding: utf-8 -*-
"""Known database-structure document templates and filename matching."""

import os
import re
import unicodedata


STANDARD_HEADERS = ["序号", "字段名称", "字段描述", "类型", "长度", "允许空", "缺省值"]
OCR_HEADERS = ["序号", "字段名称", "字段描述", "类型", "允许空"]
COMPACT8_HEADERS = ["#", "字段", "名称", "数据类型", "主键", "非空", "默认值", "备注说明"]
EMBEDDED5_HEADERS = ["字段名", "字段说明", "数据类型", "长度", "能否为空"]


def _profile(system, template, aliases=(), headers=STANDARD_HEADERS, group="", revision_layout="standard4"):
    return {
        "system": system,
        "template": template,
        "aliases": [system, *aliases],
        "headers": list(headers),
        "group": group,
        "revision_layout": revision_layout,
    }


TEMPLATE_PROFILES = [
    _profile("ECIF", r"共享中心系统\ECIF数据库表结构说明文档.docx",
             ("客户信息平台", "客户信息平台ECIF"), group="共享中心系统"),
    _profile("e保通", r"共享中心系统\e保通数据库表结构说明文档.docx", group="共享中心系统"),
    _profile("OCR识别系统", r"共享中心系统\OCR识别系统数据库表结构说明文档.docx",
             ("OCR", "OCR系统"), OCR_HEADERS, "共享中心系统", "type6"),
    _profile("单点登录平台", r"共享中心系统\单点登录平台数据库表结构说明文档V1.0.docx",
             ("单点登录", "SSO"), group="共享中心系统"),
    _profile("影像系统", r"共享中心系统\影像系统数据库表结构说明文档V1.0.docx",
             ("影像平台",), group="共享中心系统"),
    _profile("数据字典平台", r"共享中心系统\数据字典平台数据库表结构说明文档V1.0.docx",
             ("数据字典",), group="共享中心系统"),
    _profile("数据预警平台", r"共享中心系统\数据预警平台数据库表结构说明文档V1.0.docx",
             ("数据预警",), group="共享中心系统"),
    _profile("权限管理平台", r"共享中心系统\权限管理平台数据库表结构说明文档V1.0.docx",
             ("权限管理", "权限平台"), group="共享中心系统"),
    _profile("电子单证", r"共享中心系统\电子单证数据库表结构说明文档.docx",
             ("单证系统", "电子单证系统"), group="共享中心系统"),
    _profile("统一监管平台（PICS）", r"共享中心系统\统一监管平台（PICS）-数据库表结构说明文档.docx",
             ("统一监管平台", "PICS", "统一监管"), group="共享中心系统"),
    _profile("规则管理平台", r"共享中心系统\规则管理平台数据库表结构说明文档.docx",
             ("规则管理", "规则平台"), group="共享中心系统"),
    _profile("车险承保中心", r"共享中心系统\车险承保中心数据库表结构说明文档V1.0.0.docx",
             ("车险承保", "承保中心"), group="共享中心系统", revision_layout="type6"),
    _profile("企业服务总线平台", r"核心业务系统\企业服务总线平台数据库表结构说明文档.docx",
             ("企业服务总线平台", "ESB平台"), group="核心业务系统"),
    _profile("企业服务总线平台网关", r"核心业务系统\企业服务总线平台网关数据库表结构说明文档.docx",
             ("企业服务总线", "ESB网关", "ESB"), group="核心业务系统"),
    _profile("再保", r"核心业务系统\再保数据结构说明文档.docx",
             ("再保险", "再保系统"), group="核心业务系统"),
    _profile("农险理赔", r"核心业务系统\农险理赔数据库表结构文档.docx",
             ("农险理赔系统",), group="核心业务系统", revision_layout="none"),
    _profile("接报案", r"核心业务系统\接报案\接报案数据库表结构文档V2.0.docx",
             ("接报案系统",), group="核心业务系统"),
    _profile("收付", r"核心业务系统\收付数据库表结构说明文档.docx",
             ("收付系统",), group="核心业务系统"),
    _profile("核心", r"核心业务系统\核心数据库表结构说明文档.docx",
             ("核心业务", "核心业务系统"), group="核心业务系统"),
    _profile("理赔", r"核心业务系统\理赔工作流\理赔数据库表结构文档V2.0.docx",
             ("理赔系统", "理赔工作流"), group="核心业务系统", revision_layout="date_merged5"),
    _profile("电网", r"核心业务系统\电网理赔\电网数据库表结构文档V1.2.docx",
             ("电网理赔", "电网系统"), group="核心业务系统"),
    _profile("货运险", r"核心业务系统\货运险数据库表结构说明文档.docx",
             ("货运险系统",), group="核心业务系统"),
    _profile("销售管理", r"核心业务系统\销售管理数据库表结构说明文档.docx",
             ("销售管理系统", "销管", "销管系统"), group="核心业务系统"),
    _profile("诉讼案件管理系统", r"核心业务系统\诉讼案件管理系统\诉讼案件管理系统表结构说明文档V1.1.docx",
             ("诉讼案件管理", "诉讼管理系统", "诉讼系统"),
             group="核心业务系统", revision_layout="date_merged5"),
    _profile("投诉管理系统", r"核心业务系统\投诉管理系统\投诉管理数据结构文档 V1.0.1.docx",
             ("投诉管理", "投诉系统"), COMPACT8_HEADERS,
             "核心业务系统", "date_merged5"),
    _profile("非车理赔中心", r"核心业务系统\非车理赔中心\非车理赔中心 - 理赔数据库表结构文档V1.0.0.docx",
             ("非车理赔", "非车理赔系统"), COMPACT8_HEADERS,
             "核心业务系统", "date_merged5"),
    _profile("车险理赔系统", r"核心业务系统\车险理赔系统\6.4英大财险-业务应用-2024年车险理赔服务共享中心建设-设计开发实施项目-数据设计说明书-5.29-V45.0.docx",
             ("车险理赔", "车险理赔服务共享中心"), EMBEDDED5_HEADERS,
             "核心业务系统", "date_first6"),
]


def normalize_document_name(path_or_name):
    """Reduce a DOCX filename or system alias to a stable matching key."""
    name = os.path.splitext(os.path.basename(str(path_or_name)))[0]
    name = unicodedata.normalize("NFKC", name).strip()
    name = re.sub(r"_?整理后接口文档$", "", name, flags=re.I)
    name = re.sub(r"[\s_-]*V\d+(?:\.\d+)*$", "", name, flags=re.I)
    suffixes = (
        "数据库表结构说明文档", "数据库表结构文档", "数据库结构说明文档",
        "数据结构说明文档", "数据库结构", "数据结构文档", "数据库表结构说明",
    )
    changed = True
    while changed:
        changed = False
        for suffix in suffixes:
            if name.endswith(suffix):
                name = name[:-len(suffix)].rstrip(" -_—")
                changed = True
                break
    return re.sub(r"[\s()（）【】\-_—]", "", name).casefold()


def match_document_template(path_or_name):
    """Match an uploaded document name to a known latest template profile."""
    key = normalize_document_name(path_or_name)
    exact = []
    partial = []
    for profile in TEMPLATE_PROFILES:
        aliases = {normalize_document_name(alias) for alias in profile["aliases"]}
        if key in aliases:
            exact.append((len(key), profile))
            continue
        for alias in aliases:
            if len(alias) >= 2 and (alias in key or key in alias):
                overlap = min(len(alias), len(key)) / max(len(alias), len(key))
                # Long project-delivery filenames often wrap the actual system
                # name in a much longer title. A contained 4+ character alias
                # is still a strong match; longest alias wins below.
                confidence = 1.0 if len(alias) >= 4 and alias in key else overlap
                partial.append((len(alias), confidence, profile))
    if exact:
        profile = max(exact, key=lambda item: item[0])[1]
        return {**profile, "confidence": "exact", "match_key": key}
    if partial:
        _, overlap, profile = max(partial, key=lambda item: (item[0], item[1]))
        if overlap >= 0.5:
            return {**profile, "confidence": "compatible", "match_key": key}
    return None


def supported_system_names():
    return [profile["system"] for profile in TEMPLATE_PROFILES]
