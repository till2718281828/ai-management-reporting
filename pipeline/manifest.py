"""Манифест исходников: SHA-256 канонического представления каждого CSV.

Канон — таблица с колонками в алфавитном порядке, все значения строками, LF. Хэш не зависит от
порядка колонок и переводов строк в файле, но меняется от любой правки значения.
Проверяющий сверяет манифест выпуска с исходниками, которые пересчитывает сам.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

SOURCES = ["journal.csv", "accounts.csv", "mapping.csv"]


def canonical_hash(path: Path) -> tuple[str, int]:
    """SHA-256 канона и число строк."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df = df[sorted(df.columns)]
    text = df.to_csv(index=False, lineterminator="\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest(), len(df)


def build_manifest(data_dir: Path) -> dict:
    files = {}
    for name in SOURCES:
        sha, rows = canonical_hash(Path(data_dir) / name)
        files[name] = {"rows": rows, "sha256": sha}
    return {"sources": files}


def write_manifest(data_dir: Path, out_dir: Path) -> dict:
    manifest = build_manifest(data_dir)
    (Path(out_dir) / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return manifest
