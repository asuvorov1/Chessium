"""Отслеживание партии по снимкам доски и оценка ходов (как «Обзор партии» chess.com)."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import chess
import chess.pgn

from engine_api import Analysis, position_key

START_PLACEMENT = chess.STARTING_BOARD_FEN

# ключ: (название, значок, цвет)
CLASSES = {
    "brilliant": ("Блестящий", "!!", "#26c2a3"),
    "great": ("Отличный", "!", "#749bbf"),
    "best": ("Лучший", "★", "#81b64c"),
    "excellent": ("Превосходный", "✓", "#96bc4b"),
    "good": ("Хороший", "✓", "#77915f"),
    "book": ("Теория", "≡", "#a88865"),
    "inaccuracy": ("Неточность", "?!", "#f7c631"),
    "mistake": ("Ошибка", "?", "#ffa459"),
    "miss": ("Промах", "✗", "#ff7769"),
    "blunder": ("Зевок", "??", "#fa412d"),
}
CLASS_ORDER = list(CLASSES)

PIECE_VALUE = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}


@dataclass
class Node:
    board: chess.Board
    move: chess.Move | None = None
    san: str = ""
    analysis: Analysis | None = None
    cls: str | None = None
    loss: float = 0.0          # потеря шансов на победу (в процентных пунктах)
    accuracy: float | None = None

    @property
    def key(self) -> str:
        return position_key(self.board.fen())

    @property
    def mover(self) -> chess.Color:
        """Цвет того, кто сделал ход, приведший в эту позицию."""
        return not self.board.turn


def make_root(placement: str, turn: chess.Color) -> chess.Board | None:
    """Позиция без истории: пробуем указанную очередь хода, потом другую."""
    if placement == START_PLACEMENT:
        return chess.Board()
    for t in (turn, not turn):
        try:
            board = chess.Board(f"{placement} {'w' if t else 'b'} - - 0 1")
        except ValueError:
            return None
        rights = 0
        for color, rank in ((chess.WHITE, 0), (chess.BLACK, 7)):
            if board.piece_at(chess.square(4, rank)) == chess.Piece(chess.KING, color):
                for f in (0, 7):
                    sq = chess.square(f, rank)
                    if board.piece_at(sq) == chess.Piece(chess.ROOK, color):
                        rights |= chess.BB_SQUARES[sq]
        board.castling_rights = rights
        if board.is_valid():
            return board
    return None


def _is_sacrifice(board: chess.Board, move: chess.Move) -> bool:
    """Ход оставляет фигуру (не пешку) под выгодным для соперника боем."""
    piece = board.piece_at(move.from_square)
    if piece is None or piece.piece_type in (chess.PAWN, chess.KING):
        return False
    captured = board.piece_at(move.to_square)
    gained = PIECE_VALUE[captured.piece_type] if captured else 0
    after = board.copy(stack=False)
    after.push(move)
    value = PIECE_VALUE[piece.piece_type if not move.promotion else move.promotion]
    attackers = after.attackers(after.turn, move.to_square)
    if not attackers:
        return False
    cheapest = min(PIECE_VALUE[after.piece_type_at(sq)] for sq in attackers)
    defended = bool(after.attackers(not after.turn, move.to_square))
    lost = value if not defended else max(0, value - cheapest)
    return lost - gained >= 2


def opening_key(board: chess.Board) -> str:
    return f"{board.board_fen()} {'w' if board.turn else 'b'}"


def load_openings(tsv: Path, cache: Path) -> dict[str, str]:
    """База дебютов lichess (CC0): позиция -> название."""
    try:
        if cache.exists() and cache.stat().st_mtime >= tsv.stat().st_mtime:
            return json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    book: dict[str, str] = {}
    for line in tsv.read_text(encoding="utf-8").splitlines():
        parts = line.split("	")
        if len(parts) != 3:
            continue
        _, name, pgn = parts
        board = chess.Board()
        try:
            for tok in pgn.split():
                if not tok[0].isdigit():
                    board.push_san(tok)
        except ValueError:
            continue
        book[opening_key(board)] = name
    try:
        cache.write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return book


def move_accuracy(loss: float) -> float:
    return max(0.0, min(100.0, 103.1668 * math.exp(-0.04354 * loss) - 3.1669))


class GameTracker:
    def __init__(self) -> None:
        self.nodes: list[Node] = []
        self.cur = 0          # какую позицию сейчас показывает сайт
        self.openings: dict[str, str] = {}

    @property
    def standard_start(self) -> bool:
        return bool(self.nodes) and self.nodes[0].board.board_fen() == START_PLACEMENT \
            and self.nodes[0].board.turn == chess.WHITE

    def reset(self, board: chess.Board) -> None:
        self.nodes = [Node(board)]
        self.cur = 0

    # ---------- сопоставление снимков ----------
    def _one_move(self, board: chess.Board, placement: str) -> chess.Move | None:
        for move in board.legal_moves:
            board.push(move)
            hit = board.board_fen() == placement
            board.pop()
            if hit:
                return move
        return None

    def _two_moves(self, board: chess.Board, placement: str) -> tuple[chess.Move, chess.Move] | None:
        for m1 in board.legal_moves:
            board.push(m1)
            m2 = self._one_move(board, placement)
            board.pop()
            if m2:
                return m1, m2
        return None

    def match(self, placement: str) -> str | None:
        """Можно ли объяснить снимок без новой партии: 'same' / 'move' / 'nav' / None."""
        if not self.nodes:
            return None
        board = self.nodes[self.cur].board
        if board.board_fen() == placement:
            return "same"
        if self._one_move(board, placement):
            return "move"
        if any(n.board.board_fen() == placement for n in self.nodes):
            return "nav"
        if self._two_moves(board, placement):
            return "move"
        return None

    def feed(self, placement: str, turn_hint: chess.Color, allow_new: bool) -> str:
        """Обновить партию по снимку. Возвращает 'same'/'move'/'nav'/'new'/'pending'/'invalid'."""
        if self.nodes:
            board = self.nodes[self.cur].board
            if board.board_fen() == placement:
                return "same"
            move = self._one_move(board, placement)
            if move:
                nxt = self.cur + 1
                if nxt < len(self.nodes) and self.nodes[nxt].move == move:
                    self.cur = nxt
                    return "nav"
                self._append([move])
                return "move"
            same = [i for i, n in enumerate(self.nodes) if n.board.board_fen() == placement]
            if same:
                self.cur = min(same, key=lambda i: abs(i - self.cur))
                return "nav"
            pair = self._two_moves(board, placement)
            if pair:
                self._append(list(pair))
                return "move"
        if placement == START_PLACEMENT:
            self.reset(chess.Board())
            return "new"
        if not allow_new:
            return "pending"
        root = make_root(placement, turn_hint)
        if root is None:
            return "invalid"
        self.reset(root)
        return "new"

    def _append(self, moves: list[chess.Move]) -> None:
        del self.nodes[self.cur + 1:]
        for move in moves:
            board = self.nodes[-1].board
            san = board.san(move)
            nb = board.copy()
            nb.push(move)
            self.nodes.append(Node(nb, move, san))
        self.cur = len(self.nodes) - 1

    def flip_turn(self) -> None:
        """Сменить очередь хода в текущей позиции (начинает новую историю с неё)."""
        board = self.nodes[self.cur].board.copy(stack=False)
        board.turn = not board.turn
        board.ep_square = None
        if board.is_valid():
            self.reset(board)

    # ---------- анализ ----------
    def set_analysis(self, analysis: Analysis) -> bool:
        key = position_key(analysis.fen)
        hit = False
        for n in self.nodes:
            if n.key == key:
                n.analysis = analysis
                hit = True
        if hit:
            self.classify()
        return hit

    def missing_analysis(self) -> list[str]:
        return [n.board.fen() for n in self.nodes if n.analysis is None]

    def classify(self) -> None:
        for i in range(1, len(self.nodes)):
            prev, node = self.nodes[i - 1], self.nodes[i]
            if node.move is None or prev.analysis is None or node.analysis is None or not prev.analysis.best:
                node.cls, node.accuracy = None, None
                continue
            node.cls, node.loss = self._classify(i, prev, node)
            node.accuracy = move_accuracy(node.loss)

    def _classify(self, i: int, prev: Node, node: Node) -> tuple[str, float]:
        white = node.mover == chess.WHITE
        pov = (lambda w: w) if white else (lambda w: 100 - w)
        before = pov(prev.analysis.win)
        after = pov(node.analysis.win)
        loss = max(0.0, before - after)
        lines = prev.analysis.lines
        is_best = node.move.uci() == lines[0].move

        if self.standard_start and opening_key(node.board) in self.openings:
            return "book", loss
        if is_best or loss < 0.5:
            if after >= 50 and before < 95 and _is_sacrifice(prev.board, node.move):
                return "brilliant", 0.0
            if is_best and len(lines) > 1 and pov(lines[0].win) - pov(lines[1].win) >= 20 and before < 95:
                return "great", 0.0
            return "best", 0.0 if is_best else loss
        if loss < 2:
            return "excellent", loss
        if loss < 5:
            return "good", loss
        if loss < 10:
            return "inaccuracy", loss
        prev_cls = prev.cls if i >= 2 else None
        if prev_cls in ("mistake", "blunder") and loss < 30:
            return "miss", loss
        if loss < 20:
            return "mistake", loss
        return "blunder", loss

    def opening_name(self, index: int) -> str:
        if not self.standard_start:
            return ""
        for n in reversed(self.nodes[:index + 1]):
            name = self.openings.get(opening_key(n.board))
            if name:
                return name
        return ""

    # ---------- итоги ----------
    def report(self) -> dict:
        out = {}
        for color in (chess.WHITE, chess.BLACK):
            moves = [n for n in self.nodes[1:] if n.mover == color and n.cls]
            counts = {c: sum(1 for n in moves if n.cls == c) for c in CLASS_ORDER}
            acc = sum(n.accuracy for n in moves) / len(moves) if moves else None
            out[color] = {"accuracy": acc, "counts": counts, "moves": len(moves)}
        return out

    def pgn(self) -> str:
        if not self.nodes:
            return ""
        game = chess.pgn.Game()
        game.headers["Event"] = "Chessium"
        if not self.standard_start:
            game.setup(self.nodes[0].board)
        node = game
        for n in self.nodes[1:]:
            node = node.add_variation(n.move)
            if n.cls:
                node.comment = CLASSES[n.cls][0]
        return str(game)
