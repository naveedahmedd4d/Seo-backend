import base64
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.technology_ai_infographics import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    EYEBROW,
    ICONS,
    TARGET_INFOGRAPHIC_COUNT,
    TEMPLATES,
    add_infographics_to_article,
    extraction_prompt,
    fallback_syntax,
    image_tag,
    insert_at_section_end,
    insert_infographics,
    normalize_icons,
    parse_candidates,
    parse_items,
    render_infographics,
    render_spec,
    resolve_heading,
    shorten_overlong,
    structural_candidates,
    syntax_issues,
    validate_candidates,
)
from app.services.infographic_storage import resolve_infographic_path, save_png_base64


@pytest.fixture(autouse=True)
def _render_service_configured(monkeypatch):
    # Rendering is mocked in these tests, but the pipeline only runs when it's configured;
    # don't depend on the developer's .env.
    from app.config import settings
    monkeypatch.setattr(settings, "frontend_url", "http://frontend.test")
    monkeypatch.setattr(settings, "infographic_render_secret", "test-secret")

BODY = (
    "## Choosing a Workflow\n\nMeasure baseline latency before switching tools. "
    "Errors dropped 42% after the change.\n\n"
    "## Rollout Steps\n\nPlan, pilot, then scale.\n\n"
    "## Strategy Options\n\n"
    "### Rehosting\n\nMove as-is.\n\n"
    "### Refactoring\n\nRedesign for scale.\n\n"
    "## Readiness Questions\n\nAsk these first.\n\n"
    "## Conclusion\n\nReview results monthly."
)

# 1x1 transparent PNG
TINY_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
ICON_URL = "ref:remote:https://api.iconify.design/mdi/"

STEPS_SYNTAX = (
    "infographic steps\n"
    "data\n"
    "  sequences\n"
    "    - label Plan the Change\n"
    "      desc Set a latency baseline\n"
    "    - label Run a Pilot\n"
    "      desc Test with one team\n"
    "    - label Scale Up\n"
    "      desc Expand once errors drop"
)

CHECKLIST_SYNTAX = (
    "infographic checklist\n"
    "data\n"
    "  lists\n"
    "    - label Is your data clean?\n"
    "    - label Can your stack host it?\n"
    "    - label Is the team trained?"
)

COMPARISON_SYNTAX = (
    "infographic comparison\n"
    "data\n"
    "  lists\n"
    "    - label Rehosting\n"
    "      desc Move the app as-is to new servers\n"
    "    - label Refactoring\n"
    "      desc Redesign the app so it can scale"
)


def _candidate(heading="Rollout Steps", template_id="steps", syntax=STEPS_SYNTAX,
               headline="How to roll out the workflow", subtitle="Plan, pilot, then scale the change.",
               alt_text="Three-step rollout plan", eyebrow="Workflows · Rollout"):
    return {"heading": heading, "template_id": template_id, "syntax": syntax, "headline": headline,
            "subtitle": subtitle, "alt_text": alt_text, "eyebrow": eyebrow}


def _cards(n: int, icons=True, label="Option", words=("Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta")) -> str:
    rows = "".join(
        f"    - label {label} {words[i]}\n      desc Short supporting text\n" + ("      icon cloud\n" if icons else "")
        for i in range(n)
    )
    return f"infographic cards\ndata\n  lists\n{rows}".rstrip()


# --- Parsing ---

def test_parse_candidates_requires_frame_fields_and_whitelisted_template():
    raw = json.dumps({"infographics": [
        _candidate(),
        {**_candidate(), "headline": ""},
        {**_candidate(), "template_id": "relation-dagre-flow"},
        {"heading": "Missing fields"},
    ]})
    candidates = parse_candidates(raw)
    assert len(candidates) == 1
    assert candidates[0]["headline"] == "How to roll out the workflow"
    assert candidates[0]["eyebrow"] == "Workflows · Rollout"


def test_parse_candidates_tolerates_missing_eyebrow_and_non_list():
    raw = {**_candidate()}
    del raw["eyebrow"]
    assert parse_candidates(json.dumps({"infographics": [raw]}))[0]["eyebrow"] == ""
    assert parse_candidates(json.dumps({"infographics": "not a list"})) == []


def test_parse_items_reads_top_level_items_for_html_templates():
    stats = (
        "infographic stats\ndata\n  values\n"
        "    - label Error rate\n      value 42%\n      desc After the change\n"
        "    - label Teams\n      value 3"
    )
    assert parse_items(stats) == [
        {"label": "Error rate", "desc": "After the change", "icon": "", "value": "42%"},
        {"label": "Teams", "desc": "", "icon": "", "value": "3"},
    ]
    icons = parse_items(normalize_icons("cards", _cards(2)))
    assert icons[0]["icon"] == f"https://api.iconify.design/mdi/{ICONS['cloud']}.svg"


