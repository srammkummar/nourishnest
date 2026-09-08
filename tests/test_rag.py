import pytest

from nourish_nest.rag import chunk_text


def test_chunking_preserves_metadata_and_overlap():
    chunks = chunk_text("one two three four five six seven", "recipe-1", max_words=4, overlap_words=1)
    assert [chunk.chunk_id for chunk in chunks] == ["recipe-1:0", "recipe-1:1"]
    assert chunks[0].text == "one two three four"
    assert chunks[1].text == "four five six seven"


def test_invalid_chunk_settings_fail_fast():
    with pytest.raises(ValueError):
        chunk_text("content", "source", max_words=10, overlap_words=10)

