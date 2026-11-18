import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QEventLoop, QTimer
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

    def test_legacy_config_and_language_after_restart(self):
        self.dispose()
        self.config.write_text('{"biweekly": false, "custom": 17}', encoding="utf-8")
        with patch.object(widget.ScheduleWidget, "_setup_tray"):
            self.view = widget.ScheduleWidget()
            self.assertEqual(self.view.cfg.language, "ru")
            self.assertEqual(self.view.windowTitle(), "Расписание")
            self.view.open_settings()
            panel = self.view.settings
            panel.tabs.setCurrentIndex(1)
            box = panel.language_box
            box.setCurrentIndex(box.findData("en"))
            box.activated.emit(box.currentIndex())
            self.assertEqual(panel.tabs.tabText(1), "Appearance")
            self.assertEqual(box.accessibleName(), "Language")
            saved = json.loads(self.config.read_text("utf-8"))
            self.assertEqual((saved["language"], saved["custom"]), ("en", 17))
            self.dispose()
            self.view = widget.ScheduleWidget()
        self.assertEqual(self.view.windowTitle(), "Schedule")
        self.assertEqual(self.view.settings.language_box.currentData(), "en")

    def test_import_dialog_follows_interface_language(self):
        self.view.set_language("en")
        self.assertEqual(widget.QLocale().language(), widget.QLocale.English)
        with patch.object(widget.QFileDialog, "getOpenFileName", return_value=("", "")) as choose:
            self.view.import_schedule()
        self.assertTrue(choose.call_args.kwargs["options"] & widget.QFileDialog.DontUseNativeDialog)
        self.assertEqual(choose.call_args.args[1], "Import schedule…")
        self.view.set_language("ru")
        self.assertEqual(widget.QLocale().language(), widget.QLocale.Russian)

    def test_language_switch_preserves_editor_and_schedule(self):
        view = self.view
        event = {"time": "10:00", "title": "Сегодня", "category": "Settings", "duration": 45}
        data = {"weeks": {"А": {"Понедельник": [event]}, "Б": {"Пн": [event, event]}},
                "categories": {"Settings": "#123456"}}
        self.schedule.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        original = self.schedule.read_bytes()
        view.reload()
        view._on_week_name_edited(0, "Неделя А")
        view.select_page(0)
        view.open_editor()
        ref = view._edit_ref
        self.assertEqual(view.cards[0].lbl_title.text(), "Сегодня")
        editor = view.editor
        editor.ed_title.setText("  Meeting {time}  ")
        editor.ed_title.textEdited.emit(editor.ed_title.text())
        editor.ed_dur.setValue(25)
        editor.ed_time.setTime(widget.QTime(9, 15))
        view.open_settings()
        with patch.object(view, "_write_schedule", side_effect=AssertionError("unexpected save")):
            view.set_language("en")
            self.assertEqual(view.settings.sw_biweekly.accessibleName(), "Alternating weeks")
            view.close_settings()
            self.assertTrue(editor.isVisible())
            self.assertEqual(view._page(), 0)
            self.assertIs(view._edit_ref, ref)
            self.assertEqual(editor.ed_time.time(), widget.QTime(9, 15))
            self.assertEqual(editor.ed_dur.value(), 25)
            self.assertEqual(editor.ed_title.text(), "  Meeting {time}  ")
            self.assertEqual(editor.ed_cat.currentText(), "Settings")
            self.assertEqual(view.week_name(0), "Неделя А")
            self.assertEqual(view.week_name(1), "Week B")
            self.assertEqual(editor.btn_delete.text(), "Delete")
            self.assertEqual(view.pills.button(0).toolTip(), "Monday")
            self.assertEqual(self.schedule.read_bytes(), original)
            view.set_language("ru")
            self.assertEqual(editor.btn_delete.text(), "Удалить")
            self.assertEqual(self.schedule.read_bytes(), original)
        view._commit()
        self.assertEqual(ref, {**event, "time": "09:15", "duration": 25, "title": "  Meeting {time}  "})

    @patch("widget.current_time", return_value=datetime(2026, 11, 16, 10, 30))
    def test_localized_status_menus_and_errors(self, _now):
        view = self.view
        view.set_biweekly(False)
        view._load(json.dumps({"days": {"Пн": [{"time": "10:00", "title": "СЕЙЧАС", "duration": 31}]},
                               "weeks": {"a": {}, "b": {}}}))
        view.tray_menu = view._build_tray_menu()
        view.tray = widget.QSystemTrayIcon(view)
        view.go_today()
        view.set_compact(True)
        view.set_language("en")
        self.assertEqual(view.mini_left.text(), "1 min left")
        self.assertIn("СЕЙЧАС", view.mini.toolTip())
        self.assertIn("1 min remaining", view.tray.toolTip())
        self.assertEqual(view.lbl_date.text(), "Today · November 16")
        self.assertEqual(view.act_compact.text(), "Expand")
        self.assertIn("Quit", [action.text() for action in view.tray_menu.actions()])
        view._load("[]")
        view.open_editor()
        self.assertIn('Expected an object with a "days" section', view.lbl_error.text())
        view.set_language("ru")
        self.assertIn("Исправьте файл", view.lbl_error.text())
        self.assertIn("16 ноября", view.lbl_date.text())

    def test_copy_confirmation_plural_forms_update_in_place(self):
        view = self.view
        view.select_page(0)
        view.open_editor()
        for count, noun in ((1, "слот"), (2, "слота"), (11, "слотов"), (21, "слот")):
            view.editor.ask_copy_confirm(count)
            self.assertIn(f"{count} {noun} ", view.editor.btn_copy.text())
            view.set_language("en")
            english = "slot" if count == 1 else "slots"
            self.assertEqual(view.editor.btn_copy.text(), f"Replace {count} {english} in “Week B”?")
            self.assertEqual(view.editor.btn_copy.toolTip(), "Click again to replace")
            view.set_language("ru")
            self.assertEqual(view.editor.btn_copy.toolTip(), "Нажмите еще раз, чтобы заменить")

    def import_source(self, data):
        source = self.schedule.with_name("incoming.json")
        source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8-sig")
        return source

    def choose_import(self, path):
        with (patch.object(widget.QFileDialog, "getOpenFileName", return_value=(str(path), "")),
              patch.object(widget.sys, "excepthook") as qt_error):
            action = next(a for a in self.view.menu.actions() if a.text() == self.view.ui("Import schedule…"))
            action.trigger()
            if qt_error.called:
                _, error, traceback = qt_error.call_args.args
                raise error.with_traceback(traceback)

    def test_cancel_import_keeps_file_model_and_pending_edit(self):
        view = self.view
        view.select_page(0)
        view.open_editor()
        ref = view._edit_ref
        view._on_field_changed("title", "Незаконченная правка", False)
        original = self.schedule.read_bytes()
        source = self.import_source({"days": {"Пн": [{"time": "18:00", "title": "Встреча"}]}})
        source_bytes = source.read_bytes()
        displayed = {}

        def cancel_dialog(dialog):
            displayed["details"] = dialog.findChild(widget.QPlainTextEdit).toPlainText()
            buttons = dialog.findChild(widget.QDialogButtonBox)
            displayed["buttons"] = [b.text() for b in buttons.buttons()]
            next(b for b in buttons.buttons() if buttons.buttonRole(b) == widget.QDialogButtonBox.RejectRole).click()
            return dialog.result()

        with (patch.object(widget.QDialog, "exec", cancel_dialog),
              patch.object(view, "_load", side_effect=AssertionError("preview modified the model")),
              patch.object(widget, "write_text_atomic") as write):
            self.choose_import(source)
            write.assert_not_called()
        self.assertCountEqual(displayed["buttons"], ["Импортировать", "Отмена"])
        self.assertIn("Незаконченная правка", displayed["details"])
        self.assertIn("Встреча", displayed["details"])
        self.assertEqual(self.schedule.read_bytes(), original)
        self.assertEqual(source.read_bytes(), source_bytes)
        self.assertIs(view._edit_ref, ref)
        self.assertTrue(view._dirty and view._commit_timer.isActive())

    def test_import_days_with_bom_and_aliases_commits_previewed_copy(self):
        view = self.view
        view.set_language("en")
        view.select_page(0)
        view.open_editor()
        first = {"time": "09:00", "title": "Занятие", "category": "Class"}
        second = {"time": "12:30", "title": "Lunch"}
        source = self.import_source({"days": {"Понедельник": [second], "mon": [first]},
                                     "categories": {"Class": "#123456"}})
        original = self.schedule.read_bytes()

        def confirm(path, summary, details, revised):
            self.assertEqual(path, source)
            self.assertFalse(revised)
            self.assertEqual(self.schedule.read_bytes(), original)
            for section in ("Regular schedule", "Week A", "Week B"):
                self.assertIn(section + "\nMonday", details)
            self.assertIn('#123456', details)
            self.assertIn("Events added:", summary)
            # Approval is for the displayed snapshot, even if the source is edited.
            source.write_bytes(b"{}")
            return True

        with (patch.object(view, "_confirm_import", side_effect=confirm),
              patch.object(widget, "write_text_atomic", wraps=widget.write_text_atomic) as write):
            self.choose_import(source)
        write.assert_called_once()
        self.assertEqual(write.call_args.args[0], self.schedule)
        self.assertEqual(self.schedule.read_text("utf-8"), view._loaded_text)
        saved = json.loads(view._loaded_text)
        self.assertEqual(saved["days"]["mon"], [first, second])
        self.assertEqual(saved["weeks"], {"a": saved["days"], "b": saved["days"]})
        self.assertEqual(view.file_categories, {"Class": "#123456"})
        self.assertIsNot(view.raw_single[0][0], view.raw_weeks[0][0][0])
        self.assertIsNot(view.raw_weeks[0][0][0], view.raw_weeks[1][0][0])
        self.assertEqual(view.cards[0].lbl_title.text(), "Занятие")
        self.assertIs(view._edit_ref, view.raw_weeks[0][0][0])

    def test_import_no_changes_skips_confirmation_and_write(self):
        data = {"Пн": [{"time": "10:00", "title": "Lesson"}]}
        self.schedule.write_text(json.dumps({"days": data, "weeks": {"a": data, "b": data}}), "utf-8")
        self.view.reload()
        source = self.import_source({"days": {"mon": data["Пн"]}})
        original = self.schedule.read_bytes()
        model = self.view.raw_single
        with (patch.object(widget.QMessageBox, "information") as info,
              patch.object(self.view, "_confirm_import") as confirm,
              patch.object(widget, "write_text_atomic") as write):
            self.choose_import(source)
        confirm.assert_not_called()
        write.assert_not_called()
        self.assertEqual(info.call_args.args[-1], "Изменений нет.")
        self.assertEqual(self.schedule.read_bytes(), original)
        self.assertIs(self.view.raw_single, model)

    def test_import_read_and_parse_errors_preserve_schedule(self):
        path = self.schedule.with_name("incoming.json")
        view = self.view
        original = self.schedule.read_bytes()
        model = view.raw_single
        for lang, payload, reason in (("en", None, "File not found"), ("en", b"[]", 'Expected an object'),
                                      ("ru", b"{\n?", "Ошибка JSON в строке 2, столбце 1"),
                                      ("ru", b"\xff", "Некорректный UTF-8")):
            with self.subTest(reason=reason):
                view.set_language(lang)
                if payload is not None:
                    path.write_bytes(payload)
                with (patch.object(widget.QMessageBox, "warning") as warning,
                      patch.object(view, "_confirm_import") as confirm,
                      patch.object(widget, "write_text_atomic") as write):
                    self.choose_import(path)
                confirm.assert_not_called()
                write.assert_not_called()
                self.assertIn(reason, warning.call_args.args[-1])
                self.assertEqual(self.schedule.read_bytes(), original)
                self.assertIs(view.raw_single, model)
                self.assertIsNone(view._error)

    def test_import_write_failure_keeps_editor_and_disk(self):
        view = self.view
        view.select_page(0)
        view.open_editor()
        ref, card = view._edit_ref, view.cards[0]
        source = self.import_source({"days": {}})
        original = self.schedule.read_bytes()
        with (patch.object(view, "_confirm_import", return_value=True),
              patch.object(widget.os, "replace", side_effect=PermissionError("locked")),
              patch.object(widget.QMessageBox, "warning") as warning):
            self.choose_import(source)
        self.assertIn("Отказано в доступе", warning.call_args.args[-1])
        self.assertIn("locked", warning.call_args.args[-1])
        self.assertEqual(self.schedule.read_bytes(), original)
        self.assertIs(view._edit_ref, ref)
        self.assertIs(view.cards[0], card)
        self.assertFalse(list(self.schedule.parent.glob("*.tmp")))

    def test_import_inactive_weeks_duplicate_times_fields_and_colors(self):
        view = self.view
        view.set_language("en")
        view.set_biweekly(False)
        x = {"time": "10:00", "title": "X"}
        y = {**x, "title": "Y"}
        moved = {"time": "08:00", "title": "Standup", "duration": 10}
        a = {"mon": [x, x, x, y], "tue": [moved]}
        b = {"fri": [y]}
        self.schedule.write_text(json.dumps({"days": {}, "weeks": {"a": a, "b": b},
                                             "categories": {"Old": "red", "Work": "#112233"}}), "utf-8")
        view.reload()
        new_a = {"Пн": [x, {**x, "title": "U"}, {**x, "title": "V"}],
                 "Вт": [{**moved, "time": "08:15", "duration": 20, "category": "Work"}]}
        source = self.import_source({"days": {}, "weeks": {"1": new_a, "Б": {}},
                                     "categories": {"New": "blue", "Work": "#334455"}})
        with patch.object(view, "_confirm_import", return_value=True) as confirm:
            self.choose_import(source)
        _, summary, details, _ = confirm.call_args.args
        self.assertIn("Events added: 2 · removed: 4 · edited: 1. Category changes: 3.", summary)
        self.assertIn("Regular schedule\nNo changes.", details)
        self.assertIn("Week A\nMonday", details)
        self.assertIn("Week B\nFriday", details)
        self.assertEqual(details.count('− Start time: "10:00" · Task title: "X"'), 2)
        self.assertIn("Duration: 10 → 20", details)
        self.assertIn('Start time: "08:00" → "08:15"', details)
        self.assertIn('Category: Absent → "Work"', details)
        self.assertIn('"Work": #112233 → #334455', details)
        self.assertIn('− "Old": red', details)
        self.assertIn('+ "New": blue', details)
        self.assertFalse(any(view.days))
        view.set_biweekly(True)
        self.assertEqual([slot.title for slot in view.days[0]], ["X", "U", "V"])
        self.assertFalse(any(view.days[7:]))

    def test_import_rechecks_external_save_before_watcher_and_after_reload(self):
        source = self.import_source({"days": {}})
        for watcher_ran in (False, True):
            with self.subTest(watcher_ran=watcher_ran):
                def external_save(title):
                    data = {"days": {"mon": [{"title": title, "time": "12:00"}]}, "weeks": {"a": {}, "b": {}}}
                    self.schedule.write_text(json.dumps(data), "utf-8")

                external_save("Original")
                self.view.reload()
                reports = []

                def confirm(path, summary, details, revised):
                    reports.append((revised, details))
                    if not revised:
                        external_save("External edit")
                        if watcher_ran:
                            self.view.reload()
                    return True

                with (patch.object(self.view, "_confirm_import", side_effect=confirm),
                      patch.object(widget, "write_text_atomic", wraps=widget.write_text_atomic) as write):
                    self.choose_import(source)
                self.assertEqual([revised for revised, _ in reports], [False, True])
                self.assertIn("Original", reports[0][1])
                self.assertIn("External edit", reports[1][1])
                write.assert_called_once()
                self.assertFalse(any(self.view.raw_single))

    def test_import_only_week_b_seeds_regular_and_week_a(self):
        source = self.import_source({"weeks": {"Б": {"ср": [{"time": "09:00", "title": "Studio"}]}}})
        with patch.object(self.view, "_confirm_import", return_value=True):
            self.choose_import(source)
        saved = json.loads(self.schedule.read_text("utf-8"))
        self.assertEqual(saved["days"]["wed"], [{"time": "09:00", "title": "Studio"}])
        self.assertEqual(saved["weeks"], {"a": saved["days"], "b": saved["days"]})

    def test_import_same_start_unique_titles_and_order(self):
        a = {"time": "10:00", "title": "Alpha"}
        b = {"time": "10:00", "title": "Beta"}
        baseline = {"days": {"mon": [a, b]}, "weeks": {"a": {}, "b": {}}}
        self.schedule.write_text(json.dumps(baseline), "utf-8")
        self.view.reload()
        self.view.set_language("en")
        for day, edits in (([b, a], 0), ([{**a, "duration": 5}, {**b, "duration": 10}], 2)):
            with self.subTest(day=day):
                source = self.import_source({**baseline, "days": {"mon": day}})
                with patch.object(self.view, "_confirm_import", return_value=False) as confirm:
                    self.choose_import(source)
                _, summary, details, _ = confirm.call_args.args
                self.assertIn(f"Events added: 0 · removed: 0 · edited: {edits}.", summary)
                self.assertIn("Event order at 10:00", details)

    def test_import_external_change_to_candidate_becomes_no_op(self):
        source = self.import_source({"days": {}, "weeks": {"a": {}, "b": {}}})

        def confirm(*args):
            self.schedule.write_bytes(source.read_bytes())
            return True

        with (patch.object(self.view, "_confirm_import", side_effect=confirm) as preview,
              patch.object(widget.QMessageBox, "information") as info,
              patch.object(widget, "write_text_atomic") as write):
            self.choose_import(source)
        preview.assert_called_once()
        write.assert_not_called()
        self.assertEqual(info.call_args.args[-1], "Изменений нет.")
        self.assertEqual(self.schedule.read_bytes(), source.read_bytes())

    def test_watcher_reloads_external_edit_after_import(self):
        source = self.import_source({"days": {}})
        with patch.object(self.view, "_confirm_import", return_value=True):
            self.choose_import(source)
        updated = {"days": {}, "weeks": {"a": {"mon": [{"time": "09:00", "title": "New on disk"}]}, "b": {}}}
        loop = QEventLoop()
        timeout = QTimer(singleShot=True, interval=4000)
        timeout.timeout.connect(loop.quit)
        self.view._reload_timer.timeout.connect(loop.quit)
        widget.write_text_atomic(self.schedule, json.dumps(updated))
        timeout.start()
        loop.exec()
        self.assertEqual([s.title for s in self.view.days[0]], ["New on disk"])


if __name__ == "__main__":
    unittest.main()
