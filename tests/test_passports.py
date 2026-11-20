"""Паспорта блоков: блок отчёта ↔ паспорт ↔ строки реестра.

На эталонном выпуске (data/ репозитория) и на перевыпуске (data/reissue/ против него) проверяется
полнота связей: у каждого блока отчёта есть паспорт, у каждого паспорта есть строки реестра, каждая
строка реестра входит хотя бы в один паспорт. Блок HTML — <section data-block="…">, блок книги — лист,
справочный блок — файл реестра. Паспорт с only_on: reissue — блок есть только при перевыпуске.
Колонка shown_in реестра сверяется с фактом: разметкой отчёта, графиком и книгой.
"""
import re
from pathlib import Path

import openpyxl
import pandas as pd
import pytest
import yaml

from pipeline.run import run

ROOT = Path(__file__).resolve().parents[1]
PASSPORTS = ROOT / "passports"
REQUIRED = ["id", "title", "where", "marker", "purpose", "source", "formula", "owner", "controls", "selection",
            "rows"]
WHERE = {"html", "xlsx", "registry"}
OPTIONAL = {"only_on": {"reissue"}, "filter": {True}}
# Листы книги без чисел реестра: памятка и исходные данные, на которых стоят формулы листа «P&L».
XLSX_NOT_BLOCKS = {"Как читать", "Ноги", "Проводки", "Маппинг"}
SECTION = re.compile(r'<section data-block="([^"]*)">(.*?)</section>', re.S)
BOUND_ID = re.compile(r'data-r="([^"]*)"')
CHART_IDS = re.compile(r'data-rows="([^"]*)"')
MONTH = re.compile(r"\d{4}-\d{2}")


