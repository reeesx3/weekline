"""Weekline interface messages. English source strings double as message keys."""
from PySide6.QtCore import QDate, QLocale


RUSSIAN = {
    "": "",
    "Window level": "Уровень окна",
    "Start time": "Начало",
    "Duration": "Длительность",
    "NOW": "СЕЙЧАС",
    "Done — close editor (Esc)": "Готово — закрыть редактор (Esc)",
    "Start time. Scroll or press ↑/↓ to change the hours or minutes under the cursor": "Начало слота. Колесо мыши или ↑/↓ меняют часы или минуты под курсором",
    "Task title": "Название задачи",
    " min": " мин",
    "until next slot": "до следующего",
    "Duration in minutes. Set to 0 to end when the next slot starts": "Длительность в минутах. 0 — слот длится до начала следующего",
    "START": "НАЧАЛО",
    "TITLE": "НАЗВАНИЕ",
    "CATEGORY": "КАТЕГОРИЯ",
    "DURATION": "ДЛИТЕЛЬНОСТЬ",
    "Delete": "Удалить",
    "Delete selected slot (click twice to confirm)": "Удалить выбранный слот (нажмите дважды для подтверждения)",
    "+  Add slot": "+  Добавить слот",
    "Add a slot to this day": "Добавить новый слот в этот день",
    "Click again to replace": "Нажмите еще раз, чтобы заменить",
    "Select a slot or add one": "Выберите слот или добавьте новый",
    "Confirm deletion?": "Точно удалить?",
    "Copy this day to “{name}”": "Скопировать этот день в «{name}»",
    "Copy this day’s schedule to “{name}” in the other week": "Скопировать расписание этого дня в другую неделю — «{name}»",
    "SETTINGS": "НАСТРОЙКИ",
    "Done — close settings (Esc)": "Готово — закрыть настройки (Esc)",
    "Alternating weeks": "Двухнедельный режим",
    "Separate schedules for two alternating weeks": "Отдельное расписание для двух чередующихся недель",
    "WEEK NAMES": "НАЗВАНИЯ НЕДЕЛЬ",
    "For example, “Week 1”. Leave blank to use the default name": "Например, «1 нед.» — пустое поле вернет название по умолчанию",
    "CURRENT WEEK": "ТЕКУЩАЯ НЕДЕЛЯ СЕЙЧАС — ЭТО",
    "Language": "Язык",
    "Font": "Шрифт",
    "Default": "По умолчанию",
    "Text size": "Размер текста",
    "Small": "Мелкий",
    "Normal": "Обычный",
    "Medium": "Средний",
    "Large": "Крупный",
    "Extra large": "Очень крупный",
    "Choose color…": "Выбрать цвет…",
    "Accent color": "Цвет акцента",
    "Accent": "Акцент",
    "Background": "Фон",
    "Graphite": "Графит",
    "Blue gray": "Сине-серый",
    "Warm gray": "Тёплый серый",
    "Widget width": "Ширина виджета",
    "Background opacity": "Непрозрачность фона",
    "To add a category, enter a name and click “Color…”": "Чтобы добавить категорию, введите новое название и нажмите «Цвет…»",
    "Category": "Категория",
    "Color…": "Цвет…",
    "Reset appearance": "Сбросить оформление",
    "Show clock in header": "Часы в шапке",
    "Show category names in list": "Названия категорий в списке",
    "Compact list spacing": "Уменьшенные отступы в списке",
    "Schedule": "Расписание",
    "Appearance": "Оформление",
    "Elements": "Элементы",
    "The schedule alternates between “{a}” and “{b}”.": "Расписание чередуется: «{a}» и «{b}».",
    "One schedule for every week. Enable this if your weeks alternate.": "Одно расписание на все недели. Включите, если недели чередуются.",
    "Set this week to “{name}”. Weeks will alternate every Monday.": "Отметить эту неделю как «{name}» — дальше недели будут переключаться автоматически каждый понедельник",
    "Reference Monday: {date} (“{name}”). Weeks alternate automatically.": "Опорный понедельник — {date} («{name}»). Дальше недели меняются автоматически.",
    "Weeks alternate automatically every Monday.": "Недели меняются автоматически каждый понедельник.",
    "Color for “{name}”": "Цвет категории «{name}»",
    "Could not save category color to schedule.json": "Не удалось сохранить цвет категории в schedule.json",
    "Edit schedule (or double-click a slot)": "Редактировать расписание (или двойной клик по слоту)",
    "Schedule and appearance settings": "Настройки расписания и оформления",
    "Settings": "Настройки",
    "Switch to compact view": "Свернуть в мини-плашку",
    "Close": "Закрыть",
    "Expand": "Развернуть",
    "Startup": "Автозапуск",
    "Could not change startup settings: {error}": "Не удалось изменить автозапуск: {error}",
    "Hide": "Скрыть",
    "Lock position": "Зафиксировать позицию",
    "Start with Windows": "Автозапуск с Windows",
    "Settings…": "Настройки…",
    "Open JSON folder": "Открыть папку с JSON",
    "Quit": "Выход",
    "Show": "Показать",
    "Hide widget": "Скрыть виджет",
    "Today": "Сегодня",
    "Always on top": "Поверх всех окон",
    "On desktop": "На рабочем столе",
    "Edit schedule": "Редактировать расписание",
    "Open schedule.json": "Открыть schedule.json",
    "Import schedule…": "Импортировать расписание…",
    "JSON files (*.json);;All files (*)": "Файлы JSON (*.json);;Все файлы (*)",
    "Replace the entire schedule with “{name}”?": "Заменить всё расписание данными из файла «{name}»?",
    "The current schedule changed. Review the updated comparison.": "Текущее расписание изменилось. Проверьте обновлённое сравнение.",
    "Schedule changes": "Изменения в расписании",
    "Import": "Импортировать",
    "Cancel": "Отмена",
    "Regular schedule": "Обычное расписание",
    "Categories": "Категории",
    "Absent": "Отсутствует",
    "No changes.": "Изменений нет.",
    "Events added: {added} · removed: {removed} · edited: {edited}. Category changes: {cats}.": "Добавлено событий: {added} · удалено: {removed} · изменено: {edited}. Изменений категорий: {cats}.",
    "Changes to same-time lists: {count}.": "Изменений в группах событий с одинаковым временем: {count}.",
    "Event order at {time}": "Порядок событий в {time}",
    "Could not import schedule: {error}": "Не удалось импортировать расписание: {error}",
    "Could not save schedule.json: {error}": "Не удалось записать schedule.json: {error}",
    "Not enough disk space.": "Недостаточно места на диске.",
    "File not found: {path}": "Файл не найден: {path}",
    "Access denied: {detail}": "Отказано в доступе: {detail}",
    "Invalid Unicode character at position {offset}": "Некорректный символ Unicode на позиции {offset}",
    "JSON nesting is too deep.": "Слишком большая глубина вложенности JSON.",
    "Reload schedule": "Перечитать расписание",
    "Show widget": "Показать виджет",
    "Close editor": "Закрыть редактор",
    "Close settings": "Закрыть настройки",
    "Could not load schedule: {error}": "Не удалось загрузить расписание: {error}",
    "Invalid JSON at line {line}, column {column}": "Ошибка JSON в строке {line}, столбце {column}",
    "Invalid UTF-8 at byte {offset}": "Некорректный UTF-8 на позиции {offset}",
    "{detail}": "{detail}",
    "Empty — click “Add slot”": "Пусто — нажмите «Добавить слот»",
    "Nothing scheduled for this day": "На этот день ничего не запланировано",
    "Tomorrow": "Завтра",
    "Yesterday": "Вчера",
    "NOW · {category}": "СЕЙЧАС · {category}",
    "{time} remaining": "Осталось {time}",
    "  ·  next: {title}": "  ·  далее {title}",
    "{time} left": "ещё {time}",
    "{title}\n{span} · {time} left": "{title}\n{span} · осталось {time}",
    "FREE": "СВОБОДНО",
    "Free time": "Свободное время",
    "until {time}": "до {time}",
    "Next: {title} in {time}": "Далее {title} через {time}",
    "Free": "Свободно",
    "in {time}": "через {time}",
    "Free time {span}\nNext: {title}": "Свободное время {span}\nДалее: {title}",
    "EMPTY": "ПУСТО",
    "No schedule yet": "Расписание не заполнено",
    "Click the pencil in the header to add slots": "Нажмите карандаш в шапке, чтобы добавить слоты",
    "Schedule is empty": "Расписание пусто",
    "{name} — current week": "{name} — идет сейчас",
    "Fix the file to enable editing.": "Исправьте файл — тогда редактор заработает.",
    "Close editor (Esc)": "Закрыть редактор (Esc)",
    "New slot": "Новый слот",
    "Could not save": "Не удалось сохранить",
    "✓ Saved": "✓ Сохранено",
    "Already matches “{target}”": "Уже совпадает с «{target}»",
    "Replace {n} {slots} in “{target}”?": "Заменить {n} {slots} в «{target}»?",
    "✓ Copied to “{target}”": "✓ Скопировано в «{target}»",
    "Position locked — click to unlock": "Позиция зафиксирована — нажмите, чтобы открепить",
    "Drag to move — click to lock position": "Режим перемещения — нажмите, чтобы зафиксировать",
    "Always on top — click to move to desktop": "Поверх всех окон — нажмите, чтобы убрать на рабочий стол",
    "On desktop — click to keep above all windows": "На рабочем столе — нажмите, чтобы держать поверх всех окон",
    "Could not save settings: {error}": "Не удалось сохранить настройки: {error}",
    "Could not save. Try quitting again.": "Не удалось сохранить. Повторите выход.",
    "Week A": "Неделя А",
    "Week B": "Неделя Б",
    "Untitled": "Без названия",
    "General": "Общее",
    "Expected an object with a \"days\" section": "ожидается объект с разделом \"days\"",
    "Expected an object with a \"days\" or \"weeks\" section": "ожидается объект с разделом \"days\" или \"weeks\"",
    "Startup is only supported on Windows": "Автозапуск поддерживается только в Windows",
    "{h} h": "{h} ч",
    "{m} min": "{m} мин",
}


