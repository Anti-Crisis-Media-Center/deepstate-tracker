import sys
from PIL import Image, ImageDraw, ImageFont, ImageFilter
src = Image.open(sys.argv[1]).convert("RGB")
W, H = 1200, 630
F = "/home/claude/dst-repo/assets/fonts/"
YEL = (247, 197, 49); WHITE = (250, 250, 250)
# фон: мапа з обкладинки (без тексту), розтягнута по висоті
map_crop = src.crop((300, 305, 1080, 730))
mh = H; mw = int(map_crop.width * mh / map_crop.height)
bg = Image.new("RGB", (W, H), (10, 14, 26))
bg.paste(map_crop.resize((mw, mh), Image.LANCZOS), (500, 0))
# затемнення ліворуч, щоб текст читався
grad = Image.new("L", (W, H))
gd = ImageDraw.Draw(grad)
for x in range(W):
    a = 255 if x < 520 else max(0, int(255 * (1 - (x - 520) / 200)))
    gd.line([(x, 0), (x, H)], fill=a)
bg = Image.composite(Image.new("RGB", (W, H), (10, 14, 26)), bg, grad)
d = ImageDraw.Draw(bg)
f_big = ImageFont.truetype(F + "Montserrat-800.ttf", 92)
f_mid = ImageFont.truetype(F + "Montserrat-800.ttf", 64)
f_sub = ImageFont.truetype(F + "Montserrat-500.ttf", 30)
f_bad = ImageFont.truetype(F + "Montserrat-800.ttf", 26)
x0 = 64
d.text((x0, 70), "DeepState:", font=f_big, fill=YEL)
d.text((x0, 180), "хроніка", font=f_mid, fill=WHITE)
d.text((x0, 255), "змін", font=f_mid, fill=WHITE)
d.text((x0, 365), "ДОНЕЧЧИНА · ЛУГАНЩИНА", font=f_sub, fill=WHITE)
# плашка «оновлюється щодня»
txt = "ОНОВЛЮЄТЬСЯ ЩОДНЯ"
tw = d.textlength(txt, font=f_bad)
d.rounded_rectangle((x0, 440, x0 + tw + 48, 500), radius=10, outline=YEL, width=4)
d.text((x0 + 24, 454), txt, font=f_bad, fill=WHITE)
# логотип з оригіналу
logo = src.crop((735, 820, 1050, 1000))
lw = 230; logo = logo.resize((lw, int(logo.height * lw / logo.width)), Image.LANCZOS)
mask = logo.convert("L").point(lambda v: 255 if v > 110 else 0).filter(ImageFilter.GaussianBlur(0.6))
bg.paste(logo, (W - lw - 48, H - logo.height - 40), mask)
out = sys.argv[2]
for q in (88, 82, 76, 70, 64):
    bg.save(out, "JPEG", quality=q, optimize=True, progressive=True)
    import os
    if os.path.getsize(out) <= 200_000:
        break
print(out, os.path.getsize(out) // 1024, "KB, q", q)
