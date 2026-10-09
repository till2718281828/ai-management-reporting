"""Числа отчёта: формат и проверка привязки к реестру.

Каждое число в HTML выводится как <span data-r="R0123" data-f="mln|+">+370,4</span>: id строки реестра
и формат. Проверка берёт значение строки по id, форматирует заново и сравнивает с текстом — так
ловится и чужое число, и потерянный знак. Число в видимом тексте без такой привязки — ошибка.

Формат: пробел — разделитель тысяч, запятая — десятичный знак, «−» — минус. Суммы — в млн ₽
с одним знаком, проценты — с одним знаком. Округление — половина вверх (как в Excel), не банковское.
Флаги формата: «+» — плюс у положительных, «abs» — модуль, «neg» — сменить знак.
"""
from __future__ import annotations

import html
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pandas as pd

ONE_PLACE = Decimal("0.1")
MILLION = Decimal(1_000_000)
SPACES = "[   ]"

NUMBER = re.compile(rf"[+−-]?\d{{1,3}}(?:{SPACES}\d{{3}})*(?:,\d+)?|[+−-]?\d+(?:,\d+)?")
BOUND = re.compile(r'<span data-r="([^"]*)" data-f="([^"]*)">([^<]*)</span>')
# Что числом отчёта не считается: даты и месяцы, метки периодов (9М2026), годы, хэши.
NOT_NUMBERS = [
    re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b"),
    re.compile(r"\b\d{1,2}М\d{4}\b"),
    re.compile(r"\b(?:19|20)\d{2}\b(?!,\d)"),
    re.compile(r"\b[0-9a-f]{12,}\b"),
    re.compile(r"(?i)\bsha-?256\b"),
]


def _round(value: Decimal) -> Decimal:
    return value.quantize(ONE_PLACE, rounding=ROUND_HALF_UP)


def _group(value: Decimal) -> str:
    sign = "−" if value < 0 else ""
    whole, frac = f"{abs(value):.1f}".split(".")
    groups = []
    while whole:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    return f"{sign}{' '.join(groups)},{frac}"


def _signed(text: str) -> str:
    return f"+{text}" if not text.startswith(("−", "0,0")) else text


def mln(value_rub: float, signed: bool = False) -> str:
    """Рубли → «1 234,5» (млн); signed=True добавляет «+» к положительным."""
    text = _group(_round(Decimal(str(value_rub)) / MILLION))
    return _signed(text) if signed else text


def pct(value: float, signed: bool = False) -> str:
    text = _group(_round(Decimal(str(value))))
    return _signed(text) if signed else text


def format_number(value: float, fmt: str) -> str:
    """fmt = «mln» | «pct» и флаги через «|»: «+», «abs», «neg». Например «pct|neg|+»."""
    base, *flags = fmt.split("|")
    if "neg" in flags:
        value = -value
    if "abs" in flags:
        value = abs(value)
    if base not in ("mln", "pct"):
        raise ValueError(f"неизвестный формат числа: {fmt}")
    return (mln if base == "mln" else pct)(value, signed="+" in flags)


def report_numbers(html_text: str) -> list[tuple[Decimal, str]]:
    """Числа из видимого текста HTML (без тегов, стилей и служебных последовательностей) с контекстом."""
    text = re.sub(r"<(style|script)\b.*?</\1>", " ", html_text, flags=re.S | re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    for pattern in NOT_NUMBERS:
        text = pattern.sub(" ", text)
    found = []
    for m in NUMBER.finditer(text):
        raw = m.group()
        value = Decimal(re.sub(SPACES, "", raw).replace("−", "-").replace("+", "").replace(",", "."))
        context = " ".join(text[max(0, m.start() - 40):m.end() + 10].split())
        found.append((value, context))
    return found


def check_report_numbers(html_path: Path, registry: pd.DataFrame) -> list[str]:
    """Ошибки привязки: число не из своей строки реестра, неверный знак или формат, число без привязки."""
    page = Path(html_path).read_text(encoding="utf-8")
    values = dict(zip(registry["id"], registry["value"]))
    problems = []
    for rid, fmt, text in BOUND.findall(page):
        if rid not in values:
            problems.append(f"{rid}: такой строки нет в реестре (в отчёте «{text}»)")
            continue
        expected = format_number(values[rid], fmt)
        if html.unescape(text) != expected:
            problems.append(f"{rid}: в отчёте «{text}», по реестру «{expected}»")
    unbound = report_numbers(BOUND.sub(" ", page))
    problems.extend(f"число {v} без привязки к реестру: «…{ctx}…»" for v, ctx in unbound)
    return problems
