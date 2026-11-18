#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Weekline desktop schedule widget. Run with python widget.py.
"""
from __future__ import annotations

import copy
import errno
import json
import math
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import partial
from pathlib import Path

from PySide6.QtCore import (
    QEvent, QFileSystemWatcher, QLibraryInfo, QLocale, QTranslator, QPoint, QRect, QRectF, QSettings, QSize, Qt, QTime, QTimer, QUrl, Signal,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QIcon,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QButtonGroup,
    QComboBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QTabBar,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QCheckBox,
    QSystemTrayIcon,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)


from schedule_diff import schedule_changes
from translations import InterfaceText


# ───────────────────────────── пути и константы ─────────────────────────────

IS_FROZEN = bool(getattr(sys, "frozen", False))  # собран PyInstaller'ом


def _app_dir() -> Path:
    # В сборке --onefile __file__ указывает во временную распаковку (sys._MEIPASS),
    # которая удаляется после выхода, поэтому данные храним рядом с самим .exe.
    if IS_FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = _app_dir()
EXAMPLE_PATH = Path(__file__).resolve().with_name("schedule.example.json")
SCHEDULE_PATH = APP_DIR / "schedule.json"
CONFIG_PATH = APP_DIR / "config.json"
LEGACY_SETTINGS_PATH = APP_DIR / "widget_settings.ini"  # старый формат, читается один раз для миграции

IS_WINDOWS = sys.platform == "win32"
LEVEL_TOP, LEVEL_DESKTOP = "top", "desktop"


# ───────────────────────────── автозапуск (реестр) ──────────────────────────

AUTOSTART_NAME = "ScheduleWidget"
AUTOSTART_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# Сюда «Диспетчер задач → Автозагрузка» пишет флаг «Отключено» для записей из Run
AUTOSTART_APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"

if IS_WINDOWS:
    import winreg


def autostart_command() -> str:
    """Команда для HKCU\\...\\Run: сам .exe сборки или pythonw.exe + путь к скрипту."""
    if IS_FROZEN:
        return f'"{Path(sys.executable).resolve()}"'
    # abspath, а не resolve(): у venv с симлинками resolve() увел бы к базовому Python без пакетов
    exe = Path(os.path.abspath(sys.executable))
    pythonw = exe.with_name("pythonw.exe")  # без консольного окна
    if pythonw.exists():
        exe = pythonw
    return f'"{exe}" "{Path(__file__).resolve()}"'


def _autostart_value() -> str | None:
    if not IS_WINDOWS:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, AUTOSTART_NAME)
    except OSError:
        return None
    return value if isinstance(value, str) and value.strip() else None


def _autostart_disabled_by_user() -> bool:
    """Запись есть, но отключена в Диспетчере задач (младший бит первого байта = 1)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_APPROVED_KEY) as key:
            data, _ = winreg.QueryValueEx(key, AUTOSTART_NAME)
    except OSError:
        return False
    return isinstance(data, bytes) and len(data) > 0 and bool(data[0] & 1)


def autostart_enabled() -> bool:
    return _autostart_value() is not None and not _autostart_disabled_by_user()


def set_autostart(on: bool) -> None:
    """Включить / выключить автозапуск. Ошибки реестра пробрасываются как OSError."""
    if not IS_WINDOWS:
        raise OSError("Startup is only supported on Windows")
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, AUTOSTART_NAME)
            except FileNotFoundError:
                pass
    # Снять (или не оставлять) отметку «Отключено» из Диспетчера задач — иначе запись в Run игнорируется
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_APPROVED_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, AUTOSTART_NAME)
    except OSError:
        pass


def repair_autostart() -> None:
    """Если автозапуск включен, но .exe / скрипт переехал — переписать путь на текущий."""
    value = _autostart_value()
    if value is None or value == autostart_command() or _autostart_disabled_by_user():
        return
    quoted = value.split('"')[1::2]  # пути в кавычках: "exe" ["script"]
    if not quoted or any(not Path(p).exists() for p in quoted):
        try:
            set_autostart(True)
        except OSError:
            pass

DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_SHORT = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
DAY_FULL = ("Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье")

# В schedule.json день можно указать как "mon", "Пн" или "Понедельник"
DAY_ALIASES: dict[str, int] = {}
for _i, _names in enumerate(zip(DAY_KEYS, DAY_SHORT, DAY_FULL)):
    for _n in _names:
        DAY_ALIASES[_n.lower()] = _i

# Двухнедельный режим: ключи недель в schedule.json и названия по умолчанию
WEEK_KEYS = ("a", "b")
WEEK_ALIASES = {"a": 0, "а": 0, "1": 0, "b": 1, "б": 1, "2": 1}  # латиница, кириллица, номер
DEFAULT_WEEK_KEYS = ("Week A", "Week B")
WEEK_NAME_MAX = 16

DEFAULT_CATEGORIES = {"Общее": "#4C8DFF"}
FALLBACK_COLOR = "#8E8E9A"

FONT_TEXT = ["Segoe UI Variable Text", "Segoe UI", "Inter", "Helvetica Neue", "Arial"]
DEFAULT_ACCENT = "#4C8DFF"

WINDOW_BORDER = QColor(255, 255, 255, 26)  # rgba(255,255,255,0.1)
ACCENT = QColor(DEFAULT_ACCENT)
RADIUS = 16
GLOW = 5  # поле вокруг карточки под свечение активного слота


def current_time() -> datetime:
    """Единая точка получения «сейчас» (удобно подменять в тестах)."""
    return datetime.now()


HELP_TEXT = ("time — начало ЧЧ:ММ, слот длится до следующего; "
             "duration — необязательная длительность в минутах; "
             "category — ключ из categories (цвет в HEX). "
             "days — единое расписание (двухнедельный режим выключен); "
             "weeks.a / weeks.b — чередующиеся недели А и Б (режим включен, см. config.json). "
             "Файл перечитывается автоматически после сохранения.")


def _dump_days(days: dict, indent: int) -> str:
    """Блок дней недели: один слот — одна строка. indent — отступ строки с ключом блока."""
    pad = " " * indent
    blocks = []
    for key in DAY_KEYS:
        items = days.get(key, [])
        if items:
            rows = ",\n".join(f"{pad}    " + json.dumps(s, ensure_ascii=False) for s in items)
            blocks.append(f'{pad}  "{key}": [\n{rows}\n{pad}  ]')
        else:
            blocks.append(f'{pad}  "{key}": []')
    return "{\n" + ",\n".join(blocks) + f"\n{pad}}}"


def dump_schedule(data: dict) -> str:
    """Компактный JSON: один слот — одна строка, удобно править руками."""
    cats = ",\n".join(f"    {json.dumps(k, ensure_ascii=False)}: {json.dumps(v)}"
                      for k, v in data["categories"].items())
    text = ("{\n"
            f'  "_help": {json.dumps(HELP_TEXT, ensure_ascii=False)},\n'
            f'  "categories": {{\n{cats}\n  }},\n'
            f'  "days": {_dump_days(data["days"], 2)}')
    weeks = data.get("weeks")
    if weeks:
        parts = [f'    "{k}": {_dump_days(weeks.get(k, {}), 4)}' for k in WEEK_KEYS]
        text += ',\n  "weeks": {\n' + ",\n".join(parts) + "\n  }"
    return text + "\n}\n"


# ───────────────────────────── модель данных ────────────────────────────────

@dataclass(frozen=True)
class Slot:
    start: int  # минуты от полуночи
    end: int    # может быть > 1440 (слот переходит через полночь)
    title: str
    category: str
    # исходная запись из schedule.json — через неё редактор меняет слот
    ref: dict | None = field(default=None, compare=False, hash=False, repr=False)

    def contains(self, minute: int) -> bool:
        return self.start <= minute < self.end


def parse_time(value: str) -> int:
    h, m = str(value).strip().split(":")
    h, m = int(h), int(m)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(value)
    return h * 60 + m


def normalize_days(raw_days: dict) -> list[list]:
    """Записи schedule.json по 7 дням недели (ключи-синонимы вроде "Пн" сливаются).

    Словари копируются: редактор меняет их, не задевая исходные данные."""
    out: list[list] = [[] for _ in range(7)]
    for key, items in raw_days.items():
        idx = DAY_ALIASES.get(str(key).strip().lower())
        if idx is None or not isinstance(items, list):
            continue
        out[idx].extend(dict(it) if isinstance(it, dict) else it for it in items)
    return out


def slot_sort_key(item) -> tuple[int, int]:
    """Ключ сортировки записей по времени начала; битые записи уходят в конец."""
    try:
        return 0, parse_time(item["time"])
    except (KeyError, ValueError, TypeError, AttributeError):
        return 1, 0


def build_slots(norm_days: list[list]) -> list[list[Slot]]:
    """Слоты по дням «ленты». Лента — 7 дней (обычный режим) или 14 (неделя А + неделя Б);
    за последним днем ленты снова идет первый, поэтому последний слот недели Б может
    продолжиться в понедельник недели А."""
    n = len(norm_days)
    raw: list[list[tuple]] = [[] for _ in range(n)]
    for idx, items in enumerate(norm_days):
        for it in items:
            try:
                start = parse_time(it["time"])
                title = str(it.get("title", ""))
                cat = str(it.get("category", "Общее"))
                dur = int(it["duration"]) if it.get("duration") else 0
            except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
                continue
            raw[idx].append((start, title, cat, dur, it))
    for items in raw:
        items.sort(key=lambda x: x[0])

    days: list[list[Slot]] = []
    for d in range(n):
        items, tomorrow = raw[d], raw[(d + 1) % n]
        slots = []
        for i, (start, title, cat, dur, ref) in enumerate(items):
            if i + 1 < len(items):
                limit = items[i + 1][0]
            elif tomorrow:
                limit = tomorrow[0][0] + 1440  # последний слот тянется до первого слота завтра
            else:
                limit = 1440
            end = min(start + dur, limit) if dur > 0 else limit
            slots.append(Slot(start, end, title, cat, ref))
        days.append(slots)
    return days


@dataclass
class ScheduleData:
    days: list[list]             # единое расписание по дням (как в файле), его правит редактор
    weeks: list[list]            # недели А и Б, включая независимые копии отсутствующих недель
    file_categories: dict        # категории ровно в том виде, как они записаны в файле
    categories: dict[str, str]   # категории файла + встроенные пресеты
    needs_migration: bool = False

    def sorted_text(self) -> str:
        for week in (self.days, *self.weeks):
            for items in week:
                items.sort(key=slot_sort_key)
        return dump_schedule({
            "categories": self.file_categories,
            "days": dict(zip(DAY_KEYS, self.days)),
            "weeks": {k: dict(zip(DAY_KEYS, wk)) for k, wk in zip(WEEK_KEYS, self.weeks)},
        })


class InterfaceError(ValueError):
    """A schedule validation error with a translatable message."""


def parse_schedule(text: str) -> ScheduleData:
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise InterfaceError("Expected an object with a \"days\" section")
    weeks: list[list | None] = [None, None]
    if isinstance(raw.get("weeks"), dict):
        for key, wk in raw["weeks"].items():
            idx = WEEK_ALIASES.get(str(key).strip().lower())
            if idx is not None and isinstance(wk, dict):
                weeks[idx] = normalize_days(wk)
    if isinstance(raw.get("days"), dict):
        days = normalize_days(raw["days"])
    elif weeks[0] is not None or weeks[1] is not None:
        days = copy.deepcopy(weeks[0] if weeks[0] is not None else weeks[1])
    else:
        raise InterfaceError("Expected an object with a \"days\" or \"weeks\" section")
    file_cats: dict[str, str] = {}
    if isinstance(raw.get("categories"), dict):
        for k, v in raw["categories"].items():
            if QColor(str(v)).isValid():
                file_cats[str(k)] = str(v)
    complete_weeks = [wk if wk is not None else copy.deepcopy(days) for wk in weeks]
    return ScheduleData(days, complete_weeks, file_cats, {**DEFAULT_CATEGORIES, **file_cats},
                        None in weeks)


def same_day_items(a: list, b: list) -> bool:
    """Совпадают ли два дня по содержимому (порядок записей не важен)."""
    def canon(items: list) -> list[str]:
        return sorted(json.dumps(it, ensure_ascii=False, sort_keys=True) for it in items)
    return canon(a) == canon(b)


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def week_parity(anchor: date | None, anchor_week: int, day: date) -> int:
    """Какая неделя (0 — А, 1 — Б) идет в день day, если неделя с понедельником anchor — anchor_week."""
    if anchor is None:
        return anchor_week
    return (anchor_week + (monday_of(day) - monday_of(anchor)).days // 7) % 2


def write_text_atomic(path: Path, text: str) -> None:
    """Запись через временный файл: при сбое старый файл останется целым."""
    stream = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False)
    tmp = Path(stream.name)
    try:
        with stream:
            stream.write(text)
        # Windows может кратковременно удерживать файл во время проверки антивирусом.
        for attempt in range(4):
            try:
                os.replace(tmp, path)
                break
            except PermissionError as exc:
                if getattr(exc, "winerror", None) not in (5, 32) or attempt == 3:
                    raise
                time.sleep(0.01 * 2 ** attempt)
    finally:
        tmp.unlink(missing_ok=True)


def find_active(days: list[list[Slot]], wd: int, minute: int) -> tuple[int, int] | None:
    """(день, индекс) слота, идущего сейчас. Учитывает слоты, перешедшие через полночь.

    wd — индекс сегодняшнего дня в ленте (0..6 или 0..13 в двухнедельном режиме)."""
    for i, s in enumerate(days[wd]):
        if s.contains(minute):
            return wd, i
    prev = (wd - 1) % len(days)
    if days[prev] and days[prev][-1].contains(minute + 1440):
        return prev, len(days[prev]) - 1
    return None


def find_next(days: list[list[Slot]], wd: int, minute: int) -> tuple[Slot, int] | None:
    """Ближайший начинающийся слот и через сколько минут он начнётся."""
    for s in days[wd]:
        if s.start > minute:
            return s, s.start - minute
    n = len(days)
    for ahead in range(1, n + 1):
        d = (wd + ahead) % n
        if days[d]:
            s = days[d][0]
            return s, s.start + 1440 * ahead - minute
    return None


def fmt_time(minute: int) -> str:
    minute %= 1440
    return f"{minute // 60:02d}:{minute % 60:02d}"


# ───────────────────────────── config.json ──────────────────────────────────

CONFIG_HELP = ('language — язык интерфейса ("ru" или "en"); '
               'x, y — позиция левого верхнего угла; locked — запрет перетаскивания; '
               'window_level — "top" (поверх всех окон) или "desktop" (на рабочем столе); '
               'compact — свернут в мини-плашку; biweekly — двухнедельный режим (недели А и Б); '
               'week_names — названия недель; week_anchor — понедельник (ГГГГ-ММ-ДД) недели '
               'week_anchor_week ("a" или "b"), от него недели чередуются автоматически.')


def _legacy_settings() -> dict:
    """Позиция и «поверх окон» из старого widget_settings.ini (если он есть)."""
    if not LEGACY_SETTINGS_PATH.exists():
        return {}
    s = QSettings(str(LEGACY_SETTINGS_PATH), QSettings.IniFormat)
    out: dict = {"window_level": LEVEL_TOP if s.value("on_top", False, type=bool) else LEVEL_DESKTOP}
    pos = s.value("pos")
    if isinstance(pos, QPoint):
        out.update(x=pos.x(), y=pos.y())
    return out


