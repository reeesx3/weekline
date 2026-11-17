import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

import widget


class WidgetRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.config = root / "config.json"
        self.schedule = root / "schedule.json"
        overrides = patch.multiple(widget, APP_DIR=root, CONFIG_PATH=self.config,
                                   SCHEDULE_PATH=self.schedule, LEGACY_SETTINGS_PATH=root / "old.ini",
                                   IS_WINDOWS=False)
        overrides.start()
        self.addCleanup(overrides.stop)
        # No tray entry, native window hooks or changes to the user's files.
        with patch.object(widget.ScheduleWidget, "_setup_tray"):
            self.view = widget.ScheduleWidget()
        self.addCleanup(self.dispose)

    def dispose(self):
        self.view.deleteLater()
        self.app.sendPostedEvents(None, widget.QEvent.DeferredDelete)

    def test_navigation_and_title_edit_reuse(self):
        for day in range(14):
            self.view.select_page(day)
            self.app.sendPostedEvents(None, widget.QEvent.DeferredDelete)
            self.assertEqual(len(self.view.findChildren(widget.SlotCard)), len(self.view.days[day]))
        self.view.select_page(0)
        self.view.open_editor()
        ref = self.view._edit_ref
        card = self.view._card_for(ref)
        self.view._on_field_changed("title", "<новое название>", True)
        self.assertIs(card, self.view._card_for(ref))
        self.assertEqual(card.lbl_title.text(), "<новое название>")
        self.assertIn("<новое название>", self.schedule.read_text("utf-8"))

    def test_new_install_uses_packaged_example(self):
        self.assertEqual(json.loads(self.schedule.read_text("utf-8")),
                         json.loads(widget.EXAMPLE_PATH.read_text("utf-8")))
        self.assertTrue(self.view.days[0] and self.view.days[7])
        self.assertNotEqual(self.view.days[0], self.view.days[7])

    def test_existing_schedule_takes_precedence(self):
        text = json.dumps({"weeks": {"a": {}, "b": {}}})
        self.schedule.write_text(text, encoding="utf-8")
        with patch.object(widget, "EXAMPLE_PATH", self.schedule.with_name("absent.json")):
            self.view._load()
        self.assertIsNone(self.view._error)
        self.assertFalse(any(self.view.days))
        self.assertEqual(text, self.schedule.read_text("utf-8"))

    def test_appearance_controls_persist_and_reset(self):
        panel = self.view.settings
        panel.width_box.setValue(480)
        panel.size_box.setCurrentIndex(panel.size_box.findData(2))
        panel.size_box.activated.emit(panel.size_box.currentIndex())
        panel.display_boxes["show_clock"].click()
        self.assertEqual(self.view.width(), 480)
        self.assertEqual(self.view.lbl_day.font().pixelSize(), 23)
        self.assertTrue(self.view.lbl_clock.isHidden())
        self.view._save_config()
        cfg = widget.WidgetConfig.load(self.config)
        self.assertEqual((cfg.width, cfg.font_offset, cfg.show_clock), (480, 2, False))
        self.view._change_appearance("reset", None)
        self.assertEqual(self.view.lbl_day.font().pixelSize(), 21)
        self.assertFalse(self.view.lbl_clock.isHidden())

    def test_settings_height_follows_active_controls(self):
        self.view.setAttribute(widget.Qt.WA_DontShowOnScreen)
        self.view.open_settings()
        panel = self.view.settings
        self.app.processEvents()
        full_height = panel.height()
        panel.sw_biweekly.click()
        self.app.processEvents()
        compact_height = panel.height()
        self.assertLess(compact_height, full_height)
        gap = panel.mode_hint.y() - panel.sw_biweekly.geometry().bottom() - 1
        self.assertIn(gap, range(9))
        panel.tabs.setCurrentIndex(1)
        self.app.processEvents()
        self.assertGreater(panel.height(), compact_height)
        panel.tabs.setCurrentIndex(0)
        self.app.processEvents()
        self.assertEqual(panel.height(), compact_height)
        panel.sw_biweekly.click()
        self.app.processEvents()
        self.assertEqual(panel.height(), full_height)

    def test_invalid_encoding_after_valid_empty_schedule(self):
        empty = json.dumps({"days": {}, "weeks": {"a": {}, "b": {}}})
        self.schedule.write_text(empty, encoding="utf-8")
        self.view.reload()
        self.schedule.write_bytes(b"\xff")
        self.view.reload()
        self.assertTrue(self.view._error)
        self.assertFalse(any(self.view.days))
        self.assertEqual(self.schedule.read_bytes(), b"\xff")
        self.schedule.write_text(empty, encoding="utf-8")
        self.view.reload()
        self.assertIsNone(self.view._error)

    def test_atomic_save_after_temporary_windows_lock(self):
        replace = widget.os.replace
        locked = PermissionError("Файл временно занят")
        locked.winerror = 32
        attempts = []

        def replace_on_retry(source, target):
            attempts.append(target)
            if len(attempts) == 1:
                raise locked
            replace(source, target)

        with patch.object(widget.os, "replace", replace_on_retry):
            widget.write_text_atomic(self.config, "{}")
        self.assertEqual(attempts, [self.config, self.config])
        self.assertEqual(self.config.read_text("utf-8"), "{}")


if __name__ == "__main__":
    unittest.main()
