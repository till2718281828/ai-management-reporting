"""Одностраничный отчёт руководителю: output/index.html.

Все числа берутся из реестра (registry.csv) — здесь ничего не считается, только выбирается и
форматируется. Причины отклонений — из data/commentary.csv (их пишет экономист; в тексте причин
чисел нет). Нет причины — так и написано.
"""
from __future__ import annotations

import html
from pathlib import Path

import pandas as pd

from pipeline.build_pl import MANAGEMENT_LINES
from pipeline.numbers import format_number

COMPANY = "ООО «СтальКонструкт»"
MONTHS_RU = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
             "сентябрь", "октябрь", "ноябрь", "декабрь"]
MONTHS_DATIVE = ["январю", "февралю", "марту", "апрелю", "маю", "июню", "июлю", "августу",
                 "сентябрю", "октябрю", "ноябрю", "декабрю"]
MONTH_LETTERS = "ЯФМАМИИАСОНД"
DEVIATIONS_MAX = 5
DEVIATION_FLOOR_RUB = 5_000_000  # меньшее отклонение руководителю не показываем

CSS = """
:root { --ink:#1d2433; --muted:#5b6475; --line:#e3e6ec; --bg:#ffffff; --panel:#f6f7f9;
        --bad:#b42318; --good:#067647; --accent:#2a5bd7; }
@media (prefers-color-scheme: dark) {
  :root { --ink:#e8ebf1; --muted:#a3abba; --line:#2c3342; --bg:#141821; --panel:#1c212c;
          --bad:#f97066; --good:#47cd89; --accent:#7aa2ff; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font:15px/1.5 -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }
main { max-width:980px; margin:0 auto; padding:28px 16px 48px; }
h1 { font-size:24px; margin:0 0 4px; } h2 { font-size:17px; margin:32px 0 12px; }
.sub { color:var(--muted); margin:0 0 20px; }
.tiles { display:grid; grid-template-columns:repeat(auto-fit, minmax(260px, 1fr)); gap:12px; }
.tile { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }
.tile .k { color:var(--muted); font-size:13px; } .tile .v { font-size:24px; font-weight:600; margin:4px 0; white-space:nowrap; }
.tile .d { font-size:13px; color:var(--muted); }
.bad { color:var(--bad); } .good { color:var(--good); }
ol.dev { padding-left:20px; } ol.dev li { margin:0 0 12px; } ol.dev .why { color:var(--muted); }
.table-wrap { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }
th, td { padding:6px 10px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; }
th:first-child, td:first-child { text-align:left; }
th { color:var(--muted); font-weight:500; font-size:13px; }
tr.sub td { font-weight:600; background:var(--panel); }
svg text { fill:var(--muted); font-size:11px; }
.legend { color:var(--muted); font-size:13px; }
.legend i { display:inline-block; width:10px; height:10px; border-radius:2px; margin:0 4px 0 12px; }
footer { margin-top:36px; color:var(--muted); font-size:13px; border-top:1px solid var(--line); padding-top:12px; }
"""


def _month_dative(month: str) -> str:
    return f"{MONTHS_DATIVE[int(month[5:]) - 1]} {month[:4]}"


def _month_name(month: str) -> str:
    return f"{MONTHS_RU[int(month[5:]) - 1]} {month[:4]}"


def _cls(value: float, good_if_positive: bool = True) -> str:
    if value == 0:
        return ""
    return "good" if (value > 0) == good_if_positive else "bad"


class Numbers:
    """Числа отчёта из реестра. Каждое выводится с привязкой к строке: id и формат (см. pipeline.numbers)."""

    def __init__(self, registry: pd.DataFrame) -> None:
        self.rows = {(i, m, p): (rid, v) for rid, i, m, p, v in
                     zip(registry["id"], registry["indicator"], registry["measure"], registry["period"],
                         registry["value"])}

    def value(self, indicator: str, measure: str, period: str) -> float:
        return self._row(indicator, measure, period)[1]

    def has(self, indicator: str, measure: str, period: str) -> bool:
        return (indicator, measure, period) in self.rows

    def show(self, indicator: str, measure: str, period: str, fmt: str) -> str:
        rid, value = self._row(indicator, measure, period)
        return f'<span data-r="{rid}" data-f="{fmt}">{format_number(value, fmt)}</span>'

    def _row(self, indicator: str, measure: str, period: str) -> tuple[str, float]:
        key = (indicator, measure, period)
        if key not in self.rows:
            raise KeyError(f"в реестре нет числа: {indicator} / {measure} / {period}")
        return self.rows[key]


