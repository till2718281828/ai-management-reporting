"""Перевыпуск «было / стало»: сверка с отправленным реестром, причины, блок отчёта и слепая проверка.

Ветки контроля — на маленьких реестрах; интеграция — выпуск 1 из data/ и выпуск 2 из data/reissue/:
поздняя проводка за август (известная сумма) и переклассификация одной сентябрьской проводки.
"""
import re
import shutil
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from generator.generate import REISSUE_LATE_AMOUNT, generate
from pipeline.numbers import format_number
from pipeline.reissue import DELTA, WAS, compare, depends_on, months_of, reconcile
from pipeline.run import run
from verifier.recompute import verify

ROOT = Path(__file__).resolve().parents[1]
LATE = REISSUE_LATE_AMOUNT / 100  # рубли


# ---------- ветки контроля на маленьких реестрах ----------

def registry(rows: list[tuple]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["indicator", "measure", "period", "value", "unit"])
    df.insert(0, "id", [f"R{i:04d}" for i in range(1, len(df) + 1)])
    return df


def reasons(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["article", "month", "owner", "cause"])


SENT = registry([
    ("Энергия", "значение", "2026-08", -100.00, "₽"),
    ("Ремонты", "значение", "2026-08", -50.00, "₽"),
    ("Валовая прибыль", "значение", "2026-08", 900.00, "₽"),
    ("Валовая прибыль", "значение", "8М2026", 5000.00, "₽"),
    ("Валовая прибыль", "рентабельность %", "2026-08", 45.0, "%"),
])
NEW = registry([
    ("Энергия", "значение", "2026-08", -150.00, "₽"),
    ("Ремонты", "значение", "2026-08", -50.00, "₽"),
    ("Валовая прибыль", "значение", "2026-08", 850.00, "₽"),
    ("Валовая прибыль", "значение", "8М2026", 4950.00, "₽"),
    ("Валовая прибыль", "рентабельность %", "2026-08", 42.5, "%"),
])
ENERGY_REASON = ("Энергия", "2026-08", "главный энергетик", "Счёт за август пришёл после отправки.")


def test_reason_given_explains_all_changes():
    changes, problems = reconcile(SENT, NEW, reasons([ENERGY_REASON]))
    assert problems == []
    assert len(changes) == 4  # статья, итог месяца, нарастающий итог, рентабельность
    energy = changes[changes["indicator"] == "Энергия"].iloc[0]
    assert energy["primary"] and energy["cause"] == ENERGY_REASON[3] and energy["owner"] == "главный энергетик"
    assert (energy["was"], energy["now"]) == (-100.0, -150.0)
    derived = changes[~changes["primary"]]
    assert set(derived["basis"]) == {"Энергия (2026-08)"}


def test_change_without_reason_stops_release():
    _, problems = reconcile(SENT, NEW, reasons([]))
    assert len(problems) == 1
    assert "Энергия (значение, 2026-08)" in problems[0] and "изменилось без причины" in problems[0]


def test_reason_without_change_stops_release():
    extra = ("Ремонты", "2026-08", "главный механик", "Ремонт перенесён.")
    _, problems = reconcile(SENT, NEW, reasons([ENERGY_REASON, extra]))
    assert len(problems) == 1
    assert "Ремонты» (2026-08)" in problems[0] and "причина без изменения" in problems[0]


def test_derived_change_without_article_change_stops_release():
    """Итог изменился, а статьи нет (например, поменяли формулу) — тоже без причины."""
    new = NEW.copy()
    new.loc[new["indicator"] == "Энергия", "value"] = -100.00
    _, problems = reconcile(SENT, new, reasons([]))
    assert problems and all("ни одна статья" in p for p in problems)


def test_kopeck_counts_percent_by_shown_digit():
    sent = registry([("Энергия", "значение", "2026-08", -100.00, "₽"),
                     ("EBITDA", "рентабельность %", "2026-08", 20.0, "%")])
    new = registry([("Энергия", "значение", "2026-08", -100.01, "₽"),
                    ("EBITDA", "рентабельность %", "2026-08", 20.0, "%")])
    assert list(compare(sent, new)["indicator"]) == ["Энергия"]
    new.loc[1, "value"] = 20.1
    assert len(compare(sent, new)) == 2


