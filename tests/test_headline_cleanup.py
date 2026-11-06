"""Strip model preamble/reasoning from generated headlines.

Reasoning-capable models (gpt-oss, nemotron) narrate before answering. That
chatter was taken verbatim as the headline, so a card rendered:
  "We need 2-line headline about cleaning kit article. Provide 55-75 chars
   each line. Let's craft. Line1: "Compact Home Cleaning Kit Review..."
"""

import pytest

from app.services.openrouter_service import clean_headline_output

LEAKED = (
    "We need 2-line headline about cleaning kit article. Provide 55-75 chars "
    "each line. Let's craft.\n"
    'Line1: "Compact Home Cleaning Kit Review: 7 Space-Saving Solutions Ranked —"\n'
    'Line2: "Reviewers Say One Pick Beats Them All"'
)


def test_extracts_labelled_lines_and_drops_the_preamble():
    out = clean_headline_output(LEAKED, max_lines=2)
    assert "We need" not in out
    assert "Let's craft" not in out
    assert "Line1:" not in out and "Line2:" not in out
    assert out.startswith("Compact Home Cleaning Kit Review")
    assert len(out.split("\n")) == 2


@pytest.mark.parametrize("prefix", [
    "Here is the headline:",
    "Here's a 2-line headline:",
    "Sure! Here you go:",
    "**Headline:**",
    "Output:",
])
def test_drops_common_lead_ins(prefix):
    out = clean_headline_output(f"{prefix}\nReal Headline Line One\nReal Line Two", max_lines=2)
    # The lead-in line is gone entirely; only the two real lines remain.
    assert out == "Real Headline Line One\nReal Line Two"


def test_strips_think_blocks():
    raw = "<think>I should make it punchy and under 75 chars.</think>\nActual Headline\nSecond Line"
    out = clean_headline_output(raw, max_lines=2)
    assert "punchy" not in out
    assert out.startswith("Actual Headline")


def test_unwraps_quoted_lines():
    out = clean_headline_output('"Quoted Line One"\n"Quoted Line Two"', max_lines=2)
    assert out == "Quoted Line One\nQuoted Line Two"


def test_plain_output_is_untouched():
    """A well-behaved model must pass through unchanged."""
    raw = "Compact Cleaning Kit Ranked\nReviewers Pick a Clear Winner"
    assert clean_headline_output(raw, max_lines=2) == raw


def test_preamble_only_returns_empty_so_caller_can_error():
    assert clean_headline_output("Let me think about this.", max_lines=2) == ""


def test_strips_character_count_meta_and_repeated_phrase():
    """gpt-oss style: repeats the hook then narrates character counting."""
    raw = (
        '**Science diplomacy** unites nations today Celebrating World Science Day '
        'for peace **Common ground** sparks global collaboration '
        '**Science diplomacy** unites nations today" length: 2 asterisks count as '
        'characters. Count characters: "**Science diplomacy**" ='
    )
    out = clean_headline_output(raw, max_lines=3)
    assert "length:" not in out.lower()
    assert "asterisks" not in out.lower()
    assert "count characters" not in out.lower()
    assert out.count("Science diplomacy") == 1
    assert "Common ground" in out
    assert len(out) < 160


def test_splits_em_dash_into_two_lines():
    raw = "Rookie Season Ends Without ROY — Fans Debate the Snub"
    out = clean_headline_output(raw, max_lines=2)
    assert "\n" in out
    assert out.split("\n")[0].rstrip().endswith("—")
