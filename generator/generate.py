"""Генератор синтетической главной книги вымышленного завода металлоконструкций.

Все данные вымышлены. Фиксированный seed: результат одинаков на любой машине.
Суммы считаются в копейках (int), чтобы двойная запись сходилась без ошибок округления.

Запуск: python -m generator.generate [папка]   — по умолчанию data/

Пишет:
  accounts.csv  — план счетов (РСБУ, упрощённый): code, name, type (balance | pl)
  mapping.csv   — правила разноски ног проводок по статьям P&L: account, side, cost_item, article
  journal.csv   — проводки: entry_id, date, doc, debit, credit, amount, cost_item, description
"""
from __future__ import annotations

import calendar
import csv
import random
import sys
from pathlib import Path

SEED = 20261009
MONTHS = [(y, m) for y in (2025, 2026) for m in range(1, 13) if (y, m) <= (2026, 9)]

# Сезонность стройки: зимой отгрузки ниже, летом выше.
SEASON = {1: .78, 2: .82, 3: .95, 4: 1.02, 5: 1.08, 6: 1.12,
          7: 1.15, 8: 1.12, 9: 1.08, 10: 1.02, 11: .95, 12: .91}
BASE_REVENUE = 458_000_000_00         # копейки, выручка без НДС в «средний» месяц 2025
PROFIT_TAX = 25                       # %, с 2025 (176-ФЗ от 12.07.2024)
INSURANCE_RATE = 0.30                 # страховые взносы к ФОТ

# Параметры по годам: рост выручки, НДС (%, с 2026 — 22 % по 425-ФЗ от 28.11.2025),
# индексация зарплат и тарифов, амортизация (новое оборудование), проценты по кредиту.
YEAR = {
    2025: {"growth": 1.00, "vat": 20, "indexation": 1.00, "depreciation": 1.00, "interest": 6_500_000_00},
    2026: {"growth": 1.08, "vat": 22, "indexation": 1.07, "depreciation": 1.06, "interest": 8_000_000_00},
}

# Сюжеты для отклонений.
MATERIAL_SHARE = 0.54
MATERIAL_SHARE_SPIKE = {(2026, 7): 0.62}                     # подорожал металлопрокат
REPAIRS_RAMP = {(2026, 3): 1.2, (2026, 4): 1.4, (2026, 5): 1.6, (2026, 6): 1.8,
                (2026, 7): 1.9, (2026, 8): 2.0, (2026, 9): 2.0}  # износ станочного парка
INVENTORY_WRITEOFF = {(2026, 5): 20_000_000_00}               # списание неликвидов

ACCOUNTS = [
    ("02", "Амортизация основных средств", "balance"),
    ("10", "Материалы", "balance"),
    ("20", "Основное производство", "pl"),
    ("26", "Общехозяйственные расходы", "pl"),
    ("44", "Расходы на продажу", "pl"),
    ("51", "Расчётные счета", "balance"),
    ("60", "Расчёты с поставщиками и подрядчиками", "balance"),
    ("62", "Расчёты с покупателями и заказчиками", "balance"),
    ("66", "Краткосрочные кредиты и займы", "balance"),
    ("68.02", "Налог на добавленную стоимость", "balance"),
    ("68.04", "Налог на прибыль", "balance"),
    ("69", "Страховые взносы", "balance"),
    ("70", "Расчёты с персоналом по оплате труда", "balance"),
    ("76", "Расчёты с разными дебиторами и кредиторами", "balance"),
    ("90.01", "Выручка", "pl"),
    ("90.02", "Себестоимость продаж", "pl"),
    ("90.03", "Налог на добавленную стоимость", "pl"),
    ("90.07", "Расходы на продажу", "pl"),
    ("90.08", "Управленческие расходы", "pl"),
    ("90.09", "Прибыль / убыток от продаж", "pl"),
    ("91.01", "Прочие доходы", "pl"),
    ("91.02", "Прочие расходы", "pl"),
    ("91.09", "Сальдо прочих доходов и расходов", "pl"),
    ("99", "Прибыли и убытки", "pl"),
]