def test_render_spec_sends_items_for_html_and_syntax_for_diagrams():
    html = render_spec("steps", STEPS_SYNTAX)
    assert html["kind"] == "html" and html["template"] == "steps" and len(html["items"]) == 3
    tree = TEMPLATES["hierarchy-tree"]["example"]
    assert render_spec("hierarchy-tree", tree) == {"kind": "antv", "template": "hierarchy-tree", "syntax": tree}


# --- Template rules ---

def test_every_template_example_passes_its_own_rules():
    for template_id, info in TEMPLATES.items():
        if template_id in ("chart-bar-plain-text", "stats"):
            continue  # their examples use placeholders on purpose
        assert syntax_issues(template_id, info["example"]) == [], template_id


def test_generic_labels_are_rejected():
    issues = syntax_issues("steps", STEPS_SYNTAX.replace("Plan the Change", "Step 1"))
    assert any("generic label" in i for i in issues)


def test_wheel_needs_short_labels_and_a_center_title():
    wheel = TEMPLATES["list-sector"]["example"]
    assert any("label too long" in i for i in syntax_issues("list-sector", wheel.replace("Identity", "Personalization")))
    assert "missing center title" in syntax_issues("list-sector", wheel.replace("  title Zero Trust\n", ""))


def test_item_counts_follow_each_layout():
    assert syntax_issues("cards", _cards(2)) == []
    assert syntax_issues("cards", _cards(6)) == []
    assert any("don't fit" in i for i in syntax_issues("cards", _cards(7)))
    assert any("don't fit" in i for i in syntax_issues("comparison", COMPARISON_SYNTAX + "\n    - label Third\n      desc Another way"))


def test_checklist_items_do_not_need_descriptions_but_cards_do():
    assert syntax_issues("checklist", CHECKLIST_SYNTAX) == []
    assert any("missing descriptions" in i for i in syntax_issues("cards", _cards(3).replace("      desc Short supporting text\n", "", 1)))


def test_stats_need_a_short_numeric_value_per_item():
    good = "infographic stats\ndata\n  values\n    - label Error drop\n      value 42%\n    - label Teams\n      value 3"
    assert syntax_issues("stats", good) == []
    assert any("value" in i for i in syntax_issues("stats", good.replace("value 3", "value Many")))
    assert any("value" in i for i in syntax_issues("stats", good.replace("\n      value 3", "")))


def test_syntax_issues_rejects_mismatched_template_line():
    assert syntax_issues("cards", STEPS_SYNTAX)


# --- Icons ---

def test_icon_names_map_to_exact_iconify_icons():
    out = normalize_icons("cards", _cards(3))
    assert out.count(f"icon {ICON_URL}{ICONS['cloud']}.svg") == 3


def test_unknown_or_missing_icons_fall_back_to_the_default_icon():
    syntax = _cards(3, icons=False).replace("desc Short supporting text\n", "desc Short supporting text\n      icon not-a-real-icon\n", 1)
    out = normalize_icons("cards", syntax)
    assert out.count("icon ref:remote:") == 3
    assert out.count(f"{ICONS['idea']}.svg") == 3


def test_missing_icons_are_guessed_from_the_item_text():
    syntax = (
        "infographic cards\ndata\n  lists\n"
        "    - label Answer Accuracy\n      desc Reviewed answers marked correct\n"
        "    - label User Satisfaction\n      desc Thumbs-up ratio\n"
        "    - label Data Redaction\n      desc Remove personal data\n      icon privacy."
    )
    out = normalize_icons("cards", syntax)
    icons = [l.strip() for l in out.split("\n") if l.strip().startswith("icon ")]
    assert icons == [f"icon {ICON_URL}{ICONS[k]}.svg" for k in ("check", "customer", "privacy")]
    assert out.index("Answer Accuracy") < out.index(ICONS["check"]) < out.index("User Satisfaction")


def test_model_supplied_urls_are_never_used_as_icons():
    syntax = _cards(3).replace("icon cloud", "icon ref:remote:https://evil.example/x.svg", 1)
    assert "evil.example" not in normalize_icons("cards", syntax)


def test_icons_are_removed_from_layouts_without_icon_slots():
    assert "icon" not in normalize_icons("steps", STEPS_SYNTAX + "\n      icon cloud")


# --- Validation and layout switching ---

def test_validate_candidates_normalizes_icons_and_strips_frame_titles():
    syntax = _cards(3).replace("data\n", "data\n  title Should Be Removed\n")
    survivors, _ = validate_candidates([_candidate(heading="Strategy Options", template_id="cards", syntax=syntax)], BODY)
    assert "title Should Be Removed" not in survivors[0]["syntax"]
    assert ICON_URL in survivors[0]["syntax"]