def _labels(registry: pd.DataFrame) -> tuple[str, str, str, str]:
    months = sorted(p for p in registry["period"].unique() if len(p) == 7 and p[4] == "-")
    month = months[-1]
    month_ly = f"{int(month[:4]) - 1}{month[4:]}"
    ytd = next(p for p in registry["period"].unique() if p.endswith(f"М{month[:4]}"))
    ytd_ly = next(p for p in registry["period"].unique() if p.endswith(f"М{int(month[:4]) - 1}"))
    return month, month_ly, ytd, ytd_ly


def _tiles(n: Numbers, month: str, month_ly: str, ytd: str, ytd_ly: str) -> str:
    tiles = []

    def tile(title: str, value: str, detail: str) -> None:
        tiles.append(f'<div class="tile"><div class="k">{title}</div><div class="v">{value}</div>'
                     f'<div class="d">{detail}</div></div>')

    for line in ("Выручка", "EBITDA", "Чистая прибыль"):
        period = f"{ytd} к {ytd_ly}"
        cls = _cls(n.value(line, "изменение %", period))
        detail = f'<span class="{cls}">{n.show(line, "изменение %", period, "pct|+")} %</span> к {ytd_ly}'
        if line != "Выручка":
            detail += f' · рентабельность {n.show(line, "рентабельность %", ytd, "pct")} %'
        tile(f"{line}, {ytd}", f'{n.show(line, "значение", ytd, "mln")} млн ₽', detail)
    for line in ("Выручка", "Чистая прибыль"):
        period = f"{month} к {month_ly}"
        cls = _cls(n.value(line, "изменение %", period))
        tile(f"{line}, {_month_name(month)}", f'{n.show(line, "значение", month, "mln")} млн ₽',
             f'<span class="{cls}">{n.show(line, "изменение %", period, "pct|+")} %</span> '
             f"к {_month_dative(month_ly)}")
    tile(f"Рентабельность по чистой прибыли, {ytd}",
         f'{n.show("Чистая прибыль", "рентабельность %", ytd, "pct")} %',
         f'{n.show("Чистая прибыль", "рентабельность %", ytd_ly, "pct")} % за {ytd_ly}')
    return '<div class="tiles">' + "".join(tiles) + "</div>"


def _deviations(n: Numbers, registry: pd.DataFrame, commentary: pd.DataFrame, ytd: str, ytd_ly: str) -> str:
    """Статьи, ушедшие от пропорции к выручке сильнее порога. Затраты показаны модулем, рост — плюсом."""
    period = f"{ytd} к {ytd_ly}"
    measure = "отклонение сверх роста выручки"
    dev = registry[(registry["measure"] == measure) & (registry["period"] == period)]
    dev = dev[dev["value"].abs() >= DEVIATION_FLOOR_RUB]
    dev = dev.reindex(dev["value"].abs().sort_values(ascending=False).index).head(DEVIATIONS_MAX)
    causes = dict(zip(commentary["article"], commentary["cause"]))
    revenue_word = "росте" if n.value("Выручка", "изменение %", period) > 0 else "снижении"
    items = []
    for article, value in zip(dev["indicator"], dev["value"]):
        verdict = "хуже" if value < 0 else "лучше"
        cause = causes.get(article)
        why = html.escape(cause) if cause else "Причина не установлена — нужен разбор с владельцем статьи."
        # Статьи затрат отрицательны; изменение % — к модулю базы, минус = расходы выросли → показываем «neg».
        items.append(
            f'<li><b>{html.escape(article)}</b>: <span class="{_cls(value)}">на '
            f'{n.show(article, measure, period, "mln|abs")} млн ₽ {verdict} пропорции к выручке</span>. '
            f'Расходы {ytd}: {n.show(article, "значение", ytd, "mln|abs")} млн ₽ против '
            f'{n.show(article, "значение", ytd_ly, "mln|abs")} млн ₽ за {ytd_ly} '
            f'({n.show(article, "изменение %", period, "pct|neg|+")} %) при {revenue_word} выручки на '
            f'{n.show("Выручка", "изменение %", period, "pct|abs")} %.'
            f'<br><span class="why">{why}</span></li>')
    if not items:
        return "<p>Существенных отклонений нет.</p>"
    return '<ol class="dev">' + "".join(items) + "</ol>"


