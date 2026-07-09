import os
import contextlib
import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
import tkinter as tk
import zipfile
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html import unescape
from io import BytesIO, StringIO
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw, ImageFont, ImageTk
from pypdf import PageObject, PdfReader, PdfWriter, Transformation
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader


APP_VERSION = "2.0.0"
APP_AUTHOR = "tc"
APP_TITLE = f"发票汇编整理申报工具 v{APP_VERSION} -by {APP_AUTHOR}"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
PDF_EXTS = {".pdf"}
OFD_EXTS = {".ofd"}
PAGE_SIZE = landscape(A4)
PAGE_WIDTH, PAGE_HEIGHT = PAGE_SIZE

# 页面版式微调区：下面这些数值都可以手动调整。
# 单位是 PDF 点（pt），A4 横版大约是 841.89 x 595.28。
HEADER_FONT_SIZE = 15  # 页眉字体大小；想更大就调高，比如 16。
HEADER_Y = PAGE_HEIGHT - 48  # 页眉文字位置；数值越小越靠下，越大越靠上。
HEADER_LINE_Y = PAGE_HEIGHT - 68  # 内容顶部参考位置；页眉下方不画线，但内容区域仍用它避开页眉。
HEADER_SIDE_INSET = 90  # 页眉左右文字距离页面边缘的距离；越大越往中间收。
PORTRAIT_SIDE_HEADER_X_OFFSET = 35  # 竖版 PDF 右侧页眉距离页面右边缘的距离；越大越往左。
PORTRAIT_SIDE_HEADER_TOP = 150  # 竖版 PDF 右侧上方页眉距离页面顶端的距离；越大越靠下。
PORTRAIT_SIDE_HEADER_BOTTOM = 150  # 竖版 PDF 右侧下方页眉距离页面底端的距离；越大越靠上。
PORTRAIT_SIDE_HEADER_MARGIN = 48  # 竖版 PDF 内容区右侧预留给页眉的宽度；越大右侧留白越多。
PORTRAIT_CONTENT_LEFT = 24  # 竖版 PDF 内容区左边距；越小原 PDF 越大。
PORTRAIT_CONTENT_VERTICAL_MARGIN = 24  # 竖版 PDF 上下边距；越小原 PDF 越大。
CONTENT_LEFT = 52  # 内容区域左边距；图片和 PDF 都会参考这个区域。
CONTENT_RIGHT_MARGIN = 52  # 内容区域右边距；越大右侧留白越多。
CONTENT_RIGHT = PAGE_WIDTH - CONTENT_RIGHT_MARGIN  # 横向 A4 默认内容右边界。
CONTENT_BOTTOM = 38  # 内容区域下边距。
CONTENT_TOP_OFFSET = PAGE_HEIGHT - (HEADER_LINE_Y - 20)  # 内容顶部距离页面顶端的距离；越大内容越靠下。
CONTENT_TOP = PAGE_HEIGHT - CONTENT_TOP_OFFSET  # 横向 A4 默认内容顶部。
PDF_SCALE_FACTOR = 0.90  # PDF 原页缩放比例；越小 PDF 越小，留白越多。
PDF_X_OFFSET = 34  # PDF 缩小后向右偏移量；越大越靠右，左侧留白越多。
IMAGE_GAP = 14  # 多张图片之间的间距；越大图片之间空隙越宽。
PREVIEW_PANEL_WIDTH = 440  # 右侧预览栏宽度；想让预览更大就调高。
PDF_OCR_DPI = 200  # 扫描 PDF / OFD 转换页 OCR 前的渲染分辨率；越高越准但越慢。

# 页眉纵向位置用横版 A4 做基准；竖版页面会自动保持同样的顶部距离。
HEADER_TOP_OFFSET = PAGE_HEIGHT - HEADER_Y
AMOUNT_KEYWORDS = ("价税合计", "合计金额", "小写", "报销金额", "金额合计", "总金额", "金额", "合计")
INVOICE_STRONG_MARKERS = (
    "价税合计",
    "合计金额",
    "小写",
    "开票日期",
    "购买方",
    "销售方",
    "税额",
    "税率",
    "发票代码",
    "发票号码",
    "电子发票",
    "普通发票",
    "专用发票",
)
PAYMENT_PROOF_MARKERS = (
    "支付成功",
    "退款记录",
    "交易单号",
    "商户单号",
    "收单机构",
    "支付方式",
    "当前状态",
    "已退款",
)
_rapidocr_engine = None
_rapidocr_error: str | None = None


def bundled_python() -> str:
    return sys.executable


def find_chinese_font() -> tuple[str, str]:
    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\arialuni.ttf",
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            pdfmetrics.registerFont(TTFont("InvoiceCJK", candidate))
            return "InvoiceCJK", candidate
    return "Helvetica", ""


FONT_NAME, FONT_PATH = find_chinese_font()


@dataclass
class InvoiceItem:
    path: Path
    amount: Decimal | None
    voucher_count: int
    pages: int
    status: str
    extracted_text: str = ""
    image_paths: list[Path] | None = None
    render_path: Path | None = None

    @property
    def is_pdf(self) -> bool:
        return self.path.suffix.lower() in PDF_EXTS

    @property
    def is_ofd(self) -> bool:
        return self.path.suffix.lower() in OFD_EXTS

    @property
    def is_document(self) -> bool:
        return self.is_pdf or self.is_ofd

    @property
    def is_image_page(self) -> bool:
        return self.image_paths is not None

    @property
    def display_name(self) -> str:
        if self.image_paths:
            if len(self.image_paths) == 1:
                return self.image_paths[0].name
            return f"图片页（{len(self.image_paths)}张）：{self.image_paths[0].name} 等"
        return self.path.name


def page_size_for_aspect(width: float, height: float) -> tuple[float, float]:
    return A4 if height >= width else PAGE_SIZE


def pdf_visible_box(page: PageObject) -> tuple[float, float, float, float]:
    # 一些发票的 MediaBox 是竖版 A4，但真正可见的 CropBox 是横版。
    # 页面方向和缩放必须优先按照 CropBox 判断。
    box = page.cropbox
    width = float(box.width)
    height = float(box.height)
    if width <= 0 or height <= 0:
        box = page.mediabox
        width = float(box.width)
        height = float(box.height)
    return float(box.left), float(box.bottom), width, height


def pdf_display_size(page: PageObject) -> tuple[float, float]:
    _left, _bottom, width, height = pdf_visible_box(page)
    if int(page.rotation or 0) % 180:
        return height, width
    return width, height


def total_voucher_count(items: list[InvoiceItem]) -> int:
    # “共几张”的自动值是所有列表项当前凭证张数之和，包含用户手动修改后的数值。
    return sum(max(0, item.voucher_count) for item in items)


def available_document_pages(item: InvoiceItem) -> int:
    if item.is_image_page or item.path.suffix.lower() in IMAGE_EXTS:
        return 1
    source_path = item.render_path or item.path
    try:
        return max(1, len(PdfReader(str(source_path)).pages))
    except Exception:
        return max(1, item.pages)


def content_bounds(page_width: float, page_height: float) -> tuple[float, float, float, float]:
    left = CONTENT_LEFT
    right = page_width - CONTENT_RIGHT_MARGIN
    bottom = CONTENT_BOTTOM
    top = page_height - CONTENT_TOP_OFFSET
    return left, right, bottom, top


def document_content_bounds(page_width: float, page_height: float) -> tuple[float, float, float, float]:
    if page_height > page_width:
        return (
            PORTRAIT_CONTENT_LEFT,
            page_width - PORTRAIT_SIDE_HEADER_MARGIN,
            PORTRAIT_CONTENT_VERTICAL_MARGIN,
            page_height - PORTRAIT_CONTENT_VERTICAL_MARGIN,
        )
    return content_bounds(page_width, page_height)


def header_y_for_page(page_height: float) -> float:
    return page_height - HEADER_TOP_OFFSET


def portrait_side_header_items(
    height: float, amount: Decimal | None, vouchers: int, page_no: int, total_pages_text: str
) -> list[tuple[str, float]]:
    return [
        (f"报销金额：{format_money(amount)}", height - PORTRAIT_SIDE_HEADER_TOP),
        (f"原始凭证张数：{vouchers}", height / 2),
        (f"第{page_no}页/共{total_pages_text or '  '}张", PORTRAIT_SIDE_HEADER_BOTTOM),
    ]


def is_single_portrait_image_page(item: InvoiceItem) -> bool:
    paths = item.image_paths or [item.path]
    if len(paths) != 1:
        return False
    try:
        with Image.open(paths[0]) as img:
            return img.height >= img.width
    except Exception:
        return False


def uses_portrait_side_header(item: InvoiceItem, page_width: float, page_height: float) -> bool:
    return page_height > page_width and (item.is_document or is_single_portrait_image_page(item))