@dataclass
class WidgetConfig:
    x: int | None = None
    y: int | None = None
    locked: bool = False
    window_level: str = LEVEL_DESKTOP
    compact: bool = False
    biweekly: bool = True                       # двухнедельный режим (по умолчанию включен)
    week_names: list = field(default_factory=lambda: ["", ""])
    week_anchor: date | None = None             # понедельник опорной недели
    week_anchor_week: int = 0                   # какая это была неделя: 0 — А, 1 — Б
    font_family: str = ""
    font_offset: int = 0
    accent: str = DEFAULT_ACCENT
    width: int = 392
    opacity: int = 90
    background: str = "#141418"
    dense: bool = False
    show_clock: bool = True
    show_categories: bool = True
    language: str = "ru"
    extra: dict = field(default_factory=dict, repr=False)  # чужие ключи не теряем при сохранении

    KEYS = ("language", "x", "y", "locked", "window_level", "compact",
            "biweekly", "week_names", "week_anchor", "week_anchor_week",
            "font_family", "font_offset", "accent", "width", "opacity", "background", "dense", "show_clock", "show_categories")

    @classmethod
    def load(cls, path: Path) -> "WidgetConfig":
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            raw = _legacy_settings()
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}

        def as_int(v) -> int | None:
            if type(v) is int or (type(v) is float and math.isfinite(v)):
                return max(-2147483648, min(int(v), 2147483647))
            return None

        def bounded(key, default, low, high):
            value = as_int(raw.get(key))
            return default if value is None else max(low, min(value, high))

        names = ["", ""]
        if isinstance(raw.get("week_names"), list):
            for i, v in enumerate(raw["week_names"][:2]):
                if isinstance(v, str) and v.strip():
                    names[i] = v
        try:
            anchor = date.fromisoformat(str(raw["week_anchor"])) if raw.get("week_anchor") else None
        except ValueError:
            anchor = None

        return cls(
            language="en" if raw.get("language") == "en" else "ru",
            font_family=raw["font_family"] if isinstance(raw.get("font_family"), str) else "",
            font_offset=bounded("font_offset", 0, -1, 3),
            accent=QColor(raw["accent"]).name() if isinstance(raw.get("accent"), str)
                   and QColor(raw["accent"]).isValid() else DEFAULT_ACCENT,
            width=bounded("width", 392, 392, 640),
            background=raw.get("background") if raw.get("background") in ("#141418", "#17232e", "#24201e") else "#141418",
            dense=raw.get("dense") is True,
            show_clock=raw.get("show_clock") is not False,
            show_categories=raw.get("show_categories") is not False,
            opacity=bounded("opacity", 90, 45, 100),
            x=as_int(raw.get("x")),
            y=as_int(raw.get("y")),
            locked=raw.get("locked") is True,
            window_level=LEVEL_TOP if raw.get("window_level") == LEVEL_TOP else LEVEL_DESKTOP,
            compact=raw.get("compact") is True,
            biweekly=raw.get("biweekly") is not False,  # нет ключа (старый config) — включен
            week_names=names,
            week_anchor=monday_of(anchor) if anchor else None,
            week_anchor_week=WEEK_ALIASES.get(str(raw.get("week_anchor_week", "a")).strip().lower(), 0),
            extra={k: v for k, v in raw.items() if k not in cls.KEYS and k != "_help"},
        )

    def save(self, path: Path) -> None:
        data = {"_help": CONFIG_HELP, **self.extra}
        data.update({k: getattr(self, k) for k in self.KEYS})
        data["week_anchor"] = self.week_anchor.isoformat() if self.week_anchor else None
        data["week_anchor_week"] = WEEK_KEYS[self.week_anchor_week]
        write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


# ───────────────────────────── уровень «на рабочем столе» (Win32) ───────────

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    try:
        _dwmapi = ctypes.WinDLL("dwmapi")
    except OSError:
        _dwmapi = None

    class _WINDOWPOS(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("hwndInsertAfter", wintypes.HWND),
                    ("x", ctypes.c_int), ("y", ctypes.c_int),
                    ("cx", ctypes.c_int), ("cy", ctypes.c_int), ("flags", wintypes.UINT)]

    _WINEVENTPROC = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
                                       wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD)

    _user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, wintypes.UINT]
    _user32.SetWinEventHook.restype = wintypes.HANDLE
    _user32.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, _WINEVENTPROC,
                                        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
    _user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetTopWindow.restype = wintypes.HWND
    _user32.GetTopWindow.argtypes = [wintypes.HWND]
    _user32.GetWindow.restype = wintypes.HWND
    _user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    _user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsIconic.argtypes = [wintypes.HWND]
    _user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]


class DesktopLayer:
    """Режим «На рабочем столе» для Windows.

    • Окно всегда держится в самом низу z-порядка — сразу над обоями и иконками,
      под браузерами и IDE (даже после клика по нему).
    • Win+D («Показать рабочий стол») прячет обычные окна за рабочим столом.
      Когда рабочий стол становится активным и виджет ничем не перекрыт, он временно
      поднимается поверх (без активации), а как только пользователь переключается
      в обычное окно — снова опускается вниз.
    """

    HWND_BOTTOM, HWND_TOPMOST = 1, -1
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_NOOWNERZORDER = 0x1, 0x2, 0x4, 0x10, 0x200
    WM_WINDOWPOSCHANGING = 0x0046
    EVENT_SYSTEM_FOREGROUND = 0x0003
    WINEVENT_OUTOFCONTEXT, WINEVENT_SKIPOWNPROCESS = 0x0, 0x2
    GWL_EXSTYLE, WS_EX_TOPMOST, WS_EX_TRANSPARENT = -20, 0x8, 0x20
    GW_HWNDNEXT = 2
    DWMWA_CLOAKED = 14

    DESKTOP_CLASSES = frozenset({"Progman", "WorkerW"})
    # окна оболочки, которые появляются поверх показанного рабочего стола (Пуск, поиск, Alt+Tab…)
    SHELL_CLASSES = frozenset({"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow",
                               "XamlExplorerHostIslandWindow", "ForegroundStaging", "MultitaskingViewFrame",
                               "TaskSwitcherWnd", "NotifyIconOverflowWindow",
                               "TopLevelWindowForOverflowXamlIsland"})

    def __init__(self, widget: QWidget):
        self.widget = widget
        self.enabled = False
        self.raised = False  # поднят над показанным рабочим столом
        self._hook = None
        self._proc = _WINEVENTPROC(self._on_foreground)  # ссылка обязательна, иначе GC убьёт callback

    # ── публичное ──

    def set_enabled(self, on: bool) -> None:
        self.enabled = bool(on)
        self.raised = False
        if self.enabled:
            if not self._hook:
                self._hook = _user32.SetWinEventHook(
                    self.EVENT_SYSTEM_FOREGROUND, self.EVENT_SYSTEM_FOREGROUND, None, self._proc, 0, 0,
                    self.WINEVENT_OUTOFCONTEXT | self.WINEVENT_SKIPOWNPROCESS)
            self._set_z(self.HWND_BOTTOM)
            self.check(_user32.GetForegroundWindow())
        elif self._hook:
            _user32.UnhookWinEvent(self._hook)
            self._hook = None

    def filter_message(self, message_ptr: int) -> None:
        """Вызывается из nativeEvent: любые попытки поднять окно превращаем в «вниз»."""
        if not self.enabled or self.raised:
            return
        msg = wintypes.MSG.from_address(message_ptr)
        if msg.message == self.WM_WINDOWPOSCHANGING and msg.lParam:
            wp = _WINDOWPOS.from_address(msg.lParam)
            if not wp.flags & self.SWP_NOZORDER:
                wp.hwndInsertAfter = self.HWND_BOTTOM

    def check(self, fg) -> None:
        """Реакция на смену активного окна."""
        if not self.enabled or not fg:
            return
        cls = self._class(fg)
        if cls in self.DESKTOP_CLASSES:
            if not self.raised and not self._covered():
                self.raised = True
                self._set_z(self.HWND_TOPMOST)
        elif self.raised and cls not in self.SHELL_CLASSES and not self._exstyle(fg) & self.WS_EX_TOPMOST:
            self.raised = False
            self._set_z(self.HWND_BOTTOM)

    # ── внутреннее ──

    def _hwnd(self) -> int:
        return int(self.widget.winId())

    def _set_z(self, after: int) -> None:
        _user32.SetWindowPos(self._hwnd(), after, 0, 0, 0, 0,
                             self.SWP_NOSIZE | self.SWP_NOMOVE | self.SWP_NOACTIVATE | self.SWP_NOOWNERZORDER)

    def _on_foreground(self, _hook, _event, hwnd, _obj, _child, _thread, _time) -> None:
        try:
            self.check(hwnd)
        except Exception:  # исключение внутри WinAPI-callback'а нельзя выпускать наружу
            pass

    @staticmethod
    def _class(hwnd) -> str:
        buf = ctypes.create_unicode_buffer(128)
        _user32.GetClassNameW(hwnd, buf, 128)
        return buf.value

    def _exstyle(self, hwnd) -> int:
        return _user32.GetWindowLongW(hwnd, self.GWL_EXSTYLE) & 0xFFFFFFFF

    def _cloaked(self, hwnd) -> bool:
        if not _dwmapi:
            return False
        val = wintypes.DWORD(0)
        _dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), self.DWMWA_CLOAKED, ctypes.byref(val), 4)
        return bool(val.value)

    def _covered(self) -> bool:
        """Перекрывает ли виджет какое-нибудь обычное открытое окно.

        Если да — рабочий стол просто кликнули между окнами, а не показали по Win+D,
        и поднимать виджет не нужно (иначе он вылезет поверх браузера).
        """
        me = self._hwnd()
        mine = wintypes.RECT()
        _user32.GetWindowRect(me, ctypes.byref(mine))
        pid = os.getpid()
        h = _user32.GetTopWindow(None)
        for _ in range(4096):
            if not h or h == me:
                return False
            if (_user32.IsWindowVisible(h) and not _user32.IsIconic(h)
                    and not self._exstyle(h) & (self.WS_EX_TOPMOST | self.WS_EX_TRANSPARENT)
                    and self._class(h) not in self.DESKTOP_CLASSES and not self._cloaked(h)):
                owner = wintypes.DWORD(0)
                _user32.GetWindowThreadProcessId(h, ctypes.byref(owner))
                r = wintypes.RECT()
                _user32.GetWindowRect(h, ctypes.byref(r))
                if (owner.value != pid and r.right > r.left and r.bottom > r.top
                        and r.left < mine.right and r.right > mine.left
                        and r.top < mine.bottom and r.bottom > mine.top):
                    return True
            h = _user32.GetWindow(h, self.GW_HWNDNEXT)
        return False


# ───────────────────────────── вспомогательное UI ───────────────────────────

def make_font(px: float, weight: QFont.Weight = QFont.Normal, *, spacing: float = 0.0) -> QFont:
    f = QFont()
    f.setFamilies(QApplication.font().families())
    f.setPixelSize(int(round(px)) + (QApplication.instance().property("fontOffset") or 0))
    f.setWeight(weight)
    if spacing:
        f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    f.setHintingPreference(QFont.PreferNoHinting)
    return f


def rgba(c: QColor, alpha: int) -> str:
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


def icon_font_family() -> str | None:
    families = set(QFontDatabase.families())
    for fam in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
        if fam in families:
            return fam
    return None


def make_app_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(255, 255, 255, 70), 2))
    p.setBrush(QColor("#141418"))
    p.drawRoundedRect(QRectF(3, 3, 58, 58), 15, 15)
    p.setPen(Qt.NoPen)
    for y, w, col in ((17, 34, "#4C8DFF"), (29, 26, "#FF9F43"), (41, 30, "#2ED47A")):
        p.setBrush(QColor(col))
        p.drawRoundedRect(QRectF(15, y, w, 7), 3.5, 3.5)
    p.end()
    return QIcon(pm)


