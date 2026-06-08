import os
import re
import sys
import tempfile
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageFont, ImageTk
from pypdf import PageObject, PdfReader, PdfWriter, Transformation
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


APP_TITLE = "发票汇编整理申报工具 -by tc"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
PDF_EXTS = {".pdf"}
PAGE_SIZE = landscape(A4)
PAGE_WIDTH, PAGE_HEIGHT = PAGE_SIZE

# 页面版式微调区：下面这些数值都可以手动调整。
# 单位是 PDF 点（pt），A4 横版大约是 841.89 x 595.28。
HEADER_FONT_SIZE = 15  # 页眉字体大小；想更大就调高，比如 16。
HEADER_Y = PAGE_HEIGHT - 48  # 页眉文字位置；数值越小越靠下，越大越靠上。
HEADER_LINE_Y = PAGE_HEIGHT - 68  # 内容顶部参考位置；页眉下方不画线，但内容区域仍用它避开页眉。
HEADER_SIDE_INSET = 90  # 页眉左右文字距离页面边缘的距离；越大越往中间收。
CONTENT_LEFT = 52  # 内容区域左边距；图片和 PDF 都会参考这个区域。
CONTENT_RIGHT = PAGE_WIDTH - 52  # 内容区域右边距。
CONTENT_BOTTOM = 38  # 内容区域下边距。
CONTENT_TOP = HEADER_LINE_Y - 20  # 内容区域顶部；用于避免内容压到页眉。
PDF_SCALE_FACTOR = 0.90  # PDF 原页缩放比例；越小 PDF 越小，留白越多。
PDF_X_OFFSET = 34  # PDF 缩小后向右偏移量；越大越靠右，左侧留白越多。
IMAGE_GAP = 14  # 多张图片之间的间距；越大图片之间空隙越宽。
PREVIEW_PANEL_WIDTH = 440  # 右侧预览栏宽度；想让预览更大就调高。


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

    @property
    def is_pdf(self) -> bool:
        return self.path.suffix.lower() in PDF_EXTS

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


def extract_amount(text: str) -> Decimal | None:
    compact = re.sub(r"\s+", "", text)
    patterns = [
        r"(?:价税合计|小写|合计金额|报销金额|金额合计|总金额|合计)[：:（(]?(?:人民币)?[¥￥]?([0-9][0-9,]*\.?[0-9]{0,2})",
        r"[¥￥]\s*([0-9][0-9,]*\.?[0-9]{0,2})",
        r"([0-9][0-9,]*\.[0-9]{2})",
    ]
    amounts: list[Decimal] = []
    for pattern in patterns:
        for match in re.findall(pattern, compact):
            amount = normalize_amount(match)
            if amount is not None and amount > 0:
                amounts.append(amount)
        if amounts:
            break
    return max(amounts) if amounts else None


def try_image_ocr(path: Path) -> tuple[str, str]:
    try:
        import pytesseract  # type: ignore
    except Exception:
        return "", "未安装图片 OCR，需手动核对"

    try:
        text = pytesseract.image_to_string(Image.open(path), lang="chi_sim+eng")
        return text, "图片 OCR 完成" if text.strip() else "图片 OCR 无文字"
    except Exception as exc:
        return "", f"图片 OCR 失败：{exc}"


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


def inspect_file(path: Path) -> InvoiceItem:
    suffix = path.suffix.lower()
    if suffix in PDF_EXTS:
        text, pages, status = read_pdf_text_and_pages(path)
        amount = extract_amount(text)
        return InvoiceItem(path, amount, max(pages, 1), max(pages, 1), status, text)
    if suffix in IMAGE_EXTS:
        text, status = try_image_ocr(path)
        amount = extract_amount(text)
        return InvoiceItem(path, amount, 1, 1, status, text)
    return InvoiceItem(path, None, 1, 1, "不支持的文件类型", "")


