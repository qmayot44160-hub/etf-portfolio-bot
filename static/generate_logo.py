"""
Genere le logo Q PortfolioQuant en PNG 512x512.
Cadre sobre, le glossy/aurora est applique sur le Q lui-meme :
degrade aurora (bleu -> violet -> vert), highlight specular, glow externe.

Usage : python static/generate_logo.py
Sortie : static/logo-512.png
"""
from PIL import Image, ImageDraw, ImageFilter, ImageFont
import os
import sys

SIZE = 512
RADIUS = 96
BG_COLOR = (10, 10, 14, 255)
BORDER_COLOR = (38, 50, 80, 255)

# Palette aurora (matche le DA app)
AURORA_BLUE = (10, 132, 255)
AURORA_PURPLE = (191, 90, 242)
AURORA_GREEN = (48, 209, 88)
AURORA_ORANGE = (255, 159, 10)

def find_bold_font():
    candidates = [
        "C:/Windows/Fonts/seguibl.ttf",     # Segoe UI Black
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/System/Library/Fonts/SFNS.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None

def make_rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size, size), radius=radius, fill=255)
    return m

def make_aurora_fill(size):
    """Cree une image RGBA size x size avec un degrade aurora multi-couleurs.
    Utilisee comme remplissage pour le Q via mask."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Base : degrade vertical bleu (haut) -> violet (bas)
    for y in range(size):
        t = y / size
        # Lerp bleu -> violet
        r = int(AURORA_BLUE[0] * (1 - t) + AURORA_PURPLE[0] * t)
        g = int(AURORA_BLUE[1] * (1 - t) + AURORA_PURPLE[1] * t)
        b = int(AURORA_BLUE[2] * (1 - t) + AURORA_PURPLE[2] * t)
        draw.line([(0, y), (size, y)], fill=(r, g, b, 255))
    # Tache verte en bas-gauche
    green_blob = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(green_blob)
    cx, cy = int(size * 0.25), int(size * 0.78)
    radius = int(size * 0.4)
    gd.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
               fill=AURORA_GREEN + (180,))
    green_blob = green_blob.filter(ImageFilter.GaussianBlur(radius=radius * 0.5))
    img.alpha_composite(green_blob)
    # Touche orange chaude en haut-droite
    orange_blob = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    od = ImageDraw.Draw(orange_blob)
    cx, cy = int(size * 0.78), int(size * 0.22)
    radius = int(size * 0.3)
    od.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
               fill=AURORA_ORANGE + (120,))
    orange_blob = orange_blob.filter(ImageFilter.GaussianBlur(radius=radius * 0.5))
    img.alpha_composite(orange_blob)
    return img

def main():
    out_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(out_dir, "logo-512.png")

    # ─── 1. Fond sombre simple ──────────────────────────────
    base = Image.new("RGBA", (SIZE, SIZE), BG_COLOR)

    # Tres legere texture de fond (eclairage diffus, pas trop visible)
    soft = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    sd = ImageDraw.Draw(soft)
    sd.ellipse((-100, -100, SIZE + 100, int(SIZE * 0.6)),
               fill=(40, 50, 90, 60))
    soft = soft.filter(ImageFilter.GaussianBlur(radius=80))
    base.alpha_composite(soft)

    # Clip aux coins arrondis
    rounded_mask = make_rounded_mask(SIZE, RADIUS)
    final = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    final.paste(base, (0, 0), rounded_mask)

    # ─── 2. Bordure discrete ────────────────────────────────
    border_layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    bd = ImageDraw.Draw(border_layer)
    bd.rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=RADIUS,
                         outline=BORDER_COLOR, width=3)
    final.alpha_composite(border_layer)

    # ─── 3. Le Q : ici tout le glossy aurora ────────────────
    font_path = find_bold_font()
    if not font_path:
        print("ERREUR : aucune police bold trouvee")
        sys.exit(1)

    font_size = int(SIZE * 0.72)
    font = ImageFont.truetype(font_path, font_size)
    fdraw = ImageDraw.Draw(final)
    bbox = fdraw.textbbox((0, 0), "Q", font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (SIZE - text_w) // 2 - bbox[0]
    y = (SIZE - text_h) // 2 - bbox[1]

    # 3a : glow externe aurora (halo colore derriere le Q)
    glow_layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow_layer)
    gd.text((x, y), "Q", font=font, fill=AURORA_BLUE + (180,))
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=22))
    final.alpha_composite(glow_layer)
    # Deuxieme couche de glow violet plus diffus
    glow2 = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    g2d = ImageDraw.Draw(glow2)
    g2d.text((x, y), "Q", font=font, fill=AURORA_PURPLE + (120,))
    glow2 = glow2.filter(ImageFilter.GaussianBlur(radius=40))
    final.alpha_composite(glow2)

    # 3b : le Q rempli avec aurora
    # On cree le mask du Q, puis on applique l'aurora fill via ce mask
    q_mask = Image.new("L", (SIZE, SIZE), 0)
    qmd = ImageDraw.Draw(q_mask)
    qmd.text((x, y), "Q", font=font, fill=255)

    aurora_fill = make_aurora_fill(SIZE)
    q_aurora = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    q_aurora.paste(aurora_fill, (0, 0), q_mask)
    final.alpha_composite(q_aurora)

    # 3c : highlight glossy en haut du Q (bande blanche brillante clippee au Q)
    highlight = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    hd = ImageDraw.Draw(highlight)
    # Forte luminosite en haut du Q, fade rapide vers le milieu
    hl_height = int(text_h * 0.55)
    for hy in range(hl_height):
        t = hy / hl_height
        # Ease-out cubique : super brillant en haut, fade rapide
        ease = (1 - t) ** 3
        alpha = int(220 * ease)
        hd.line([(0, y + hy + bbox[1]), (SIZE, y + hy + bbox[1])],
                fill=(255, 255, 255, alpha))
    highlight_blurred = highlight.filter(ImageFilter.GaussianBlur(radius=2))
    highlight_clipped = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    highlight_clipped.paste(highlight_blurred, (0, 0), q_mask)
    final.alpha_composite(highlight_clipped)

    # 3d : reflet specular tres fin au tout sommet du Q (effet verre)
    spec = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    sd2 = ImageDraw.Draw(spec)
    spec_h = int(text_h * 0.18)
    for sy in range(spec_h):
        t = sy / spec_h
        alpha = int(180 * (1 - t) ** 2)
        sd2.line([(0, y + sy + bbox[1]), (SIZE, y + sy + bbox[1])],
                 fill=(255, 255, 255, alpha))
    spec_clipped = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    spec_clipped.paste(spec, (0, 0), q_mask)
    final.alpha_composite(spec_clipped)

    # 3e : ombre interne en bas du Q (donne du volume)
    inner_shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    isd = ImageDraw.Draw(inner_shadow)
    is_h = int(text_h * 0.3)
    for iy in range(is_h):
        t = iy / is_h
        alpha = int(110 * (1 - t) ** 2)
        isd.line([(0, y + bbox[3] - iy - 1), (SIZE, y + bbox[3] - iy - 1)],
                 fill=(0, 0, 30, alpha))
    inner_shadow_clipped = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    inner_shadow_clipped.paste(inner_shadow, (0, 0), q_mask)
    final.alpha_composite(inner_shadow_clipped)

    final.save(out_path, "PNG", optimize=True)
    print(f"OK : {out_path} ({SIZE}x{SIZE})")

if __name__ == "__main__":
    main()
