"""Реестр чисел: единственный источник чисел для отчёта.

Правило: число попадает в отчёт только из реестра, а в реестр — только из расчёта по исходникам.
Отчёт (HTML, Excel) реестр читает и ничего не пересчитывает; проверка сверяет отчёт с реестром,
а реестр — со своим пересчётом из исходников.

Колонки: id, indicator, measure, period, value, unit, source, shown_in, level.
  id      — номер строки выпуска; реестры между собой сверять по ключу (indicator, measure, period)
  measure — значение | изменение | изменение % | рентабельность % | отклонение сверх роста выручки
  unit    — ₽ (рубли с копейками) | %
  source  — исходники или расчёт из других строк реестра
  shown_in — html (отчёт руководителю) | xlsx (помесячная книга)
  level   — A: пересчитано из первичных данных
"""
from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import pandas as pd

from pipeline.build_pl import MANAGEMENT_LINES

SOURCE = "journal.csv × mapping.csv"
DERIVED = "расчёт из значений реестра"
MARGIN_LINES = ["Валовая прибыль", "EBITDA", "Прибыль от продаж", "Чистая прибыль"]
COST_ARTICLES = [name for name, kind in MANAGEMENT_LINES
                 if kind == "article" and name not in ("Выручка", "Налог на прибыль")]


class Periods(NamedTuple):
    """Метки периодов отчёта и месяцы, из которых они складываются."""
    month: str
    month_ly: str
    ytd: str
    ytd_ly: str
    months: dict[str, list[str]]


def periods(mgmt: pd.DataFrame) -> Periods:
    """Отчётный месяц, тот же месяц прошлого года, нарастающие итоги текущего и прошлого года."""
    month = max(mgmt.columns)
    year, mm = int(month[:4]), int(month[5:])
    ytd = [f"{year}-{m:02d}" for m in range(1, mm + 1)]
    ytd_ly = [f"{year - 1}-{m:02d}" for m in range(1, mm + 1)]
    missing = [m for m in ytd + ytd_ly if m not in mgmt.columns]
    if missing:
        raise ValueError(f"для сравнения с прошлым годом нет данных за месяцы: {', '.join(missing)}")
    month_ly, label_ytd, label_ytd_ly = f"{year - 1}-{mm:02d}", f"{mm}М{year}", f"{mm}М{year - 1}"
    return Periods(month, month_ly, label_ytd, label_ytd_ly,
                   {month: [month], month_ly: [month_ly], label_ytd: ytd, label_ytd_ly: ytd_ly})


def change_pct(cur: float, base: float) -> float | None:
    """Изменение в % к модулю базы: рост убытка — минус, рост прибыли — плюс. Нулевая база — нет числа."""
    return None if base == 0 else (cur - base) / abs(base) * 100


def build_registry(mgmt: pd.DataFrame) -> pd.DataFrame:
    """Реестр: помесячные значения (для Excel) и показатели отчёта (для HTML).

    Отклонение статьи затрат сверх роста выручки = факт − база × (выручка факта / выручка базы).
    Минус — затраты выросли быстрее выручки. Формулы показателей описаны и в docs/methodology.md —
    по ним работает независимая проверка.
    """
    rows: list[tuple] = []

    def add(indicator: str, measure: str, period: str, value: float | None, unit: str,
            source: str, shown_in: str) -> None:
        if value is None:
            return
        rows.append((indicator, measure, period, round(float(value), 2 if unit == "₽" else 1), unit,
                     source, shown_in))

    for line, _ in MANAGEMENT_LINES:
        for month in mgmt.columns:
            add(line, "значение", month, mgmt.at[line, month], "₽", SOURCE, "xlsx")

    per = periods(mgmt)
    values = {label: mgmt[months].sum(axis=1) for label, months in per.months.items()}
    comparisons = [(per.month, per.month_ly), (per.ytd, per.ytd_ly)]

    for line, _ in MANAGEMENT_LINES:
        for label in (per.ytd, per.ytd_ly):
            add(line, "значение", label, values[label][line], "₽", SOURCE, "html")
        for cur, base in comparisons:
            label = f"{cur} к {base}"
            add(line, "изменение", label, values[cur][line] - values[base][line], "₽", DERIVED, "html")
            add(line, "изменение %", label, change_pct(values[cur][line], values[base][line]), "%",
                DERIVED, "html")

    for line in MARGIN_LINES:
        for label in (per.month, per.month_ly, per.ytd, per.ytd_ly):
            revenue = values[label]["Выручка"]
            add(line, "рентабельность %", label,
                None if revenue == 0 else values[label][line] / revenue * 100, "%", DERIVED, "html")

    for cur, base in comparisons:
        if values[base]["Выручка"] == 0:
            continue
        scale = values[cur]["Выручка"] / values[base]["Выручка"]
        for article in COST_ARTICLES:
            add(article, "отклонение сверх роста выручки", f"{cur} к {base}",
                values[cur][article] - values[base][article] * scale, "₽", DERIVED, "html")

    df = pd.DataFrame(rows, columns=["indicator", "measure", "period", "value", "unit", "source", "shown_in"])
    df.insert(0, "id", [f"R{i:04d}" for i in range(1, len(df) + 1)])
    df["level"] = "A"
    return df


def write_registry(out_dir: Path, registry: pd.DataFrame) -> None:
    registry.to_csv(Path(out_dir) / "registry.csv", index=False, encoding="utf-8", lineterminator="\n")


def read_registry(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype={"period": str}, encoding="utf-8")


def lookup(registry: pd.DataFrame, indicator: str, measure: str, period: str) -> float:
    hit = registry[(registry["indicator"] == indicator) & (registry["measure"] == measure)
                   & (registry["period"] == period)]
    if len(hit) != 1:
        raise KeyError(f"в реестре нет числа: {indicator} / {measure} / {period}")
    return float(hit["value"].iloc[0])
