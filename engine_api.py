"""Анализ позиций через https://chess-api.com (Stockfish в облаке).

Основной канал — WebSocket (несколько линий за раз), запасной — обычный POST.
Запросы выполняются в отдельном потоке по очереди: срочные (текущая позиция) — первыми.
"""
from __future__ import annotations

import json
import math
import threading
import time
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import chess

API_URL = "https://chess-api.com/v1"
WS_URL = "wss://chess-api.com/v1"


def win_percent(cp: float | None, mate: int | None) -> float:
    """Шансы белых 0..100 (формула lichess)."""
    if mate is not None:
        return 100.0 if mate > 0 else 0.0
    cp = (cp or 0.0) * 100
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


@dataclass
class Line:
    move: str                     # UCI
    cp: float | None              # оценка в пешках с точки зрения белых
    mate: int | None              # >0 — мат ставят белые
    pv: list[str] = field(default_factory=list)  # продолжение, включая move

    @property
    def win(self) -> float:
        return win_percent(self.cp, self.mate)

    def score_text(self) -> str:
        if self.mate is not None:
            return f"{'' if self.mate > 0 else '-'}M{abs(self.mate)}"
        return f"{self.cp:+.2f}"


@dataclass
class Analysis:
    fen: str
    lines: list[Line]
    depth: int = 0
    result: str | None = None     # для законченной партии: '1-0', '0-1', '1/2-1/2'

    @property
    def best(self) -> Line | None:
        return self.lines[0] if self.lines else None

    @property
    def win(self) -> float:
        if self.result:
            return {"1-0": 100.0, "0-1": 0.0}.get(self.result, 50.0)
        return self.best.win if self.best else 50.0

    def score_text(self) -> str:
        if self.result:
            return {"1-0": "1-0", "0-1": "0-1"}.get(self.result, "½-½")
        return self.best.score_text() if self.best else "—"


def position_key(fen: str) -> str:
    return " ".join(fen.split()[:4])


def terminal_analysis(board: chess.Board) -> Analysis | None:
    if board.is_checkmate():
        return Analysis(board.fen(), [], result="0-1" if board.turn == chess.WHITE else "1-0")
    if board.is_stalemate() or board.is_insufficient_material():
        return Analysis(board.fen(), [], result="1/2-1/2")
    return None


class ApiRejected(Exception):
    """API ответил, но отказался считать позицию (повторять бессмысленно)."""


def _line_from_msg(m: dict) -> Line:
    mate = m.get("mate")
    cp = m.get("eval")
    return Line(move=m["move"], cp=float(cp) if cp is not None else 0.0,
                mate=int(mate) if mate is not None else None,
                pv=[m["move"]] + list(m.get("continuationArr") or []))