def draw_portrait_side_header(
    c: canvas.Canvas, width: float, height: float, amount: Decimal | None, vouchers: int, page_no: int, total_pages_text: str
) -> None:
    x = width - PORTRAIT_SIDE_HEADER_X_OFFSET
    for text, center_y in portrait_side_header_items(height, amount, vouchers, page_no, total_pages_text):
        text_width = pdfmetrics.stringWidth(text, FONT_NAME, HEADER_FONT_SIZE)
        c.saveState()
        c.translate(x, center_y + text_width / 2)
        c.rotate(-90)
        c.drawString(0, 0, text)
        c.restoreState()


def preview_page_size_for_item(item: InvoiceItem) -> tuple[float, float]:
    if item.is_image_page or item.path.suffix.lower() in IMAGE_EXTS:
        return A4 if is_single_portrait_image_page(item) else PAGE_SIZE
    if item.is_document:
        source_path = item.render_path or item.path
        if source_path.suffix.lower() in PDF_EXTS and source_path.exists():
            try:
                first_page = PdfReader(str(source_path)).pages[0]
                source_w, source_h = pdf_display_size(first_page)
                return page_size_for_aspect(source_w, source_h)
            except Exception:
                pass
        return A4
    return PAGE_SIZE


def app_resource_dir() -> Path:
    # PyInstaller 打包后，内嵌资源会被释放到 sys._MEIPASS；源码运行时则使用当前脚本目录。
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


def bundled_ofd_converter_exe() -> Path | None:
    # OFD 转 PDF 工具放在 tools/ofd2pdf；打包时也会按这个目录结构内嵌。
    candidate = app_resource_dir() / "tools" / "ofd2pdf" / "Ofd2Pdf.exe"
    return candidate if candidate.exists() else None


def bundled_easyofd_dir() -> Path | None:
    candidate = app_resource_dir() / "tools" / "easyofd" / "easyofd-20260427"
    return candidate if candidate.exists() else None


def bundled_rapidocr_models_dir() -> Path | None:
    candidate = app_resource_dir() / "tools" / "rapidocr_models"
    return candidate if candidate.exists() else None


def normalize_amount(value: str | Decimal | int | float | None) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("￥", "").replace("¥", "")
    if not text:
        return None
    try:
        return Decimal(text).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def format_money(value: Decimal | None) -> str:
    if value is None:
        return ""
    return f"{value.quantize(Decimal('0.01'))}"


def default_output_filename() -> str:
    return f"发票汇编_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"


def _amount_candidates(text: str, allow_plain_integer: bool = False) -> list[Decimal]:
    cleaned = text.replace("，", ",").replace("．", ".").replace("。", ".")
    amounts: list[Decimal] = []
    pattern = re.compile(r"(?:CNY|RMB|人民币|[¥￥])?\s*([0-9][0-9,]*(?:\s*\.\s*[0-9]{1,2})?)", re.IGNORECASE)
    for match in pattern.finditer(cleaned):
        token = match.group(0).strip()
        value = match.group(1).replace(" ", "")
        has_decimal = "." in value
        has_currency = token.upper().startswith(("CNY", "RMB")) or token.startswith(("人民币", "¥", "￥"))
        if not allow_plain_integer and not has_decimal and not has_currency:
            continue
        amount = normalize_amount(value)
        if amount is not None and Decimal("0") < amount < Decimal("10000000"):
            amounts.append(amount)
    return amounts


def _keyword_amount(text: str) -> Decimal | None:
    lines = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]
    for index, line in enumerate(lines):
        compact_line = re.sub(r"\s+", "", line)
        if not any(keyword in compact_line for keyword in AMOUNT_KEYWORDS):
            continue
        window = "\n".join(lines[index : index + 2])
        amounts = _amount_candidates(window)
        if not amounts:
            amounts = _amount_candidates(window, allow_plain_integer=True)
        if amounts:
            return max(amounts)
    compact = re.sub(r"\s+", "", text)
    for keyword in AMOUNT_KEYWORDS:
        match = re.search(rf"{re.escape(keyword)}[：:（(]?(?:人民币)?[¥￥]?([0-9][0-9,]*\.?[0-9]{{0,2}})", compact)
        if not match:
            continue
        amount = normalize_amount(match.group(1))
        if amount is not None and Decimal("0") < amount < Decimal("10000000"):
            return amount
    return None


def extract_amount(text: str) -> Decimal | None:
    keyword_amount = _keyword_amount(text)
    if keyword_amount is not None:
        return keyword_amount

    compact = re.sub(r"\s+", "", text)
    patterns = [
        r"(?:CNY|RMB|人民币|¥|￥)\s*([0-9]{1,7}(?:,[0-9]{3})*\.[0-9]{2})",
        r"[¥￥]\s*([0-9][0-9,]*\.?[0-9]{0,2})",
        r"\b([0-9]{1,7}(?:,[0-9]{3})*\.[0-9]{2})\b",
    ]
    amounts: list[Decimal] = []
    for pattern in patterns:
        for match in re.findall(pattern, compact):
            amount = normalize_amount(match)
            if amount is not None and Decimal("0") < amount < Decimal("10000000"):
                amounts.append(amount)
        if amounts:
            break
    return max(amounts) if amounts else None


def _compact_ocr_text(text: str) -> str:
    return re.sub(r"\s+", "", text)


def is_payment_proof_text(text: str) -> bool:
    compact = _compact_ocr_text(text)
    return any(marker in compact for marker in PAYMENT_PROOF_MARKERS)


def is_invoice_like_image_text(text: str) -> bool:
    compact = _compact_ocr_text(text)
    if any(marker in compact for marker in INVOICE_STRONG_MARKERS):
        return True
    return "发票" in compact and not is_payment_proof_text(text)


def extract_image_amount(text: str) -> Decimal | None:
    if not is_invoice_like_image_text(text):
        return None
    return extract_amount(text)


def _rapidocr_config_path() -> Path | None:
    models_dir = bundled_rapidocr_models_dir()
    if not models_dir:
        return None
    for name in ("rapidocr.yaml", "rapidocr.yml", "config.yaml", "config.yml"):
        candidate = models_dir / name
        if candidate.exists():
            return candidate
    return None


def _get_rapidocr_engine():
    global _rapidocr_engine, _rapidocr_error
    if _rapidocr_engine is not None:
        return _rapidocr_engine, ""
    if _rapidocr_error:
        return None, _rapidocr_error
    try:
        from rapidocr import RapidOCR  # type: ignore
    except Exception as exc:
        _rapidocr_error = f"OCR 不可用：未安装 rapidocr / onnxruntime（{exc}）"
        return None, _rapidocr_error

    try:
        config_path = _rapidocr_config_path()
        if config_path:
            _rapidocr_engine = RapidOCR(config_path=str(config_path))
        else:
            _rapidocr_engine = RapidOCR()
        return _rapidocr_engine, ""
    except Exception as exc:
        _rapidocr_error = f"OCR 初始化失败：{exc}"
        return None, _rapidocr_error


def _rapidocr_text_lines(result) -> list[str]:
    if result is None:
        return []
    for attr in ("txts", "texts", "rec_texts"):
        values = getattr(result, attr, None)
        if values:
            return [str(value).strip() for value in values if str(value).strip()]
    if isinstance(result, dict):
        for key in ("txts", "texts", "rec_texts"):
            values = result.get(key)
            if values:
                return [str(value).strip() for value in values if str(value).strip()]
    if isinstance(result, tuple) and result:
        return _rapidocr_text_lines(result[0])
    if isinstance(result, list):
        lines: list[str] = []
        for item in result:
            if hasattr(item, "text"):
                text = str(item.text).strip()
                if text:
                    lines.append(text)
            elif isinstance(item, (list, tuple)):
                for value in item:
                    if isinstance(value, str) and value.strip():
                        lines.append(value.strip())
                        break
            elif isinstance(item, str) and item.strip():
                lines.append(item.strip())
        return lines
    return []


def try_image_ocr(path: Path) -> tuple[str, str]:
    engine, error = _get_rapidocr_engine()
    if engine is None:
        return "", f"{error}，需手动核对"

    try:
        result = engine(str(path))
        lines = _rapidocr_text_lines(result)
        text = "\n".join(lines)
        return text, "图片 OCR 完成" if text.strip() else "图片 OCR 无文字"
    except Exception as exc:
        return "", f"图片 OCR 失败：{exc}"


def _load_pymupdf():
    try:
        import pymupdf  # type: ignore

        return pymupdf, ""
    except Exception as first_exc:
        try:
            import fitz as pymupdf  # type: ignore

            return pymupdf, ""
        except Exception as second_exc:
            return None, f"PDF OCR 不可用：未安装 PyMuPDF（{first_exc}; {second_exc}）"


