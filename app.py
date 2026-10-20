"""Chessium — анализ шахматной позиции с экрана поверх сайта (в стиле анализа chess.com)."""
from __future__ import annotations

import ctypes
import json
import logging
import os
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

import chess
import mss

MSS = getattr(mss, "MSS", None) or mss.mss
import numpy as np
from PyQt6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QAction, QActionGroup, QColor, QGuiApplication, QIcon, QKeySequence, QPainter, QPen, QPolygonF,
                         QShortcut)
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton,
                             QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

import engine_api
import pieceset
import ui
from board_finder import find_board_on_screen
from engine_api import Analysis
from game import START_PLACEMENT, GameTracker, load_openings
from recognizer import BoardRecognizer, grid_to_placement, guess_white_bottom, placement_to_grid
from version import APP_NAME, REPO_URL, __version__

FROZEN = getattr(sys, "frozen", False)
# Ресурсы программы (в собранном exe — распакованная папка), данные пользователя — отдельно
APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
DATA_DIR = (Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME) if FROZEN else APP_DIR / "data"
ICON_PATH = APP_DIR / "assets" / "icon.ico"
CONFIG_PATH = DATA_DIR / "config.json"
LOG_PATH = DATA_DIR / "chessium.log"

SCAN_INTERVAL_MS = 300
STABLE_TICKS = 2       # столько раз подряд снимок должен совпасть, чтобы принять ход
NEW_GAME_TICKS = 5     # для позиции «из ниоткуда» (новая партия, задача) — дольше
LOST_TICKS = 4         # столько неудачных снимков подряд — ищем доску заново
FIND_COOLDOWN = 2.5

log = logging.getLogger("chessium")