def test_three_item_wheel_becomes_icon_cards_without_its_center_title():
    wheel = TEMPLATES["list-sector"]["example"]
    wheel = wheel.replace("    - label Data\n      desc Encrypt at rest\n      icon lock", "").rstrip()
    survivors, _ = validate_candidates([_candidate(heading="Strategy Options", template_id="list-sector", syntax=wheel)], BODY)
    assert survivors[0]["template_id"] == "cards"
    assert "title Zero Trust" not in survivors[0]["syntax"]


def test_wheel_with_long_labels_switches_to_cards():
    wheel = TEMPLATES["list-sector"]["example"].replace("Identity", "Identity Verification")
    survivors, _ = validate_candidates([_candidate(heading="Strategy Options", template_id="list-sector", syntax=wheel)], BODY)
    assert survivors[0]["template_id"] == "cards"


def test_seven_cards_have_no_layout_and_are_rejected():
    assert validate_candidates([_candidate(heading="Strategy Options", template_id="cards", syntax=_cards(7))], BODY)[0] == []


def test_two_step_sequence_becomes_a_comparison():
    two = STEPS_SYNTAX.split("    - label Scale Up")[0].rstrip()
    survivors, _ = validate_candidates([_candidate(syntax=two)], BODY)
    assert survivors[0]["template_id"] == "comparison"
    assert "  lists\n" in survivors[0]["syntax"]


def test_three_stage_cycle_becomes_numbered_steps():
    cycle = (
        "infographic sequence-circular\ndata\n  sequences\n"
        "    - label Collect Feedback\n      desc Log failed answers\n      icon message\n"
        "    - label Retrain\n      desc Update the model\n      icon model\n"
        "    - label Redeploy\n      desc Ship behind a flag\n      icon launch"
    )
    survivors, _ = validate_candidates([_candidate(template_id="sequence-circular", syntax=cycle)], BODY)
    assert survivors[0]["template_id"] == "steps"
    assert "icon" not in survivors[0]["syntax"]


def test_long_headline_or_subtitle_is_rejected():
    assert validate_candidates([_candidate(headline="x" * 80)], BODY)[0] == []
    assert validate_candidates([_candidate(subtitle="x" * 130)], BODY)[0] == []


def test_filler_or_restating_subtitles_are_dropped():
    filler, _ = validate_candidates([_candidate(subtitle="Follow these steps to roll out safely.")], BODY)
    assert filler[0]["subtitle"] == ""
    restating, _ = validate_candidates([_candidate(subtitle="How to roll out the workflow.")], BODY)
    assert restating[0]["subtitle"] == ""
    kept, _ = validate_candidates([_candidate()], BODY)
    assert kept[0]["subtitle"] == "Plan, pilot, then scale the change."


def test_category_or_overlong_eyebrow_is_dropped():
    assert validate_candidates([_candidate(eyebrow="Technology & AI")], BODY)[0][0]["eyebrow"] == ""
    assert validate_candidates([_candidate(eyebrow="x" * 50)], BODY)[0][0]["eyebrow"] == ""


def test_invented_numbers_in_headline_are_rejected():
    survivors, warnings = validate_candidates([_candidate(headline="Cut errors by 75%")], BODY)
    assert survivors == []
    assert any("number not present" in w for w in warnings)


def test_validate_candidates_drops_unmatched_heading():
    survivors, warnings = validate_candidates([_candidate(heading="Nonexistent Section")], BODY)
    assert survivors == []
    assert any("not found" in w for w in warnings)


def test_paraphrased_headings_map_to_the_real_heading():
    body = "## Refactoring and Code Cleanup\n\nText.\n\n## Implementing Automated Testing\n\nText.\n\n## Data Pipeline\n\nText."
    assert resolve_heading("Key Steps for Refactoring Legacy Code", body) == "Refactoring and Code Cleanup"
    assert resolve_heading("Automated Testing Benefits", body) == "Implementing Automated Testing"
    assert resolve_heading("Data Security", body) is None
    assert resolve_heading("Completely Unrelated", body) is None


def test_prompt_lists_exact_headings_but_not_framing_sections():
    prompt = extraction_prompt("AI", BODY)
    assert "- Rollout Steps" in prompt and "- Rehosting" in prompt
    assert "- Conclusion" not in prompt
    assert '"eyebrow"' in prompt


def test_next_step_and_conclusion_sections_are_skipped():
    assert validate_candidates([_candidate(heading="Conclusion")], BODY)[0] == []


def test_graphics_repeating_another_graphics_items_are_skipped():
    same_items_as_checklist = (
        "infographic checklist\ndata\n  lists\n"
        "    - label Plan the change\n    - label Run a pilot\n    - label Scale up"
    )
    survivors, _ = validate_candidates(
        [_candidate(), _candidate(heading="Readiness Questions", template_id="checklist", syntax=same_items_as_checklist)], BODY
    )
    assert [s["heading"] for s in survivors] == ["Rollout Steps"]