def test_reason_rules():
    digits = ("Энергия", "2026-08", "главный энергетик", "Счёт на 5 млн пришёл поздно.")
    _, problems = reconcile(SENT, NEW, reasons([digits]))
    assert any("содержит числа" in p for p in problems)
    _, problems = reconcile(None, NEW, reasons([ENERGY_REASON]))
    assert any("отправленный выпуск не указан" in p for p in problems)
    other_month = NEW.replace({"period": {"2026-08": "2026-09"}})
    _, problems = reconcile(SENT, other_month, reasons([ENERGY_REASON]))
    assert any("отчётный месяц" in p for p in problems)


def test_dependencies():
    assert months_of("9М2026 к 9М2025")[:2] == ["2026-01", "2026-02"] and len(months_of("9М2026 к 9М2025")) == 18
    assert ("Энергия", "2026-08") in depends_on(("Чистая прибыль", "значение", "9М2026"))
    assert ("Выручка", "2026-08") in depends_on(("Энергия", "отклонение сверх роста выручки", "9М2026 к 9М2025"))
    assert ("Энергия", "2026-08") not in depends_on(("Выручка", "значение", "9М2026"))


# ---------- выпуск 2 из генератора ----------

@pytest.fixture(scope="module")
def releases(tmp_path_factory):
    out1, out2 = tmp_path_factory.mktemp("out"), tmp_path_factory.mktemp("out-v2")
    assert run(ROOT / "data", out1) == 0
    assert run(ROOT / "data" / "reissue", out2, previous=out1) == 0
    read = lambda out: pd.read_csv(out / "registry.csv", dtype={"period": str}, encoding="utf-8-sig")  # noqa: E731
    return out1, out2, read(out1), read(out2)


def value(reg: pd.DataFrame, indicator: str, measure: str, period: str) -> Decimal:
    row = reg[(reg["indicator"] == indicator) & (reg["measure"] == measure) & (reg["period"] == period)]
    assert len(row) == 1, (indicator, measure, period)
    return Decimal(str(row["value"].iloc[0]))


def journal(folder: Path) -> pd.DataFrame:
    return pd.read_csv(folder / "journal.csv", dtype=str, keep_default_na=False, encoding="utf-8-sig")


def story() -> tuple[Decimal, Decimal]:
    """Сумма переклассифицированной проводки и поправка налога — из журнала выпуска 2."""
    j1, j2 = journal(ROOT / "data"), journal(ROOT / "data" / "reissue")
    moved = j2.loc[:len(j1) - 1][j1["cost_item"] != j2.loc[:len(j1) - 1, "cost_item"]]
    assert len(moved) == 1
    tax = j2[(j2["debit"] == "68.04") & (j2["credit"] == "99")]
    assert len(tax) == 1
    return Decimal(moved["amount"].iloc[0]), Decimal(tax["amount"].iloc[0])


def test_committed_reissue_data_is_fresh(tmp_path):
    generate(tmp_path)
    for name in ["journal.csv", "accounts.csv", "mapping.csv"]:
        assert (ROOT / "data" / "reissue" / name).read_bytes() == (tmp_path / "reissue" / name).read_bytes(), name
    for name in ["journal.csv", "accounts.csv", "mapping.csv", "commentary.csv", "reissue_reasons.csv"]:
        assert (ROOT / "data" / "reissue" / name).read_bytes().startswith(b"\xef\xbb\xbf"), name


