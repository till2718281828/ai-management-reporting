"""Выпуск отчёта одной командой: python -m pipeline.run [--data data] [--out output]

Порядок: манифест исходников → P&L (УУ и БУ) → контроли → реестр чисел.
При ошибке контролей выпуск останавливается с кодом 1; controls.md пишется всегда.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipeline.build_pl import accounting_pl, connect, management_pl
from pipeline.controls import run_controls, write_controls
from pipeline.manifest import write_manifest
from pipeline.registry import build_registry, write_registry

OUTPUTS = ["manifest.json", "controls.md", "registry.csv", "index.html", "report.xlsx", "verification.md"]


def run(data_dir: Path, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in OUTPUTS:  # выпуск прошлого прогона не должен пережить упавший прогон
        (out_dir / stale).unlink(missing_ok=True)
    registry = None
    try:
        write_manifest(data_dir, out_dir)
        con = connect(data_dir)
        mgmt, acct = management_pl(con), accounting_pl(con)
        errors, report = run_controls(con, mgmt, acct)
        if not errors:
            registry = build_registry(mgmt)
    except Exception as exc:  # сбой расчёта — тоже ошибка выпуска, с записью в controls.md
        errors, report = [f"Сбой расчёта: {exc}"], [f"- ✘ Сбой расчёта: {exc}"]
    write_controls(out_dir, errors, report)
    if errors:
        print(f"Контроли не пройдены ({len(errors)}), см. {out_dir / 'controls.md'}", file=sys.stderr)
        return 1
    write_registry(out_dir, registry)
    print(f"Выпуск собран: {out_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Выпуск управленческого P&L")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=Path("output"))
    args = parser.parse_args(argv)
    return run(args.data, args.out)


if __name__ == "__main__":
    sys.exit(main())