def test_layout_variety_limits_checklists_and_repeats():
    other_checklist = CHECKLIST_SYNTAX.replace("clean", "labelled").replace("stack host it", "budget cover it").replace(
        "team trained", "owner named")
    survivors, _ = validate_candidates([
        _candidate(heading="Readiness Questions", template_id="checklist", syntax=CHECKLIST_SYNTAX),
        _candidate(heading="Choosing a Workflow", template_id="checklist", syntax=other_checklist),
    ], BODY)
    assert [s["heading"] for s in survivors] == ["Readiness Questions"]

    cards = [
        _candidate(heading=h, template_id="cards", syntax=_cards(3, label="", words=words))
        for h, words in (("Strategy Options", ("Cloud", "Edge", "Hybrid")), ("Rehosting", ("Servers", "Storage", "Network")),
                         ("Refactoring", ("Modules", "Services", "Tests")))
    ]
    survivors, _ = validate_candidates(cards, BODY)
    assert [s["heading"] for s in survivors] == ["Strategy Options", "Rehosting"]


def test_only_three_infographics_are_kept():
    candidates = [
        _candidate(),
        _candidate(heading="Strategy Options", template_id="comparison", syntax=COMPARISON_SYNTAX),
        _candidate(heading="Readiness Questions", template_id="checklist", syntax=CHECKLIST_SYNTAX),
        _candidate(heading="Choosing a Workflow", template_id="cards", syntax=_cards(3)),
    ]
    survivors, _ = validate_candidates(candidates, BODY)
    assert len(survivors) == TARGET_INFOGRAPHIC_COUNT == 3
    assert [s["template_id"] for s in survivors] == ["steps", "comparison", "checklist"]


# --- Charts and stats (regressions from the live test runs) ---

CHART_BODY = (
    "## Latency by Deployment Model\n\nMedian response time was 420 ms on-premise, "
    "260 ms hybrid, and 180 ms on a managed cloud endpoint.\n\n## Next Step\n\nBenchmark it."
)


def _chart(values):
    rows = "".join(f"    - label {label}\n      value {v}\n" for label, v in zip(("On-premise", "Hybrid", "Cloud"), values))
    return f"infographic chart-bar-plain-text\ndata\n  values\n{rows}".rstrip()


def test_chart_units_are_moved_into_the_headline():
    candidate = _candidate(heading="Latency by Deployment Model", template_id="chart-bar-plain-text",
                           syntax=_chart(["420 ms", "260 ms", "180 ms"]), headline="Latency by deployment model")
    survivors, _ = validate_candidates([candidate], CHART_BODY)
    assert survivors[0]["headline"] == "Latency by deployment model (ms)"
    values = [l.strip() for l in survivors[0]["syntax"].split("\n") if l.strip().startswith("value ")]
    assert values == ["value 420", "value 260", "value 180"]


def test_chart_with_word_values_is_rejected():
    candidate = _candidate(heading="Latency by Deployment Model", template_id="chart-bar-plain-text",
                           syntax=_chart(["More", "Better", "Less"]))
    assert validate_candidates([candidate], CHART_BODY)[0] == []


def test_chart_or_stats_with_invented_value_is_rejected():
    for template_id in ("chart-bar-plain-text", "stats"):
        syntax = _chart(["420", "260", "99"]).replace("chart-bar-plain-text", template_id)
        candidate = _candidate(heading="Latency by Deployment Model", template_id=template_id, syntax=syntax)
        survivors, warnings = validate_candidates([candidate], CHART_BODY)
        assert survivors == [], template_id
        assert any("number not present" in w for w in warnings)


def test_stats_keep_their_units():
    syntax = _chart(["420 ms", "260 ms", "180 ms"]).replace("chart-bar-plain-text", "stats")
    candidate = _candidate(heading="Latency by Deployment Model", template_id="stats", syntax=syntax,
                           headline="Managed endpoints cut latency by more than half")
    survivors, _ = validate_candidates([candidate], CHART_BODY)
    assert survivors[0]["template_id"] == "stats"
    assert [i["value"] for i in parse_items(survivors[0]["syntax"])] == ["420 ms", "260 ms", "180 ms"]


# --- Fallback ---

def test_only_diagram_lists_fall_back_to_cards():
    wheel = TEMPLATES["list-sector"]["example"]
    result = fallback_syntax("list-sector", wheel)
    assert result.startswith("infographic cards")
    assert "title" not in result and ICON_URL in result
    assert fallback_syntax("cards", _cards(3)) is None  # HTML layouts retry at compact density instead
    assert fallback_syntax("chart-bar-plain-text", "infographic chart-bar-plain-text") is None
    assert fallback_syntax("hierarchy-tree", TEMPLATES["hierarchy-tree"]["example"]) is None