class ElidedLabel(QLabel):
    """QLabel, который обрезает текст многоточием, а полный показывает в подсказке."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setTextFormat(Qt.PlainText)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(20)

    def set_full_text(self, text: str) -> None:
        if text == self._full:
            return
        self._full = text
        self.setAccessibleName(text)
        self._elide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.FontChange and hasattr(self, "_full"):
            self._elide()

    def _elide(self) -> None:
        w = self.contentsRect().width()
        shown = self.fontMetrics().elidedText(self._full, Qt.ElideRight, max(0, w))
        super().setText(shown)
        self.setToolTip(self._full if shown != self._full else "")


class ProgressLine(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(4)
        self._value = 0.0
        self._color = QColor("#4C8DFF")

    def set_value(self, value: float, color: QColor) -> None:
        value = max(0.0, min(1.0, value))
        if abs(value - self._value) < 1e-4 and color == self._color:
            return
        self._value, self._color = value, QColor(color)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        r = QRectF(self.rect())
        rad = r.height() / 2
        p.setBrush(QColor(255, 255, 255, 20))
        p.drawRoundedRect(r, rad, rad)
        if self._value > 0:
            p.setBrush(self._color)
            p.drawRoundedRect(QRectF(r.left(), r.top(), max(r.height(), r.width() * self._value), r.height()),
                              rad, rad)


# ───────────────────────────── карточка слота ───────────────────────────────

class SlotCard(QWidget):
    NORMAL, PAST, ACTIVE = "normal", "past", "active"

    clicked = Signal(object)        # одиночный клик в режиме редактирования (передаёт себя)
    doubleClicked = Signal(object)  # двойной клик — открыть редактор на этом слоте

    def __init__(self, slot: Slot, color: QColor, parent: QWidget | None = None):
        super().__init__(parent)
        self.slot = slot
        self.color = QColor(color)
        self.state = None
        self.progress = 0.0
        self._hover = False
        self.selected = False
        self.selectable = False  # True, пока открыт редактор: клик выбирает слот

        grid = QGridLayout(self)
        grid.setContentsMargins(GLOW + 18, GLOW + 10, GLOW + 12, GLOW + 11)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(2)

        self.lbl_start = QLabel(fmt_time(slot.start))
        self.lbl_start.setFont(make_font(13.5, QFont.DemiBold))
        self.lbl_end = QLabel(fmt_time(slot.end))
        self.lbl_end.setFont(make_font(11.5))
        self.lbl_title = QLabel(slot.title)
        self.lbl_title.setFont(make_font(13.5, QFont.Medium))
        self.lbl_title.setTextFormat(Qt.PlainText)
        self.lbl_title.setWordWrap(True)
        self.lbl_cat = QLabel(slot.category)
        self.lbl_cat.setTextFormat(Qt.PlainText)
        self.lbl_cat.setFont(make_font(11.5, QFont.Medium))
        self.lbl_cat.setFixedHeight(18)  # = высота бейджа, чтобы карточка не «прыгала»
        self.badge = QLabel()
        self.badge.setFont(make_font(9.5, QFont.Bold, spacing=0.8))
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setFixedHeight(18)
        self.badge.hide()

        meta = QHBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(6)
        meta.addWidget(self.lbl_cat)
        meta.addStretch(1)
        meta.addWidget(self.badge)

        grid.addWidget(self.lbl_start, 0, 0, Qt.AlignTop | Qt.AlignLeft)
        grid.addWidget(self.lbl_title, 0, 1)
        grid.addWidget(self.lbl_end, 1, 0, Qt.AlignTop | Qt.AlignLeft)
        grid.addLayout(meta, 1, 1)
        grid.setColumnMinimumWidth(0, 40)
        grid.setColumnStretch(1, 1)

        self.set_state(self.NORMAL)

    def set_state(self, state: str, progress: float = 0.0) -> None:
        progress = round(progress, 3)
        if state == self.state and progress == self.progress:
            return
        changed = state != self.state
        self.state, self.progress = state, progress
        if changed:
            c = self.color
            if state == self.PAST:
                self.lbl_start.setStyleSheet("color: rgba(255,255,255,90);")
                self.lbl_end.setStyleSheet("color: rgba(255,255,255,56);")
                self.lbl_title.setStyleSheet("color: rgba(255,255,255,97);")
                self.lbl_cat.setStyleSheet(f"color: {rgba(c, 110)};")
            else:
                self.lbl_start.setStyleSheet("color: rgba(255,255,255,235);")
                self.lbl_end.setStyleSheet("color: rgba(255,255,255,105);")
                self.lbl_title.setStyleSheet("color: rgba(255,255,255,235);")
                self.lbl_cat.setStyleSheet(f"color: {c.name()};")
            ink = "white" if c.lightnessF() < 0.45 else "#101014"
            self.badge.setStyleSheet(
                f"background: {c.name()}; color: {ink}; border-radius: 9px; padding: 0 8px;")
            self.badge.setVisible(state == self.ACTIVE)
        self.update()

    def set_selected(self, on: bool) -> None:
        if on != self.selected:
            self.selected = on
            self.update()

    def set_selectable(self, on: bool) -> None:
        self.selectable = on
        if on:
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.unsetCursor()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.selectable:
            self.clicked.emit(self)
            e.accept()
        else:
            e.ignore()  # без редактора нажатие уходит окну — работает перетаскивание виджета

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.doubleClicked.emit(self)
            e.accept()
        else:
            e.ignore()

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(GLOW, GLOW, -GLOW, -GLOW)
        c = self.color
        radius = 12.0

        if self.state == self.ACTIVE:
            # мягкое свечение: несколько расширяющихся контуров с падающей прозрачностью
            p.setBrush(Qt.NoBrush)
            for i in range(GLOW, 0, -1):
                a = int(85 * (1 - i / (GLOW + 1)) ** 2)
                p.setPen(QPen(QColor(c.red(), c.green(), c.blue(), a), 1.6))
                p.drawRoundedRect(r.adjusted(-i, -i, i, i), radius + i, radius + i)
            bg = QColor(c.red(), c.green(), c.blue(), 38)
            border = QColor(c.red(), c.green(), c.blue(), 200)
        elif self.state == self.PAST:
            bg = QColor(255, 255, 255, 12 if self._hover else 5)
            border = QColor(255, 255, 255, 10)
        else:
            bg = QColor(255, 255, 255, 20 if self._hover else 10)
            border = QColor(255, 255, 255, 24 if self._hover else 16)

        if self.selected:  # слот, открытый в редакторе
            bg = QColor(ACCENT.red(), ACCENT.green(), ACCENT.blue(), 34)
        p.setPen(QPen(border, 1))
        p.setBrush(bg)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        if self.selected:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(ACCENT.lighter(125), 1.6))
            p.drawRoundedRect(r.adjusted(0.8, 0.8, -0.8, -0.8), radius, radius)

        # цветная полоса категории слева
        bar = QColor(c)
        bar.setAlpha(100 if self.state == self.PAST else 255)
        p.setPen(Qt.NoPen)
        p.setBrush(bar)
        p.drawRoundedRect(QRectF(r.left() + 8, r.top() + 11, 3, r.height() - 22), 1.5, 1.5)

        # прогресс активного слота по нижнему краю
        if self.state == self.ACTIVE and self.progress > 0:
            track = QRectF(r.left() + 18, r.bottom() - 5, r.width() - 32, 2)
            p.setBrush(QColor(255, 255, 255, 22))
            p.drawRoundedRect(track, 1, 1)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(track.left(), track.top(), max(2.0, track.width() * self.progress), 2), 1, 1)


# ───────────────────────────── встроенный редактор ──────────────────────────

def color_dot_icon(color: str, size: int = 12) -> QIcon:
    pm = QPixmap(size * 2, size * 2)  # двойное разрешение — кружок четкий на HiDPI
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(1.5, 1.5, size - 3, size - 3))
    p.end()
    return QIcon(pm)


class ChevronComboBox(QComboBox):
    """QComboBox со своей стрелкой: стандартная после QSS-стилизации выглядит чужеродно."""

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(255, 255, 255, 150 if self.isEnabled() else 60), 1.5)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        cx, cy = self.width() - 15.0, self.height() / 2 + 0.5
        path = QPainterPath()
        path.moveTo(cx - 4, cy - 2)
        path.lineTo(cx, cy + 2)
        path.lineTo(cx + 4, cy - 2)
        p.drawPath(path)


class SlotEditor(QFrame):
    """Компактная форма редактирования слота. Встраивается в виджет на место карточки «сейчас».

    Сама ничего не сохраняет: сообщает об изменениях сигналами, а виджет правит
    запись в schedule.json, сортирует слоты и сохраняет файл."""

    fieldChanged = Signal(str, object, bool)  # (поле, значение, сохранить сразу)
    addRequested = Signal()
    deleteRequested = Signal()
    copyRequested = Signal(bool)              # скопировать день в другую неделю (подтверждено?)
    closeRequested = Signal()

    def __init__(self, icon_font: str | None, ui: InterfaceText, parent: QWidget | None = None):
        super().__init__(parent, objectName="editorCard")
        self.ui = ui
        self._item: dict | None = None
        self._loading = False
        self._copy_target: str | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 9, 8, 12)
        lay.setSpacing(0)

        # шапка: что редактируем, индикатор сохранения, закрыть
        head = QHBoxLayout()
        head.setSpacing(6)
        self.caption = ElidedLabel()
        self.caption.setObjectName("editorCaption")
        self.caption.setFont(make_font(10.5, QFont.Bold, spacing=0.9))
        self.status = QLabel(objectName="editorStatus")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setFont(make_font(11, QFont.Medium))
        self.btn_close = QPushButton(objectName="iconBtn")
        self.btn_close.setFixedSize(26, 26)
        self.btn_close.setFocusPolicy(Qt.StrongFocus)
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.ui.bind(self.btn_close.setAccessibleName, "Close editor")
        self.ui.bind(self.btn_close.setToolTip, "Done — close editor (Esc)")
        if icon_font:
            f = QFont(icon_font)
            f.setPixelSize(12)
            self.btn_close.setFont(f)
            self.btn_close.setText("")  # CheckMark
        else:
            self.btn_close.setText("✓")
        self.btn_close.clicked.connect(self.closeRequested)
        head.addWidget(self.caption, 1, Qt.AlignVCenter)
        head.addWidget(self.status, 0, Qt.AlignVCenter)
        head.addWidget(self.btn_close, 0, Qt.AlignVCenter)
        lay.addLayout(head)
        lay.addSpacing(4)

        form = QGridLayout()
        form.setContentsMargins(0, 0, 4, 0)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(3)

        def cap(text: str) -> QLabel:
            lbl = QLabel(objectName="fieldCap")
            self.ui.bind(lbl.setText, text)
            lbl.setFont(make_font(9.5, QFont.Bold, spacing=0.7))
            return lbl

        self.ed_time = QTimeEdit(objectName="edField")
        self.ui.bind(self.ed_time.setAccessibleName, "Start time")
        self.ed_time.setDisplayFormat("HH:mm")
        self.ed_time.setWrapping(True)
        self.ed_time.setAlignment(Qt.AlignCenter)
        self.ed_time.setFixedWidth(68)
        self.ui.bind(self.ed_time.setToolTip, "Start time. Scroll or press ↑/↓ to change the hours or minutes under the cursor")
        self.ed_time.timeChanged.connect(self._on_time)

        self.ed_title = QLineEdit(objectName="edField")
        self.ui.bind(self.ed_title.setPlaceholderText, "Task title")
        self.ui.bind(self.ed_title.setAccessibleName, "Task title")
        self.ed_title.setClearButtonEnabled(False)
        self.ed_title.textEdited.connect(self._on_title)
        self.ed_title.returnPressed.connect(lambda: self.fieldChanged.emit("title", self.ed_title.text(), True))

        self.ed_cat = ChevronComboBox(objectName="edField")
        self.ui.bind(self.ed_cat.setAccessibleName, "Category")
        self.ed_cat.setMaxVisibleItems(12)
        self.ed_cat.currentIndexChanged.connect(self._on_category)

        self.ed_dur = QSpinBox(objectName="edField")
        self.ed_dur.setRange(0, 24 * 60)
        self.ui.bind(self.ed_dur.setAccessibleName, "Duration")
        self.ed_dur.setSingleStep(5)
        self.ui.bind(self.ed_dur.setSuffix, " min")
        self.ui.bind(self.ed_dur.setSpecialValueText, "until next slot")
        self.ed_dur.setAlignment(Qt.AlignCenter)
        self.ed_dur.setFixedWidth(118)
        self.ui.bind(self.ed_dur.setToolTip, "Duration in minutes. Set to 0 to end when the next slot starts")
        self.ed_dur.valueChanged.connect(self._on_duration)

        for w in (self.ed_time, self.ed_title, self.ed_cat, self.ed_dur):
            w.setFont(make_font(13))
            w.setFixedHeight(30)
        self.ed_time.setFont(make_font(13.5, QFont.DemiBold))

        form.addWidget(cap("START"), 0, 0)
        form.addWidget(cap("TITLE"), 0, 1)
        form.addWidget(self.ed_time, 1, 0)
        form.addWidget(self.ed_title, 1, 1, 1, 2)
        form.setRowMinimumHeight(2, 6)
        form.addWidget(cap("CATEGORY"), 3, 0, 1, 2)
        form.addWidget(cap("DURATION"), 3, 2)
        form.addWidget(self.ed_cat, 4, 0, 1, 2)
        form.addWidget(self.ed_dur, 4, 2)
        form.setColumnStretch(1, 1)
        lay.addLayout(form)
        lay.addSpacing(10)

        acts = QHBoxLayout()
        acts.setContentsMargins(0, 0, 4, 0)
        acts.setSpacing(8)
        self.btn_delete = QPushButton(objectName="edDanger")
        self.ui.bind(self.btn_delete.setText, "Delete")
        self.ui.bind(self.btn_delete.setToolTip, "Delete selected slot (click twice to confirm)")
        self.btn_delete.clicked.connect(self._on_delete)
        self.btn_add = QPushButton(objectName="edPrimary")
        self.ui.bind(self.btn_add.setText, "+  Add slot")
        self.ui.bind(self.btn_add.setToolTip, "Add a slot to this day")
        self.btn_add.clicked.connect(self.addRequested)
        for b in (self.btn_delete, self.btn_add):
            b.setFont(make_font(12.5, QFont.DemiBold))
            b.setFixedHeight(30)
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.StrongFocus)
        acts.addWidget(self.btn_delete)
        acts.addStretch(1)
        acts.addWidget(self.btn_add)
        lay.addLayout(acts)

        # Двухнедельный режим: скопировать весь день в тот же день другой недели
        self.btn_copy = QPushButton(objectName="edGhost")
        self.btn_copy.setFont(make_font(12.5, QFont.DemiBold))
        self.btn_copy.setFixedHeight(30)
        self.btn_copy.setCursor(Qt.PointingHandCursor)
        self.btn_copy.setFocusPolicy(Qt.NoFocus)
        self.btn_copy.clicked.connect(lambda: self.copyRequested.emit(bool(self.btn_copy.property("confirm"))))
        self.copy_row = QWidget()
        cl = QHBoxLayout(self.copy_row)
        cl.setContentsMargins(0, 8, 4, 0)
        cl.addWidget(self.btn_copy)
        self.copy_row.hide()
        lay.addWidget(self.copy_row)

        self._confirm_timer = QTimer(self, singleShot=True, interval=2500)
        self._confirm_timer.timeout.connect(self._reset_delete)
        self._copy_timer = QTimer(self, singleShot=True, interval=3000)
        self._copy_timer.timeout.connect(self._reset_copy)
        self._status_timer = QTimer(self, singleShot=True, interval=1600)
        self._status_timer.timeout.connect(self._clear_status)

    # ── публичное ──

    def set_caption(self, text: str) -> None:
        self.caption.set_full_text(text)

    def set_copy_target(self, name: str | None, *, preserve_confirmation=False) -> None:
        """Название другой недели для кнопки копирования; None — кнопка скрыта (режим выключен)."""
        self._copy_target = name
        self.copy_row.setVisible(bool(name))
        if preserve_confirmation and self.btn_copy.property("confirm"):
            self._update_copy_question()
        else:
            self._reset_copy()

    def _update_copy_question(self):
        n = self._replace_count
        self.btn_copy.setText(self.ui("Replace {n} {slots} in “{target}”?", n=n,
                                      slots=self.ui.slots(n), target=self._copy_target))
        self.btn_copy.setToolTip(self.ui("Click again to replace"))

    def ask_copy_confirm(self, count: int) -> None:
        """В другой неделе в этот день уже есть слоты — второе нажатие подтверждает замену."""
        self.btn_copy.setProperty("confirm", True)
        self._replace_count = count
        self._update_copy_question()
        repolish(self.btn_copy)
        self._copy_timer.start()

    def set_categories(self, categories: dict[str, str], extra: str | None = None) -> None:
        self._loading = True
        self.ed_cat.clear()
        for name, color in categories.items():
            self.ed_cat.addItem(color_dot_icon(color), name, name)
        if extra and extra not in categories:
            self.ed_cat.addItem(color_dot_icon(FALLBACK_COLOR), extra, extra)
        self._loading = False

    def load(self, item: dict | None, caption: str, categories: dict[str, str]) -> None:
        """Показать запись в форме (None — в дне не выбран ни один слот)."""
        self._item = item
        self._reset_delete()
        self._reset_copy()
        self.set_caption(caption)
        has = item is not None
        for w in (self.ed_time, self.ed_title, self.ed_cat, self.ed_dur, self.btn_delete):
            w.setEnabled(has)
        cat = str(item.get("category", "")) if has else ""
        self.set_categories(categories, cat or None)
        self._loading = True
        try:
            if has:
                try:
                    t = parse_time(item.get("time", ""))
                except (ValueError, TypeError):
                    t = 12 * 60
                self.ed_time.setTime(QTime(t // 60, t % 60))
                self.ed_title.setText(str(item.get("title", "")))
                idx = next((i for i in range(self.ed_cat.count())
                            if self.ed_cat.itemData(i) == cat), 0)
                self.ed_cat.setCurrentIndex(idx)
                try:
                    self.ed_dur.setValue(min(1440, max(0, int(item.get("duration") or 0))))
                except (ValueError, TypeError, OverflowError):
                    self.ed_dur.setValue(0)
            else:
                self.ed_time.setTime(QTime(12, 0))
                self.ed_title.clear()
                self.ui.bind(self.ed_title.setPlaceholderText, "Select a slot or add one")
                self.ed_cat.setCurrentIndex(-1)
                self.ed_dur.setValue(0)
            if has:
                self.ui.bind(self.ed_title.setPlaceholderText, "Task title")
        finally:
            self._loading = False

    def focus_title(self) -> None:
        if self.ed_title.isEnabled():
            self.ed_title.setFocus(Qt.OtherFocusReason)
            self.ed_title.selectAll()

    def _clear_status(self):
        self.ui.bind(self.status.setText, "")

    def show_status(self, key: str, error: bool = False, detail: str = "", **values) -> None:
        self.status.setProperty("error", error)
        repolish(self.status)
        self.ui.bind(self.status.setText, key, **values)
        if detail:
            self.ui.bind(self.status.setToolTip, "{detail}", detail=detail)
        else:
            self.ui.bind(self.status.setToolTip, key if error else "", **values)
        if error:
            self._status_timer.stop()
        else:
            self._status_timer.start()

    # ── обработчики полей ──

    def _emit(self, name: str, value, now: bool) -> None:
        if not self._loading and self._item is not None:
            self.fieldChanged.emit(name, value, now)

    def _on_time(self, t: QTime) -> None:
        self._emit("time", t.toString("HH:mm"), False)

    def _on_title(self, text: str) -> None:
        self._emit("title", text, False)

    def _on_category(self, idx: int) -> None:
        if idx >= 0:
            self._emit("category", self.ed_cat.itemData(idx), True)

    def _on_duration(self, v: int) -> None:
        self._emit("duration", v, False)

    def _on_delete(self) -> None:
        if self.btn_delete.property("confirm"):
            self._reset_delete()
            self.deleteRequested.emit()
            return
        self.btn_delete.setProperty("confirm", True)
        self.ui.bind(self.btn_delete.setText, "Confirm deletion?")
        repolish(self.btn_delete)
        self._confirm_timer.start()

    def _reset_delete(self) -> None:
        self._confirm_timer.stop()
        if self.btn_delete.property("confirm"):
            self.btn_delete.setProperty("confirm", False)
            self.ui.bind(self.btn_delete.setText, "Delete")
            repolish(self.btn_delete)

    def _reset_copy(self) -> None:
        self._copy_timer.stop()
        name = self._copy_target or ""
        self.btn_copy.setText(self.ui("Copy this day to “{name}”", name=name))
        self.btn_copy.setToolTip(self.ui("Copy this day’s schedule to “{name}” in the other week", name=name))
        if self.btn_copy.property("confirm"):
            self.btn_copy.setProperty("confirm", False)
            repolish(self.btn_copy)


class ToggleSwitch(QAbstractButton):
    """Компактный переключатель вкл/выкл в стиле виджета."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFixedSize(40, 22)

    def sizeHint(self) -> QSize:
        return QSize(40, 22)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        on = self.isChecked()
        p.setPen(Qt.NoPen if on else QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(ACCENT if on else QColor(255, 255, 255, 22))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        x = r.right() - 3 - d if on else r.left() + 3
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#FFFFFF") if on else QColor(255, 255, 255, 170))
        p.drawEllipse(QRectF(x, r.top() + 3, d, d))


class SettingsPanel(QFrame):
    """Настройки расписания и оформления. Открывается на месте карточки «сейчас», как редактор.

    Сама ничего не сохраняет — сообщает о действиях сигналами, конфиг правит виджет."""

    appearanceChanged = Signal(str, object)
    categoryRequested = Signal(str)
    contentSizeChanged = Signal()
    biweeklyToggled = Signal(bool)
    weekNameEdited = Signal(int, str)
    weekNameFinished = Signal(int)
    currentWeekChosen = Signal(int)
    closeRequested = Signal()

    def __init__(self, icon_font: str | None, cfg: WidgetConfig, ui: InterfaceText, parent: QWidget | None = None):
        super().__init__(parent, objectName="editorCard")
        self.ui = ui
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 9, 8, 12)
        lay.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(6)
        caption = QLabel(objectName="editorCaption")
        self.ui.bind(caption.setText, "SETTINGS")
        caption.setFont(make_font(10.5, QFont.Bold, spacing=0.9))
        self.btn_close = QPushButton(objectName="iconBtn")
        self.btn_close.setFixedSize(26, 26)
        self.btn_close.setFocusPolicy(Qt.StrongFocus)
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.ui.bind(self.btn_close.setAccessibleName, "Close settings")
        self.ui.bind(self.btn_close.setToolTip, "Done — close settings (Esc)")
        if icon_font:
            f = QFont(icon_font)
            f.setPixelSize(12)
            self.btn_close.setFont(f)
            self.btn_close.setText("\ue73e")  # CheckMark
        else:
            self.btn_close.setText("✓")
        self.btn_close.clicked.connect(self.closeRequested)
        head.addWidget(caption, 0, Qt.AlignVCenter)
        head.addStretch(1)
        head.addWidget(self.btn_close, 0, Qt.AlignVCenter)
        lay.addLayout(head)
        lay.addSpacing(4)

        body = QVBoxLayout()
        body.setContentsMargins(0, 8, 4, 0)
        body.setAlignment(Qt.AlignTop)
        body.setSpacing(0)

        # переключатель режима
        row = QHBoxLayout()
        row.setSpacing(8)
        lbl = QLabel(objectName="settingLbl")
        self.ui.bind(lbl.setText, "Alternating weeks")
        lbl.setFont(make_font(13.5, QFont.Medium))
        self.sw_biweekly = ToggleSwitch()
        self.ui.bind(self.sw_biweekly.setAccessibleName, "Alternating weeks")
        self.ui.bind(self.sw_biweekly.setToolTip, "Separate schedules for two alternating weeks")
        self.sw_biweekly.clicked.connect(lambda: self.biweeklyToggled.emit(self.sw_biweekly.isChecked()))
        row.addWidget(lbl, 1, Qt.AlignVCenter)
        row.addWidget(self.sw_biweekly, 0, Qt.AlignVCenter)
        body.addLayout(row)
        body.addSpacing(2)
        self.mode_hint = QLabel(objectName="fieldHint")
        self.mode_hint.setFont(make_font(11.5))
        self.mode_hint.setWordWrap(True)
        body.addWidget(self.mode_hint)

        # всё про недели — только во включенном режиме
        self.weeks_box = QWidget()
        wl = QVBoxLayout(self.weeks_box)
        wl.setContentsMargins(0, 10, 0, 0)
        wl.setSpacing(3)

        def cap(text: str) -> QLabel:
            c = QLabel(objectName="fieldCap")
            self.ui.bind(c.setText, text)
            c.setFont(make_font(9.5, QFont.Bold, spacing=0.7))
            return c

        wl.addWidget(cap("WEEK NAMES"))
        names = QHBoxLayout()
        names.setSpacing(8)
        self.ed_names: list[QLineEdit] = []
        for i in range(2):
            ed = QLineEdit(objectName="edField")
            ed.setFont(make_font(13))
            ed.setFixedHeight(30)
            ed.setMaxLength(max(WEEK_NAME_MAX, len(cfg.week_names[i])))
            self.ui.bind(ed.setPlaceholderText, DEFAULT_WEEK_KEYS[i])
            self.ui.bind(ed.setAccessibleName, DEFAULT_WEEK_KEYS[i])
            self.ui.bind(ed.setToolTip, "For example, “Week 1”. Leave blank to use the default name")
            ed.textEdited.connect(lambda text, i=i: self.weekNameEdited.emit(i, text))
            ed.editingFinished.connect(lambda i=i: self.weekNameFinished.emit(i))
            names.addWidget(ed)
            self.ed_names.append(ed)
        wl.addLayout(names)
        wl.addSpacing(8)

        wl.addWidget(cap("CURRENT WEEK"))
        seg = QFrame(objectName="pillBar")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(2)
        self.cur_group = QButtonGroup(self)
        self.cur_group.setExclusive(True)
        for i in range(2):
            b = QPushButton(objectName="pill")
            b.setCheckable(True)
            b.setFocusPolicy(Qt.StrongFocus)
            b.setCursor(Qt.PointingHandCursor)
            b.setFont(make_font(12, QFont.DemiBold))
            b.setFixedHeight(26)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.cur_group.addButton(b, i)
            sl.addWidget(b)
        self.cur_group.idClicked.connect(self.currentWeekChosen)
        wl.addWidget(seg)
        wl.addSpacing(2)
        self.anchor_hint = QLabel(objectName="fieldHint")
        self.anchor_hint.setFont(make_font(11.5))
        self.anchor_hint.setWordWrap(True)
        wl.addWidget(self.anchor_hint)
        body.addWidget(self.weeks_box)
        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        lay.addWidget(self.tabs)
        schedule_tab = QWidget()
        schedule_tab.setLayout(body)
        appearance = QWidget()
        form = QFormLayout(appearance)
        form.setContentsMargins(6, 8, 6, 6)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        def choices(label, key, options):
            box = ChevronComboBox(objectName="edField")
            box.setMinimumHeight(28)
            box.setMinimumWidth(90)
            box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            self.ui.bind(box.setAccessibleName, label)
            for title, value in options:
                box.addItem(title, value)
                if key != "language" and (key != "font_family" or value == ""):
                    self.ui.bind(partial(box.setItemText, box.count() - 1), title)
            box.setCurrentIndex(max(0, box.findData(getattr(cfg, key))))
            box.activated.connect(lambda i: self.appearanceChanged.emit(key, box.itemData(i)))
            form.addRow(label, box)
            self.ui.bind(form.labelForField(box).setText, label)
            return box

        self.language_box = choices("Language", "language", [("Русский", "ru"), ("English", "en")])
        families = QFontDatabase.families(QFontDatabase.Cyrillic)
        self.font_box = choices("Font", "font_family", [("Default", "")] + [(f, f) for f in families])
        self.size_box = choices("Text size", "font_offset", [(n, i) for i, n in
                                enumerate(("Small", "Normal", "Medium", "Large", "Extra large"), -1)])
        self.accent_btn = QPushButton(objectName="edGhost")
        self.ui.bind(self.accent_btn.setText, "Choose color…")
        self.accent_btn.setMinimumHeight(28)
        self.accent_btn.setIcon(color_dot_icon(cfg.accent))
        def choose_accent():
            color = QColorDialog.getColor(QColor(cfg.accent), self, self.ui("Accent color"),
                                          QColorDialog.DontUseNativeDialog)
            if color.isValid():
                self.appearanceChanged.emit("accent", color.name())
        self.accent_btn.clicked.connect(choose_accent)
        form.addRow("Accent", self.accent_btn)
        self.ui.bind(form.labelForField(self.accent_btn).setText, "Accent")

        def number(label, key, low, high, step, suffix):
            box = QSpinBox()
            box.setRange(low, high)
            box.setSuffix(suffix)
            box.setSingleStep(step)
            box.setValue(getattr(cfg, key))
            box.setKeyboardTracking(False)
            self.ui.bind(box.setAccessibleName, label)
            box.valueChanged.connect(lambda value: self.appearanceChanged.emit(key, value))
            form.addRow(label, box)
            self.ui.bind(form.labelForField(box).setText, label)
            return box

        self.bg_box = choices("Background", "background", [("Graphite", "#141418"), ("Blue gray", "#17232e"),
                                                       ("Warm gray", "#24201e")])
        self.width_box = number("Widget width", "width", 392, 640, 8, " px")
        self.opacity_box = number("Background opacity", "opacity", 45, 100, 5, " %")
        self.cat_box = QComboBox()
        self.cat_box.setEditable(True)
        self.cat_box.setInsertPolicy(QComboBox.NoInsert)
        self.ui.bind(self.cat_box.setToolTip, "To add a category, enter a name and click “Color…”")
        self.cat_box.lineEdit().setMaxLength(48)
        self.ui.bind(self.cat_box.setAccessibleName, "Category")
        self.cat_button = QPushButton(objectName="edGhost")
        self.ui.bind(self.cat_button.setText, "Color…")
        def choose_category():
            box = self.cat_box
            name = box.currentText()
            if box.currentIndex() >= 0 and name == box.itemText(box.currentIndex()):
                name = box.currentData()
            self.categoryRequested.emit(name)
        self.cat_button.clicked.connect(choose_category)
        cat_row = QHBoxLayout()
        cat_row.addWidget(self.cat_box, 1)
        cat_row.addWidget(self.cat_button)
        form.addRow("Category", cat_row)
        self.ui.bind(form.labelForField(cat_row).setText, "Category")
        reset = QPushButton(objectName="edGhost")
        self.ui.bind(reset.setText, "Reset appearance")
        reset.setMinimumHeight(28)
        reset.clicked.connect(lambda: self.appearanceChanged.emit("reset", None))
        form.addRow(reset)
        elements = QWidget()
        content = QVBoxLayout(elements)
        self.display_boxes = {}
        for key, label in (("show_clock", "Show clock in header"), ("show_categories", "Show category names in list"),
                           ("dense", "Compact list spacing")):
            box = QCheckBox()
            self.ui.bind(box.setText, label)
            box.setChecked(getattr(cfg, key))
            box.clicked.connect(lambda on, key=key: self.appearanceChanged.emit(key, on))
            content.addWidget(box)
            self.display_boxes[key] = box
        content.addStretch()
        pages = (schedule_tab, appearance, elements)
        for index, (label, page) in enumerate(zip(("Schedule", "Appearance", "Elements"), pages)):
            self.tabs.addTab(label)
            self.ui.bind(partial(self.tabs.setTabText, index), label)
            lay.addWidget(page)
            page.setVisible(index == 0)

        def switch_page(index):
            for i, page in enumerate(pages):
                page.setVisible(i == index)
            self.contentSizeChanged.emit()

        self.tabs.currentChanged.connect(switch_page)
        for keys, step in (("Ctrl+Tab", 1), ("Ctrl+Shift+Tab", -1)):
            shortcut = QShortcut(keys, self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda step=step: self.tabs.setCurrentIndex(
                (self.tabs.currentIndex() + step) % self.tabs.count()))

    def set_state(self, biweekly: bool, raw_names: list[str], names: list[str],
                  current: int, anchor: date | None, anchor_week: int) -> None:
        """Показать текущие настройки (сигналы при этом не испускаются)."""
        self.sw_biweekly.setChecked(biweekly)
        self.sw_biweekly.update()
        self.mode_hint.setText(
            self.ui("The schedule alternates between “{a}” and “{b}”.", a=names[0], b=names[1]) if biweekly
            else self.ui("One schedule for every week. Enable this if your weeks alternate."))
        mode_changed = self.weeks_box.isHidden() == biweekly
        self.weeks_box.setVisible(biweekly)
        for ed, raw in zip(self.ed_names, raw_names):
            if ed.text() != raw:  # не сбиваем курсор, пока человек печатает
                ed.setText(raw)
        for i, b in enumerate(self.cur_group.buttons()):
            b.setText(names[i])
            b.setToolTip(self.ui("Set this week to “{name}”. Weeks will alternate every Monday.", name=names[i]))
        btn = self.cur_group.button(current)
        if btn and not btn.isChecked():
            btn.setChecked(True)
        if anchor is not None:
            self.anchor_hint.setText(
                self.ui("Reference Monday: {date} (“{name}”). Weeks alternate automatically.", date=self.ui.date(anchor, year=True), name=names[anchor_week]))
        else:
            self.anchor_hint.setText(self.ui("Weeks alternate automatically every Monday."))
        if mode_changed:
            self.contentSizeChanged.emit()