def make_header_overlay(width: float, height: float, amount: Decimal | None, vouchers: int, page_no: int, total_pages_text: str) -> str:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.close()
    c = canvas.Canvas(tmp.name, pagesize=(width, height))
    c.setFont(FONT_NAME, HEADER_FONT_SIZE)
    c.setFillColor(colors.black)
    c.drawString(HEADER_SIDE_INSET, HEADER_Y, f"报销金额：{format_money(amount)}")
    c.drawCentredString(width / 2, HEADER_Y, f"原始凭证张数：{vouchers}")
    c.drawRightString(width - HEADER_SIDE_INSET, HEADER_Y, f"第{page_no}页/共{total_pages_text or '  '}张")
    c.save()
    return tmp.name


def add_header_to_page(page: PageObject, amount: Decimal | None, vouchers: int, page_no: int, total_pages_text: str) -> None:
    overlay_path = make_header_overlay(PAGE_WIDTH, PAGE_HEIGHT, amount, vouchers, page_no, total_pages_text)
    overlay = PdfReader(overlay_path).pages[0]
    page.merge_page(overlay)
    os.unlink(overlay_path)


def add_pdf_pages(writer: PdfWriter, item: InvoiceItem, amount: Decimal | None, vouchers: int, start_page: int, total_pages_text: str) -> int:
    reader = PdfReader(str(item.path))
    page_no = start_page
    content_w = CONTENT_RIGHT - CONTENT_LEFT
    content_h = CONTENT_TOP - CONTENT_BOTTOM
    for source_page in reader.pages:
        source_w = float(source_page.mediabox.width)
        source_h = float(source_page.mediabox.height)
        scale = min(content_w / source_w, content_h / source_h) * PDF_SCALE_FACTOR
        draw_w = source_w * scale
        draw_h = source_h * scale
        centered_x = CONTENT_LEFT + (content_w - draw_w) / 2
        x = min(centered_x + PDF_X_OFFSET, CONTENT_RIGHT - draw_w)
        y = CONTENT_BOTTOM + (content_h - draw_h) / 2
        page = PageObject.create_blank_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.merge_transformed_page(source_page, Transformation().scale(scale).translate(x, y))
        add_header_to_page(page, amount, vouchers, page_no, total_pages_text)
        writer.add_page(page)
        page_no += 1
    return page_no


def grid_for_count(count: int) -> tuple[int, int]:
    if count <= 1:
        return 1, 1
    if count <= 9:
        return count, 1
    return 9, 1