# --- Placement, tags, featured image ---

def test_insert_at_section_end_places_after_section_content():
    tag = '<img src="x.png" />'
    result = insert_at_section_end(BODY, "Choosing a Workflow", tag)
    assert result.index("Errors dropped 42%") < result.index(tag) < result.index("## Rollout Steps")


def test_insert_at_section_end_places_overview_under_heading_without_own_content():
    tag = '<img src="x.png" />'
    result = insert_at_section_end(BODY, "Strategy Options", tag)
    assert "## Strategy Options\n\n" + tag + "\n\n### Rehosting" in result


def test_image_tag_is_16_by_9_lazy_and_escaped():
    tag = image_tag("https://api.example.com/a.png", 'The "best" plan')
    assert (CANVAS_WIDTH, CANVAS_HEIGHT) == (1200, 675)
    assert f'width="{CANVAS_WIDTH}" height="{CANVAS_HEIGHT}"' in tag
    assert 'loading="lazy"' in tag
    assert "&quot;best&quot;" in tag


def test_first_rendered_infographic_becomes_featured_image():
    candidates = [_candidate(heading="Choosing a Workflow", headline="First"),
                  _candidate(heading="Rollout Steps", headline="Second")]
    results = {0: {"ok": False, "error": "Layout overlap"}, 1: {"ok": True, "png_base64": TINY_PNG_BASE64}}
    with (
        patch("app.services.technology_ai_infographics.save_png_base64", return_value="second-abc.png") as save,
        patch("app.services.technology_ai_infographics.public_infographic_url", return_value="https://api.example.com/second-abc.png"),
    ):
        body, warnings, featured = insert_infographics(BODY, candidates, results)
    assert save.call_args.args[1] == "second"
    assert featured == {"url": "https://api.example.com/second-abc.png", "thumbnail": "https://api.example.com/second-abc.png",
                        "title": "Second", "alt": "Three-step rollout plan"}
    assert any("Choosing a Workflow" in w for w in warnings)
    assert body.count("second-abc.png") == 1


class _FakeResponse:
    status_code = 200

    def __init__(self, n):
        self.n = n

    def json(self):
        return {"results": [{"id": str(i), "ok": True, "png_base64": TINY_PNG_BASE64} for i in range(self.n)]}


@pytest.mark.asyncio
async def test_render_request_carries_frame_brand_and_specs():
    captured = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json, headers):
            captured.update(url=url, items=json["items"], headers=headers)
            return _FakeResponse(len(json["items"]))

    wheel = normalize_icons("list-sector", TEMPLATES["list-sector"]["example"])
    with (
        patch("app.services.technology_ai_infographics.httpx.AsyncClient", FakeClient),
        patch("app.services.technology_ai_infographics.settings") as s,
    ):
        s.frontend_url = "http://frontend/"
        s.infographic_render_secret = "secret"
        s.infographic_brand_color = "#2563eb"
        s.infographic_brand_name = "example.com"
        s.infographic_brand_logo_url = None
        results = await render_infographics([
            {**_candidate(template_id="cards", syntax=normalize_icons("cards", _cards(3)), eyebrow=""), "source": "Guide"},
            _candidate(template_id="list-sector", syntax=wheel),
        ])
    cards, diagram = captured["items"]
    assert captured["url"] == "http://frontend/api/infographic/render"
    assert captured["headers"] == {"x-infographic-secret": "secret"}
    assert results[0]["ok"] is True and results[1]["ok"] is True
    assert cards["frame"] == {"headline": "How to roll out the workflow", "subtitle": "Plan, pilot, then scale the change.",
                              "eyebrow": EYEBROW, "source": "Guide", "brand_color": "#2563eb",
                              "brand_name": "example.com", "brand_logo_url": ""}
    assert cards["primary"]["kind"] == "html" and cards["primary"]["template"] == "cards"
    assert cards["primary"]["items"][0]["icon"].startswith("https://api.iconify.design/mdi/")
    assert cards["fallback"] is None
    assert diagram["frame"]["eyebrow"] == "Workflows · Rollout"
    assert diagram["primary"] == {"kind": "antv", "template": "list-sector", "syntax": wheel}
    assert diagram["fallback"]["kind"] == "html" and diagram["fallback"]["template"] == "cards"
    assert [i["label"] for i in diagram["fallback"]["items"]] == ["Identity", "Devices", "Network", "Data"]


@pytest.mark.asyncio
async def test_render_infographics_returns_error_per_item_when_unconfigured():
    with patch("app.services.technology_ai_infographics.settings") as s:
        s.frontend_url = ""
        s.infographic_render_secret = None
        results = await render_infographics([_candidate()])
    assert results[0]["ok"] is False


