"""Перевыпуск: что изменилось относительно отправленного выпуска — было, стало, причина.

Отчёт за месяц уже ушёл руководителю (выпуск 1), потом в учёт попали правки — поздняя проводка,
переклассификация. Выпуск 2 за тот же отчётный месяц сверяется с реестром отправленного выпуска
по ключу (показатель, мера, период), а не по id.

Причину изменения даёт человек — владелец статьи — в data/…/reissue_reasons.csv
(article, month, owner, cause): одна строка на статью P&L за месяц. Конвейер причин не придумывает.

Контроль (ошибка останавливает выпуск, как контроли pipeline/controls.py):
1. статья P&L за месяц изменилась, а причины нет — «изменилось без причины»;
2. причина есть, а статья за этот месяц не изменилась — «причина без изменения»;
3. производное число (итог, нарастающий итог, %, отклонение) изменилось, а ни одна статья за месяц,
   из которых оно складывается, не изменилась — тоже «без причины» (например, поменяли формулу);
4. строка есть только в одном из выпусков или отчётный месяц другой — сверять нечего.

Изменение — по точному сравнению: ₽ — в копейках, % — в единицах последнего знака реестра
(одна десятая процента, как в отчёте). Меньшее, чем копейка или 0,1 п. п., изменением не считается.

Каждое изменившееся «значение» получает в реестре выпуска 2 две строки: «было при отправке»
(из отправленного реестра) и «изменение к отправке» (стало − было); «стало» — сама строка «значение».
Блок отчёта «Что изменилось с прошлой отправки» выводит эти три числа и причину.
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import NamedTuple

import pandas as pd

from pipeline.build_pl import MANAGEMENT_LINES
from pipeline.manifest import canonical_hash

REASONS_FILE = "reissue_reasons.csv"
REASON_COLUMNS = ["article", "month", "owner", "cause"]
WAS = "было при отправке"
DELTA = "изменение к отправке"
REISSUE_MEASURES = (WAS, DELTA)
VALUE = "значение"
DERIVED = "расчёт из значений реестра"
MONTH = re.compile(r"\d{4}-\d{2}")
YTD = re.compile(r"(\d{1,2})М(\d{4})")
ORDER = {name: i for i, (name, _) in enumerate(MANAGEMENT_LINES)}
ARTICLES = [name for name, kind in MANAGEMENT_LINES if kind == "article"]
WITH_REVENUE = ("рентабельность %", "отклонение сверх роста выручки")

Key = tuple[str, str, str]


class Previous(NamedTuple):
    """Отправленный выпуск: его реестр и канонический SHA-256 файла реестра."""
    registry: pd.DataFrame
    sha256: str
    path: Path


def load_previous(path: Path) -> Previous:
    """Папка отправленного выпуска или сам registry.csv."""
    path = Path(path)
    if path.is_dir():
        path = path / "registry.csv"
    if not path.exists():
        raise FileNotFoundError(f"реестр отправленного выпуска не найден: {path}")
    registry = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    sha, _ = canonical_hash(path)
    return Previous(registry, sha, path)


def load_reasons(data_dir: Path) -> pd.DataFrame:
    path = Path(data_dir) / REASONS_FILE
    if not path.exists():
        return pd.DataFrame(columns=REASON_COLUMNS)
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")


def months_of(period: str) -> list[str]:
    """Месяцы, из которых складывается период: месяц, нарастающий итог «9М2026», сравнение «A к B»."""
    if " к " in period:
        cur, base = period.split(" к ")
        return months_of(cur) + months_of(base)
    if MONTH.fullmatch(period):
        return [period]
    ytd = YTD.fullmatch(period)
    if ytd:
        mm, year = int(ytd.group(1)), ytd.group(2)
        return [f"{year}-{m:02d}" for m in range(1, mm + 1)]
    raise ValueError(f"неизвестный период: {period}")


def lines_of(indicator: str, measure: str) -> set[str]:
    """Статьи P&L, из которых складывается число: статья — сама, итог — все статьи выше него."""
    if indicator in ARTICLES:
        lines = {indicator}
    else:
        lines = {a for a in ARTICLES if ORDER[a] < ORDER[indicator]}
    if measure in WITH_REVENUE:
        lines.add("Выручка")
    return lines


def depends_on(key: Key) -> set[tuple[str, str]]:
    """(статья, месяц), от которых зависит строка реестра."""
    indicator, measure, period = key
    return {(a, m) for a in lines_of(indicator, measure) for m in months_of(period)}


def is_primary(indicator: str, measure: str, period: str) -> bool:
    """Первичное число — статья P&L за месяц: только у него бывает своя причина."""
    return indicator in ARTICLES and measure == VALUE and bool(MONTH.fullmatch(period))


def to_kopecks_or_tenths(value, unit: str) -> int:
    """Значение в единицах сравнения: копейки для ₽, десятые доли для %."""
    scale = 100 if unit == "₽" else 10
    return int((Decimal(str(value)) * scale).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def report_month(registry: pd.DataFrame) -> str:
    return max(p for p in registry["period"] if MONTH.fullmatch(str(p)))


def compare(previous: pd.DataFrame, current: pd.DataFrame) -> pd.DataFrame:
    """Строки, изменившиеся к отправленному реестру: indicator, measure, period, unit, was, now.

    was / now — None, если строки нет в одном из выпусков. Строки перевыпуска (было, изменение
    к отправке) в сравнение не входят: у отправленного выпуска могли быть свои.
    """
    def table(df: pd.DataFrame) -> dict[Key, tuple[str, str]]:
        df = df[~df["measure"].isin(REISSUE_MEASURES)]
        return {(i, m, str(p)): (u, v) for i, m, p, u, v in
                zip(df["indicator"], df["measure"], df["period"], df["unit"], df["value"])}

    old, new = table(previous), table(current)
    rows = []
    for key in list(new) + [k for k in old if k not in new]:
        o, n = old.get(key), new.get(key)
        unit = (n or o)[0]
        if o is not None and n is not None and to_kopecks_or_tenths(o[1], unit) == to_kopecks_or_tenths(n[1], unit):
            continue
        rows.append((*key, unit, None if o is None else float(o[1]), None if n is None else float(n[1])))
    return pd.DataFrame(rows, columns=["indicator", "measure", "period", "unit", "was", "now"])


def _money(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "нет строки"
    text = f"{abs(value):,.2f}".replace(",", " ").replace(".", ",")
    return f"−{text}" if value < 0 else text


def reconcile(previous: pd.DataFrame | None, current: pd.DataFrame,
              reasons: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Изменения с причинами и ошибки контроля перевыпуска.

    Возвращает таблицу изменений с колонками primary, owner, cause (для статьи за месяц) и basis —
    статьи за месяц, из-за которых изменилось производное число.
    """
    problems: list[str] = []
    if previous is None:
        if len(reasons):
            problems.append(f"в данных есть {REASONS_FILE}, а отправленный выпуск не указан (--previous): "
                            "причины перевыпуска не к чему приложить")
        return compare(current.iloc[0:0], current.iloc[0:0]), problems

    if report_month(previous) != report_month(current):
        problems.append(f"отчётный месяц выпуска {report_month(current)} не совпадает с отправленным "
                        f"{report_month(previous)}: перевыпуск сверяет выпуски за один месяц")
        return compare(current.iloc[0:0], current.iloc[0:0]), problems

    reason_map: dict[tuple[str, str], tuple[str, str]] = {}
    for article, month, owner, cause in zip(reasons["article"], reasons["month"], reasons["owner"],
                                            reasons["cause"]):
        key = (article, month)
        if key in reason_map:
            problems.append(f"причина для «{article}» ({month}) указана дважды")
        if not cause.strip() or not owner.strip():
            problems.append(f"причина для «{article}» ({month}): не заполнены причина или её владелец")
        if re.search(r"\d", cause):
            problems.append(f"причина для «{article}» ({month}) содержит числа — числа отчёта берутся "
                            "только из реестра, в причине их быть не должно")
        reason_map[key] = (owner, cause)

    changes = compare(previous, current)
    changes["primary"] = [is_primary(i, m, p) for i, m, p in
                          zip(changes["indicator"], changes["measure"], changes["period"])]
    primary_keys = {(i, p) for i, p, prim in zip(changes["indicator"], changes["period"], changes["primary"])
                    if prim}
    owners, causes, bases = [], [], []
    for row in changes.itertuples(index=False):
        owner = cause = basis = ""
        what = f"{row.indicator} ({row.measure}, {row.period})"
        if pd.isna(row.was) or pd.isna(row.now):
            problems.append(f"{what}: строка есть только в одном из выпусков — {_money(row.was)} → "
                            f"{_money(row.now)}")
        elif row.primary:
            if (row.indicator, row.period) in reason_map:
                owner, cause = reason_map[(row.indicator, row.period)]
            else:
                problems.append(f"{what}: было {_money(row.was)}, стало {_money(row.now)} — "
                                f"изменилось без причины в {REASONS_FILE}")
        else:
            deps = depends_on((row.indicator, row.measure, row.period)) & primary_keys
            if deps:
                basis = ", ".join(f"{a} ({m})" for a, m in sorted(deps, key=lambda d: (d[1], ORDER[d[0]])))
            else:
                problems.append(f"{what}: было {_money(row.was)}, стало {_money(row.now)} — изменилось, "
                                "хотя ни одна статья за месяц, из которых оно складывается, не изменилась")
        owners.append(owner)
        causes.append(cause)
        bases.append(basis)
    changes["owner"], changes["cause"], changes["basis"] = owners, causes, bases

    for (article, month) in reason_map:
        if (article, month) not in primary_keys:
            problems.append(f"причина для «{article}» ({month}) есть, а статья за этот месяц не изменилась — "
                            "причина без изменения")
    return changes, problems