def ocr_pdf_pages(path: Path) -> tuple[str, str]:
    engine, error = _get_rapidocr_engine()
    if engine is None:
        return "", f"{error}，需手动核对"

    pymupdf, import_error = _load_pymupdf()
    if pymupdf is None:
        return "", f"{import_error}，需手动核对"

    texts: list[str] = []
    page_failures = 0
    try:
        with pymupdf.open(str(path)) as document:
            for page in document:
                tmp_path = ""
                try:
                    pixmap = page.get_pixmap(dpi=PDF_OCR_DPI, alpha=False)
                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
                    tmp_path = tmp.name
                    tmp.close()
                    pixmap.save(tmp_path)
                    page_text, _status = try_image_ocr(Path(tmp_path))
                    if page_text.strip():
                        texts.append(page_text)
                except Exception:
                    page_failures += 1
                finally:
                    if tmp_path:
                        with contextlib.suppress(Exception):
                            Path(tmp_path).unlink(missing_ok=True)
    except Exception as exc:
        return "", f"PDF OCR 失败：{exc}"

    if texts:
        if page_failures:
            return "\n".join(texts), "PDF OCR 部分完成，需核对金额"
        return "\n".join(texts), "PDF OCR 完成"
    if page_failures:
        return "", "PDF OCR 未完成，需手动核对金额"
    return "", "PDF OCR 无文字，需手动核对金额"


def read_pdf_text_and_pages(path: Path) -> tuple[str, int, str]:
    try:
        reader = PdfReader(str(path))
        texts = []
        for page in reader.pages:
            try:
                texts.append(page.extract_text() or "")
            except Exception:
                texts.append("")
        text = "\n".join(texts)
        status = "PDF 文字识别完成" if text.strip() else "扫描 PDF，需手动核对金额"
        return text, len(reader.pages), status
    except Exception as exc:
        return "", 0, f"PDF 读取失败：{exc}"


def read_ofd_text(path: Path) -> tuple[str, str]:
    try:
        texts: list[str] = []
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if not name.lower().endswith((".xml", ".txt")):
                    continue
                raw = zf.read(name).decode("utf-8", errors="ignore")
                plain = re.sub(r"<[^>]+>", " ", raw)
                texts.append(unescape(plain))
        text = re.sub(r"\s+", " ", "\n".join(texts)).strip()
        if text:
            return text, "OFD 文字识别完成"
        return "", "OFD 已添加，未提取到文字，需手动核对金额"
    except zipfile.BadZipFile:
        return "", "OFD 读取失败：文件不是可解析的 OFD 包"
    except Exception as exc:
        return "", f"OFD 读取失败：{exc}"


def xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_ofd_box(value: str | None) -> tuple[float, float, float, float] | None:
    if not value:
        return None
    parts = value.replace(",", " ").split()
    if len(parts) < 4:
        return None
    try:
        return tuple(float(part) for part in parts[:4])  # type: ignore[return-value]
    except ValueError:
        return None


