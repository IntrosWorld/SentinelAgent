"""Local OCR contract tests with mocked subprocesses; no OCR install required."""
import base64
from io import BytesIO
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
import pytest

from sentinel_agent.computer_use import desktop_ocr as ocr


HEADER = b"level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"


def image_data(format="PNG", size=(100, 80)):
    output = BytesIO()
    Image.new("RGB", size, "white").save(output, format=format)
    return base64.b64encode(output.getvalue()).decode("ascii")


def row(text="Rectangle", *, level=5, x=30, y=60, width=90, height=30, confidence=90):
    return f"{level}\t1\t1\t1\t1\t1\t{x}\t{y}\t{width}\t{height}\t{confidence}\t{text}\n".encode("utf-8")


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.setattr(ocr.shutil, "which", Mock(return_value=r"C:\Program Files\Tesseract-OCR\tesseract.exe"))
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=HEADER + row(), stderr=b""))
    monkeypatch.setattr(ocr, "_run_tesseract", run)
    return run


@pytest.mark.parametrize("format", ["PNG", "JPEG"])
def test_image_is_sent_through_fixed_argv_and_stdin_only(runner, format):
    image = image_data(format)
    assert ocr.extract_words(image, 100, 80) == [{"text": "Rectangle", "bounds": (10, 20, 40, 30), "confidence": 90.0}]
    assert runner.call_count == 2
    assert [call.args[0][-2] for call in runner.call_args_list] == ["6", "11"]
    for call in runner.call_args_list:
        argv, processed, timeout = call.args
        assert argv[:5] == [r"C:\Program Files\Tesseract-OCR\tesseract.exe", "stdin", "stdout", "--psm", argv[4]]
        assert argv[-1] == "tsv"
        assert 0 < timeout <= 5
        with Image.open(BytesIO(processed)) as frame:
            assert frame.mode == "L" and frame.size == (300, 240)


def test_missing_binary_fails_without_subprocess(runner, monkeypatch):
    monkeypatch.setattr(ocr.shutil, "which", lambda _: None)
    with pytest.raises(ocr.LocalOCRError, match="installed Tesseract"):
        ocr.extract_words(image_data(), 100, 80)
    runner.assert_not_called()


@pytest.mark.parametrize("exception", [subprocess.TimeoutExpired("private filename", 5, output=b"private OCR text"),
                                       OSError("private filename")])
def test_subprocess_failures_are_value_free(runner, exception):
    runner.side_effect = exception
    with pytest.raises(ocr.LocalOCRError) as error:
        ocr.extract_words(image_data(), 100, 80)
    assert "private" not in str(error.value)


def test_nonzero_exit_does_not_echo_stderr(runner):
    runner.return_value = SimpleNamespace(returncode=1, stdout=b"private recognized text", stderr=b"private paths")
    with pytest.raises(ocr.LocalOCRError) as error:
        ocr.extract_words(image_data(), 100, 80)
    assert "private" not in str(error.value)


@pytest.mark.parametrize("image", [None, "", "private invalid base64!", base64.b64encode(b"arbitrary").decode(),
                                    base64.b64encode(b"\x89PNG\r\n\x1a\ntruncated").decode()])
def test_invalid_image_never_starts_subprocess(runner, image):
    with pytest.raises(ocr.LocalOCRError):
        ocr.extract_words(image, 100, 80)
    runner.assert_not_called()


@pytest.mark.parametrize("size", [(99, 80), (True, 80), (0, 80), (20000, 80), (10000, 10000)])
def test_mismatched_or_unsafe_dimensions_rejected(runner, size):
    with pytest.raises(ocr.LocalOCRError):
        ocr.extract_words(image_data(), *size)
    runner.assert_not_called()


