# -*- coding: utf-8 -*-
"""dev/_make_icons.py — 程序化生成应用图标三件套（icon.ico / icon.png / icon.icns）。

背景（打包/卸载复盘 2026-10-25）：resources/ 下 icon 三件套缺失，
electron-builder 回退 Electron 默认图标。本脚本以 boot-gate 品牌语言
（宣纸白底 + 楷体「墨」+ 蓝印方章）绘制并输出到 resources/。
一次性工具：换图标设计时改本脚本重跑（需 conda inkcube 环境的 Pillow）。

用法：C:/Users/Kelvin/.conda/envs/inkcube/python.exe dev/_make_icons.py
"""
import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "resources")

SIZE = 1024
RADIUS = 200
PAPER = (248, 245, 238, 255)   # 宣纸暖白
INK = (26, 26, 26, 255)        # 深墨黑
SEAL = (37, 99, 235, 255)      # accent 蓝 #2563eb
BORDER = (224, 218, 205, 255)  # 纸边浅棕灰

# 候选楷体/书法感字体（Windows 中文字体；缺失则按序回退）
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\simkai.ttf",    # 楷体
    r"C:\Windows\Fonts\STKAITI.TTF",   # 华文楷体
    r"C:\Windows\Fonts\simhei.ttf",    # 黑体（兜底）
    r"C:\Windows\Fonts\msyh.ttc",      # 微软雅黑（最后兜底）
]


def pick_font(size: int) -> str:
    for path in FONT_CANDIDATES:
        if os.path.isfile(path):
            return path
    raise SystemExit("no CJK font found in C:\\Windows\\Fonts")


def build_icon() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 圆角方形纸底 + 细边框
    draw.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=RADIUS, fill=PAPER,
                           outline=BORDER, width=6)

    # 主字「墨」：楷体居中（视觉重心略上移）
    font = ImageFont.truetype(pick_font(640), 640)
    bbox = draw.textbbox((0, 0), "墨", font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((SIZE - w) / 2 - bbox[0], (SIZE - h) / 2 - bbox[1] - 20),
              "墨", font=font, fill=INK)

    # 右下角蓝印方章（纯色小方块，微圆角；小尺寸下不渲染字避免糊）
    seal = 150
    draw.rounded_rectangle([SIZE - seal - 96, SIZE - seal - 96,
                            SIZE - 96, SIZE - 96], radius=28, fill=SEAL)
    return img


def main() -> None:
    img = build_icon()

    png_path = os.path.join(RES, "icon.png")
    img.save(png_path)

    ico_path = os.path.join(RES, "icon.ico")
    img.save(ico_path, sizes=[(256, 256), (128, 128), (64, 64), (48, 48),
                              (32, 32), (24, 24), (16, 16)])

    icns_path = os.path.join(RES, "icon.icns")
    img.save(icns_path, format="ICNS",
             sizes=[(16, 16), (32, 32), (128, 128), (256, 256),
                    (512, 512), (1024, 1024)])

    for p in (png_path, ico_path, icns_path):
        print(f"{p}  {os.path.getsize(p) / 1024:.0f} KB")


if __name__ == "__main__":
    main()
