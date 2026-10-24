import models

RAW = [
    {"id": "a/vision:free", "name": "A", "created": 1792000000, "context_length": 1000,
     "pricing": {"prompt": "0", "completion": "0"},
     "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]}},
    {"id": "b/text:free", "name": "B", "created": 1791000000, "context_length": 2000,
     "pricing": {"prompt": "0", "completion": "0"},
     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}},
    {"id": "c/paid", "name": "C", "pricing": {"prompt": "0.000001", "completion": "0"},
     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}},
    {"id": "d/image-gen:free", "name": "D", "pricing": {"prompt": "0", "completion": "0"},
     "architecture": {"input_modalities": ["text"], "output_modalities": ["image"]}},
]


def test_summarize_keeps_only_free_text_output_models():
    result = models.summarize(RAW)
    assert [m["id"] for m in result] == ["a/vision:free", "b/text:free"]
    assert result[0]["vision"] and not result[1]["vision"]
    assert result[0]["created"] == "2026-10-14"


def test_build_chain_defaults_when_nothing_selected():
    assert models.build_chain([], ["x", "y"]) == ["x", "y"]
    assert models.build_chain(None, ["x"]) == ["x"]


def test_build_chain_puts_default_models_first_and_caps_length():
    chain = models.build_chain(["n1", "y", "n2", "n3", "n4"], ["x", "y"])
    assert chain == ["y", "n1", "n2", "n3"]
    assert len(chain) == models.MAX_CHAIN