def load_passports(folder: Path = PASSPORTS) -> dict[str, dict]:
    passports = {}
    for path in sorted(folder.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        passports[data["id"]] = data
    return passports


def period_tokens(registry: pd.DataFrame) -> dict[str, set[str]]:
    """Символьные периоды паспорта → метки периодов выпуска (как в pipeline.registry.periods)."""
    months = sorted(p for p in registry["period"].unique() if MONTH.fullmatch(p))
    month = months[-1]
    year, mm = int(month[:4]), int(month[5:])
    month_ly, ytd, ytd_ly = f"{year - 1}-{mm:02d}", f"{mm}М{year}", f"{mm}М{year - 1}"
    return {"month": {month}, "month_ly": {month_ly}, "ytd": {ytd}, "ytd_ly": {ytd_ly},
            "month_vs_ly": {f"{month} к {month_ly}"}, "ytd_vs_ly": {f"{ytd} к {ytd_ly}"}, "months": set(months)}


def select_rows(passport: dict, registry: pd.DataFrame) -> set[str]:
    """id строк реестра, которые отбирают правила паспорта."""
    tokens = period_tokens(registry)
    selected: set[str] = set()
    for rule in passport["rows"]:
        periods = set().union(*(tokens[t] for t in rule["period"]))
        mask = registry["measure"].isin(rule["measure"]) & registry["period"].isin(periods)
        if rule["indicator"] != "*":
            mask &= registry["indicator"].isin(rule["indicator"])
        selected |= set(registry.loc[mask, "id"])
    return selected


def html_blocks(page: str) -> dict[str, set[str]]:
    return {name: set(BOUND_ID.findall(body)) for name, body in SECTION.findall(page)}


def xlsx_blocks(path: Path) -> set[str]:
    return set(openpyxl.load_workbook(path, read_only=True).sheetnames) - XLSX_NOT_BLOCKS


def link_problems(passports: dict[str, dict], registry: pd.DataFrame, page: str,
                  sheets: set[str]) -> list[str]:
    """Ошибки связей: паспорт без строк, блок без паспорта, паспорт без блока, строка реестра без паспорта."""
    problems = []
    selected = {pid: select_rows(p, registry) for pid, p in passports.items()}
    markers = {(p["where"], p["marker"]) for p in passports.values()}
    blocks = html_blocks(page)
    present = {pid: {"html": p["marker"] in blocks, "xlsx": p["marker"] in sheets,
                     "registry": p["marker"] == "registry.csv"}[p["where"]] for pid, p in passports.items()}
    # Блок перевыпуска в обычном выпуске отсутствует законно: его паспорт тогда не проверяется.
    passports = {pid: p for pid, p in passports.items() if present[pid] or p.get("only_on") != "reissue"}
    selected = {pid: rows for pid, rows in selected.items() if pid in passports}
    for pid, rows in selected.items():
        if not rows:
            problems.append(f"паспорт {pid}: правила не отбирают ни одной строки реестра")
    for name in blocks:
        if ("html", name) not in markers:
            problems.append(f"блок HTML {name}: нет паспорта")
    unmarked = set(BOUND_ID.findall(SECTION.sub(" ", page)))
    if unmarked:
        problems.append(f"числа HTML вне блоков с паспортом: {', '.join(sorted(unmarked))}")
    for sheet in sheets:
        if ("xlsx", sheet) not in markers:
            problems.append(f"лист книги «{sheet}»: нет паспорта")
    for pid, p in passports.items():
        if not present[pid]:
            problems.append(f"паспорт {pid}: блока {p['where']}:{p['marker']} в выпуске нет")
    covered = set().union(*selected.values()) if selected else set()
    orphans = registry.loc[~registry["id"].isin(covered)]
    problems.extend(f"строка реестра {r.id} ({r.indicator} / {r.measure} / {r.period}): нет паспорта"
                    for r in orphans.itertuples())
    return problems


def load_release(out: Path):
    registry = pd.read_csv(out / "registry.csv", dtype={"period": str}, keep_default_na=False, encoding="utf-8-sig")
    page = (out / "index.html").read_text(encoding="utf-8")
    return registry, page, xlsx_blocks(out / "report.xlsx"), out


@pytest.fixture(scope="module")
def release_pair(tmp_path_factory):
    """Выпуск 1 из data/ и перевыпуск из data/reissue/ против него."""
    out1, out2 = tmp_path_factory.mktemp("out"), tmp_path_factory.mktemp("out-v2")
    assert run(ROOT / "data", out1) == 0
    assert run(ROOT / "data" / "reissue", out2, previous=out1) == 0
    return load_release(out1), load_release(out2)


@pytest.fixture(scope="module")
def release(release_pair):
    return release_pair[0]


@pytest.fixture(scope="module", params=["выпуск 1", "перевыпуск"])
def any_release(request, release_pair):
    return release_pair[0 if request.param == "выпуск 1" else 1]


@pytest.fixture(scope="module")
def passports():
    return load_passports()


def test_passport_files_are_complete(passports):
    files = sorted(PASSPORTS.glob("*.yaml"))
    assert len(passports) == len(files), "id паспортов повторяются"
    for path in files:
        p = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert p["id"] == path.stem, f"{path.name}: id не совпадает с именем файла"
        missing = [k for k in REQUIRED if not p.get(k)]
        assert not missing, f"{path.name}: не заполнено {missing}"
        assert p["where"] in WHERE, f"{path.name}: where={p['where']}"
        extra = set(p) - set(REQUIRED) - set(OPTIONAL)
        assert not extra, f"{path.name}: неизвестные поля {extra}"
        for key, allowed in OPTIONAL.items():
            assert key not in p or p[key] in allowed, f"{path.name}: {key}={p[key]}"
        for rule in p["rows"]:
            assert set(rule) == {"indicator", "measure", "period"}, f"{path.name}: правило {rule}"


def test_release_links_are_complete(passports, any_release):
    registry, page, sheets, _ = any_release
    assert link_problems(passports, registry, page, sheets) == []


def test_html_blocks_show_only_passport_rows(passports, any_release):
    """Блок выводит только строки своего паспорта; без отбора по значению — ровно их."""
    registry, page, _, _ = any_release
    blocks = html_blocks(page)
    expected = {"kpi_tiles", "deviations", "monthly_chart", "pl_table"}
    if (registry["measure"] == "было при отправке").any():
        expected.add("reissue_changes")
    assert set(blocks) == expected
    for p in passports.values():
        if p["where"] != "html" or p["marker"] not in blocks:
            continue
        shown, selected = blocks[p["marker"]], select_rows(p, registry)
        assert shown <= selected, f"{p['id']}: в блоке строки вне паспорта {sorted(shown - selected)}"
        if not p.get("filter") and p["id"] != "monthly_chart":  # в графике нет подписей-чисел
            assert shown == selected, f"{p['id']}: паспорт обещает строки, которых в блоке нет"


def changes_sheet_ids(out: Path) -> set[str]:
    wb = openpyxl.load_workbook(out / "report.xlsx", read_only=True)
    if "Изменения" not in wb.sheetnames:
        return set()
    ids = set()
    for row in wb["Изменения"].iter_rows(min_row=2, values_only=True):
        ids |= set(row[7].split(" · "))
    return ids


def test_shown_in_matches_fact_and_passports(passports, any_release):
    """shown_in — факт: html — число с data-r, chart — строка графика, xlsx — число в книге."""
    registry, page, _, out = any_release
    tokens = {rid: set(s.split(", ")) for rid, s in zip(registry["id"], registry["shown_in"])}
    html_ids = set(BOUND_ID.findall(page))
    chart_ids = set(" ".join(CHART_IDS.findall(page)).split())
    book_ids = select_rows(passports["xlsx_pl"], registry) | changes_sheet_ids(out)
    assert chart_ids == select_rows(passports["monthly_chart"], registry), "график строит не те строки, что в паспорте"
    for rid, where in tokens.items():
        assert ("html" in where) == (rid in html_ids), f"{rid}: shown_in={where}, в отчёте {'есть' if rid in html_ids else 'нет'}"
        assert ("chart" in where) == (rid in chart_ids), f"{rid}: shown_in={where}"
        assert ("xlsx" in where) == (rid in book_ids), f"{rid}: shown_in={where}"
        assert ("registry" in where) == (not (where - {"registry"})), f"{rid}: shown_in={where}"
    # Строка «только в реестре» — либо справочная, либо кандидат блока с отбором по значению.
    allowed = select_rows(passports["registry_reference"], registry).union(
        *(select_rows(p, registry) for p in passports.values() if p.get("filter")))
    hidden = {rid for rid, where in tokens.items() if where == {"registry"}}
    assert hidden <= allowed, f"скрытые строки вне справочного паспорта: {sorted(hidden - allowed)[:5]}"


def test_reissue_sheet_matches_registry(passports, release_pair):
    """Лист «Изменения»: было, стало, изменение — те же числа, что в строках реестра по id."""
    registry, _, sheets, out = release_pair[1]
    assert "Изменения" in sheets
    values = dict(zip(registry["id"], registry["value"].astype(float)))
    ws = openpyxl.load_workbook(out / "report.xlsx", read_only=True)["Изменения"]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    assert rows
    for row in rows:
        was_id, now_id, delta_id = row[7].split(" · ")
        assert (row[2], row[3], row[4]) == (values[was_id], values[now_id], values[delta_id])
        assert round(row[3] - row[2], 2) == round(row[4], 2)


def test_xlsx_passport_matches_sheet(passports, release):
    registry, _, _, out = release
    ws = openpyxl.load_workbook(out / "report.xlsx")["P&L"]
    months = [c.value.strftime("%Y-%m") for c in ws[3][1:] if c.value is not None]
    lines = [r[0].value for r in ws.iter_rows(min_row=4) if r[0].value]
    in_sheet = {(line, m) for line in lines for m in months}
    rows = registry[registry["id"].isin(select_rows(passports["xlsx_pl"], registry))]
    assert set(zip(rows["indicator"], rows["period"])) == in_sheet
    assert (rows["measure"] == "значение").all() and rows["shown_in"].str.contains("xlsx").all()


def test_reference_rows_are_not_shown(passports, release):
    """Справочные строки не выводятся в отчёт: показанная строка должна быть в паспорте своего блока."""
    registry, page, _, _ = release
    shown = set(BOUND_ID.findall(page))
    leaked = select_rows(passports["registry_reference"], registry) & shown
    assert not leaked, f"строки справочного паспорта выведены в отчёт: {sorted(leaked)}"


def test_checker_catches_broken_links(passports, release):
    """Проверка связей не молчит: строка без паспорта, пустой паспорт, блок без паспорта."""
    registry, page, sheets, _ = release
    orphan = pd.concat([registry, pd.DataFrame([{"id": "R9999", "indicator": "Выручка", "measure": "значение",
                                                  "period": "9М2026 к 9М2025"}])], ignore_index=True)
    assert any("R9999" in p for p in link_problems(passports, orphan, page, sheets))

    empty = dict(passports, ghost={**passports["kpi_tiles"], "id": "ghost", "marker": "kpi_tiles",
                                   "rows": [{"indicator": ["Нет такой строки"], "measure": ["значение"],
                                             "period": ["ytd"]}]})
    assert any("паспорт ghost" in p for p in link_problems(empty, registry, page, sheets))

    extra = page.replace("</main>", '<section data-block="new_block"><span data-r="R0001" data-f="mln">0,0'
                                    "</span></section></main>")
    assert any("new_block" in p for p in link_problems(passports, registry, extra, sheets))

    loose = page.replace("</main>", '<p><span data-r="R0001" data-f="mln">0,0</span></p></main>')
    assert any("вне блоков" in p for p in link_problems(passports, registry, loose, sheets))

    assert any("«Новый лист»" in p for p in link_problems(passports, registry, page, sheets | {"Новый лист"}))

    # Паспорт перевыпуска в обычном выпуске молчит, а без only_on — требует свой блок.
    assert not any("reissue_changes" in p for p in link_problems(passports, registry, page, sheets))
    strict = dict(passports, reissue_changes={k: v for k, v in passports["reissue_changes"].items() if k != "only_on"})
    assert any("паспорт reissue_changes: блока" in p for p in link_problems(strict, registry, page, sheets))
