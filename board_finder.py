"""Автоматический поиск шахматной доски на скриншоте.

Границы клеток доски — это длинные вертикальные и горизонтальные линии перепада
яркости, идущие с равным шагом. Ищем группы вертикальных линий одинаковой длины L
с шагом L/8, уточняем сетку по профилю перепадов и проверяем «шахматку» цветов.
"""
from __future__ import annotations

import numpy as np

from recognizer import checker_colors, split_squares, square_bg

EDGE_THR = 30      # перепад суммы RGB между соседними пикселями
GAP = 10           # разрывы линии до стольких пикселей прощаем (фигуры, стрелки)
MIN_BOARD = 160    # минимальный размер доски в пикселях


def _runs(col: np.ndarray, min_len: int) -> list[tuple[int, int]]:
    idx = np.flatnonzero(col)
    if len(idx) < min_len:
        return []
    breaks = np.flatnonzero(np.diff(idx) > GAP)
    starts = idx[np.r_[0, breaks + 1]]
    ends = idx[np.r_[breaks, len(idx) - 1]]
    return [(int(a), int(b) + 1) for a, b in zip(starts, ends) if b - a + 1 >= min_len]


def _vertical_lines(edges: np.ndarray, min_len: int) -> list[tuple[float, int, int]]:
    """Вертикальные отрезки (x, y0, y1); соседние столбцы склеиваются."""
    cols = np.flatnonzero(edges.sum(axis=0) >= min_len)
    raw = [(int(x), y0, y1) for x in cols for (y0, y1) in _runs(edges[:, x], min_len)]
    raw.sort()
    merged: list[list] = []
    for x, y0, y1 in raw:
        for m in merged:
            if x - m[1] <= 2 and abs(y0 - m[2]) <= GAP and abs(y1 - m[3]) <= GAP:
                m[1] = x
                m[2], m[3] = min(m[2], y0), max(m[3], y1)
                break
        else:
            merged.append([x, x, y0, y1])
    return [((a + b) / 2 + 1, y0, y1) for a, b, y0, y1 in merged]  # +1: diff[i] — граница между i и i+1


def _grid_candidates(lines: list[tuple[float, int, int]]) -> list[tuple[float, float, float]]:
    """Из вертикальных линий -> кандидаты (left, top, square) по сетке с равным шагом."""
    out = []
    used = set()
    lines = sorted(lines, key=lambda l: l[2] - l[1], reverse=True)
    for i, (_, y0, y1) in enumerate(lines):
        if i in used:
            continue
        length = y1 - y0
        tol = max(3, length * 0.01)
        group = [j for j, (_, a, b) in enumerate(lines) if abs(a - y0) <= tol and abs(b - y1) <= tol]
        if len(group) < 5:
            continue
        used.update(group)
        s = length / 8
        xs = np.array(sorted(lines[j][0] for j in group))
        best = None
        for anchor in xs:
            k = np.round((xs - anchor) / s)
            ok = np.abs(xs - anchor - k * s) <= max(2.0, s * 0.05)
            ks, xk = k[ok], xs[ok]
            if len(np.unique(ks)) < 5 or ks.max() - ks.min() > 8:
                continue
            if best is None or len(np.unique(ks)) > len(np.unique(best[0])):
                best = (ks, xk)
        if best is None:
            continue
        ks, xk = best
        step, a = np.polyfit(ks, xk, 1)
        good = np.abs(xk - (a + ks * step)) <= 2.0  # отбрасываем выбросы (рамка доски и т.п.)
        if good.sum() >= 5 and not good.all():
            ks, xk = ks[good], xk[good]
            step, a = np.polyfit(ks, xk, 1)
        if abs(step - s) > s * 0.06:
            continue
        top = float(np.median([lines[j][1] for j in group]))
        kmin, span = ks.min(), ks.max() - ks.min()
        for j in range(int(8 - span) + 1):
            out.append((a + (kmin - j) * step, top, step))
    return out


def _refine(edges_v: np.ndarray, edges_h: np.ndarray, left: float, top: float, s: float) -> tuple[float, float, float]:
    """Подгонка сетки по профилям перепадов (точность до пикселя)."""
    h, w = edges_h.shape[0] + 1, edges_v.shape[1] + 1
    size = 8 * s
    y0, y1 = int(max(0, top)), int(min(h - 1, top + size))
    x0, x1 = int(max(0, left)), int(min(w - 1, left + size))
    px = edges_v[y0:y1].sum(axis=0)        # профиль по x (индекс i = граница i+1)
    py = edges_h[:, x0:x1].sum(axis=1)

    def fit(profile: np.ndarray, start: float, step: float) -> tuple[float, float]:
        best = (-1.0, start, step)
        for ds in np.linspace(-1.0, 1.0, 9):
            st = step + ds / 8
            for d in range(-4, 5):
                pos = np.round(start + d + st * np.arange(1, 8)).astype(int) - 1
                pos = pos[(pos >= 0) & (pos < len(profile))]
                score = float(profile[pos].sum())
                if score > best[0]:
                    best = (score, start + d, st)
        return best[1], best[2]

    left, sx = fit(px, left, s)
    top, sy = fit(py, top, s)
    return left, top, (sx + sy) / 2


def find_boards(img: np.ndarray) -> list[tuple[int, int, int, int, float]]:
    """Кандидаты (x, y, w, h, score) в координатах изображения, лучший — первый."""
    gray = img.astype(np.int16).sum(axis=2)
    edges_v = np.abs(np.diff(gray, axis=1)) > EDGE_THR
    edges_h = np.abs(np.diff(gray, axis=0)) > EDGE_THR
    h, w = gray.shape

    min_len = int(MIN_BOARD * 0.85)
    cands = _grid_candidates(_vertical_lines(edges_v, min_len))
    results = []
    for left, top, s in cands:
        left, top, s = _refine(edges_v, edges_h, left, top, s)
        x, y, size = round(left), round(top), round(8 * s)
        if size < MIN_BOARD or x < 0 or y < 0 or x + size > w or y + size > h:
            continue
        crop = img[y:y + size, x:x + size]
        bgs = np.array([square_bg(sq) for sq in split_squares(crop)])
        light, dark, checker = checker_colors(bgs)
        if checker < 0.75:
            continue
        contrast = float(np.linalg.norm(light - dark))
        # Насколько линии перепадов совпадают с внутренними границами клеток
        inner = np.round(np.arange(1, 8) * size / 8).astype(int)
        align = (edges_v[y:y + size, x + inner - 1].mean() + edges_h[y + inner - 1, x:x + size].mean()) / 2
        results.append((x, y, size, size, checker * align ** 2 * min(contrast, 120) * np.sqrt(size)))
    results.sort(key=lambda r: -r[4])
    # убираем дубликаты (пересекающиеся кандидаты)
    final = []
    for r in results:
        if all(abs(r[0] - f[0]) > r[2] / 4 or abs(r[1] - f[1]) > r[2] / 4 for f in final):
            final.append(r)
    return final


def find_board_on_screen(sct) -> tuple[int, int, int, int] | None:
    """Ищет доску на всех мониторах (mss). Возвращает (x, y, w, h) в экранных пикселях."""
    best = None
    for mon in sct.monitors[1:]:
        shot = np.asarray(sct.grab(mon))[:, :, 2::-1]
        found = find_boards(np.ascontiguousarray(shot))
        if found and (best is None or found[0][4] > best[4]):
            x, y, w, h, score = found[0]
            best = (x + mon["left"], y + mon["top"], w, h, score)
    return best[:4] if best else None