def test_known_delta_to_the_kopeck(releases):
    _, _, r1, r2 = releases
    moved, tax = story()
    late = Decimal(REISSUE_LATE_AMOUNT) / 100
    expected = {  # (показатель, период): изменение к отправке
        ("Энергия", "2026-08"): -late, ("Энергия", "9М2026"): -late,
        ("Валовая прибыль", "2026-08"): -late, ("Прибыль до налога", "9М2026"): -late,
        ("Налог на прибыль", "2026-08"): tax, ("Чистая прибыль", "2026-08"): -late + tax,
        ("Чистая прибыль", "9М2026"): -late + tax,
        ("Ремонты", "2026-09"): moved, ("Прочие производственные", "2026-09"): -moved,
    }
    for (indicator, period), delta in expected.items():
        assert value(r2, indicator, "значение", period) - value(r1, indicator, "значение", period) == delta
        assert value(r2, indicator, DELTA, period) == delta
        assert value(r2, indicator, WAS, period) == value(r1, indicator, "значение", period)
    assert abs(tax - late * Decimal("0.25")) <= Decimal("0.01")
    # Переклассификация внутри валовой прибыли: итог сентября не изменился и в блок не попал.
    assert value(r2, "Валовая прибыль", "значение", "2026-09") == value(r1, "Валовая прибыль", "значение", "2026-09")
    assert r2[(r2["measure"] == WAS) & (r2["period"] == "2026-09")]["indicator"].tolist() == \
        ["Ремонты", "Прочие производственные"]
    # Обычные строки реестра — те же id, перевыпуск только дописывает свои.
    assert (r2["id"].iloc[:len(r1)] == r1["id"]).all()
    assert len(r2) - len(r1) == 2 * 18


def test_reissue_block_in_report(releases):
    _, out2, _, _ = releases
    page = (out2 / "index.html").read_text(encoding="utf-8")
    block = re.search(r'<section data-block="reissue_changes">(.*?)</section>', page, re.S).group(1)
    assert page.index("reissue_changes") < page.index('data-block="kpi_tiles"')  # первым на странице
    causes = pd.read_csv(ROOT / "data" / "reissue" / "reissue_reasons.csv", encoding="utf-8-sig")["cause"]
    assert all(c in block for c in causes)
    assert "Из-за изменения: Энергия (август 2026)" in block
    assert format_number(-LATE, "mln|+") in block
    controls = (out2 / "controls.md").read_text(encoding="utf-8")
    assert "✔ Перевыпуск" in controls


def test_first_release_has_no_reissue_block(releases):
    out1, _, r1, _ = releases
    assert "reissue_changes" not in (out1 / "index.html").read_text(encoding="utf-8")
    assert not r1["measure"].isin([WAS, DELTA]).any()


@pytest.fixture
def reissue_copy(tmp_path):
    return Path(shutil.copytree(ROOT / "data" / "reissue", tmp_path / "data"))


def test_missing_reason_stops_reissue(releases, reissue_copy, tmp_path):
    out1, *_ = releases
    reasons_path = reissue_copy / "reissue_reasons.csv"
    rows = reasons_path.read_text(encoding="utf-8-sig").splitlines()
    reasons_path.write_text("\n".join(r for r in rows if not r.startswith("Энергия")) + "\n", encoding="utf-8-sig")
    out = tmp_path / "out"
    assert run(reissue_copy, out, previous=out1) == 1
    controls = (out / "controls.md").read_text(encoding="utf-8")
    assert "Энергия (значение, 2026-08)" in controls and "изменилось без причины" in controls
    assert not (out / "registry.csv").exists() and not (out / "index.html").exists()


def test_extra_reason_stops_reissue(releases, reissue_copy, tmp_path):
    out1, *_ = releases
    with (reissue_copy / "reissue_reasons.csv").open("a", encoding="utf-8") as f:
        f.write("Маркетинг,2026-08,коммерческий директор,Перенос выставки.\n")
    out = tmp_path / "out"
    assert run(reissue_copy, out, previous=out1) == 1
    assert "причина без изменения" in (out / "controls.md").read_text(encoding="utf-8")


def test_reasons_without_previous_stop_release(reissue_copy, tmp_path):
    assert run(reissue_copy, tmp_path / "out") == 1
    assert "отправленный выпуск не указан" in (tmp_path / "out" / "controls.md").read_text(encoding="utf-8")


def test_reissue_does_not_overwrite_sent_release(releases):
    out1, *_ = releases
    before = (out1 / "registry.csv").read_bytes()
    assert run(ROOT / "data" / "reissue", out1, previous=out1) == 1
    assert (out1 / "registry.csv").read_bytes() == before


# ---------- слепая проверка перевыпуска ----------