def add_image_page(writer: PdfWriter, item: InvoiceItem, amount: Decimal | None, vouchers: int, page_no: int, total_pages_text: str) -> int:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.close()
    width, height = PAGE_SIZE
    paths = item.image_paths or [item.path]
    cols, rows = grid_for_count(len(paths))
    gap = IMAGE_GAP
    content_w = CONTENT_RIGHT - CONTENT_LEFT
    content_h = CONTENT_TOP - CONTENT_BOTTOM
    cell_w = (content_w - gap * (cols - 1)) / cols
    cell_h = (content_h - gap * (rows - 1)) / rows

    c = canvas.Canvas(tmp.name, pagesize=PAGE_SIZE)
    c.setFont(FONT_NAME, HEADER_FONT_SIZE)
    c.drawString(HEADER_SIDE_INSET, HEADER_Y, f"报销金额：{format_money(amount)}")
    c.drawCentredString(width / 2, HEADER_Y, f"原始凭证张数：{vouchers}")
    c.drawRightString(width - HEADER_SIDE_INSET, HEADER_Y, f"第{page_no}页/共{total_pages_text or '   '}张")
    for index, path in enumerate(paths[:9]):
        row = index // cols
        col = index % cols
        cell_x = CONTENT_LEFT + col * (cell_w + gap)
        cell_y = CONTENT_TOP - (row + 1) * cell_h - row * gap
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
    total_pages = sum(max(item.pages, 1) for item in items)
    page_no = 1
    for item in items:
        suffix = item.path.suffix.lower()
        if item.is_pdf:
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
        self.total_pages_text_var.trace_add("write", lambda *_args: self.update_preview())
        self.preview_photo: ImageTk.PhotoImage | None = None
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
        self._make_button(action_panel, "添加 PDF", self.add_pdf_files, primary=True).pack(fill=tk.X, padx=22, pady=(0, 12))
        self._make_button(action_panel, "添加图片", self.add_image_page_files, accent=True).pack(fill=tk.X, padx=22, pady=(0, 12))
        self._make_button(action_panel, "修改选中", self.edit_selected).pack(fill=tk.X, padx=22, pady=(0, 12))
        self._make_button(action_panel, "删除选中", self.remove_selected).pack(fill=tk.X, padx=22, pady=(0, 12))
        tk.Label(
            action_panel,
            text="提示：PDF 会缩小放入横向 A4 页面；图片可新开一页或追加到选中的图片页。",
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

        self._panel_title(preview_frame, "实时预览")
        preview_container = tk.Frame(preview_frame, bg="#ffffff")
        preview_container.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 18))
        self.preview_canvas = tk.Canvas(preview_container, bg="#f8fafc", highlightthickness=1, highlightbackground="#cbd5e1")
        self.preview_canvas.pack(fill=tk.BOTH, expand=True)
        self.preview_canvas.bind("<Configure>", lambda _event: self.update_preview())

    def add_pdf_files(self) -> None:
        filetypes = [
            ("PDF", "*.pdf"),
            ("所有文件", "*.*"),
        ]
        paths = filedialog.askopenfilenames(title="选择 PDF 发票文件", filetypes=filetypes)
        if not paths:
            return
        for raw_path in paths:
            item = inspect_file(Path(raw_path))
            if item.is_pdf:
                self.items.append(item)
            else:
                messagebox.showwarning(APP_TITLE, f"已跳过非 PDF 文件：\n{raw_path}")
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
        amount = extract_amount("\n".join(text_parts))
        item = InvoiceItem(
            image_paths[0],
            amount,
            len(image_paths),
            1,
            "图片页，凭证张数已按图片数量更新",
            "\n".join(text_parts),
            image_paths,
        )
        if any("OCR" in status for status in status_parts):
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
        if item.amount is None:
            item.amount = extract_amount(item.extracted_text)
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
                item.voucher_count = len(item.image_paths or [])
            else:
                try:
                    item.voucher_count = max(1, int(dialog.vouchers_var.get()))
                except ValueError:
                    item.voucher_count = 1
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
        voucher_count = sum(item.voucher_count for item in self.items)
        pages = sum(item.pages for item in self.items)
        if hasattr(self, "total_amount_var"):
            self.total_amount_var.set(format_money(total_amount))
            self.total_vouchers_var.set(str(voucher_count))
            self.total_pages_var.set(str(pages))

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
        scale = min((canvas_w - margin * 2) / PAGE_WIDTH, (canvas_h - margin * 2) / PAGE_HEIGHT)
        scale = max(scale, 0.1)
        page_w = int(PAGE_WIDTH * scale)
        page_h = int(PAGE_HEIGHT * scale)
        image = Image.new("RGB", (canvas_w, canvas_h), "#f4f4f4")
        draw = ImageDraw.Draw(image)
        ox = (canvas_w - page_w) // 2
        oy = (canvas_h - page_h) // 2
        draw.rectangle([ox, oy, ox + page_w, oy + page_h], fill="white", outline="#b8b8b8", width=1)

        def px(x: float) -> int:
            return int(ox + x * scale)

        def py(y_from_bottom: float) -> int:
            return int(oy + (PAGE_HEIGHT - y_from_bottom) * scale)

        header_font = self._preview_font(max(11, int(HEADER_FONT_SIZE * scale * 1.55)))
        small_font = self._preview_font(max(10, int(10 * scale * 1.6)))
        header_y = py(HEADER_Y) - int(HEADER_FONT_SIZE * scale)
        amount_text = f"报销金额：{format_money(item.amount)}"
        voucher_text = f"原始凭证张数：{item.voucher_count}"
        page_text = f"第{page_no}页/共{self.total_pages_text_var.get().strip() or ' '}张"
        draw.text((px(HEADER_SIDE_INSET), header_y), amount_text, fill="black", font=header_font)
        voucher_box = draw.textbbox((0, 0), voucher_text, font=header_font)
        draw.text((px(PAGE_WIDTH / 2) - (voucher_box[2] - voucher_box[0]) / 2, header_y), voucher_text, fill="black", font=header_font)
        page_box = draw.textbbox((0, 0), page_text, font=header_font)
        draw.text((px(PAGE_WIDTH - HEADER_SIDE_INSET) - (page_box[2] - page_box[0]), header_y), page_text, fill="black", font=header_font)
        if item.is_image_page:
            self._draw_image_preview(image, draw, item, scale, ox, oy)
        else:
            self._draw_pdf_preview(draw, item, scale, ox, oy, small_font)
        return image

    def _draw_pdf_preview(self, draw: ImageDraw.ImageDraw, item: InvoiceItem, scale: float, ox: int, oy: int, font: ImageFont.ImageFont) -> None:
        try:
            first_page = PdfReader(str(item.path)).pages[0]
            source_w = float(first_page.mediabox.width)
            source_h = float(first_page.mediabox.height)
        except Exception:
            source_w, source_h = A4
        content_w = CONTENT_RIGHT - CONTENT_LEFT
        content_h = CONTENT_TOP - CONTENT_BOTTOM
        fit = min(content_w / source_w, content_h / source_h) * PDF_SCALE_FACTOR
        draw_w = source_w * fit
        draw_h = source_h * fit
        centered_x = CONTENT_LEFT + (content_w - draw_w) / 2
        x = min(centered_x + PDF_X_OFFSET, CONTENT_RIGHT - draw_w)
        y_bottom = CONTENT_BOTTOM + (content_h - draw_h) / 2
        left = int(ox + x * scale)
        top = int(oy + (PAGE_HEIGHT - y_bottom - draw_h) * scale)
        right = int(left + draw_w * scale)
        bottom = int(top + draw_h * scale)
        draw.rectangle([left, top, right, bottom], fill="#fafafa", outline="#999", width=2)
        label = f"PDF 页面缩放预览：{item.path.name}"
        box = draw.textbbox((0, 0), label, font=font)
        draw.text((left + (right - left - (box[2] - box[0])) / 2, top + (bottom - top) / 2), label, fill="#555", font=font)

    def _draw_image_preview(self, target: Image.Image, draw: ImageDraw.ImageDraw, item: InvoiceItem, scale: float, ox: int, oy: int) -> None:
        paths = (item.image_paths or [item.path])[:9]
        cols, rows = grid_for_count(len(paths))
        gap = IMAGE_GAP
        content_w = CONTENT_RIGHT - CONTENT_LEFT
        content_h = CONTENT_TOP - CONTENT_BOTTOM
        cell_w = (content_w - gap * (cols - 1)) / cols
        cell_h = (content_h - gap * (rows - 1)) / rows
        for index, path in enumerate(paths):
            row = index // cols
            col = index % cols
            cell_x = CONTENT_LEFT + col * (cell_w + gap)
            cell_y_bottom = CONTENT_TOP - (row + 1) * cell_h - row * gap
            cell_left = int(ox + cell_x * scale)
            cell_top = int(oy + (PAGE_HEIGHT - cell_y_bottom - cell_h) * scale)
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

        frame = ttk.Frame(self, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text=item.display_name, width=64).grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 12))
        ttk.Label(frame, text="报销金额").grid(row=1, column=0, sticky=tk.W, pady=4)
        ttk.Entry(frame, textvariable=self.amount_var, width=24).grid(row=1, column=1, sticky=tk.W, pady=4)
        ttk.Label(frame, text="原始凭证张数").grid(row=2, column=0, sticky=tk.W, pady=4)
        voucher_state = "readonly" if item.is_image_page else "normal"
        ttk.Entry(frame, textvariable=self.vouchers_var, width=24, state=voucher_state).grid(row=2, column=1, sticky=tk.W, pady=4)
        if item.is_image_page:
            ttk.Label(frame, text="图片页的凭证张数会按图片数量自动更新。", foreground="#555").grid(
                row=3, column=0, columnspan=2, sticky=tk.W, pady=(4, 0)
            )

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=2, sticky=tk.E, pady=(14, 0))
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