def shown_changes(changes: pd.DataFrame) -> pd.DataFrame:
    """Изменения, которые показываются руководителю: все изменившиеся «значения».

    Сначала статьи за месяц (у них причина), затем итоги и нарастающие итоги (у них — из-за каких статей).
    """
    shown = changes[(changes["measure"] == VALUE) & changes["was"].notna() & changes["now"].notna()].copy()
    shown["_month"] = shown["period"].map(lambda p: 0 if MONTH.fullmatch(p) else 1)
    shown["_order"] = shown["indicator"].map(ORDER)
    shown = shown.sort_values(["primary", "_month", "period", "_order"], ascending=[False, True, True, True])
    return shown.drop(columns=["_month", "_order"]).reset_index(drop=True)


def add_reissue_rows(registry: pd.DataFrame, changes: pd.DataFrame, previous: Previous) -> pd.DataFrame:
    """Дописать в реестр «было при отправке» и «изменение к отправке» для каждого изменившегося значения."""
    shown = shown_changes(changes)
    source = f"registry.csv отправленного выпуска, SHA-256 {previous.sha256[:16]}"
    rows = []
    for r in shown.itertuples(index=False):
        rows.append((r.indicator, WAS, r.period, round(r.was, 2), r.unit, source))
        rows.append((r.indicator, DELTA, r.period, round(r.now - r.was, 2), r.unit, DERIVED))
    if not rows:
        return registry
    extra = pd.DataFrame(rows, columns=["indicator", "measure", "period", "value", "unit", "source"])
    start = len(registry) + 1
    extra.insert(0, "id", [f"R{i:04d}" for i in range(start, start + len(extra))])
    extra["shown_in"] = ""
    extra["level"] = "A"
    return pd.concat([registry, extra[registry.columns]], ignore_index=True)
