"""Deterministic vector diagrams; all numerical plots are labeled illustrations."""
from __future__ import annotations

import math
import re

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen

INK = "#e7eff8"
MUTED = "#92a6bd"
CYAN = "#56dec5"
AMBER = "#f2b460"
BLUE = "#77a9f6"


class FrameArtist:
    def __init__(self, width, height, title, count):
        self.width, self.height, self.title, self.count = width, height, title, count
        self.scale = width / 1440

    def text(self, rect, text, size=30, color=INK, bold=False, center=False):
        self.p.setPen(QColor(color))
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(round(size * self.scale))
        font.setBold(bold)
        self.p.setFont(font)
        flags = Qt.TextFlag.TextWordWrap | (Qt.AlignmentFlag.AlignCenter if center else Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.p.drawText(QRectF(*rect), int(flags), text)

    def line(self, x1, y1, x2, y2, color=MUTED, width=3):
        self.p.setPen(QPen(QColor(color), width * self.scale))
        self.p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    def box(self, rect, fill="#111d2d", border="#24374d", radius=18):
        self.p.setPen(QPen(QColor(border), self.scale))
        self.p.setBrush(QColor(fill))
        self.p.drawRoundedRect(QRectF(*rect), radius * self.scale, radius * self.scale)

    def dot(self, x, y, r=6, color=CYAN):
        self.p.setPen(Qt.PenStyle.NoPen)
        self.p.setBrush(QColor(color))
        self.p.drawEllipse(QPointF(x, y), r * self.scale, r * self.scale)

    def curve(self, points, color, width=4):
        path = QPainterPath(QPointF(*points[0]))
        for point in points[1:]:
            path.lineTo(QPointF(*point))
        self.p.setPen(QPen(QColor(color), width * self.scale))
        self.p.setBrush(Qt.BrushStyle.NoBrush)
        self.p.drawPath(path)

    def rc(self, x, y, w, h, t):
        self.text((x+25*self.scale, y, w-50*self.scale, 64*self.scale), "同一总线电容下，上拉电阻越大，上升越慢", 28, bold=True)
        left, top = x+w*.12, y+h*.23
        pw, ph = w*.8, h*.58
        for fraction in (0, .3, .7, 1):
            yy = top + ph*(1-fraction)
            self.line(left, yy, left+pw, yy, "#28394e", 1)
            self.text((x+8*self.scale, yy-18*self.scale, w*.1, 36*self.scale), f"{fraction:g} Vcc", 20, MUTED)
        self.line(left, top+ph, left+pw, top+ph)
        phase = (t % 4) / 4
        for tau, color, caption in ((.12, CYAN, "较小 Rp"), (.3, AMBER, "中等 Rp"), (.6, BLUE, "较大 Rp")):
            points = [(left+pw*j/160, top+ph*math.exp(-(j/160)/tau)) for j in range(161)]
            self.curve(points, color)
            self.dot(left+pw*phase, top+ph*math.exp(-phase/tau), 7, color)
        self.text((left, top+ph+15*self.scale, pw, 35*self.scale), "时间 →    电压曲线为原理示意，不是示波器实测", 21, MUTED)
        for i, (caption, color) in enumerate((("较小 Rp", CYAN), ("中等 Rp", AMBER), ("较大 Rp", BLUE))):
            self.text((left+i*pw/3, y+h*.88, pw/3, h*.1), caption, 24, color)

    def circuit(self, x, y, w, h, t):
        self.text((x+25*self.scale, y, w-50*self.scale, 64*self.scale), "开漏只负责拉低；释放后由上拉电阻充电", 28, bold=True)
        bus, supply, low = y+h*.5, y+h*.19, y+h*.82
        rx, cx, end = x+w*.35, x+w*.72, x+w*.9
        on = t % 6 < 2
        color = AMBER if on else CYAN
        self.line(rx, supply, rx, y+h*.27)
        self.text((rx-70*self.scale, supply-45*self.scale, 140*self.scale, 36*self.scale), "Vcc", 26, center=True)
        points = [(rx, y+h*.27)]
        for i in range(9):
            points.append((rx+(-1 if i%2 else 1)*14*self.scale, y+h*(.28+.019*i)))
        points.append((rx, bus))
        self.curve(points, AMBER, 3)
        self.text((rx+28*self.scale, y+h*.3, 120*self.scale, 50*self.scale), "Rp", 31, AMBER)
        self.line(x+w*.16, bus, end, bus, color, 4)
        self.dot(rx, bus, 7, color)
        self.text((end-140*self.scale, bus-55*self.scale, 160*self.scale, 45*self.scale), "SDA / SCL", 24, color)
        sx = x+w*.22
        self.line(sx, bus, sx, y+h*.59, color)
        self.line(sx, y+h*.59, sx+(0 if on else 40*self.scale), y+h*.71, color, 4)
        self.line(sx, y+h*.71, sx, low)
        self.text((x+w*.03, y+h*.65, w*.15, h*.2), "NMOS\n开漏输出", 24, MUTED)
        self.line(cx, bus, cx, y+h*.64, color)
        self.line(cx-32*self.scale, y+h*.64, cx+32*self.scale, y+h*.64, color, 4)
        self.line(cx-32*self.scale, y+h*.68, cx+32*self.scale, y+h*.68, color, 4)
        self.line(cx, y+h*.68, cx, low)
        self.text((cx+46*self.scale, y+h*.61, w*.18, h*.2), "Cb\n总线等效电容", 24, MUTED)
        for gx in (sx, cx):
            for i in range(3):
                self.line(gx-(24-i*7)*self.scale, low+i*9*self.scale, gx+(24-i*7)*self.scale, low+i*9*self.scale)
        if not on:
            phase = ((t-2) % 4) / 4
            self.dot(rx+(cx-rx)*phase, bus, 8, CYAN)
        self.text((x+20*self.scale, y+h*.9, w-40*self.scale, h*.09), "导通：总线被拉低" if on else "截止：通过 Rp 向 Cb 充电，总线缓慢上升", 26, color, center=True)

    def limits(self, x, y, w, h, t):
        self.text((x+25*self.scale, y, w-50*self.scale, 64*self.scale), "选电阻，是在灌电流与上升时间之间取平衡", 28, bold=True)
        gap = w*.045
        bw = (w-3*gap)/2
        bh = h*.46
        for i, (title, detail, formula, color) in enumerate((
                ("Rp 过小", "低电平灌电流增大", "Rp_min = (Vcc − VOL_max) / IOL", AMBER),
                ("Rp 过大", "上升时间变长", "Rp_max = tr_max / (0.8473 × Cb)", CYAN))):
            xx = x+gap+(bw+gap)*i
            self.box((xx, y+h*.25, bw, bh), border=color)
            self.text((xx+20*self.scale, y+h*.27, bw-40*self.scale, bh*.25), title, 38, color, True)
            self.text((xx+20*self.scale, y+h*.39, bw-40*self.scale, bh*.23), detail, 27)
            self.text((xx+20*self.scale, y+h*.53, bw-40*self.scale, bh*.28), formula, 23, MUTED)
        yy = y+h*.82
        self.line(x+gap, yy, x+w-gap, yy, "#32485f", 15)
        self.line(x+w*.35, yy, x+w*.65, yy, CYAN, 15)
        self.dot(x+w*(.35+.3*(.5+.5*math.sin(t))), yy, 10, AMBER)
        self.text((x+gap, yy+25*self.scale, w-2*gap, 40*self.scale), "边界取决于具体器件与总线电容；此图不代替器件手册", 21, MUTED, center=True)

    def sampling(self, x, y, w, h, t):
        self.text((x+25*self.scale, y, w-50*self.scale, 64*self.scale), "连续信号 → 离散采样", 30, bold=True)
        left, top, pw, ph = x+w*.08, y+h*.25, w*.84, h*.56
        for i in range(9):
            yy = top+ph*i/8
            self.line(left, yy, left+pw, yy, "#28394e", 1)
        self.curve([(left+pw*j/240, top+ph*(.5-.35*math.sin(j/240*math.tau*2))) for j in range(241)], CYAN)
        for i in range(25):
            xx = left+pw*i/24
            yy = top+ph*(.5-.35*math.sin(i/24*math.tau*2))
            self.line(xx, top+ph, xx, yy, "#61533d", 2)
            self.dot(xx, yy, 6 if i != int(t*6)%25 else 10, AMBER)
        self.text((left, y+h*.87, pw, h*.12), "连续信号（青）    采样点（橙）    原理示意，非实测数据", 23, MUTED, center=True)

    def flow(self, x, y, w, h, t, text):
        words = [word for word in ("DMA", "CPU", "RAM", "中断", "寄存器", "缓冲区", "任务", "输入", "输出") if word in text]
        words = (words[:4] if len(words) >= 2 else ["输入", "处理", "状态", "输出"])
        self.text((x+25*self.scale, y, w-50*self.scale, 64*self.scale), "机制与状态变化示意", 30, bold=True)
        n = len(words)
        bw = w*.72/n
        for i, word in enumerate(words):
            xx = x+w*.06+i*w*.9/n
            active = int(t*1.2)%n == i
            self.box((xx, y+h*.4, bw, h*.23), border=CYAN if active else "#34485f")
            self.text((xx, y+h*.4, bw, h*.23), word, 35, CYAN if active else INK, True, True)
            if i < n-1:
                self.text((xx+bw, y+h*.4, w*.18/n, h*.23), "→", 34, AMBER, center=True)

    def frame(self, scene, caption, local_time, overall, index, visual_image=None):
        w, h, s = self.width, self.height, self.scale
        image = QImage(w, h, QImage.Format.Format_RGBA8888)
        image.fill(QColor("#080f1a"))
        self.p = QPainter(image)
        self.p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for gx in range(0, w, max(1, int(80*s))):
            self.line(gx, 0, gx, h, "#101c2a", .7)
        self.text((w*.06, h*.035, w*.7, 42*s), "EMBEDDED / 技术讲解", 23, CYAN, True)
        self.text((w*.06, h*.09, w*.88, h*.105), self.title, 43, INK, True)
        heading = re.split(r"[，。！？；\n]", scene["narration"])[0][:28]
        self.text((w*.06, h*.19, w*.88, h*.06), heading, 28, AMBER)
        rect = (w*.06, h*.27, w*.88, h*.46)
        self.box(rect)
        content = scene.get("visual_prompt", "")+" "+scene["narration"]
        i2c = "I2C" in (content+self.title).upper() or "上拉" in content
        if visual_image is not None:
            name = scene["imported_visual"]["name"]
            self.text((w*.08, h*.28, w*.84, h*.05), "导入画面 · " + name[:48], 24, MUTED)
            available = (w*.08, h*.34, w*.84, h*.37)
            factor = min(available[2]/visual_image.width(), available[3]/visual_image.height())
            iw, ih = visual_image.width()*factor, visual_image.height()*factor
            self.p.drawImage(QRectF(available[0]+(available[2]-iw)/2, available[1]+(available[3]-ih)/2, iw, ih), visual_image)
        elif i2c and any(key in content for key in ("最小", "最大", "选型", "过小", "过大")):
            self.limits(*rect, local_time)
        elif i2c and any(key in content for key in ("开漏", "NMOS", "电路", "充电路径")):
            self.circuit(*rect, local_time)
        elif i2c or "RC" in content or "上升时间" in content:
            self.rc(*rect, local_time)
        elif "ADC" in (content+self.title).upper() or "采样" in content:
            self.sampling(*rect, local_time)
        else:
            self.flow(*rect, local_time, content)
        self.box((w*.06, h*.77, w*.88, h*.14), "#111c2a", "#243447", 12)
        self.text((w*.09, h*.777, w*.82, h*.125), caption, 42, center=True)
        provenance = "导入画面 · 工作台合成旁白与字幕" if visual_image is not None else "技术原理示意 · 非实测画面"
        self.text((w*.06, h*.93, w*.64, h*.035), provenance, 20, MUTED)
        self.text((w*.75, h*.93, w*.19, h*.035), f"{index+1:02d} / {self.count:02d}", 22, AMBER, center=True)
        self.line(w*.06, h*.985, w*.94, h*.985, "#243447", 5)
        self.line(w*.06, h*.985, w*(.06+.88*overall), h*.985, CYAN, 5)
        self.p.end()
        return image
