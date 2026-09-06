# -*- coding: utf-8 -*-
"""生成应用图标 icon.ico（多尺寸，供 PyInstaller --icon 使用）。

依赖：PyQt6, Pillow
用法：python make_icon.py
"""
import os
import io
from PyQt6.QtCore import Qt, QBuffer
from PyQt6.QtGui import QPixmap, QPainter, QColor, QBrush, QPen
from PyQt6.QtWidgets import QApplication
from PIL import Image

app = QApplication([])


def make_icon(size):
    """绘制紫色圆角底 + 白色转发箭头图标。"""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    radius = max(2, int(size * 0.22))
    p.setBrush(QBrush(QColor("#7c6cff")))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(0, 0, size, size, radius, radius)
    lw = max(2, int(size * 0.10))
    pen = QPen(QColor("#ffffff"))
    pen.setWidth(lw)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    m = int(size * 0.26)
    p.drawLine(m, size - m, size - m, size // 2)
    p.drawLine(size - m, size // 2, m, m)
    ah = int(size * 0.16)
    p.drawLine(m, m, m + ah, m - ah // 2)
    p.drawLine(m, m, m + ah // 2, m + ah)
    p.end()
    return pix


sizes = (16, 24, 32, 48, 64, 128, 256)
pil_imgs = []
for s in sizes:
    pm = make_icon(s)
    buf = QBuffer()
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    pm.save(buf, "PNG")
    buf.close()
    pil_img = Image.open(io.BytesIO(bytes(buf.data())))
    pil_imgs.append(pil_img)

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
pil_imgs[0].save(out, format="ICO", sizes=[(s, s) for s in sizes])
print(f"已生成 {out}  尺寸: {sizes}")