# ───────────────────────────── главное окно ─────────────────────────────────

QSS = """
QTabBar::tab { background: transparent; border: 0; border-bottom: 2px solid transparent; padding: 5px 10px; color: #B9BCC5; }
QTabBar::tab:selected { color: white; border-bottom: 2px solid #4C8DFF; }
QAbstractButton:focus { outline: 1px solid #8DB4FF; }
QLabel { background: transparent; color: rgba(255,255,255,235); }
QLabel#daySub { color: rgba(255,255,255,115); }
QLabel#clock { color: rgba(255,255,255,224); }
QLabel#statusRange, QLabel#statusSub { color: rgba(255,255,255,128); }
QLabel#emptyLbl { color: rgba(255,255,255,100); }
QLabel#errorLbl { color: #FF7A7A; }

QPushButton#iconBtn {
    background: transparent; border: none; border-radius: 8px;
    color: rgba(255,255,255,128);
}
QPushButton#iconBtn:hover { background: rgba(255,255,255,20); color: #FFFFFF; }
QPushButton#iconBtn[accent="true"] { color: #4C8DFF; }
QPushButton#iconBtn[accent="true"]:hover { color: #8DB4FF; }
QLabel#miniLeft { color: rgba(255,255,255,165); }
QPushButton#closeBtn {
    background: transparent; border: none; border-radius: 8px;
    color: rgba(255,255,255,128);
}
QPushButton#closeBtn:hover { background: rgba(232,17,35,215); color: #FFFFFF; }

QFrame#pillBar {
    background: rgba(255,255,255,10);
    border: 1px solid rgba(255,255,255,15);
    border-radius: 16px;
}
QPushButton#pill {
    background: transparent; border: 1px solid transparent; border-radius: 13px;
    color: rgba(255,255,255,140); padding: 0;
}
QPushButton#pill:hover { background: rgba(255,255,255,18); color: #FFFFFF; }
QPushButton#pill[today="true"] { color: #8DB4FF; border: 1px solid rgba(76,141,255,90); }
QPushButton#pill:checked { background: rgba(255,255,255,235); color: #141418; border: 1px solid transparent; }
QPushButton#pill:focus { outline: none; border: 1px solid #4C8DFF; }

QFrame#statusCard {
    background: rgba(255,255,255,10);
    border: 1px solid rgba(255,255,255,18);
    border-radius: 12px;
}

QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 6px; margin: 4px 0 4px 0; }
QScrollBar::handle:vertical { background: rgba(255,255,255,40); border-radius: 3px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: rgba(255,255,255,80); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

QMenu {
    background: #1C1C22; color: rgba(255,255,255,230);
    border: 1px solid rgba(255,255,255,26); border-radius: 10px; padding: 6px;
}
QMenu::item { padding: 6px 22px 6px 12px; border-radius: 6px; }
QMenu::item:selected { background: rgba(255,255,255,20); }
QMenu::indicator { width: 0; }
QMenu::separator { height: 1px; background: rgba(255,255,255,20); margin: 5px 8px; }

QToolTip {
    background: #1C1C22; color: #FFFFFF;
    border: 1px solid rgba(255,255,255,30); border-radius: 6px; padding: 4px 8px;
}

QFrame#editorCard {
    background: rgba(76,141,255,14);
    border: 1px solid rgba(76,141,255,95);
    border-radius: 12px;
}
QLabel#editorCaption { color: #8DB4FF; }
QLabel#editorStatus { color: #2ED47A; }
QLabel#editorStatus[error="true"] { color: #FF7A7A; }
QLabel#fieldCap { color: rgba(255,255,255,105); }

QLineEdit#edField, QTimeEdit#edField, QSpinBox#edField, QComboBox#edField {
    background: rgba(255,255,255,13);
    border: 1px solid rgba(255,255,255,24);
    border-radius: 8px;
    color: #FFFFFF;
    padding: 0 8px;
    selection-background-color: rgba(76,141,255,170);
    selection-color: #FFFFFF;
}
QLineEdit#edField:hover, QTimeEdit#edField:hover, QSpinBox#edField:hover, QComboBox#edField:hover {
    border: 1px solid rgba(255,255,255,45);
}
QLineEdit#edField:focus, QTimeEdit#edField:focus, QSpinBox#edField:focus, QComboBox#edField:focus {
    background: rgba(255,255,255,20);
    border: 1px solid rgba(76,141,255,220);
}
QLineEdit#edField:disabled, QTimeEdit#edField:disabled, QSpinBox#edField:disabled,
QComboBox#edField:disabled {
    background: rgba(255,255,255,5); border: 1px solid rgba(255,255,255,12); color: rgba(255,255,255,70);
}
QTimeEdit#edField::up-button, QTimeEdit#edField::down-button,
QSpinBox#edField::up-button, QSpinBox#edField::down-button { width: 0; height: 0; border: none; }
QComboBox#edField { padding-left: 8px; }
QComboBox#edField::drop-down { border: none; width: 26px; }
QComboBox#edField::down-arrow { image: none; width: 0; height: 0; }
QComboBox#edField QAbstractItemView {
    background: #1C1C22; color: rgba(255,255,255,230);
    border: 1px solid rgba(255,255,255,30); padding: 4px; outline: 0;
    selection-background-color: rgba(255,255,255,24); selection-color: #FFFFFF;
}

QPushButton#edPrimary {
    background: #4C8DFF; color: #0E1420; border: none; border-radius: 8px; padding: 0 14px;
}
QPushButton#edPrimary:hover { background: #6BA0FF; }
QPushButton#edPrimary:pressed { background: #3A78E8; }
QPushButton#edDanger {
    background: transparent; color: #FF8A8A;
    border: 1px solid rgba(255,107,107,90); border-radius: 8px; padding: 0 12px;
}
QPushButton#edDanger:hover { background: rgba(255,107,107,28); }
QPushButton#edDanger[confirm="true"] { background: #E5484D; color: #FFFFFF; border: 1px solid #E5484D; }
QPushButton#edDanger:disabled { color: rgba(255,255,255,55); border: 1px solid rgba(255,255,255,14); }
QPushButton#edGhost {
    background: rgba(255,255,255,10); color: rgba(255,255,255,200);
    border: 1px solid rgba(255,255,255,24); border-radius: 8px; padding: 0 12px;
}
QPushButton#edGhost:hover { background: rgba(255,255,255,20); color: #FFFFFF; }
QPushButton#edGhost[confirm="true"] {
    background: rgba(76,141,255,45); color: #FFFFFF; border: 1px solid rgba(76,141,255,210);
}

QLabel#settingLbl { color: rgba(255,255,255,230); }
QLabel#fieldHint { color: rgba(255,255,255,110); }
"""


