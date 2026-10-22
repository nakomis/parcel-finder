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
                        {"item": "USB cable tester", "confidence": "medium", "evidence": "labels"})
    r = identify.identify(photo_fixture, RunLog.start(photo_fixture, tmp_path))
    assert r.source == "haiku" and "Lightning" in seen["p"]


def test_identify_hides_uninformative_ocr(monkeypatch, tmp_path, photo_fixture):
    seen = {}
    monkeypatch.setattr(identify, "ocr", lambda p: "PWR\nRESET\nBOOT")
    monkeypatch.setattr(identify.haiku, "ask_json",
                        lambda prompt, images, schema, **k: seen.setdefault("p", prompt) and
                        {"item": "RP2040 board", "confidence": "medium", "evidence": "logo"})
    r = identify.identify(photo_fixture, RunLog.start(photo_fixture, tmp_path))
    assert r.source == "haiku-image-only" and "RESET" not in seen["p"]


def test_identify_without_ocr(monkeypatch, tmp_path, photo_fixture):   # Review Focus 4
    monkeypatch.setattr(identify, "ocr", lambda p: None)
    monkeypatch.setattr(identify.haiku, "ask_json",
                        lambda prompt, images, schema, **k: {"item": "USB tester", "confidence": "medium", "evidence": "labels"})
    r = identify.identify(photo_fixture, RunLog.start(photo_fixture, tmp_path))
    assert r.source == "haiku-image-only" and r.ocr_text is None


def test_ocr_returns_none_when_ollama_down(monkeypatch, photo_fixture):
    monkeypatch.setattr(identify.config, "OLLAMA_URL", "http://127.0.0.1:9")
    assert identify.ocr(photo_fixture) is None