@pytest.mark.asyncio
async def test_add_infographics_to_article_is_never_fatal_on_bad_llm_json():
    body, warnings, featured = await add_infographics_to_article("AI", BODY, AsyncMock(return_value="not json"), AsyncMock())
    assert (body, warnings, featured) == (BODY, [], None)


@pytest.mark.asyncio
async def test_add_infographics_to_article_end_to_end_with_mocked_render():
    generate = AsyncMock(return_value=json.dumps({"infographics": [_candidate()]}))
    render = AsyncMock(return_value={0: {"ok": True, "png_base64": TINY_PNG_BASE64}})
    with (
        patch("app.services.technology_ai_infographics.render_infographics", new=render),
        patch("app.services.technology_ai_infographics.save_png_base64", return_value="generated.png"),
        patch("app.services.technology_ai_infographics.public_infographic_url",
              return_value="https://api.example.com/api/blog/infographics/generated.png"),
    ):
        body, warnings, featured = await add_infographics_to_article("AI", BODY, generate, AsyncMock(),
                                                                     source_title="Rolling Out AI Workflows")
    # One proposal survives, so a top-up round asks again for the remaining sections only
    # (nothing is too long, so no shortening call).
    assert generate.await_count == 2
    top_up = generate.await_args_list[1].args[0]
    assert "still needs 2 more" in top_up and "- Rollout Steps" not in top_up and "- Rehosting" in top_up
    assert render.await_args.args[0][0]["source"] == "Rolling Out AI Workflows"
    assert "api/blog/infographics/generated.png" in body
    assert warnings == []
    assert featured["title"] == "How to roll out the workflow"


# --- Shortening over-long text ---

@pytest.mark.asyncio
async def test_overlong_labels_are_shortened_by_one_model_call():
    long_wheel = TEMPLATES["list-sector"]["example"].replace("Identity", "Identity Verification")
    fixed = TEMPLATES["list-sector"]["example"]
    generate = AsyncMock(return_value=json.dumps({"fixes": [{"index": 1, "syntax": fixed}]}))
    candidates = [_candidate(), _candidate(heading="Strategy Options", template_id="list-sector", syntax=long_wheel)]
    out = await shorten_overlong(candidates, generate, AsyncMock())
    assert generate.await_count == 1
    assert "labels <= 12 characters" in generate.await_args.args[0]
    assert out[1]["syntax"] == fixed
    assert out[0] == candidates[0]


@pytest.mark.asyncio
async def test_shortening_is_skipped_when_nothing_is_too_long_and_ignores_bad_fixes():
    generate = AsyncMock()
    assert await shorten_overlong([_candidate()], generate, AsyncMock()) == [_candidate()]
    generate.assert_not_awaited()
    long_steps = STEPS_SYNTAX.replace("Plan the Change", "Plan the Organization-Wide Change")
    bad = AsyncMock(return_value=json.dumps({"fixes": [{"index": 0, "syntax": "infographic list-sector\n..."}]}))
    out = await shorten_overlong([_candidate(syntax=long_steps)], bad, AsyncMock())
    assert out[0]["syntax"] == long_steps  # a fix that changes the template is ignored


@pytest.mark.asyncio
async def test_shortened_items_without_header_are_spliced_back_in():
    long_cards = _cards(3).replace("Option Alpha", "Maintain Complete System Integrity")
    items_only = ("  - label Integrity\n    desc Short supporting text\n    icon check\n"
                  "  - label Option Beta\n    desc Short supporting text\n    icon cloud\n"
                  "  - label Option Gamma\n    desc Short supporting text\n    icon cloud")
    generate = AsyncMock(return_value=json.dumps({"fixes": [{"index": 0, "syntax": items_only}]}))
    out = await shorten_overlong([_candidate(template_id="cards", syntax=long_cards)], generate, AsyncMock())
    assert out[0]["syntax"].startswith("infographic cards\ndata\n  lists\n    - label Integrity\n      desc")
    assert syntax_issues("cards", out[0]["syntax"]) == []


@pytest.mark.asyncio
async def test_shortening_fix_that_changes_item_count_is_ignored():
    long_cards = _cards(3).replace("Option Alpha", "Maintain Complete System Integrity")
    one_item = "  - label Integrity\n    desc Short supporting text\n    icon check"
    generate = AsyncMock(return_value=json.dumps({"fixes": [{"index": 0, "syntax": one_item}]}))
    out = await shorten_overlong([_candidate(template_id="cards", syntax=long_cards)], generate, AsyncMock())
    assert out[0]["syntax"] == long_cards


