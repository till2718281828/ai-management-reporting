"""Искажения готового отчёта: каждое меняет ровно одно число, которое видит руководитель.

Искажение вносится в index.html уже собранного и проверенного конвейером выпуска — так же, как
`python -m pipeline.run --inject-error`: ручная правка «на глаз» или ошибка агента, собиравшего текст.
Реестр, книга Excel и исходники не меняются.

Число отчёта — `<span data-r="<id>" data-f="<формат>">текст</span>` (docs/methodology.md, «Отображение»).
Мутатор выбирает одно такое число и меняет его текст, ссылку на строку реестра или снимает привязку;
один вид дописывает в текст новое число без привязки.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import pandas as pd

from pipeline.numbers import BOUND, SPACES, format_number

SECTION = re.compile(r'<section data-block="([^"]+)">')
WAS, VALUE = "было при отправке", "значение"
REISSUE_BLOCK = "reissue_changes"

# (тип, вид): подписи для таблицы. Порядок — порядок строк таблицы.
TYPES = {
    "sign": "Знак числа",
    "digit": "Разряд ×10",
    "period": "Число соседнего периода",
    "row": "Подмена строки",
    "rounding": "Округление сверх допуска",
    "unbound": "Число без привязки к реестру",
    "reissue": "Перевыпуск: правка без причины",
}
VARIANTS = {
    ("sign", "text"): "знак сменён",
    ("digit", "text"): "число ×10",
    ("period", "text"): "подменено число, ссылка прежняя",
    ("period", "link"): "подменены число и ссылка на строку реестра",
    ("row", "text"): "подменено число, ссылка прежняя",
    ("row", "link"): "подменены число и ссылка на строку реестра",
    ("rounding", "text"): "±0,1 в последнем знаке",
    ("unbound", "removed"): "снята привязка data-r",
    ("unbound", "added"): "дописано новое число",
    ("reissue", "was"): "«было» заменено на «стало»",
    ("reissue", "silent"): "число изменено, в блоке изменений его нет",
}
COMBOS = list(VARIANTS)
RELEASE_OF = {"reissue": 2}  # остальные типы искажают выпуск 1


class NoCandidate(Exception):
    """В отчёте нет числа, к которому применим мутатор."""


@dataclass
class Span:
    start: int
    end: int
    rid: str
    fmt: str
    text: str
    block: str


@dataclass
class Release:
    """Собранный выпуск: страница, числа на ней и реестр по id."""
    page: str
    spans: list[Span]
    rows: dict[str, dict]                     # id → строка реестра
    by_key: dict[tuple[str, str, str], str]   # (показатель, мера, период) → id
    cache: dict = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, folder: Path) -> "Release":
        page = (folder / "index.html").read_text(encoding="utf-8")
        registry = pd.read_csv(folder / "registry.csv", dtype=str, keep_default_na=False, encoding="utf-8-sig")
        rows = {r["id"]: r for r in registry.to_dict("records")}
        by_key = {(r["indicator"], r["measure"], r["period"]): rid for rid, r in rows.items()}
        return cls(page, parse_spans(page), rows, by_key)

    def key(self, rid: str) -> tuple[str, str, str]:
        r = self.rows[rid]
        return r["indicator"], r["measure"], r["period"]

    def shown(self, rid: str, fmt: str) -> str:
        """Как конвейер вывел бы строку реестра rid в формате fmt."""
        return format_number(float(self.rows[rid]["value"]), fmt)

    def changed_keys(self) -> set[tuple[str, str, str]]:
        """Значения, изменившиеся к отправленному выпуску (у них есть строка «было при отправке»)."""
        return {(i, VALUE, p) for i, m, p in self.by_key if m == WAS}


@dataclass
class Mutation:
    type: str
    variant: str
    release: int
    block: str
    rid: str
    key: tuple[str, str, str]
    before: str
    after: str
    page: str = field(repr=False)
    new_rid: str = ""
    new_key: tuple[str, str, str] | None = None

    @property
    def label(self) -> str:
        return f"{TYPES[self.type]}: {VARIANTS[(self.type, self.variant)]}"


def parse_spans(page: str) -> list[Span]:
    sections = [(m.start(), m.group(1)) for m in SECTION.finditer(page)]

    def block_of(pos: int) -> str:
        name = ""
        for start, block in sections:
            if start <= pos:
                name = block
        return name

    return [Span(m.start(), m.end(), m.group(1), m.group(2), m.group(3), block_of(m.start()))
            for m in BOUND.finditer(page)]


# ---------- текст числа ----------

def parse(text: str) -> Decimal:
    return Decimal(re.sub(SPACES, "", text).replace("−", "-").replace("+", "").replace(",", "."))


def render(value: Decimal, fmt: str) -> str:
    """Текст числа уровня отчёта (уже в млн или %) форматом конвейера: «1 234,5», «−», «+».

    Значение уже в единицах отчёта, поэтому масштаб «mln» и флаги abs/neg не применяются — только «+».
    Формат «pct» у format_number как раз не масштабирует.
    """
    return format_number(value, "pct|+" if "+" in fmt.split("|")[1:] else "pct")


def _replace(page: str, span: Span, html: str) -> str:
    return page[:span.start] + html + page[span.end:]


def _span_html(rid: str, fmt: str, text: str) -> str:
    return f'<span data-r="{rid}" data-f="{fmt}">{text}</span>'


def _pick(rng: random.Random, items: list):
    if not items:
        raise NoCandidate
    return items[rng.randrange(len(items))]


def _mutation(rel: Release, kind: str, variant: str, span: Span, html: str, after: str,
              release: int) -> Mutation:
    return Mutation(kind, variant, release, span.block, span.rid, rel.key(span.rid), span.text, after,
                    _replace(rel.page, span, html))


def _report_spans(rel: Release) -> list[Span]:
    """Числа вне блока изменений перевыпуска."""
    return [s for s in rel.spans if s.block != REISSUE_BLOCK]


# ---------- мутаторы ----------

def mutate_text(rel: Release, kind: str, rng: random.Random, release: int = 1) -> Mutation:
    """Знак, разряд ×10, округление ±0,1: меняется только текст числа."""
    spans = [s for s in _report_spans(rel) if kind == "rounding" or parse(s.text) != 0]
    span = _pick(rng, spans)
    value = parse(span.text)
    if kind == "sign":
        new = -value
    elif kind == "digit":
        new = value * 10
    else:
        step = Decimal("0.1") if rng.random() < 0.5 else Decimal("-0.1")
        if value + step < 0 <= value and "abs" in span.fmt:   # модуль не бывает отрицательным
            step = Decimal("0.1")
        new = value + step
    after = render(new, span.fmt)
    return _mutation(rel, kind, "text", span, _span_html(span.rid, span.fmt, after), after, release)


def mutate_neighbour(rel: Release, kind: str, variant: str, rng: random.Random) -> Mutation:
    """Число другого периода той же строки (kind=period) или другой строки того же периода (kind=row).

    variant=text — подменён только текст, ссылка data-r прежняя; variant=link — ссылка ведёт на ту строку
    реестра, чьё число теперь показано (так ошибается агент, взявший не ту строку реестра).
    """
    if kind not in rel.cache:   # варианты не зависят от случая — считаются один раз на выпуск
        options = []
        for span in _report_spans(rel):
            ind, measure, period = rel.key(span.rid)
            for (i2, m2, p2), rid2 in sorted(rel.by_key.items()):
                same = (i2 == ind and m2 == measure and p2 != period) if kind == "period" else \
                       (i2 != ind and m2 == measure and p2 == period)
                if same and rel.shown(rid2, span.fmt) != span.text:
                    options.append((span, rid2))
        rel.cache[kind] = options
    span, rid2 = _pick(rng, rel.cache[kind])
    after = rel.shown(rid2, span.fmt)
    rid = rid2 if variant == "link" else span.rid
    m = _mutation(rel, kind, variant, span, _span_html(rid, span.fmt, after), after, 1)
    if variant == "link":
        m.new_rid, m.new_key = rid2, rel.key(rid2)
    return m


def mutate_unbound(rel: Release, variant: str, rng: random.Random) -> Mutation:
    """removed — число осталось, разметку data-r сняли; added — в текст дописано новое число."""
    if variant == "removed":
        span = _pick(rng, _report_spans(rel))
        return _mutation(rel, "unbound", "removed", span, span.text, span.text, 1)
    span = _pick(rng, [s for s in _report_spans(rel) if s.fmt.startswith("mln") and parse(s.text) != 0])
    added = render(abs(parse(span.text)) * Decimal(rng.randint(10, 90)) / 100, "mln")
    tail = rel.page[span.end:span.end + 12]
    insert_at = span.end + (tail.index("млн ₽") + len("млн ₽") if "млн ₽" in tail else 0)
    page = rel.page[:insert_at] + f", в т. ч. разовый эффект {added} млн ₽" + rel.page[insert_at:]
    return Mutation("unbound", "added", 1, span.block, span.rid, rel.key(span.rid), "", added, page)


def mutate_reissue(rel: Release, variant: str, rng: random.Random) -> Mutation:
    """Перевыпуск. was — в блоке изменений «было» заменено числом «стало» (правка спрятана);
    silent — число, которое не менялось к отправке, изменено в отчёте без причины и без строки в блоке."""
    if variant == "was":
        options = []
        for span in rel.spans:
            ind, measure, period = rel.key(span.rid)
            if span.block == REISSUE_BLOCK and measure == WAS:
                now = rel.shown(rel.by_key[(ind, VALUE, period)], span.fmt)
                if now != span.text:
                    options.append((span, now))
        span, after = _pick(rng, options)
        return _mutation(rel, "reissue", "was", span, _span_html(span.rid, span.fmt, after), after, 2)
    changed = rel.changed_keys()
    spans = [s for s in _report_spans(rel)
             if rel.key(s.rid)[1] == VALUE and rel.key(s.rid) not in changed and parse(s.text) != 0]
    span = _pick(rng, spans)
    value = float(rel.rows[span.rid]["value"])
    for _ in range(20):
        share = rng.uniform(0.005, 0.05) * (1 if rng.random() < 0.5 else -1)
        after = format_number(value * (1 + share), span.fmt)
        if after != span.text:
            break
    else:
        raise NoCandidate
    return _mutation(rel, "reissue", "silent", span, _span_html(span.rid, span.fmt, after), after, 2)


def mutate(kind: str, variant: str, release1: Release, release2: Release, rng: random.Random) -> Mutation:
    if kind in ("sign", "digit", "rounding"):
        return mutate_text(release1, kind, rng)
    if kind in ("period", "row"):
        return mutate_neighbour(release1, kind, variant, rng)
    if kind == "unbound":
        return mutate_unbound(release1, variant, rng)
    if kind == "reissue":
        return mutate_reissue(release2, variant, rng)
    raise ValueError(f"неизвестный тип искажения: {kind}")


def report_numbers(page: str) -> list[tuple[str, str, str, str]]:
    """Все числа страницы: размеченные (span, id, формат, текст) и свободные в видимом тексте ('free', …)."""
    found = [("span", m.group(1), m.group(2), m.group(3)) for m in BOUND.finditer(page)]
    text = re.sub(r"<(style|script)\b.*?</\1>", " ", BOUND.sub(" ", page), flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\d{4}-\d{2}(?:-\d{2})?|\b\d{1,2}М\d{4}\b|\b(?:19|20)\d{2}\b(?!,\d)|\b[0-9a-f]{12,}\b", " ", text)
    found += [("free", "", "", m.group().strip())
              for m in re.finditer(r"[+−-]?\d[\d   ]*(?:,\d+)?", text)]
    return found
