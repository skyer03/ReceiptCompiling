#!/usr/bin/env python
# -*- coding: utf-8 -*-
# PROJECT_NAME:  file_publicres_parser.py
# CREATE_TIME: 2025/3/28 11:49
# E_MAIL: renoyuan@foxmail.com
# AUTHOR: reno
# NOTE: PublicResFileParser

from .file_parser_base import FileParserBase

class PublicResFileParser(FileParserBase):
    """
    Parser PublicRes 抽取里面 获取公共信息
    字体信息 ofd:Font
    绘制参数信息 ofd:DrawParams
    
    /xml_dir/Doc_0/PublicRes.xml
    """

    def normalize_font_name(self, font_name):
        """将字体名称规范化，例如 'Times New Roman Bold' -> 'TimesNewRoman-Bold'"""
        # 替换空格为无，并将样式（Bold/Italic等）用连字符连接
        if not isinstance(font_name,str):
            return ""
        normalized = font_name.replace(' ', '')
        # 处理常见的样式后缀
        for style in ['Bold', 'Italic', 'Regular', 'Light', 'Medium', ]:
            if style in normalized:
                normalized = normalized.replace(style, f'-{style}')

        # todo 特殊字体名规范 后续存在需要完善
        if normalized ==  "TimesNewRoman" :
            normalized = normalized.replace("TimesNewRoman","Times-Roman")
        return normalized

    def __call__(self):
        info = {"font": {}, "drawparams": {}}
        public_res_font: list = []
        public_res_drawparams: list = []
        public_res_font_key = "ofd:Font"
        public_res_drawparams_key = "ofd:DrawParam"
        self.recursion_ext(self.xml_obj, public_res_font, public_res_font_key)
        self.recursion_ext(self.xml_obj, public_res_drawparams, public_res_drawparams_key)

        if public_res_font:
            for i in public_res_font:
                info["font"][i.get("@ID")] = {
                    "FontName": self.normalize_font_name(i.get("@FontName")),
                    "FontNameORI": i.get("@FontName"),
                    "FamilyName": self.normalize_font_name(i.get("@FamilyName")),
                    "FamilyNameORI": i.get("@FamilyName"),
                    "Bold": i.get("@Bold"),
                    "Serif": i.get("@Serif"),
                    "FixedWidth": i.get("@FixedWidth"),
                    "FontFile": i.get("ofd:FontFile"),
                }
        print("public_res_drawparam", public_res_drawparams)
        if public_res_drawparams:
            for i in public_res_drawparams:
                info["drawparams"][i.get("@ID")] = {
                    
                    "LineWidth": i.get("@LineWidth"),
                    
                    "StrokeColor":{
                        "value":i.get("ofd:StrokeColor", {}).get('@Value', "").split(" "),
                        "color_space": i.get("ofd:StrokeColor", {}).get('@ColorSpace', "0"),
                            },
                    "FillColor":{
                        "value":i.get("ofd:FillColor", {}).get('@Value', "").split(" "),
                        "color_space": i.get("ofd:FillColor", {}).get('@ColorSpace', "0"),    
                            }
                    }
                    
        return info

