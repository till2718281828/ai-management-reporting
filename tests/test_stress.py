"""Стресс-тест: каждое искажение меняет ровно одно число отчёта, прогон воспроизводим по seed,
каждый тип встречается, таблица в docs/stress-test.md — из реального прогона."""
import json
import random
from collections import Counter
from pathlib import Path

import pytest

from stress.mutators import COMBOS, TYPES, mutate, report_numbers
from stress.run import ROOT, Releases, doc_table, run_cases, table_md
from stress.sample import build_sample
from stress.sample import main as sample_main


@pytest.fixture(scope="module")
def releases(tmp_path_factory):
    return Releases(ROOT / "data", tmp_path_factory.mktemp("releases"))


@pytest.mark.parametrize("combo", COMBOS, ids=["-".join(c) for c in COMBOS])
def test_mutator_changes_exactly_one_number(releases, combo):
    kind, variant = combo
    for seed in range(8):
        m = mutate(kind, variant, releases.release1, releases.release2, random.Random(seed))
        original = (releases.release1 if m.release == 1 else releases.release2).page
        before, after = Counter(report_numbers(original)), Counter(report_numbers(m.page))
        removed, added = before - after, after - before
        assert sum(added.values()) == 1, (combo, seed, added)
        assert sum(removed.values()) <= 1, (combo, seed, removed)
        if removed:   # то же место отчёта: снятая привязка оставляет тот же текст
            (_, _, _, old), = removed
            assert m.before == old
        if combo != ("unbound", "removed"):   # снятая привязка текст не меняет
            assert m.after != m.before


def test_run_is_reproducible_and_covers_every_type(releases):
    first = run_cases(21, 42, releases=releases)
    second = run_cases(21, 42, releases=releases)
    assert first == second
    assert table_md(first) == table_md(second)
    seen = {(c["type"], c["variant"]) for c in first["cases"]}
    assert seen == set(COMBOS)
    assert {c["type"] for c in first["cases"]} == set(TYPES)
    assert first["summary"]["total"]["injected"] == 21
    other = run_cases(21, 43, releases=releases)
    assert [c["rid"] for c in other["cases"]] != [c["rid"] for c in first["cases"]]


def test_doc_table_is_from_real_run(releases):
    """Таблица в docs/stress-test.md совпадает с прогоном python -m stress.run --n 200 --seed 42."""
    result = run_cases(200, 42, releases=releases)
    assert doc_table(ROOT / "docs" / "stress-test.md") == table_md(result)


def test_sample_writes_releases_and_key_outside(tmp_path):
    dest, key = tmp_path / "sample", tmp_path / "secret" / "key.json"
    entries = build_sample(3, 1, dest, key)
    assert [e["folder"] for e in entries] == ["sample-1", "sample-2", "sample-3"]
    assert len({e["type"] for e in entries}) == 3 and "reissue" in {e["type"] for e in entries}
    for e in entries:
        folder = dest / e["folder"]
        assert {p.name for p in folder.iterdir()} == {"manifest.json", "controls.md", "registry.csv",
                                                      "index.html", "report.xlsx"}
    assert json.loads(key.read_text(encoding="utf-8"))["samples"] == entries
    names = " ".join(p.read_text(encoding="utf-8", errors="ignore") for p in dest.rglob("*.md"))
    assert "key.json" not in names and not list(dest.rglob("*key*"))


def test_sample_refuses_key_in_repo_or_dest(tmp_path):
    with pytest.raises(ValueError, match="репозитори"):
        build_sample(2, 1, tmp_path / "a", ROOT / "key.json")
    with pytest.raises(ValueError, match="выборки"):
        build_sample(2, 1, tmp_path / "b", tmp_path / "b" / "key.json")
    with pytest.raises(ValueError, match="папка выборки внутри репозитория"):
        build_sample(2, 1, ROOT / "sample-in-repo", tmp_path / "key.json")
    assert not (ROOT / "sample-in-repo").exists()


def test_sample_cli_requires_seed_and_refuses_dest_in_repo(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        sample_main(["--dest", str(tmp_path / "s"), "--key", str(tmp_path / "k.json")])
    assert exc.value.code == 2
    code = sample_main(["--seed", "1", "--dest", str(ROOT / "sample-in-repo"), "--key", str(tmp_path / "k.json")])
    assert code == 2 and "репозитори" in capsys.readouterr().err
    assert not (ROOT / "sample-in-repo").exists()
