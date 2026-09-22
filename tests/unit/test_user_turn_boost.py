"""memory v8 C1/C6 (LEARNINGS L18, L30–L33): the speaker boost after fusion; 1.3 in question mode by default."""

from mlpal_memory_graph.services.direct import apply_user_turn_boost, has_assistant_turn, has_user_turn, speaker_for_question


def test_user_turn_is_line_anchored():
    assert has_user_turn("user: I still need to pick up my boots")
    assert has_user_turn("assistant: sure\nHuman: and the blazer")
    assert not has_user_turn("assistant: the user: label appears mid-line here")
    assert not has_user_turn("1. **Create a To-Pickup list**: write it down")
    assert not has_user_turn("")


def test_boost_off_returns_the_same_mapping():
    fused = {"a": 0.5, "b": 0.4}
    assert apply_user_turn_boost(fused, lambda _: "user: x", 1.0) is fused


def test_boost_reorders_a_user_passage_past_assistant_prose():
    content = {"a": "assistant: 1. Utilize the back of the door", "b": "user: by the way, I still need to return the boots"}
    fused = {"a": 0.50, "b": 0.45}
    out = apply_user_turn_boost(fused, content.__getitem__, 1.3)
    assert out["a"] == 0.50 and abs(out["b"] - 0.585) < 1e-9
    assert max(out, key=out.get) == "b"


def test_default_is_c6():
    from mlpal_memory_graph.core.config import get_settings

    assert float(get_settings().direct_user_turn_boost) == 1.3


def test_question_about_the_assistant_targets_assistant_turns():
    for q in ("I'm going back to our previous conversation about the children's book on dinosaurs. What colour was the Plesiosaur?",
              "You told me about the refining processes at the plant. Which one was the cheapest?",
              "What did you suggest for my sourdough starter?", "Can you remind me of your recommendations for Amsterdam?",
              "We discussed shift rotations earlier; which pattern did you say works for nurses?"):
        assert speaker_for_question(q) == "assistant", q
    for q in ("How many projects have I led or am currently leading?", "Where did I go on my most recent family trip?",
              "What is the total amount I spent on luxury items?", "Can you suggest a hotel for my upcoming trip to Miami?", None, ""):
        assert speaker_for_question(q) == "user", q


def test_assistant_boost_reorders_an_assistant_passage_past_a_user_aside():
    # "a" is a continuation chunk of a long assistant turn: no speaker line at all (L32)
    content = {"a": "3. **The Plesiosaur** had a blue scaly body and a long neck", "b": "user: I'm writing a children's book on dinosaurs"}
    assert has_assistant_turn(content["a"]) and not has_assistant_turn(content["b"])
    assert has_assistant_turn("assistant: sure, here are three ideas")
    fused = {"a": 0.45, "b": 0.50}
    out = apply_user_turn_boost(fused, content.__getitem__, 1.3, speaker="assistant")
    assert max(out, key=out.get) == "a" and out["b"] == 0.50


def test_default_mode_is_question():
    from mlpal_memory_graph.core.config import get_settings

    assert get_settings().direct_speaker_boost_mode == "question"
