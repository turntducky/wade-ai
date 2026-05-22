from pathlib import Path
from PIL import Image, ImageDraw

ICONS = [
    ("tray-idle",      (100, 149, 237, 255)),  # cornflower blue  — at rest
    ("tray-thinking",  (255, 193,   7, 255)),  # amber            — LLM generating
    ("tray-running",   ( 76, 175,  80, 255)),  # green            — task executing
    ("tray-attention", (244,  67,  54, 255)),  # red              — proactive alert
]

OUT = Path("src-tauri/icons")
OUT.mkdir(parents=True, exist_ok=True)

for stem, colour in ICONS:
    for size in (16, 32):
        img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        m    = max(1, size // 8)
        draw.ellipse([m, m, size - m, size - m], fill=colour)
        path = OUT / f"{stem}-{size}.png"
        img.save(path)
        print(f"  created {path}")

print("Done.")