class Engine:
    def __init__(self, on_done: Callable[[Analysis], None], on_error: Callable[[str, str, bool], None]) -> None:
        self.on_done = on_done
        self.on_error = on_error
        self.depth = 18
        self.num_lines = 3
        self.cache: dict[str, Analysis] = {}
        self.rejected: set[str] = set()
        self._queue: deque[str] = deque()
        self._cv = threading.Condition()
        self._ws = None
        self._ws_failures = 0
        self._stop = False
        threading.Thread(target=self._loop, daemon=True, name="engine").start()

    def request(self, fen: str, urgent: bool = False) -> None:
        key = position_key(fen)
        if key in self.cache:
            self.on_done(self.cache[key])
            return
        if key in self.rejected:
            return
        with self._cv:
            if fen in self._queue:
                if not urgent:
                    return
                self._queue.remove(fen)
            if urgent:
                self._queue.appendleft(fen)
            else:
                self._queue.append(fen)
            self._cv.notify()

    def clear_queue(self) -> None:
        with self._cv:
            self._queue.clear()

    def stop(self) -> None:
        self._stop = True
        with self._cv:
            self._cv.notify()

    # ---------- рабочий поток ----------
    def _loop(self) -> None:
        while not self._stop:
            with self._cv:
                while not self._queue and not self._stop:
                    self._cv.wait()
                if self._stop:
                    return
                fen = self._queue.popleft()
            key = position_key(fen)
            if key in self.cache:
                self.on_done(self.cache[key])
                continue
            try:
                analysis = self._analyze(fen)
                self.cache[key] = analysis
                self.on_done(analysis)
            except ApiRejected as e:
                self.rejected.add(key)
                self.on_error(fen, str(e), True)
            except Exception as e:  # сеть, лимиты — сообщаем и идём дальше
                self.on_error(fen, str(e) or e.__class__.__name__, False)
                time.sleep(1.5)

    def _analyze(self, fen: str) -> Analysis:
        board = chess.Board(fen)
        done = terminal_analysis(board)
        if done:
            return done
        # chess-api.com отвергает любой FEN с полем взятия на проходе, поэтому отправляем
        # позицию без него, а взятие на проходе (если оно есть) оцениваем отдельно.
        plain = board.copy(stack=False)
        plain.ep_square = None
        try:
            analysis = self._query(plain)
        except ApiRejected:
            if not plain.castling_rights:
                raise
            plain.castling_rights = 0  # права на рокировку могли быть угаданы неверно
            analysis = self._query(plain)
        analysis.fen = fen

        ep_lines = []
        for move in board.legal_moves:
            if board.is_en_passant(move):
                after = board.copy(stack=False)
                after.push(move)
                sub = terminal_analysis(after) or self._query(after)
                if sub.result:
                    ep_lines.append(Line(move.uci(), None, 1 if sub.result == "1-0" else -1, [move.uci()]))
                elif sub.best:
                    ep_lines.append(Line(move.uci(), sub.best.cp, sub.best.mate, [move.uci()] + sub.best.pv))
        if ep_lines:
            sign = 1 if board.turn == chess.WHITE else -1
            lines = analysis.lines + ep_lines
            lines.sort(key=lambda l: -sign * (l.win - 50))
            analysis.lines = lines[:self.num_lines]
        return analysis

    def _query(self, board: chess.Board) -> Analysis:
        fen = board.fen()
        if self._ws_failures < 3:
            try:
                result = self._analyze_ws(fen, board)
                self._ws_failures = 0
                return result
            except ApiRejected:
                raise
            except Exception:
                self._ws_failures += 1
                self._close_ws()
        return self._analyze_post(fen)

    def _close_ws(self) -> None:
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass
        self._ws = None

    def _analyze_ws(self, fen: str, board: chess.Board) -> Analysis:
        import websocket  # websocket-client

        if self._ws is None:
            self._ws = websocket.create_connection(WS_URL, timeout=15)
        self._ws.send(json.dumps({"fen": fen, "variants": self.num_lines, "depth": self.depth,
                                  "maxThinkingTime": 100}))
        by_move: dict[str, dict] = {}
        best_msg = None
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            msg = json.loads(self._ws.recv())
            kind = msg.get("type")
            if kind == "move" and msg.get("move"):
                by_move[msg["move"]] = msg
            elif kind == "bestmove":
                best_msg = msg
                break
            elif kind == "error":
                raise ApiRejected(msg.get("text") or msg.get("error") or "ошибка API")
        if best_msg is None or not best_msg.get("move"):
            raise TimeoutError("нет ответа от движка")

        max_depth = max((m.get("depth") or 0 for m in by_move.values()), default=0)
        sign = 1 if board.turn == chess.WHITE else -1
        others = [_line_from_msg(m) for mv, m in by_move.items()
                  if mv != best_msg["move"] and (m.get("depth") or 0) >= max_depth - 2]
        others.sort(key=lambda l: -sign * (l.win - 50))
        best = _line_from_msg(best_msg)
        if len(best.pv) < 2 and best.move in by_move:  # у bestmove бывает пустое продолжение
            best.pv = _line_from_msg(by_move[best.move]).pv
        lines = [best] + others[:max(0, self.num_lines - 1)]
        legal = {m.uci() for m in board.legal_moves}
        lines = [l for l in lines if l.move in legal]
        return Analysis(fen, lines, int(best_msg.get("depth") or max_depth))

    def _analyze_post(self, fen: str) -> Analysis:
        body = json.dumps({"fen": fen, "depth": self.depth, "maxThinkingTime": 100}).encode()
        req = urllib.request.Request(API_URL, data=body, method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "Chessium/2.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not data.get("move"):
            raise ApiRejected(data.get("text") or "API не вернул ход")
        return Analysis(fen, [_line_from_msg(data)], int(data.get("depth") or 0))


def line_to_san(fen: str, moves: list[str], limit: int = 10) -> str:
    """UCI-ходы -> «12. Nf3 Nc6 13. ...»."""
    board = chess.Board(fen)
    parts = []
    for i, uci in enumerate(moves[:limit]):
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            break
        if move not in board.legal_moves:
            break
        num = board.fullmove_number
        if board.turn == chess.WHITE:
            parts.append(f"{num}.")
        elif i == 0:
            parts.append(f"{num}...")
        parts.append(board.san(move))
        board.push(move)
    return " ".join(parts)