class InterfaceText:
    def __init__(self, language):
        self._bindings = {}
        self.set_language(language)

    def set_language(self, language):
        self.language = language
        self.locale = QLocale("en_US" if language == "en" else "ru_RU")
        for setter, (key, values) in self._bindings.items():
            setter(self(key, **values))

    def __call__(self, key, **values):
        template = RUSSIAN[key] if self.language == "ru" else key
        # Some parameters (for example an unnamed week's label) also follow the locale.
        return template.format(**{k: v() if callable(v) else v for k, v in values.items()})

    def bind(self, setter, key, **values):
        """Keep a persistent control's current message in sync with the language."""
        self._bindings[setter] = key, values
        setter(self(key, **values))

    def weekday(self, index, short=False):
        if short and self.language == "en":
            return ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")[index]
        fmt = QLocale.ShortFormat if short else QLocale.LongFormat
        return self.locale.dayName(index + 1, fmt).capitalize()

    def date(self, day, year=False):
        pattern = "MMMM d" if self.language == "en" else "d MMMM"
        if year:
            pattern += ", yyyy" if self.language == "en" else " yyyy"
        return self.locale.toString(QDate(day.year, day.month, day.day), pattern)

    def duration(self, minutes):
        h, m = divmod(max(0, minutes), 60)
        parts = []
        if h:
            parts.append(self("{h} h", h=h))
        if m or not h:
            parts.append(self("{m} min", m=m))
        return " ".join(parts)

    def slots(self, n):
        if self.language == "en":
            return "slot" if n == 1 else "slots"
        if n % 10 == 1 and n % 100 != 11:
            return "слот"
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return "слота"
        return "слотов"

