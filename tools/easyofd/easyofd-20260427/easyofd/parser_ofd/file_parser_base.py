#!/usr/bin/env python
# -*- coding: utf-8 -*-
# PROJECT_NAME:  file_parser_base.py
# CREATE_TIME: 2025/3/28 11:43
# E_MAIL: renoyuan@foxmail.com
# AUTHOR: reno
# NOTE: base 解析器

import sys

sys.path.insert(0, "..")
import logging
import os
import traceback
import base64
import re
from typing import Any
from .parameter_parser import ParameterParser
logger = logging.getLogger("root")


class FileParserBase(object):
    """xml解析"""

    def __init__(self, xml_obj):
        assert xml_obj
        self.ofd_param = ParameterParser()
        self.xml_obj = xml_obj

    def recursion_ext(self, need_ext_obj, ext_list, key, parent_draw_param=""):
        """
        抽取需要xml要素
        need_ext_obj : xmltree
        ext_list: data container
        key: key
        """

        if isinstance(need_ext_obj, dict):
            current_draw_param = need_ext_obj.get("@DrawParam", parent_draw_param)
            need_ext_obj["@DrawParam"] = current_draw_param
            for k, v in need_ext_obj.items():
                if k == key:

                    if isinstance(v, (dict, str)):
                        if "@DrawParam" not in v and current_draw_param :
                            v["@DrawParam"] = current_draw_param
                        ext_list.append(v)
                    elif isinstance(v, list):
                        for item in v:
                            if isinstance(item, dict):
                                if "@DrawParam" not in item and current_draw_param is not None:
                                    item["@DrawParam"] = current_draw_param
                        
                        ext_list.extend(v)
                else:

                    if isinstance(v, dict):
                        self.recursion_ext(v, ext_list, key, parent_draw_param=current_draw_param)
                    elif isinstance(v, list):
                        for cell in v:
                            self.recursion_ext(cell, ext_list, key, parent_draw_param=current_draw_param)
                    else:

                        pass
        else:

            print(type(need_ext_obj))

