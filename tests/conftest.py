"""Общие фикстуры: маленькая главная книга, P&L по которой посчитан вручную."""
import csv
from pathlib import Path

import pytest

from generator.generate import ACCOUNTS, MAPPING

# Один месяц, НДС 20 %. Ожидаемый P&L — в test_pipeline.py.
FIXTURE_JOURNAL = [
    # date, debit, credit, amount, cost_item
    ("2025-01-10", "62", "90.01", "1200.00", ""),
    ("2025-01-10", "90.03", "68.02", "200.00", ""),
    ("2025-01-12", "20", "10", "400.00", "Материалы"),
    ("2025-01-31", "20", "70", "100.00", "ФОТ"),
    ("2025-01-31", "20", "69", "30.00", "Страховые взносы"),
    ("2025-01-31", "20", "02", "50.00", "Амортизация"),
    ("2025-01-31", "26", "70", "60.00", "ФОТ"),
    ("2025-01-31", "26", "02", "10.00", "Амортизация"),
    ("2025-01-15", "44", "60", "40.00", "Доставка"),
    ("2025-01-31", "90.02", "20", "580.00", ""),
    ("2025-01-31", "90.08", "26", "70.00", ""),
    ("2025-01-31", "90.07", "44", "40.00", ""),
    ("2025-01-20", "51", "91.01", "5.00", "Проценты к получению"),
    ("2025-01-31", "91.02", "66", "25.00", "Проценты к уплате"),
    ("2025-01-31", "90.09", "99", "310.00", ""),
    ("2025-01-31", "99", "91.09", "20.00", ""),
    ("2025-01-31", "99", "68.04", "72.50", "Налог на прибыль"),
]


def write_data(folder: Path, journal: list[tuple], mapping: list[tuple] = MAPPING) -> Path:
    folder.mkdir(parents=True, exist_ok=True)

    def write(name: str, header: list[str], rows: list) -> None:
        with (folder / name).open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(header)
            w.writerows(rows)

    write("accounts.csv", ["code", "name", "type"], ACCOUNTS)
    write("mapping.csv", ["account", "side", "cost_item", "article"], mapping)
    write("journal.csv", ["entry_id", "date", "doc", "debit", "credit", "amount", "cost_item", "description"],
          [(i, d, f"Д-{i}", dt, kt, a, ci, "") for i, (d, dt, kt, a, ci) in enumerate(journal, 1)])
    return folder


@pytest.fixture
def fixture_data(tmp_path):
    return write_data(tmp_path / "data", FIXTURE_JOURNAL)
