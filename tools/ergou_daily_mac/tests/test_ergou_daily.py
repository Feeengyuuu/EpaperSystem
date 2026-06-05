import datetime as dt
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ergou_daily


class ErgouDailyTests(unittest.TestCase):
    def test_date_label_uses_chinese_weekday(self):
        self.assertEqual(ergou_daily.date_label(dt.date(2026, 6, 5)), "2026年6月5日 星期五")

    def test_beijing_now_accepts_aware_datetime(self):
        config = dict(ergou_daily.DEFAULT_CONFIG)
        now = dt.datetime(2026, 6, 4, 21, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(ergou_daily.beijing_now(config, now).date(), dt.date(2026, 6, 5))

    def test_parse_json_text_accepts_fenced_json(self):
        parsed = ergou_daily.parse_json_text('```json\n{"date_label":"x"}\n```')
        self.assertEqual(parsed["date_label"], "x")

    def test_extract_response_text_reads_output_text(self):
        payload = {"output_text": "{\"ok\": true}"}
        self.assertEqual(ergou_daily.extract_response_text(payload), "{\"ok\": true}")

    def test_sample_brief_validates(self):
        target = dt.datetime(2026, 6, 5, 12, 0, tzinfo=dt.timezone.utc)
        brief = ergou_daily.sample_brief(target)
        ergou_daily.validate_brief(brief)
        self.assertEqual(len(brief["incidents"]), 8)
        self.assertEqual(len(brief["watchlist"]), 3)

    def test_img2_prompt_contains_required_sections(self):
        target = dt.datetime(2026, 6, 5, 12, 0, tzinfo=dt.timezone.utc)
        prompt = ergou_daily.brief_to_img2_prompt(
            ergou_daily.sample_brief(target),
            "固定规则",
        )
        self.assertIn("img-2 / gpt-image-2", prompt)
        self.assertIn("二狗新闻早报", prompt)
        self.assertIn("今日意外", prompt)
        self.assertIn("三只买入观察", prompt)
        self.assertIn("96px outer safe margin", prompt)
        self.assertIn("Do not render the JSON schema", prompt)
        self.assertIn("细节：", prompt)
        self.assertIn("1290万人", prompt)

    def test_config_rejects_non_img2_model(self):
        with self.assertRaises(ValueError):
            ergou_daily.validate_config({"image_model": "gpt-image-1.5"})

    def test_validate_rejects_wrong_incident_count(self):
        target = dt.datetime(2026, 6, 5, 12, 0, tzinfo=dt.timezone.utc)
        brief = ergou_daily.sample_brief(target)
        brief["incidents"] = brief["incidents"][:7]
        with self.assertRaises(ValueError):
            ergou_daily.validate_brief(brief)


if __name__ == "__main__":
    unittest.main()