def _table(n: Numbers, month: str, month_ly: str, ytd: str, ytd_ly: str) -> str:
    head = (f"<tr><th>Статья, млн ₽</th><th>{ytd_ly}</th><th>{ytd}</th><th>Δ</th><th>Δ %</th>"
            f"<th>{month_ly}</th><th>{month}</th><th>Δ %</th></tr>")
    rows = []
    for line, kind in MANAGEMENT_LINES:
        def change_pct(cur: str, base: str) -> str:
            period = f"{cur} к {base}"
            return n.show(line, "изменение %", period, "pct|+") if n.has(line, "изменение %", period) else "—"
        cells = [n.show(line, "значение", ytd_ly, "mln"), n.show(line, "значение", ytd, "mln"),
                 n.show(line, "изменение", f"{ytd} к {ytd_ly}", "mln|+"), change_pct(ytd, ytd_ly),
                 n.show(line, "значение", month_ly, "mln"), n.show(line, "значение", month, "mln"),
                 change_pct(month, month_ly)]
        cls = ' class="sub"' if kind == "subtotal" else ""
        rows.append(f"<tr{cls}><td>{html.escape(line)}</td>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    return f'<div class="table-wrap"><table>{head}{"".join(rows)}</table></div>'


def _chart(reg: pd.DataFrame) -> str:
    """Выручка (столбики) и чистая прибыль (линия) по месяцам. Подписей-чисел нет — только форма."""
    monthly = reg[(reg["measure"] == "значение") & reg["period"].str.fullmatch(r"\d{4}-\d{2}")]
    months = sorted(monthly["period"].unique())
    rev = monthly[monthly["indicator"] == "Выручка"].set_index("period")["value"]
    net = monthly[monthly["indicator"] == "Чистая прибыль"].set_index("period")["value"]
    w, h, pad = 940, 200, 24
    top = max(rev.max(), 1)
    step = (w - 2 * pad) / len(months)
    bars, points, labels = [], [], []
    for i, m in enumerate(months):
        x = pad + i * step
        bh = (h - 2 * pad) * rev[m] / top
        bars.append(f'<rect x="{x + step * 0.15:.1f}" y="{h - pad - bh:.1f}" width="{step * 0.7:.1f}" '
                    f'height="{bh:.1f}" rx="2" fill="var(--accent)" opacity="0.35"/>')
        ny = h - pad - (h - 2 * pad) * net[m] / top * 4  # прибыль в масштабе ×4 для читаемости
        points.append(f"{x + step / 2:.1f},{ny:.1f}")
        labels.append(f'<text x="{x + step / 2:.1f}" y="{h - 6}" text-anchor="middle">'
                      f"{MONTH_LETTERS[int(m[5:]) - 1]}</text>")
    line = f'<polyline points="{" ".join(points)}" fill="none" stroke="var(--good)" stroke-width="2"/>'
    years = sorted({m[:4] for m in months})
    legend = (f'<div class="legend">{" — ".join(years)}: <i style="background:var(--accent);opacity:.35"></i>'
              f'выручка <i style="background:var(--good)"></i>чистая прибыль (в увеличенном масштабе)</div>')
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="Выручка и чистая прибыль по месяцам">'
            + "".join(bars) + line + "".join(labels) + "</svg>" + legend)


def render_html(out_dir: Path, registry: pd.DataFrame, commentary: pd.DataFrame, manifest: dict) -> Path:
    month, month_ly, ytd, ytd_ly = _labels(registry)
    n = Numbers(registry)
    sha = manifest["sources"]["journal.csv"]["sha256"][:16]
    body = f"""<main>
<h1>Управленческий P&amp;L · {ytd}</h1>
<p class="sub">{COMPANY} — демо-пример на синтетических данных · отчётный месяц: {_month_name(month)}</p>
{_tiles(n, month, month_ly, ytd, ytd_ly)}
<h2>Отклонения сверх роста выручки, {ytd} к {ytd_ly}</h2>
{_deviations(n, registry, commentary, ytd, ytd_ly)}
<h2>Помесячная динамика</h2>
{_chart(registry)}
<h2>P&amp;L</h2>
{_table(n, month, month_ly, ytd, ytd_ly)}
<footer>
Источник: проводки по счетам РСБУ (journal.csv), разнесены по статьям правилами mapping.csv;
SHA-256 journal.csv: {sha}…; проводки по {manifest['journal_last_date']} включительно.
Все числа отчёта взяты из реестра чисел выпуска и сверены с ним; контроли выпуска пройдены.
Оговорки: затраты закрываются на счёт продаж ежемесячно без незавершённого производства;
амортизация показана отдельной строкой, а не внутри себестоимости и управленческих расходов;
«отклонение сверх роста выручки» — факт статьи минус база прошлого года, пересчитанная на рост выручки;
Δ % считается к модулю базы, поэтому минус всегда означает изменение в худшую для прибыли сторону.
</footer>
</main>"""
    page = (f'<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>P&amp;L {ytd} — демо</title><style>{CSS}</style></head><body>{body}</body></html>\n")
    path = Path(out_dir) / "index.html"
    path.write_text(page, encoding="utf-8", newline="\n")
    return path
