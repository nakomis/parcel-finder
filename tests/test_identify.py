from parcel_finder import identify
from parcel_finder.identify import is_informative, match_rule, tokens
from parcel_finder.runlog import RunLog

SD_OCR = "3V\nCLK D0 GMD D3 D1\nDET\nGND\nSO\nST\nCS\nDAT2"


def test_tokens_fold_ocr_typos():
    assert {"CMD", "SI", "D0", "D1", "DAT2"} <= tokens("CLK D0 GMD D3 D1\nST\nDAT2")


def test_rule_sdio_breakout():
    assert match_rule(tokens(SD_OCR)) == "microSD breakout, SPI + 4-bit SDIO"


def test_rule_spi_only():
    assert match_rule({"CS", "MISO", "MOSI", "SCK", "GND", "VCC"}) == "microSD breakout, SPI only"


def test_no_rule_for_usb_tester():
    assert match_rule(tokens("Type-C\nD+ D- VCC GND\nCC R1 R2\nLightning\nMicro")) is None


def test_generic_words_are_not_informative():
    assert not is_informative("PWR\nRESET\nBOOT")
    assert not is_informative(None)
    assert is_informative(SD_OCR)


def test_identify_uses_rule_without_haiku(monkeypatch, tmp_path, photo_fixture):
    monkeypatch.setattr(identify, "ocr", lambda p: SD_OCR)
    monkeypatch.setattr(identify.haiku, "ask_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no Haiku")))
    r = identify.identify(photo_fixture, RunLog.start(photo_fixture, tmp_path))
    assert r.source == "rule" and r.item == "microSD breakout, SPI + 4-bit SDIO" and r.confidence == "high"


def test_identify_passes_informative_ocr_to_haiku(monkeypatch, tmp_path, photo_fixture):
    seen = {}
    monkeypatch.setattr(identify, "ocr", lambda p: "Type-C\nD+ D- VCC GND\nCC R1 R2\nLightning\nMicro")
    monkeypatch.setattr(identify.haiku, "ask_json",
                        lambda prompt, images, schema, **k: seen.setdefault("p", prompt) and
                        {"name": "USB cable tester", "item": "USB cable tester", "confidence": "medium", "evidence": "labels"})
    r = identify.identify(photo_fixture, RunLog.start(photo_fixture, tmp_path))
    assert r.source == "haiku" and "Lightning" in seen["p"]


def test_identify_hides_uninformative_ocr(monkeypatch, tmp_path, photo_fixture):
    seen = {}
    monkeypatch.setattr(identify, "ocr", lambda p: "PWR\nRESET\nBOOT")
    monkeypatch.setattr(identify.haiku, "ask_json",
                        lambda prompt, images, schema, **k: seen.setdefault("p", prompt) and
                        {"name": "RP2040 board", "item": "RP2040 board", "confidence": "medium", "evidence": "logo"})
    r = identify.identify(photo_fixture, RunLog.start(photo_fixture, tmp_path))
    assert r.source == "haiku-image-only" and "RESET" not in seen["p"]


def test_identify_without_ocr(monkeypatch, tmp_path, photo_fixture):   # Review Focus 4
    monkeypatch.setattr(identify, "ocr", lambda p: None)
    monkeypatch.setattr(identify.haiku, "ask_json",
                        lambda prompt, images, schema, **k: {"name": "USB tester", "item": "USB tester", "confidence": "medium", "evidence": "labels"})
    r = identify.identify(photo_fixture, RunLog.start(photo_fixture, tmp_path))
    assert r.source == "haiku-image-only" and r.ocr_text is None


def test_ocr_returns_none_when_ollama_down(monkeypatch, photo_fixture):
    monkeypatch.setattr(identify.config, "OLLAMA_URL", "http://127.0.0.1:9")
    assert identify.ocr(photo_fixture) is None



def _jpeg(path, w, h):
    """A real w×h JPEG, made with sips from a 1×1 PNG."""
    import subprocess
    png = path.with_suffix(".png")
    png.write_bytes(bytes.fromhex("89504e470d0a1a0a0000000d494844520000000100000001080600"
                                  "00001f15c4890000000d49444154789c6360f8ffff3f0005fe02fea7"
                                  "d6a45b0000000049454e44ae426082"))
    subprocess.run(["sips", "-s", "format", "jpeg", "-z", str(h), str(w), str(png), "--out", str(path)],
                   capture_output=True, check=True)
    return path


def _dims(path):
    import subprocess
    out = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
                         capture_output=True, text=True).stdout.split()
    return int(out[out.index("pixelWidth:") + 1]), int(out[out.index("pixelHeight:") + 1])


def test_ocr_sends_a_downscaled_image(monkeypatch, tmp_path):   # ScreenEye saves 2160×3840; GLM-OCR overflows
    import base64, io, json
    big = _jpeg(tmp_path / "big.jpg", 2160, 3840)
    sent = tmp_path / "sent.jpg"

    def urlopen(req, timeout):
        sent.write_bytes(base64.b64decode(json.loads(req.data)["images"][0]))
        return io.BytesIO(b'{"response": "CLK"}')
    monkeypatch.setattr(identify.urllib.request, "urlopen", urlopen)
    assert identify.ocr(big) == "CLK"
    assert max(_dims(sent)) <= identify.OCR_MAX_DIM


def test_haiku_gives_short_name_and_description(monkeypatch, tmp_path, photo_fixture):
    seen = {}
    monkeypatch.setattr(identify, "ocr", lambda p: None)
    monkeypatch.setattr(identify.haiku, "ask_json", lambda prompt, images, schema, **k: seen.setdefault("s", schema) and {
        "name": "SOP-24 to DIP adapter board", "item": "Green PCB breakout adapter for a 24-pin SOP...",
        "confidence": "high", "evidence": "SOP24PIN"})
    r = identify.identify(photo_fixture, RunLog.start(photo_fixture, tmp_path))
    assert r.item == "SOP-24 to DIP adapter board" and r.description.startswith("Green PCB")
    assert "name" in seen["s"]["required"]


def test_search_terms_use_name_and_distinctive_ocr_words():
    ident = identify.Identification("SOP-24 to DIP adapter board", "high", "", "haiku",
                                    "1 SOP24PIN\n24\n23\n22981A\n11 1.27MM\nGND", "long description")
    q = identify.search_terms(ident)
    assert q.startswith("SOP-24 to DIP adapter board") and "SOP24PIN" in q and "1.27MM" in q
    assert "GND" not in q and " 24 " not in f" {q} " and len(q) < 120
