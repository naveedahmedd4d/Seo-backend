"""One-off script to generate catalog.yaml from legacy patterns."""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def rep(default, formal=None, casual=None, professional=None, friendly=None):
    d = {"default": default if isinstance(default, list) else [default]}
    for key, val in [
        ("formal", formal),
        ("casual", casual),
        ("professional", professional),
        ("friendly", friendly),
    ]:
        if val is not None:
            d[key] = val if isinstance(val, list) else [val]
    for t in ("formal", "casual", "professional", "friendly"):
        if t not in d:
            d[t] = list(d["default"])
    return d


entries = []


def add(entry_id, pattern, cat, replacements, llm_only=False, typ="phrase", weight=1.0):
    entries.append(
        {
            "id": entry_id,
            "type": typ,
            "category": cat,
            "pattern": pattern,
            "weight": weight,
            "replacements": replacements,
            "llm_only": llm_only,
        }
    )


# Transitions
transitions = [
    ("furthermore", "also"),
    ("moreover", "and"),
    ("additionally", "also"),
    ("consequently", "so"),
    ("nevertheless", "still"),
    ("nonetheless", "still"),
    ("accordingly", "so"),
    ("henceforth", "from now on"),
    ("in conclusion", "to wrap up"),
    ("in summary", "overall"),
    ("to summarize", "overall"),
    ("on the other hand", "but"),
    ("at the end of the day", "ultimately"),
    ("hence", "so"),
    ("thus", "so"),
    ("therefore", "so"),
]
for word, default_alt in transitions:
    eid = word.replace(" ", "_")
    typ = "word" if " " not in word else "phrase"
    add(
        eid,
        word,
        "transition",
        rep([default_alt, "and"] if default_alt != "and" else [default_alt]),
        typ=typ,
    )

