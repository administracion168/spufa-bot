import io
import os
import random
import struct
import subprocess
import tempfile
import time
import zipfile

from PIL import Image, ImageEnhance, ImageFilter
import piexif

SUPPORTED_IMAGE_FORMATS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
SUPPORTED_VIDEO_FORMATS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def _to_dms(deg: float):
    d = int(abs(deg))
    m = int((abs(deg) - d) * 60)
    s = int(((abs(deg) - d) * 60 - m) * 60 * 100)
    return ((d, 1), (m, 1), (s, 100))


def _make_exif() -> bytes:
    lat = random.uniform(25.0, 49.0)
    lon = random.uniform(-125.0, -67.0)

    cameras = [
        (b"Apple", b"iPhone 14 Pro"),
        (b"Samsung", b"SM-S918B"),
        (b"Google", b"Pixel 8 Pro"),
        (b"Sony", b"ILCE-7M4"),
        (b"Canon", b"Canon EOS R6"),
    ]
    make, model = random.choice(cameras)

    ts = int(time.time()) - random.randint(0, 60 * 60 * 24 * 365 * 2)
    t = time.localtime(ts)
    dt_str = time.strftime("%Y:%m:%d %H:%M:%S", t).encode()

    exif_dict = {
        "0th": {
            piexif.ImageIFD.Make: make,
            piexif.ImageIFD.Model: model,
            piexif.ImageIFD.DateTime: dt_str,
            piexif.ImageIFD.Software: b"SPUFA/1.0",
        },
        "Exif": {
            piexif.ExifIFD.DateTimeOriginal: dt_str,
            piexif.ExifIFD.DateTimeDigitized: dt_str,
        },
        "GPS": {
            piexif.GPSIFD.GPSLatitudeRef: b"N",
            piexif.GPSIFD.GPSLatitude: _to_dms(lat),
            piexif.GPSIFD.GPSLongitudeRef: b"W",
            piexif.GPSIFD.GPSLongitude: _to_dms(abs(lon)),
        },
        "1st": {},
        "thumbnail": None,
    }
    return piexif.dump(exif_dict)


def process_image(image_bytes: bytes, seed: int = None) -> bytes:
    if seed is not None:
        random.seed(seed)

    img = Image.open(io.BytesIO(image_bytes))

    if img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        if img.mode in ("RGBA", "LA"):
            bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    img = ImageEnhance.Brightness(img).enhance(1.0 + random.uniform(-0.015, 0.015))
    img = ImageEnhance.Color(img).enhance(1.0 + random.uniform(-0.03, 0.03))
    img = ImageEnhance.Contrast(img).enhance(1.0 + random.uniform(-0.015, 0.015))
    img = ImageEnhance.Sharpness(img).enhance(1.0 + random.uniform(-0.02, 0.02))

    angle = random.uniform(0.1, 0.4) * random.choice([-1, 1])
    img = img.rotate(angle, expand=False, fillcolor=(255, 255, 255))

    if random.random() < 0.4:
        img = img.transpose(
            Image.FLIP_LEFT_RIGHT if random.random() < 0.5 else Image.FLIP_TOP_BOTTOM
        )

    w, h = img.size
    left = random.randint(0, 2)
    top = random.randint(0, 2)
    right = w - random.randint(0, 2)
    bottom = h - random.randint(0, 2)
    img = img.crop((left, top, max(right, left + 1), max(bottom, top + 1)))

    dx, dy = random.randint(-1, 1), random.randint(-1, 1)
    if dx != 0 or dy != 0:
        shifted = Image.new("RGB", img.size, (255, 255, 255))
        shifted.paste(img, (dx, dy))
        img = shifted

    if random.random() < 0.5:
        img = img.filter(ImageFilter.GaussianBlur(radius=0.15))
    else:
        img = img.filter(ImageFilter.SMOOTH)

    quality = random.randint(93, 97)
    output = io.BytesIO()
    img.save(output, format="JPEG", quality=quality, exif=_make_exif())
    jpeg_bytes = output.getvalue()

    noise = bytes([random.randint(0, 255) for _ in range(random.randint(4, 16))])
    com_marker = b"\xff\xfe" + struct.pack(">H", len(noise) + 2) + noise
    jpeg_bytes = jpeg_bytes[:2] + com_marker + jpeg_bytes[2:]

    return jpeg_bytes


def process_image_variants(image_bytes: bytes, count: int) -> list:
    base_seed = random.randint(0, 10_000_000)
    return [process_image(image_bytes, seed=base_seed + i) for i in range(count)]


def process_video(video_bytes: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = os.path.join(tmpdir, "input.mp4")
        palette_path = os.path.join(tmpdir, "palette.png")
        output_path = os.path.join(tmpdir, "output.gif")

        with open(input_path, "wb") as f:
            f.write(video_bytes)

        subprocess.run(
            [
                "ffmpeg", "-y", "-i", input_path,
                "-vf", "fps=18,scale=480:-1:flags=lanczos,palettegen=stats_mode=diff",
                "-map_metadata", "-1",
                palette_path,
            ],
            capture_output=True,
            check=True,
        )

        subprocess.run(
            [
                "ffmpeg", "-y", "-i", input_path, "-i", palette_path,
                "-lavfi", "fps=18,scale=480:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5",
                "-map_metadata", "-1",
                output_path,
            ],
            capture_output=True,
            check=True,
        )

        with open(output_path, "rb") as f:
            return f.read()


def is_image(filename: str) -> bool:
    return os.path.splitext(filename.lower())[1] in SUPPORTED_IMAGE_FORMATS


def is_video(filename: str) -> bool:
    return os.path.splitext(filename.lower())[1] in SUPPORTED_VIDEO_FORMATS


def create_zip(file_list: list) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in file_list:
            zf.writestr(name, data)
    return buf.getvalue()
