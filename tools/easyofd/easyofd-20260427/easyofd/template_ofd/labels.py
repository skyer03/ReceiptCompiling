"""
#!/usr/bin/env python
-*- coding: utf-8 -*-
PROJECT_NAME: F:\opensource\easyofd\easyofd\template_ofd
CREATE_TIME: 2026-04-24 
E_MAIL: renoyuan@foxmail.com
AUTHOR: reno 
note:  label
"""
class LabelBase:
    def __init__(self):
        self.id = ""
        self.value = ""

# 万能文本节点：自带标签名 + 值
class TextNodeLabel(LabelBase):
    def __init__(self, tag: str = "", text: str = ""):
        self.tag = tag    # 标签名：DocID, Author, CreationDate...
        self.text = text  # 文本内容
        
class OFDLabel(LabelBase):
    def __init__(self):
        self.doc_type = "OFD"    # DocType 属性
        self.version = "renoyuan"     # Version 属性
        self.doc_body = DocBody()# <ofd:DocBody>

class DocInfoLabel(LabelBase):
    def __init__(self):
        self.doc_id = ""       # <ofd:DocID>
        self.author = ""       # <ofd:Author>
        self.creation_date = "" # <ofd:CreationDate>
        self.mod_date = ""     # <ofd:ModDate>
        
class DocBodyLabel(LabelBase):
    def __init__(self):
        self.doc_info = DocInfo()   # <ofd:DocInfo>
        self.doc_root = ""          # <ofd:DocRoot>
        self.doc_root_link = ""          # <ofd:DocRoot>
        

        
# 主类：TextObject
class TextObject:
    def __init__(self):
        self.id = ""
        self.boundary = None  # Boundary类
        self.ctm= None      # CTM类
        self.font_id: str = ""
        self.size: float = 0.0
        self.alpha: int = 255
        
        # 子标签 → 全部变成成员变量
        self.fill_color = None       # FillColor
        self.cg_transform = None     # CGTransform
        self.text_code = None        # TextCode

class FillColor:
    def __init__(self):
        self.value = ""  # "170 160 165"

class CGTransform:
    def __init__(self):
        self.code_position = 0
        self.code_count = 0
        self.glyph_count = 0
        self.glyphs = ""  # 字形编号
        

class TextCode:
    def __init__(self):
        self.x = 0.0
        self.y = 0.0
        self.delta_x = ""
        self.content = ""  # 保密资料
        
# 1. 坐标边界类 (对应 Boundary 属性)
class Boundary:
    def __init__(self):
        self.x = 0.0
        self.y = 0.0
        self.w = 0.0
        self.h = 0.0

# 2. 坐标变换矩阵 (对应 CTM 属性)
class CTM:
    def __init__(self):
        self.a = 1.0    # X缩放
        self.b = 0.0    # X斜切
        self.c = 0.0    # Y斜切
        self.d = 1.0    # Y缩放
        self.e = 0.0    # X平移
        self.f = 0.0    # Y平移

# 3. 颜色类
class FillColor:
    def __init__(self):
        self.value = "" # RGB值 "170 160 165"

class StrokeColor:
    def __init__(self):
        self.value = ""

# 4. 字形映射
class CGTransform:
    def __init__(self):
        self.code_position = 0
        self.code_count = 0
        self.glyph_count = 0
        self.glyphs = "" # 字形索引序列

# 5. 文字内容
class TextCode:
    def __init__(self):
        self.x = 0.0
        self.y = 0.0
        self.delta_x = ""
        self.content = "" # 真实文字

# 🔥 主文本对象
class TextObject:
    def __init__(self):
        # 自身属性
        self.id = ""
        self.boundary = Boundary()
        self.ctm = CTM()
        self.font_id = ""
        self.size = 0.0
        self.alpha = 255
        
        # 子标签（嵌套Class）
        self.fill_color = FillColor()
        self.cg_transform = CGTransform()
        self.text_code = TextCode()


# 6. 路径数据
class AbbreviatedData:
    def __init__(self):
        self.value = "" # 绘图指令 M L C Z

# 🔥 主路径对象
class PathObject:
    def __init__(self):
        self.id = ""
        self.boundary = Boundary()
        self.ctm = CTM()
        self.line_width = 0.5
        self.alpha = 255
        
        # 子标签
        self.fill_color = FillColor()
        self.stroke_color = StrokeColor()
        self.abbreviated_data = AbbreviatedData()
        

# 7. 页面内容 (对应 Page_0/Content.xml)
class PageContent:
    def __init__(self):
        self.text_objects = []   # List<TextObject>
        self.path_objects = []   # List<PathObject>
        self.image_objects = []  # List<ImageObject>

# 8. 页面
class Page:
    def __init__(self):
        self.id = ""
        self.base_loc = ""
        self.physical_box = Boundary()
        self.content = PageContent()
        

# 9. 字体
class Font:
    def __init__(self):
        self.id = ""
        self.font_name = ""
        self.family = ""
        self.bold = False
        self.italic = False

# 10. 多媒体资源（图片）
class MultiMedia:
    def __init__(self):
        self.id = ""
        self.type = ""
        self.format = ""
        self.loc = ""