def ofd_media_map(zf: zipfile.ZipFile, doc_dir: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    res_path = f"{doc_dir}/DocumentRes.xml"
    if res_path not in zf.namelist():
        return mapping
    try:
        root = ET.fromstring(zf.read(res_path))
    except Exception:
        return mapping
    base_loc = root.attrib.get("BaseLoc", "Res")
    for node in root.iter():
        if xml_local_name(node.tag) != "MultiMedia":
            continue
        media_id = node.attrib.get("ID")
        media_file = None
        for child in node:
            if xml_local_name(child.tag) == "MediaFile" and child.text:
                media_file = child.text.strip()
                break
        if media_id and media_file:
            mapping[media_id] = f"{doc_dir}/{base_loc}/{media_file}".replace("\\", "/")
    return mapping


def draw_simple_ofd_content(c: canvas.Canvas, zf: zipfile.ZipFile, xml_path: str, page_height: float, unit: float, media: dict[str, str]) -> None:
    try:
        root = ET.fromstring(zf.read(xml_path))
    except Exception:
        return

    for node in root.iter():
        name = xml_local_name(node.tag)
        box = parse_ofd_box(node.attrib.get("Boundary"))
        if name == "PathObject" and box:
            x, y, _w, _h = box
            data = ""
            for child in node:
                if xml_local_name(child.tag) == "AbbreviatedData" and child.text:
                    data = child.text
                    break
            numbers = [float(part) for part in re.findall(r"-?\d+(?:\.\d+)?", data)]
            if len(numbers) >= 4:
                x1, y1, x2, y2 = numbers[:4]
                c.setStrokeColor(colors.black)
                c.setLineWidth(0.35)
                c.line((x + x1) * unit, page_height - (y + y1) * unit, (x + x2) * unit, page_height - (y + y2) * unit)

        elif name == "TextObject" and box:
            x, y, _w, _h = box
            size = float(node.attrib.get("Size", "3") or 3) * unit
            c.setFont(FONT_NAME, max(4, size))
            fill_color = colors.black
            for child in node:
                if xml_local_name(child.tag) == "FillColor":
                    parts = child.attrib.get("Value", "").split()
                    if len(parts) >= 3:
                        try:
                            fill_color = colors.Color(int(parts[0]) / 255, int(parts[1]) / 255, int(parts[2]) / 255)
                        except Exception:
                            fill_color = colors.black
            c.setFillColor(fill_color)
            for child in node:
                if xml_local_name(child.tag) != "TextCode":
                    continue
                text = "".join(child.itertext()).strip()
                if not text:
                    continue
                tx = float(child.attrib.get("X", "0") or 0)
                ty = float(child.attrib.get("Y", "0") or 0)
                c.drawString((x + tx) * unit, page_height - (y + ty) * unit, text)

        elif name == "ImageObject" and box:
            resource_id = node.attrib.get("ResourceID")
            image_path = media.get(resource_id or "")
            if not image_path or image_path not in zf.namelist():
                continue
            x, y, w, h = box
            try:
                image = ImageReader(BytesIO(zf.read(image_path)))
                c.drawImage(image, x * unit, page_height - (y + h) * unit, width=w * unit, height=h * unit, mask="auto")
            except Exception:
                continue


def render_simple_ofd_to_pdf(path: Path) -> tuple[Path | None, str]:
    try:
        with zipfile.ZipFile(path) as zf:
            doc_xml_path = next((name for name in zf.namelist() if name.endswith("Document.xml")), "")
            if not doc_xml_path:
                return None, "OFD 简易渲染失败：未找到 Document.xml"
            doc_dir = str(Path(doc_xml_path).parent).replace("\\", "/")
            doc_root = ET.fromstring(zf.read(doc_xml_path))
            physical_box = None
            page_paths: list[str] = []
            template_paths: list[str] = []
            for node in doc_root.iter():
                local = xml_local_name(node.tag)
                if local == "PhysicalBox" and node.text and physical_box is None:
                    physical_box = parse_ofd_box(node.text)
                elif local == "TemplatePage":
                    base_loc = node.attrib.get("BaseLoc")
                    if base_loc:
                        template_paths.append(f"{doc_dir}/{base_loc}".replace("\\", "/"))
                elif local == "Page":
                    base_loc = node.attrib.get("BaseLoc")
                    if base_loc:
                        page_paths.append(f"{doc_dir}/{base_loc}".replace("\\", "/"))
            if not physical_box or not page_paths:
                return None, "OFD 简易渲染失败：页面结构不完整"

            _x, _y, page_w_mm, page_h_mm = physical_box
            unit = 72 / 25.4
            page_w = page_w_mm * unit
            page_h = page_h_mm * unit
            output = Path(tempfile.NamedTemporaryFile(delete=False, suffix=".pdf").name)
            c = canvas.Canvas(str(output), pagesize=(page_w, page_h))
            media = ofd_media_map(zf, doc_dir)
            for page_index, page_path in enumerate(page_paths):
                if page_index:
                    c.showPage()
                for template_path in template_paths:
                    if template_path in zf.namelist():
                        draw_simple_ofd_content(c, zf, template_path, page_h, unit, media)
                draw_simple_ofd_content(c, zf, page_path, page_h, unit, media)
            c.save()
            if output.exists() and output.stat().st_size > 0:
                return output, "OFD 内嵌工具不支持，已使用简易渲染生成 PDF"
            return None, "OFD 简易渲染失败：未生成 PDF"
    except Exception as exc:
        return None, f"OFD 简易渲染失败：{exc}"


class _QuietLogger:
    def __getattr__(self, _name: str):
        return lambda *args, **kwargs: None


def _easyofd_xml_key(tag: str) -> str:
    if tag.startswith("{http://www.ofdspec.org/2016}"):
        return "ofd:" + tag.rsplit("}", 1)[-1]
    return tag.rsplit("}", 1)[-1]


def _easyofd_xml_convert(node: ET.Element):
    data: dict[str, object] = {f"@{key}": value for key, value in node.attrib.items()}
    children: dict[str, list[object]] = {}
    for child in list(node):
        children.setdefault(_easyofd_xml_key(child.tag), []).append(_easyofd_xml_convert(child))
    for key, values in children.items():
        data[key] = values[0] if len(values) == 1 else values
    text = (node.text or "").strip()
    if text:
        if data:
            data["#text"] = text
        else:
            return text
    return data


def _install_easyofd_compat_modules() -> None:
    if importlib.util.find_spec("loguru") is None:
        sys.modules["loguru"] = type(sys)("loguru")
        sys.modules["loguru"].logger = _QuietLogger()  # type: ignore[attr-defined]

    if importlib.util.find_spec("fitz") is None:
        sys.modules["fitz"] = type(sys)("fitz")

    if importlib.util.find_spec("xmltodict") is None:
        xmltodict_module = type(sys)("xmltodict")

        def parse(xml_text: str):
            root = ET.fromstring(xml_text)
            return {_easyofd_xml_key(root.tag): _easyofd_xml_convert(root)}

        xmltodict_module.parse = parse  # type: ignore[attr-defined]
        xmltodict_module.unparse = lambda *args, **kwargs: ""  # type: ignore[attr-defined]
        sys.modules["xmltodict"] = xmltodict_module

    if importlib.util.find_spec("fontTools") is None:
        font_tools = type(sys)("fontTools")
        tt_lib = type(sys)("fontTools.ttLib")
        pens = type(sys)("fontTools.pens")
        base_pen = type(sys)("fontTools.pens.basePen")

        class DummyTTFont:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def getGlyphSet(self):
                return {}

        class DummyBasePen:
            pass

        tt_lib.TTFont = DummyTTFont  # type: ignore[attr-defined]
        base_pen.BasePen = DummyBasePen  # type: ignore[attr-defined]
        sys.modules["fontTools"] = font_tools
        sys.modules["fontTools.ttLib"] = tt_lib
        sys.modules["fontTools.pens"] = pens
        sys.modules["fontTools.pens.basePen"] = base_pen

    if importlib.util.find_spec("pyasn1") is None:
        for name in [
            "pyasn1",
            "pyasn1.codec",
            "pyasn1.codec.der",
            "pyasn1.codec.der.decoder",
            "pyasn1.type",
            "pyasn1.type.univ",
            "pyasn1.error",
        ]:
            sys.modules[name] = type(sys)(name)

        def decode(*args, **kwargs):
            raise Exception("pyasn1 unavailable")

        class PyAsn1Error(Exception):
            pass

        sys.modules["pyasn1.codec.der.decoder"].decode = decode  # type: ignore[attr-defined]
        sys.modules["pyasn1.error"].PyAsn1Error = PyAsn1Error  # type: ignore[attr-defined]


def convert_ofd_with_easyofd(path: Path) -> tuple[Path | None, str]:
    easyofd_dir = bundled_easyofd_dir()
    try:
        _install_easyofd_compat_modules()
        if easyofd_dir:
            easyofd_path = str(easyofd_dir)
        else:
            easyofd_path = ""
        if easyofd_path and easyofd_path not in sys.path:
            sys.path.insert(0, easyofd_path)
        from easyofd.ofd import OFD  # type: ignore

        ofd = OFD()
        with contextlib.redirect_stdout(StringIO()), contextlib.redirect_stderr(StringIO()):
            ofd.read(str(path), fmt="path")
            pdf_bytes = ofd.to_pdf()
        try:
            ofd.del_data()
        except Exception:
            pass
        output = Path(tempfile.NamedTemporaryFile(delete=False, suffix=".pdf").name)
        output.write_bytes(pdf_bytes)
        if output.exists() and output.stat().st_size > 0:
            return output, "OFD 已通过 easyofd 转换为 PDF"
        return None, "easyofd 未生成 PDF"
    except Exception as exc:
        return None, f"easyofd 转换失败：{exc}"


def convert_ofd_to_pdf(path: Path) -> tuple[Path | None, str]:
    output = Path(tempfile.NamedTemporaryFile(delete=False, suffix=".pdf").name)

    bundled_converter = bundled_ofd_converter_exe()
    if bundled_converter:
        powershell = shutil.which("powershell") or shutil.which("powershell.exe")
        if not powershell:
            easyofd_path, easyofd_status = convert_ofd_with_easyofd(path)
            if easyofd_path:
                return easyofd_path, easyofd_status
            return render_simple_ofd_to_pdf(path)
        script = r"""param(
    [string]$tool,
    [string]$inputPath,
    [string]$outputPath
)
$ErrorActionPreference = 'Stop'
$toolDir = Split-Path -Parent $tool
[System.IO.Directory]::SetCurrentDirectory($toolDir)
Get-ChildItem -LiteralPath $toolDir -Filter '*.dll' | ForEach-Object {
    [System.Reflection.Assembly]::LoadFile($_.FullName) | Out-Null
}
$assembly = [System.Reflection.Assembly]::LoadFile($tool)
$converterType = $assembly.GetType('Ofd2Pdf.Converter')
$converter = [System.Activator]::CreateInstance($converterType)
$result = $converter.ConvertToPdf($inputPath, $outputPath)
if ($result.ToString() -eq 'Successful' -and (Test-Path $outputPath)) { exit 0 }
Write-Error ("OFD convert failed: " + $result.ToString())
exit 2
"""
        script_path = Path(tempfile.NamedTemporaryFile(delete=False, suffix=".ps1").name)
        script_path.write_text(script, encoding="utf-8")
        try:
            subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-STA",
                    "-File",
                    str(script_path),
                    "-tool",
                    str(bundled_converter),
                    "-inputPath",
                    str(path),
                    "-outputPath",
                    str(output),
                ],
                check=True,
                capture_output=True,
                timeout=90,
                cwd=str(bundled_converter.parent),
            )
            if output.exists() and output.stat().st_size > 0:
                return output, "OFD 已通过内嵌工具转换为 PDF"
        except subprocess.CalledProcessError:
            try:
                output.unlink(missing_ok=True)
            except Exception:
                pass
            easyofd_path, easyofd_status = convert_ofd_with_easyofd(path)
            if easyofd_path:
                return easyofd_path, easyofd_status
            return render_simple_ofd_to_pdf(path)
        except Exception as exc:
            try:
                output.unlink(missing_ok=True)
            except Exception:
                pass
            easyofd_path, easyofd_status = convert_ofd_with_easyofd(path)
            if easyofd_path:
                return easyofd_path, easyofd_status
            simple_path, simple_status = render_simple_ofd_to_pdf(path)
            if simple_path:
                return simple_path, simple_status
            return None, f"OFD 内嵌转换失败，生成时会放入提示页：{exc}"
        finally:
            try:
                script_path.unlink(missing_ok=True)
            except Exception:
                pass

    converter = shutil.which("ofd2pdf")
    if not converter:
        return None, "未检测到 OFD 转 PDF 工具，生成时会放入提示页"

    commands = [
        [converter, str(path), str(output)],
        [converter, "-i", str(path), "-o", str(output)],
    ]
    last_error = ""
    for command in commands:
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=60)
            if output.exists() and output.stat().st_size > 0:
                return output, "OFD 已转换为 PDF，可按 PDF 方式汇编"
        except Exception as exc:
            last_error = str(exc)
    try:
        output.unlink(missing_ok=True)
    except Exception:
        pass
    easyofd_path, easyofd_status = convert_ofd_with_easyofd(path)
    if easyofd_path:
        return easyofd_path, easyofd_status
    simple_path, simple_status = render_simple_ofd_to_pdf(path)
    if simple_path:
        return simple_path, simple_status
    return None, f"OFD 转 PDF 失败，生成时会放入提示页：{last_error}"


def inspect_file(path: Path) -> InvoiceItem:
    suffix = path.suffix.lower()
    if suffix in PDF_EXTS:
        text, pages, status = read_pdf_text_and_pages(path)
        amount = extract_amount(text)
        if amount is None:
            ocr_text, ocr_status = ocr_pdf_pages(path)
            if ocr_text.strip():
                text = "\n".join(part for part in (text, ocr_text) if part.strip())
                amount = extract_amount(text)
            status = f"{status}；{ocr_status}"
        return InvoiceItem(path, amount, max(pages, 1), max(pages, 1), status, text)
    if suffix in OFD_EXTS:
        text, status = read_ofd_text(path)
        render_path, convert_status = convert_ofd_to_pdf(path)
        status_parts = [status, convert_status]
        pages = 1
        if render_path:
            pdf_text, pages, _pdf_status = read_pdf_text_and_pages(render_path)
            if pdf_text.strip():
                text = "\n".join(part for part in (text, pdf_text) if part.strip())
        amount = extract_amount(text)
        if amount is None and render_path:
            ocr_text, ocr_status = ocr_pdf_pages(render_path)
            status_parts.append(ocr_status)
            if ocr_text.strip():
                text = "\n".join(part for part in (text, ocr_text) if part.strip())
                amount = extract_amount(text)
        return InvoiceItem(path, amount, max(pages, 1), max(pages, 1), "；".join(status_parts), text, render_path=render_path)
    if suffix in IMAGE_EXTS:
        text, status = try_image_ocr(path)
        amount = extract_image_amount(text)
        if text.strip() and amount is None and not is_invoice_like_image_text(text):
            status = "图片未识别为发票，金额可留空或手动填写"
        return InvoiceItem(path, amount, 1, 1, status, text)
    return InvoiceItem(path, None, 1, 1, "不支持的文件类型", "")


