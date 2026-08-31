"""Corner-case test suite for image quality assessment and cropper resilience."""

import pytest
from PIL import Image, ImageDraw

from src.cropper import process_for_pdf
from src.quality import (
    analyze_single_image,
    assess_image_quality,
    check_image_quality,
    is_blurry,
)


@pytest.fixture
def corrupt_files(tmp_path):
    zero_byte = tmp_path / "zero_byte.jpg"
    zero_byte.write_bytes(b"")

    garbage = tmp_path / "garbage.png"
    garbage.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\xff" * 50)

    not_image = tmp_path / "text_file.jpg"
    not_image.write_text("this is plain text and definitely not an image")

    return {
        "zero_byte": zero_byte,
        "garbage": garbage,
        "not_image": not_image,
    }


class TestCorruptAndTruncatedImages:
    def test_zero_byte_file_in_check_image_quality(self, corrupt_files):
        ok, score, dims = check_image_quality(corrupt_files["zero_byte"], blur_threshold=50.0)
        assert ok is False
        assert score == 0.0
        assert dims == (0, 0)

    def test_garbage_file_in_check_image_quality(self, corrupt_files):
        ok, score, dims = check_image_quality(corrupt_files["garbage"], blur_threshold=50.0)
        assert ok is False
        assert score == 0.0
        assert dims == (0, 0)

    def test_text_file_in_assess_image_quality(self, corrupt_files):
        ok, score, dims = assess_image_quality(corrupt_files["not_image"], blur_threshold=50.0)
        assert ok is False
        assert score == 0.0
        assert dims == (0, 0)

    def test_is_blurry_on_corrupt_files_returns_false_and_zero_without_crashing(self, corrupt_files):
        for name, path in corrupt_files.items():
            blurry, score = is_blurry(path, threshold=50.0)
            assert blurry is False
            assert score == 0.0

    def test_analyze_single_image_corrupt_file_sets_integrity_and_is_not_ok(self, corrupt_files):
        for name, path in corrupt_files.items():
            res = analyze_single_image(path)
            assert res["is_ok"] is False
            assert "Corrupto" in res["integrity"] or "Error" in res["integrity"] or "Dimensiones" in res["integrity"]


class TestUnusualImageColorModes:
    @pytest.fixture
    def mode_images(self, tmp_path):
        """Create valid card-sized (800x1120) images in various color modes."""
        w, h = 800, 1120
        # Base RGB pattern
        base = Image.new("RGB", (w, h), color="white")
        draw = ImageDraw.Draw(base)
        for i in range(0, w, 30):
            draw.line([(i, 0), (i, h)], fill="black", width=4)
            draw.line([(0, i), (w, i)], fill="black", width=4)

        paths = {}
        # 1-bit mode ('1')
        img_1 = base.convert("1")
        p_1 = tmp_path / "mode_1.png"
        img_1.save(p_1)
        paths["1"] = p_1

        # Paletted 8-bit mode ('P')
        img_p = base.convert("P")
        p_p = tmp_path / "mode_P.png"
        img_p.save(p_p)
        paths["P"] = p_p

        # Greyscale + Alpha ('LA')
        img_la = base.convert("LA")
        p_la = tmp_path / "mode_LA.png"
        img_la.save(p_la)
        paths["LA"] = p_la

        # CMYK mode ('CMYK')
        img_cmyk = base.convert("CMYK")
        p_cmyk = tmp_path / "mode_CMYK.jpg"
        img_cmyk.save(p_cmyk)
        paths["CMYK"] = p_cmyk

        # RGBA mode ('RGBA')
        img_rgba = base.convert("RGBA")
        p_rgba = tmp_path / "mode_RGBA.png"
        img_rgba.save(p_rgba)
        paths["RGBA"] = p_rgba

        return paths

    def test_check_image_quality_handles_all_modes_without_crashing(self, mode_images):
        for mode, path in mode_images.items():
            ok, score, dims = check_image_quality(path, blur_threshold=10.0, check_brightness_contrast=False)
            assert ok is True, f"Failed for mode {mode}"
            assert score > 10.0
            assert dims == (800, 1120)

    def test_is_blurry_handles_all_modes(self, mode_images):
        for mode, path in mode_images.items():
            blurry, score = is_blurry(path, threshold=10.0)
            assert blurry is False, f"Flagged as blurry for mode {mode}"
            assert score > 10.0

    def test_analyze_single_image_handles_all_modes(self, mode_images):
        for mode, path in mode_images.items():
            res = analyze_single_image(path, check_brightness_contrast=False)
            assert res["is_ok"] is True, f"analyze_single_image failed for mode {mode}"
            assert res["integrity"] == "OK"

    def test_cropper_process_for_pdf_converts_unusual_modes_to_rgb(self, mode_images, tmp_path):
        for mode, in_path in mode_images.items():
            out_path = tmp_path / f"cropped_{mode}.jpg"
            result = process_for_pdf(in_path, out_path, crop_borders=True)
            assert result.exists()
            with Image.open(result) as out_img:
                assert out_img.mode == "RGB"


class TestSolidColorZeroVariance:
    def test_pure_solid_black_zero_variance(self, tmp_path):
        """Solid black has 0 Laplacian variance; verify no ZeroDivisionError."""
        black_img = Image.new("RGB", (800, 1120), color="black")
        black_path = tmp_path / "solid_black.png"
        black_img.save(black_path)

        blurry, score = is_blurry(black_path, threshold=50.0)
        assert blurry is True  # 0.0 < 50.0
        assert score == 0.0

        ok, score, dims = check_image_quality(black_path, blur_threshold=0.0, check_brightness_contrast=False)
        assert ok is True
        assert score == 0.0
        assert dims == (800, 1120)

    def test_pure_solid_white_zero_variance(self, tmp_path):
        """Solid white has 0 Laplacian variance; verify no ZeroDivisionError."""
        white_img = Image.new("RGB", (800, 1120), color="white")
        white_path = tmp_path / "solid_white.png"
        white_img.save(white_path)

        blurry, score = is_blurry(white_path, threshold=50.0)
        assert blurry is True
        assert score == 0.0

        ok, score, dims = check_image_quality(white_path, blur_threshold=0.0, check_brightness_contrast=False)
        assert ok is True
        assert score == 0.0
        assert dims == (800, 1120)
