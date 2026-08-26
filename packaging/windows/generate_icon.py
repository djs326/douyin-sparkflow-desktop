"""生成 DouYin SparkFlow 应用图标（packaging/windows/app.ico）。

用法：
    python packaging/windows/generate_icon.py

产物：packaging/windows/app.ico（构建脚本与安装脚本引用它）。
想换图标：替换/重新生成此文件后重新执行 build.ps1 即可。
"""

from pathlib import Path

from PIL import Image, ImageDraw


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def build_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 深蓝渐变圆角背景
    radius = int(size * 0.22)
    for y in range(size):
        t = y / max(1, size - 1)
        color = _lerp((0x16, 0x21, 0x3E), (0x0F, 0x34, 0x63), t) + (255,)
        draw.line([(0, y), (size, y)], fill=color)
    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    img.putalpha(mask)

    # 橙色火焰圆（火花）
    cx, cy = size * 0.5, size * 0.56
    r = size * 0.30
    for radius_factor, color in [
        (1.00, (0xFF, 0x6B, 0x35)),
        (0.72, (0xFF, 0x9E, 0x40)),
        (0.42, (0xFF, 0xD1, 0x66)),
    ]:
        rr = int(r * radius_factor)
        draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=color + (255,))

    # 顶部亮点
    hx, hy = size * 0.44, size * 0.40
    hr = int(size * 0.07)
    draw.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=(255, 255, 255, 235))

    return img


def main():
    out = Path(__file__).resolve().parent / "app.ico"
    sizes = [256, 128, 64, 48, 32, 16]
    images = [build_icon(s) for s in sizes]
    images[0].save(out, format="ICO", sizes=[(s, s) for s in sizes], append_images=images[1:])
    print(f"OK -> {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
