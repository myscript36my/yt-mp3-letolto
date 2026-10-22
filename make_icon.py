"""Egyszerű saját ikon: piros lekerekített négyzet, fehér hangjegy és lefelé nyíl."""
from PIL import Image, ImageDraw

S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((8, 8, S - 8, S - 8), radius=56, fill=(214, 40, 40))

# hangjegy
d.ellipse((58, 128, 118, 178), fill="white")
d.rectangle((104, 52, 118, 156), fill="white")
d.polygon([(118, 52), (160, 70), (160, 96), (118, 80)], fill="white")

# lefelé mutató nyíl
d.rectangle((170, 100, 192, 160), fill="white")
d.polygon([(150, 156), (212, 156), (181, 200)], fill="white")

img.save("icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icon.ico kész")
