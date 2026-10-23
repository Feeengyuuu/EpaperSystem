"""The rail avatar frames are bundled artwork dealt in non-repeating rounds."""

import json
import random

import pytest

from plugins.steam_profile_dashboard import avatar_frame
from plugins.steam_profile_dashboard.avatar_frame import (
    AVATAR_BOX,
    AVATAR_SIZE,
    FRAME_ORIGIN,
    OVERLAY_SIZE,
    frame_catalog,
    load_avatar_frame,
    next_avatar_frame,
    next_frame_id,
)

# The bundled set grows in batches (01-30 freestyle, 31-79 freestyle, 81-100 game themes).
FRAME_IDS = [frame_id for frame_id, _ in frame_catalog()]


@pytest.fixture(autouse=True)
def fresh_rotation(monkeypatch):
    monkeypatch.setattr(avatar_frame, "_memory_states", {})
    monkeypatch.setattr(avatar_frame, "_write_failure_logged", set())


def forget_process_memory(monkeypatch):
    monkeypatch.setattr(avatar_frame, "_memory_states", {})


def test_catalog_bundles_every_batch_in_numeric_order():
    catalog = frame_catalog()

    assert len(catalog) >= 99
    assert [int(frame_id) for frame_id, _ in catalog] == sorted(int(frame_id) for frame_id, _ in catalog)
    assert catalog[0] == ("01", "橘子汽水")
    assert ("30", "糖纸没拆完") in catalog and ("31", "彩窗残角") in catalog
    assert catalog[-1] == ("100", "风之旅人")


@pytest.mark.parametrize("frame_id", FRAME_IDS)
def test_every_frame_opening_fits_the_original_avatar(frame_id):
    frame = load_avatar_frame(frame_id)

    assert frame.image.mode == "RGBA" and frame.image.size == OVERLAY_SIZE
    assert frame.mask.mode == "L" and frame.mask.size == (AVATAR_SIZE, AVATAR_SIZE)
    centre = AVATAR_SIZE // 2
    assert frame.mask.getpixel((centre, centre)) == 255
    assert frame.image.getpixel((AVATAR_BOX[0] + centre, AVATAR_BOX[1] + centre))[3] == 0
    # The frame is scaled to the photo, not the photo to the frame.
    left, top, right, bottom = frame.mask.getbbox()
    assert max(right - left, bottom - top) >= AVATAR_SIZE - 4


@pytest.mark.parametrize("frame_id", FRAME_IDS)
def test_the_overlay_holds_the_whole_frame(frame_id):
    # Anchored at the rail's corner, the canvas reaches past the persona name (y 175) and
    # into the hero panel (x 194), so no frame is cut at its right or bottom edge.
    assert FRAME_ORIGIN == (0, 0) and OVERLAY_SIZE[0] > 194 and OVERLAY_SIZE[1] > 207
    alpha = load_avatar_frame(frame_id).image.getchannel("A")
    width, height = alpha.size

    assert alpha.crop((0, height - 1, width, height)).getbbox() is None
    assert alpha.crop((width - 1, 0, width, height)).getbbox() is None


def test_rebuilt_frames_run_on_over_the_name_and_into_the_hero_panel():
    def solid(frame_id, box):
        alpha = load_avatar_frame(frame_id).image.getchannel("A")
        return alpha.crop(box).point(lambda value: 255 if value == 255 else 0).getbbox() is not None

    assert solid("85", (0, 175, *OVERLAY_SIZE))
    assert solid("90", (0, 175, *OVERLAY_SIZE)) and solid("90", (194, 0, *OVERLAY_SIZE))


def test_heavy_frames_break_out_of_the_old_box():
    alpha = load_avatar_frame("13").image.getchannel("A")
    width, height = alpha.size

    assert alpha.crop((0, 0, 1, height)).getbbox() is not None
    assert alpha.crop((0, 0, width, 1)).getbbox() is not None
    # Past the rail divider at x=181.
    assert alpha.crop((183, 0, width, height)).getbbox() is not None


def test_unknown_frame_ids_are_not_loaded():
    assert load_avatar_frame("80") is None  # not bundled (awaiting its artwork)
    assert load_avatar_frame("../01") is None


def test_each_round_deals_every_frame_once():
    rng = random.Random(7)
    first = [next_frame_id(None, rng) for _ in FRAME_IDS]
    second = [next_frame_id(None, rng) for _ in FRAME_IDS]

    assert sorted(first, key=int) == FRAME_IDS
    assert sorted(second, key=int) == FRAME_IDS
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

    assert sorted(before + after, key=int) == FRAME_IDS
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

    assert sorted(dealt, key=int) == FRAME_IDS


def test_state_from_a_previous_catalog_keeps_only_known_frames(tmp_path):
    state_path = tmp_path / "rotation.json"
    state_path.write_text(json.dumps({"version": 1, "queue": ["999", "05", "06"], "last": "998"}),
                          encoding="utf-8")

    assert [next_frame_id(state_path) for _ in range(2)] == ["05", "06"]


def test_unwritable_state_keeps_rotating_in_memory(tmp_path, monkeypatch, caplog):
    def refuse(*_args, **_kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(avatar_frame, "atomic_write_json", refuse)
    rng = random.Random(11)
    dealt = [next_frame_id(tmp_path / "rotation.json", rng) for _ in range(len(FRAME_IDS) * 2)]

    assert sorted(dealt[:len(FRAME_IDS)]) == sorted(FRAME_IDS)
    assert all(previous != current for previous, current in zip(dealt, dealt[1:]))
    assert sum("memory only" in record.getMessage() for record in caplog.records) == 1


def test_next_avatar_frame_skips_a_frame_that_fails_to_load(monkeypatch):
    real = avatar_frame.load_avatar_frame
    monkeypatch.setattr(avatar_frame, "load_avatar_frame",
                        lambda frame_id: None if frame_id == "01" else real(frame_id))
    state = {"version": 1, "queue": ["01", "02"], "last": None}
    avatar_frame._memory_states[None] = state

    assert next_avatar_frame().frame_id == "02"
