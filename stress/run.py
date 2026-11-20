"""Стресс-тест слепой проверки: python -m stress.run [--n 200] [--seed 42] [--out output-stress]
                                                 [--doc docs/stress-test.md]

1. Собирает во временной папке эталонный выпуск 1 (data/) и перевыпуск 2 (data/reissue/ против выпуска 1)
   и убеждается, что слепая проверка на обоих чистая.
2. N раз копирует выпуск, вносит в index.html одно искажение (stress/mutators.py) и запускает слепую
   проверку verifier.recompute.verify — ту же функцию, что python -m verifier.recompute. Проверка ничего
   не знает об искажении.
3. Пишет в --out: stress-results.json и stress-results.csv (каждый случай), stress-table.md (таблица по
   типам), stress-test.html (страница для сайта). С --doc обновляет таблицу в документе между метками.

Типы искажений распределены поровну (круговая раскладка по видам, затем перемешивание по seed);
выбор числа — по seed и номеру случая. Тот же seed — тот же результат.
«Найдено» — проверка отказала выпуску (есть хоть одно расхождение); «место указано» — в расхождениях
названо искажённое число (его показатель, мера и период или сам текст).
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import html
import io
import json
import random
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

from pipeline.run import run as build
from stress.mutators import COMBOS, RELEASE_OF, TYPES, VARIANTS, Mutation, Release, mutate
from verifier.recompute import describe, verify

ROOT = Path(__file__).resolve().parents[1]
RELEASE_FILES = ["manifest.json", "controls.md", "registry.csv", "index.html", "report.xlsx"]
TABLE_START, TABLE_END = "<!-- stress-table:start -->", "<!-- stress-table:end -->"

# Почему пропуск закономерен — для видов, где он ожидаем по устройству проверки (docs/stress-test.md).
MISS_REASON = {
    ("period", "link"): "число верно для строки реестра, на которую ссылается, но стоит не в своей колонке; "
                        "подходит ли ключ месту, автоматика не решает (методика, «Форматы») — это пункт "
                        "чек-листа приёмки и вопрос к агенту-проверяющему",
    ("row", "link"): "то же: число и ссылка взяты из чужой строки реестра того же периода; поймано, только "
                     "если формат недопустим для чужой строки (расход выведен модулем там, где ждут знак)",
}


class Releases:
    """Эталонные выпуски во временной папке."""

    def __init__(self, data: Path, folder: Path):
        self.data1, self.data2 = data, data / "reissue"
        self.dir1, self.dir2 = folder / "release-1", folder / "release-2"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            if build(self.data1, self.dir1) != 0:
                raise RuntimeError(f"эталонный выпуск 1 не собран: {err.getvalue()}")
            if build(self.data2, self.dir2, previous=self.dir1) != 0:
                raise RuntimeError(f"эталонный перевыпуск 2 не собран: {err.getvalue()}")
        self.release1, self.release2 = Release.load(self.dir1), Release.load(self.dir2)

    def verify(self, release: int, folder: Path) -> list[str]:
        if release == 1:
            return verify(self.data1, folder)[1]
        return verify(self.data2, folder, self.dir1, self.data1)[1]

    def copy(self, release: int, dest: Path, page: str | None = None) -> Path:
        src = self.dir1 if release == 1 else self.dir2
        dest.mkdir(parents=True, exist_ok=True)
        for name in RELEASE_FILES:
            shutil.copyfile(src / name, dest / name)
        if page is not None:
            (dest / "index.html").write_text(page, encoding="utf-8", newline="\n")
        return dest


def plan(n: int, seed: int) -> list[tuple[str, str]]:
    """Виды искажений по случаям: круговая раскладка (каждый вид встречается при n ≥ 11), перемешанная."""
    combos = [COMBOS[i % len(COMBOS)] for i in range(n)]
    random.Random(seed).shuffle(combos)
    return combos


def located(m: Mutation, issues: list[str]) -> bool:
    marks = [describe(m.key)] + ([describe(m.new_key)] if m.new_key else []) + ([m.after] if m.after else [])
    return any(mark in issue for issue in issues for mark in marks)


def check_clean(rel: Releases) -> None:
    for release, folder in ((1, rel.dir1), (2, rel.dir2)):
        issues = rel.verify(release, folder)
        if issues:
            raise RuntimeError(f"эталонный выпуск {release} не проходит слепую проверку: {issues[:3]}")


def run_cases(n: int, seed: int, data: Path = ROOT / "data", releases: Releases | None = None) -> dict:
    """N искажений и слепая проверка каждого. releases — уже собранные эталонные выпуски (для тестов)."""
    with tempfile.TemporaryDirectory(prefix="stress-") as tmp:
        tmp = Path(tmp)
        rel = releases or Releases(data, tmp)
        check_clean(rel)
        folders = {r: rel.copy(r, tmp / f"case-{r}") for r in (1, 2)}
        cases = []
        for i, (kind, variant) in enumerate(plan(n, seed), 1):
            m = mutate(kind, variant, rel.release1, rel.release2, random.Random(f"{seed}:{i}"))
            folder = folders[m.release]
            (folder / "index.html").write_text(m.page, encoding="utf-8", newline="\n")
            issues = rel.verify(m.release, folder)
            cases.append({
                "case": i, "type": kind, "variant": variant,
                "type_label": TYPES[kind], "variant_label": VARIANTS[(kind, variant)],
                "release": m.release, "block": m.block, "rid": m.rid, "new_rid": m.new_rid,
                "key": describe(m.key), "before": m.before, "after": m.after,
                "found": bool(issues), "located": located(m, issues),
                "issues": len(issues), "first_issue": issues[0] if issues else "",
            })
    return {"n": n, "seed": seed, "summary": summarize(cases), "cases": cases}


def summarize(cases: list[dict]) -> dict:
    rows = []
    for kind, variant in COMBOS:
        group = [c for c in cases if (c["type"], c["variant"]) == (kind, variant)]
        rows.append({"type": kind, "variant": variant, "type_label": TYPES[kind],
                     "variant_label": VARIANTS[(kind, variant)], "release": RELEASE_OF.get(kind, 1),
                     "injected": len(group), "found": sum(c["found"] for c in group),
                     "located": sum(c["located"] for c in group),
                     "missed": sum(not c["found"] for c in group)})
    total = {k: sum(r[k] for r in rows) for k in ("injected", "found", "located", "missed")}
    total["found_pct"] = round(100 * total["found"] / total["injected"], 1) if total["injected"] else 0.0
    return {"rows": rows, "total": total}


def pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}".replace(".", ",") + " %" if whole else "—"


def table_md(result: dict) -> str:
    s = result["summary"]
    lines = [f"Прогон: `python -m stress.run --n {result['n']} --seed {result['seed']}`.", "",
             "| Тип искажения | Вид | Выпуск | Внесено | Найдено | Место указано | Пропущено |",
             "|---|---|---|---:|---:|---:|---:|"]
    for r in s["rows"]:
        lines.append(f"| {r['type_label']} | {r['variant_label']} | {'перевыпуск' if r['release'] == 2 else '1'} | "
                     f"{r['injected']} | {r['found']} | {r['located']} | {r['missed']} |")
    t = s["total"]
    lines.append(f"| **Итого** | | | **{t['injected']}** | **{t['found']}** | **{t['located']}** | **{t['missed']}** |")
    lines += ["", f"Найдено {t['found']} из {t['injected']} — {pct(t['found'], t['injected'])}; "
                  f"пропущено {t['missed']}."]
    return "\n".join(lines) + "\n"


def write_results(result: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "stress-results.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n",
                                             encoding="utf-8", newline="\n")
    with (out / "stress-results.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(result["cases"][0]), lineterminator="\n")
        w.writeheader()
        w.writerows(result["cases"])
    (out / "stress-table.md").write_text(table_md(result), encoding="utf-8", newline="\n")
    (out / "stress-test.html").write_text(page_html(result), encoding="utf-8", newline="\n")


def update_doc(doc: Path, result: dict) -> None:
    text = doc.read_text(encoding="utf-8")
    head, rest = text.split(TABLE_START, 1)
    _, tail = rest.split(TABLE_END, 1)
    doc.write_text(f"{head}{TABLE_START}\n{table_md(result)}{TABLE_END}{tail}", encoding="utf-8", newline="\n")


def doc_table(doc: Path) -> str:
    text = doc.read_text(encoding="utf-8")
    return text.split(TABLE_START, 1)[1].split(TABLE_END, 1)[0].lstrip("\n")


def page_html(result: dict) -> str:
    s, t = result["summary"], result["summary"]["total"]
    e = html.escape
    rows = "".join(
        f"<tr><td>{e(r['type_label'])}</td><td>{e(r['variant_label'])}</td>"
        f"<td>{'перевыпуск' if r['release'] == 2 else '1'}</td><td>{r['injected']}</td><td>{r['found']}</td>"
        f"<td>{r['located']}</td><td class=\"{'miss' if r['missed'] else ''}\">{r['missed']}</td></tr>"
        for r in s["rows"])
    misses = Counter((c["type"], c["variant"]) for c in result["cases"] if not c["found"])
    why = "".join(
        f"<li><b>{e(TYPES[k])}: {e(VARIANTS[(k, v)])}</b> — пропущено {cnt}: "
        f"{e(MISS_REASON.get((k, v), 'причина не разобрана — см. stress-results.csv'))}.</li>"
        for (k, v), cnt in sorted(misses.items(), key=lambda kv: COMBOS.index(kv[0])))
    examples = "".join(
        f"<tr><td>{c['case']}</td><td>{e(c['type_label'])}: {e(c['variant_label'])}</td><td>{e(c['key'])}</td>"
        f"<td>{e(c['before'])}</td><td>{e(c['after'])}</td><td>{'да' if c['found'] else 'нет'}</td></tr>"
        for c in result["cases"][:12])
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Стресс-тест проверки</title><style>
:root {{ --ink:#1d2433; --muted:#5b6475; --line:#e3e6ec; --bg:#ffffff; --panel:#f6f7f9; --bad:#b42318; --accent:#2a5bd7; }}
@media (prefers-color-scheme: dark) {{ :root {{ --ink:#e8ebf1; --muted:#a3abba; --line:#2c3342; --bg:#141821;
  --panel:#1c212c; --bad:#f97066; --accent:#7aa2ff; }} }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }}
main {{ max-width:980px; margin:0 auto; padding:28px 16px 48px; }}
h1 {{ font-size:24px; margin:0 0 4px; }} h2 {{ font-size:17px; margin:28px 0 10px; }}
.sub, .note {{ color:var(--muted); }} a {{ color:var(--accent); }}
.big {{ font-size:22px; font-weight:600; background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px 16px; }}
.wrap {{ overflow-x:auto; }} table {{ border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }}
th, td {{ padding:6px 10px; border-bottom:1px solid var(--line); text-align:right; }}
th:nth-child(-n+3), td:nth-child(-n+3) {{ text-align:left; }} th {{ color:var(--muted); font-weight:500; font-size:13px; }}
td.miss {{ color:var(--bad); font-weight:600; }} td {{ white-space:nowrap; }} td:nth-child(-n+3) {{ white-space:normal; }}
</style></head><body><main>
<h1>Стресс-тест слепой проверки</h1>
<p class="sub">Демо на синтетических данных. В готовый отчёт вносится одно искажённое число, затем запускается
автоматическая слепая проверка (verifier/), которая пересчитывает всё из проводок и об искажении не знает.
Прогон: <code>python -m stress.run --n {result['n']} --seed {result['seed']}</code>; тот же seed — тот же результат.</p>
<p class="big">Найдено {t['found']} из {t['injected']} искажений — {pct(t['found'], t['injected'])}; пропущено {t['missed']}.</p>
<h2>По типам искажений</h2>
<div class="wrap"><table><tr><th>Тип</th><th>Вид</th><th>Выпуск</th><th>Внесено</th><th>Найдено</th>
<th>Место указано</th><th>Пропущено</th></tr>{rows}</table></div>
<p class="note">«Найдено» — проверка запретила отправку. «Место указано» — в протоколе названо искажённое число.</p>
<h2>Почему пропущено</h2>
<ul>{why or '<li>Пропусков нет.</li>'}</ul>
<h2>Первые случаи прогона</h2>
<div class="wrap"><table><tr><th>№</th><th>Искажение</th><th>Число</th><th>Было</th><th>Стало</th><th>Найдено</th></tr>
{examples}</table></div>
<p class="note">Все случаи — <a href="stress-results.csv">stress-results.csv</a>, <a href="stress-results.json">stress-results.json</a>.
Отчёт — <a href="./">выпуск 1</a>, <a href="reissue/">перевыпуск «было/стало»</a>.</p>
</main></body></html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Стресс-тест слепой проверки выпуска")
    parser.add_argument("--n", type=int, default=200, help="число искажённых выпусков")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--out", type=Path, default=Path("output-stress"))
    parser.add_argument("--doc", type=Path, default=None, help="обновить таблицу в документе (docs/stress-test.md)")
    args = parser.parse_args(argv)
    result = run_cases(args.n, args.seed, args.data)
    write_results(result, args.out)
    if args.doc:
        update_doc(args.doc, result)
    print(table_md(result))
    print(f"Результаты: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
