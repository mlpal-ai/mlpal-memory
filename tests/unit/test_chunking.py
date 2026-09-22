"""memory v7 WP14: chunking is line-aware inside oversize paragraphs, with overlap; paragraphs still pack."""

from mlpal_memory_graph.services.direct import chunk_text, split_turns


def test_transcript_lines_are_never_cut_mid_turn_and_overlap_one_line(monkeypatch):
    from mlpal_memory_graph.core.config import get_settings
    monkeypatch.setattr(get_settings(), "chunk_mode", "line")
    turns = [f"user: message number {i} " + ("x" * 150) for i in range(20)]
    text = "\n".join(turns)  # one paragraph, ~3,400 chars
    chunks = chunk_text(text, max_chars=1000, overlap_lines=1)
    assert len(chunks) >= 4
    for c in chunks:
        assert len(c) <= 1000
        for ln in c.split("\n"):
            assert ln in turns, "a chunk boundary cut a turn"
    # the last line of a chunk opens the next one
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert b.split("\n")[0] == a.split("\n")[-1]


def test_paragraph_mode_is_the_default_and_keeps_fixed_cuts():
    long_para = "\n".join(f"user: line {i} " + "x" * 120 for i in range(30))
    chunks = chunk_text(long_para, max_chars=1000)
    assert all(len(c) == 1000 for c in chunks[:-1]), "paragraph mode cuts at the cap"


def test_paragraph_packing_is_unchanged_and_no_overlap_when_zero():
    paras = ["para one " * 20, "para two " * 20, "para three " * 20]
    chunks = chunk_text("\n\n".join(paras), max_chars=500, overlap_lines=0)
    assert all(len(c) <= 500 for c in chunks) and chunks[0].startswith("para one")
    long_line = "y" * 2500
    assert [len(c) for c in chunk_text(long_line, max_chars=1000, overlap_lines=1)] == [1000, 1000, 500]


def test_turn_mode_packs_whole_turns_and_never_splits_one(monkeypatch):
    from mlpal_memory_graph.core.config import get_settings
    monkeypatch.setattr(get_settings(), "chunk_mode", "turn")
    turns = [f"{'user' if i % 2 == 0 else 'assistant'}: turn {i} " + "x" * 200 for i in range(16)]
    chunks = chunk_text("\n".join(turns), max_chars=1000)
    assert len(chunks) >= 4
    for c in chunks:
        assert len(c) <= 1000
        assert c.split(":", 1)[0] in ("user", "assistant"), "every chunk opens with a speaker"
        for ln in c.split("\n"):
            assert ln in turns, "a turn was cut"


def test_turn_mode_re_prefixes_continuation_pieces_of_a_long_assistant_turn(monkeypatch):
    from mlpal_memory_graph.core.config import get_settings
    from mlpal_memory_graph.services.direct import has_user_turn
    monkeypatch.setattr(get_settings(), "chunk_mode", "turn")
    body = "\n".join(f"{i}. **Tip {i}**: " + "y" * 120 for i in range(1, 30))  # ≈ 4,000 chars, one assistant turn
    text = "user: any tips for my garden?\nassistant: sure, here are some:\n" + body + "\nuser: thanks, and the Plesiosaur?"
    chunks = chunk_text(text, max_chars=1000)
    assert chunks[0].startswith("user: any tips")
    conts = [c for c in chunks if c.startswith("assistant: (continued) ")]
    assert len(conts) >= 3, "continuation pieces carry the speaker"
    assert all(not has_user_turn(c) for c in conts) and all(len(c) <= 1000 for c in chunks)
    assert chunks[-1].startswith("user: thanks") or "user: thanks" in chunks[-1]


def test_split_turns_uses_names_when_no_roles_and_ignores_one_off_heads():
    text = "Caroline: hi\nMelanie: hello\nNote: this is not a speaker\nCaroline: bye\nMelanie: bye"
    turns = split_turns(text)
    assert [t[0] for t in turns] == ["Caroline", "Melanie", "Caroline", "Melanie"]
    assert turns[1][1] == ["Melanie: hello", "Note: this is not a speaker"]
