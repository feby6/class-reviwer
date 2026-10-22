"""media.py: parsing ffmpeg output and deciding whether a file can be reviewed."""
from reviewer.media import parse_detect, verdict

STDERR = """
[blackdetect @ 0x1] black_start:0 black_end:30 black_duration:30
[blackdetect @ 0x1] black_start:100 black_end:120.5 black_duration:20.5
[silencedetect @ 0x2] silence_start: 10
[silencedetect @ 0x2] silence_end: 25 | silence_duration: 15
"""
OK = {"duration_s": 3600.0, "has_video": True, "has_audio": True}


def test_parse_detect():
    assert parse_detect(STDERR) == (50.5, 15.0)


def test_verdict():
    assert verdict(OK, 50, 15) == []
    assert verdict({**OK, "has_audio": False}, 0, 0) == ["no audio stream"]
    assert verdict({**OK, "duration_s": 60}, 0, 0) == ["too short (60s)"]
    assert verdict({**OK, "duration_s": 0}, 0, 0) == ["too short (0s)"]   # no division by zero
    assert "mostly black" in verdict(OK, 3000, 0)[0]
    assert "mostly silent" in verdict(OK, 0, 3000)[0]
