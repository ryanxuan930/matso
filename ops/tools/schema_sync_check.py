"""比對 db/prisma/schema.prisma 與 core/app/models 的結構一致性（SPEC_FULL §15.4）。

檢查層級（v1）：table 集合、column 集合、nullability、primary key、型別大類（含 enum 名稱）。
尚未比對：index / unique constraint / FK / 精確 DB 型別（如 VARCHAR 長度）——留待後續強化。

用法：uv run python ops/tools/schema_sync_check.py
Exit code 0 = 一致；1 = drift（CI 失敗）。
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import sqlalchemy as sa

REPO_ROOT = Path(__file__).resolve().parents[2]
PRISMA_SCHEMA = REPO_ROOT / "db" / "prisma" / "schema.prisma"

_SCALAR_CATEGORY: dict[str, str] = {
    "String": "string",
    "Int": "int",
    "BigInt": "bigint",
    "Float": "float",
    "Boolean": "bool",
    "DateTime": "datetime",
    "Json": "json",
    "Bytes": "bytes",
    "Decimal": "decimal",
}


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    category: str
    nullable: bool
    primary_key: bool


def parse_prisma(text: str) -> dict[str, dict[str, ColumnSpec]]:
    """回傳 {model_name: {column_name: ColumnSpec}}。關聯欄位（型別為 model）不產生 column。"""
    text = re.sub(r"//[^\n]*", "", text)
    # 先剝除字串字面值：@default("{}") 之類的內容含 } 會截斷 model body 的比對
    text = re.sub(r'"[^"]*"', '""', text)
    enum_names = set(re.findall(r"\benum\s+(\w+)\s*\{", text))
    model_bodies = dict(re.findall(r"\bmodel\s+(\w+)\s*\{([^}]*)\}", text))
    model_names = set(model_bodies)

    models: dict[str, dict[str, ColumnSpec]] = {}
    for model, body in model_bodies.items():
        columns: dict[str, ColumnSpec] = {}
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("@@"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            field_name, field_type = parts[0], parts[1]
            attrs = " ".join(parts[2:])
            nullable = field_type.endswith("?")
            base_type = field_type.rstrip("?").removesuffix("[]")
            if base_type in model_names or field_type.endswith("[]"):
                continue  # relation / list field：無實體 column
            if base_type in enum_names:
                category = f"enum:{base_type}"
            elif base_type in _SCALAR_CATEGORY:
                category = _SCALAR_CATEGORY[base_type]
            else:
                # 未知型別 = 硬錯誤（O1.7/r16）：若只 WARN 跳過，該欄從此不在比對範圍，
                # 真 drift 永遠測不到（CI 假綠燈）。新 Prisma 型別需明確加入 _SCALAR_CATEGORY。
                raise SystemExit(
                    f"schema_sync_check：{model}.{field_name} 使用未知型別 {base_type}——"
                    f"請將其加入 _SCALAR_CATEGORY 的映射後再跑（拒絕靜默跳過）"
                )
            columns[field_name] = ColumnSpec(
                name=field_name,
                category=category,
                nullable=nullable,
                primary_key="@id" in attrs,
            )
        models[model] = columns
    return models


def sqlalchemy_category(col_type: sa.types.TypeEngine[object]) -> str:
    # 注意 isinstance 順序：Enum 是 String 子類、BigInteger 是 Integer 子類
    if isinstance(col_type, sa.Enum):
        enum_class = col_type.enum_class
        return f"enum:{enum_class.__name__}" if enum_class else "enum:?"
    if isinstance(col_type, sa.Boolean):
        return "bool"
    if isinstance(col_type, sa.BigInteger):
        return "bigint"
    if isinstance(col_type, sa.Integer):
        return "int"
    if isinstance(col_type, (sa.Float, sa.Double)):
        return "float"
    if isinstance(col_type, sa.DateTime):
        return "datetime"
    if isinstance(col_type, sa.LargeBinary):
        return "bytes"
    if isinstance(col_type, sa.JSON):
        return "json"
    if isinstance(col_type, (sa.String, sa.Text)):
        return "string"
    return f"unknown:{type(col_type).__name__}"


def load_sqlalchemy_models() -> dict[str, dict[str, ColumnSpec]]:
    from app.models import Base

    models: dict[str, dict[str, ColumnSpec]] = {}
    for table_name, table in Base.metadata.tables.items():
        columns: dict[str, ColumnSpec] = {}
        for col in table.columns:
            columns[col.name] = ColumnSpec(
                name=col.name,
                category=sqlalchemy_category(col.type),
                nullable=bool(col.nullable),
                primary_key=col.primary_key,
            )
        models[table_name] = columns
    return models


def diff(
    prisma: dict[str, dict[str, ColumnSpec]],
    sqla: dict[str, dict[str, ColumnSpec]],
) -> list[str]:
    problems: list[str] = []
    for missing in sorted(prisma.keys() - sqla.keys()):
        problems.append(f"table {missing}: 存在於 prisma，SQLAlchemy 缺少")
    for extra in sorted(sqla.keys() - prisma.keys()):
        problems.append(f"table {extra}: 存在於 SQLAlchemy，prisma 缺少")
    for model in sorted(prisma.keys() & sqla.keys()):
        p_cols, s_cols = prisma[model], sqla[model]
        for missing_col in sorted(p_cols.keys() - s_cols.keys()):
            problems.append(f"{model}.{missing_col}: prisma 有，SQLAlchemy 缺")
        for extra_col in sorted(s_cols.keys() - p_cols.keys()):
            problems.append(f"{model}.{extra_col}: SQLAlchemy 有，prisma 缺")
        for name in sorted(p_cols.keys() & s_cols.keys()):
            p, s = p_cols[name], s_cols[name]
            if p.category != s.category:
                problems.append(f"{model}.{name}: 型別大類 prisma={p.category} sqla={s.category}")
            if p.nullable != s.nullable:
                problems.append(f"{model}.{name}: nullable prisma={p.nullable} sqla={s.nullable}")
            if p.primary_key != s.primary_key:
                problems.append(f"{model}.{name}: pk prisma={p.primary_key} sqla={s.primary_key}")
    return problems


def main() -> int:
    prisma = parse_prisma(PRISMA_SCHEMA.read_text(encoding="utf-8"))
    sqla = load_sqlalchemy_models()
    problems = diff(prisma, sqla)
    if problems:
        print(f"SCHEMA DRIFT — {len(problems)} 個不一致：")
        for p in problems:
            print(f"  - {p}")
        return 1
    n_cols = sum(len(c) for c in prisma.values())
    print(f"schema sync OK：{len(prisma)} tables / {n_cols} columns 一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