def test_oversized_encoded_input_rejected_before_decoding(runner, monkeypatch):
    monkeypatch.setattr(ocr.base64, "b64decode", Mock(side_effect=AssertionError("Must not decode")))
    with pytest.raises(ocr.LocalOCRError, match="size limit"):
        ocr.extract_words("a" * (4 * ((ocr.MAX_IMAGE_BYTES + 2) // 3) + 1), 100, 80)
    runner.assert_not_called()


def test_pillow_image_bomb_warning_is_rejected(runner, monkeypatch):
    import warnings
    image = image_data()
    def bomb(*args, **kwargs):
        warnings.warn("private image metadata", Image.DecompressionBombWarning)
    monkeypatch.setattr(Image, "open", bomb)
    with pytest.raises(ocr.LocalOCRError) as error:
        ocr.extract_words(image, 100, 80)
    assert "private" not in str(error.value)
    runner.assert_not_called()


@pytest.mark.parametrize("output", [b"", b"private invalid output", b"level\ttext\n5\tsecret\n", HEADER + b"\xff",
                                     b"x" * (ocr.MAX_TSV_BYTES + 1)],
                         ids=["empty", "garbage", "bad-header", "invalid-utf8", "oversized"])
def test_invalid_tsv_fails_without_disclosure(runner, output):
    runner.return_value.stdout = output
    with pytest.raises(ocr.LocalOCRError) as error:
        ocr.extract_words(image_data(), 100, 80)
    assert "secret" not in str(error.value) and "private" not in str(error.value)


def test_nonword_low_confidence_malformed_and_out_of_bounds_rows_are_ignored(runner):
    runner.return_value.stdout = HEADER + b"bad\trow\n" + row(level=4) + row(confidence=49.99) + row(confidence="nan") + row(confidence="inf") + row(confidence=101) + row(x=-1) + row(x=290, width=30) + row(height=0) + row(width="three") + row(text="") + row(text="valid", confidence=50)
    assert ocr.extract_words(image_data(), 100, 80) == [{"text": "valid", "bounds": (10, 20, 40, 30), "confidence": 50.0}]


def test_word_count_and_text_length_are_bounded_in_source_order(runner):
    runner.return_value.stdout = HEADER + row(text="a" * 200) + b"".join(row(text=f"word{i}") for i in range(100))
    words = ocr.extract_words(image_data(), 100, 80)
    assert len(words) == 80
    assert words[0]["text"] == "a" * 80
    assert words[-1]["text"] == "word78"
    assert set(words[0]) == {"text", "bounds", "confidence"}


def test_header_only_is_a_valid_empty_result(runner):
    runner.return_value.stdout = HEADER
    assert ocr.extract_words(image_data(), 100, 80) == []


def test_unicode_quotes_and_shell_syntax_remain_data(runner):
    runner.return_value.stdout = HEADER + row(text='"हिन्दी";$(calc)')
    assert ocr.extract_words(image_data(), 100, 80)[0]["text"] == '"हिन्दी";$(calc)'
    assert len(runner.call_args.args[0]) == 6


@pytest.mark.parametrize("shade,expected", [(0, 255), (100, 155), (127, 127), (255, 255)])
def test_only_dark_images_are_inverted(shade, expected):
    buffer = BytesIO()
    Image.new("RGB", (20, 10), (shade, shade, shade)).save(buffer, format="PNG")
    processed, width, height = ocr._prepare_image(buffer.getvalue(), 20, 10)
    with Image.open(BytesIO(processed)) as frame:
        assert frame.size == (60, 30) and (width, height) == frame.size
        assert frame.getpixel((30, 15)) == expected


@pytest.mark.parametrize("size", [(2000, 1000), (100, 5000)])
def test_processed_pixel_and_side_caps(size):
    data = base64.b64decode(image_data(size=size))
    _, width, height = ocr._prepare_image(data, *size)
    assert width * height <= ocr.MAX_PROCESSED_PIXELS
    assert max(width, height) <= ocr.MAX_PROCESSED_SIDE


def test_non_square_rounding_uses_actual_axis_sizes():
    words = [{"text": "7", "bounds": (41, 101, 78, 142), "confidence": 96.8}]
    assert ocr._map_words(words, 300, 239, 100, 80)[0]["bounds"] == (13, 33, 26, 48)


def test_merge_keeps_higher_confidence_but_preserves_separate_repeated_digits(runner):
    runner.side_effect = [SimpleNamespace(returncode=0, stdout=HEADER + row("7", confidence=80)),
                          SimpleNamespace(returncode=0, stdout=HEADER + row("7", x=33, confidence=95) + row("7", x=150))]
    words = ocr.extract_words(image_data(), 100, 80)
    assert len(words) == 2
    assert words[0] == {"text": "7", "bounds": (11, 20, 41, 30), "confidence": 95.0}
    assert words[1]["bounds"] == (50, 20, 80, 30)


def test_two_passes_share_one_deadline(runner, monkeypatch):
    monkeypatch.setattr(ocr.time, "monotonic", Mock(side_effect=[100, 101, 104]))
    ocr.extract_words(image_data(), 100, 80)
    assert [call.args[2] for call in runner.call_args_list] == [4, 1]


def test_successful_first_pass_survives_second_pass_timeout(runner):
    runner.side_effect = [SimpleNamespace(returncode=0, stdout=HEADER + row()), subprocess.TimeoutExpired("local-ocr", 1)]
    assert len(ocr.extract_words(image_data(), 100, 80)) == 1


def test_preprocessing_consumes_deadline(runner, monkeypatch):
    monkeypatch.setattr(ocr.time, "monotonic", Mock(side_effect=[100, 106]))
    with pytest.raises(ocr.LocalOCRError, match="time limit"):
        ocr.extract_words(image_data(), 100, 80)
    runner.assert_not_called()


def fake_process(stdout=HEADER, stderr=b"", running=False):
    return SimpleNamespace(stdin=BytesIO(), stdout=BytesIO(stdout), stderr=BytesIO(stderr),
                           poll=Mock(return_value=None if running else 0), returncode=0,
                           wait=Mock(return_value=0), kill=Mock())


def test_pipe_helper_returns_bounded_stdout_and_discards_stderr(monkeypatch):
    process = fake_process(HEADER + row(), b"private diagnostic")
    popen = Mock(return_value=process)
    monkeypatch.setattr(ocr.subprocess, "Popen", popen)
    result = ocr._run_tesseract(["trusted-tesseract", "stdin", "stdout", "--psm", "6", "tsv"], b"image", 1)
    assert result.stdout == HEADER + row() and result.stderr is None
    assert popen.call_args.kwargs["shell"] is False
    assert popen.call_args.kwargs["bufsize"] == 0


@pytest.mark.parametrize("pipe", ["stdout", "stderr"])
def test_pipe_helper_kills_on_output_overflow(monkeypatch, pipe):
    process = fake_process(running=True)
    setattr(process, pipe, BytesIO(b"x" * 257))
    monkeypatch.setattr(ocr, "MAX_TSV_BYTES", 256)
    monkeypatch.setattr(ocr, "MAX_STDERR_BYTES", 128)
    monkeypatch.setattr(ocr.subprocess, "Popen", Mock(return_value=process))
    with pytest.raises(ocr.LocalOCRError, match="output limit"):
        ocr._run_tesseract(["trusted-tesseract"], b"image", 1)
    process.kill.assert_called_once()


def test_pipe_helper_kills_on_deadline(monkeypatch):
    process = fake_process(running=True)
    monkeypatch.setattr(ocr.subprocess, "Popen", Mock(return_value=process))
    with pytest.raises(subprocess.TimeoutExpired):
        ocr._run_tesseract(["trusted-tesseract"], b"image", 0.01)
    process.kill.assert_called_once()
