// Day Math — calendar-date arithmetic for due dates and ages.
//
// The whole point of this module is that a bare YYYY-MM-DD is a CALENDAR DATE,
// not an instant, and the two must never be subtracted from each other.
//
// The bug this exists to prevent, found 2026-09-10 by looking at a render:
// parsing "2026-09-11" as 2026-09-11T00:00:00Z and diffing it against Date.now()
// gives a sub-day fraction that rounds to zero all morning, so an item due
// TOMORROW renders as "today". The same expression is wrong in the other
// direction west of UTC -- after 8pm Eastern, "now" has already crossed into
// the next UTC day, so an item due tomorrow reads as due today, every evening.
//
// Comparing local midnights instead makes both cases correct: the answer is a
// whole number of calendar days as a person in that timezone would count them.
// Returns: { localDateKey, calendarDaysUntil, calendarDaysSince }

function pad(n) { return String(n).padStart(2, "0"); }

// The viewer's local calendar date, as YYYY-MM-DD.
function localDateKey(nowMs) {
  const d = new Date(nowMs);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

// Local midnight of a bare YYYY-MM-DD, or of the local day containing an
// instant. Built from the numeric parts rather than parsed, because
// `new Date("2026-09-11")` is specified to parse as UTC while
// `new Date("2026-09-11T00:00")` parses as local -- a difference that is
// invisible until it moves a date by a day.
function localMidnightOf(value) {
  if (typeof value === "number") {
    const d = new Date(value);
    return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  }
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value));
  if (!m) return null;
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])).getTime();
}

// Whole calendar days from today to `date`. Positive = future, 0 = today,
// negative = past. Rounding rather than flooring absorbs the 23- and 25-hour
// days that DST transitions produce.
function calendarDaysUntil(date, nowMs) {
  const target = localMidnightOf(date);
  if (target == null) return null;
  const today = localMidnightOf(nowMs);
  return Math.round((target - today) / 86400000);
}

// Whole calendar days since `date`. Positive = in the past.
function calendarDaysSince(date, nowMs) {
  const d = calendarDaysUntil(date, nowMs);
  return d == null ? null : -d;
}

return { localDateKey, calendarDaysUntil, calendarDaysSince };
