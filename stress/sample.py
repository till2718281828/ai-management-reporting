"""Выборка искажённых выпусков для прогона агента-проверяющего:
    python -m stress.sample --k 5 --seed <число> --dest <папка> --key <файл-ключа>

В --dest кладётся k папок sample-1 … sample-k, каждая — полный выпуск, как output/ (manifest.json,
controls.md, registry.csv, index.html, report.xlsx), с одним искажением в index.html. Типы искажений
разные; перевыпуск попадает в выборку всегда (при k ≥ 2). Рядом — _sent-release-1/: чистый отправленный
выпуск 1, с которым сверяется перевыпуск, и TASK.md — что проверять, без ответа.

Ключ (что и где искажено) пишется только в файл --key. Он должен лежать вне репозитория и вне --dest,
а сама выборка — вне репозитория: агент, читающий репозиторий и выборку, ключа не увидит. Seed обязателен
и нигде не публикуется: по seed и коду выборку, а значит и ключ, можно собрать заново.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import tempfile
from pathlib import Path

from stress.mutators import TYPES, VARIANTS, mutate
from stress.run import ROOT, Releases, check_clean
from verifier.recompute import describe

TASK = """# Выборка выпусков для слепой проверки

В папках sample-* — выпуски управленческого P&L, каждый как output/. Проверить каждый по
.claude/agents/blind-verifier.md и вынести вердикт: можно отправлять или нет, какое число расходится.

- Исходники выпуска 1 — data/; перевыпуска — data/reissue/.
- Перевыпуск узнаётся по manifest.json: есть previous_registry. Отправленный выпуск, с которым он
  сверяется, — _sent-release-1/ (его реестр — то, что ушло руководителю).
- Сколько чисел искажено в каждой папке и какие — не сообщается.
"""


def inside(path: Path, folder: Path) -> bool:
    return path.resolve().is_relative_to(folder.resolve())


def build_sample(k: int, seed: int, dest: Path, key: Path, data: Path = ROOT / "data") -> list[dict]:
    if inside(key, ROOT):
        raise ValueError("файл ключа внутри репозитория — агент его увидит; укажите путь вне репозитория")
    if inside(key, dest):
        raise ValueError("файл ключа внутри папки выборки — агент его увидит")
    if inside(dest, ROOT):
        raise ValueError("папка выборки внутри репозитория — укажите путь вне репозитория")
    if not 1 <= k <= len(TYPES):
        raise ValueError(f"k — от 1 до {len(TYPES)}: по одному искажению каждого типа")
    if dest.exists() and any(dest.iterdir()):
        raise ValueError(f"папка выборки не пуста: {dest}")
    rng = random.Random(seed)
    kinds = [t for t in TYPES if t != "reissue"]
    rng.shuffle(kinds)
    chosen = (["reissue"] if k >= 2 else []) + kinds
    chosen = chosen[:k]
    rng.shuffle(chosen)
    entries = []
    with tempfile.TemporaryDirectory(prefix="stress-sample-") as tmp:
        rel = Releases(data, Path(tmp))
        check_clean(rel)
        dest.mkdir(parents=True, exist_ok=True)
        rel.copy(1, dest / "_sent-release-1")
        for i, kind in enumerate(chosen, 1):
            variant = rng.choice([v for t, v in VARIANTS if t == kind])
            m = mutate(kind, variant, rel.release1, rel.release2, random.Random(f"{seed}:sample:{i}"))
            folder = rel.copy(m.release, dest / f"sample-{i}", m.page)
            entries.append({
                "folder": folder.name, "type": kind, "variant": variant,
                "label": f"{TYPES[kind]}: {VARIANTS[(kind, variant)]}",
                "release": "перевыпуск" if m.release == 2 else "выпуск 1",
                "data": "data/reissue" if m.release == 2 else "data",
                "block": m.block, "rid": m.rid, "new_rid": m.new_rid, "number": describe(m.key),
                "new_number": describe(m.new_key) if m.new_key else "",
                "before": m.before, "after": m.after,
            })
    (dest / "TASK.md").write_text(TASK, encoding="utf-8", newline="\n")
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text(json.dumps({"seed": seed, "k": k, "samples": entries}, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8", newline="\n")
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Искажённые выпуски для прогона агента-проверяющего")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--seed", type=int, required=True,
                        help="обязателен и не публикуется: по нему выборку и ключ можно собрать заново")
    parser.add_argument("--dest", type=Path, required=True, help="куда положить выпуски (пустая папка)")
    parser.add_argument("--key", type=Path, required=True, help="файл ключа — вне репозитория и вне --dest")
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    args = parser.parse_args(argv)
    try:
        entries = build_sample(args.k, args.seed, args.dest, args.key, args.data)
    except ValueError as exc:
        print(f"Выборка не собрана: {exc}", file=sys.stderr)
        return 2
    print(f"Выборка: {len(entries)} выпусков в {args.dest}; ключ — {args.key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
