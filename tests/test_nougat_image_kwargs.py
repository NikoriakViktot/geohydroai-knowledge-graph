"""The processor kwarg set must be complete and coherent, or transformers 5.x
rejects it — this is what silently emptied every Nougat region of content."""
from __future__ import annotations

import pytest

from src.document.nougat_parser import NougatParser


class _SizeDict:
    """Stands in for transformers' SizeDict: convertible via dict(), not a dict."""

    def __init__(self, **fields):
        self._fields = fields

    def keys(self):
        return self._fields.keys()

    def __getitem__(self, key):
        return self._fields[key]


class _IP:
    do_crop_margin = True
    do_thumbnail = True
    do_align_long_axis = False
    do_resize = True
    size = None
    resample = 2
    do_rescale = True
    rescale_factor = 1 / 255
    do_normalize = True
    image_mean = (0.485, 0.456, 0.406)
    image_std = (0.229, 0.224, 0.225)
    do_pad = True
    unrelated = "ignored"


class _Processor:
    def __init__(self, ip):
        self.image_processor = ip


def _parser(ip):
    p = NougatParser.__new__(NougatParser)
    p._processor = _Processor(ip)
    return p


def test_every_flag_travels_with_its_companions():
    ip = _IP()
    ip.size = {"height": 896, "width": 672}
    kwargs = _parser(ip)._image_kwargs()
    for flag, companion in (("do_rescale", "rescale_factor"),
                            ("do_normalize", "image_mean"),
                            ("do_normalize", "image_std"),
                            ("do_resize", "size"),
                            ("do_resize", "resample")):
        if kwargs.get(flag):
            assert companion in kwargs, f"{flag} is set without {companion}"
    assert "unrelated" not in kwargs


def test_no_value_is_ever_none():
    """A None is exactly what the validator rejects."""
    ip = _IP()
    ip.size = {"height": 896, "width": 672}
    ip.image_std = None
    kwargs = _parser(ip)._image_kwargs()
    assert all(v is not None for v in kwargs.values())
    assert "image_std" not in kwargs


def test_margin_cropping_is_forced_off_whatever_the_config_says():
    ip = _IP()
    ip.size = {"height": 896, "width": 672}
    assert ip.do_crop_margin is True
    assert _parser(ip)._image_kwargs()["do_crop_margin"] is False


def test_size_is_normalised_to_a_plain_dict_without_none_entries():
    ip = _IP()
    ip.size = _SizeDict(height=896, width=672, longest_edge=None, max_height=None)
    size = _parser(ip)._image_kwargs()["size"]
    assert type(size) is dict
    assert size == {"height": 896, "width": 672}