@pytest.mark.asyncio
async def test_text_is_measured_against_the_layout_it_will_switch_to():
    # A 3-item wheel must become cards (labels <= 26). Its 14-char labels are too long for
    # the wheel but fine as cards, so no shortening call is spent on it.
    three_wheel = (
        "infographic list-sector\ndata\n  title Migration\n  lists\n"
        "    - label Rehost Quickly\n      desc Move as-is\n      icon cloud\n"
        "    - label Refactor Parts\n      desc Redesign\n      icon code\n"
        "    - label Retire Legacy!\n      desc Switch off\n      icon server"
    )
    generate = AsyncMock()
    out = await shorten_overlong([_candidate(heading="Strategy Options", template_id="list-sector", syntax=three_wheel)],
                                 generate, AsyncMock())
    generate.assert_not_awaited()
    survivors, _ = validate_candidates(out, BODY)
    assert survivors[0]["template_id"] == "cards"


def test_trailing_full_stops_are_removed_from_descriptions():
    syntax = STEPS_SYNTAX.replace("Set a latency baseline", "Set a latency baseline.")
    survivors, _ = validate_candidates([_candidate(syntax=syntax)], BODY)
    assert "desc Set a latency baseline\n" in survivors[0]["syntax"]


# --- Storage ---

def test_save_png_base64_rejects_non_png_bytes():
    with pytest.raises(ValueError):
        save_png_base64(base64.b64encode(b"not a png").decode())


def test_save_png_base64_uses_sanitized_slug():
    filename = save_png_base64(TINY_PNG_BASE64, "../Evil Slug!!")
    try:
        assert filename.startswith("evilslug-") and filename.endswith(".png")
    finally:
        resolve_infographic_path(filename).unlink()


def test_resolve_infographic_path_rejects_traversal_and_missing_files():
    with pytest.raises(FileNotFoundError):
        resolve_infographic_path("../../../etc/passwd")
    with pytest.raises(FileNotFoundError):
        resolve_infographic_path("does-not-exist.png")


def test_template_id_echoing_the_syntax_line_is_accepted():
    raw = json.dumps({"infographics": [{**_candidate(), "template_id": "infographic steps"}]})
    assert parse_candidates(raw)[0]["template_id"] == "steps"


def test_items_that_all_open_with_a_figure_are_drawn_as_stats():
    three_way = (
        "infographic comparison\ndata\n  lists\n"
        "    - label On-Premise\n      desc 420 ms median response time with older GPUs\n"
        "    - label Hybrid Setup\n      desc 260 ms median response time with edge caching\n"
        "    - label Managed Cloud\n      desc 180 ms median response time on a managed endpoint"
    )
    survivors, _ = validate_candidates(
        [_candidate(heading="Latency by Deployment Model", template_id="comparison", syntax=three_way,
                    headline="Managed endpoints respond fastest")], CHART_BODY)
    assert survivors[0]["template_id"] == "stats"
    items = parse_items(survivors[0]["syntax"])
    assert [(i["value"], i["desc"]) for i in items][0] == ("420 ms", "Median response time with older GPUs")
    # Cards whose descriptions are ordinary text stay cards.
    assert validate_candidates([_candidate(heading="Strategy Options", template_id="cards", syntax=_cards(3))], BODY)[0][0][
        "template_id"] == "cards"


# --- Guaranteed count: top-up round, article-structure fallback, render reserve ---

STRUCTURED_BODY = (
    "## Refactoring Steps\n\nHere is how:\n\n"
    "1. **Identify Code Smells**: Look for indicators of poor design, such as duplicated code, long methods, and large classes.\n"
    "2. **Break Down Monoliths**: Split large applications into components.\n"
    "3. **Simplify Logic**: Rewrite convoluted sections.\n\n"
    "## Common Pitfalls\n\n"
    "1. **Skipping Assessment**: Teams start without knowing what they have.\n"
    "2. **Ignoring Stakeholders**: Users are not consulted.\n"
    "3. **Underestimating Complexity**: Timelines slip.\n\n"
    "## Migration Options\n\n"
    "### Rehosting\n\nMoving applications to the cloud with minimal changes.\n\n"
    "### Refactoring\n\nRedesigning applications to improve performance.\n\n"
    "### Rebuilding\n\nWriting the application again on a modern stack.\n\n"
    "## Readiness\n\n"
    "- Business impact\n- Technical debt\n- Compliance risk\n\n"
    "## Conclusion\n\n- Plan the work\n- Test the plan\n- Ship it"
)


def test_structural_candidates_use_the_articles_own_lists_and_subsections():
    by_heading = {c["heading"]: c for c in structural_candidates(STRUCTURED_BODY)}
    steps = by_heading["Refactoring Steps"]
    assert steps["template_id"] == "steps"
    # Three steps leave room for the article's full description.
    assert "desc Look for indicators of poor design, such as duplicated code, long methods, and large classes\n" in steps["syntax"]
    # A numbered list of non-actions is an enumeration, not a process.
    assert by_heading["Common Pitfalls"]["template_id"] == "cards"
    options = by_heading["Migration Options"]
    assert options["template_id"] == "cards" and "label Rehosting" in options["syntax"]
    # Label-only topics never become a checklist; framing sections are skipped.
    assert "Readiness" not in by_heading and "Conclusion" not in by_heading
    # A key sentence from a prose section comes last, as the lowest-priority fallback.
    takeaways = [c for c in structural_candidates(STRUCTURED_BODY) if c["template_id"] == "takeaway"]
    assert takeaways and structural_candidates(STRUCTURED_BODY)[-1]["template_id"] == "takeaway"
    assert "label Moving applications to the cloud with minimal changes." in takeaways[0]["syntax"]
    survivors, _ = validate_candidates(structural_candidates(STRUCTURED_BODY), STRUCTURED_BODY, limit=6)
    assert [s["template_id"] for s in survivors][:3] == ["steps", "cards", "cards"]


