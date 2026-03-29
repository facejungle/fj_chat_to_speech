from typing import Literal

from PyQt6.QtGui import QColor

type ColorsLiteral = Literal[
    "BLACK", "WHITE", "GRAY", "YELLOW", "GREEN", "BLUE", "ORANGE"
]
COLORS_RGBA: dict[ColorsLiteral, QColor] = {
    "BLACK": QColor(0, 0, 0, 200),
    "WHITE": QColor(255, 255, 255, 220),
    "GRAY": QColor(68, 68, 68, 220),
    "YELLOW": QColor(92, 91, 26, 220),
    "GREEN": QColor(51, 94, 22, 220),
    "BLUE": QColor(17, 27, 54, 220),
    "ORANGE": QColor(69, 19, 19, 220),
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
