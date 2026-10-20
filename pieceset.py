"""Наборы фигур -> банк силуэтов для распознавания.

Встроенный набор (cburnett из python-chess) доступен всегда. Популярные наборы
chess.com и lichess скачиваются при первом запуске в папку данных пользователя —
они не распространяются вместе с программой.
"""
from __future__ import annotations

import hashlib
import logging
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import chess
import chess.svg
import numpy as np
from PIL import Image
from PyQt6.QtCore import QByteArray
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer

from recognizer import PieceBank, build_bank

RENDER_SIZE = 96
BANK_VERSION = "5"
CODES = [c + p for c in "wb" for p in "pnbrqk"]

CHESSCOM_SETS = ["neo", "classic", "wood", "alpha", "game_room", "tournament"]
LICHESS_SETS = ["merida", "alpha", "california", "maestro", "staunty"]

log = logging.getLogger("chessium")


def _sources() -> dict[str, tuple[str, str]]:
    """набор -> (шаблон URL с {code}, расширение файла)."""
    out = {f"chesscom_{s}": (f"https://images.chesscomfiles.com/chess-themes/pieces/{s}/150/{{code}}.png", ".png")
           for s in CHESSCOM_SETS}
    out.update({f"lichess_{s}": (f"https://lichess1.org/assets/piece/{s}/{{code}}.svg", ".svg") for s in LICHESS_SETS})
    return out


def download_piece_sets(pieces_dir: Path, timeout: float = 10) -> None:
    """Докачивает недостающие наборы параллельно. Ошибки сети не критичны — работаем с тем, что есть."""
    jobs = []
    for name, (url, ext) in _sources().items():
        d = pieces_dir / name
        d.mkdir(parents=True, exist_ok=True)
        for code in CODES:
            path = d / f"{code}{ext}"
            if not path.exists():
                remote = code if ext == ".png" else code[0] + code[1].upper()
                jobs.append((url.format(code=remote), path))

    def fetch(job: tuple[str, Path]) -> None:
        url, path = job
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Chessium"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
            tmp = path.with_suffix(path.suffix + ".part")
            tmp.write_bytes(data)
            tmp.replace(path)
        except Exception as e:
            log.warning("Не удалось скачать %s: %s", url, e)

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(fetch, jobs))


def _render_svg(svg: bytes) -> np.ndarray:
    renderer = QSvgRenderer(QByteArray(svg))
    img = QImage(RENDER_SIZE, RENDER_SIZE, QImage.Format.Format_RGBA8888)
    img.fill(0)
    p = QPainter(img)
    renderer.render(p)
    p.end()
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    return np.frombuffer(ptr, np.uint8).reshape(RENDER_SIZE, img.bytesPerLine() // 4, 4)[:, :RENDER_SIZE].copy()


def _load_image(path: Path) -> np.ndarray:
    if path.suffix == ".svg":
        return _render_svg(path.read_bytes())
    im = Image.open(path).convert("RGBA").resize((RENDER_SIZE, RENDER_SIZE), Image.LANCZOS)
    return np.asarray(im).copy()


def builtin_set() -> dict[str, np.ndarray]:
    """Набор cburnett, встроенный в python-chess."""
    out = {}
    for code in CODES:
        piece = chess.Piece.from_symbol(code[1].upper() if code[0] == "w" else code[1])
        svg = chess.svg.piece(piece, size=45)
        out[code] = _render_svg(svg.encode())
    return out


def load_piece_sets(pieces_dir: Path, exclude: tuple[str, ...] = ()) -> dict[str, dict[str, np.ndarray]]:
    sets = {"builtin_cburnett": builtin_set()}
    if pieces_dir.is_dir():
        for d in sorted(p for p in pieces_dir.iterdir() if p.is_dir() and p.name not in exclude):
            try:
                pieces = {f.stem: _load_image(f) for f in d.iterdir() if f.suffix in (".png", ".svg")}
            except Exception as e:
                log.warning("Битый набор %s: %s", d.name, e)
                continue
            if sorted(pieces) == sorted(CODES):
                sets[d.name] = pieces
    return sets


def load_bank(pieces_dir: Path, cache_dir: Path) -> PieceBank:
    """Банк силуэтов; пересобирается только если поменялись файлы фигур."""
    files = sorted(pieces_dir.rglob("*.*")) if pieces_dir.is_dir() else []
    digest = hashlib.md5((BANK_VERSION + "".join(f"{f.relative_to(pieces_dir)}{f.stat().st_size}"
                                                  for f in files)).encode()).hexdigest()[:10]
    cache = cache_dir / f"bank_{digest}.npz"
    if cache.exists():
        try:
            return PieceBank.load(cache)
        except Exception:
            pass
    bank = build_bank(load_piece_sets(pieces_dir))
    cache_dir.mkdir(parents=True, exist_ok=True)
    for old in cache_dir.glob("bank_*.npz"):
        old.unlink(missing_ok=True)
    bank.save(cache)
    return bank
