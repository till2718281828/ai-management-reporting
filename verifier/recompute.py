"""Слепая проверка выпуска: python -m verifier.recompute [--data data] [--out output]

Проверка не импортирует и не читает код конвейера (pipeline/) — это проверяет тест. Она знает только
методику (docs/methodology.md) и сама считает всё заново из исходников другим способом: pandas и
целые копейки вместо DuckDB. Затем сверяет:

1. SHA-256 исходников — с манифестом выпуска;
2. каждую строку реестра — со своим пересчётом (по ключу показатель / мера / период);
3. каждое число отчёта HTML — со своим пересчётом, отформатированным заново;
4. чистую прибыль каждого месяца — с оборотами счёта 99.

Протокол — output/verification.md. Код возврата 1, если есть хоть одно расхождение.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pandas as pd

LINES = [  # (строка, итог?) — по docs/methodology.md
    ("Выручка", False), ("Материалы", False), ("Персонал производства", False), ("Энергия", False),
    ("Ремонты", False), ("Прочие производственные", False), ("Валовая прибыль", True),
    ("Коммерческие расходы", False), ("Управленческие расходы", False), ("EBITDA", True),
    ("Амортизация", False), ("Прибыль от продаж", True), ("Проценты", False),
    ("Прочие доходы и расходы", False), ("Прибыль до налога", True), ("Налог на прибыль", False),
    ("Чистая прибыль", True),
]
MARGIN_LINES = ["Валовая прибыль", "EBITDA", "Прибыль от продаж", "Чистая прибыль"]
DEVIATION_ARTICLES = ["Материалы", "Персонал производства", "Энергия", "Ремонты", "Прочие производственные",
                      "Коммерческие расходы", "Управленческие расходы", "Амортизация", "Проценты",
                      "Прочие доходы и расходы"]
SOURCES = ["journal.csv", "accounts.csv", "mapping.csv", "commentary.csv"]
TOL_RUB = Decimal("0.01")
TOL_PCT = Decimal("0.05")   # реестр хранит проценты с одним знаком
SPAN = re.compile(r'<span data-r="([^"]*)" data-f="([^"]*)">([^<]*)</span>')
# Число в видимом тексте отчёта и то, что числом не считается (docs/methodology.md, «Отображение»).
NUMBER = re.compile(r"[+−-]?\d[\d \u00a0\u202f]*(?:,\d+)?")
NOT_A_NUMBER = re.compile(r"\d{4}-\d{2}(?:-\d{2})?|\b\d{1,2}М\d{4}\b|\b(?:19|20)\d{2}\b(?!,\d)"
                          r"|\b[0-9a-f]{12,}\b|(?i:sha-?256)")
RELEASE_FILES = ["manifest.json", "registry.csv", "index.html"]

Key = tuple[str, str, str]


# ---------- пересчёт ----------

def canonical_sha(path: Path) -> str:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    text = df[sorted(df.columns)].to_csv(index=False, lineterminator="\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def kopecks(amount: str) -> int:
    sign = -1 if amount.strip().startswith("-") else 1
    rub, _, kop = amount.strip().lstrip("+-").partition(".")
    return sign * (int(rub or 0) * 100 + int((kop + "00")[:2]))


def monthly_pl(data: Path) -> tuple[dict[str, dict[str, int]], dict[str, int], list[str]]:
    """{месяц: {строка: копейки}}, обороты 99 по месяцам, проблемы разноски."""
    journal = pd.read_csv(data / "journal.csv", dtype=str, keep_default_na=False)
    accounts = pd.read_csv(data / "accounts.csv", dtype=str, keep_default_na=False)
    mapping = pd.read_csv(data / "mapping.csv", dtype=str, keep_default_na=False)
    pl_accounts = set(accounts.loc[accounts["type"] == "pl", "code"])
    rules = {(a, s, c): art for a, s, c, art in
             zip(mapping["account"], mapping["side"], mapping["cost_item"], mapping["article"])}

    articles: dict[str, dict[str, int]] = {}
    acc99: dict[str, int] = {}
    problems = []
    for date, debit, credit, amount, item in zip(journal["date"], journal["debit"], journal["credit"],
                                                 journal["amount"], journal["cost_item"]):
        month, kop = date[:7], kopecks(amount)
        for account, side, signed in ((debit, "D", -kop), (credit, "C", kop)):
            if account == "99":
                acc99[month] = acc99.get(month, 0) + signed
            if account not in pl_accounts:
                continue
            article = rules.get((account, side, item))
            if article is None:
                problems.append(f"нога без правила разноски: {date} {account} {side} «{item}»")
            elif article != "тех":
                row = articles.setdefault(month, {})
                row[article] = row.get(article, 0) + signed

    pl = {}
    for month in sorted(articles):
        running, row = 0, {}
        for line, subtotal in LINES:
            if not subtotal:
                running += articles[month].get(line, 0)
                row[line] = articles[month].get(line, 0)
            else:
                row[line] = running
        pl[month] = row
    return pl, acc99, problems


def period_labels(month: str) -> tuple[str, str, str]:
    """Для отчётного месяца ГГГГ-ММ: тот же месяц прошлого года, нарастающий итог и его база."""
    year, mm = int(month[:4]), int(month[5:])
    return f"{year - 1}-{mm:02d}", f"{mm}М{year}", f"{mm}М{year - 1}"


def rub(kop: int) -> Decimal:
    return Decimal(kop) / 100


def expected_values(pl: dict[str, dict[str, int]]) -> dict[Key, Decimal]:
    """Все меры реестра по методике. ₽ — точные рубли, % — без округления."""
    month = max(pl)
    year, mm = int(month[:4]), int(month[5:])
    month_ly, ytd, ytd_ly = period_labels(month)
    totals = {
        month: pl[month], month_ly: pl[month_ly],
        ytd: {line: sum(pl[f"{year}-{m:02d}"][line] for m in range(1, mm + 1)) for line, _ in LINES},
        ytd_ly: {line: sum(pl[f"{year - 1}-{m:02d}"][line] for m in range(1, mm + 1)) for line, _ in LINES},
    }
    exp: dict[Key, Decimal] = {}
    for m, row in pl.items():
        for line, _ in LINES:
            exp[(line, "значение", m)] = rub(row[line])
    for label in (ytd, ytd_ly):
        for line, _ in LINES:
            exp[(line, "значение", label)] = rub(totals[label][line])
    for cur, base in ((month, month_ly), (ytd, ytd_ly)):
        period = f"{cur} к {base}"
        c, b = totals[cur], totals[base]
        for line, _ in LINES:
            exp[(line, "изменение", period)] = rub(c[line] - b[line])
            if b[line] != 0:
                exp[(line, "изменение %", period)] = Decimal(c[line] - b[line]) / abs(Decimal(b[line])) * 100
        if b["Выручка"] != 0:
            scale = Decimal(c["Выручка"]) / Decimal(b["Выручка"])
            for article in DEVIATION_ARTICLES:
                exp[(article, "отклонение сверх роста выручки", period)] = (Decimal(c[article]) - Decimal(b[article]) * scale) / 100
    for label in (month, month_ly, ytd, ytd_ly):
        if totals[label]["Выручка"] != 0:
            for line in MARGIN_LINES:
                exp[(line, "рентабельность %", label)] = (Decimal(totals[label][line])
                                                         / Decimal(totals[label]["Выручка"]) * 100)
    return exp


# ---------- отображение (своя реализация формата из методики) ----------

def format_allowed(key: Key, fmt: str) -> bool:
    """Какие форматы вправе иметь числа отчёта — по таблице «Форматы» в docs/methodology.md."""
    indicator, measure, _ = key
    cost = indicator in DEVIATION_ARTICLES
    allowed = {
        "значение": {"mln"} | ({"mln|abs"} if cost else set()),
        "изменение": {"mln|+"},
        "изменение %": {"pct|+"} | ({"pct|neg|+"} if cost else set()) | ({"pct|abs"} if indicator == "Выручка" else set()),
        "рентабельность %": {"pct"},
        "отклонение сверх роста выручки": {"mln|abs"},
    }
    return fmt in allowed.get(measure, set())


def show(value: Decimal, fmt: str) -> str:
    base, *flags = fmt.split("|")
    if "neg" in flags:
        value = -value
    if "abs" in flags:
        value = abs(value)
    if base == "mln":
        value = value / 1_000_000
    q = value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    whole, frac = f"{abs(q):.1f}".split(".")
    text = f"{int(whole):,}".replace(",", " ") + "," + frac
    if q < 0:
        text = "−" + text
    elif "+" in flags and q != 0:
        text = "+" + text
    return text


# ---------- сверка ----------

def verify(data: Path, out: Path) -> tuple[list[dict], list[str], dict]:
    """Вернуть (строки протокола по ключевым показателям, расхождения, статистика)."""
    issues: list[str] = []
    stats = {"sources": 0, "registry": 0, "html": 0, "months": 0}

    missing = [name for name in RELEASE_FILES if not (out / name).exists()]
    if missing:
        return [], [f"в выпуске нет файла {name} — проверять нечего, выпуск не собран" for name in missing], stats

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    for name in SOURCES:
        stats["sources"] += 1
        own = canonical_sha(data / name)
        if manifest["sources"].get(name, {}).get("sha256") != own:
            issues.append(f"{name}: SHA-256 исходника не совпадает с манифестом выпуска")

    pl, acc99, problems = monthly_pl(data)
    issues.extend(problems)
    for month, row in pl.items():
        stats["months"] += 1
        if row["Чистая прибыль"] != acc99.get(month, 0):
            issues.append(f"{month}: чистая прибыль {row['Чистая прибыль'] / 100:,.2f} ≠ обороты 99 "
                          f"{acc99.get(month, 0) / 100:,.2f}")

    exp = expected_values(pl)
    registry = pd.read_csv(out / "registry.csv", dtype=str, keep_default_na=False)
    seen = set()
    by_id: dict[str, Key] = {}
    for rid, ind, measure, period, value, unit in zip(registry["id"], registry["indicator"], registry["measure"],
                                                      registry["period"], registry["value"], registry["unit"]):
        key = (ind, measure, period)
        by_id[rid] = key
        seen.add(key)
        stats["registry"] += 1
        if key not in exp:
            issues.append(f"реестр {rid}: {describe(key)} — по методике такого числа нет")
            continue
        tol = TOL_RUB if unit == "₽" else TOL_PCT
        if abs(Decimal(value) - exp[key]) > tol:
            issues.append(f"реестр {rid}: {describe(key)} — в реестре {value}, по пересчёту {exp[key]:.2f}")
    for key in sorted(set(exp) - seen):
        issues.append(f"в реестре нет числа: {describe(key)}")

    page = (out / "index.html").read_text(encoding="utf-8")
    shown: dict[Key, list[tuple[str, bool]]] = {}
    for rid, fmt, raw in SPAN.findall(page):
        stats["html"] += 1
        text = html.unescape(raw)
        key = by_id.get(rid)
        if key is None or key not in exp:
            issues.append(f"отчёт: число «{text}» ссылается на {rid}, которого нет в реестре")
            continue
        if not format_allowed(key, fmt):
            issues.append(f"отчёт: {describe(key)} выведено в формате «{fmt}», методика его не допускает")
        recomputed = show(exp[key], fmt)
        shown.setdefault(key, []).append((text, text == recomputed))
        if text != recomputed:
            issues.append(f"отчёт: {describe(key)} — в отчёте {text}, по пересчёту {recomputed}")

    visible = re.sub(r"<(style|script)\b.*?</\1>", " ", SPAN.sub(" ", page), flags=re.S | re.I)
    visible = NOT_A_NUMBER.sub(" ", html.unescape(re.sub(r"<[^>]+>", " ", visible)))
    for m in NUMBER.finditer(visible):
        context = " ".join(visible[max(0, m.start() - 40):m.end() + 10].split())
        issues.append(f"отчёт: число {m.group().strip()} без привязки к реестру — «…{context}…»")

    _, ytd, ytd_ly = period_labels(max(pl))
    reg_value = {by_id[r]: Decimal(v) for r, v in zip(registry["id"], registry["value"])}
    summary = []
    for line in ["Выручка", "Валовая прибыль", "EBITDA", "Прибыль от продаж", "Чистая прибыль"]:
        for period in (ytd, ytd_ly, max(pl)):
            key = (line, "значение", period)
            texts = list(dict.fromkeys(t for t, _ in shown.get(key, [])))
            ok = (key in reg_value and abs(reg_value[key] - exp[key]) <= TOL_RUB
                  and all(good for _, good in shown.get(key, [])))
            summary.append({"key": key, "recomputed": exp[key], "registry": reg_value.get(key),
                            "shown": " / ".join(texts) or "—", "ok": ok})
    return summary, issues, stats


def describe(key: Key) -> str:
    indicator, measure, period = key
    return f"{indicator} ({measure}, {period})"


def write_protocol(out: Path, summary: list[dict], issues: list[str], stats: dict) -> None:
    verdict = ("**Вердикт: выпуск можно отправлять** — расхождений нет." if not issues else
               f"**Вердикт: выпуск отправлять нельзя** — расхождений: {len(issues)}, список ниже.")

    def mln_text(value: Decimal | None) -> str:
        return "—" if value is None else show(value, "mln")

    lines = ["# Протокол слепой проверки", "", verdict, "",
             "Проверка пересчитала всё из исходников по docs/methodology.md, не открывая код конвейера:", "",
             f"- контрольные суммы исходников против манифеста выпуска — файлов: {stats['sources']};",
             f"- чистая прибыль каждого месяца против оборотов счёта 99 — месяцев: {stats['months']};",
             f"- каждая строка реестра чисел против пересчёта — строк: {stats['registry']};",
             f"- каждое число отчёта против пересчёта и допустимого формата — чисел: {stats['html']}.", "",
             "Ключевые показатели, млн ₽:", "",
             "| Показатель | Период | Пересчёт | Реестр | В отчёте | Вердикт |",
             "|---|---|---:|---:|---:|---|"]
    for row in summary:
        indicator, _, period = row["key"]
        lines.append(f"| {indicator} | {period} | {mln_text(row['recomputed'])} | {mln_text(row['registry'])} | "
                     f"{row['shown']} | {'ОК' if row['ok'] else 'РАСХОЖДЕНИЕ'} |")
    lines += ["", f"**Расхождений: {len(issues)}**"]
    if issues:
        lines += [""] + [f"- {i}" for i in issues]
    (out / "verification.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Слепая проверка выпуска P&L")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=Path("output"))
    args = parser.parse_args(argv)
    summary, issues, stats = verify(args.data, args.out)
    write_protocol(args.out, summary, issues, stats)
    print(f"Слепая проверка: расхождений {len(issues)}; протокол {args.out / 'verification.md'}")
    for issue in issues[:10]:
        print(f"  - {issue}")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
