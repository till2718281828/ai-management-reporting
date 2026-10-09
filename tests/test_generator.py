"""Инварианты синтетических данных: двойная запись, детерминизм, закрытие счетов результата."""
import hashlib
from pathlib import Path

import pandas as pd
import pytest

from generator.generate import generate

CLOSING_GROUPS = ["20", "26", "44", "90", "91"]


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    out = tmp_path_factory.mktemp("data")
    generate(out)
    journal = pd.read_csv(out / "journal.csv", dtype={"debit": str, "credit": str, "cost_item": str})
    accounts = pd.read_csv(out / "accounts.csv", dtype={"code": str})
    mapping = pd.read_csv(out / "mapping.csv", dtype={"account": str, "cost_item": str})
    journal["month"] = journal["date"].str[:7]
    return out, journal, accounts, mapping


def legs(journal):
    d = journal.assign(account=journal["debit"], side="D", signed=-journal["amount"])
    c = journal.assign(account=journal["credit"], side="C", signed=journal["amount"])
    return pd.concat([d, c], ignore_index=True)


def test_files_written(data):
    out, *_ = data
    for name in ["journal.csv", "accounts.csv", "mapping.csv"]:
        assert (out / name).stat().st_size > 0


def test_double_entry(data):
    """Оборотная ведомость: Дт = Кт по каждому месяцу; суммы в рублях с копейками; счета из плана счетов."""
    _, journal, accounts, _ = data
    lg = legs(journal)
    assert (lg.groupby("month")["signed"].sum().round(2) == 0).all()
    assert (journal["amount"] > 0).all()
    raw = pd.read_csv(data[0] / "journal.csv", dtype=str)["amount"]
    assert raw.str.fullmatch(r"\d+\.\d{2}").all()
    assert (journal["debit"] != journal["credit"]).all()
    known = set(accounts["code"])
    assert set(journal["debit"]) <= known
    assert set(journal["credit"]) <= known
    assert journal["entry_id"].is_unique


def test_period(data):
    _, journal, *_ = data
    months = sorted(journal["month"].unique())
    assert months[0] == "2025-01" and months[-1] == "2026-09" and len(months) == 21


def test_committed_data_is_fresh(data):
    """data/ в репозитории совпадает с прогоном генератора: генератор не правили без перегенерации."""
    out, *_ = data
    repo_data = Path(__file__).resolve().parents[1] / "data"
    for name in ["journal.csv", "accounts.csv", "mapping.csv"]:
        assert (repo_data / name).read_bytes() == (out / name).read_bytes(), name


@pytest.mark.parametrize("sub", ["90.09", "91.09"])
def test_result_closed_to_99(data, sub):
    """Финансовый результат от продаж и прочих операций закрывается именно на 99."""
    _, journal, *_ = data
    closing = journal[(journal["debit"] == sub) | (journal["credit"] == sub)]
    counter = set(closing["credit"].where(closing["debit"] == sub, closing["debit"]))
    assert counter == {"99"}
    assert closing["month"].nunique() == 21


def test_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    generate(a)
    generate(b)
    for name in ["journal.csv", "accounts.csv", "mapping.csv"]:
        ha = hashlib.sha256((a / name).read_bytes()).hexdigest()
        hb = hashlib.sha256((b / name).read_bytes()).hexdigest()
        assert ha == hb, name


@pytest.mark.parametrize("group", CLOSING_GROUPS)
def test_result_accounts_close_monthly(data, group):
    """Затратные счета и счета 90/91 закрываются помесячно: нулевое сальдо оборотов группы."""
    _, journal, *_ = data
    lg = legs(journal)
    lg = lg[lg["account"].str.split(".").str[0] == group]
    net = lg.groupby("month")["signed"].sum().round(2)
    assert (net == 0).all(), net[net != 0]


def test_vat_rates(data):
    """НДС: 20 % в 2025, 22 % с 2026 (425-ФЗ от 28.11.2025)."""
    _, journal, *_ = data
    journal = journal.assign(year=journal["date"].str[:4])
    for year, rate in [("2025", 0.20), ("2026", 0.22)]:
        j = journal[journal["year"] == year]
        gross = j.loc[j["credit"] == "90.01", "amount"].sum()
        vat = j.loc[j["debit"] == "90.03", "amount"].sum()
        assert vat / (gross - vat) == pytest.approx(rate, abs=1e-4)


def test_every_result_leg_mapped(data):
    _, journal, accounts, mapping = data
    pl_accounts = set(accounts.loc[accounts["type"] == "pl", "code"])
    lg = legs(journal).fillna({"cost_item": ""})
    lg = lg[lg["account"].isin(pl_accounts)]
    keys = set(zip(mapping["account"], mapping["side"], mapping["cost_item"].fillna("")))
    missing = {k for k in zip(lg["account"], lg["side"], lg["cost_item"]) if k not in keys}
    assert not missing


def test_stories(data):
    """Сюжеты для отклонений: рост ремонтов в 2026, скачок доли материалов в 2026-07."""
    _, journal, *_ = data
    rep = journal[(journal["debit"] == "20") & (journal["cost_item"] == "Ремонты")]
    by_month = rep.groupby("month")["amount"].sum()
    avg_2025 = by_month[by_month.index.str.startswith("2025")].mean()
    assert by_month["2026-08"] > 1.5 * avg_2025

    rev = journal[journal["credit"] == "90.01"].groupby("month")["amount"].sum() - \
        journal[journal["debit"] == "90.03"].groupby("month")["amount"].sum()
    mat = journal[(journal["debit"] == "20") & (journal["cost_item"] == "Материалы")] \
        .groupby("month")["amount"].sum()
    share = mat / rev
    others = share.drop("2026-07")
    assert share["2026-07"] > others.max() + 0.04
