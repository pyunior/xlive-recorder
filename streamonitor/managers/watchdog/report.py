from datetime import datetime, timedelta

from streamonitor.managers.watchdog.telegram import html_escape

DAYS_ES = ['lun', 'mar', 'mie', 'jue', 'vie', 'sab', 'dom']
MONTHS_ES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun',
             'jul', 'ago', 'sep', 'oct', 'nov', 'dic']


def fmt_duration(seconds):
    seconds = max(0, int(round(seconds)))
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def fmt_datetime(ts, tz):
    dt = datetime.fromtimestamp(ts, tz)
    return f"{DAYS_ES[dt.weekday()]} {dt.day:02d} {MONTHS_ES[dt.month - 1]} {dt:%H:%M}"


def fmt_time(ts, tz):
    return datetime.fromtimestamp(ts, tz).strftime("%H:%M")


def subtract(intervals, holes):
    """Remove every `holes` span from `intervals`. Both are (start, end) lists."""
    result = list(intervals)
    for hs, he in holes:
        nxt = []
        for s, e in result:
            if he <= s or hs >= e:
                nxt.append((s, e))
                continue
            if s < hs:
                nxt.append((s, hs))
            if he < e:
                nxt.append((he, e))
        result = nxt
    return [(s, e) for s, e in result if e > s]


def total(intervals):
    return sum(e - s for s, e in intervals)


def window_for(report_day, day_start_hour, tz):
    """The window a report published on `report_day` covers:
    day_start_hour the previous day up to day_start_hour on `report_day`."""
    end = datetime(report_day.year, report_day.month, report_day.day,
                   day_start_hour, 0, 0, tzinfo=tz)
    start = end - timedelta(days=1)
    return start.timestamp(), end.timestamp()


def room_stats(store, site, room, t0, t1, gaps):
    """Online intervals for one room inside [t0, t1), minus watchdog downtime."""
    baseline = store.last_event(site, room, before=t0)
    state = bool(baseline['online']) if baseline else False

    intervals = []
    cursor = t0
    for ev in store.events_between(site, room, t0, t1):
        if state and ev['ts'] > cursor:
            intervals.append((cursor, ev['ts']))
        state = bool(ev['online'])
        cursor = ev['ts']
    if state and t1 > cursor:
        intervals.append((cursor, t1))

    measured = subtract(intervals, gaps)
    return {
        'site': site,
        'room': room,
        'seconds': total(measured),
        'raw_seconds': total(intervals),
        'sessions': len(measured),
        'first_on': measured[0][0] if measured else None,
        'last_off': measured[-1][1] if measured else None,
        'online_at_end': state,
    }


def build_report(store, rooms, t0, t1, tz, gap_threshold, title="Resumen de transmision"):
    """rooms: list of (site, room, siteslug, url). Returns an HTML message."""
    gaps = store.downtime_gaps(t0, t1, gap_threshold)
    downtime = total(gaps)

    stats = []
    for site, room, slug, url in rooms:
        s = room_stats(store, site, room, t0, t1, gaps)
        s['slug'] = slug
        s['url'] = url
        stats.append(s)

    # Only rooms that actually transmitted are listed; the rest are just counted.
    active = [s for s in stats if s['seconds'] >= 60]
    active.sort(key=lambda s: s['seconds'], reverse=True)

    grand = sum(s['seconds'] for s in stats)
    window_len = max(1.0, (t1 - t0) - downtime)

    lines = [
        f"📊 <b>{html_escape(title)}</b>",
        f"🗓 {fmt_datetime(t0, tz)} → {fmt_datetime(t1, tz)}",
        "",
    ]

    if active:
        for i, s in enumerate(active, 1):
            name = html_escape(s['room'])
            label = f'<a href="{html_escape(s["url"])}">{name}</a>' if s['url'] and s['url'].startswith('http') else f"<code>{name}</code>"
            pct = s['seconds'] / window_len * 100
            detail = (f"{s['sessions']} ses · {fmt_time(s['first_on'], tz)}"
                      f"→{fmt_time(s['last_off'], tz)} · {pct:.0f}%")
            lines.append(f"{i}. {label} [{html_escape(s['slug'])}]")
            lines.append(f"    ⏱ <b>{fmt_duration(s['seconds'])}</b> · {detail}")
    else:
        lines.append("<i>Ninguna sala registro transmision en la ventana.</i>")

    lines.append("")
    lines.append(f"🟢 Con transmision: <b>{len(active)}</b> de {len(stats)} salas")
    lines.append(f"Σ Total: <b>{fmt_duration(grand)}</b>")
    if active:
        lines.append(f"x̄ Promedio (activas): {fmt_duration(grand / len(active))}")

    if downtime > 60:
        lines.append("")
        lines.append(
            f"⚠️ Monitoreo caido {fmt_duration(downtime)} en la ventana; "
            "ese tiempo se descuenta y no se cuenta como transmision.")

    return "\n".join(lines)
