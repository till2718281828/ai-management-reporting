"""Манифест исходников: SHA-256 канонического представления каждого CSV.

Канон — таблица с колонками в алфавитном порядке, все значения строками, LF. Хэш не зависит от
порядка колонок и переводов строк в файле, но меняется от любой правки значения.
Проверяющий сверяет манифест выпуска с исходниками, которые пересчитывает сам.

При перевыпуске в манифест входят причины изменений (reissue_reasons.csv) и SHA-256 реестра
отправленного выпуска — проверяющий сверяет с ним тот реестр, который ему дали как «отправленный».
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

SOURCES = ["journal.csv", "accounts.csv", "mapping.csv", "commentary.csv"]
OPTIONAL_SOURCES = ["reissue_reasons.csv"]  # входит в манифест, если есть в папке данных


def canonical_hash(path: Path) -> tuple[str, int]:
    """SHA-256 канона и число строк."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df = df[sorted(df.columns)]
    text = df.to_csv(index=False, lineterminator="\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest(), len(df)


def build_manifest(data_dir: Path, previous_registry: Path | None = None) -> dict:
    files = {}
    names = SOURCES + [n for n in OPTIONAL_SOURCES if (Path(data_dir) / n).exists()]
    for name in names:
        sha, rows = canonical_hash(Path(data_dir) / name)
        files[name] = {"rows": rows, "sha256": sha}
    dates = pd.read_csv(Path(data_dir) / "journal.csv", dtype=str, usecols=["date"], encoding="utf-8-sig")["date"]
    manifest = {"sources": files, "journal_last_date": dates.max()}
    if previous_registry is not None:
        sha, rows = canonical_hash(previous_registry)
        manifest["previous_registry"] = {"rows": rows, "sha256": sha}
    return manifest


def write_manifest(data_dir: Path, out_dir: Path, previous_registry: Path | None = None) -> dict:
    manifest = build_manifest(data_dir, previous_registry)
    (Path(out_dir) / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return manifest
