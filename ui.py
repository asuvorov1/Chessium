"""Виджеты интерфейса Chessium (тёмная тема в стиле chess.com)."""
from __future__ import annotations

import chess
import chess.svg
from PyQt6.QtCore import QByteArray, QEasingCurve, QPointF, QRectF, QSize, Qt, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPolygonF
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QSizePolicy, QWidget

from engine_api import Analysis, line_to_san
from game import CLASS_ORDER, CLASSES, Node

BG = "#262421"
CARD = "#1e1d1b"
CARD_2 = "#302e2b"
TEXT = "#e8e6e3"
MUTED = "#9b9893"
ACCENT = "#81b64c"
LIGHT_SQ = "#ebecd0"
DARK_SQ = "#739552"

STYLE = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-family: 'Segoe UI'; font-size: 13px; }}
QLabel {{ background: transparent; }}
QFrame#Card {{ background: {CARD}; border-radius: 10px; }}
QPushButton {{ background: #3a3835; border: none; border-radius: 6px; padding: 7px 12px; color: {TEXT}; }}
QPushButton:hover {{ background: #4a4744; }}
QPushButton:pressed {{ background: #2f2d2a; }}
QPushButton:disabled {{ color: #6b6863; }}
QPushButton#Primary {{ background: {ACCENT}; color: white; font-weight: 600; }}
QPushButton#Primary:hover {{ background: #95c75f; }}
QPushButton#Nav {{ font-size: 22px; font-weight: 600; padding: 0 0 4px 0; min-width: 48px; min-height: 34px; }}
QPushButton#Live {{ background: transparent; color: {MUTED}; font-weight: 600; }}
QPushButton#Live:checked {{ color: {ACCENT}; }}
QPushButton#Tab {{ background: transparent; border-radius: 0; padding: 9px 0; color: {MUTED};
                  font-weight: 600; border-bottom: 2px solid transparent; }}
QPushButton#Tab:checked {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QPushButton#Icon {{ background: transparent; font-size: 17px; padding: 4px 8px; }}
QPushButton#Icon:hover {{ background: #3a3835; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ width: 8px; background: transparent; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #4a4744; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QMenu {{ background: #2f2d2a; border: 1px solid #45423e; padding: 6px; border-radius: 8px; }}
QMenu::item {{ padding: 7px 26px 7px 22px; border-radius: 5px; }}
QMenu::item:selected {{ background: #45423e; }}
QMenu::separator {{ height: 1px; background: #45423e; margin: 5px 8px; }}
QMenu::indicator {{ width: 14px; height: 14px; left: 4px; }}
QToolTip {{ background: #2f2d2a; color: {TEXT}; border: 1px solid #45423e; padding: 4px; }}
"""


def font(px: int, bold: bool = False) -> QFont:
    f = QFont("Segoe UI")
    f.setPixelSize(px)
    f.setBold(bold)
    return f


def draw_badge(p: QPainter, center: QPointF, r: float, cls: str) -> None:
    _, sym, color = CLASSES[cls]
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor(0, 0, 0, 80), max(1.0, r * 0.12)))
    p.setBrush(QColor(color))
    p.drawEllipse(center, r, r)
    f = font(max(6, int(r * (1.25 if len(sym) == 1 else 0.98))), True)
    p.setFont(f)
    p.setPen(QColor("white"))
    p.drawText(QRectF(center.x() - r, center.y() - r * 1.02, 2 * r, 2 * r), Qt.AlignmentFlag.AlignCenter, sym)
    p.restore()


def score_chip(p: QPainter, rect: QRectF, text: str, white_better: bool) -> None:
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#f0f0f0") if white_better else QColor("#403e3b"))
    p.drawRoundedRect(rect, 4, 4)
    p.setPen(QColor("#1e1d1b") if white_better else QColor("white"))
    p.setFont(font(12, True))
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
    p.restore()


# ---------------------------------------------------------------- шкала оценки
class EvalBar(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedWidth(26)
        self._shown = 50.0
        self.text = ""
        self.white_bottom = True
        self.horizontal = False
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(350)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def _on_anim(self, v) -> None:
        self._shown = float(v)
        self.update()

    def set_value(self, white_win: float, text: str, white_bottom: bool) -> None:
        self.text = text
        self.white_bottom = white_bottom
        self._anim.stop()
        self._anim.setStartValue(self._shown)
        self._anim.setEndValue(max(2.0, min(98.0, white_win)) if text else 50.0)
        self._anim.start()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 0, -1, 0)
        clip = QPainterPath()
        clip.addRoundedRect(r, 4, 4)
        p.setClipPath(clip)
        p.fillRect(r, QColor("#403d39"))
        if self.horizontal:
            ww = r.width() * self._shown / 100
            p.fillRect(QRectF(r.left(), r.top(), ww, r.height()), QColor("#f2f2f2"))
            if self.text:
                white_ahead = self._shown >= 50
                p.setFont(font(12, True))
                p.setPen(QColor("#2b2a28") if white_ahead else QColor("#f2f2f2"))
                align = Qt.AlignmentFlag.AlignLeft if white_ahead else Qt.AlignmentFlag.AlignRight
                p.drawText(r.adjusted(8, 0, -8, 0), align | Qt.AlignmentFlag.AlignVCenter, self.text)
            return
        wh = r.height() * self._shown / 100
        white_rect = (QRectF(r.left(), r.bottom() - wh, r.width(), wh) if self.white_bottom
                      else QRectF(r.left(), r.top(), r.width(), wh))
        p.fillRect(white_rect, QColor("#f2f2f2"))
        if self.text:
            white_ahead = self._shown >= 50
            p.setFont(font(10, True))
            at_bottom = white_ahead == self.white_bottom
            tr = QRectF(r.left(), r.bottom() - 22, r.width(), 20) if at_bottom else QRectF(r.left(), r.top() + 2, r.width(), 20)
            p.setPen(QColor("#2b2a28") if white_ahead else QColor("#f2f2f2"))
            txt = self.text.lstrip("+-") if not self.text.startswith("-M") else self.text[1:]
            p.drawText(tr, Qt.AlignmentFlag.AlignCenter, txt)


# ---------------------------------------------------------------- мини-доска
class BoardView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(220, 220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.board = chess.Board()
        self.white_bottom = True
        self.lastmove: chess.Move | None = None
        self.arrows: list[tuple[int, int, str]] = []
        self.badge: tuple[int, str] | None = None
        self._renderer: QSvgRenderer | None = None
        self._dirty = True
        self.placeholder = ""

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        return w

    def sizeHint(self) -> QSize:
        return QSize(340, 340)

    def set_state(self, board: chess.Board, white_bottom: bool, lastmove: chess.Move | None,
                  arrows: list[tuple[int, int, str]], badge: tuple[int, str] | None) -> None:
        self.board, self.white_bottom, self.lastmove = board, white_bottom, lastmove
        self.arrows, self.badge = arrows, badge
        self.placeholder = ""
        self._dirty = True
        self.update()

    def _svg(self) -> QSvgRenderer:
        if self._dirty or self._renderer is None:
            check = self.board.king(self.board.turn) if self.board.is_check() else None
            svg = chess.svg.board(
                self.board, orientation=self.white_bottom, lastmove=self.lastmove, check=check,
                arrows=[chess.svg.Arrow(a, b, color=c) for a, b, c in self.arrows],
                coordinates=False, size=400,
                colors={"square light": LIGHT_SQ, "square dark": DARK_SQ, "arrow green": "#81b64cd0",
                        "arrow blue": "#5b9bd5d0", "arrow red": "#fa412dd0",
                        "square light lastmove": "#f5f682", "square dark lastmove": "#b9ca43"})
            self._renderer = QSvgRenderer(QByteArray(svg.encode()))
            self._dirty = False
        return self._renderer

    def _square_rect(self, sq: int, area: QRectF) -> QRectF:
        f, r = chess.square_file(sq), chess.square_rank(sq)
        col, row = (f, 7 - r) if self.white_bottom else (7 - f, r)
        s = area.width() / 8
        return QRectF(area.left() + col * s, area.top() + row * s, s, s)

    def board_rect(self) -> QRectF:
        side = min(self.width(), self.height())
        return QRectF((self.width() - side) / 2, 0, side, side)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = self.board_rect()
        clip = QPainterPath()
        clip.addRoundedRect(area, 6, 6)
        p.setClipPath(clip)
        self._svg().render(p, area)
        p.setClipping(False)

        # координаты как на chess.com
        s = area.width() / 8
        p.setFont(font(max(8, int(s * 0.2)), True))
        for i in range(8):
            file_ch = "abcdefgh"[i if self.white_bottom else 7 - i]
            rank_ch = str(8 - i if self.white_bottom else i + 1)
            p.setPen(QColor(DARK_SQ if i % 2 == 0 else LIGHT_SQ))
            p.drawText(QRectF(area.left() + 3, area.top() + i * s + 1, s, s), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, rank_ch)
            p.setPen(QColor(LIGHT_SQ if i % 2 == 0 else DARK_SQ))
            p.drawText(QRectF(area.left() + i * s, area.bottom() - s, s - 3, s - 1), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, file_ch)

        if self.badge:
            sq, cls = self.badge
            rect = self._square_rect(sq, area)
            r = s * 0.2
            draw_badge(p, QPointF(min(rect.right() - r * 0.4, area.right() - r - 1),
                                  max(rect.top() + r * 0.4, area.top() + r + 1)), r, cls)

        if self.placeholder:
            p.fillRect(area, QColor(20, 19, 18, 170))
            p.setPen(QColor(TEXT))
            p.setFont(font(15, True))
            p.drawText(area.adjusted(20, 0, -20, 0), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.placeholder)


# ---------------------------------------------------------------- линии движка
class LinesView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[tuple[str, bool, str]] = []
        self.header = ""
        self.setMinimumHeight(30)

    def set_analysis(self, analysis: Analysis | None, pending: bool) -> None:
        self.rows = []
        if analysis and analysis.result:
            self.header = {"1-0": "Мат — победа белых", "0-1": "Мат — победа чёрных"}.get(analysis.result, "Ничья")
        elif analysis:
            self.header = f"Stockfish · глубина {analysis.depth}"
            for line in analysis.lines:
                self.rows.append((line.score_text(), line.win >= 50, line_to_san(analysis.fen, line.pv, 12)))
        else:
            self.header = "Анализ…" if pending else ""
        self.setFixedHeight(24 + 28 * max(1, len(self.rows)))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setFont(font(11, True))
        p.setPen(QColor(MUTED))
        p.drawText(QRectF(2, 0, self.width(), 20), Qt.AlignmentFlag.AlignVCenter, self.header.upper())
        y = 24
        for score, white_better, san in self.rows:
            score_chip(p, QRectF(0, y + 2, 54, 22), score, white_better)
            p.setFont(font(13))
            p.setPen(QColor(TEXT))
            fm = QFontMetrics(p.font())
            p.drawText(QRectF(62, y, self.width() - 64, 26), Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(san, Qt.TextElideMode.ElideRight, self.width() - 66))
            y += 28


# ---------------------------------------------------------------- оценка хода
class FeedbackCard(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(58)
        self.node: Node | None = None
        self.prev: Node | None = None

    def set_node(self, node: Node | None, prev: Node | None) -> None:
        self.node, self.prev = node, prev
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(CARD_2))
        p.drawRoundedRect(QRectF(self.rect()), 8, 8)
        node = self.node
        if node is None or node.move is None:
            p.setPen(QColor(MUTED))
            p.setFont(font(13))
            p.drawText(QRectF(self.rect()).adjusted(14, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter,
                       "Сделайте ход на доске — здесь появится его оценка")
            return
        if node.cls is None:
            p.setPen(QColor(MUTED))
            p.setFont(font(14, True))
            p.drawText(QRectF(self.rect()).adjusted(14, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, f"{node.san} — оцениваю…")
            return
        name, _, color = CLASSES[node.cls]
        draw_badge(p, QPointF(29, self.height() / 2), 15, node.cls)
        title = f"{node.san} — {name.lower()}"
        if node.cls in ("best", "brilliant", "great"):
            title = f"{node.san} — {name.lower()} ход"
        p.setPen(QColor(color))
        p.setFont(font(15, True))
        has_sub = self.prev is not None and self.prev.analysis and self.prev.analysis.best \
            and node.cls not in ("best", "brilliant", "great", "book")
        p.drawText(QRectF(54, 6 if has_sub else 0, self.width() - 130, 26 if has_sub else self.height()),
                   Qt.AlignmentFlag.AlignVCenter, title)
        if has_sub:
            best = self.prev.analysis.best
            san = line_to_san(self.prev.board.fen(), [best.move], 1).split(" ")[-1]
            p.setPen(QColor(MUTED))
            p.setFont(font(12))
            p.drawText(QRectF(54, 30, self.width() - 130, 22), Qt.AlignmentFlag.AlignVCenter,
                       f"Лучше было {san} ({best.score_text()})")
        if node.analysis:
            score_chip(p, QRectF(self.width() - 66, (self.height() - 24) / 2, 54, 24),
                       node.analysis.score_text(), node.analysis.win >= 50)


# ---------------------------------------------------------------- график оценки
class EvalGraph(QWidget):
    selected = pyqtSignal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(84)
        self.values: list[float | None] = []
        self.classes: list[str | None] = []
        self.current = -1
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_data(self, nodes: list[Node], current: int) -> None:
        self.values = [n.analysis.win if n.analysis else None for n in nodes]
        self.classes = [n.cls for n in nodes]
        self.current = current
        self.update()

    def _x(self, i: int) -> float:
        n = max(1, len(self.values) - 1)
        return 4 + (self.width() - 8) * i / n

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(r, 8, 8)
        p.setClipPath(clip)
        p.fillRect(r, QColor("#403d39"))
        if len(self.values) < 2:
            p.setPen(QColor(MUTED))
            p.setFont(font(12))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "График оценки появится по ходу партии")
            return
        h = r.height()
        pts, last = [], 50.0
        for i, v in enumerate(self.values):
            last = v if v is not None else last
            pts.append(QPointF(self._x(i), h - h * (4 + last * 0.92) / 100))
        poly = QPolygonF([QPointF(0, h), QPointF(0, pts[0].y())] + pts + [QPointF(r.width(), pts[-1].y()), QPointF(r.width(), h)])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#f2f2f2"))
        p.drawPolygon(poly)
        p.setPen(QPen(QColor(128, 128, 128, 120), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(0, h / 2), QPointF(r.width(), h / 2))
        if 0 <= self.current < len(pts):
            p.setPen(QPen(QColor(ACCENT), 2))
            p.drawLine(QPointF(pts[self.current].x(), 0), QPointF(pts[self.current].x(), h))
        for i, c in enumerate(self.classes):
            if c in ("blunder", "mistake", "miss", "brilliant", "great", "inaccuracy"):
                p.setPen(QPen(QColor(BG), 1.5))
                p.setBrush(QColor(CLASSES[c][2]))
                p.drawEllipse(pts[i], 4, 4)

    def mousePressEvent(self, e):
        if len(self.values) >= 2:
            n = len(self.values) - 1
            i = round((e.position().x() - 4) / max(1, self.width() - 8) * n)
            self.selected.emit(max(0, min(n, i)))


# ---------------------------------------------------------------- список ходов
class MoveList(QWidget):
    selected = pyqtSignal(int)
    ROW = 30

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[tuple[int, int | None, int | None]] = []
        self.nodes: list[Node] = []
        self.current = 0
        self._hover: int | None = None
        self.setMouseTracking(True)

    def set_nodes(self, nodes: list[Node], current: int) -> None:
        self.nodes, self.current = nodes, current
        self.rows = []
        for i in range(1, len(nodes)):
            before = nodes[i - 1].board
            num = before.fullmove_number
            if before.turn == chess.WHITE:
                self.rows.append((num, i, None))
            elif self.rows and self.rows[-1][0] == num and self.rows[-1][2] is None:
                self.rows[-1] = (num, self.rows[-1][1], i)
            else:
                self.rows.append((num, None, i))
        self.setMinimumHeight(max(self.ROW, self.ROW * len(self.rows)))
        self.update()

    def row_of(self, index: int) -> int:
        for r, (_, w, b) in enumerate(self.rows):
            if index in (w, b):
                return r
        return 0

    def _cell(self, row: int, col: int) -> QRectF:
        cw = (self.width() - 46) / 2
        return QRectF(46 + col * cw, row * self.ROW, cw - 4, self.ROW)

    def _hit(self, pos) -> int | None:
        row = int(pos.y() // self.ROW)
        if not 0 <= row < len(self.rows):
            return None
        for col in (0, 1):
            if self._cell(row, col).contains(pos):
                return self.rows[row][1 + col]
        return None

    def mouseMoveEvent(self, e):
        h = self._hit(e.position())
        if h != self._hover:
            self._hover = h
            self.setCursor(Qt.CursorShape.PointingHandCursor if h else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, _):
        self._hover = None
        self.update()

    def mousePressEvent(self, e):
        h = self._hit(e.position())
        if h:
            self.selected.emit(h)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.rows:
            p.setPen(QColor(MUTED))
            p.setFont(font(13))
            p.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, "Ходов пока нет")
            return
        for r, (num, w, b) in enumerate(self.rows):
            if r % 2 == 1:
                p.fillRect(QRectF(0, r * self.ROW, self.width(), self.ROW), QColor(255, 255, 255, 8))
            p.setPen(QColor(MUTED))
            p.setFont(font(13))
            p.drawText(QRectF(8, r * self.ROW, 34, self.ROW), Qt.AlignmentFlag.AlignVCenter, f"{num}.")
            for col, idx in enumerate((w, b)):
                if idx is None:
                    if col == 0:
                        p.drawText(self._cell(r, 0).adjusted(8, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, "…")
                    continue
                node = self.nodes[idx]
                cell = self._cell(r, col)
                if idx == self.current or idx == self._hover:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor("#4b4847") if idx == self.current else QColor(255, 255, 255, 18))
                    p.drawRoundedRect(cell.adjusted(0, 3, 0, -3), 4, 4)
                x = cell.left() + 6
                if node.cls:
                    draw_badge(p, QPointF(x + 8, cell.center().y()), 8, node.cls)
                    x += 22
                p.setFont(font(14, idx == self.current))
                p.setPen(QColor(CLASSES[node.cls][2]) if node.cls in ("blunder", "mistake", "miss", "inaccuracy", "brilliant", "great")
                         else QColor(TEXT))
                p.drawText(QRectF(x, cell.top(), cell.right() - x, self.ROW), Qt.AlignmentFlag.AlignVCenter, node.san)


# ---------------------------------------------------------------- отчёт
class ReportView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.report: dict | None = None
        self.setMinimumHeight(110 + 26 * len(CLASS_ORDER))

    def set_report(self, report: dict) -> None:
        self.report = report
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        rep = self.report or {chess.WHITE: {"accuracy": None, "counts": {}}, chess.BLACK: {"accuracy": None, "counts": {}}}
        p.setFont(font(11, True))
        p.setPen(QColor(MUTED))
        p.drawText(QRectF(0, 0, w, 18), Qt.AlignmentFlag.AlignLeft, "ТОЧНОСТЬ")
        cw = (w - 10) / 2
        for i, color in enumerate((chess.WHITE, chess.BLACK)):
            box = QRectF(i * (cw + 10), 24, cw, 70)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#f2f2f2") if color else QColor("#403d39"))
            p.drawRoundedRect(box, 8, 8)
            fg = QColor("#262421") if color else QColor("white")
            p.setPen(fg)
            p.setFont(font(12, True))
            p.drawText(box.adjusted(12, 8, 0, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                       "Белые" if color else "Чёрные")
            acc = rep[color]["accuracy"]
            p.setFont(font(28, True))
            p.drawText(box.adjusted(12, 0, -12, -6), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
                       f"{acc:.1f}" if acc is not None else "—")
        y = 108
        for cls in CLASS_ORDER:
            name, _, color = CLASSES[cls]
            wc, bc = rep[chess.WHITE]["counts"].get(cls, 0), rep[chess.BLACK]["counts"].get(cls, 0)
            p.setFont(font(13, True))
            p.setPen(QColor(color) if (wc or bc) else QColor("#6b6863"))
            p.drawText(QRectF(0, y, cw * 0.5, 24), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, str(wc))
            draw_badge(p, QPointF(cw * 0.5 + 22, y + 12), 9, cls)
            p.setPen(QColor(TEXT) if (wc or bc) else QColor("#6b6863"))
            p.setFont(font(13))
            p.drawText(QRectF(cw * 0.5 + 38, y, w - cw - 76, 24), Qt.AlignmentFlag.AlignVCenter, name)
            p.setFont(font(13, True))
            p.setPen(QColor(color) if (wc or bc) else QColor("#6b6863"))
            p.drawText(QRectF(w - cw * 0.5 - 30, y, 30, 24), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, str(bc))
            y += 26


# ---------------------------------------------------------------- индикатор состояния
class StatusPill(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.text, self.color = "", MUTED
        self.setFixedHeight(26)

    def set_status(self, text: str, color: str) -> None:
        if (text, color) != (self.text, self.color):
            self.text, self.color = text, color
            self.setToolTip(text)
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(font(12, True))
        fm = QFontMetrics(p.font())
        text = fm.elidedText(self.text, Qt.TextElideMode.ElideRight, self.width() - 34)
        w = min(self.width(), fm.horizontalAdvance(text) + 32)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(CARD_2))
        p.drawRoundedRect(QRectF(0, 0, w, self.height()), 13, 13)
        p.setBrush(QColor(self.color))
        p.drawEllipse(QPointF(13, self.height() / 2), 4, 4)
        p.setPen(QColor(TEXT))
        p.drawText(QRectF(23, 0, w - 26, self.height()), Qt.AlignmentFlag.AlignVCenter, text)
