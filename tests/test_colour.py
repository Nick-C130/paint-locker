import pytest

from app.colour import delta_e_2000, normalise_hex, oklab_mix, spectral_mix


@pytest.mark.parametrize(
    "a,b,fa,fb,expected",
    [
        # Reference values produced by spectral.js v3.0.0 in node
        ("#002185", "#FCD200", 1, 1, "#3D933E"),
        ("#FF0000", "#FFFFFF", 1, 3, "#FF869B"),
        ("#1E4F39", "#E7A221", 2, 1, "#386E37"),
    ],
)
def test_spectral_mix_matches_spectral_js(a, b, fa, fb, expected):
    assert spectral_mix([(a, fa), (b, fb)]) == expected


def test_blue_and_yellow_make_green_not_grey():
    mixed = spectral_mix([("#002185", 1), ("#FCD200", 1)])
    r, g, b = (int(mixed[i : i + 2], 16) for i in (1, 3, 5))
    assert g > r and g > b


def test_single_component_and_empty():
    assert spectral_mix([("#123456", 3)]) == "#123456"
    assert spectral_mix([]) is None
    assert spectral_mix([("#123456", 0)]) is None
    assert oklab_mix([("#000000", 1), ("#FFFFFF", 1)]) is not None


@pytest.mark.parametrize(
    "lab1,lab2,expected",
    [
        # Sharma, Wu & Dalal (2005) test data
        ((50, 2.6772, -79.7751), (50, 0, -82.7485), 2.0425),
        ((50, 2.5, 0), (73, 25, -18), 27.1492),
        ((50, 2.5, 0), (50, 3.1736, 0.5854), 1.0000),
        ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
    ],
)
def test_ciede2000(lab1, lab2, expected):
    assert delta_e_2000(lab1, lab2) == pytest.approx(expected, abs=1e-4)


def test_normalise_hex():
    assert normalise_hex("abc") == "#AABBCC"
    assert normalise_hex("#a1b2c3") == "#A1B2C3"
    assert normalise_hex("nope") is None
    assert normalise_hex("") is None
