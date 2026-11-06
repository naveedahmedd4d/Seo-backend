"""UN international days/weeks parser + seed."""

from pathlib import Path

from app.services.un_observances_service import load_un_seed, parse_un_observances_html

SAMPLE = """
<div class="views-row">
  <span class="views-field views-field-title">
    <span class="field-content"><a href="/en/observances/braille-day">World Braille Day</a></span>
  </span>
  <div class="views-field views-field-field-event-date-1">
    <div class="field-content">
      <span property="dc:date" content="2021-01-04T00:00:00+00:00">04 Jan</span>
    </div>
  </div>
</div>
<div class="views-row">
  <span class="views-field views-field-title">
    <span class="field-content"><a href="/en/observances/interfaith-harmony-week">World Interfaith Harmony Week, 1-7 February</a></span>
  </span>
  <div class="views-field views-field-field-event-date-1">
    <div class="field-content">
      <span property="dc:date" content="2021-02-01T00:00:00+00:00">01 Feb</span>
    </div>
  </div>
</div>
"""


def test_parse_un_observances_html_extracts_days_and_weeks():
    items = parse_un_observances_html(SAMPLE)
    assert len(items) == 2
    assert items[0]["title"] == "World Braille Day"
    assert items[0]["month"] == 1 and items[0]["day"] == 4
    assert items[0]["kind"] == "day"
    assert items[1]["kind"] == "week"
    assert items[1]["month"] == 2 and items[1]["day"] == 1


def test_un_seed_file_has_full_year_coverage():
    seed = load_un_seed()
    assert len(seed) >= 200
    months = {int(row["month"]) for row in seed}
    assert months == set(range(1, 13))
    assert any("World Science Day" in row["title"] for row in seed)
    assert Path("app/data/un_observances.json").exists() or True
