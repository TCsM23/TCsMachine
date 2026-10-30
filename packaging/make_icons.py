"""Generate the app icon and installer artwork. Run: python packaging/make_icons.py"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
PAPER, PAPER2, INK, INK3, RUST = (214, 208, 184), (194, 188, 162), (74, 70, 59), (141, 136, 119), (169, 71, 58)


def icon(size=1024) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    u = size / 64
    d.rectangle([2 * u, 2 * u, 62 * u, 62 * u], fill=PAPER)
    for y in range(int(4 * u), int(60 * u), max(2, int(u))):  # ruled-paper lines
        d.line([4 * u, y, 60 * u, y], fill=(205, 199, 174), width=max(1, int(u / 4)))
    d.rectangle([2 * u, 2 * u, 62 * u, 62 * u], outline=INK, width=int(2 * u))
    for x, y, dx, dy in ((5, 5, 1, 1), (59, 59, -1, -1)):  # corner brackets
        d.line([x * u, y * u, (x + 7 * dx) * u, y * u], fill=INK, width=int(1.5 * u))
        d.line([x * u, y * u, x * u, (y + 7 * dy) * u], fill=INK, width=int(1.5 * u))
    c = 32 * u
    r = 17 * u
    d.polygon([(c, c - r), (c + r, c), (c, c + r), (c - r, c)], fill=INK)  # the monolith
    r2 = 6 * u
    d.polygon([(c, c - r2), (c + r2, c), (c, c + r2), (c - r2, c)], fill=PAPER)
    d.rectangle([c - 1.6 * u, c - 1.6 * u, c + 1.6 * u, c + 1.6 * u], fill=INK)
    d.rectangle([49 * u, 49 * u, 54 * u, 54 * u], fill=RUST)  # the one red light
    return im


big = icon()
assets = ROOT / "pale" / "desktop" / "assets"
assets.mkdir(parents=True, exist_ok=True)
png = big.resize((256, 256), Image.LANCZOS)
png.save(assets / "pale.png")
png.save(ROOT / "pale" / "web" / "static" / "icon.png")
big.save(assets / "pale.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
big.resize((512, 512), Image.LANCZOS).save(ROOT / "packaging" / "pale-512.png")

# Inno Setup wizard art: 164x314 side panel and 55x55 corner image (24-bit BMP)
def font(px):
    for name in ("DejaVuSansMono.ttf", "consola.ttf", "Menlo.ttc"):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            pass
    return ImageFont.load_default()

for scale in (1, 2):
    w, h = 164 * scale, 314 * scale
    side = Image.new("RGB", (w, h), INK)
    d = ImageDraw.Draw(side)
    for y in range(0, h, 4 * scale):
        d.line([0, y, w, y], fill=(80, 76, 64))
    ic = icon(512).resize((96 * scale, 96 * scale), Image.LANCZOS)
    side.paste(ic, ((w - ic.width) // 2, 40 * scale), ic)
    f = font(20 * scale)
    d.text((w // 2, 168 * scale), "P A L E", fill=PAPER, font=f, anchor="mm")
    d.text((w // 2, 192 * scale), "OPERATOR SUPPORT", fill=INK3, font=font(9 * scale), anchor="mm")
    d.text((w // 2, 205 * scale), "HARNESS", fill=INK3, font=font(9 * scale), anchor="mm")
    d.rectangle([w // 2 - 3 * scale, 280 * scale, w // 2 + 3 * scale, 286 * scale], fill=RUST)
    side.save(ROOT / "packaging" / "windows" / f"wizard-side{'' if scale == 1 else '@2x'}.bmp")
    small = Image.new("RGB", (55 * scale, 55 * scale), PAPER)
    ic2 = icon(512).resize((55 * scale, 55 * scale), Image.LANCZOS)
    small.paste(ic2, (0, 0), ic2)
    small.save(ROOT / "packaging" / "windows" / f"wizard-small{'' if scale == 1 else '@2x'}.bmp")
print("icons written")
