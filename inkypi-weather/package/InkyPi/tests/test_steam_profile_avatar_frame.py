"""The rail avatar frames are bundled artwork dealt in non-repeating rounds."""

import json
import random

import pytest

from plugins.steam_profile_dashboard import avatar_frame
from plugins.steam_profile_dashboard.avatar_frame import (
    FRAME_ORIGIN,
    FRAME_SIZE,
    frame_catalog,
    load_avatar_frame,
    next_avatar_frame,
    next_frame_id,
)

FRAME_IDS = [f"{number:02d}" for number in range(1, 31)]


@pytest.fixture(autouse=True)
def fresh_rotation(monkeypatch):
    monkeypatch.setattr(avatar_frame, "_memory_states", {})
    monkeypatch.setattr(avatar_frame, "_write_failure_logged", set())


def forget_process_memory(monkeypatch):
    monkeypatch.setattr(avatar_frame, "_memory_states", {})


def test_catalog_bundles_the_thirty_frames_in_order():
    catalog = frame_catalog()

    assert [frame_id for frame_id, _ in catalog] == FRAME_IDS
    assert catalog[0] == ("01", "橘子汽水")
    assert catalog[-1] == ("30", "糖纸没拆完")


@pytest.mark.parametrize("frame_id", FRAME_IDS)
def test_every_frame_has_an_open_centre_for_the_avatar(frame_id):
    frame = load_avatar_frame(frame_id)

    assert frame.image.mode == "RGBA" and frame.image.size == FRAME_SIZE
    assert frame.mask.mode == "L" and frame.mask.size == FRAME_SIZE
    left, top, right, bottom = frame.opening
    assert right - left >= 80 and bottom - top >= 80
    centre = (FRAME_SIZE[0] // 2, FRAME_SIZE[1] // 2)
    assert frame.mask.getpixel(centre) == 255
    assert frame.image.getpixel(centre)[3] == 0
    # The photo never reaches past the frame's outer extent.
    frame_left, frame_top, frame_right, frame_bottom = frame.image.getchannel("A").getbbox()
    assert frame_left <= left and frame_top <= top
    assert right <= frame_right and bottom <= frame_bottom


def test_frames_fit_the_rail_above_the_persona_name():
    origin_x, origin_y = FRAME_ORIGIN

    assert origin_x >= 0 and origin_x + FRAME_SIZE[0] <= 180
    assert origin_y >= 0 and origin_y + FRAME_SIZE[1] <= 178


def test_unknown_frame_ids_are_not_loaded():
    assert load_avatar_frame("31") is None
    assert load_avatar_frame("../01") is None


def test_each_round_deals_every_frame_once():
    rng = random.Random(7)
    first = [next_frame_id(None, rng) for _ in FRAME_IDS]
    second = [next_frame_id(None, rng) for _ in FRAME_IDS]

    assert sorted(first) == FRAME_IDS
    assert sorted(second) == FRAME_IDS
    assert first != second


@pytest.mark.parametrize("seed", range(20))
def test_consecutive_frames_never_repeat_across_rounds(seed):
    rng = random.Random(seed)
    dealt = [next_frame_id(None, rng) for _ in range(len(FRAME_IDS) * 6)]

    assert all(previous != current for previous, current in zip(dealt, dealt[1:]))


def test_round_survives_a_restart(tmp_path, monkeypatch):
    state_path = tmp_path / "data" / "rotation.json"
    rng = random.Random(3)
    before = [next_frame_id(state_path, rng) for _ in range(12)]

    forget_process_memory(monkeypatch)
    after = [next_frame_id(state_path, rng) for _ in range(len(FRAME_IDS) - 12)]

    assert sorted(before + after) == FRAME_IDS
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved == {"version": 1, "queue": [], "last": after[-1]}
    forget_process_memory(monkeypatch)
    assert next_frame_id(state_path, rng) != after[-1]


@pytest.mark.parametrize("content", ["not json", "[]", '{"version": 1, "queue": ["01", "01"], "last": "02"}',
                                     '{"version": 1, "queue": ["99", 3], "last": null}'])
def test_corrupt_state_starts_a_fresh_round(tmp_path, content):
    state_path = tmp_path / "rotation.json"
    state_path.write_text(content, encoding="utf-8")

    dealt = [next_frame_id(state_path, random.Random(5)) for _ in FRAME_IDS]

    assert sorted(dealt) == FRAME_IDS


def test_state_from_a_previous_catalog_keeps_only_known_frames(tmp_path):
    state_path = tmp_path / "rotation.json"
    state_path.write_text(json.dumps({"version": 1, "queue": ["99", "05", "06"], "last": "98"}),
                          encoding="utf-8")

    assert [next_frame_id(state_path) for _ in range(2)] == ["05", "06"]


def test_unwritable_state_keeps_rotating_in_memory(tmp_path, monkeypatch, caplog):
    def refuse(*_args, **_kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(avatar_frame, "atomic_write_json", refuse)
    rng = random.Random(11)
    dealt = [next_frame_id(tmp_path / "rotation.json", rng) for _ in range(len(FRAME_IDS) * 2)]

    assert sorted(dealt[:30]) == FRAME_IDS
    assert all(previous != current for previous, current in zip(dealt, dealt[1:]))
    assert sum("memory only" in record.getMessage() for record in caplog.records) == 1


def test_next_avatar_frame_skips_a_frame_that_fails_to_load(monkeypatch):
    real = avatar_frame.load_avatar_frame
    monkeypatch.setattr(avatar_frame, "load_avatar_frame",
                        lambda frame_id: None if frame_id == "01" else real(frame_id))
    state = {"version": 1, "queue": ["01", "02"], "last": None}
    avatar_frame._memory_states[None] = state

    assert next_avatar_frame().frame_id == "02"
