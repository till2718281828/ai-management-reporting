"""Выпуск отчёта одной командой: python -m pipeline.run [--data data] [--out output] [--inject-error]

Порядок: манифест исходников → P&L (УУ и БУ) → контроли → реестр чисел → отчёт HTML и книга Excel →
проверка «каждое число отчёта есть в реестре».
При ошибке контролей выпуск останавливается с кодом 1; controls.md пишется всегда.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from pipeline.build_pl import accounting_pl, connect, management_pl
from pipeline.controls import run_controls, write_controls
from pipeline.manifest import write_manifest
from pipeline.numbers import check_report_numbers, mln
from pipeline.registry import build_registry, write_registry
from pipeline.render_html import render_html
from pipeline.render_xlsx import render_xlsx

OUTPUTS = ["manifest.json", "controls.md", "registry.csv", "index.html", "report.xlsx", "verification.md"]


def run(data_dir: Path, out_dir: Path, inject_error: bool = False) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in OUTPUTS:  # выпуск прошлого прогона не должен пережить упавший прогон
        (out_dir / stale).unlink(missing_ok=True)
    try:
        manifest = write_manifest(data_dir, out_dir)
        con = connect(data_dir)
        mgmt, acct = management_pl(con), accounting_pl(con)
        errors, report = run_controls(con, mgmt, acct)
        if not errors:
            registry = build_registry(mgmt)
            write_registry(out_dir, registry)
            commentary = pd.read_csv(data_dir / "commentary.csv", dtype=str, keep_default_na=False, encoding="utf-8-sig")
            html_path = render_html(out_dir, registry, commentary, manifest)
            render_xlsx(out_dir, data_dir, list(mgmt.columns))
            problems = check_report_numbers(html_path, registry)
            report.append(f"- {'✔' if not problems else '✘'} Каждое число отчёта есть в реестре чисел")
            report.extend(f"  - {p}" for p in problems)
            errors.extend(f"Числа отчёта: {p}" for p in problems)
    except Exception as exc:  # сбой расчёта — тоже ошибка выпуска, с записью в controls.md
        errors, report = [f"Сбой расчёта: {exc}"], [f"- ✘ Сбой расчёта: {exc}"]
    write_controls(out_dir, errors, report)
    if errors:
        for stale in ("registry.csv", "index.html", "report.xlsx"):  # непроверенный выпуск не оставляем
            (out_dir / stale).unlink(missing_ok=True)
        print(f"Контроли не пройдены ({len(errors)}), см. {out_dir / 'controls.md'}", file=sys.stderr)
        return 1
    if inject_error:
        print(f"ВНИМАНИЕ: в отчёт внесена ошибка для демонстрации — {inject_report_error(out_dir)}")
    print(f"Выпуск собран: {out_dir}")
    return 0


def inject_report_error(out_dir: Path) -> str:
    """Исказить одно число отчёта уже после сборки и собственных проверок — как ручная правка «на глаз».

    Чистая прибыль за нарастающий итог увеличивается на 10 млн ₽. Конвейер этого не видит;
    слепая проверка (python -m verifier.recompute) должна поймать.
    """
    registry = pd.read_csv(out_dir / "registry.csv", dtype={"period": str}, encoding="utf-8-sig")
    last = max(p for p in registry["period"] if len(p) == 7 and p[4] == "-")
    ytd = f"{int(last[5:])}М{last[:4]}"
    row = registry[(registry["indicator"] == "Чистая прибыль") & (registry["measure"] == "значение")
                   & (registry["period"] == ytd)].iloc[0]
    path = out_dir / "index.html"
    page = path.read_text(encoding="utf-8")
    old = f'data-r="{row["id"]}" data-f="mln">{mln(row["value"])}<'
    new = f'data-r="{row["id"]}" data-f="mln">{mln(row["value"] + 10_000_000)}<'
    if old not in page:
        raise RuntimeError("число для искажения не найдено в отчёте")
    path.write_text(page.replace(old, new, 1), encoding="utf-8", newline="\n")
    return f"чистая прибыль {ytd}: {mln(row['value'])} → {mln(row['value'] + 10_000_000)} млн ₽"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Выпуск управленческого P&L")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=Path("output"))
    parser.add_argument("--inject-error", action="store_true",
                        help="исказить одно число готового отчёта, чтобы показать работу слепой проверки")
    args = parser.parse_args(argv)
    return run(args.data, args.out, args.inject_error)


if __name__ == "__main__":
    sys.exit(main())
