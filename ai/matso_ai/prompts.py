"""角色 system prompt 載入（SPEC_FULL §9.1）——模式適配（§9.0）。

prompt 本體為資料（ai/prompts/<ROLE>.md，含 YAML frontmatter）；本模組依 AiMode 附加引用條款：
- AI_FULL：可引用 RAG 準則，cited_documents 填實際錨點。
- AI_BARE：無 RAG，依自身知識推理，cited_documents MUST 為空（護欄 G5 會剔除任何非空引用）。
prompt 變更走 PR（prompt 即程式碼）。
"""

from __future__ import annotations

from pathlib import Path

from matso_ai.roles import Role

_PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"

_MODE_CLAUSE = {
    "AI_FULL": (
        "\n\n【引用】本模式接有 RAG 準則庫。推理時可檢索並**逐字引用**，"
        "把實際引用的文件錨點填入 cited_documents（如 doctrine_general/foo.md#GEN-01）。"
    ),
    "AI_BARE": (
        "\n\n【引用】本模式**無** RAG 語料。請依你自身的軍事知識推理，"
        "cited_documents **必須為空陣列 []**——捏造引用會被護欄剔除並記為違規。"
    ),
}


def _strip_front_matter(md: str) -> str:
    if md.startswith("---"):
        parts = md.split("---", 2)
        if len(parts) == 3:
            return parts[2].strip()
    return md.strip()


def load_base_prompt(role: Role) -> str:
    """讀 ai/prompts/<ROLE>.md 本體（去 frontmatter）。"""
    return _strip_front_matter((_PROMPT_DIR / f"{role.value}.md").read_text(encoding="utf-8"))


def build_system_prompt(role: Role, mode: str) -> str:
    """組合最終 system prompt：角色本體 + 模式引用條款。"""
    return load_base_prompt(role) + _MODE_CLAUSE.get(mode, _MODE_CLAUSE["AI_BARE"])
