from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "docs" / "design" / "assets" / "collie-portrait-reference-sheet-chroma.png"
OUT = ROOT / "collie-ui" / "src" / "renderer" / "src" / "assets" / "portrait"

CELLS = {
    "idle": (0, 0),
    "thinking": (1, 0),
    "happy": (2, 0),
    "sleepy": (0, 1),
    "concerned": (1, 1),
    "paw-over-ring": (2, 1),
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sheet = remove_magenta(Image.open(MASTER).convert("RGBA"))
    cell_width = sheet.width // 3
    cell_height = sheet.height // 2

    for name, (column, row) in CELLS.items():
        left = column * cell_width + (3 if column else 0)
        top = row * cell_height + (3 if row else 0)
        right = (column + 1) * cell_width - 3
        bottom = (row + 1) * cell_height - 3
        frame = sheet.crop((left, top, right, bottom))
        frame.thumbnail((384, 384), Image.Resampling.LANCZOS)
        frame.save(OUT / f"{name}.webp", "WEBP", quality=88, method=6, exact=True)

    paw_cell = sheet.crop((2 * cell_width + 3, cell_height + 3, sheet.width, sheet.height))
    paw = paw_cell.crop((315, 190, 505, 480))
    paw.thumbnail((190, 290), Image.Resampling.LANCZOS)
    silhouette = Image.new("L", paw.size)
    ImageDraw.Draw(silhouette).polygon(
        [(5, 120), (30, 82), (72, 66), (126, 75), (165, 105), (186, 148),
         (175, 198), (155, 224), (153, 290), (0, 290), (0, 185)],
        fill=255,
    )
    alpha = Image.new("L", paw.size)
    alpha_data = [min(a, m) for a, m in zip(paw.getchannel("A").getdata(), silhouette.getdata())]
    alpha.putdata(alpha_data)
    paw.putalpha(alpha)
    paw.save(OUT / "paw-front.webp", "WEBP", quality=90, method=6, exact=True)


def remove_magenta(image: Image.Image) -> Image.Image:
    cleaned = Image.new("RGBA", image.size)
    pixels = []
    for red, green, blue, _ in image.getdata():
        dominance = max(0, min(red, blue) - green)
        if dominance <= 10:
            alpha = 255
        elif dominance >= 82:
            alpha = 0
        else:
            alpha = round(255 * (82 - dominance) / 72)
        spill = min(dominance, 80)
        pixels.append((max(green, red - spill), green, max(green, blue - spill), alpha))
    cleaned.putdata(pixels)
    return cleaned


if __name__ == "__main__":
    main()