TECH = "тех"  # техническая нога: закрытие счетов, в статьи P&L не попадает
MAPPING = [
    ("90.01", "C", "", "Выручка"),
    ("90.03", "D", "", "Выручка"),
    ("20", "D", "Материалы", "Материалы"),
    ("20", "D", "ФОТ", "Персонал производства"),
    ("20", "D", "Страховые взносы", "Персонал производства"),
    ("20", "D", "Энергия", "Энергия"),
    ("20", "D", "Ремонты", "Ремонты"),
    ("20", "D", "Прочие производственные", "Прочие производственные"),
    ("20", "D", "Амортизация", "Амортизация"),
    ("26", "D", "ФОТ", "Управленческие расходы"),
    ("26", "D", "Страховые взносы", "Управленческие расходы"),
    ("26", "D", "Аренда", "Управленческие расходы"),
    ("26", "D", "Прочие управленческие", "Управленческие расходы"),
    ("26", "D", "Амортизация", "Амортизация"),
    ("44", "D", "Доставка", "Коммерческие расходы"),
    ("44", "D", "Маркетинг", "Коммерческие расходы"),
    ("91.01", "C", "Проценты к получению", "Проценты"),
    ("91.02", "D", "Проценты к уплате", "Проценты"),
    ("91.01", "C", "Прочие доходы", "Прочие доходы и расходы"),
    ("91.02", "D", "Прочие расходы", "Прочие доходы и расходы"),
    ("91.02", "D", "Списание запасов", "Прочие доходы и расходы"),
    ("99", "D", "Налог на прибыль", "Налог на прибыль"),
    ("99", "C", "Налог на прибыль", "Налог на прибыль"),
    ("20", "C", "", TECH),
    ("26", "C", "", TECH),
    ("44", "C", "", TECH),
    ("90.02", "D", "", TECH),
    ("90.07", "D", "", TECH),
    ("90.08", "D", "", TECH),
    ("90.09", "D", "", TECH),
    ("90.09", "C", "", TECH),
    ("91.09", "D", "", TECH),
    ("91.09", "C", "", TECH),
    ("99", "D", "", TECH),
    ("99", "C", "", TECH),
]


def rub(kop: int) -> str:
    return f"{kop // 100}.{kop % 100:02d}"


def split(total: int, n: int, rng: random.Random) -> list[int]:
    """Разбить сумму на n положительных частей, сумма частей ровно равна total."""
    weights = [rng.uniform(0.5, 1.5) for _ in range(n)]
    s = sum(weights)
    parts = [int(total * w / s) for w in weights[:-1]]
    parts.append(total - sum(parts))
    return parts


def noise(rng: random.Random, spread: float = 0.05) -> float:
    return rng.uniform(1 - spread, 1 + spread)


class Ledger:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.seq: dict[str, int] = {}

    def turnover(self, month: str, debit: str | None = None, credit: str | None = None) -> int:
        """Оборот за месяц ГГГГ-ММ по дебету и/или кредиту счёта."""
        return sum(r["amount"] for r in self.rows if r["date"][:7] == month
                   and (debit is None or r["debit"] == debit) and (credit is None or r["credit"] == credit))

    def payroll(self, date: str, account: str, amount: int, who: str) -> None:
        """Начисление зарплаты и страховых взносов на затратный счёт."""
        self.post(date, "ЗП", account, "70", amount, "ФОТ", f"Начисление зарплаты: {who}")
        self.post(date, "ЗП", account, "69", round(amount * INSURANCE_RATE), "Страховые взносы",
                  f"Страховые взносы: {who}")

    def post(self, date: str, kind: str, debit: str, credit: str, amount: int,
             cost_item: str = "", description: str = "") -> None:
        if amount == 0:
            return
        if amount < 0:
            debit, credit, amount = credit, debit, -amount
        key = f"{kind}-{date[2:4]}"
        self.seq[key] = self.seq.get(key, 0) + 1
        self.rows.append({
            "entry_id": len(self.rows) + 1,
            "date": date,
            "doc": f"{key}-{self.seq[key]:04d}",
            "debit": debit,
            "credit": credit,
            "amount": amount,
            "cost_item": cost_item,
            "description": description,
        })