# Phrases from legacy catalog
phrases = [
    ("delve_into", "delve into", "buzzword", rep(["dig into", "look at"])),
    ("delve", "delve", "buzzword", rep(["explore", "look at"]), "word"),
    ("testament_to", "testament to", "buzzword", rep(["proof of", "shows"])),
    ("in_the_world_of", "in the world of", "buzzword", rep(["in"])),
    ("when_it_comes_to", "when it comes to", "buzzword", rep(["for", "about"])),
    ("landscape", "landscape", "buzzword", rep(["field", "space"]), "word"),
    ("robust", "robust", "buzzword", rep(["strong", "solid"]), "word"),
    ("tapestry", "tapestry", "buzzword", rep(["mix", "blend"]), "word"),
    ("unlock", "unlock", "buzzword", rep(["open up", "enable"]), "word"),
    ("leverage", "leverage", "buzzword", rep(["use"]), "word"),
    ("steadfast", "steadfast", "buzzword", rep(["steady", "firm"]), "word"),
    ("embarked_on_journey", "embarked on a journey", "buzzword", rep(["started", "began"])),
    ("intriguing_journey", "intriguing journey", "buzzword", rep(["story", "path"])),
    ("path_forged", "path forged", "buzzword", rep(["path built", "route"])),
    ("familiar_face", "familiar face", "buzzword", rep(["known name", "regular"])),
    ("distinctive", "distinctive", "buzzword", rep(["standout", "unique"]), "word"),
    ("memorable", "memorable", "buzzword", rep(["unforgettable", "notable"]), "word"),
    ("holistic", "holistic", "buzzword", rep(["whole", "full"]), "word"),
    ("multifaceted", "multifaceted", "buzzword", rep(["complex", "varied"]), "word"),
    ("pivotal", "pivotal", "buzzword", rep(["key", "central"]), "word"),
    ("underscores", "underscores", "buzzword", rep(["shows", "highlights"]), "word"),
    ("underscore", "underscore", "buzzword", rep(["show", "highlight"]), "word"),
    ("utilize", "utilize", "buzzword", rep(["use"]), "word"),
    ("facilitate", "facilitate", "buzzword", rep(["help", "enable"]), "word"),
    ("paramount", "paramount", "buzzword", rep(["main", "top"]), "word"),
    ("myriad", "myriad", "buzzword", rep(["many", "lots of"]), "word"),
    ("plethora", "plethora", "buzzword", rep(["many", "lots of"]), "word"),
    ("realm", "realm", "buzzword", rep(["area", "field"]), "word"),
    ("crucial", "crucial", "buzzword", rep(["key", "vital"]), "word"),
    ("comprehensive", "comprehensive", "buzzword", rep(["full", "complete"]), "word"),
    ("nuanced", "nuanced", "buzzword", rep(["subtle", "layered"]), "word"),
    ("seamless", "seamless", "buzzword", rep(["smooth", "easy"]), "word"),
    ("cutting_edge", "cutting-edge", "buzzword", rep(["new", "latest"]), "word"),
    ("game_changer", "game-changer", "buzzword", rep(["big shift", "breakthrough"])),
    ("at_its_core", "at its core", "formulaic", rep(["basically", "at heart"])),
    ("plays_vital_role", "plays a vital role", "formulaic", rep(["matters", "helps"])),
    ("serves_as_a", "serves as a", "formulaic", rep(["is a", "acts as"])),
    ("worth_noting", "it's worth noting", "formulaic", rep(["note that", "keep in mind"])),
    ("worth_noting_is", "it is worth noting", "formulaic", rep(["note that", "keep in mind"])),
    ("in_todays_world", "in today's world", "formulaic", rep(["today", "now"])),
    ("in_this_day_and_age", "in this day and age", "formulaic", rep(["today", "now"])),
    ("in_an_era_where", "in an era where", "formulaic", rep(["now that", "when"])),
    ("dive_deep", "dive deep", "buzzword", rep(["look closely", "dig in"])),
    ("look_no_further", "look no further", "formulaic", rep(["this is it", "here it is"])),
    ("without_further_ado", "without further ado", "formulaic", rep(["here goes", "let us begin"])),
    ("all_sorts_of", "all sorts of", "cliche", rep(["many kinds of", "various"])),
    ("made_all_difference", "made all the difference", "cliche", rep(["changed everything", "mattered a lot"])),
    ("behind_every_step", "behind him every step", "cliche", rep(["supported throughout", "there throughout"])),
    ("worth_chasing", "worth chasing", "cliche", rep(["worth pursuing", "worth going for"])),
    ("navigate", "navigate", "buzzword", rep(["handle", "work through"]), "word"),
    ("foster", "foster", "buzzword", rep(["build", "encourage"]), "word"),
    ("enhance", "enhance", "buzzword", rep(["improve", "boost"]), "word"),
    ("streamline", "streamline", "buzzword", rep(["simplify", "speed up"]), "word"),
    ("empower", "empower", "buzzword", rep(["enable", "support"]), "word"),
    ("inherent", "inherent", "buzzword", rep(["built-in", "natural"]), "word"),
    ("notably", "notably", "buzzword", rep(["especially", "in particular"]), "word"),
    ("specifically", "specifically", "buzzword", rep(["in particular", "exactly"]), "word"),
    ("ultimately", "ultimately", "buzzword", rep(["in the end", "finally"]), "word"),
    ("essentially", "essentially", "buzzword", rep(["basically", "at heart"]), "word"),
    ("arguably", "arguably", "buzzword", rep(["perhaps", "some say"]), "word"),
    ("showcase", "showcase", "buzzword", rep(["show", "highlight"]), "word"),
    ("elevate", "elevate", "buzzword", rep(["raise", "lift"]), "word"),
    ("shed_light_on", "shed light on", "buzzword", rep(["explain", "clarify"])),
    ("important_to_note", "it's important to note", "formulaic", rep(["note that", "remember"])),
    ("in_order_to", "in order to", "formulaic", rep(["to"])),
    ("due_to_the_fact", "due to the fact that", "formulaic", rep(["because", "since"])),
]

for item in phrases:
    if len(item) == 5:
        add(item[0], item[1], item[2], item[3], typ=item[4])
    else:
        add(item[0], item[1], item[2], item[3])

# llm_only
add("its_not_just", "it's not just", "formulaic", {}, llm_only=True)
add("its_not_just_is", "it is not just", "formulaic", {}, llm_only=True)
add("whether_youre_a", "whether you're a", "formulaic", {}, llm_only=True)
add(
    "sentence_initial_furthermore",
    r"^(Furthermore|Moreover|Additionally),\s*",
    "structure",
    {},
    llm_only=True,
    typ="regex",
)

out_path = Path(__file__).resolve().parents[1] / "app/services/humanizer/catalog.yaml"
out_path.write_text(
    yaml.dump({"entries": entries}, default_flow_style=False, allow_unicode=True, sort_keys=False),
    encoding="utf-8",
)
print(f"Wrote {len(entries)} entries to {out_path}")

from app.services.humanizer.catalog import flag_only_ratio, load_catalog

load_catalog.cache_clear()
print("flag_only_ratio", flag_only_ratio())
