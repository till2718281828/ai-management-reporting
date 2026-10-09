"""Контроли выпуска. Любая ошибка останавливает выпуск: отчёт не собирается.

1. Все счета проводок есть в плане счетов; каждая нога по счёту результата разнесена маппингом.
2. Затратные счета и 90/91 закрыты помесячно (сальдо оборотов = 0) — технические ноги не «висят».
3. Сверка с главной книгой: выручка = Кт 90.01 − Дт 90.03; прибыль от продаж = закрытие 90.09 на 99;
   чистая прибыль = обороты 99.
4. УУ = БУ: итоговые строки управленческого и бухгалтерского P&L равны по каждому месяцу.

controls.md пишется всегда — и при успехе, и при ошибках.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

TOL = 0.005  # рубли: допуск только на представление чисел
CLOSED_GROUPS = ("20", "26", "44", "90", "91")
COMMON_LINES = ["Выручка", "Прибыль от продаж", "Прибыль до налога", "Чистая прибыль"]


def _ledger_totals(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute("""
        SELECT month,
               CAST(sum(signed) FILTER (WHERE account IN ('90.01', '90.03')) AS DOUBLE) AS revenue,
               CAST(-sum(signed) FILTER (WHERE account = '90.09') AS DOUBLE) AS sales_profit,
               CAST(sum(signed) FILTER (WHERE account = '99') AS DOUBLE) AS net_profit
        FROM legs
        GROUP BY month
    """).df().set_index("month").fillna(0.0)


def run_controls(con: duckdb.DuckDBPyConnection, mgmt: pd.DataFrame,
                 acct: pd.DataFrame) -> tuple[list[str], list[str]]:
    """Вернуть (ошибки, строки отчёта)."""
    errors: list[str] = []
    report: list[str] = []

    def check(title: str, problems: list[str]) -> None:
        report.append(f"- {'✔' if not problems else '✘'} {title}")
        report.extend(f"  - {p}" for p in problems)
        errors.extend(f"{title}: {p}" for p in problems)

    unknown = con.execute("""
        SELECT account, count(*) FROM legs
        WHERE account NOT IN (SELECT code FROM accounts) GROUP BY ALL ORDER BY ALL
    """).fetchall()
    check("Все счета проводок есть в плане счетов",
          [f"счёт {a} не найден в accounts.csv — {n} ног" for a, n in unknown])

    unmapped = con.execute("""
        SELECT account, side, cost_item, count(*) AS n FROM pl_legs
        WHERE article IS NULL GROUP BY ALL ORDER BY ALL
    """).fetchall()
    check("Все ноги по счетам результата разнесены маппингом",
          [f"нет правила: счёт {a}, сторона {s}, статья затрат «{c}» — {n} ног" for a, s, c, n in unmapped])

    groups = ", ".join(f"'{g}'" for g in CLOSED_GROUPS)
    unclosed = con.execute(f"""
        SELECT month, split_part(account, '.', 1) AS grp, CAST(sum(signed) AS DOUBLE) AS rest
        FROM legs WHERE split_part(account, '.', 1) IN ({groups})
        GROUP BY ALL HAVING abs(sum(signed)) > {TOL} ORDER BY ALL
    """).fetchall()
    check("Счета 20, 26, 44, 90, 91 закрыты помесячно",
          [f"{m}: счёт {g} не закрыт, остаток {r:,.2f}" for m, g, r in unclosed])

    gl = _ledger_totals(con).reindex(mgmt.columns, fill_value=0.0)
    acct = acct.reindex(columns=mgmt.columns, fill_value=0.0)
    gl_problems = []
    for line, col in [("Выручка", "revenue"), ("Прибыль от продаж", "sales_profit"),
                      ("Чистая прибыль", "net_profit")]:
        for month in mgmt.columns:
            diff = mgmt.at[line, month] - gl.at[month, col]
            if abs(diff) > TOL:
                gl_problems.append(f"{month}: {line} — УУ {mgmt.at[line, month]:,.2f}, "
                                   f"главная книга {gl.at[month, col]:,.2f}")
    check("Сверка с главной книгой (выручка, 90.09, 99)", gl_problems)

    inv_problems = []
    for line in COMMON_LINES:
        for month in mgmt.columns:
            diff = mgmt.at[line, month] - acct.at[line, month]
            if abs(diff) > TOL:
                inv_problems.append(f"{month}: {line} — УУ {mgmt.at[line, month]:,.2f}, "
                                    f"БУ {acct.at[line, month]:,.2f}")
    check("УУ = БУ по выручке, прибыли от продаж, прибыли до налога и чистой прибыли", inv_problems)

    return errors, report


def write_controls(out_dir: Path, errors: list[str], report: list[str]) -> None:
    verdict = "Все тождества сходятся." if not errors else f"Ошибки: {len(errors)}. Выпуск остановлен."
    text = "# Контроли выпуска\n\n" + "\n".join(report) + f"\n\n**{verdict}**\n"
    (Path(out_dir) / "controls.md").write_text(text, encoding="utf-8", newline="\n")
