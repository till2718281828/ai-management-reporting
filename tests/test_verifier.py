"""Слепая проверка: независима от конвейера и ловит ошибку, внесённую в готовый отчёт."""
import ast
import shutil
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from pipeline.run import run
from verifier.recompute import main as verify_main
from verifier.recompute import kopecks, show, verify

ROOT = Path(__file__).resolve().parents[1]


def test_verifier_does_not_import_pipeline():
    """Проверка не видит код конвейера: ни импорта pipeline, ни общего движка DuckDB."""
    for path in (ROOT / "verifier").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert not name.startswith(("pipeline", "duckdb")), f"{path.name}: import {name}"


def test_show_matches_methodology():
    assert show(Decimal("4547720950.43"), "mln") == "4 547,7"
    assert show(Decimal("-1450000"), "mln") == "−1,5"
    assert show(Decimal("-10.46"), "pct|neg|+") == "+10,5"
    assert show(Decimal("-35906853"), "mln|abs") == "35,9"
    assert show(Decimal("0.01"), "pct|+") == "0,0"


@pytest.fixture
def data_copy(tmp_path):
    return Path(shutil.copytree(ROOT / "data", tmp_path / "data"))


def test_format_not_allowed_is_caught(data_copy, tmp_path):
    """Конвейер вывел расходы модулем там, где методика требует знак: проверка ловит, хотя число «совпадает»."""
    out = tmp_path / "out"
    assert run(data_copy, out) == 0
    page = (out / "index.html").read_text(encoding="utf-8")
    old = 'data-f="mln|+">'
    assert old in page
    (out / "index.html").write_text(page.replace(old, 'data-f="mln|abs|+">', 1), encoding="utf-8")
    _, issues, _ = verify(data_copy, out)
    assert any("методика его не допускает" in i for i in issues)


def test_unbound_number_in_report_is_caught(data_copy, tmp_path):
    """Число дописали в готовый отчёт без разметки — проверка ловит, хотя все размеченные числа верны."""
    out = tmp_path / "out"
    assert run(data_copy, out) == 0
    page = (out / "index.html").read_text(encoding="utf-8")
    (out / "index.html").write_text(page.replace("</main>", "<p>Выручка выросла на 9 999,9 млн ₽</p></main>"),
                                    encoding="utf-8")
    _, issues, _ = verify(data_copy, out)
    assert len(issues) == 1 and "9 999,9" in issues[0]


def test_missing_release_is_reported(data_copy, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    _, issues, _ = verify(data_copy, out)
    assert issues and all("нет файла" in i for i in issues)
    assert verify_main(["--data", str(data_copy), "--out", str(out)]) == 1


def test_kopecks_keeps_sign():
    assert kopecks("-0.50") == -50
    assert kopecks("1234.5") == 123450
    assert kopecks("7") == 700


def test_clean_release_passes(data_copy, tmp_path):
    out = tmp_path / "out"
    assert run(data_copy, out) == 0
    summary, issues, stats = verify(data_copy, out)
    assert issues == []
    assert stats["registry"] > 400 and stats["html"] > 100 and stats["months"] == 21
    assert all(row["ok"] for row in summary)
    assert verify_main(["--data", str(data_copy), "--out", str(out)]) == 0
    assert "Расхождений: 0" in (out / "verification.md").read_text(encoding="utf-8")


def test_injected_error_is_caught(data_copy, tmp_path):
    out = tmp_path / "out"
    assert run(data_copy, out, inject_error=True) == 0      # конвейер ошибку не видит
    assert verify_main(["--data", str(data_copy), "--out", str(out)]) == 1
    _, issues, _ = verify(data_copy, out)
    assert len(issues) == 1
    assert "Чистая прибыль (значение, 9М2026)" in issues[0]


def test_source_changed_after_release_is_caught(data_copy, tmp_path):
    """Исходник поправили после выпуска: не сходятся манифест и пересчёт."""
    out = tmp_path / "out"
    assert run(data_copy, out) == 0
    journal = pd.read_csv(data_copy / "journal.csv", dtype=str, keep_default_na=False)
    first_sale = journal.index[journal["credit"] == "90.01"][0]
    journal.loc[first_sale, "amount"] = f"{Decimal(journal.loc[first_sale, 'amount']) + 1000:.2f}"
    journal.to_csv(data_copy / "journal.csv", index=False, lineterminator="\n")
    _, issues, _ = verify(data_copy, out)
    assert any("SHA-256" in i for i in issues)
    assert any(i.startswith("реестр") for i in issues)
