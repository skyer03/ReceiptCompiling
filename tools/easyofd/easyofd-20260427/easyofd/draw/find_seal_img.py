#!/usr/bin/env python
# -*- coding: utf-8 -*-
# PROJECT_NAME: easyofd read_seal_img
# CREATE_TIME: 2024/5/28 14:13
# E_MAIL: renoyuan@foxmail.com
# AUTHOR: renoyuan
# note: 根据 ASN.1 解析签章 拿到 签章图片

import base64
import io
from PIL import Image, UnidentifiedImageError
from pyasn1.codec.der.decoder import decode
from pyasn1.type import univ
from pyasn1.error import PyAsn1Error
from loguru import logger




class SealExtract(object):
    def __init__(self):
        pass

    def read_signed_value(self, path="", b64=""):
        """读取二进制或base64数据"""
        try:
            if b64:
                return base64.b64decode(b64)
            elif path:
                with open(path, 'rb') as file:
                    return file.read()
        except Exception as e:
            logger.warning(f"读取签名数据失败: {e}")
        return None

    def find_octet_strings(self, asn1_data, octet_strings: list):
        """
        【完整版递归】遍历所有 ASN.1 结构，提取所有 OctetString
        修复：遍历方式更稳定、支持所有嵌套类型
        """
        if asn1_data is None:
            return

        # 1. 匹配到 OctetString → 直接保存
        if isinstance(asn1_data, univ.OctetString):
            octet_strings.append(asn1_data)
            return

        # 2. 序列/集合 → 遍历所有子项
        try:
            if isinstance(asn1_data, (univ.Sequence, univ.Set, univ.SequenceOf, univ.SetOf)):
                for idx in range(len(asn1_data)):
                    try:
                        component = asn1_data.getComponentByPosition(idx)
                        self.find_octet_strings(component, octet_strings)
                    except Exception:
                        continue

            # 3. Choice 类型
            elif isinstance(asn1_data, univ.Choice):
                self.find_octet_strings(asn1_data.getComponent(), octet_strings)

            # 4. Any 类型 → 内部再次 ASN.1 解码（印章最常藏在这里）
            elif isinstance(asn1_data, univ.Any):
                try:
                    sub_data, _ = decode(asn1_data.asOctets())
                    self.find_octet_strings(sub_data, octet_strings)
                except PyAsn1Error:
                    pass

        except Exception as e:
            logger.debug(f"遍历ASN.1节点异常: {e}")

    def hex_to_image(self, binary_data, inx=0):
        """
        【优化】直接用二进制，不经过 hex 字符串，避免丢失数据
        """
        try:
            image_stream = io.BytesIO(binary_data)
            image = Image.open(image_stream)
            return image
        except UnidentifiedImageError:
            return None
        except Exception as e:
            logger.debug(f"图片解析异常: {e}")
            return None

    def __call__(self, path="", b64=""):
        img_list = []
        binary_data = self.read_signed_value(path=path, b64=b64)
        if not binary_data:
            return img_list

        # 解码 ASN.1
        try:
            decoded_data, _ = decode(binary_data)
        except PyAsn1Error as e:
            logger.warning(f"ASN.1 解码失败: {e}")
            return img_list

        # 提取所有 OctetString
        octet_strings = []
        self.find_octet_strings(decoded_data, octet_strings)

        # 尝试解析为图片（直接用二进制，不转16进制！）
        for octet_string in octet_strings:
            raw_bytes = bytes(octet_string)  # ✅ 最稳定的取值方式
            img = self.hex_to_image(raw_bytes)
            if img:
                img_list.append(img)

        return img_list

if __name__=="__main__":
    print(SealExtract()(r"F:\code\easyofd\test\1111_xml\Doc_0\Signs\Sign_0\SignedValue.dat" ))

