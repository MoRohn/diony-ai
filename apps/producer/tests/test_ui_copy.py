from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "diony_producer" / "static"


def test_producer_floors_include_program_and_prompt_edit() -> None:
    html = (STATIC / "index.html").read_text()
    css = (STATIC / "app.css").read_text()
    js = (STATIC / "app.js").read_text()
    assert html.count('data-bay="') == 4
    assert 'data-bay="bible"' not in html
    assert 'data-bay="rundown"' not in html
    assert 'data-bay="program"' in html
    assert 'class="sheet"' in html
    assert "folio" not in html
    assert "Write the picture" in html
    assert "Produce" in html
    assert 'class="clap"' in html
    assert 'id="role"' not in html
    assert "showrunner" not in html
    assert 'id="ladder"' in html
    assert 'id="cast"' in html
    assert 'id="stage"' in html
    assert "Prompt to edit" in html
    assert 'id="edit-in"' in html
    assert "class=\"sr\"" not in html.split('id="edit-in"')[1][:80]
    assert "Assemble" in html
    assert 'id="site-line"' in html
    assert 'id="retake"' in html
    assert "v=program8" in html
    assert 'id="allotment"' in html
    assert "Goal for the finished show" in html or "Not a stopwatch" in html
    assert "targetMinutes" in js
    assert "allotment-picks" in js
    assert "/api/finish" in js
    assert "pollFinish" in js
    assert "/api/farm" in js
    assert "pollFarm" in js
    assert "/api/shots/" in js
    assert "retake" in js
    assert "border: 2px dashed" not in css
    assert "sprockets" not in html
    assert "/api/studio/open" in js
    assert "/api/studio/roll" in js
    assert "/api/program/assemble" in js
    assert 'showBay("dailies")' in js
    cast_at = js.index('getElementById("cast")')
    roll_in_make = js.find("/api/studio/roll", cast_at)
    dailies_in_make = js.find('showBay("dailies")', cast_at)
    assert 0 < roll_in_make < dailies_in_make
