"""Metadata extraction: EXIF dates/camera/GPS, filename dates, source guessing, orientation."""
import io

from PIL import Image

from app import hashing, metadata


def _jpeg_with_exif(**over) -> bytes:
    img = Image.new("RGB", (400, 300), (120, 160, 200))
    exif = Image.Exif()
    exif[271] = over.get("make", "Canon")
    exif[272] = over.get("model", "Canon EOS 80D")
    exif[305] = over.get("software", "Firmware 1.0")
    exif[274] = over.get("orientation", 1)
    ifd = exif.get_ifd(0x8769)
    ifd[36867] = over.get("dto", "2015:07:04 18:30:00")
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2], gps[3], gps[4] = "S", (23.0, 33.0, 0.0), "W", (46.0, 38.0, 0.0)
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def test_exif_fields(tmp_path):
    p = tmp_path / "IMG_0001.jpg"
    p.write_bytes(_jpeg_with_exif())
    meta = metadata.extract(Image.open(p), p.name)
    assert meta["date_taken"] == "2015-07-04T18:30:00"
    assert meta["date_source"] == "exif_original"
    assert meta["camera_make"] == "Canon" and meta["camera_model"] == "Canon EOS 80D"
    assert meta["source_guess"] == "Camera"
    assert meta["gps_lat"] == -23.55 and meta["gps_lon"] == -46.633333
    assert meta["has_exif"] == 1


def test_orientation_swaps_reported_size(tmp_path):
    p = tmp_path / "rot.jpg"
    p.write_bytes(_jpeg_with_exif(orientation=6))
    hashing._KNOWN = set()
    res = hashing.analyze_file(str(p), known_shas=set())
    assert (res["width"], res["height"]) == (300, 400)


def test_filename_dates_and_sources():
    assert metadata.date_from_filename("IMG_20190812_153012.jpg") == "2019-08-12T15:30:12"
    assert metadata.date_from_filename("WhatsApp Image 2020-03-01 at 10.11.12.jpeg") == "2020-03-01T10:11:12"
    assert metadata.date_from_filename("IMG-20190812-WA0003.jpg") == "2019-08-12T00:00:00"
    assert metadata.date_from_filename("meme.jpg") is None
    assert metadata.guess_source("IMG-20190812-WA0003.jpg", None, None, False, "JPEG") == "WhatsApp"
    assert metadata.guess_source("Screenshot_20200101-101010.png", None, None, False, "PNG") == "Screenshot"
    assert metadata.guess_source("x.jpg", None, "Adobe Photoshop CC", True, "JPEG") == "Edited"
    assert metadata.guess_source("funny.png", None, None, False, "PNG") == "Graphic / download"


def test_trim_bars_detects_caption():
    base = Image.effect_noise((400, 300), 80).convert("RGB")
    meme = Image.new("RGB", (400, 380), "white")
    meme.paste(base, (0, 80))
    t = hashing.trim_bars(meme)
    assert t is not None and abs(t.height - 300) <= 4
    assert hashing.trim_bars(base) is None
