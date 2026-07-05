import hashlib
import io

from PIL import Image

from bot.services.photo_storage import hamming_distance, photo_metadata_from_bytes


def test_hamming_distance_handles_hex_and_invalid_values():
    assert hamming_distance("0", "0") == 0
    assert hamming_distance("f", "0") == 4
    assert hamming_distance(None, "0") is None
    assert hamming_distance("not-hex", "0") is None


def test_photo_metadata_from_bytes_hashes_and_detects_content_type():
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), color=(255, 0, 0)).save(buffer, format="PNG")
    data = buffer.getvalue()

    metadata = photo_metadata_from_bytes(data=data, file_path="photo.png")

    assert metadata.content_type == "image/png"
    assert metadata.file_size == len(data)
    assert metadata.sha256 == hashlib.sha256(data).hexdigest()
    assert metadata.perceptual_hash is not None
    assert len(metadata.perceptual_hash) == 16