# ---------------------------------------------------------------- утилиты
def setup_logging() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=LOG_PATH, level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s", force=True)

    def hook(exc_type, exc, tb):
        # Без этого PyQt6 аварийно завершает программу при любой ошибке в обработчике
        log.error("Необработанная ошибка:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))

    sys.excepthook = hook
    threading.excepthook = lambda args: hook(args.exc_type, args.exc_value, args.exc_traceback)


def exclude_from_capture(widget: QWidget) -> None:
    """Окно не попадает в скриншоты (Windows 10 2004+), чтобы не мешать распознаванию."""
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.SetWindowDisplayAffinity(int(widget.winId()), 0x11)
        except Exception:
            pass


def dark_title_bar(widget: QWidget) -> None:
    if sys.platform == "win32":
        try:
            value = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(int(widget.winId()), 20, ctypes.byref(value), 4)
        except Exception:
            pass


def native_to_logical(x: float, y: float) -> QPointF:
    for s in QGuiApplication.screens():
        g, dpr = s.geometry(), s.devicePixelRatio()
        if g.x() <= x < g.x() + g.width() * dpr and g.y() <= y < g.y() + g.height() * dpr:
            return QPointF(g.x() + (x - g.x()) / dpr, g.y() + (y - g.y()) / dpr)
    return QPointF(x, y)


# ---------------------------------------------------------------- ручной выбор области
class RegionSelector(QWidget):
    selected = pyqtSignal(object)  # (x, y, w, h) в физических пикселях или None

    def __init__(self, screen) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.scr = screen
        self.setGeometry(screen.geometry())
        self.start: QPointF | None = None
        self.end: QPointF | None = None

    def _rect(self) -> QRectF | None:
        if self.start is None or self.end is None:
            return None
        return QRectF(self.start, self.end).normalized()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        p.setPen(QColor(255, 255, 255))
        p.setFont(ui.font(20, True))
        p.drawText(self.rect().adjusted(0, 40, 0, 0), Qt.AlignmentFlag.AlignHCenter,
                   "Обведите доску мышью точно по краям клеток.  Esc — отмена")
        r = self._rect()
        if r:
            p.fillRect(r, QColor(255, 255, 255, 30))
            p.setPen(QPen(QColor(ui.ACCENT), 2))
            p.drawRect(r)

    def mousePressEvent(self, e):
        self.start = self.end = e.position()
        self.update()

    def mouseMoveEvent(self, e):
        if self.start is not None:
            self.end = e.position()
            self.update()

    def mouseReleaseEvent(self, e):
        self.end = e.position()
        r = self._rect()
        if r is None or r.width() < 80 or r.height() < 80:
            self.start = self.end = None
            self.update()
            return
        g, dpr = self.scr.geometry(), self.scr.devicePixelRatio()
        self.selected.emit((round(g.x() + r.x() * dpr), round(g.y() + r.y() * dpr),
                            round(r.width() * dpr), round(r.height() * dpr)))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.selected.emit(None)


# ---------------------------------------------------------------- слой поверх доски на сайте
class BoardOverlay(QWidget):
    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.arrow: tuple[int, int] | None = None
        self.badge: tuple[int, str] | None = None
        self.white_bottom = True

    def set_board(self, rect: tuple[int, int, int, int]) -> None:
        x, y, w, h = rect
        self.setGeometry(QRectF(native_to_logical(x, y), native_to_logical(x + w, y + h)).toRect())

    def set_marks(self, arrow: tuple[int, int] | None, badge: tuple[int, str] | None, white_bottom: bool) -> None:
        if (arrow, badge, white_bottom) == (self.arrow, self.badge, self.white_bottom):
            return
        self.arrow, self.badge, self.white_bottom = arrow, badge, white_bottom
        if (arrow or badge) and not self.isVisible():
            self.show()
            exclude_from_capture(self)
        self.update()

    def _rect(self, sq: int) -> QRectF:
        f, r = chess.square_file(sq), chess.square_rank(sq)
        col, row = (f, 7 - r) if self.white_bottom else (7 - f, r)
        cw, ch = self.width() / 8, self.height() / 8
        return QRectF(col * cw, row * ch, cw, ch)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = min(self.width(), self.height()) / 8
        if self.arrow:
            a, b = (self._rect(sq).center() for sq in self.arrow)
            color = QColor(129, 182, 76, 205)
            dx, dy = b.x() - a.x(), b.y() - a.y()
            length = (dx * dx + dy * dy) ** 0.5 or 1
            ux, uy = dx / length, dy / length
            head_len, head_w, shaft_w = s * 0.45, s * 0.42, s * 0.17
            base = QPointF(b.x() - ux * head_len, b.y() - uy * head_len)
            p.setPen(QPen(color, shaft_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            p.drawLine(a, base)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawPolygon(QPolygonF([b, QPointF(base.x() - uy * head_w / 2, base.y() + ux * head_w / 2),
                                     QPointF(base.x() + uy * head_w / 2, base.y() - ux * head_w / 2)]))
        if self.badge:
            sq, cls = self.badge
            r = self._rect(sq)
            br = s * 0.2
            ui.draw_badge(p, QPointF(min(r.right() - br * 0.4, self.width() - br - 1),
                                     max(r.top() + br * 0.4, br + 1)), br, cls)


class Bridge(QObject):
    analysis_done = pyqtSignal(object)
    analysis_failed = pyqtSignal(str, str, bool)
    board_found = pyqtSignal(object)
    bank_ready = pyqtSignal(object)
    progress = pyqtSignal(str)


def card() -> QFrame:
    f = QFrame()
    f.setObjectName("Card")
    return f


# ---------------------------------------------------------------- главное окно
class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle(f"{APP_NAME} — анализ партии")
        self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.setMinimumSize(380, 680)

        self.cfg = self._load_config()
        self.bridge = Bridge()
        self.bridge.analysis_done.connect(self._on_analysis)
        self.bridge.analysis_failed.connect(self._on_analysis_error)
        self.bridge.board_found.connect(self._on_board_found)
        self.bridge.bank_ready.connect(self._on_bank_ready)
        self.bridge.progress.connect(lambda text: self._set_status(text, ui.MUTED))

        self.engine = engine_api.Engine(self.bridge.analysis_done.emit, self.bridge.analysis_failed.emit)
        self.engine.depth = self.cfg.get("depth", 18)
        self.engine.num_lines = self.cfg.get("lines", 3)
        self.recognizer: BoardRecognizer | None = None
        self.tracker = GameTracker()
        self.sct = MSS()
        self.overlay = BoardOverlay()
        self.selectors: list[RegionSelector] = []

        self.rect: tuple[int, int, int, int] | None = tuple(self.cfg["rect"]) if self.cfg.get("rect") else None
        self.white_bottom = True
        self.view: int | None = None      # None — следим за сайтом, иначе номер просматриваемого хода
        self.last_grid = None
        self.last_rec = None
        self.stable = 0
        self.lost = 0
        self.finding = False
        self.find_requested_by_user = False
        self.last_find = 0.0
        self.last_save = time.monotonic()
        self.api_error: str | None = None
        self._retry_pending = False

        self._build_ui()
        if self.rect:
            self.overlay.set_board(self.rect)
        self._set_status("Загружаю шаблоны фигур…", ui.MUTED)
        self._refresh()

        threading.Thread(target=self._load_bank, daemon=True).start()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(SCAN_INTERVAL_MS)

    # ================================================================ интерфейс
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # --- шапка
        header = QHBoxLayout()
        logo = QLabel("♞ <b>Chessium</b>")
        logo.setStyleSheet("font-size: 18px;")
        self.status = ui.StatusPill()
        find_btn = QPushButton("⌖")
        find_btn.setObjectName("Icon")
        find_btn.setToolTip("Найти доску на экране")
        find_btn.clicked.connect(self._find_clicked)
        menu_btn = QPushButton("⋮")
        menu_btn.setObjectName("Icon")
        menu_btn.setToolTip("Настройки")
        menu_btn.clicked.connect(lambda: self.menu.exec(menu_btn.mapToGlobal(menu_btn.rect().bottomLeft())))
        header.addWidget(logo)
        header.addSpacing(6)
        header.addWidget(self.status, 1)
        header.addWidget(find_btn)
        header.addWidget(menu_btn)
        root.addLayout(header)

        # --- доска и шкала
        board_row = QHBoxLayout()
        board_row.setSpacing(8)
        self.eval_bar = ui.EvalBar()
        self.board_view = ui.BoardView()
        board_row.addWidget(self.eval_bar)
        board_row.addWidget(self.board_view, 1)
        root.addLayout(board_row)

        # --- оценка хода
        self.feedback = ui.FeedbackCard()
        root.addWidget(self.feedback)

        # --- линии движка
        lines_card = card()
        lc = QVBoxLayout(lines_card)
        lc.setContentsMargins(12, 8, 12, 8)
        self.lines_view = ui.LinesView()
        lc.addWidget(self.lines_view)
        root.addWidget(lines_card)

        # --- вкладки: ходы / отчёт
        tabs_card = card()
        tc = QVBoxLayout(tabs_card)
        tc.setContentsMargins(12, 2, 12, 10)
        tc.setSpacing(8)
        tab_row = QHBoxLayout()
        tab_row.setSpacing(0)
        self.tab_group = QButtonGroup(self)
        for i, name in enumerate(("Ходы", "Отчёт")):
            b = QPushButton(name)
            b.setObjectName("Tab")
            b.setCheckable(True)
            b.setChecked(i == 0)
            self.tab_group.addButton(b, i)
            tab_row.addWidget(b)
        tc.addLayout(tab_row)

        self.stack = QStackedWidget()
        moves_page = QWidget()
        mp = QVBoxLayout(moves_page)
        mp.setContentsMargins(0, 0, 0, 0)
        mp.setSpacing(8)
        self.opening_label = QLabel("")
        self.opening_label.setStyleSheet(f"color: {ui.MUTED}; font-size: 12px;")
        mp.addWidget(self.opening_label)
        self.graph = ui.EvalGraph()
        self.graph.setFixedHeight(64)
        self.graph.selected.connect(self._select_node)
        self.move_list = ui.MoveList()
        self.move_list.selected.connect(self._select_node)
        self.move_scroll = QScrollArea()
        self.move_scroll.setWidgetResizable(True)
        self.move_scroll.setWidget(self.move_list)
        mp.addWidget(self.graph)
        mp.addWidget(self.move_scroll, 1)
        self.report_view = ui.ReportView()
        report_scroll = QScrollArea()
        report_scroll.setWidgetResizable(True)
        report_scroll.setWidget(self.report_view)
        self.stack.addWidget(moves_page)
        self.stack.addWidget(report_scroll)
        self.tab_group.idClicked.connect(self.stack.setCurrentIndex)
        tc.addWidget(self.stack, 1)
        root.addWidget(tabs_card, 1)

        # --- навигация
        nav = QHBoxLayout()
        nav.setSpacing(6)
        for text, tip, fn in (("«", "К началу (Home)", lambda: self._step(-10_000)), ("‹", "Назад (←)", lambda: self._step(-1)),
                              ("›", "Вперёд (→)", lambda: self._step(1)), ("»", "К последнему ходу (End)", lambda: self._step(10_000))):
            b = QPushButton(text)
            b.setObjectName("Nav")
            b.setToolTip(tip)
            b.clicked.connect(fn)
            nav.addWidget(b)
        nav.addStretch()
        self.live_btn = QPushButton("● С сайта")
        self.live_btn.setObjectName("Live")
        self.live_btn.setCheckable(True)
        self.live_btn.setChecked(True)
        self.live_btn.setToolTip("Показывать позицию, которая сейчас на сайте")
        self.live_btn.clicked.connect(self._go_live)
        nav.addWidget(self.live_btn)
        root.addLayout(nav)

        for key, fn in ((Qt.Key.Key_Left, lambda: self._step(-1)), (Qt.Key.Key_Right, lambda: self._step(1)),
                        (Qt.Key.Key_Home, lambda: self._step(-10_000)), (Qt.Key.Key_End, lambda: self._step(10_000))):
            QShortcut(QKeySequence(key), self, fn)

        self._build_menu()

    def _build_menu(self) -> None:
        m = self.menu = QMenu(self)

        def toggle(text: str, key: str, default: bool, fn=None) -> QAction:
            a = QAction(text, self, checkable=True)
            a.setChecked(self.cfg.get(key, default))
            a.toggled.connect(lambda v: (self.cfg.__setitem__(key, v), fn and fn(v), self._save_config()))
            m.addAction(a)
            return a

        def choice(title: str, key: str, options: list[tuple[str, object]], default, fn=None) -> None:
            sub = m.addMenu(title)
            group = QActionGroup(self)
            for label, value in options:
                a = QAction(label, self, checkable=True)
                a.setChecked(self.cfg.get(key, default) == value)
                a.triggered.connect(lambda _=False, v=value: (self.cfg.__setitem__(key, v), fn and fn(v), self._save_config()))
                group.addAction(a)
                sub.addAction(a)

        toggle("Следить за доской", "auto", True)
        toggle("Стрелка лучшего хода на сайте", "arrow", True, lambda _: self._refresh_overlay())
        toggle("Значки оценки ходов на сайте", "badges", True, lambda _: self._refresh_overlay())
        toggle("Показывать доску в окне", "show_board", True, lambda _: self._apply_layout())
        toggle("Поверх всех окон", "on_top", True, self._apply_on_top)
        m.addSeparator()
        choice("Линии движка", "lines", [("1", 1), ("2", 2), ("3", 3), ("4", 4), ("5", 5)], 3, self._set_lines)
        choice("Глубина", "depth", [(str(d), d) for d in (10, 12, 14, 16, 18)], 18, self._set_depth)
        choice("Снизу на сайте", "orientation", [("Определять автоматически", "auto"), ("Белые", "white"), ("Чёрные", "black")],
               "auto", lambda _: self._reset_scan())
        m.addSeparator()
        m.addAction("Сменить очередь хода", self._flip_turn)
        m.addAction("Новая партия (сбросить ходы)", self._new_game)
        m.addAction("Выделить доску вручную…", self._select_region)
        m.addAction("Сбросить обучение распознавания", self._forget)
        m.addSeparator()
        m.addAction("Открыть позицию в Lichess", self._open_lichess)
        m.addAction("Копировать FEN", lambda: QGuiApplication.clipboard().setText(self._shown_node().board.fen()
                                                                                 if self.tracker.nodes else ""))
        m.addAction("Копировать PGN", lambda: QGuiApplication.clipboard().setText(self.tracker.pgn()))
        m.addSeparator()
        m.addAction("Открыть папку данных", lambda: os.startfile(DATA_DIR))
        m.addAction(f"О программе {APP_NAME} {__version__}", lambda: webbrowser.open(REPO_URL))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_layout()

    def _apply_layout(self) -> None:
        show = self.cfg.get("show_board", True)
        self.board_view.setVisible(show)
        if show:
            side = max(200, min(self.width() - 28 - 34, int(self.height() * 0.37)))
            self.board_view.setFixedSize(side, side)
            self.eval_bar.setFixedHeight(side)
            self.eval_bar.setFixedWidth(26)
        else:
            self.eval_bar.setFixedHeight(34)
            self.eval_bar.setFixedWidth(self.width() - 28)
        self.eval_bar.horizontal = not show
        self.eval_bar.update()

    def showEvent(self, e):
        super().showEvent(e)
        exclude_from_capture(self)
        dark_title_bar(self)

    def closeEvent(self, e):
        self._save_config()
        if self.recognizer:
            self.recognizer.save()
        self.engine.stop()
        self.overlay.close()
        super().closeEvent(e)

    def _apply_on_top(self, on: bool) -> None:
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        self.show()

    # ================================================================ настройки
    def _load_config(self) -> dict:
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_config(self) -> None:
        try:
            self.cfg["rect"] = list(self.rect) if self.rect else None
            CONFIG_PATH.write_text(json.dumps(self.cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            log.exception("Не удалось сохранить настройки")

    def _set_lines(self, n: int) -> None:
        self.engine.num_lines = n
        self.engine.cache.clear()
        self._request_analysis()

    def _set_depth(self, d: int) -> None:
        self.engine.depth = d
        self.engine.cache.clear()
        self._request_analysis()

    def _set_status(self, text: str, color: str = ui.ACCENT) -> None:
        self.status.set_status(text, color)

    # ================================================================ загрузка шаблонов
    def _load_bank(self) -> None:
        try:
            try:
                self.tracker.openings = load_openings(APP_DIR / "openings.tsv", DATA_DIR / "openings.json")
            except Exception:
                log.exception("Не удалось загрузить дебюты")
            pieces_dir = APP_DIR / "pieces"  # наборы, вложенные в установщик
            if not pieces_dir.is_dir():
                pieces_dir = DATA_DIR / "pieces"
            if not (pieces_dir / ".complete").exists():
                self.bridge.progress.emit("Первый запуск: загружаю наборы фигур…")
                pieceset.download_piece_sets(pieces_dir)
                if sum(1 for d in pieces_dir.iterdir() if d.is_dir()) >= 11:
                    (pieces_dir / ".complete").touch()
            self.bridge.progress.emit("Готовлю распознавание фигур…")
            bank = pieceset.load_bank(pieces_dir, DATA_DIR)
            self.bridge.bank_ready.emit(bank)
        except Exception:
            log.exception("Не удалось загрузить шаблоны фигур")
            self.bridge.bank_ready.emit(None)

    def _on_bank_ready(self, bank) -> None:
        if bank is None:
            self._set_status(f"Ошибка загрузки шаблонов (см. {LOG_PATH})", "#fa412d")
            return
        self.recognizer = BoardRecognizer(bank, DATA_DIR / "themes")
        if self.rect is None:
            self._request_find(force=True)
        else:
            self._set_status("Слежу за доской")

    # ================================================================ поиск доски
    def _find_clicked(self) -> None:
        self.find_requested_by_user = True
        self._request_find(force=True)

    def _request_find(self, force: bool = False) -> None:
        if self.finding or (not force and time.monotonic() - self.last_find < FIND_COOLDOWN):
            return
        self.finding = True
        self.last_find = time.monotonic()
        self._set_status("Ищу доску на экране…", "#f7c631")

        def run():
            result = None
            try:
                with MSS() as sct:
                    result = find_board_on_screen(sct)
            except Exception:
                log.exception("Ошибка поиска доски")
            self.bridge.board_found.emit(result)

        threading.Thread(target=run, daemon=True).start()

    def _on_board_found(self, rect) -> None:
        self.finding = False
        self.last_find = time.monotonic()
        user = self.find_requested_by_user
        self.find_requested_by_user = False
        if rect is None:
            self._set_status("Доска не найдена — откройте партию на сайте", "#fa412d")
            if user:
                self.board_view.placeholder = "Доска не найдена.\nОткройте партию на chess.com / lichess\nили выделите доску вручную (меню ⋮)"
                self.board_view.update()
            return
        self._use_rect(tuple(rect))

    def _use_rect(self, rect: tuple[int, int, int, int]) -> None:
        self.rect = rect
        self.lost = 0
        self._reset_scan()
        self.overlay.set_board(rect)
        self._save_config()
        self._set_status("Доска найдена — слежу за партией")
        log.info("Доска: %s", rect)

    def _select_region(self) -> None:
        self.overlay.hide()
        self.selectors = []
        for screen in QGuiApplication.screens():
            sel = RegionSelector(screen)
            sel.selected.connect(self._on_region_selected)
            sel.show()
            sel.activateWindow()
            self.selectors.append(sel)

    def _on_region_selected(self, rect) -> None:
        for sel in self.selectors:
            sel.close()
        self.selectors = []
        if rect:
            self._use_rect(tuple(rect))
        self._refresh_overlay()

    # ================================================================ слежение за доской
    def _reset_scan(self) -> None:
        self.last_grid = None
        self.stable = 0

    def _grab(self) -> np.ndarray:
        x, y, w, h = self.rect
        shot = self.sct.grab({"left": x, "top": y, "width": w, "height": h})
        return np.ascontiguousarray(np.asarray(shot)[:, :, 2::-1])

    def _tick(self) -> None:
        try:
            self._scan()
        except Exception:
            log.exception("Ошибка при сканировании")
            self._reset_scan()

    def _scan(self) -> None:
        if self.recognizer is None or not self.cfg.get("auto", True) or self.selectors:
            return
        if self.rect is None:
            self._request_find()
            return
        try:
            img = self._grab()
            rec = self.recognizer.recognize(img)
        except Exception:
            log.exception("Не удалось снять доску")
            rec = None
        if rec is None:
            self.lost += 1
            if self.lost >= LOST_TICKS:
                self._set_status("Доска пропала — ищу…", "#f7c631")
                self._request_find()
            return
        if self.lost >= LOST_TICKS:
            self._set_status("Слежу за доской")
        self.lost = 0

        if rec.grid == self.last_grid:
            self.stable += 1
        else:
            self.last_grid, self.last_rec, self.stable = rec.grid, rec, 1
        if self.stable not in (STABLE_TICKS, NEW_GAME_TICKS):
            return

        white_bottom, placement = self._orientation(rec.grid)
        self.white_bottom = white_bottom
        if rec.last_mover:
            turn = chess.BLACK if rec.last_mover == "w" else chess.WHITE
        else:
            turn = chess.WHITE if white_bottom else chess.BLACK
        event = self.tracker.feed(placement, turn, allow_new=self.stable >= NEW_GAME_TICKS)

        if event == "same":
            return
        if event == "pending":
            self._set_status("Распознаю позицию…", "#f7c631")
            return
        if event == "invalid":
            self._set_status("Позиция не распознана — проверьте доску", "#fa412d")
            return
        if event == "move" or (event == "new" and placement == START_PLACEMENT):
            # Позиция подтверждена правилами — запоминаем, как выглядят клетки
            self.recognizer.learn(rec, placement_to_grid(placement, white_bottom))
            if time.monotonic() - self.last_save > 15:
                self.recognizer.save()
                self.last_save = time.monotonic()
        if event == "new":
            self.engine.clear_queue()
            self.view = None
        self._set_status("Слежу за партией")
        self._request_analysis()
        self._refresh()

    def _orientation(self, grid: list[list[str]]) -> tuple[bool, str]:
        mode = self.cfg.get("orientation", "auto")
        if mode != "auto":
            wb = mode == "white"
            return wb, grid_to_placement(grid, wb)
        a = grid_to_placement(grid, self.white_bottom)
        if a == START_PLACEMENT or self.tracker.match(a):
            return self.white_bottom, a
        b = grid_to_placement(grid, not self.white_bottom)
        if b == START_PLACEMENT or self.tracker.match(b):
            return not self.white_bottom, b
        guess, confidence = guess_white_bottom(grid)
        wb = guess if confidence > 1.0 else self.white_bottom
        return wb, grid_to_placement(grid, wb)

    # ================================================================ анализ
    def _request_analysis(self) -> None:
        nodes = self.tracker.nodes
        if not nodes:
            return
        self.engine.request(nodes[self.tracker.cur].board.fen(), urgent=True)
        if self.view is not None and self.view < len(nodes):
            self.engine.request(nodes[self.view].board.fen(), urgent=True)
        for fen in self.tracker.missing_analysis():
            self.engine.request(fen)

    def _on_analysis(self, analysis: Analysis) -> None:
        if self.api_error:
            self.api_error = None
            self._set_status("Слежу за партией")
        if self.tracker.set_analysis(analysis):
            self._refresh()

    def _on_analysis_error(self, fen: str, msg: str, rejected: bool) -> None:
        log.warning("API (%s): %s (%s)", "отказ" if rejected else "сеть", msg, fen)
        self.api_error = msg
        if rejected:
            # Эту позицию API считать не стал — остальные анализируются дальше
            self._set_status("chess-api.com не принял эту позицию", "#f7c631")
            return
        self._set_status("Нет связи с chess-api.com — повторяю…", "#fa412d")
        if not self._retry_pending:
            self._retry_pending = True
            QTimer.singleShot(3000, self._retry_analysis)

    def _retry_analysis(self) -> None:
        self._retry_pending = False
        self._request_analysis()

    # ================================================================ навигация
    def _shown_index(self) -> int:
        if self.view is not None and self.view < len(self.tracker.nodes):
            return self.view
        return self.tracker.cur

    def _shown_node(self):
        return self.tracker.nodes[self._shown_index()]

    def _select_node(self, index: int) -> None:
        if not self.tracker.nodes:
            return
        index = max(0, min(len(self.tracker.nodes) - 1, index))
        self.view = None if index == self.tracker.cur else index
        self._request_analysis()
        self._refresh()

    def _step(self, delta: int) -> None:
        if self.tracker.nodes:
            self._select_node(self._shown_index() + delta)

    def _go_live(self) -> None:
        self.view = None
        self._refresh()

    def _flip_turn(self) -> None:
        if self.tracker.nodes:
            self.tracker.flip_turn()
            self.view = None
            self._request_analysis()
            self._refresh()

    def _new_game(self) -> None:
        self.tracker.nodes.clear()
        self.tracker.cur = 0
        self.view = None
        self._reset_scan()
        self._refresh()

    def _forget(self) -> None:
        if self.recognizer:
            self.recognizer.forget()
        self._reset_scan()

    def _open_lichess(self) -> None:
        if self.tracker.nodes:
            webbrowser.open("https://lichess.org/analysis/" + self._shown_node().board.fen().replace(" ", "_"))

    # ================================================================ отрисовка
    def _refresh(self) -> None:
        nodes = self.tracker.nodes
        self.live_btn.setChecked(self.view is None)
        if not nodes:
            self.board_view.set_state(chess.Board(), self.white_bottom, None, [], None)
            self.board_view.placeholder = "Откройте партию на сайте —\nпозиция появится здесь"
            self.eval_bar.set_value(50, "", self.white_bottom)
            self.lines_view.set_analysis(None, False)
            self.feedback.set_node(None, None)
            self.graph.set_data([], -1)
            self.opening_label.hide()
            self.move_list.set_nodes([], 0)
            self.report_view.set_report(self.tracker.report())
            self._refresh_overlay()
            return

        idx = self._shown_index()
        node = nodes[idx]
        prev = nodes[idx - 1] if idx > 0 else None
        arrows = []
        if node.analysis and node.analysis.best:
            mv = chess.Move.from_uci(node.analysis.best.move)
            arrows.append((mv.from_square, mv.to_square, "green"))
        badge = (node.move.to_square, node.cls) if node.move and node.cls else None
        self.board_view.set_state(node.board, self.white_bottom, node.move, arrows, badge)

        if node.analysis:
            self.eval_bar.set_value(node.analysis.win, node.analysis.score_text(), self.white_bottom)
        self.lines_view.set_analysis(node.analysis, pending=True)
        self.feedback.set_node(node if node.move else None, prev)
        opening = self.tracker.opening_name(idx)
        self.opening_label.setText(f"📖  {opening}" if opening else "")
        self.opening_label.setVisible(bool(opening))
        self.graph.set_data(nodes, idx)
        self.move_list.set_nodes(nodes, idx)
        row_y = self.move_list.row_of(idx) * ui.MoveList.ROW
        # после смены высоты списка — когда раскладка обновится
        QTimer.singleShot(30, lambda: self._scroll_moves(row_y))
        self.report_view.set_report(self.tracker.report())
        self._refresh_overlay()

    def _scroll_moves(self, row_y: int) -> None:
        bar = self.move_scroll.verticalScrollBar()
        view_h = self.move_scroll.viewport().height()
        if not bar.value() <= row_y <= bar.value() + view_h - ui.MoveList.ROW:
            bar.setValue(max(0, row_y - view_h // 2))

    def _refresh_overlay(self) -> None:
        nodes = self.tracker.nodes
        if not nodes or self.rect is None or self.selectors:
            self.overlay.set_marks(None, None, self.white_bottom)
            return
        node = nodes[self.tracker.cur]
        arrow = badge = None
        if self.cfg.get("arrow", True) and node.analysis and node.analysis.best:
            mv = chess.Move.from_uci(node.analysis.best.move)
            arrow = (mv.from_square, mv.to_square)
        if self.cfg.get("badges", True) and node.move and node.cls:
            badge = (node.move.to_square, node.cls)
        self.overlay.set_marks(arrow, badge, self.white_bottom)


def main() -> None:
    setup_logging()
    log.info("Запуск %s %s", APP_NAME, __version__)
    if sys.platform == "win32":
        # Только одна копия: две программы рисовали бы стрелки друг поверх друга.
        # Этот же мьютекс установщик использует, чтобы попросить закрыть программу.
        ctypes.windll.kernel32.CreateMutexW(None, False, f"{APP_NAME}AppMutex")
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            ctypes.windll.user32.MessageBoxW(None, f"{APP_NAME} уже запущен.", APP_NAME, 0x40)
            return
        try:  # своя иконка на панели задач, а не иконка python
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_NAME}.App")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    app.setStyleSheet(ui.STYLE)
    w = MainWindow()
    if w.cfg.get("on_top", True):
        w.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    screen = QGuiApplication.primaryScreen().availableGeometry()
    w.resize(440, min(940, screen.height() - 40))
    w.move(screen.right() - w.width() - 12, screen.top() + 8)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
