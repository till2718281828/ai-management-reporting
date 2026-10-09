"""Конвейер P&L: сборка на фикстуре с ручным расчётом, контроли на отрицательных случаях, манифест."""
from pathlib import Path

import pytest

from generator.generate import MAPPING
from pipeline.build_pl import accounting_pl, connect, management_pl
from pipeline.controls import run_controls
from pipeline.manifest import canonical_hash
from pipeline.run import run
from tests.conftest import FIXTURE_JOURNAL, write_data

# Ручной расчёт по FIXTURE_JOURNAL
EXPECTED_MANAGEMENT = {
    "Выручка": 1000.00,                  # 1200 с НДС − 200 НДС
    "Материалы": -400.00,
    "Персонал производства": -130.00,    # 100 ФОТ + 30 взносы
    "Энергия": 0.0,
    "Ремонты": 0.0,
    "Прочие производственные": 0.0,
    "Валовая прибыль": 470.00,
    "Коммерческие расходы": -40.00,
    "Управленческие расходы": -60.00,
    "EBITDA": 370.00,
    "Амортизация": -60.00,               # 50 производство + 10 офис
    "Прибыль от продаж": 310.00,
    "Проценты": -20.00,                  # +5 − 25
    "Прочие доходы и расходы": 0.0,
    "Прибыль до налога": 290.00,
    "Налог на прибыль": -72.50,
    "Чистая прибыль": 217.50,
}
EXPECTED_ACCOUNTING = {
    "Выручка": 1000.00,
    "Себестоимость продаж": -580.00,
    "Коммерческие расходы": -40.00,
    "Управленческие расходы": -70.00,    # 26 целиком, с амортизацией
    "Прибыль от продаж": 310.00,
    "Прочие доходы и расходы": -20.00,
    "Прибыль до налога": 290.00,
    "Налог на прибыль": -72.50,
    "Чистая прибыль": 217.50,
}


def build(folder: Path):
    con = connect(folder)
    mgmt, acct = management_pl(con), accounting_pl(con)
    return con, mgmt, acct


def test_management_pl_by_hand(fixture_data):
    _, mgmt, _ = build(fixture_data)
    assert mgmt["2025-01"].to_dict() == EXPECTED_MANAGEMENT


def test_accounting_pl_by_hand(fixture_data):
    _, _, acct = build(fixture_data)
    assert acct["2025-01"].to_dict() == EXPECTED_ACCOUNTING


def test_controls_pass_on_clean_fixture(fixture_data):
    errors, report = run_controls(*build(fixture_data))
    assert errors == []
    assert len(report) == 5


def test_unmapped_leg_is_error(tmp_path):
    journal = FIXTURE_JOURNAL + [("2025-01-31", "20", "60", "15.00", "Охрана")]
    journal = [r if r[1:3] != ("90.02", "20") else (r[0], "90.02", "20", "595.00", "") for r in journal]
    journal = [r if r[1:3] != ("90.09", "99") else (r[0], "90.09", "99", "295.00", "") for r in journal]
    errors, _ = run_controls(*build(write_data(tmp_path, journal)))
    assert any("нет правила: счёт 20, сторона D, статья затрат «Охрана»" in e for e in errors)


def test_unclosed_account_is_error(tmp_path):
    journal = [r for r in FIXTURE_JOURNAL if r[1:3] != ("90.08", "26")]
    errors, _ = run_controls(*build(write_data(tmp_path, journal)))
    assert any("счёт 26 не закрыт" in e for e in errors)


def test_double_count_breaks_invariant(tmp_path):
    """Техническая нога, ошибочно разнесённая в статью, удваивает затраты: УУ ≠ БУ и ≠ главной книге."""
    mapping = [m if m[:3] != ("90.02", "D", "") else ("90.02", "D", "", "Материалы") for m in MAPPING]
    errors, _ = run_controls(*build(write_data(tmp_path, FIXTURE_JOURNAL, mapping)))
    assert any(e.startswith("УУ = БУ") for e in errors)
    assert any(e.startswith("Сверка с главной книгой") for e in errors)


def test_run_stops_on_errors(tmp_path):
    data = write_data(tmp_path / "data", [r for r in FIXTURE_JOURNAL if r[1:3] != ("90.08", "26")])
    out = tmp_path / "out"
    assert run(data, out) == 1
    assert "Выпуск остановлен" in (out / "controls.md").read_text(encoding="utf-8")
    assert not (out / "registry.csv").exists()


def test_manifest_hash_changes_with_value(tmp_path):
    a = write_data(tmp_path / "a", FIXTURE_JOURNAL)
    changed = [r if r[1:3] != ("20", "10") else (r[0], "20", "10", "400.01", "Материалы") for r in FIXTURE_JOURNAL]
    b = write_data(tmp_path / "b", changed)
    assert canonical_hash(a / "journal.csv") != canonical_hash(b / "journal.csv")


def test_manifest_hash_ignores_column_order(tmp_path):
    a = write_data(tmp_path / "a", FIXTURE_JOURNAL)
    lines = (a / "journal.csv").read_text(encoding="utf-8").splitlines()
    swapped = [",".join(reversed(line.split(","))) for line in lines]
    (tmp_path / "b.csv").write_text("\r\n".join(swapped) + "\r\n", encoding="utf-8", newline="")
    assert canonical_hash(a / "journal.csv") == canonical_hash(tmp_path / "b.csv")


def test_unknown_account_is_error(tmp_path):
    journal = FIXTURE_JOURNAL + [("2025-01-31", "97", "60", "10.00", "")]
    errors, _ = run_controls(*build(write_data(tmp_path, journal)))
    assert any("счёт 97 не найден" in e for e in errors)


def test_calculation_failure_writes_controls(tmp_path):
    """Отчётный месяц без базы прошлого года: выпуск падает с кодом 1, controls.md записан, реестра нет."""
    data = write_data(tmp_path / "data", FIXTURE_JOURNAL)
    out = tmp_path / "out"
    out.mkdir()
    (out / "registry.csv").write_text("устаревший реестр", encoding="utf-8")
    assert run(data, out) == 1
    assert "Сбой расчёта" in (out / "controls.md").read_text(encoding="utf-8")
    assert not (out / "registry.csv").exists()


def test_change_pct_uses_absolute_base():
    from pipeline.registry import change_pct
    assert change_pct(50, -100) == 150
    assert change_pct(-150, -100) == -50
    assert change_pct(10, 0) is None


@pytest.fixture(scope="module")
def full_release(tmp_path_factory):
    out = tmp_path_factory.mktemp("out")
    code = run(Path(__file__).resolve().parents[1] / "data", out)
    return code, out


def test_full_data_controls_pass(full_release):
    code, out = full_release
    assert code == 0
    assert "Все тождества сходятся" in (out / "controls.md").read_text(encoding="utf-8")
    for name in ["manifest.json", "registry.csv"]:
        assert (out / name).exists()
