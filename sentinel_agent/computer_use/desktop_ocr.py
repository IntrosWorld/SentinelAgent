"""Optional local word recognition, not an executable UI or editable-field map.

Tesseract receives a validated image over stdin and returns TSV over stdout.
No temporary image files, downloads, shell command strings, model calls or
network connections are used. The caller must treat recognized text as
untrusted observations and may not infer control roles from OCR alone.
"""
from __future__ import annotations

import base64
from contextlib import ExitStack
from contextlib import suppress
import csv
from io import BytesIO, StringIO
import math
import shutil
import subprocess
import threading
import time
import warnings


MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_TSV_BYTES = 2 * 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_WORDS = 80
MAX_TEXT_LENGTH = 80
MAX_PROCESSED_PIXELS = 8_000_000
MAX_PROCESSED_SIDE = 4096
OCR_TIMEOUT_SECONDS = 5.0
_TSV_COLUMNS = {"level", "page_num", "block_num", "par_num", "line_num", "word_num",
                "left", "top", "width", "height", "conf", "text"}


class LocalOCRError(RuntimeError):
    """Value-free local OCR failure, safe to include in diagnostic summaries."""


def _image_bytes(screenshot_base64, width, height):
    if (type(width) is not int or type(height) is not int or not 0 < width <= 16384
            or not 0 < height <= 16384 or width * height > 32_000_000):
        raise LocalOCRError("Local OCR requires valid image dimensions")
    if not isinstance(screenshot_base64, str) or not screenshot_base64:
        raise LocalOCRError("Local OCR requires a base64 image")
    if len(screenshot_base64) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
        raise LocalOCRError("Local OCR image exceeds the size limit")
    try:
        data = base64.b64decode(screenshot_base64, validate=True)
        if len(data) > MAX_IMAGE_BYTES or not (data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff")):
            raise ValueError
        from PIL import Image
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as frame:
                if frame.format not in {"PNG", "JPEG"} or frame.size != (width, height):
                    raise ValueError
                frame.verify()
        return data
    except ImportError:
        raise LocalOCRError("Local OCR image validation requires Pillow") from None
    except Exception:
        raise LocalOCRError("Local OCR requires a valid PNG or JPEG matching the image dimensions") from None


def _words_from_tsv(output, width, height):
    if not isinstance(output, bytes) or len(output) > MAX_TSV_BYTES:
        raise LocalOCRError("Local OCR returned invalid or oversized data")
    try:
        reader = csv.DictReader(StringIO(output.decode("utf-8-sig")), delimiter="\t", quoting=csv.QUOTE_NONE)
        if (reader.fieldnames is None or len(reader.fieldnames) != len(_TSV_COLUMNS)
                or set(reader.fieldnames) != _TSV_COLUMNS):
            raise ValueError
        words = []
        for row in reader:
            if set(row) != _TSV_COLUMNS or any(value is None for value in row.values()):
                continue
            try:
                if int(row["level"]) != 5:
                    continue
                confidence = float(row["conf"])
                if not math.isfinite(confidence) or not 50 <= confidence <= 100:
                    continue
                left, top, box_width, box_height = (int(row[key]) for key in ("left", "top", "width", "height"))
                right, bottom = left + box_width, top + box_height
                if not (0 <= left < right <= width and 0 <= top < bottom <= height):
                    continue
                text = " ".join(row["text"].split())[:MAX_TEXT_LENGTH]
                if not text:
                    continue
            except (ValueError, TypeError, OverflowError):
                continue
            words.append({"text": text, "bounds": (left, top, right, bottom), "confidence": confidence})
            if len(words) >= MAX_WORDS:
                break
        return words
    except (UnicodeError, csv.Error, ValueError, TypeError):
        raise LocalOCRError("Local OCR returned invalid TSV data") from None


def _prepare_image(data, width, height):
    """Improve small light/dark UI text without changing the caller's image.

    Three-times enlargement is a ceiling, not a requirement. Large captures are
    resized downward when necessary to respect the processed pixel/side caps.
    """
    try:
        from PIL import Image, ImageOps, ImageStat
        scale = min(3.0, MAX_PROCESSED_SIDE / max(width, height),
                    math.sqrt(MAX_PROCESSED_PIXELS / (width * height)))
        processed_width = max(1, math.floor(width * scale))
        processed_height = max(1, math.floor(height * scale))
        with ExitStack() as resources:
            source = resources.enter_context(Image.open(BytesIO(data)))
            gray = resources.enter_context(source.convert("L"))
            if ImageStat.Stat(gray).mean[0] < 127:
                gray = resources.enter_context(ImageOps.invert(gray))
            if gray.size != (processed_width, processed_height):
                gray = resources.enter_context(gray.resize((processed_width, processed_height), Image.Resampling.LANCZOS))
            output = resources.enter_context(BytesIO())
            gray.save(output, format="PNG", compress_level=1)
            return output.getvalue(), processed_width, processed_height
    except Exception:
        raise LocalOCRError("Local OCR could not prepare the image") from None


def _map_words(words, processed_width, processed_height, width, height):
    """Map validated processed rectangles outward, using each actual axis scale."""
    mapped = []
    for word in words:
        left, top, right, bottom = word["bounds"]
        bounds = (left * width // processed_width, top * height // processed_height,
                  (right * width + processed_width - 1) // processed_width,
                  (bottom * height + processed_height - 1) // processed_height)
        mapped.append({**word, "bounds": bounds})
    return mapped


def _merge_words(existing, additions):
    """Dedupe equal words whose rectangles overlap substantially, preserving order."""
    result = list(existing)
    for word in additions:
        left, top, right, bottom = word["bounds"]
        area = (right - left) * (bottom - top)
        for index, prior in enumerate(result):
            if prior["text"] != word["text"]:
                continue
            pleft, ptop, pright, pbottom = prior["bounds"]
            intersection = max(0, min(right, pright) - max(left, pleft)) * max(0, min(bottom, pbottom) - max(top, ptop))
            prior_area = (pright - pleft) * (pbottom - ptop)
            # Intersection-over-union avoids collapsing adjacent repeated digits
            # whose outward-rounded boxes merely share a one-pixel edge strip.
            if intersection > 0 and intersection / (area + prior_area - intersection) >= 0.5:
                if word["confidence"] > prior["confidence"]:
                    result[index] = word
                break
        else:
            if len(result) < MAX_WORDS:
                result.append(word)
    return result


def _run_tesseract(argv, data, timeout):
    """Drain pipes concurrently with hard byte ceilings, not communicate().

    Reader buffers remain bounded even when a process emits excessive output
    before its deadline. Cleanup kills the process and joins pipe workers with
    a short bounded grace period; diagnostics never contain child output.
    """
    deadline = time.monotonic() + timeout
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               shell=False, bufsize=0, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    stopped, failed, overflow = threading.Event(), threading.Event(), threading.Event()
    completed = [threading.Event() for _ in range(3)]
    output = bytearray()

    def read_pipe(stream, limit, keep, done):
        count = 0
        try:
            while not stopped.is_set():
                chunk = stream.read(min(65536, limit - count + 1))
                if not chunk:
                    return
                count += len(chunk)
                if count > limit:
                    overflow.set()
                    stopped.set()
                    return
                if keep:
                    output.extend(chunk)
        except (OSError, ValueError):
            if not stopped.is_set():
                failed.set()
                stopped.set()
        finally:
            done.set()

    def write_input():
        try:
            view, offset = memoryview(data), 0
            while offset < len(view) and not stopped.is_set():
                written = process.stdin.write(view[offset:offset + 65536])
                if not written:
                    raise OSError
                offset += written
        except (OSError, ValueError):
            failed.set()
            stopped.set()
        finally:
            with suppress(OSError, ValueError):
                process.stdin.close()
            completed[2].set()

    workers = [threading.Thread(target=read_pipe, args=(process.stdout, MAX_TSV_BYTES, True, completed[0]), daemon=True),
               threading.Thread(target=read_pipe, args=(process.stderr, MAX_STDERR_BYTES, False, completed[1]), daemon=True),
               threading.Thread(target=write_input, daemon=True)]
    try:
        for worker in workers:
            worker.start()
        while True:
            if overflow.is_set():
                raise LocalOCRError("Local OCR exceeded its output limit")
            if failed.is_set():
                raise LocalOCRError("Local OCR process input or output failed")
            if process.poll() is not None and all(done.is_set() for done in completed):
                return subprocess.CompletedProcess(argv, process.returncode, stdout=bytes(output))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired("local-ocr", timeout)
            stopped.wait(min(remaining, 0.01))
    finally:
        stopped.set()
        if process.poll() is None:
            with suppress(OSError):
                process.kill()
        with suppress(OSError, subprocess.TimeoutExpired):
            process.wait(timeout=0.1)
        # Unbuffered streams avoid BufferedReader locks around blocked reads.
        for stream in (process.stdin, process.stdout, process.stderr):
            with suppress(OSError, ValueError):
                stream.close()
        for worker in workers:
            if worker.ident is not None:
                worker.join(timeout=0.03)


def extract_words(screenshot_base64: str, width: int, height: int) -> list[dict]:
    """Recognize up to 80 confident words, in unchanged physical image pixels.

    Word detections are not proof that a region is a button, editable, or safe.
    An empty list is a valid result for an image with no confident text.
    """
    deadline = time.monotonic() + OCR_TIMEOUT_SECONDS
    data = _image_bytes(screenshot_base64, width, height)
    executable = shutil.which("tesseract")
    if executable is None:
        raise LocalOCRError("Local OCR requires an installed Tesseract executable on PATH")
    processed, processed_width, processed_height = _prepare_image(data, width, height)
    words, valid_pass = [], False
    failure = "Local OCR failed to recognize the image"
    # PSM 6 finds short aligned UI labels/digits; PSM 11 finds sparse text.
    # Both are generic segmentation modes, not application-specific workflows.
    for mode in ("6", "11"):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            failure = "Local OCR exceeded its time limit"
            break
        try:
            completed = _run_tesseract([executable, "stdin", "stdout", "--psm", mode, "tsv"], processed, remaining)
            if completed.returncode != 0:
                continue
            detected = _words_from_tsv(completed.stdout, processed_width, processed_height)
            mapped = _map_words(detected, processed_width, processed_height, width, height)
            words = _merge_words(words, mapped)
            valid_pass = True
        except subprocess.TimeoutExpired:
            failure = "Local OCR exceeded its time limit"
            break
        except LocalOCRError:
            failure = "Local OCR returned invalid TSV data"
        except (OSError, ValueError, subprocess.SubprocessError):
            failure = "Local OCR could not start"
    if not valid_pass:
        raise LocalOCRError(failure)
    return words
