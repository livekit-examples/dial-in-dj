from agent import _clean_tags


def test_splits_tags_packed_into_one_string():
    packed = ['french house", "synth", "disco", "upbeat']
    assert _clean_tags(packed) == ["french house", "synth", "disco", "upbeat"]


def test_keeps_clean_tags_and_drops_empty_ones():
    assert _clean_tags([" Rhodes piano ", "dark, moody", ""]) == [
        "Rhodes piano",
        "dark",
        "moody",
    ]


def test_featured_layer_gets_a_real_share():
    from agent import layer_mix

    mix = {"disco": 1.0, "electric bass": 0.85, "strings": 0.7, "funky": 0.55}
    out = layer_mix(mix, "Alto Saxophone", "featured")
    assert out["Alto Saxophone"] / sum(out.values()) >= 0.34


def test_lead_layer_is_half_and_mix_is_capped():
    from agent import layer_mix

    mix = {"hard rock": 1.0, "a": 0.9, "b": 0.8, "c": 0.3, "d": 0.5}
    out = layer_mix(mix, "Cello", "lead")
    assert len(out) <= 5 and "hard rock" in out and "c" not in out
    assert abs(out["Cello"] / sum(out.values()) - 0.5) < 0.01
