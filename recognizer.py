"""Распознавание шахматной позиции по скриншоту доски — без калибровки.

1. Встроенный банк силуэтов фигур (популярные наборы chess.com и lichess):
   для каждой клетки выделяется силуэт фигуры, тип берётся по ближайшему силуэту
   из самого подходящего для этой доски набора, цвет — по яркости фигуры.
2. Самообучение: подтверждённые позиции запоминаются как точные образцы для
   текущей темы доски, после чего распознавание становится практически безошибочным
   даже для редких наборов фигур.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

FEAT_SIZE = 32          # размер отпечатка клетки
MASK_THR = 40.0         # насколько пиксель должен отличаться от фона, чтобы считаться фигурой
HIGHLIGHT_THR = 28.0    # насколько фон клетки должен отличаться от обычного, чтобы считаться подсвеченным
THEME_MATCH_THR = 45.0  # насколько цвета клеток могут отличаться от сохранённой темы
EMPTY_AREA = 0.05       # силуэт меньше этой доли клетки — пусто
LEARNED_MATCH = 0.035   # расстояние до выученного образца, при котором ему доверяем
LEARN_CAP = 48          # сколько образцов хранить на каждую метку

# Начальная позиция, строка 0 = 8-я горизонталь
START_ROWS = ["rnbqkbnr", "pppppppp", "........", "........",
              "........", "........", "PPPPPPPP", "RNBQKBNR"]


# ---------------------------------------------------------------- геометрия
def split_squares(img: np.ndarray, dx: int = 0, dy: int = 0) -> list[np.ndarray]:
    """64 клетки построчно сверху вниз; dx/dy — сдвиг сетки."""
    h, w = img.shape[:2]
    pad = max(abs(dx), abs(dy))
    if pad:
        img = np.pad(img, ((pad, pad), (pad, pad), (0, 0)), mode="edge")
    ys = np.linspace(0, h, 9).round().astype(int) + pad + dy
    xs = np.linspace(0, w, 9).round().astype(int) + pad + dx
    return [img[ys[r]:ys[r + 1], xs[c]:xs[c + 1]] for r in range(8) for c in range(8)]


def square_bg(sq: np.ndarray) -> np.ndarray:
    """Цвет фона клетки — медиана узкой рамки по краю."""
    h, w = sq.shape[:2]
    b = max(1, int(min(h, w) * 0.05))
    ring = np.concatenate([
        sq[1:b + 1].reshape(-1, 3), sq[-b - 1:-1].reshape(-1, 3),
        sq[:, 1:b + 1].reshape(-1, 3), sq[:, -b - 1:-1].reshape(-1, 3),
    ]).astype(np.float32)
    return np.median(ring, axis=0)


PARITY = np.array([(r + c) % 2 == 1 for r in range(8) for c in range(8)])  # True — тёмная клетка


def checker_colors(bgs: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """По фонам 64 клеток: (светлый цвет, тёмный цвет, насколько это похоже на доску 0..1)."""
    light, dark = np.median(bgs[~PARITY], axis=0), np.median(bgs[PARITY], axis=0)
    if np.linalg.norm(light - dark) < 25:
        return light, dark, 0.0
    expected = np.where(PARITY[:, None], dark, light)
    return light, dark, float((np.linalg.norm(bgs - expected, axis=1) < 28).mean())


# ---------------------------------------------------------------- признаки клеток
def _fill_holes(mask: np.ndarray) -> np.ndarray:
    """Заливка «дыр» в силуэтах (пачка масок N x S x S)."""
    free = ~mask
    out = np.zeros_like(mask)
    out[:, 0, :], out[:, -1, :] = free[:, 0, :], free[:, -1, :]
    out[:, :, 0] |= free[:, :, 0]
    out[:, :, -1] |= free[:, :, -1]
    while True:
        grown = out.copy()
        grown[:, 1:, :] |= out[:, :-1, :]
        grown[:, :-1, :] |= out[:, 1:, :]
        grown[:, :, 1:] |= out[:, :, :-1]
        grown[:, :, :-1] |= out[:, :, 1:]
        grown &= free
        if np.array_equal(grown, out):
            return ~out
        out = grown


def _blur(x: np.ndarray) -> np.ndarray:
    p = np.pad(x, ((0, 0), (1, 1), (1, 1)), mode="edge")
    return sum(p[:, i:i + x.shape[1], j:j + x.shape[2]] for i in range(3) for j in range(3)) / 9


@dataclass
class Features:
    gray: np.ndarray        # N x F — фигура в оттенках серого, фон нейтрализован (точное сравнение)
    sil: np.ndarray         # N x F — размытый силуэт (сравнение с встроенными наборами)
    area: np.ndarray        # N — доля клетки под фигурой
    bright: np.ndarray      # N — средняя яркость фигуры
    dot: np.ndarray         # N — похоже на точку-подсказку хода
    bg: np.ndarray          # N x 3 — цвет фона клетки


def square_features(squares: list[np.ndarray], bgs: np.ndarray | None = None) -> Features:
    if bgs is None:
        bgs = np.array([square_bg(sq) for sq in squares])
    small = []
    for sq in squares:
        h, w = sq.shape[:2]
        m = int(round(min(h, w) * 0.08))
        inner = np.ascontiguousarray(sq[m:h - m, m:w - m])
        small.append(np.asarray(Image.fromarray(inner).resize((FEAT_SIZE, FEAT_SIZE), Image.BILINEAR)))
    small = np.array(small, dtype=np.float32)
    mask = np.linalg.norm(small - bgs[:, None, None, :], axis=3) > MASK_THR
    filled = _fill_holes(mask)
    gray = small.mean(axis=3)

    n = len(squares)
    area = filled.mean(axis=(1, 2))
    bright = (gray * filled).sum(axis=(1, 2)) / np.maximum(filled.sum(axis=(1, 2)), 1)

    # Точка/кружок-подсказка: маленькое, круглое, по центру
    dot = np.zeros(n, dtype=bool)
    for i in np.flatnonzero((area > 0) & (area < 0.16)):
        ys, xs = np.nonzero(filled[i])
        bh, bw = np.ptp(ys) + 1, np.ptp(xs) + 1
        fill_ratio = filled[i].sum() / (bh * bw)
        cy, cx = ys.mean() / FEAT_SIZE, xs.mean() / FEAT_SIZE
        dot[i] = (fill_ratio > 0.68 and 0.8 < bh / bw < 1.25
                  and abs(cy - 0.5) < 0.12 and abs(cx - 0.5) < 0.12)

    return Features(
        gray=(np.where(mask, gray, 128.0) / 255.0).reshape(n, -1),
        sil=_blur(filled.astype(np.float32)).reshape(n, -1),
        area=area, bright=bright, dot=dot, bg=bgs,
    )


# ---------------------------------------------------------------- встроенные наборы
@dataclass
class PieceBank:
    sil: np.ndarray       # M x F
    gray: np.ndarray      # M x F
    codes: np.ndarray     # M — 'P','n',... (регистр = цвет)
    set_idx: np.ndarray   # M
    set_names: list[str]

    def save(self, path: Path) -> None:
        np.savez_compressed(path, sil=self.sil, gray=self.gray, codes=self.codes, set_idx=self.set_idx,
                            set_names=np.array(self.set_names))

    @classmethod
    def load(cls, path: Path) -> "PieceBank":
        d = np.load(path)
        return cls(d["sil"], d["gray"], d["codes"], d["set_idx"], [str(s) for s in d["set_names"]])


def build_bank(piece_sets: dict[str, dict[str, np.ndarray]]) -> PieceBank:
    """piece_sets: {набор: {'wp': RGBA 96x96, ...}} -> банк силуэтов с вариациями сдвига/масштаба."""
    size = 96
    backgrounds = [(238, 238, 210), (118, 150, 86), (240, 217, 181), (181, 136, 99)]
    sils, grays, codes, idx = [], [], [], []
    names = sorted(piece_sets)
    for si, name in enumerate(names):
        squares, labels = [], []
        for code, rgba in piece_sets[name].items():
            piece = Image.fromarray(rgba)
            for scale in (0.9, 1.0):
                ps = round(size * scale)
                scaled = piece.resize((ps, ps), Image.LANCZOS)
                for k, (dx, dy) in enumerate([(0, 0), (-3, 0), (3, 0), (0, -3), (0, 3)]):
                    bg = backgrounds[k % len(backgrounds)]
                    canvas = Image.new("RGBA", (size, size), bg + (255,))
                    off = (size - ps) // 2
                    canvas.alpha_composite(scaled, (off + dx, off + dy))
                    squares.append(np.asarray(canvas.convert("RGB")))
                    labels.append(code[1].upper() if code[0] == "w" else code[1])
        f = square_features(squares)
        sils.append(f.sil)
        grays.append(f.gray)
        codes += labels
        idx += [si] * len(labels)
    return PieceBank(np.concatenate(sils).astype(np.float32), np.concatenate(grays).astype(np.float32),
                     np.array(codes), np.array(idx), names)


# ---------------------------------------------------------------- выученные темы
@dataclass
class Theme:
    light: np.ndarray
    dark: np.ndarray
    features: np.ndarray    # K x F (gray)
    labels: np.ndarray      # K
    piece_set: int = -1     # какой встроенный набор лучше всего подходит

    def color_distance(self, light: np.ndarray, dark: np.ndarray) -> float:
        return float(max(np.linalg.norm(self.light - light), np.linalg.norm(self.dark - dark)))

    @property
    def key(self) -> str:
        return hashlib.md5(np.concatenate([self.light, self.dark]).round().tobytes()).hexdigest()[:10]


@dataclass
class Recognition:
    grid: list[list[str]]       # по экрану: grid[0] — верхний ряд; '.' — пусто
    last_mover: str | None      # 'w'/'b' — кто ходил последним (по подсветке), если ясно
    features: Features
    theme: Theme


class BoardRecognizer:
    def __init__(self, bank: PieceBank, themes_dir: Path) -> None:
        self.bank = bank
        self.themes_dir = themes_dir
        self.themes: list[Theme] = []
        self._dirty: set[str] = set()
        themes_dir.mkdir(parents=True, exist_ok=True)
        for path in themes_dir.glob("*.npz"):
            try:
                d = np.load(path)
                self.themes.append(Theme(d["light"], d["dark"], d["features"], d["labels"],
                                         int(d["piece_set"])))
            except Exception:
                path.unlink(missing_ok=True)

    def _theme(self, light: np.ndarray, dark: np.ndarray) -> Theme:
        best = min(self.themes, key=lambda t: t.color_distance(light, dark), default=None)
        if best is not None and best.color_distance(light, dark) <= THEME_MATCH_THR:
            return best
        theme = Theme(light, dark, np.zeros((0, FEAT_SIZE * FEAT_SIZE), np.float32), np.array([], dtype="<U1"))
        self.themes.append(theme)
        return theme

    # ---------- распознавание ----------
    def recognize(self, img: np.ndarray) -> Recognition | None:
        squares = split_squares(img)
        bgs = np.array([square_bg(sq) for sq in squares])
        light, dark, checker = checker_colors(bgs)
        if checker < 0.6:
            return None  # в этой области нет доски
        theme = self._theme(light, dark)
        f = square_features(squares, bgs)

        labels = np.full(64, "", dtype="<U1")
        if len(theme.labels):
            d = np.abs(f.gray[:, None, :] - theme.features[None, :, :]).mean(axis=2)
            nearest = d.argmin(axis=1)
            sure = d[np.arange(64), nearest] < LEARNED_MATCH
            labels[sure] = theme.labels[nearest[sure]]

        todo = np.flatnonzero(labels == "")
        empty = (f.area[todo] < EMPTY_AREA) | f.dot[todo]
        labels[todo[empty]] = "."
        todo = todo[~empty]
        if len(todo):
            d = np.abs(f.sil[todo, None, :] - self.bank.sil[None, :, :]).mean(axis=2)
            if theme.piece_set < 0 or len(todo) >= 6:
                # Выбираем набор фигур, лучше всего объясняющий доску
                scores = [d[:, self.bank.set_idx == s].min(axis=1).sum() for s in range(len(self.bank.set_names))]
                best_set = int(np.argmin(scores))
                if theme.piece_set != best_set and len(todo) >= 6:
                    theme.piece_set = best_set
                    self._dirty.add(theme.key)
            # Тип — по силуэту, цвет — по сравнению с белой/чёрной версией фигуры того же набора
            cols = self.bank.set_idx == max(theme.piece_set, 0)
            dg = np.abs(f.gray[todo, None, :] - self.bank.gray[None, cols, :]).mean(axis=2)
            labels[todo] = self.bank.codes[cols][(d[:, cols] + dg).argmin(axis=1)]

        grid = [[str(x) for x in labels[r * 8:(r + 1) * 8]] for r in range(8)]
        return Recognition(grid, self._last_mover(labels, f, theme), f, theme)

    @staticmethod
    def _last_mover(labels: np.ndarray, f: Features, theme: Theme) -> str | None:
        normal = np.where(PARITY[:, None], theme.dark, theme.light)
        other = np.where(PARITY[:, None], theme.light, theme.dark)
        lit = ((np.linalg.norm(f.bg - normal, axis=1) > HIGHLIGHT_THR)
               & (np.linalg.norm(f.bg - other, axis=1) > HIGHLIGHT_THR) & (labels != "."))
        return _last_mover([str(p) for p in labels[lit]])

    # ---------- самообучение ----------
    def learn(self, rec: Recognition, grid: list[list[str]]) -> None:
        """Запоминает клетки подтверждённой позиции как точные образцы для этой темы."""
        theme = rec.theme
        labels = np.array([p for row in grid for p in row])
        feats = rec.features.gray.astype(np.float32)
        if len(theme.labels):
            d = np.abs(feats[:, None, :] - theme.features[None, :, :]).mean(axis=2)
            new = d.min(axis=1) > LEARNED_MATCH * 0.5
        else:
            new = np.ones(64, dtype=bool)
        if not new.any():
            return
        all_f = np.concatenate([theme.features, feats[new]])
        all_l = np.concatenate([theme.labels, labels[new]])
        keep = np.concatenate([np.flatnonzero(all_l == lab)[-LEARN_CAP:] for lab in np.unique(all_l)])
        keep.sort()
        theme.features, theme.labels = all_f[keep], all_l[keep]
        self._dirty.add(theme.key)

    def save(self) -> None:
        for theme in self.themes:
            if theme.key in self._dirty and len(theme.labels):
                np.savez_compressed(self.themes_dir / f"{theme.key}.npz", light=theme.light, dark=theme.dark,
                                    features=theme.features, labels=theme.labels,
                                    piece_set=np.array(theme.piece_set))
        self._dirty.clear()

    def forget(self) -> None:
        """Сбросить всё выученное (если распознавание начало ошибаться)."""
        self.themes.clear()
        for path in self.themes_dir.glob("*.npz"):
            path.unlink(missing_ok=True)


def _last_mover(highlighted: list[str]) -> str | None:
    """По фигурам на подсвеченных клетках определяем, кто ходил последним."""
    white = [p for p in highlighted if p.isupper()]
    black = [p for p in highlighted if p.islower()]
    if white and not black:
        return "w"
    if black and not white:
        return "b"
    if white and black:
        # Подсвеченный король без других фигур своего цвета — это шах, ход его стороны
        if all(p == "K" for p in white):
            return "b"
        if all(p == "k" for p in black):
            return "w"
    return None


def guess_white_bottom(grid: list[list[str]]) -> tuple[bool, float]:
    """Где белые: по среднему ряду фигур (короли весомее). Возвращает (белые снизу?, уверенность)."""
    def mean_row(is_white: bool) -> float | None:
        total = weight = 0.0
        for r, row in enumerate(grid):
            for p in row:
                if p != "." and p.isupper() == is_white:
                    w = 3.0 if p in "Kk" else 1.0
                    total += r * w
                    weight += w
        return total / weight if weight else None

    w, b = mean_row(True), mean_row(False)
    if w is None or b is None:
        return True, 0.0
    return w > b, abs(w - b)


def grid_to_placement(grid: list[list[str]], white_bottom: bool) -> str:
    rows = grid if white_bottom else [row[::-1] for row in grid[::-1]]
    out = []
    for row in rows:
        s, empty = "", 0
        for p in row:
            if p == ".":
                empty += 1
            else:
                if empty:
                    s += str(empty)
                    empty = 0
                s += p
        if empty:
            s += str(empty)
        out.append(s)
    return "/".join(out)


def placement_to_grid(placement: str, white_bottom: bool) -> list[list[str]]:
    rows = []
    for part in placement.split("/"):
        row = []
        for ch in part:
            row += ["."] * int(ch) if ch.isdigit() else [ch]
        rows.append(row)
    return rows if white_bottom else [row[::-1] for row in rows[::-1]]
