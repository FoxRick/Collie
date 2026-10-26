"""Fresh capture grounding without invoking a model or retaining image history."""

import base64
import hashlib
import io


def pixel_fingerprint(screenshot: str, x: float, y: float) -> str:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(base64.b64decode(screenshot.split(",", 1)[1]))) as image:
            if not (0 <= x < image.width and 0 <= y < image.height):
                raise ValueError("Coordinates are outside the observed image.")
            crop = image.crop(
                (
                    max(0, int(x) - 12),
                    max(0, int(y) - 12),
                    min(image.width, int(x) + 13),
                    min(image.height, int(y) + 13),
                )
            )
            return hashlib.sha256(crop.tobytes()).hexdigest()
    except (OSError, IndexError):
        raise ValueError("Capture cannot ground this coordinate action.") from None
