from typing import Literal

from PyQt6.QtGui import QColor

type ColorsLiteral = Literal[
    "BLACK", "WHITE", "GRAY", "YELLOW", "GREEN", "BLUE", "ORANGE"
]
COLORS_RGBA: dict[ColorsLiteral, QColor] = {
    "BLACK": QColor(0, 0, 0, 180),
    "WHITE": QColor(255, 255, 255, 200),
    "GRAY": QColor(68, 68, 68, 200),
    "YELLOW": QColor(92, 91, 26, 200),
    "GREEN": QColor(51, 94, 22, 200),
    "BLUE": QColor(17, 27, 54, 200),
    "ORANGE": QColor(69, 19, 19, 200),
}

COLORS_SOLID: dict[ColorsLiteral, QColor] = {
    "BLACK": QColor(0, 0, 0, 220),
    "WHITE": QColor(255, 255, 255, 255),
    "GRAY": QColor(68, 68, 68, 255),
    "YELLOW": QColor(92, 91, 26, 255),
    "GREEN": QColor(51, 94, 22, 255),
    "BLUE": QColor(17, 27, 54, 255),
    "ORANGE": QColor(69, 19, 19, 255),
}