def make_header_overlay(width: float, height: float, amount: Decimal | None, vouchers: int, page_no: int, total_pages_text: str) -> str:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.close()
    header_y = header_y_for_page(height)
    c = canvas.Canvas(tmp.name, pagesize=(width, height))
    c.setFont(FONT_NAME, HEADER_FONT_SIZE)
    c.setFillColor(colors.black)
    if height > width:
        draw_portrait_side_header(c, width, height, amount, vouchers, page_no, total_pages_text)
    else:
        c.drawString(HEADER_SIDE_INSET, header_y, f"报销金额：{format_money(amount)}")
        c.drawCentredString(width / 2, header_y, f"原始凭证张数：{vouchers}")
        c.drawRightString(width - HEADER_SIDE_INSET, header_y, f"第{page_no}页/共{total_pages_text or '  '}张")
    c.save()
    return tmp.name


def add_header_to_page(page: PageObject, amount: Decimal | None, vouchers: int, page_no: int, total_pages_text: str) -> None:
    page_width = float(page.mediabox.width)
    page_height = float(page.mediabox.height)
    overlay_path = make_header_overlay(page_width, page_height, amount, vouchers, page_no, total_pages_text)
    overlay = PdfReader(overlay_path).pages[0]
    page.merge_page(overlay)
    os.unlink(overlay_path)


def add_pdf_pages(writer: PdfWriter, item: InvoiceItem, amount: Decimal | None, vouchers: int, start_page: int, total_pages_text: str) -> int:
    source_path = item.render_path or item.path
    reader = PdfReader(str(source_path))
    page_no = start_page
    page_limit = min(max(1, item.pages), len(reader.pages))
    for source_index, source_page in enumerate(reader.pages):
        if source_index >= page_limit:
            break
        # 规范化 PDF 自带的 /Rotate 标记，只保留文件原本的视觉方向，不额外旋转页面。
        if source_page.rotation:
            source_page.transfer_rotation_to_content()
        source_left, source_bottom, source_w, source_h = pdf_visible_box(source_page)
        target_w, target_h = page_size_for_aspect(source_w, source_h)
        content_left, content_right, content_bottom, content_top = document_content_bounds(target_w, target_h)
        content_w = content_right - content_left
        content_h = content_top - content_bottom
        scale_factor = 0.98 if target_h > target_w else PDF_SCALE_FACTOR
        scale = min(content_w / source_w, content_h / source_h) * scale_factor
        draw_w = source_w * scale
        draw_h = source_h * scale
        centered_x = content_left + (content_w - draw_w) / 2
        x = min(centered_x + PDF_X_OFFSET, content_right - draw_w)
        y = content_bottom + (content_h - draw_h) / 2
        page = PageObject.create_blank_page(width=target_w, height=target_h)
        transform = Transformation().translate(-source_left, -source_bottom).scale(scale).translate(x, y)
        page.merge_transformed_page(source_page, transform)
        add_header_to_page(page, amount, vouchers, page_no, total_pages_text)
        writer.add_page(page)
        page_no += 1
    return page_no


def add_ofd_notice_page(writer: PdfWriter, item: InvoiceItem, page_no: int, total_pages_text: str) -> int:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.close()
    width, height = A4
    c = canvas.Canvas(tmp.name, pagesize=A4)
    c.setFont(FONT_NAME, HEADER_FONT_SIZE)
    header_y = header_y_for_page(height)
    c.drawString(HEADER_SIDE_INSET, header_y, f"报销金额：{format_money(item.amount)}")
    c.drawCentredString(width / 2, header_y, f"原始凭证张数：{item.voucher_count}")
    c.drawRightString(width - HEADER_SIDE_INSET, header_y, f"第{page_no}页/共{total_pages_text or '   '}张")
    c.setFont(FONT_NAME, 13)
    c.drawCentredString(width / 2, height / 2 + 24, "OFD 文件已识别，但当前电脑未完成 OFD 转 PDF")
    c.setFont(FONT_NAME, 10)
    c.drawCentredString(width / 2, height / 2, item.path.name)
    c.drawCentredString(width / 2, height / 2 - 22, "如需完整嵌入版式，请安装本地 ofd2pdf 转换工具后重新添加。")
    c.save()
    writer.add_page(PdfReader(tmp.name).pages[0])
    os.unlink(tmp.name)
    return page_no + 1


def grid_for_count(count: int) -> tuple[int, int]:
    if count <= 1:
        return 1, 1
    if count <= 9:
        return count, 1
    return 9, 1


def add_image_page(writer: PdfWriter, item: InvoiceItem, amount: Decimal | None, vouchers: int, page_no: int, total_pages_text: str) -> int:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.close()
    paths = item.image_paths or [item.path]
    width, height = A4 if is_single_portrait_image_page(item) else PAGE_SIZE
    side_header = uses_portrait_side_header(item, width, height)
    content_left, content_right, content_bottom, content_top = document_content_bounds(width, height) if side_header else content_bounds(width, height)
    cols, rows = grid_for_count(len(paths))
    gap = IMAGE_GAP
    content_w = content_right - content_left
    content_h = content_top - content_bottom
    cell_w = (content_w - gap * (cols - 1)) / cols
    cell_h = (content_h - gap * (rows - 1)) / rows

    c = canvas.Canvas(tmp.name, pagesize=(width, height))
    c.setFont(FONT_NAME, HEADER_FONT_SIZE)
    if side_header:
        draw_portrait_side_header(c, width, height, amount, vouchers, page_no, total_pages_text)
    else:
        header_y = header_y_for_page(height)
        c.drawString(HEADER_SIDE_INSET, header_y, f"报销金额：{format_money(amount)}")
        c.drawCentredString(width / 2, header_y, f"原始凭证张数：{vouchers}")
        c.drawRightString(width - HEADER_SIDE_INSET, header_y, f"第{page_no}页/共{total_pages_text or '   '}张")
    for index, path in enumerate(paths[:9]):
        row = index // cols
        col = index % cols
        cell_x = content_left + col * (cell_w + gap)
        cell_y = content_top - (row + 1) * cell_h - row * gap
        with Image.open(path) as img:
            img_w, img_h = img.size
        scale = min(cell_w / img_w, cell_h / img_h)
        draw_w = img_w * scale
        draw_h = img_h * scale
        x = cell_x + (cell_w - draw_w) / 2
        y = cell_y + (cell_h - draw_h) / 2
        c.drawImage(str(path), x, y, width=draw_w, height=draw_h, preserveAspectRatio=True, anchor="c")
    c.save()

    writer.add_page(PdfReader(tmp.name).pages[0])
    os.unlink(tmp.name)
    return page_no + 1


def compile_pdf(items: list[InvoiceItem], output_path: Path, total_pages_text: str) -> None:
    writer = PdfWriter()
    page_no = 1
    for item in items:
        suffix = item.path.suffix.lower()
        if item.is_ofd and item.render_path is None:
            page_no = add_ofd_notice_page(writer, item, page_no, total_pages_text)
        elif item.is_document:
            page_no = add_pdf_pages(writer, item, item.amount, item.voucher_count, page_no, total_pages_text)
        elif item.is_image_page or suffix in IMAGE_EXTS:
            page_no = add_image_page(writer, item, item.amount, item.voucher_count, page_no, total_pages_text)

    with open(output_path, "wb") as fp:
        writer.write(fp)


class InvoiceApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1360x780")
        self.minsize(1180, 680)
        self.configure(bg="#eef2f5")
        self.items: list[InvoiceItem] = []
        self.total_pages_text_var = tk.StringVar()
        self.total_pages_user_edited = False
        self._updating_total_pages_text = False
        self.total_pages_text_var.trace_add("write", self._on_total_pages_text_changed)
        self.preview_photo: ImageTk.PhotoImage | None = None
        self._drag_row_index: int | None = None
        self._build_ui()
        self._refresh_summary()

    def _make_button(self, parent: tk.Widget, text: str, command, primary: bool = False, accent: bool = False) -> tk.Button:
        bg = "#0f766e" if primary else "#ecfdf5" if accent else "#ffffff"
        fg = "#ffffff" if primary else "#0f766e" if accent else "#334155"
        border = "#0f766e" if primary else "#99f6e4" if accent else "#cbd5e1"
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=bg,
            activeforeground=fg,
            relief=tk.FLAT,
            bd=0,
            highlightthickness=1,
            highlightbackground=border,
            font=("Microsoft YaHei UI", 10),
            cursor="hand2",
            padx=12,
            pady=8,
        )

    def _make_card(self, parent: tk.Widget, title: str, variable: tk.StringVar, width: int) -> tk.Frame:
        card = tk.Frame(parent, bg="#ffffff", highlightthickness=1, highlightbackground="#d7dee8", width=width, height=88)
        card.pack(side=tk.LEFT, padx=(0, 14))
        card.pack_propagate(False)
        tk.Label(card, text=title, bg="#ffffff", fg="#697386", font=("Microsoft YaHei UI", 9)).pack(anchor=tk.W, padx=22, pady=(14, 0))
        tk.Label(card, textvariable=variable, bg="#ffffff", fg="#172033", font=("Microsoft YaHei UI", 20, "bold")).pack(anchor=tk.W, padx=22, pady=(6, 0))
        return card

    def _panel_title(self, parent: tk.Widget, text: str) -> None:
        tk.Label(parent, text=text, bg="#ffffff", fg="#172033", font=("Microsoft YaHei UI", 11, "bold")).pack(anchor=tk.W, padx=22, pady=(18, 12))

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.configure("Treeview", rowheight=42, font=("Microsoft YaHei UI", 10), background="#ffffff", fieldbackground="#ffffff", foreground="#172033")
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"), foreground="#334155")
        style.map("Treeview", background=[("selected", "#e0f2fe")], foreground=[("selected", "#0f172a")])

        root = tk.Frame(self, bg="#eef2f5", padx=22, pady=18)
        root.pack(fill=tk.BOTH, expand=True)

        header = tk.Frame(root, bg="#eef2f5")
        header.pack(fill=tk.X)
        tk.Label(header, text="发票汇编", bg="#eef2f5", fg="#172033", font=("Microsoft YaHei UI", 22, "bold")).pack(side=tk.LEFT)
        tk.Label(header, text="本地处理 · 横向 A4 · 实时预览", bg="#eef2f5", fg="#697386", font=("Microsoft YaHei UI", 10)).pack(side=tk.LEFT, padx=(16, 0), pady=(10, 0))

        top = tk.Frame(root, bg="#eef2f5")
        top.pack(fill=tk.X, pady=(20, 18))
        self.total_amount_var = tk.StringVar(value="0.00")
        self.total_vouchers_var = tk.StringVar(value="0")
        self.total_pages_var = tk.StringVar(value="0")
        self._make_card(top, "合计报销金额", self.total_amount_var, 230)
        self._make_card(top, "原始凭证张数", self.total_vouchers_var, 190)
        self._make_card(top, "汇编页数", self.total_pages_var, 170)

        total_card = tk.Frame(top, bg="#ffffff", highlightthickness=1, highlightbackground="#d7dee8", width=280, height=88)
        total_card.pack(side=tk.LEFT, padx=(0, 14))
        total_card.pack_propagate(False)
        tk.Label(total_card, text="页眉共几张", bg="#ffffff", fg="#697386", font=("Microsoft YaHei UI", 9)).pack(anchor=tk.W, padx=22, pady=(14, 4))
        total_entry = tk.Entry(total_card, textvariable=self.total_pages_text_var, bg="#f8fafc", fg="#172033", relief=tk.FLAT, highlightthickness=1, highlightbackground="#cbd5e1", font=("Microsoft YaHei UI", 11))
        total_entry.pack(anchor=tk.W, padx=22, ipady=4, ipadx=6, fill=tk.X)
        self._make_button(top, "生成 PDF", self.export_pdf, primary=True).pack(side=tk.RIGHT, pady=(17, 0), ipadx=12)
        self._make_button(top, "打印", self.print_pdf, accent=True).pack(side=tk.RIGHT, pady=(17, 0), padx=(0, 10), ipadx=12)

        content = tk.Frame(root, bg="#eef2f5")
        content.pack(fill=tk.BOTH, expand=True)

        columns = ("file", "amount", "vouchers", "pages", "status")
        action_panel = tk.Frame(content, bg="#ffffff", highlightthickness=1, highlightbackground="#d7dee8", width=210)
        action_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 16))
        action_panel.pack_propagate(False)
        list_frame = tk.Frame(content, bg="#ffffff", highlightthickness=1, highlightbackground="#d7dee8")
        list_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 16))
        preview_frame = tk.Frame(content, bg="#ffffff", highlightthickness=1, highlightbackground="#d7dee8", width=PREVIEW_PANEL_WIDTH)
        preview_frame.pack(side=tk.RIGHT, fill=tk.BOTH)
        preview_frame.pack_propagate(False)

        self._panel_title(action_panel, "操作")
        self._make_button(action_panel, "添加 PDF / OFD", self.add_pdf_files, primary=True).pack(fill=tk.X, padx=22, pady=(0, 12))
        self._make_button(action_panel, "添加图片", self.add_image_page_files, accent=True).pack(fill=tk.X, padx=22, pady=(0, 12))
        self._make_button(action_panel, "修改选中", self.edit_selected).pack(fill=tk.X, padx=22, pady=(0, 12))
        self._make_button(action_panel, "删除选中", self.remove_selected).pack(fill=tk.X, padx=22, pady=(0, 12))
        self._make_button(action_panel, "关于软件", self.show_about).pack(fill=tk.X, padx=22, pady=(0, 12))
        tk.Label(
            action_panel,
            text="提示：PDF/OFD 会按原方向缩小放入 A4 页面；图片可新开一页或追加到选中的图片页。",
            wraplength=160,
            justify=tk.LEFT,
            bg="#ffffff",
            fg="#697386",
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor=tk.W, padx=22, pady=(16, 0))

        self._panel_title(list_frame, "发票页面")
        tree_container = tk.Frame(list_frame, bg="#ffffff")
        tree_container.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 18))
        self.tree = ttk.Treeview(tree_container, columns=columns, show="headings", height=18, selectmode="extended")
        self.tree.heading("file", text="文件")
        self.tree.heading("amount", text="识别/报销金额")
        self.tree.heading("vouchers", text="原始凭证张数")
        self.tree.heading("pages", text="汇编页数")
        self.tree.heading("status", text="识别状态")
        self.tree.column("file", width=260, minwidth=180, anchor=tk.W)
        self.tree.column("amount", width=100, minwidth=90, anchor=tk.E)
        self.tree.column("vouchers", width=110, minwidth=90, anchor=tk.CENTER)
        self.tree.column("pages", width=80, minwidth=70, anchor=tk.CENTER)
        self.tree.column("status", width=170, minwidth=140, anchor=tk.W)
        scrollbar = ttk.Scrollbar(tree_container, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<Double-1>", lambda _event: self.edit_selected())
        self.tree.bind("<Delete>", lambda _event: self.remove_selected())
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self.update_preview())
        self.tree.bind("<ButtonPress-1>", self._start_tree_drag, add="+")
        self.tree.bind("<B1-Motion>", self._move_tree_drag, add="+")
        self.tree.bind("<ButtonRelease-1>", self._finish_tree_drag, add="+")

        self._panel_title(preview_frame, "实时预览")
        preview_container = tk.Frame(preview_frame, bg="#ffffff")
        preview_container.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 18))
        self.preview_canvas = tk.Canvas(preview_container, bg="#f8fafc", highlightthickness=1, highlightbackground="#cbd5e1")
        self.preview_canvas.pack(fill=tk.BOTH, expand=True)
        self.preview_canvas.bind("<Configure>", lambda _event: self.update_preview())

    def _start_tree_drag(self, event: tk.Event) -> None:
        if self.tree.identify_region(event.x, event.y) != "cell":
            self._drag_row_index = None
            return
        row = self.tree.identify_row(event.y)
        if not row:
            self._drag_row_index = None
            return
        self._drag_row_index = int(row)
        self.tree.selection_set(row)
        self.tree.focus(row)

    def _move_tree_drag(self, event: tk.Event):
        if self._drag_row_index is None:
            return None
        row = self.tree.identify_row(event.y)
        if not row:
            return "break"
        target_index = int(row)
        source_index = self._drag_row_index
        if target_index == source_index or not (0 <= source_index < len(self.items)):
            return "break"

        moved_item = self.items.pop(source_index)
        self.items.insert(target_index, moved_item)
        self._drag_row_index = target_index
        self._reload_table()
        target_iid = str(target_index)
        self.tree.selection_set(target_iid)
        self.tree.focus(target_iid)
        self.tree.see(target_iid)
        self.tree.configure(cursor="fleur")
        return "break"

    def _finish_tree_drag(self, _event: tk.Event) -> None:
        self._drag_row_index = None
        self.tree.configure(cursor="")

    def add_pdf_files(self) -> None:
        filetypes = [
            ("PDF / OFD", "*.pdf *.ofd"),
            ("PDF", "*.pdf"),
            ("OFD", "*.ofd"),
            ("所有文件", "*.*"),
        ]
        paths = filedialog.askopenfilenames(title="选择 PDF / OFD 发票文件", filetypes=filetypes)
        if not paths:
            return
        for raw_path in paths:
            item = inspect_file(Path(raw_path))
            if item.is_document:
                self.items.append(item)
            else:
                messagebox.showwarning(APP_TITLE, f"已跳过非 PDF/OFD 文件：\n{raw_path}")
        self._reload_table()

    def add_image_page_files(self) -> None:
        dialog = ImageTargetDialog(self, self._selected_image_page_index() is not None)
        self.wait_window(dialog)
        if not dialog.choice:
            return
        target_index = self._selected_image_page_index()
        if dialog.choice == "existing" and target_index is None:
            messagebox.showinfo(APP_TITLE, "请先在列表中选中一个图片页，再选择添加到原有页面。")
            return

        filetypes = [
            ("图片", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp"),
            ("所有文件", "*.*"),
        ]
        paths = filedialog.askopenfilenames(title="选择图片", filetypes=filetypes)
        if not paths:
            return
        image_paths = [Path(raw_path) for raw_path in paths if Path(raw_path).suffix.lower() in IMAGE_EXTS]
        if not image_paths:
            messagebox.showinfo(APP_TITLE, "请选择图片文件。")
            return

        if dialog.choice == "existing":
            self._append_images_to_page(target_index, image_paths)
            self._reload_table()
            return

        if len(image_paths) > 9:
            messagebox.showwarning(APP_TITLE, "单页最多自动摆放 9 张图片，本次只使用前 9 张。")
            image_paths = image_paths[:9]
        self.items.append(self._make_image_item(image_paths))
        self._reload_table()

    def _make_image_item(self, image_paths: list[Path]) -> InvoiceItem:
        text_parts = []
        status_parts = []
        for path in image_paths:
            text, status = try_image_ocr(path)
            text_parts.append(text)
            status_parts.append(status)
        extracted_text = "\n".join(text_parts)
        amount = extract_image_amount(extracted_text)
        item = InvoiceItem(
            image_paths[0],
            amount,
            len(image_paths),
            1,
            "图片页，凭证张数已按图片数量更新",
            extracted_text,
            image_paths,
        )
        if amount is not None:
            item.status = "图片页，已自动识别发票金额；凭证张数已按图片数量更新"
        elif extracted_text.strip() and not is_invoice_like_image_text(extracted_text):
            item.status = "图片页，未识别为发票，金额可留空或手动填写；凭证张数已自动更新"
        elif any("OCR" in status for status in status_parts):
            item.status = "图片页，需核对金额；凭证张数已自动更新"
        return item

    def _append_images_to_page(self, index: int | None, new_paths: list[Path]) -> None:
        if index is None:
            return
        item = self.items[index]
        existing_paths = item.image_paths or [item.path]
        slots = max(0, 9 - len(existing_paths))
        if slots == 0:
            messagebox.showinfo(APP_TITLE, "选中的图片页已经有 9 张图片，不能继续追加。")
            return
        if len(new_paths) > slots:
            messagebox.showwarning(APP_TITLE, f"单页最多 9 张图片，本次只追加前 {slots} 张。")
            new_paths = new_paths[:slots]
        item.image_paths = existing_paths + new_paths
        item.path = item.image_paths[0]
        item.voucher_count = len(item.image_paths)
        item.pages = 1
        new_text_parts = []
        for path in new_paths:
            text, _status = try_image_ocr(path)
            new_text_parts.append(text)
        item.extracted_text = "\n".join(part for part in [item.extracted_text, "\n".join(new_text_parts)] if part)
        if item.amount is None and is_invoice_like_image_text(item.extracted_text):
            item.amount = extract_amount(item.extracted_text)
        if item.amount is not None:
            item.status = "图片页，已追加图片；发票金额已保留；凭证张数已自动更新"
        elif item.extracted_text.strip() and not is_invoice_like_image_text(item.extracted_text):
            item.status = "图片页，已追加图片；未识别为发票，金额可留空或手动填写；凭证张数已自动更新"
        else:
            item.status = "图片页，已追加图片；凭证张数已自动更新"

    def _selected_image_page_index(self) -> int | None:
        for iid in self.tree.selection():
            index = int(iid)
            if 0 <= index < len(self.items) and self.items[index].is_image_page:
                return index
        return None

    def remove_selected(self) -> None:
        selected = sorted((int(iid) for iid in self.tree.selection()), reverse=True)
        if not selected:
            messagebox.showinfo(APP_TITLE, "请先选择要删除的页面。")
            return
        for index in selected:
            if 0 <= index < len(self.items):
                del self.items[index]
        self._reload_table()

    def edit_selected(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo(APP_TITLE, "请先选择一条发票记录。")
            return
        index = int(selection[0])
        item = self.items[index]
        dialog = EditDialog(self, item)
        self.wait_window(dialog)
        if dialog.saved:
            item.amount = normalize_amount(dialog.amount_var.get())
            if item.is_image_page:
                try:
                    item.voucher_count = max(1, int(dialog.vouchers_var.get()))
                except ValueError:
                    item.voucher_count = max(1, len(item.image_paths or []))
                item.pages = 1
            else:
                try:
                    item.voucher_count = max(1, int(dialog.vouchers_var.get()))
                except ValueError:
                    item.voucher_count = 1
                available_pages = available_document_pages(item)
                try:
                    requested_pages = max(1, int(dialog.pages_var.get()))
                except ValueError:
                    requested_pages = item.pages
                if requested_pages > available_pages:
                    messagebox.showwarning(APP_TITLE, f"原文件最多有 {available_pages} 页，汇编页数已自动调整为 {available_pages}。")
                item.pages = min(requested_pages, available_pages)
            self._reload_table()

    def export_pdf(self) -> None:
        if not self.items:
            messagebox.showinfo(APP_TITLE, "请先添加发票文件。")
            return
        output = filedialog.asksaveasfilename(
            title="保存汇编 PDF",
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")],
            initialfile=default_output_filename(),
        )
        if not output:
            return
        try:
            compile_pdf(self.items, Path(output), self.total_pages_text_var.get().strip())
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"生成失败：{exc}")
            return
        messagebox.showinfo(APP_TITLE, f"已生成：\n{output}")

    def print_pdf(self) -> None:
        if not self.items:
            messagebox.showinfo(APP_TITLE, "请先添加发票文件。")
            return
        output = Path(tempfile.gettempdir()) / default_output_filename()
        try:
            compile_pdf(self.items, output, self.total_pages_text_var.get().strip())
            os.startfile(str(output), "print")  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"打印失败：{exc}")
            return
        messagebox.showinfo(APP_TITLE, "已交给系统打印流程，请在弹出的打印窗口中确认。")

    def show_about(self) -> None:
        messagebox.showinfo(
            APP_TITLE,
            f"发票汇编整理申报工具\n版本：{APP_VERSION}\n作者：{APP_AUTHOR}\n\n本地处理文件，不上传发票内容。",
        )

    def _on_total_pages_text_changed(self, *_args) -> None:
        if not self._updating_total_pages_text:
            self.total_pages_user_edited = True
        self.update_preview()

    def _reload_table(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, item in enumerate(self.items):
            self.tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(
                    item.display_name,
                    format_money(item.amount),
                    item.voucher_count,
                    item.pages,
                    item.status,
                ),
            )
        self._refresh_summary()
        self.update_preview()

    def _refresh_summary(self) -> None:
        total_amount = sum((item.amount or Decimal("0.00") for item in self.items), Decimal("0.00"))
        voucher_count = total_voucher_count(self.items)
        pages = sum(item.pages for item in self.items)
        if hasattr(self, "total_amount_var"):
            self.total_amount_var.set(format_money(total_amount))
            self.total_vouchers_var.set(str(voucher_count))
            self.total_pages_var.set(str(pages))
        if not self.total_pages_user_edited:
            self._updating_total_pages_text = True
            self.total_pages_text_var.set(str(voucher_count) if voucher_count else "")
            self._updating_total_pages_text = False

    def update_preview(self) -> None:
        if not hasattr(self, "preview_canvas"):
            return
        width = max(1, self.preview_canvas.winfo_width())
        height = max(1, self.preview_canvas.winfo_height())
        self.preview_canvas.delete("all")
        if width < 20 or height < 20:
            return
        selection = self.tree.selection()
        if not selection:
            self.preview_canvas.create_text(width / 2, height / 2, text="选中一页查看预览", fill="#666")
            return
        index = int(selection[0])
        if not (0 <= index < len(self.items)):
            return
        page_no = sum(max(item.pages, 1) for item in self.items[:index]) + 1
        preview = self._build_preview_image(self.items[index], page_no, width, height)
        self.preview_photo = ImageTk.PhotoImage(preview)
        self.preview_canvas.create_image(width / 2, height / 2, image=self.preview_photo)

    def _preview_font(self, size: int) -> ImageFont.ImageFont:
        try:
            if FONT_PATH:
                return ImageFont.truetype(FONT_PATH, size)
        except Exception:
            pass
        return ImageFont.load_default()

    def _build_preview_image(self, item: InvoiceItem, page_no: int, canvas_w: int, canvas_h: int) -> Image.Image:
        margin = 12
        preview_width, preview_height = preview_page_size_for_item(item)
        scale = min((canvas_w - margin * 2) / preview_width, (canvas_h - margin * 2) / preview_height)
        scale = max(scale, 0.1)
        page_w = int(preview_width * scale)
        page_h = int(preview_height * scale)
        image = Image.new("RGB", (canvas_w, canvas_h), "#f4f4f4")
        draw = ImageDraw.Draw(image)
        ox = (canvas_w - page_w) // 2
        oy = (canvas_h - page_h) // 2
        draw.rectangle([ox, oy, ox + page_w, oy + page_h], fill="white", outline="#b8b8b8", width=1)

        def px(x: float) -> int:
            return int(ox + x * scale)

        def py(y_from_bottom: float) -> int:
            return int(oy + (preview_height - y_from_bottom) * scale)

        header_font = self._preview_font(max(11, int(HEADER_FONT_SIZE * scale * 1.55)))
        small_font = self._preview_font(max(10, int(10 * scale * 1.6)))
        header_y = py(header_y_for_page(preview_height)) - int(HEADER_FONT_SIZE * scale)
        amount_text = f"报销金额：{format_money(item.amount)}"
        voucher_text = f"原始凭证张数：{item.voucher_count}"
        page_text = f"第{page_no}页/共{self.total_pages_text_var.get().strip() or ' '}张"
        if uses_portrait_side_header(item, preview_width, preview_height):
            items = [
                (amount_text, preview_height - PORTRAIT_SIDE_HEADER_TOP),
                (voucher_text, preview_height / 2),
                (page_text, PORTRAIT_SIDE_HEADER_BOTTOM),
            ]
            for text, center_y in items:
                text_box = draw.textbbox((0, 0), text, font=header_font)
                text_w = max(1, text_box[2] - text_box[0])
                text_h = max(1, text_box[3] - text_box[1])
                text_image = Image.new("RGBA", (text_w + 8, text_h + 8), (255, 255, 255, 0))
                text_draw = ImageDraw.Draw(text_image)
                text_draw.text((4, 4), text, fill="black", font=header_font)
                rotated = text_image.rotate(-90, expand=True)
                paste_x = px(preview_width - PORTRAIT_SIDE_HEADER_X_OFFSET) - rotated.width // 2
                paste_y = py(center_y) - rotated.height // 2
                image.paste(rotated, (paste_x, paste_y), rotated)
        else:
            draw.text((px(HEADER_SIDE_INSET), header_y), amount_text, fill="black", font=header_font)
            voucher_box = draw.textbbox((0, 0), voucher_text, font=header_font)
            draw.text((px(preview_width / 2) - (voucher_box[2] - voucher_box[0]) / 2, header_y), voucher_text, fill="black", font=header_font)
            page_box = draw.textbbox((0, 0), page_text, font=header_font)
            draw.text((px(preview_width - HEADER_SIDE_INSET) - (page_box[2] - page_box[0]), header_y), page_text, fill="black", font=header_font)
        if item.is_image_page:
            self._draw_image_preview(image, draw, item, scale, ox, oy, preview_width, preview_height)
        else:
            self._draw_pdf_preview(draw, item, scale, ox, oy, small_font, preview_width, preview_height)
        return image

    def _draw_pdf_preview(
        self,
        draw: ImageDraw.ImageDraw,
        item: InvoiceItem,
        scale: float,
        ox: int,
        oy: int,
        font: ImageFont.ImageFont,
        page_width: float,
        page_height: float,
    ) -> None:
        source_path = item.render_path or item.path
        try:
            first_page = PdfReader(str(source_path)).pages[0]
            source_w, source_h = pdf_display_size(first_page)
        except Exception:
            source_w, source_h = A4
        content_left, content_right, content_bottom, content_top = document_content_bounds(page_width, page_height)
        content_w = content_right - content_left
        content_h = content_top - content_bottom
        scale_factor = 0.98 if page_height > page_width else PDF_SCALE_FACTOR
        fit = min(content_w / source_w, content_h / source_h) * scale_factor
        draw_w = source_w * fit
        draw_h = source_h * fit
        centered_x = content_left + (content_w - draw_w) / 2
        x = min(centered_x + PDF_X_OFFSET, content_right - draw_w)
        y_bottom = content_bottom + (content_h - draw_h) / 2
        left = int(ox + x * scale)
        top = int(oy + (page_height - y_bottom - draw_h) * scale)
        right = int(left + draw_w * scale)
        bottom = int(top + draw_h * scale)
        draw.rectangle([left, top, right, bottom], fill="#fafafa", outline="#999", width=2)
        label = f"{'OFD' if item.is_ofd else 'PDF'} 页面缩放预览：{item.path.name}"
        if item.is_ofd and item.render_path is None:
            label = f"OFD 待转换：{item.path.name}"
        box = draw.textbbox((0, 0), label, font=font)
        draw.text((left + (right - left - (box[2] - box[0])) / 2, top + (bottom - top) / 2), label, fill="#555", font=font)

    def _draw_image_preview(
        self,
        target: Image.Image,
        draw: ImageDraw.ImageDraw,
        item: InvoiceItem,
        scale: float,
        ox: int,
        oy: int,
        page_width: float,
        page_height: float,
    ) -> None:
        paths = (item.image_paths or [item.path])[:9]
        cols, rows = grid_for_count(len(paths))
        gap = IMAGE_GAP
        if uses_portrait_side_header(item, page_width, page_height):
            content_left, content_right, content_bottom, content_top = document_content_bounds(page_width, page_height)
        else:
            content_left, content_right, content_bottom, content_top = content_bounds(page_width, page_height)
        content_w = content_right - content_left
        content_h = content_top - content_bottom
        cell_w = (content_w - gap * (cols - 1)) / cols
        cell_h = (content_h - gap * (rows - 1)) / rows
        for index, path in enumerate(paths):
            row = index // cols
            col = index % cols
            cell_x = content_left + col * (cell_w + gap)
            cell_y_bottom = content_top - (row + 1) * cell_h - row * gap
            cell_left = int(ox + cell_x * scale)
            cell_top = int(oy + (page_height - cell_y_bottom - cell_h) * scale)
            cell_right = int(cell_left + cell_w * scale)
            cell_bottom = int(cell_top + cell_h * scale)
            draw.rectangle([cell_left, cell_top, cell_right, cell_bottom], outline="#e2e2e2", width=1)
            try:
                with Image.open(path) as source:
                    thumb = source.convert("RGB")
                thumb.thumbnail((max(1, cell_right - cell_left), max(1, cell_bottom - cell_top)), Image.LANCZOS)
                paste_x = cell_left + (cell_right - cell_left - thumb.width) // 2
                paste_y = cell_top + (cell_bottom - cell_top - thumb.height) // 2
                target.paste(thumb, (paste_x, paste_y))
            except Exception:
                draw.text((cell_left + 8, cell_top + 8), path.name, fill="#555", font=self._preview_font(10))


class EditDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk, item: InvoiceItem) -> None:
        super().__init__(parent)
        self.title("修改发票信息")
        self.resizable(False, False)
        self.saved = False
        self.amount_var = tk.StringVar(value=format_money(item.amount))
        self.vouchers_var = tk.StringVar(value=str(item.voucher_count))
        self.pages_var = tk.StringVar(value=str(item.pages))

        frame = ttk.Frame(self, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text=item.display_name, width=64).grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 12))
        ttk.Label(frame, text="报销金额").grid(row=1, column=0, sticky=tk.W, pady=4)
        ttk.Entry(frame, textvariable=self.amount_var, width=24).grid(row=1, column=1, sticky=tk.W, pady=4)
        ttk.Label(frame, text="原始凭证张数").grid(row=2, column=0, sticky=tk.W, pady=4)
        ttk.Entry(frame, textvariable=self.vouchers_var, width=24).grid(row=2, column=1, sticky=tk.W, pady=4)
        ttk.Label(frame, text="汇编页数").grid(row=3, column=0, sticky=tk.W, pady=4)
        pages_state = "readonly" if item.is_image_page else "normal"
        ttk.Entry(frame, textvariable=self.pages_var, width=24, state=pages_state).grid(row=3, column=1, sticky=tk.W, pady=4)
        if item.is_image_page:
            ttk.Label(frame, text="图片页的汇编页数固定为 1。", foreground="#555").grid(
                row=4, column=0, columnspan=2, sticky=tk.W, pady=(4, 0)
            )

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=2, sticky=tk.E, pady=(14, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="保存", command=self._save).pack(side=tk.RIGHT, padx=(0, 8))

        self.transient(parent)
        self.grab_set()
        self.amount_var.set(format_money(item.amount))

    def _save(self) -> None:
        self.saved = True
        self.destroy()


class ImageTargetDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk, has_selected_image_page: bool) -> None:
        super().__init__(parent)
        self.title("添加图片")
        self.resizable(False, False)
        self.choice: str | None = None

        frame = ttk.Frame(self, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="请选择图片要放置的位置：", width=42).grid(row=0, column=0, columnspan=3, sticky=tk.W, pady=(0, 12))
        ttk.Button(frame, text="新开一页", command=lambda: self._choose("new")).grid(row=1, column=0, padx=(0, 8))
        existing_button = ttk.Button(frame, text="添加到选中页", command=lambda: self._choose("existing"))
        existing_button.grid(row=1, column=1, padx=(0, 8))
        ttk.Button(frame, text="取消", command=self.destroy).grid(row=1, column=2)

        if not has_selected_image_page:
            ttk.Label(frame, text="如需添加到原有页面，请先选中一个图片页。", foreground="#555").grid(
                row=2, column=0, columnspan=3, sticky=tk.W, pady=(12, 0)
            )

        self.transient(parent)
        self.grab_set()

    def _choose(self, choice: str) -> None:
        self.choice = choice
        self.destroy()


def main() -> None:
    app = InvoiceApp()
    app.mainloop()


if __name__ == "__main__":
    main()
