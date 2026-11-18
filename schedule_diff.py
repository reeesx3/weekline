"""Compare the three normalized timetables without assigning IDs to events."""
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field


def _value(item):
    return json.dumps(item, ensure_ascii=False, sort_keys=True)


def _unmatched(items, other_counts):
    remaining = []
    for item in items:
        key = _value(item)
        if other_counts[key]:
            other_counts[key] -= 1
        else:
            remaining.append(item)
    return remaining


def _identifiers(item, sort_key, combined):
    if isinstance(item, dict):
        invalid, minute = sort_key(item)
        title = item.get("title")
        has_title = isinstance(title, str) and bool(title)
        if combined:
            if not invalid and has_title:
                yield (minute, title)
        else:
            if not invalid:
                yield ("start", minute)
            if has_title:
                yield ("title", title)


def _unique_partners(items, others, sort_key, combined):
    index = defaultdict(list)
    for i, item in enumerate(others):
        for key in _identifiers(item, sort_key, combined):
            index[key].append(i)
    partners = {}
    for i, item in enumerate(items):
        groups = [index[key] for key in _identifiers(item, sort_key, combined) if key in index]
        if groups and all(len(group) == 1 and group == groups[0] for group in groups):
            partners[i] = groups[0][0]
    return partners


def _entries_at_start(items, sort_key):
    starts = defaultdict(list)
    for item in items:
        invalid, minute = sort_key(item)
        if not invalid:
            starts[minute].append(item)
    return starts


@dataclass
class DayChanges:
    added: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    edited: list = field(default_factory=list)
    order: list = field(default_factory=list)


def _compare_day(before, after, sort_key):
    old = _unmatched(before, Counter(map(_value, after)))
    new = _unmatched(after, Counter(map(_value, before)))
    changes = DayChanges()
    # First match a unique (time, title) together. Then a time or a title
    # alone can explain an edit, but only when neither side has alternatives.
    for combined in (True, False):
        forward = _unique_partners(old, new, sort_key, combined)
        reverse = _unique_partners(new, old, sort_key, combined)
        paired = {i: j for i, j in forward.items() if reverse.get(j) == i}
        matched = set(paired.values())
        changes.edited.extend((old[i], new[j]) for i, j in paired.items())
        old = [item for i, item in enumerate(old) if i not in paired]
        new = [item for j, item in enumerate(new) if j not in matched]
    changes.removed, changes.added = old, new

    # build_slots uses a stable time sort. Swapping two events at the same
    # minute can change which event is active even when their fields match.
    old_starts = _entries_at_start(before, sort_key)
    new_starts = _entries_at_start(after, sort_key)
    for minute in sorted(old_starts.keys() & new_starts.keys()):
        a, b = old_starts[minute], new_starts[minute]
        if max(len(a), len(b)) > 1 and _value(a) != _value(b):
            changes.order.append((minute, a, b))
    return changes


FIELD_LABELS = {"time": "Start time", "title": "Task title", "category": "Category", "duration": "Duration"}


def _field_name(key, ui):
    return ui(FIELD_LABELS[key]) if key in FIELD_LABELS else _value(key)


def _describe(item, ui):
    if not isinstance(item, dict) or not item:
        return _value(item)
    keys = [key for key in FIELD_LABELS if key in item] + sorted(item.keys() - FIELD_LABELS.keys())
    return " · ".join(f"{_field_name(key, ui)}: {_value(item[key])}" for key in keys)


def _day_details(changes, ui):
    lines = ["− " + _describe(item, ui) for item in changes.removed]
    lines.extend("+ " + _describe(item, ui) for item in changes.added)
    for old, new in changes.edited:
        lines.append("• " + _describe(old, ui))
        for key in sorted(old.keys() | new.keys()):
            before = _value(old[key]) if key in old else ui("Absent")
            after = _value(new[key]) if key in new else ui("Absent")
            if before != after:
                lines.append(f"    {_field_name(key, ui)}: {before} → {after}")
    for minute, before, after in changes.order:
        lines.append(ui("Event order at {time}", time=f"{minute // 60:02}:{minute % 60:02}"))
        for prefix, items in (("−", before), ("+", after)):
            for index, item in enumerate(items, 1):
                lines.append(f"    {prefix} {index}. {_describe(item, ui)}")
    return lines


def schedule_changes(current, incoming, ui, sort_key):
    """Return a localized (summary, details) pair, or None for a no-op."""
    added = removed = edited = ordered = 0
    sections = []
    for name, before, after in zip(("Regular schedule", "Week A", "Week B"),
                                   (current.days, *current.weeks), (incoming.days, *incoming.weeks)):
        days = []
        for index, (old, new) in enumerate(zip(before, after)):
            changes = _compare_day(old, new, sort_key)
            lines = _day_details(changes, ui)
            if lines:
                days.append("\n".join([ui.weekday(index), *lines]))
            added += len(changes.added)
            removed += len(changes.removed)
            edited += len(changes.edited)
            ordered += len(changes.order)
        sections.append(ui(name) + "\n" + ("\n\n".join(days) if days else ui("No changes.")))

    cats = []
    old, new = current.file_categories, incoming.file_categories
    for name in sorted(old.keys() | new.keys()):
        if name not in old:
            cats.append(f"+ {_value(name)}: {new[name]}")
        elif name not in new:
            cats.append(f"− {_value(name)}: {old[name]}")
        elif old[name] != new[name]:
            cats.append(f"{_value(name)}: {old[name]} → {new[name]}")
    if not (added or removed or edited or ordered or cats):
        return None
    sections.append(ui("Categories") + "\n" + "\n".join(cats or [ui("No changes.")]))
    summary = ui("Events added: {added} · removed: {removed} · edited: {edited}. Category changes: {cats}.",
                 added=added, removed=removed, edited=edited, cats=len(cats))
    if ordered:
        summary += " " + ui("Changes to same-time lists: {count}.", count=ordered)
    return summary, "\n\n".join(sections)
