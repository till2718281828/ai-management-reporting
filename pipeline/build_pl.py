"""Сборка P&L из проводок в двух представлениях.

Управленческое (УУ): ноги проводок по счетам результата разносятся по статьям через mapping.csv —
затраты видны по элементам (материалы, персонал, ремонты…), амортизация отдельно.
Бухгалтерское (БУ): строки берутся напрямую со счетов 90, 91 и 99, без маппинга.

Знак ноги: кредит — плюс, дебет — минус. Доходы положительны, расходы отрицательны.
Промежуточный итог — нарастающая сумма всех статей выше него.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

TECH = "тех"

MANAGEMENT_LINES = [
    ("Выручка", "article"),
    ("Материалы", "article"),
    ("Персонал производства", "article"),
    ("Энергия", "article"),
    ("Ремонты", "article"),
    ("Прочие производственные", "article"),
    ("Валовая прибыль", "subtotal"),
    ("Коммерческие расходы", "article"),
    ("Управленческие расходы", "article"),
    ("EBITDA", "subtotal"),
    ("Амортизация", "article"),
    ("Прибыль от продаж", "subtotal"),
    ("Проценты", "article"),
    ("Прочие доходы и расходы", "article"),
    ("Прибыль до налога", "subtotal"),
    ("Налог на прибыль", "article"),
    ("Чистая прибыль", "subtotal"),
]

ACCOUNTING_LINES = [
    ("Выручка", "article"),
    ("Себестоимость продаж", "article"),
    ("Коммерческие расходы", "article"),
    ("Управленческие расходы", "article"),
    ("Прибыль от продаж", "subtotal"),
    ("Прочие доходы и расходы", "article"),
    ("Прибыль до налога", "subtotal"),
    ("Налог на прибыль", "article"),
    ("Чистая прибыль", "subtotal"),
]


def connect(data_dir: Path) -> duckdb.DuckDBPyConnection:
    """Загрузить исходники в DuckDB и построить представления legs и pl_legs."""
    con = duckdb.connect()
    for table in ("journal", "accounts", "mapping"):
        path = (Path(data_dir) / f"{table}.csv").resolve().as_posix().replace("'", "''")
        con.execute(f"CREATE TABLE {table} AS "
                    f"SELECT * FROM read_csv('{path}', header=true, all_varchar=true)")
    con.execute("""
        CREATE VIEW legs AS
        SELECT entry_id, substr(date, 1, 7) AS month, debit AS account, 'D' AS side,
               coalesce(cost_item, '') AS cost_item, -CAST(amount AS DECIMAL(18, 2)) AS signed
        FROM journal
        UNION ALL
        SELECT entry_id, substr(date, 1, 7), credit, 'C',
               coalesce(cost_item, ''), CAST(amount AS DECIMAL(18, 2))
        FROM journal
    """)
    con.execute("""
        CREATE VIEW pl_legs AS
        SELECT l.*, m.article
        FROM legs l
        JOIN accounts a ON a.code = l.account AND a.type = 'pl'
        LEFT JOIN mapping m
          ON m.account = l.account AND m.side = l.side AND coalesce(m.cost_item, '') = l.cost_item
    """)
    return con


def to_wide(long: pd.DataFrame, lines: list[tuple[str, str]]) -> pd.DataFrame:
    """Длинная таблица (month, line, value) → строки P&L × месяцы, с промежуточными итогами."""
    wide = long.pivot_table(index="line", columns="month", values="value", aggfunc="sum").fillna(0.0)
    months = sorted(wide.columns)
    rows, running = {}, pd.Series(0.0, index=months)
    for name, kind in lines:
        if kind == "article":
            value = wide.loc[name, months] if name in wide.index else pd.Series(0.0, index=months)
            running = running + value
        else:
            value = running
        rows[name] = value.round(2)
    result = pd.DataFrame(rows).T
    result.columns.name = "month"
    return result


def management_pl(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    long = con.execute(f"""
        SELECT month, article AS line, CAST(sum(signed) AS DOUBLE) AS value
        FROM pl_legs
        WHERE article IS NOT NULL AND article <> '{TECH}'
        GROUP BY ALL
    """).df()
    return to_wide(long, MANAGEMENT_LINES)


def accounting_pl(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    long = con.execute("""
        SELECT month, line, CAST(sum(signed) AS DOUBLE) AS value
        FROM (
            SELECT month, signed, CASE
                WHEN account IN ('90.01', '90.03') THEN 'Выручка'
                WHEN account = '90.02' THEN 'Себестоимость продаж'
                WHEN account = '90.07' THEN 'Коммерческие расходы'
                WHEN account = '90.08' THEN 'Управленческие расходы'
                WHEN account IN ('91.01', '91.02') THEN 'Прочие доходы и расходы'
                WHEN account = '99' AND cost_item = 'Налог на прибыль' THEN 'Налог на прибыль'
            END AS line
            FROM legs
        )
        WHERE line IS NOT NULL
        GROUP BY ALL
    """).df()
    return to_wide(long, ACCOUNTING_LINES)