def test_verifier_passes_reissue(releases):
    out1, out2, _, _ = releases
    summary, issues, stats = verify(ROOT / "data" / "reissue", out2, out1, ROOT / "data")
    assert issues == []
    assert len(stats["changes"]) == 18
    energy = next(c for c in stats["changes"] if c["key"] == ("Энергия", "значение", "2026-08"))
    assert energy["now"] - energy["was"] == -Decimal(REISSUE_LATE_AMOUNT) / 100


def test_verifier_needs_previous(releases):
    _, out2, _, _ = releases
    _, issues, _ = verify(ROOT / "data" / "reissue", out2)
    assert any("укажите отправленный выпуск" in i for i in issues)


def tampered(out: Path, tmp_path: Path, indicator: str, measure: str, period: str, shift: float) -> Path:
    """Копия выпуска, где одно число согласованно изменено и в реестре, и в отчёте — как подлог конвейера."""
    copy = Path(shutil.copytree(out, tmp_path / "tampered"))
    reg = pd.read_csv(copy / "registry.csv", dtype=str, keep_default_na=False, encoding="utf-8-sig")
    mask = (reg["indicator"] == indicator) & (reg["measure"] == measure) & (reg["period"] == period)
    rid, old = reg.loc[mask, "id"].iloc[0], float(reg.loc[mask, "value"].iloc[0])
    reg.loc[mask, "value"] = f"{old + shift:.2f}"
    reg.to_csv(copy / "registry.csv", index=False, encoding="utf-8-sig", lineterminator="\n")
    page = (copy / "index.html").read_text(encoding="utf-8")
    for fmt in set(re.findall(rf'data-r="{rid}" data-f="([^"]*)"', page)):
        page = page.replace(f'data-r="{rid}" data-f="{fmt}">{format_number(old, fmt)}<',
                            f'data-r="{rid}" data-f="{fmt}">{format_number(old + shift, fmt)}<')
    (copy / "index.html").write_text(page, encoding="utf-8")
    return copy


@pytest.mark.parametrize("measure", [WAS, "значение"])
def test_verifier_catches_tampered_was_or_now(releases, tmp_path, measure):
    """Подменили «было» (приукрасили отправленное) или «стало» — и в реестре, и в отчёте."""
    out1, out2, _, _ = releases
    copy = tampered(out2, tmp_path, "Энергия", measure, "2026-08", 1_000_000)
    _, issues, _ = verify(ROOT / "data" / "reissue", copy, out1, ROOT / "data")
    assert any(f"Энергия ({measure}, 2026-08)" in i for i in issues), issues[:5]


def test_verifier_catches_hidden_change(releases, tmp_path):
    """Строку изменения убрали из блока — правка прошла бы тихо."""
    out1, out2, _, r2 = releases
    copy = Path(shutil.copytree(out2, tmp_path / "hidden"))
    page = (copy / "index.html").read_text(encoding="utf-8")
    was_id = r2[(r2["indicator"] == "Энергия") & (r2["measure"] == WAS) & (r2["period"] == "2026-08")]["id"].iloc[0]
    row = re.search(rf'<tr>(?:(?!</tr>).)*data-r="{was_id}"(?:(?!</tr>).)*</tr>', page, re.S).group()
    (copy / "index.html").write_text(page.replace(row, ""), encoding="utf-8")
    _, issues, _ = verify(ROOT / "data" / "reissue", copy, out1)
    assert any("Энергия (значение, 2026-08) не показано" in i for i in issues)
    assert any("причины по Энергия" in i for i in issues)


def test_verifier_catches_wrong_sent_release(releases, tmp_path):
    """Проверяющему подсунули не тот «отправленный» реестр — SHA-256 не сходится с манифестом."""
    out1, out2, _, _ = releases
    fake = tmp_path / "registry.csv"
    reg = pd.read_csv(out1 / "registry.csv", dtype=str, keep_default_na=False, encoding="utf-8-sig")
    reg.loc[0, "value"] = f"{float(reg.loc[0, 'value']) + 1:.2f}"
    reg.to_csv(fake, index=False, encoding="utf-8-sig", lineterminator="\n")
    _, issues, _ = verify(ROOT / "data" / "reissue", out2, fake, ROOT / "data")
    assert any("SHA-256 не совпадает" in i for i in issues)
    assert any("не сходится с пересчётом из исходников отправленного выпуска" in i for i in issues)