G_LOCKED, G_UNLOCKED = "", ""
G_ON_TOP, G_ON_DESKTOP = "", ""
G_COLLAPSE, G_EXPAND = "", ""
G_CLOSE = ""
G_SETTINGS = "\ue713"
G_EDIT = ""
# запасные символы, если в системе нет Segoe Fluent Icons / MDL2 Assets
FALLBACK_GLYPHS = {G_LOCKED: "■", G_UNLOCKED: "□", G_ON_TOP: "▲", G_ON_DESKTOP: "▼",
                   G_COLLAPSE: "–", G_EXPAND: "+", G_CLOSE: "✕", G_EDIT: "✎", G_SETTINGS: "⚙"}


def repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)


def set_flag_prop(w: QWidget, name: str, value: bool) -> None:
    """Выставить булево свойство для QSS и перерисовать стиль, только если оно изменилось."""
    if bool(w.property(name)) != value:
        w.setProperty(name, value)
        repolish(w)


class ScheduleWidget(QWidget):
    languageChanged = Signal()
    MINI_WIDTH = 340
    MARGIN = 14

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self.setWindowIcon(make_app_icon())
        self.setFocusPolicy(Qt.ClickFocus)

        self.cfg = WidgetConfig.load(CONFIG_PATH)
        self.ui = InterfaceText(self.cfg.language)
        self._qt_translator = QTranslator(self)
        self._set_qt_language()
        self.ui.bind(self.setWindowTitle, "Schedule")
        self._apply_theme()
        self.setFixedWidth(self.cfg.width)
        if self.cfg.biweekly and self.cfg.week_anchor is None:
            # первый запуск двухнедельного режима: текущая неделя — «А», дальше чередование
            self.cfg.week_anchor, self.cfg.week_anchor_week = monday_of(current_time().date()), 0
            self._save_config()
        # «Лента» дней: 7 (одно расписание) или 14 (неделя А, затем неделя Б).
        # raw_days — те же списки записей, что в raw_single / raw_weeks, просто подряд.
        self.days: list[list[Slot]] = [[] for _ in range(7)]
        self.categories: dict[str, str] = dict(DEFAULT_CATEGORIES)
        self.raw_single: list[list] = [[] for _ in range(7)]                     # "days"
        self.raw_weeks: list[list[list]] = [[[] for _ in range(7)] for _ in range(2)]  # "weeks"
        self.raw_days: list[list] = list(self.raw_single)  # записи файла — их меняет редактор
        self.file_categories: dict[str, str] = {}
        self.cards: list[SlotCard] = []
        self._editing = False
        self._settings_open = False
        self._edit_ref: dict | None = None  # запись слота, открытая в редакторе
        self._dirty = False                 # есть несохраненные правки
        self._loaded_text: str | None = None  # последняя успешно загруженная или сохранённая версия
        self._today: int | None = None      # индекс сегодняшнего дня в ленте
        self._selected = current_time().weekday()       # выбранный день недели 0..6
        self._view_week = self._current_week()          # просматриваемая неделя 0 / 1
        self._error: Exception | None = None
        self._press_pos: QPoint | None = None  # точка нажатия ЛКМ для перетаскивания
        self._icon_font = icon_font_family()
        self._desk = DesktopLayer(self) if IS_WINDOWS else None

        # Правки из редактора пишутся в schedule.json с короткой задержкой (пока печатают)
        self._commit_timer = QTimer(self, singleShot=True, interval=350)
        self._commit_timer.timeout.connect(self._commit)
        self._cfg_save_timer = QTimer(self, singleShot=True, interval=600)
        self._cfg_save_timer.timeout.connect(self._save_config)
        self._scroll_timer = QTimer(self, singleShot=True, interval=0)
        self._scroll_timer.timeout.connect(self._restore_scroll)
        self._scroll_position = None

        self._build_ui()
        self.menu = self._build_menu()
        self._load()

        # Тик ровно на границе минуты (без дрейфа и лишних пробуждений)
        self._tick = QTimer(self)
        self._tick.setSingleShot(True)
        self._tick.setTimerType(Qt.PreciseTimer)
        self._tick.timeout.connect(self._on_tick)

        self._save_pos_timer = QTimer(self, singleShot=True, interval=400)
        self._save_pos_timer.timeout.connect(self._save_pos)

        # Авто-перезагрузка при сохранении schedule.json
        self._reload_timer = QTimer(self, singleShot=True, interval=300)
        self._reload_timer.timeout.connect(self.reload)
        self.watcher = QFileSystemWatcher(self)
        self.watcher.addPath(str(APP_DIR))
        self.watcher.fileChanged.connect(lambda _: self._reload_timer.start())
        self.watcher.directoryChanged.connect(self._on_dir_changed)
        self._watch_file()

        self.set_locked(self.cfg.locked, save=False)
        self.set_compact(self.cfg.compact, save=False)
        self.set_window_level(self.cfg.window_level, save=False)
        self._restore_pos()
        self._sync_week_ui()
        self.select_page(self._page())
        self._on_tick()
        self._setup_tray()

    # ── построение интерфейса ──────────────────────────────────────────────

    def _apply_theme(self) -> None:
        app = QApplication.instance()
        previous = app.property("fontOffset") or 0
        app.setProperty("fontOffset", self.cfg.font_offset)
        font = make_font(13)
        font.setFamilies(([self.cfg.font_family] if self.cfg.font_family else []) + FONT_TEXT)
        delta = self.cfg.font_offset - previous
        text_fonts = [(child, child.font()) for child in self.findChildren(QWidget)
                      if child.font().family() not in ("Segoe Fluent Icons", "Segoe MDL2 Assets")]
        app.setFont(font)
        self.setFont(font)
        for child, f in text_fonts:
            f.setFamilies(font.families())
            if delta and f.pixelSize() > 0:
                f.setPixelSize(max(1, f.pixelSize() + delta))
            child.setFont(f)
        ACCENT.setRgb(QColor(self.cfg.accent).rgb())
        rgb = f"{ACCENT.red()},{ACCENT.green()},{ACCENT.blue()}"
        # Акцент выбирается произвольно. Для текста на тёмной панели нужен светлый оттенок.
        tint = QColor.fromRgbF(*(0.40 + v * 0.60 for v in ACCENT.getRgbF()[:3])).name()
        style = (QSS.replace("76,141,255", rgb).replace("#4C8DFF", ACCENT.name())
                 .replace("#8DB4FF", tint)
                 .replace("#0E1420", "#ffffff" if ACCENT.lightnessF() < 0.45 else "#0E1420")
                 .replace("#6BA0FF", ACCENT.lighter(115).name())
                 .replace("#3A78E8", ACCENT.darker(115).name()))
        if style != app.styleSheet():
            app.setStyleSheet(style)
        palette = app.palette()
        palette.setColor(QPalette.Highlight, ACCENT)
        app.setPalette(palette)

    def _set_qt_language(self):
        app = QApplication.instance()
        app.removeTranslator(self._qt_translator)
        QLocale.setDefault(self.ui.locale)
        self.setLocale(self.ui.locale)
        if self.cfg.language == "ru":
            path = QLibraryInfo.path(QLibraryInfo.TranslationsPath)
            if self._qt_translator.load("qtbase_ru", path):
                app.installTranslator(self._qt_translator)

    def set_language(self, language: str) -> None:
        if language not in ("ru", "en") or self.cfg.language == language:
            return
        self.cfg.language = language
        self.ui.set_language(language)
        self._set_qt_language()
        box = self.settings.language_box
        box.setCurrentIndex(box.findData(language))
        for i in range(7):
            button = self.pills.button(i)
            button.setText(self.ui.weekday(i, short=True))
            button.setToolTip(self.ui.weekday(i))
            button.setAccessibleName(self.ui.weekday(i))
        self._sync_week_ui(preserve_confirmation=True)
        self._translate_category_choices()
        self._update_list_text()
        self.refresh()
        self._fit_height()
        self.languageChanged.emit()
        self._save_config()

    def _change_appearance(self, key: str, value) -> None:
        if key == "language":
            self.set_language(value)
            return
        panel = self.settings
        if key == "reset":
            defaults = WidgetConfig()
            for name in ("font_family", "font_offset", "width", "opacity", "accent", "background", *panel.display_boxes):
                setattr(self.cfg, name, getattr(defaults, name))
            panel.bg_box.setCurrentIndex(0)
            for name, box in panel.display_boxes.items():
                box.setChecked(getattr(defaults, name))
            panel.font_box.setCurrentIndex(0)
            panel.size_box.setCurrentIndex(panel.size_box.findData(defaults.font_offset))
            for box, val in ((panel.width_box, defaults.width), (panel.opacity_box, defaults.opacity)):
                box.blockSignals(True)
                box.setValue(val)
                box.blockSignals(False)
        else:
            setattr(self.cfg, key, value)
        if key in ("reset", "accent", "font_family", "font_offset"):
            self._apply_theme()
            panel.accent_btn.setIcon(color_dot_icon(self.cfg.accent))
            self._sync_week_ui()
        self.lbl_clock.setVisible(self.cfg.show_clock)
        if key in ("dense", "show_categories", "reset"):
            self._build_pages()
            self.refresh()
        old = self.geometry()
        self.setFixedWidth(self.cfg.width)
        self._fit_height()
        self._reanchor(old)
        self.update()
        self._cfg_save_timer.start()

    def _choose_category_color(self, name: str) -> None:
        if not name or self._error:
            return
        label = self.settings.cat_box.currentText()
        color = QColorDialog.getColor(self.color_for(name), self, self.ui("Color for “{name}”", name=label),
                                      QColorDialog.DontUseNativeDialog)
        if color.isValid():
            self.file_categories[name] = self.categories[name] = color.name()
            self._dirty = True
            self._category_error = not self._commit()
            self._show_file_errors()
            self._sync_categories(name)

    def _translate_category_choices(self):
        # A category explicitly named in JSON is user text, even if it matches the preset.
        key = "Общее"
        explicit = key in self.file_categories or any(
            isinstance(item, dict) and item.get("category") == key
            for days in (self.raw_single, *self.raw_weeks) for day in days for item in day)
        label = key if explicit else self.ui("General")
        for box in (self.settings.cat_box, self.editor.ed_cat):
            index = box.findData(key)
            if index >= 0 and box.itemText(index) != label:
                draft = None
                if box.isEditable() and box.currentText() != box.itemText(box.currentIndex()):
                    draft = box.currentText()
                box.setItemText(index, label)
                if draft is not None:
                    box.setEditText(draft)

    def _sync_categories(self, selected="") -> None:
        box = self.settings.cat_box
        box.clear()
        for name, color in self.categories.items():
            box.addItem(color_dot_icon(color), name, name)
        self._translate_category_choices()
        if selected:
            box.setCurrentIndex(box.findData(selected))
        self.settings.cat_button.setEnabled(not self._error)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Полный вид
        m = self.MARGIN
        self.full = QWidget()
        root = QVBoxLayout(self.full)
        root.setContentsMargins(m - GLOW, m, m - GLOW, m - GLOW)
        root.setSpacing(8)
        outer.addWidget(self.full)
        self._build_mini()
        outer.addWidget(self.mini)

        self.top = QWidget()
        top = QVBoxLayout(self.top)
        top.setContentsMargins(GLOW, 0, GLOW, 0)
        top.setSpacing(10)

        # Заголовок: день, дата, часы, кнопки
        hdr = QHBoxLayout()
        hdr.setSpacing(2)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.lbl_day = ElidedLabel()
        self.lbl_day.setObjectName("dayTitle")
        self.lbl_day.setFont(make_font(21, QFont.DemiBold))
        self.lbl_date = QLabel(objectName="daySub")
        self.lbl_date.setFont(make_font(12))
        titles.addWidget(self.lbl_day)
        titles.addWidget(self.lbl_date)
        hdr.addLayout(titles, 1)

        self.lbl_clock = QLabel(objectName="clock")
        self.lbl_clock.setFont(make_font(24, QFont.Light))
        self.lbl_clock.setVisible(self.cfg.show_clock)
        hdr.addWidget(self.lbl_clock, 0, Qt.AlignVCenter)
        hdr.addSpacing(6)

        self.btn_edit = self._icon_button("iconBtn", G_EDIT)
        self.ui.bind(self.btn_edit.setToolTip, "Edit schedule (or double-click a slot)")
        self.ui.bind(self.btn_edit.setAccessibleName, "Edit schedule")
        self.btn_edit.clicked.connect(self.toggle_editor)
        self.btn_settings = self._icon_button("iconBtn", G_SETTINGS)
        self.ui.bind(self.btn_settings.setToolTip, "Schedule and appearance settings")
        self.ui.bind(self.btn_settings.setAccessibleName, "Settings")
        self.btn_settings.clicked.connect(self.toggle_settings)
        self.btn_lock = self._icon_button("iconBtn", G_UNLOCKED)
        self.ui.bind(self.btn_lock.setAccessibleName, "Lock position")
        self.btn_lock.clicked.connect(lambda: self.set_locked(not self.cfg.locked))
        self.btn_level = self._icon_button("iconBtn", G_ON_DESKTOP)
        self.ui.bind(self.btn_level.setAccessibleName, "Window level")
        self.btn_level.clicked.connect(lambda: self.set_window_level(
            LEVEL_DESKTOP if self.cfg.window_level == LEVEL_TOP else LEVEL_TOP))
        self.btn_collapse = self._icon_button("iconBtn", G_COLLAPSE)
        self.ui.bind(self.btn_collapse.setToolTip, "Switch to compact view")
        self.ui.bind(self.btn_collapse.setAccessibleName, "Switch to compact view")
        self.btn_collapse.clicked.connect(lambda: self.set_compact(True))
        self.btn_close = self._icon_button("closeBtn", G_CLOSE)
        self.ui.bind(self.btn_close.setToolTip, "Close")
        self.ui.bind(self.btn_close.setAccessibleName, "Close")
        self.btn_close.clicked.connect(self.quit_app)
        for b in (self.btn_edit, self.btn_settings, self.btn_lock, self.btn_level, self.btn_collapse, self.btn_close):
            hdr.addWidget(b, 0, Qt.AlignVCenter)
        top.addLayout(hdr)

        # Переключатель недель [Неделя А | Неделя Б] — только в двухнедельном режиме
        nav = QHBoxLayout()
        nav.setSpacing(6)
        self.week_bar = QFrame(objectName="pillBar")
        wl = QHBoxLayout(self.week_bar)
        wl.setContentsMargins(3, 3, 3, 3)
        wl.setSpacing(2)
        self.week_btns = QButtonGroup(self)
        self.week_btns.setExclusive(True)
        for i in range(2):
            b = QPushButton(objectName="pill")
            b.setCheckable(True)
            b.setFocusPolicy(Qt.StrongFocus)
            b.setCursor(Qt.PointingHandCursor)
            b.setFont(make_font(11.5, QFont.DemiBold))
            b.setFixedHeight(26)
            self.week_btns.addButton(b, i)
            wl.addWidget(b)
        self.week_btns.idClicked.connect(self.select_week)
        nav.addWidget(self.week_bar, 0)

        # Переключатель дней
        bar = QFrame(objectName="pillBar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(3, 3, 3, 3)
        bl.setSpacing(2)
        self.pills = QButtonGroup(self)
        self.pills.setExclusive(True)
        for i in range(7):
            b = QPushButton(self.ui.weekday(i, short=True), objectName="pill")
            b.setCheckable(True)
            b.setFocusPolicy(Qt.StrongFocus)
            b.setCursor(Qt.PointingHandCursor)
            b.setFont(make_font(12, QFont.DemiBold))
            b.setFixedHeight(26)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.setToolTip(self.ui.weekday(i))
            b.setAccessibleName(self.ui.weekday(i))
            self.pills.addButton(b, i)
            bl.addWidget(b)
        self.pills.idClicked.connect(self.select_day)
        nav.addWidget(bar, 1)
        top.addLayout(nav)

        # Статус «сейчас»
        self.status = QFrame(objectName="statusCard")
        sg = QVBoxLayout(self.status)
        sg.setContentsMargins(14, 10, 14, 12)
        sg.setSpacing(3)
        cap = QHBoxLayout()
        cap.setSpacing(7)
        self.st_dot = QLabel()
        self.st_dot.setFixedSize(8, 8)
        self.st_caption = ElidedLabel()
        self.st_caption.setFont(make_font(10.5, QFont.Bold, spacing=0.9))
        self.st_range = QLabel(objectName="statusRange")
        self.st_range.setFont(make_font(11.5, QFont.Medium))
        cap.addWidget(self.st_dot, 0, Qt.AlignVCenter)
        cap.addWidget(self.st_caption, 1)
        cap.addWidget(self.st_range, 0, Qt.AlignVCenter)
        self.st_title = ElidedLabel()
        self.st_title.setFont(make_font(15.5, QFont.DemiBold))
        self.st_sub = ElidedLabel()
        self.st_sub.setObjectName("statusSub")
        self.st_sub.setFont(make_font(12))
        self.st_progress = ProgressLine()
        sp = self.st_progress.sizePolicy()
        sp.setRetainSizeWhenHidden(True)  # высота карточки статуса постоянна
        self.st_progress.setSizePolicy(sp)
        sg.addLayout(cap)
        sg.addSpacing(1)
        sg.addWidget(self.st_title)
        sg.addWidget(self.st_sub)
        sg.addSpacing(5)
        sg.addWidget(self.st_progress)
        top.addWidget(self.status)

        # Редактор открывается на месте карточки «сейчас»
        self.editor = SlotEditor(self._icon_font, self.ui)
        self.editor.hide()
        self.editor.fieldChanged.connect(self._on_field_changed)
        self.editor.addRequested.connect(self.add_slot)
        self.editor.deleteRequested.connect(self.delete_slot)
        self.editor.copyRequested.connect(self.copy_day_to_other_week)
        self.editor.closeRequested.connect(self.close_editor)
        top.addWidget(self.editor)

        # Настройки двухнедельного режима — тоже на месте карточки «сейчас»
        self.settings = SettingsPanel(self._icon_font, self.cfg, self.ui)
        # Пересчитать список после того, как Qt обновит геометрию показанных полей.
        self.settings.contentSizeChanged.connect(self._fit_height, Qt.QueuedConnection)
        self.settings.appearanceChanged.connect(self._change_appearance)
        self.settings.categoryRequested.connect(self._choose_category_color)
        self.settings.hide()
        self.settings.biweeklyToggled.connect(self.set_biweekly)
        self.settings.weekNameEdited.connect(self._on_week_name_edited)
        self.settings.weekNameFinished.connect(self._on_week_name_finished)
        self.settings.currentWeekChosen.connect(self.set_current_week)
        self.settings.closeRequested.connect(self.close_settings)
        top.addWidget(self.settings)

        self.lbl_error = QLabel(objectName="errorLbl")
        self.lbl_error.setFont(make_font(11.5))
        self.lbl_error.setWordWrap(True)
        self.lbl_error.setTextFormat(Qt.PlainText)
        self.lbl_error.hide()
        top.addWidget(self.lbl_error)
        root.addWidget(self.top)

        # Список слотов: создаются только карточки открытого дня
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)
        root.addWidget(self.scroll)

    def _build_mini(self) -> None:
        """Компактная плашка: текущая задача, сколько осталось, кнопка «развернуть»."""
        self.mini = QWidget()
        self.mini.hide()
        ml = QHBoxLayout(self.mini)
        ml.setContentsMargins(14, 8, 7, 8)
        ml.setSpacing(10)

        self.mini_dot = QLabel()
        self.mini_dot.setFixedSize(8, 8)
        self.mini_title = ElidedLabel()
        self.mini_title.setFont(make_font(13.5, QFont.DemiBold))
        self.mini_left = QLabel(objectName="miniLeft")
        self.mini_left.setFont(make_font(12, QFont.Medium))
        self.mini_progress = ProgressLine()
        self.mini_progress.setFixedHeight(3)
        sp = self.mini_progress.sizePolicy()
        sp.setRetainSizeWhenHidden(True)
        self.mini_progress.setSizePolicy(sp)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.mini_title, 1)
        row.addWidget(self.mini_left, 0)
        col = QVBoxLayout()
        col.setSpacing(5)
        col.setContentsMargins(0, 2, 0, 2)
        col.addLayout(row)
        col.addWidget(self.mini_progress)

        self.btn_expand = self._icon_button("iconBtn", G_EXPAND)
        self.ui.bind(self.btn_expand.setToolTip, "Expand")
        self.ui.bind(self.btn_expand.setAccessibleName, "Expand")
        self.btn_expand.clicked.connect(lambda: self.set_compact(False))

        ml.addWidget(self.mini_dot, 0, Qt.AlignVCenter)
        ml.addLayout(col, 1)
        ml.addWidget(self.btn_expand, 0, Qt.AlignVCenter)

    def _icon_button(self, name: str, glyph: str) -> QPushButton:
        b = QPushButton(objectName=name)
        b.setFixedSize(30, 30)
        b.setFocusPolicy(Qt.StrongFocus)
        b.setCursor(Qt.PointingHandCursor)
        if self._icon_font:
            f = QFont(self._icon_font)
            f.setPixelSize(11)
            b.setFont(f)
        else:
            b.setFont(make_font(13))
        self._set_glyph(b, glyph)
        return b

    def _set_glyph(self, b: QPushButton, glyph: str, accent: bool | None = None) -> None:
        b.setText(glyph if self._icon_font else FALLBACK_GLYPHS.get(glyph, glyph))
        if accent is not None and b.property("accent") != accent:
            b.setProperty("accent", accent)
            repolish(b)

    def _styled_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        menu.setAttribute(Qt.WA_TranslucentBackground)
        menu.setFont(make_font(12.5))
        return menu

    @staticmethod
    def _mark(on: bool, text: str) -> str:
        # QMenu::indicator скрыт в QSS, поэтому галочку рисуем в тексте пункта
        return ("✓  " if on else "     ") + text

    def open_data_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(APP_DIR)))

    def set_autostart(self, on: bool) -> None:
        try:
            set_autostart(on)
        except OSError as e:
            if getattr(self, "tray", None):
                detail = str(e) if IS_WINDOWS else self.ui("Startup is only supported on Windows")
                message = self.ui("Could not change startup settings: {error}", error=detail)
                self.tray.showMessage(self.ui("Startup"), message, QSystemTrayIcon.Warning, 5000)

    def _menu_action(self, menu, key):
        action = menu.addAction("")
        self.ui.bind(action.setText, key)
        return action

    def _build_tray_menu(self) -> QMenu:
        """Короткое меню иконки в трее."""
        menu = self._styled_menu()
        act_toggle = self._menu_action(menu, "Hide")
        act_toggle.triggered.connect(self.toggle_visible)
        menu.addSeparator()
        act_lock = self._menu_action(menu, "Lock position")
        act_lock.setCheckable(True)
        act_lock.triggered.connect(lambda: self.set_locked(not self.cfg.locked))
        act_auto = self._menu_action(menu, "Start with Windows")
        act_auto.setCheckable(True)
        act_auto.setEnabled(IS_WINDOWS)
        act_auto.triggered.connect(lambda: self.set_autostart(not autostart_enabled()))
        menu.addSeparator()
        self._menu_action(menu, "Settings…").triggered.connect(self.open_settings)
        self._menu_action(menu, "Open JSON folder").triggered.connect(self.open_data_folder)
        menu.addSeparator()
        self._menu_action(menu, "Quit").triggered.connect(self.quit_app)

        def sync() -> None:
            auto = autostart_enabled()  # читаем реестр заново: запись могли изменить извне
            act_toggle.setText(self.ui("Hide") if self.isVisible() else self.ui("Show"))
            act_lock.setChecked(self.cfg.locked)
            act_lock.setText(self._mark(self.cfg.locked, self.ui("Lock position")))
            act_auto.setChecked(auto)
            act_auto.setText(self._mark(auto, self.ui("Start with Windows")))

        self.languageChanged.connect(sync)
        menu.aboutToShow.connect(sync)
        return menu

    def _build_menu(self) -> QMenu:
        menu = self._styled_menu()

        def add(text: str, fn) -> None:
            self._menu_action(menu, text).triggered.connect(fn)

        self.act_toggle = self._menu_action(menu, "Hide widget")
        self.act_toggle.triggered.connect(self.toggle_visible)
        add("Today", self.go_today)
        self.act_compact = self._menu_action(menu, "Switch to compact view")
        self.act_compact.triggered.connect(lambda: self.set_compact(not self.cfg.compact))
        menu.addSeparator()
        self.act_lock = self._menu_action(menu, "Lock position")
        self.act_lock.triggered.connect(lambda: self.set_locked(not self.cfg.locked))
        self.act_top = self._menu_action(menu, "Always on top")
        self.act_top.triggered.connect(lambda: self.set_window_level(LEVEL_TOP))
        self.act_desktop = self._menu_action(menu, "On desktop")
        self.act_desktop.triggered.connect(lambda: self.set_window_level(LEVEL_DESKTOP))
        self.act_autostart = self._menu_action(menu, "Start with Windows")
        self.act_autostart.setEnabled(IS_WINDOWS)
        self.act_autostart.triggered.connect(lambda: self.set_autostart(not autostart_enabled()))
        menu.addSeparator()
        self.act_biweekly = self._menu_action(menu, "Alternating weeks")
        self.act_biweekly.triggered.connect(lambda: self.set_biweekly(not self.cfg.biweekly))
        self.act_settings = self._menu_action(menu, "Settings…")
        self.act_settings.triggered.connect(self.toggle_settings)
        menu.addSeparator()
        self.act_edit = self._menu_action(menu, "Edit schedule")
        self.act_edit.triggered.connect(self.toggle_editor)
        add("Import schedule…", self.import_schedule)
        add("Open schedule.json", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(SCHEDULE_PATH))))
        add("Open JSON folder", self.open_data_folder)
        add("Reload schedule", self.reload)
        menu.addSeparator()
        add("Quit", self.quit_app)

        mark = self._mark

        def sync() -> None:
            self.act_autostart.setText(mark(autostart_enabled(), self.ui("Start with Windows")))
            self.act_lock.setText(mark(self.cfg.locked, self.ui("Lock position")))
            self.act_top.setText(mark(self.cfg.window_level == LEVEL_TOP, self.ui("Always on top")))
            self.act_desktop.setText(mark(self.cfg.window_level == LEVEL_DESKTOP, self.ui("On desktop")))
            self.act_compact.setText(self.ui("Expand") if self.cfg.compact else self.ui("Switch to compact view"))
            self.act_toggle.setText(self.ui("Hide widget") if self.isVisible() else self.ui("Show widget"))
            self.act_edit.setText(self.ui("Close editor") if self._editing else self.ui("Edit schedule"))
            self.act_biweekly.setText(mark(self.cfg.biweekly, self.ui("Alternating weeks")))
            self.act_settings.setText(self.ui("Close settings") if self._settings_open else self.ui("Settings…"))

        self.languageChanged.connect(sync)
        menu.aboutToShow.connect(sync)
        return menu

    def _setup_tray(self) -> None:
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(make_app_icon(), self)
        self.tray_menu = self._build_tray_menu()
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(  # одиночный клик по иконке — показать / спрятать
            lambda reason: self.toggle_visible() if reason == QSystemTrayIcon.Trigger else None)
        self.tray.setToolTip(self.ui("Schedule"))
        self.tray.show()
        self._on_tick()  # обновить подсказку трея

    # ── данные ─────────────────────────────────────────────────────────────

    def import_schedule(self) -> None:
        pending_edit = self._commit_timer.isActive()
        self._commit_timer.stop()
        try:
            path, _ = QFileDialog.getOpenFileName(self, self.ui("Import schedule…"), str(APP_DIR),
                                                  self.ui("JSON files (*.json);;All files (*)"),
                                                  options=QFileDialog.DontUseNativeDialog)
            if path:
                self._import_file(Path(path))
        finally:
            if pending_edit and self._dirty:
                self._commit_timer.start()

    def _import_file(self, path: Path) -> None:
        try:
            candidate = parse_schedule(path.read_text(encoding="utf-8-sig"))
            text = candidate.sorted_text()
            # Check encodability before displaying JSON strings with unpaired surrogates.
            text.encode("utf-8")
            preview = self._compare_import(candidate)
            revised = False
            while True:
                if preview is None:
                    QMessageBox.information(self, self.ui("Import schedule…"), self.ui("No changes."))
                    return
                if not self._confirm_import(path, *preview, revised):
                    return
                latest = self._compare_import(candidate)
                if latest == preview:
                    break
                preview, revised = latest, True
        except (OSError, ValueError, RecursionError) as error:
            QMessageBox.warning(self, self.ui("Import schedule…"),
                                self.ui("Could not import schedule: {error}", error=self._file_error_detail(error)))
            return
        try:
            write_text_atomic(SCHEDULE_PATH, text)
        except OSError as error:
            QMessageBox.warning(self, self.ui("Import schedule…"),
                                self.ui("Could not save schedule.json: {error}", error=self._file_error_detail(error)))
            return
        self._reload_from_text(text)
        self._watch_file()

    def _compare_import(self, candidate: ScheduleData):
        # The watcher is debounced. An external save can already be on disk
        # while the widget still shows the previous version.
        text = SCHEDULE_PATH.read_text(encoding="utf-8-sig")
        current = self._current_schedule() if text == self._loaded_text else parse_schedule(text)
        return schedule_changes(current, candidate, self.ui, slot_sort_key)

    def _confirm_import(self, path: Path, summary: str, details: str, revised: bool) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle(self.ui("Import schedule…"))
        layout = QVBoxLayout(dialog)
        heading = self.ui("Replace the entire schedule with “{name}”?", name=path.name)
        if revised:
            heading = self.ui("The current schedule changed. Review the updated comparison.") + "\n" + heading
        label = QLabel(heading + "\n\n" + summary)
        label.setTextFormat(Qt.PlainText)
        label.setWordWrap(True)
        layout.addWidget(label)
        report = QPlainTextEdit()
        report.setReadOnly(True)
        report.setAccessibleName(self.ui("Schedule changes"))
        report.setPlainText(details)
        layout.addWidget(report)
        buttons = QDialogButtonBox()
        buttons.addButton(self.ui("Import"), QDialogButtonBox.AcceptRole)
        cancel = buttons.addButton(self.ui("Cancel"), QDialogButtonBox.RejectRole)
        cancel.setDefault(True)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(640, 460)
        try:
            return dialog.exec() == QDialog.Accepted
        finally:
            dialog.deleteLater()

    def _load(self, text: str | None = None) -> None:
        self._editor_blocked = self._category_error = False
        migrated = False
        try:
            if text is None:
                if not SCHEDULE_PATH.exists():
                    seed = EXAMPLE_PATH.read_text(encoding="utf-8")
                    parse_schedule(seed)
                    try:
                        with SCHEDULE_PATH.open("x", encoding="utf-8") as stream:
                            stream.write(seed)
                    except FileExistsError:
                        pass
                text = SCHEDULE_PATH.read_text(encoding="utf-8-sig")
            data = parse_schedule(text.lstrip("﻿"))
            migrated = data.needs_migration
            self.raw_single, self.raw_weeks = data.days, data.weeks
            self.file_categories, self.categories = data.file_categories, data.categories
            self._loaded_text = text
            self._error = None
        except (OSError, ValueError) as e:  # json.JSONDecodeError — подкласс ValueError
            self._loaded_text = None
            self._error = e
        self._sync_categories()
        self._rebuild_timeline()
        if migrated:
            self._write_schedule()  # при ошибке записи данные останутся в памяти и запишутся при правке
        self._dirty = False
        self._cat_lookup = {k.lower(): QColor(v) for k, v in self.categories.items()}
        self._build_pages()
        self._show_file_errors()
        self._fit_height()

    def reload(self) -> None:
        try:
            text = SCHEDULE_PATH.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            text = None
        self._watch_file()
        if text is not None and text == self._loaded_text:
            return  # это наша собственная запись из редактора — всё уже на экране
        self._reload_from_text(text)

    def _reload_from_text(self, text: str | None) -> None:
        self._commit_timer.stop()  # файл изменили снаружи — он главнее несохраненной правки
        page = self._page()
        self._load(text)
        self._today = None  # заставить refresh перепроставить «сегодня»
        self.refresh()
        self.select_page(page)  # остаемся на том же дне; редактор выберет слот заново
        if self._editing and self._error:
            self.close_editor()  # битый файл не перезаписываем последней рабочей версией

    def _watch_file(self) -> None:
        if SCHEDULE_PATH.exists() and str(SCHEDULE_PATH) not in self.watcher.files():
            self.watcher.addPath(str(SCHEDULE_PATH))

    def _on_dir_changed(self, _path: str) -> None:
        # редакторы часто сохраняют файл через удаление+создание — подхватываем заново
        if SCHEDULE_PATH.exists() and str(SCHEDULE_PATH) not in self.watcher.files():
            self._reload_timer.start()

    def color_for(self, category: str) -> QColor:
        return self._cat_lookup.get(category.lower()) or QColor(FALLBACK_COLOR)

    def _rebuild_timeline(self) -> None:
        """Собрать ленту дней под текущий режим: 7 дней или 14 (неделя А + неделя Б)."""
        if self.cfg.biweekly:
            self.raw_days = self.raw_weeks[0] + self.raw_weeks[1]  # те же списки, не копии
        else:
            self.raw_days = list(self.raw_single)
            self._view_week = 0
        self.days = build_slots(self.raw_days)

    def _current_schedule(self) -> ScheduleData:
        return ScheduleData(self.raw_single, self.raw_weeks, self.file_categories, self.categories)

    def _write_schedule(self) -> OSError | None:
        """Отсортировать слоты по времени и записать schedule.json. Возвращает ошибку записи."""
        text = self._current_schedule().sorted_text()
        try:
            write_text_atomic(SCHEDULE_PATH, text)
        except OSError as e:
            return e
        self._loaded_text = text  # чтобы file watcher не перечитывал нашу же запись
        return None

    def _build_pages(self) -> None:
        self._rendered_day = None
        self._render_day(self._page())

    def _render_day(self, d: int) -> None:
        if self._rendered_day == d:
            return
        previous = self.scroll.takeWidget()
        if previous is not None:
            previous.deleteLater()
        self._rendered_day = d
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        row = []
        for s in self.days[d]:
            card = SlotCard(s, self.color_for(s.category))
            if self.cfg.dense:
                card.layout().setContentsMargins(GLOW + 18, GLOW + 3, GLOW + 12, GLOW + 4)
            card.lbl_cat.setVisible(self.cfg.show_categories)
            card.set_selectable(self._editing)
            card.set_selected(self._editing and s.ref is not None and s.ref is self._edit_ref)
            card.clicked.connect(self._on_card_clicked)
            card.doubleClicked.connect(self._on_card_double_clicked)
            lay.addWidget(card)
            row.append(card)
        if not row:
            empty = QLabel(objectName="emptyLbl")
            empty.setFont(make_font(12.5))
            empty.setAlignment(Qt.AlignCenter)
            empty.setMinimumHeight(80)
            lay.addWidget(empty)
        lay.addStretch(1)
        self.scroll.setWidget(page)
        page.setAutoFillBackground(False)
        self.cards = row
        self._update_list_text()

    def _update_list_text(self) -> None:
        for card in self.cards:
            card.badge.setText(self.ui("NOW"))
            card.lbl_title.setText(self.slot_title(card.slot))
            card.lbl_cat.setText(self.slot_category(card.slot))
        if not self.cards:
            label = self.scroll.widget().findChild(QLabel, "emptyLbl")
            key = "Empty — click “Add slot”" if self._editing else "Nothing scheduled for this day"
            label.setText(self.ui(key))

    def _fit_height(self) -> None:
        content_w = self.cfg.width - 2 * (self.MARGIN - GLOW)
        if self._settings_open:
            self.settings.layout().activate()
            self.top.layout().invalidate()
        self.top.layout().activate()
        page = self.scroll.widget()
        hh = page.heightForWidth(content_w) if page.hasHeightForWidth() else -1
        h = hh if hh > 0 else page.sizeHint().height()
        screen = self.screen() or QGuiApplication.primaryScreen()
        top_h = self.top.heightForWidth(content_w) if self.top.hasHeightForWidth() else -1
        if top_h < 0:
            top_h = self.top.sizeHint().height()
        cap = int(screen.availableGeometry().height() * 0.9) - top_h - 3 * self.MARGIN
        self.scroll.setFixedHeight(max(140, min(h + 2, cap)))
        self.full.layout().activate()
        self.layout().activate()
        # в компактном режиме полный вид скрыт и не участвует в sizeHint
        self.resize(self.width(), self.sizeHint().height())

    # ── логика времени ─────────────────────────────────────────────────────

    def _on_tick(self) -> None:
        self.refresh()
        now = current_time()
        ms = 60_000 - (now.second * 1000 + now.microsecond // 1000) + 60
        self._tick.start(max(1000, ms))

    def refresh(self) -> None:
        now = current_time()
        minute = now.hour * 60 + now.minute
        today = self._today_index(now)

        # первый запуск, наступила полночь или сменилась неделя (каждый понедельник в 00:00
        # в двухнедельном режиме) — если смотрели «сегодня», переходим на новый сегодняшний день
        if self._today != today:
            follow = self._today is None or self._page() == self._today
            self._today = today
            if follow:
                self.select_page(today)
            else:
                self._update_today_marks()

        self.lbl_clock.setText(now.strftime("%H:%M"))
        self._update_titles(now)

        active = find_active(self.days, today, minute)
        d = self._page()
        for i, card in enumerate(self.cards):
            s = card.slot
            if active == (d, i):
                mm = minute if d == today else minute + 1440
                card.set_state(SlotCard.ACTIVE, (mm - s.start) / max(1, s.end - s.start))
            elif d == today and s.end <= minute:
                card.set_state(SlotCard.PAST)
            else:
                card.set_state(SlotCard.NORMAL)
        self._update_status(today, minute, active)

    def _update_titles(self, now: datetime) -> None:
        wd, d = now.weekday(), self._selected
        offset = d - wd
        if self.cfg.biweekly and self._view_week != self._current_week(now.date()):
            offset += 7  # другая неделя — показываем даты ближайшей такой недели (следующей)
        day = now.date() + timedelta(days=offset)
        date_s = self.ui.date(day)
        rel = {0: self.ui("Today"), 1: self.ui("Tomorrow"), -1: self.ui("Yesterday")}.get(offset)
        self.lbl_day.set_full_text(self.ui.weekday(d))
        self.lbl_date.setText(f"{rel} · {date_s}" if rel else date_s)

    def slot_title(self, slot: Slot) -> str:
        return slot.title if slot.title.strip() else self.ui("Untitled")

    def slot_category(self, slot: Slot) -> str:
        if slot.ref is not None and "category" not in slot.ref and "Общее" not in self.file_categories:
            return self.ui("General")
        return slot.category

    def _update_status(self, wd: int, minute: int, active: tuple[int, int] | None) -> None:
        nxt = find_next(self.days, wd, minute)
        progress: float | None = None
        if active:
            d, i = active
            s = self.days[d][i]
            mm = minute if d == wd else minute + 1440
            color = self.color_for(s.category)
            caption = self.ui("NOW · {category}", category=self.slot_category(s))
            title = self.slot_title(s)
            rng = f"{fmt_time(s.start)} – {fmt_time(s.end)}"
            sub = self.ui("{time} remaining", time=self.ui.duration(s.end - mm))
            if nxt:
                sub += self.ui("  ·  next: {title}", title=self.slot_title(nxt[0]))
            progress = (mm - s.start) / max(1, s.end - s.start)
            mini_title, mini_left = self.slot_title(s), self.ui("{time} left", time=self.ui.duration(s.end - mm))
            mini_tip = self.ui("{title}\n{span} · {time} left", title=title, span=rng, time=self.ui.duration(s.end - mm))
        elif nxt:
            s, mins = nxt
            color = QColor(FALLBACK_COLOR)
            caption = self.ui("FREE")
            title = self.ui("Free time")
            rng = self.ui("until {time}", time=fmt_time(s.start))
            sub = self.ui("Next: {title} in {time}", title=self.slot_title(s), time=self.ui.duration(mins))
            mini_title, mini_left = self.ui("Free"), self.ui("in {time}", time=self.ui.duration(mins))
            mini_tip = self.ui("Free time {span}\nNext: {title}", span=rng, title=self.slot_title(s))
        else:
            color = QColor(FALLBACK_COLOR)
            caption, title, rng = self.ui("EMPTY"), self.ui("No schedule yet"), ""
            sub = self.ui("Click the pencil in the header to add slots")
            mini_title, mini_left, mini_tip = self.ui("Schedule is empty"), "", sub

        for bar in (self.st_progress, self.mini_progress):
            if progress is None:
                bar.hide()
            else:
                bar.set_value(progress, color)
                bar.show()
        self.mini_dot.setStyleSheet(f"background: {color.name()}; border-radius: 4px;")
        self.mini_title.set_full_text(mini_title)
        self.mini_left.setText(mini_left)
        self.mini.setToolTip(mini_tip)

        self.st_dot.setStyleSheet(f"background: {color.name()}; border-radius: 4px;")
        self.st_caption.setStyleSheet(f"color: {color.name()};")
        self.st_caption.set_full_text(caption)
        self.st_range.setText(rng)
        self.st_title.set_full_text(title)
        self.st_sub.set_full_text(sub)
        if getattr(self, "tray", None):
            self.tray.setToolTip(f"{title}\n{sub}")

    # ── действия ───────────────────────────────────────────────────────────

    # ── недели и навигация по ленте ────────────────────────────────────────

    def _current_week(self, day: date | None = None) -> int:
        """Какая неделя идет сейчас (0 — А, 1 — Б); в обычном режиме всегда 0."""
        if not self.cfg.biweekly:
            return 0
        return week_parity(self.cfg.week_anchor, self.cfg.week_anchor_week,
                           day or current_time().date())

    def _today_index(self, now: datetime | None = None) -> int:
        now = now or current_time()
        return self._current_week(now.date()) * 7 + now.weekday()

    def _page(self) -> int:
        """Индекс выбранного дня в ленте."""
        return self._view_week * 7 + self._selected

    def week_name(self, i: int) -> str:
        return self.cfg.week_names[i] or self.ui(DEFAULT_WEEK_KEYS[i])

    def _page_caption(self) -> str:
        cap = self.ui.weekday(self._selected).upper()
        if self.cfg.biweekly:
            cap += f" · {self.week_name(self._view_week)}"
        return cap

    def select_page(self, page: int) -> None:
        page %= len(self.days)
        self._view_week, self._selected = divmod(page, 7)
        if self._rendered_day != page:
            self._render_day(page)
            self._fit_height()
            self.refresh()
        for group, idx in ((self.pills, self._selected), (self.week_btns, self._view_week)):
            btn = group.button(idx)
            if btn and not btn.isChecked():
                btn.setChecked(True)
        self._update_today_marks()
        self._update_titles(current_time())
        self.scroll.verticalScrollBar().setValue(0)
        if page == self._today:
            for card in self.cards:
                if card.state == SlotCard.ACTIVE:
                    self.scroll.ensureWidgetVisible(card, 0, 40)
                    break
        if self._editing and self._card_for(self._edit_ref) is None:
            self._select_ref(self._default_ref(page))  # другой день — свой слот в форме

    def select_day(self, day: int) -> None:
        """День недели в просматриваемой неделе (клик по Пн…Вс)."""
        self.select_page(self._view_week * 7 + day % 7)

    def select_week(self, week: int) -> None:
        """Тот же день недели в другой неделе (клик по [Неделя А | Неделя Б], клавиша W)."""
        if self.cfg.biweekly:
            self.select_page((week % 2) * 7 + self._selected)

    def _update_today_marks(self) -> None:
        """Подсветка «сегодня»: день — только в текущей неделе; неделя — та, что идет сейчас."""
        today = self._today if self._today is not None else self._today_index()
        cur_week, wd = divmod(today, 7)
        for i, b in enumerate(self.pills.buttons()):
            set_flag_prop(b, "today", i == wd and self._view_week == cur_week)
        for i, b in enumerate(self.week_btns.buttons()):
            set_flag_prop(b, "today", i == cur_week)

    def _sync_week_ui(self, *, preserve_confirmation=False) -> None:
        """Названия недель и видимость переключателя — после смены режима или переименования."""
        on = self.cfg.biweekly
        self.week_bar.setVisible(on)
        cur = self._current_week()
        for i, b in enumerate(self.week_btns.buttons()):
            name = self.week_name(i)
            fm = b.fontMetrics()
            text = fm.elidedText(name, Qt.ElideRight, 78)
            b.setText(text)
            b.setAccessibleName(name)
            b.setFixedWidth(fm.horizontalAdvance(text) + 16)
            b.setToolTip(self.ui("{name} — current week", name=name) if i == cur else name)
        if self._editing:
            self.editor.set_caption(self._page_caption())
            target = self.week_name(1 - self._view_week) if on else None
            self.editor.set_copy_target(target, preserve_confirmation=preserve_confirmation)
        self._sync_settings()

    def _sync_settings(self) -> None:
        self.settings.set_state(self.cfg.biweekly, list(self.cfg.week_names),
                                [self.week_name(0), self.week_name(1)],
                                self._current_week() if self.cfg.biweekly else 0,
                                self.cfg.week_anchor, self.cfg.week_anchor_week)

    def set_biweekly(self, on: bool, *, save: bool = True) -> None:
        """Включить / выключить двухнедельный режим. Данные обеих раскладок сохраняются."""
        on = bool(on)
        self._commit()  # дописать отложенную правку в старую раскладку
        self.cfg.biweekly = on
        if on and self.cfg.week_anchor is None:
            self.cfg.week_anchor, self.cfg.week_anchor_week = monday_of(current_time().date()), 0
        self._edit_ref = None
        self._view_week = self._current_week()
        self._rebuild_timeline()
        self._sync_week_ui()
        self._today = None  # refresh перейдет на сегодняшний день новой ленты
        self._rebuild_view()
        if save:
            self._save_config()

    def set_current_week(self, week: int) -> None:
        """«Текущая неделя сейчас — это А / Б»: запомнить понедельник этой недели как опорный."""
        self.cfg.week_anchor = monday_of(current_time().date())
        self.cfg.week_anchor_week = week % 2
        self._save_config()
        self._sync_week_ui()
        self._today = None  # перейти на сегодняшний день в правильной неделе
        self.refresh()

    def _on_week_name_edited(self, i: int, text: str) -> None:
        self.cfg.week_names[i] = text
        self._sync_week_ui()
        self._cfg_save_timer.start()

    def _on_week_name_finished(self, i: int) -> None:
        self._cfg_save_timer.stop()
        self._save_config()

    # ── настройки ──────────────────────────────────────────────────────────

    def toggle_settings(self) -> None:
        if self._settings_open:
            self.close_settings()
        else:
            self.open_settings()

    def open_settings(self) -> None:
        if self.cfg.compact:
            self.set_compact(False)
        if not self.isVisible():
            self.toggle_visible()
        if self._editing:
            self.editor.hide()
        self._settings_open = True
        set_flag_prop(self.btn_settings, "accent", True)
        self._sync_settings()
        self.status.hide()
        self.settings.show()
        self._fit_height()
        self.activateWindow()

    def close_settings(self) -> None:
        if not self._settings_open:
            return
        for i in range(2):
            self._on_week_name_finished(i)
        self._settings_open = False
        self.settings.hide()
        set_flag_prop(self.btn_settings, "accent", False)
        if self._editing:
            self.editor.show()
        else:
            self.status.show()
        self._fit_height()
        self.setFocus(Qt.OtherFocusReason)

    def go_today(self) -> None:
        self.select_page(self._today_index())

    def toggle_visible(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()
            self.refresh()

    # ── редактирование расписания ──────────────────────────────────────────

    def toggle_editor(self) -> None:
        if self._editing and self._settings_open:
            self.close_settings()
        elif self._editing:
            self.close_editor()
        else:
            self.open_editor()

    def open_editor(self, ref: dict | None = None, *, focus_title: bool = False) -> None:
        if self._error:
            # иначе сохранение затрет битый файл последней рабочей версией
            self._editor_blocked = True
            self._show_file_errors()
            self.lbl_error.show()
            return
        if self.cfg.compact:
            self.set_compact(False)
        if not self.isVisible():
            self.toggle_visible()
        if self._settings_open:
            self.close_settings()
        if not self._editing:
            self._editing = True
            self.status.hide()
            self.editor.show()
            self._set_glyph(self.btn_edit, G_EDIT, accent=True)
            self.ui.bind(self.btn_edit.setAccessibleName, "Close editor")
            self.ui.bind(self.btn_edit.setToolTip, "Close editor (Esc)")
            self._rebuild_view()
        self._select_ref(ref if ref is not None else self._default_ref(self._page()))
        self.activateWindow()  # без активного окна поля не получат ввод с клавиатуры
        if focus_title:
            self.editor.focus_title()

    def close_editor(self) -> None:
        if not self._editing:
            return
        self._commit()  # дописать отложенную правку
        self._editing = False
        self._edit_ref = None
        self.editor.hide()
        self.status.setVisible(not self._settings_open)
        self._set_glyph(self.btn_edit, G_EDIT, accent=False)
        self.ui.bind(self.btn_edit.setAccessibleName, "Edit schedule")
        self.ui.bind(self.btn_edit.setToolTip, "Edit schedule (or double-click a slot)")
        self._rebuild_view()
        self.setFocus(Qt.OtherFocusReason)

    def _default_ref(self, page: int) -> dict | None:
        """Слот, который открывается в форме: идущий сейчас (для сегодня) или первый в дне."""
        slots = self.days[page]
        if not slots:
            return None
        now = current_time()
        if page == self._today_index(now):
            active = find_active(self.days, page, now.hour * 60 + now.minute)
            if active and active[0] == page:
                return slots[active[1]].ref
        return slots[0].ref

    def _card_for(self, ref: dict | None) -> SlotCard | None:
        if ref is None or not self.cards:
            return None
        return next((c for c in self.cards if c.slot.ref is ref), None)

    def _select_ref(self, ref: dict | None) -> None:
        self._edit_ref = ref
        for c in self.cards:
            c.set_selected(ref is not None and c.slot.ref is ref)
        self.editor.load(ref, self._page_caption(), self.categories)
        self._translate_category_choices()
        self.editor.set_copy_target(self.week_name(1 - self._view_week) if self.cfg.biweekly else None)
        self._scroll_timer.start()

    def _restore_scroll(self) -> None:
        if self._scroll_position is not None:
            self.scroll.verticalScrollBar().setValue(self._scroll_position)
            self._scroll_position = None
        card = self._card_for(self._edit_ref)
        if card is not None:
            self.scroll.ensureWidgetVisible(card, 0, 24)

    def _on_card_clicked(self, card: SlotCard) -> None:
        if self._editing and not self._settings_open:
            self._select_ref(card.slot.ref)

    def _on_card_double_clicked(self, card: SlotCard) -> None:
        if self._editing:
            self._select_ref(card.slot.ref)
            if self._settings_open:
                self.close_settings()
            self.editor.focus_title()
        else:
            self.open_editor(card.slot.ref, focus_title=True)

    def _on_field_changed(self, name: str, value, now: bool) -> None:
        item = self._edit_ref
        if item is None:
            return
        if name == "title":
            item["title"] = str(value)
        elif name == "time":
            item["time"] = str(value)
        elif name == "category":
            item["category"] = str(value)
            # пресет, которого нет в файле, дописываем в categories — цвет сохранится вместе с файлом
            if (value.lower() not in {k.lower() for k in self.file_categories}
                    and value in self.categories):
                self.file_categories[value] = self.categories[value]
        elif name == "duration":
            if value:
                item["duration"] = int(value)
            else:
                item.pop("duration", None)
        self._dirty = True
        if now:
            self._commit()
        else:
            self._commit_timer.start()  # печать / прокрутка времени — сохраняем, когда пауза

    def add_slot(self) -> None:
        day = self._page()
        base = next((s for s in self.days[day] if s.ref is not None and s.ref is self._edit_ref), None)
        if base is not None:
            # новый слот — посередине выбранного, чтобы сразу встать после него
            end = min(base.end, 1440)
            t = base.start + max(5, (end - base.start) // 2 // 5 * 5)
            category = base.category
        else:
            now = current_time()
            t = (now.hour * 60 + now.minute + 14) // 15 * 15 if day == self._today_index(now) else 12 * 60
            category = next(iter(self.file_categories or self.categories), "Общее")
        item = {"time": fmt_time(min(t, 1439)), "title": self.ui("New slot"), "category": category}
        self.raw_days[day].append(item)
        self._edit_ref = item
        self._dirty = True
        self._commit()
        self._select_ref(item)
        self.editor.focus_title()

    def delete_slot(self) -> None:
        ref, day = self._edit_ref, self._page()
        items = self.raw_days[day]
        idx = next((i for i, it in enumerate(items) if it is ref), None)
        if ref is None or idx is None:
            return
        refs = [s.ref for s in self.days[day]]
        pos = next((i for i, r in enumerate(refs) if r is ref), -1)
        neighbor = None
        if 0 <= pos + 1 < len(refs):
            neighbor = refs[pos + 1]
        elif pos > 0:
            neighbor = refs[pos - 1]
        del items[idx]
        self._edit_ref = neighbor
        self._dirty = True
        self._commit()
        self._select_ref(neighbor)

    def _commit(self) -> bool:
        """Отсортировать слоты по времени, записать schedule.json и перерисовать список.

        Возвращает False, если записать файл не удалось."""
        self._commit_timer.stop()
        if not self._dirty:
            return True
        self._dirty = False
        err = self._write_schedule()
        if err is not None:
            self._dirty = True  # попробуем снова при следующей правке
            self.editor.show_status("Could not save", error=True, detail=str(err))
        self.days = build_slots(self.raw_days)
        slots = self.days[self._page()]
        if (self._editing and len(slots) == len(self.cards)
                and all(card.slot.ref is slot.ref and card.slot.category == slot.category
                        for card, slot in zip(self.cards, slots))):
            for card, slot in zip(self.cards, slots):
                if card.slot != slot:
                    card.slot = slot
                    card.lbl_title.setText(self.slot_title(slot))
                    card.lbl_start.setText(fmt_time(slot.start))
                    card.lbl_end.setText(fmt_time(slot.end))
            self._fit_height()
            self.refresh()
        else:
            self._rebuild_view()
        if err is None and self._editing:
            self.editor.show_status("✓ Saved")
        return err is None

    def copy_day_to_other_week(self, confirmed: bool = False) -> None:
        """Скопировать расписание выбранного дня в тот же день другой недели (с заменой)."""
        if not self.cfg.biweekly or self._error:
            return
        self._commit()  # сначала дописать правку, которую еще печатают
        w, d = self._view_week, self._selected
        src, dst = self.raw_weeks[w][d], self.raw_weeks[1 - w][d]
        target = self.week_name(1 - w)
        if same_day_items(src, dst):
            self.editor.show_status("Already matches “{target}”", target=partial(self.week_name, 1 - w))
            return
        if dst and not confirmed:
            n = len(dst)
            self.editor.ask_copy_confirm(n)
            return
        dst[:] = copy.deepcopy(src)  # тот же список — лента и раскладка недель видят изменение
        self._dirty = True
        if self._commit():
            self.editor.set_copy_target(target)  # сбросить подтверждение
            self.editor.show_status("✓ Copied to “{target}”", target=partial(self.week_name, 1 - w))

    def _rebuild_view(self) -> None:
        self._scroll_position = self.scroll.verticalScrollBar().value()
        self._cat_lookup = {k.lower(): QColor(v) for k, v in self.categories.items()}
        self._build_pages()
        self._fit_height()
        self.refresh()

        self._scroll_timer.start()

    # ── фиксация, уровень окна, компактный режим ───────────────────────────

    def set_locked(self, locked: bool, *, save: bool = True) -> None:
        """Зафиксирован — перетаскивание мышью заблокировано. Откреплен — рамка подсвечена."""
        self.cfg.locked = bool(locked)
        self._press_pos = None
        self.setCursor(Qt.ArrowCursor if self.cfg.locked else Qt.SizeAllCursor)
        self._set_glyph(self.btn_lock, G_LOCKED if self.cfg.locked else G_UNLOCKED,
                        accent=not self.cfg.locked)
        self.ui.bind(self.btn_lock.setToolTip, "Position locked — click to unlock" if self.cfg.locked
                                 else "Drag to move — click to lock position")
        self.update()
        if save:
            self._save_config()

    def set_window_level(self, level: str, *, save: bool = True) -> None:
        """LEVEL_TOP — поверх всех окон; LEVEL_DESKTOP — на рабочем столе, под обычными окнами."""
        level = LEVEL_TOP if level == LEVEL_TOP else LEVEL_DESKTOP
        on_top = level == LEVEL_TOP
        self.cfg.window_level = level
        if self._desk:
            self._desk.set_enabled(False)  # не мешаем Qt выставить/снять «поверх всех»

        want = {Qt.WindowStaysOnTopHint: on_top}
        if not IS_WINDOWS:  # на Windows «низ» держит DesktopLayer, Qt-хинт там работает только при показе
            want[Qt.WindowStaysOnBottomHint] = not on_top
        if any(bool(self.windowFlags() & flag) != on for flag, on in want.items()):
            visible, pos = self.isVisible(), self.pos()
            for flag, on in want.items():
                self.setWindowFlag(flag, on)  # скрывает окно — показываем обратно
            if visible:
                self.move(pos)
                self.show()

        if self._desk and not on_top:
            self._desk.set_enabled(True)
        self._set_glyph(self.btn_level, G_ON_TOP if on_top else G_ON_DESKTOP, accent=on_top)
        self.ui.bind(self.btn_level.setToolTip, "Always on top — click to move to desktop" if on_top
                                  else "On desktop — click to keep above all windows")
        if save:
            self._save_config()

    def set_compact(self, on: bool, *, save: bool = True) -> None:
        """Мини-плашка с текущей задачей ↔ полный вид. Угол, ближний к краю экрана, остается на месте."""
        on = bool(on)
        if on and self._editing:
            self.close_editor()
        if on and self._settings_open:
            self.close_settings()
        old = self.geometry() if self.isVisible() else None
        self.cfg.compact = on
        self.full.setVisible(not on)
        self.mini.setVisible(on)
        self.setFixedWidth(self.MINI_WIDTH if on else self.cfg.width)
        self._fit_height()
        if old is not None:
            self._reanchor(old)
        self.refresh()
        if save:
            self._save_config()

    def _reanchor(self, old: QRect) -> None:
        screen = QGuiApplication.screenAt(old.center()) or self.screen() or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        w, h = self.width(), self.height()
        # по горизонтали держим сторону, ближнюю к краю экрана; к низу привязываемся,
        # только если виджет целиком в нижней половине (высокий полный вид — всегда от верха)
        right = avail.right() - old.right() < old.left() - avail.left()
        bottom = old.top() > avail.center().y()
        x = old.right() - w + 1 if right else old.left()
        y = old.bottom() - h + 1 if bottom else old.top()
        x = max(avail.left(), min(x, avail.right() - w + 1))
        y = max(avail.top(), min(y, avail.bottom() - h + 1))
        self.move(x, y)

    def _restore_pos(self) -> None:
        if self.cfg.x is not None and self.cfg.y is not None:
            pos = QPoint(self.cfg.x, self.cfg.y)
            if QGuiApplication.screenAt(pos + QPoint(60, 20)):  # монитор мог быть отключен
                self.move(pos)
                return
        g = QGuiApplication.primaryScreen().availableGeometry()
        self.move(g.right() - self.width() - 24, g.top() + 24)

    def _save_pos(self) -> None:
        p = self.pos()
        if (p.x(), p.y()) != (self.cfg.x, self.cfg.y):
            self.cfg.x, self.cfg.y = p.x(), p.y()
            self._save_config()

    def _save_config(self) -> None:
        try:
            self.cfg.save(CONFIG_PATH)
            self._config_error = ""
        except OSError as exc:
            self._config_error = exc
        if hasattr(self, "lbl_error"):
            self._show_file_errors()

    def _file_error_detail(self, error) -> str:
        if isinstance(error, InterfaceError):
            return self.ui(str(error))
        if isinstance(error, json.JSONDecodeError):
            return self.ui("Invalid JSON at line {line}, column {column}", line=error.lineno, column=error.colno)
        if isinstance(error, UnicodeDecodeError):
            return self.ui("Invalid UTF-8 at byte {offset}", offset=error.start)
        if isinstance(error, UnicodeEncodeError):
            return self.ui("Invalid Unicode character at position {offset}", offset=error.start)
        if isinstance(error, RecursionError):
            return self.ui("JSON nesting is too deep.")
        if isinstance(error, FileNotFoundError):
            return self.ui("File not found: {path}", path=error.filename or str(error))
        if isinstance(error, PermissionError):
            return self.ui("Access denied: {detail}", detail=str(error))
        if isinstance(error, OSError) and error.errno == errno.ENOSPC:
            return self.ui("Not enough disk space.")
        return str(error)

    def _show_file_errors(self) -> None:
        messages = []
        if self._error:
            messages.append(self.ui("Could not load schedule: {error}", error=self._file_error_detail(self._error)))
            if self._editor_blocked:
                messages.append(self.ui("Fix the file to enable editing."))
        if self._category_error:
            messages.append(self.ui("Could not save category color to schedule.json"))
        if getattr(self, "_config_error", None):
            messages.append(self.ui("Could not save settings: {error}", error=self._config_error))
        message = "\n".join(messages)
        self.lbl_error.setText(message)
        self.lbl_error.setVisible(bool(message))

    def _begin_move(self) -> None:
        if self.windowHandle():
            self.windowHandle().startSystemMove()  # нативное перетаскивание (со снапом Windows)

    def quit_app(self) -> bool:
        if not self._commit():
            self.set_compact(False, save=False)
            self.show()
            self.open_editor()
            self.editor.show_status("Could not save. Try quitting again.", error=True)
            return False
        self._save_pos_timer.stop()
        self.cfg.x, self.cfg.y = self.pos().x(), self.pos().y()
        self._save_config()
        if self._desk:
            self._desk.set_enabled(False)
        if getattr(self, "tray", None):
            self.tray.hide()
        QApplication.quit()
        return True

    # ── события окна ───────────────────────────────────────────────────────

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = min(RADIUS, rect.height() / 2)
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        background = QColor(self.cfg.background)
        background.setAlphaF(self.cfg.opacity / 100)
        p.fillPath(path, background)
        if self.cfg.locked:
            p.setPen(QPen(WINDOW_BORDER, 1))
            p.drawPath(path)
        else:
            # режим перемещения: акцентная рамка + мягкое внутреннее свечение
            for inset, alpha, width in ((3.0, 18, 4.0), (1.5, 45, 2.0), (0.0, 170, 1.2)):
                c = QColor(ACCENT)
                c.setAlpha(alpha)
                p.setPen(QPen(c, width))
                p.drawRoundedRect(rect.adjusted(inset, inset, -inset, -inset),
                                  max(1.0, radius - inset), max(1.0, radius - inset))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            # перетаскивание начнется только после небольшого сдвига — клики и двойной клик не ломаются
            self._press_pos = None if self.cfg.locked else e.globalPosition().toPoint()
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if (self._press_pos is not None and e.buttons() & Qt.LeftButton
                and (e.globalPosition().toPoint() - self._press_pos).manhattanLength()
                >= QApplication.startDragDistance()):
            self._press_pos = None
            self._begin_move()
            e.accept()
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._press_pos = None
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton and self.cfg.compact:
            self.set_compact(False)
            e.accept()
        else:
            super().mouseDoubleClickEvent(e)

    def moveEvent(self, e):
        super().moveEvent(e)
        if hasattr(self, "_save_pos_timer"):
            self._save_pos_timer.start()

    def nativeEvent(self, event_type, message):
        if self._desk is not None and self._desk.enabled and bytes(event_type) == b"windows_generic_MSG":
            self._desk.filter_message(int(message))
        return False, 0

    def changeEvent(self, e):
        super().changeEvent(e)
        # «на рабочем столе» виджет не должен сворачиваться (Win+M, Win+D и т. п.)
        if (e.type() == QEvent.WindowStateChange and self.isMinimized()
                and self.cfg.window_level == LEVEL_DESKTOP):
            QTimer.singleShot(0, self.showNormal)

    def contextMenuEvent(self, e):
        self.menu.exec(e.globalPos())

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key_Escape and self._settings_open:
            self.close_settings()
        elif k == Qt.Key_Escape and self._editing:
            self.close_editor()
        elif k == Qt.Key_E and e.modifiers() & Qt.ControlModifier:
            self.toggle_editor()
        elif k == Qt.Key_Left:
            self.select_page(self._page() - 1)  # через понедельник — в воскресенье прошлой недели
        elif k == Qt.Key_Right:
            self.select_page(self._page() + 1)
        elif k == Qt.Key_W and self.cfg.biweekly:
            self.select_week(1 - self._view_week)
        elif k in (Qt.Key_Home, Qt.Key_T):
            self.go_today()
        elif k == Qt.Key_Q and e.modifiers() & Qt.ControlModifier:
            self.quit_app()
        else:
            super().keyPressEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        if hasattr(self, "_tick"):
            self.refresh()

    def closeEvent(self, e):
        if self.quit_app():
            e.accept()
        else:
            e.ignore()


def _dark_palette() -> QPalette:
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor("#141418"))
    pal.setColor(QPalette.WindowText, QColor("#EDEDF0"))
    pal.setColor(QPalette.Base, QColor("#1C1C22"))
    pal.setColor(QPalette.Text, QColor("#EDEDF0"))
    pal.setColor(QPalette.Button, QColor("#1C1C22"))
    pal.setColor(QPalette.ButtonText, QColor("#EDEDF0"))
    pal.setColor(QPalette.ToolTipBase, QColor("#1C1C22"))
    pal.setColor(QPalette.ToolTipText, QColor("#FFFFFF"))
    pal.setColor(QPalette.Highlight, QColor("#4C8DFF"))
    pal.setColor(QPalette.HighlightedText, QColor("#FFFFFF"))
    return pal


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Schedule Widget")
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")  # предсказуемая отрисовка стилей на любой версии Windows
    app.setPalette(_dark_palette())
    app.setFont(make_font(13))
    app.setStyleSheet(QSS)

    repair_autostart()  # .exe перенесли в другую папку — обновить путь в реестре

    w = ScheduleWidget()
    # An opt-in taskbar window makes the same UI accessible to window-based
    # screen capture and assistive tools. The normal desktop launch is unchanged.
    if "--window" in sys.argv:
        if w._desk:
            w._desk.set_enabled(False)
        w.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
