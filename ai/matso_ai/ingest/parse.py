"""PDF 解析（O9.1 文字層 + O9.2 OCR fallback）。

born-digital PDF 走 PyMuPDF 文字層（confidence=high）；無文字層（掃描頁）→ OCR（本機、惰性）。
OCR 引擎缺失（air-gapped 未裝模型）→ 降級「僅文字層」：該頁 confidence=none，flagged 供人工。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class PageText:
    page: int
    text: str
    confidence: str  # high（文字層）/ medium（OCR）/ none（無文字層且無 OCR）
    is_ocr: bool = False


def _ocr_page(page: Any) -> tuple[str, str] | None:  # pragma: no cover - 需本機 OCR 模型
    """OCR fallback（O9.2）。tesseract/PaddleOCR 惰性載入；未裝 → None（降級）。"""
    try:
        import pytesseract  # type: ignore[import-not-found]
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError:
        return None
    pix = page.get_pixmap(dpi=200)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return pytesseract.image_to_string(img), "medium"


def extract_pages(pdf_path: str | Path, *, enable_ocr: bool = True) -> list[PageText]:
    """抽取每頁文字。文字層優先；空白頁（掃描）→ OCR（若可）→ 否則標 confidence=none。"""
    import fitz  # PyMuPDF

    out: list[PageText] = []
    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc):
            text = page.get_text("text").strip()
            if text:
                out.append(PageText(page=i, text=text, confidence="high"))
                continue
            ocr = _ocr_page(page) if enable_ocr else None
            if ocr is not None:
                out.append(PageText(page=i, text=ocr[0].strip(), confidence=ocr[1], is_ocr=True))
            else:
                out.append(PageText(page=i, text="", confidence="none"))
    return out