def month_entries(led: Ledger, y: int, m: int, rng: random.Random) -> None:
    last = calendar.monthrange(y, m)[1]

    def day() -> str:
        return f"{y}-{m:02d}-{rng.randint(1, last):02d}"

    eom = f"{y}-{m:02d}-{last:02d}"
    yp = YEAR[y]

    # Выручка и НДС по счетам-фактурам
    revenue = int(BASE_REVENUE * SEASON[m] * yp["growth"] * noise(rng, 0.03))
    for net in split(revenue, rng.randint(28, 40), rng):
        d = day()
        vat = round(net * yp["vat"] / 100)
        led.post(d, "РН", "62", "90.01", net + vat, "", "Реализация металлоконструкций")
        led.post(d, "РН", "90.03", "68.02", vat, "", "НДС с реализации")

    # Основное производство (20)
    share = MATERIAL_SHARE_SPIKE.get((y, m), MATERIAL_SHARE * noise(rng, 0.02))
    for part in split(int(revenue * share), rng.randint(12, 18), rng):
        led.post(day(), "ТН", "20", "10", part, "Материалы", "Списание металлопроката в производство")
    led.payroll(eom, "20", int(26_000_000_00 * yp["indexation"] + revenue * 0.015), "производственный персонал")
    for part in split(int(revenue * 0.030 * noise(rng)), 2, rng):
        led.post(day(), "ПУ", "20", "60", part, "Энергия", "Электроэнергия и газ")
    repairs = int(7_300_000_00 * REPAIRS_RAMP.get((y, m), 1.0) * noise(rng, 0.08))
    for part in split(repairs, rng.randint(3, 6), rng):
        led.post(day(), "ПУ", "20", "60", part, "Ремонты", "Ремонт станочного оборудования")
    led.post(day(), "ПУ", "20", "60", int(4_500_000_00 * noise(rng, 0.1)), "Прочие производственные",
             "Инструмент, расходники, услуги")
    led.post(eom, "АМ", "20", "02", int(11_500_000_00 * yp["depreciation"]), "Амортизация",
             "Амортизация производственного оборудования")

    # Общехозяйственные (26)
    led.payroll(eom, "26", int(12_000_000_00 * yp["indexation"]), "АУП")
    led.post(day(), "ПУ", "26", "60", int(2_000_000_00 * yp["indexation"]), "Аренда", "Аренда офиса")
    led.post(day(), "ПУ", "26", "60", int(1_800_000_00 * noise(rng, 0.15)), "Прочие управленческие",
             "Связь, ПО, консультации")
    led.post(eom, "АМ", "26", "02", 600_000_00, "Амортизация", "Амортизация офисного имущества")

    # Расходы на продажу (44)
    for part in split(int(revenue * 0.030 * noise(rng)), rng.randint(4, 8), rng):
        led.post(day(), "ПУ", "44", "60", part, "Доставка", "Доставка продукции заказчикам")
    led.post(day(), "ПУ", "44", "60", int(1_000_000_00 * noise(rng, 0.2)), "Маркетинг", "Выставки и реклама")

    # Прочие доходы и расходы (91)
    led.post(day(), "ВБ", "51", "91.01", int(600_000_00 * noise(rng, 0.2)), "Проценты к получению",
             "Проценты на остаток по счёту")
    led.post(eom, "ОП", "91.02", "66", yp["interest"], "Проценты к уплате",
             "Проценты по кредитной линии")
    led.post(day(), "ОП", "91.02", "76", int(1_000_000_00 * noise(rng, 0.3)), "Прочие расходы",
             "Банковские комиссии и прочее")
    if rng.random() < 0.4:
        led.post(day(), "ОП", "76", "91.01", int(rng.uniform(400_000_00, 1_500_000_00)), "Прочие доходы",
                 "Продажа отходов, штрафы к получению")
    if (y, m) in INVENTORY_WRITEOFF:
        led.post(eom, "ОП", "91.02", "10", INVENTORY_WRITEOFF[(y, m)], "Списание запасов",
                 "Списание неликвидного металлопроката по итогам инвентаризации")

    # Закрытие месяца
    def turnover(debit: str | None = None, credit: str | None = None) -> int:
        return led.turnover(eom[:7], debit, credit)

    led.post(eom, "ЗМ", "90.02", "20", turnover(debit="20"), "", "Закрытие счёта 20")
    led.post(eom, "ЗМ", "90.08", "26", turnover(debit="26"), "", "Закрытие счёта 26")
    led.post(eom, "ЗМ", "90.07", "44", turnover(debit="44"), "", "Закрытие счёта 44")
    sales_profit = (turnover(credit="90.01") - turnover(debit="90.03") - turnover(debit="90.02")
                    - turnover(debit="90.07") - turnover(debit="90.08"))
    led.post(eom, "ЗМ", "90.09", "99", sales_profit, "", "Финансовый результат от продаж")
    other = turnover(credit="91.01") - turnover(debit="91.02")
    led.post(eom, "ЗМ", "91.09", "99", other, "", "Сальдо прочих доходов и расходов")
    led.post(eom, "ЗМ", "99", "68.04", round((sales_profit + other) * PROFIT_TAX / 100), "Налог на прибыль",
             "Налог на прибыль за месяц")


def write_csv(path: Path, header: list[str], rows: list) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:  # BOM: Excel читает кириллицу
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def generate(out: Path) -> None:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    led = Ledger()
    for y, m in MONTHS:
        month_entries(led, y, m, rng)
    write_csv(out / "accounts.csv", ["code", "name", "type"], ACCOUNTS)
    write_csv(out / "mapping.csv", ["account", "side", "cost_item", "article"], MAPPING)
    cols = ["entry_id", "date", "doc", "debit", "credit", "amount", "cost_item", "description"]
    write_csv(out / "journal.csv", cols,
              [[r[c] if c != "amount" else rub(r[c]) for c in cols] for r in led.rows])


if __name__ == "__main__":
    generate(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data"))
