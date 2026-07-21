"""文檔轉換子系統（SPEC_INGEST.md，O9）——inbox PDF/掃描 → staging markdown → 人工審核 → corpus。

- parse：PyMuPDF 抽文字層（O9.1）；OCR fallback（O9.2，本機、惰性、air-gapped）。
- structure：分節 + 錨點自動編 + front-matter 骨架 + 信心分級。
- promote：格式校驗（front-matter/錨點唯一/reviewer）→ corpus/（唯一寫入路徑）。

**人工審核是硬閘門**：機器產出落 staging/，promote（強制 reviewer）才進 corpus/——未 promote
內容不會被 O6.3 入庫 CLI 看見（目錄隔離）。
"""

from __future__ import annotations

from matso_ai.ingest.parse import PageText, extract_pages
from matso_ai.ingest.promote import IngestError, promote_markdown
from matso_ai.ingest.structure import Section, to_staging_markdown

__all__ = [
    "IngestError",
    "PageText",
    "Section",
    "extract_pages",
    "promote_markdown",
    "to_staging_markdown",
]