def test_short_desc_never_cuts_at_a_plain_comma():
    from app.services.technology_ai_infographics import _short_desc
    assert _short_desc("Decompose large, monolithic applications into smaller, manageable components.", 40) == ""
    assert _short_desc("Look for indicators of poor design, such as duplicated code and long methods.", 40) == \
        "Look for indicators of poor design"
    assert _short_desc("Short enough.", 40) == "Short enough"


@pytest.mark.asyncio
async def test_article_structure_fills_the_gap_when_the_model_falls_short():
    generate = AsyncMock(return_value=json.dumps({"infographics": []}))
    render = AsyncMock(side_effect=lambda batch: {i: {"ok": True, "png_base64": TINY_PNG_BASE64} for i in range(len(batch))})
    with (
        patch("app.services.technology_ai_infographics.render_infographics", new=render),
        patch("app.services.technology_ai_infographics.save_png_base64", side_effect=lambda b, slug: f"{slug}.png"),
        patch("app.services.technology_ai_infographics.public_infographic_url", side_effect=lambda f: f"https://api.example.com/{f}"),
    ):
        body, warnings, featured = await add_infographics_to_article("AI", STRUCTURED_BODY, generate, AsyncMock())
    assert body.count("<img ") == TARGET_INFOGRAPHIC_COUNT
    assert warnings == [] and featured is not None


@pytest.mark.asyncio
async def test_a_failed_render_is_replaced_by_a_spare():
    candidates = [
        _candidate(),
        _candidate(heading="Strategy Options", template_id="comparison", syntax=COMPARISON_SYNTAX),
        _candidate(heading="Readiness Questions", template_id="checklist", syntax=CHECKLIST_SYNTAX),
        _candidate(heading="Choosing a Workflow", template_id="cards", syntax=_cards(3)),
    ]
    generate = AsyncMock(return_value=json.dumps({"infographics": candidates}))
    batches = []

    async def render(batch):
        batches.append([c["heading"] for c in batch])
        return {i: ({"ok": False, "error": "overlap"} if c["heading"] == "Strategy Options" else
                    {"ok": True, "png_base64": TINY_PNG_BASE64}) for i, c in enumerate(batch)}

    with (
        patch("app.services.technology_ai_infographics.render_infographics", new=render),
        patch("app.services.technology_ai_infographics.save_png_base64", side_effect=lambda b, slug: f"{slug}.png"),
        patch("app.services.technology_ai_infographics.public_infographic_url", side_effect=lambda f: f"https://api.example.com/{f}"),
    ):
        body, warnings, _ = await add_infographics_to_article("AI", BODY, generate, AsyncMock())
    assert batches == [["Rollout Steps", "Strategy Options", "Readiness Questions"], ["Choosing a Workflow"]]
    assert body.count("<img ") == 3 and warnings == []
    assert generate.await_count == 1  # the first round already had enough candidates


def test_closing_sections_can_give_a_takeaway_but_never_a_diagram():
    body = "## Why Stability Wins\n\nShort.\n\n## Next Step\n\nWrite down the one AI capability your product needs this quarter and defer everything else."
    takeaways = [c for c in structural_candidates(body) if c["template_id"] == "takeaway"]
    assert [c["heading"] for c in takeaways] == ["Next Step"]
    assert validate_candidates(takeaways, body)[0][0]["heading"] == "Next Step"
    assert validate_candidates([_candidate(heading="Conclusion")], BODY)[0] == []


def test_icons_are_not_repeated_when_the_item_text_suggests_another():
    syntax = (
        "infographic cards\ndata\n  lists\n"
        "    - label Unstructured Data\n      desc Transforming unstructured data into usable formats\n      icon database\n"
        "    - label Data Engineering\n      desc Streamlining data pipelines for efficiency\n      icon database\n"
        "    - label NLP Techniques\n      desc Utilizing language models for processing data\n      icon database"
    )
    icons = [l.strip() for l in normalize_icons("cards", syntax).split("\n") if l.strip().startswith("icon ")]
    assert len(set(icons)) == 3
    assert icons[0].endswith(f"{ICONS['database']}.svg")
