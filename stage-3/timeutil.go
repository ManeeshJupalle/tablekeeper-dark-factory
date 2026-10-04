package main

import (
	"fmt"
	"regexp"
	"strconv"
	"time"
)

// rfc3339Offset always writes a numeric offset (+00:00 rather than Z).
const rfc3339Offset = "2006-01-02T15:04:05-07:00"

const (
	minutesPerHour = 60
	minutesPerDay  = 24 * minutesPerHour
	secondsPerDay  = 24 * 60 * 60
)

var (
	localTimeRE = regexp.MustCompile(`^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$`)
	dateRE      = regexp.MustCompile(`^(\d{4})-(\d{2})-(\d{2})$`)
	hhmmRE      = regexp.MustCompile(`^(\d{2}):(\d{2})$`)
)

// localDate is a calendar date with no zone attached.
type localDate struct{ Year, Month, Day int }

// localTime is a bare wall-clock time: a date plus minutes since midnight.
type localTime struct {
	localDate
	Minute int
}

func (d localDate) String() string {
	return fmt.Sprintf("%04d-%02d-%02d", d.Year, d.Month, d.Day)
}

// addDays returns the calendar date n days later (n may be negative).
func (d localDate) addDays(n int) localDate {
	t := time.Date(d.Year, time.Month(d.Month), d.Day+n, 0, 0, 0, 0, time.UTC)
	return localDate{t.Year(), int(t.Month()), t.Day()}
}

// compare orders two dates: negative, zero or positive.
func (d localDate) compare(o localDate) int {
	switch {
	case d.Year != o.Year:
		return d.Year - o.Year
	case d.Month != o.Month:
		return d.Month - o.Month
	default:
		return d.Day - o.Day
	}
}

// String renders the YYYY-MM-DDTHH:MM form used by the API.
func (t localTime) String() string {
	return fmt.Sprintf("%sT%02d:%02d", t.localDate, t.Minute/minutesPerHour, t.Minute%minutesPerHour)
}

func atoi(s string) int {
	n, _ := strconv.Atoi(s)
	return n
}

func validDate(y, m, d int) bool {
	if m < 1 || m > 12 || d < 1 {
		return false
	}
	t := time.Date(y, time.Month(m), d, 0, 0, 0, 0, time.UTC)
	return t.Year() == y && int(t.Month()) == m && t.Day() == d
}

// parseLocalDate accepts exactly YYYY-MM-DD naming a real calendar date.
func parseLocalDate(s string) (localDate, bool) {
	m := dateRE.FindStringSubmatch(s)
	if m == nil {
		return localDate{}, false
	}
	d := localDate{atoi(m[1]), atoi(m[2]), atoi(m[3])}
	return d, validDate(d.Year, d.Month, d.Day)
}

// parseLocalTime accepts exactly YYYY-MM-DDTHH:MM: no seconds, no offset, no Z.
func parseLocalTime(s string) (localTime, bool) {
	m := localTimeRE.FindStringSubmatch(s)
	if m == nil {
		return localTime{}, false
	}
	d := localDate{atoi(m[1]), atoi(m[2]), atoi(m[3])}
	hh, mm := atoi(m[4]), atoi(m[5])
	if !validDate(d.Year, d.Month, d.Day) || hh > 23 || mm > 59 {
		return localTime{}, false
	}
	return localTime{d, hh*minutesPerHour + mm}, true
}

// parseHHMM parses an opening-hours boundary as minutes since midnight.
// 24:00 is allowed so a day may close at midnight.
func parseHHMM(s string) (int, bool) {
	m := hhmmRE.FindStringSubmatch(s)
	if m == nil {
		return 0, false
	}
	hh, mm := atoi(m[1]), atoi(m[2])
	total := hh*minutesPerHour + mm
	if mm > 59 || total > minutesPerDay {
		return 0, false
	}
	return total, true
}

// resolveLocal maps a wall-clock time in loc to an instant. ok is false when the
// time falls in a daylight-saving gap. A time that occurs twice resolves to the
// first occurrence, i.e. the pre-transition offset.
func resolveLocal(loc *time.Location, lt localTime) (time.Time, bool) {
	wall := time.Date(lt.Year, time.Month(lt.Month), lt.Day, 0, lt.Minute, 0, 0, time.UTC).Unix()
	seen := map[int]bool{}
	var best time.Time
	found := false
	// Offsets in force a day either side of the wall time cover both sides of
	// any transition near it.
	for _, probe := range []int64{wall - secondsPerDay, wall, wall + secondsPerDay} {
		_, off := time.Unix(probe, 0).In(loc).Zone()
		if seen[off] {
			continue
		}
		seen[off] = true
		candidate := time.Unix(wall-int64(off), 0).In(loc)
		if _, actual := candidate.Zone(); actual != off {
			continue
		}
		if !found || candidate.Before(best) {
			best, found = candidate, true
		}
	}
	return best, found
}

var weekdayCodes = [...]string{"sun", "mon", "tue", "wed", "thu", "fri", "sat"}

func validWeekday(code string) bool {
	for _, c := range weekdayCodes {
		if c == code {
			return true
		}
	}
	return false
}

func weekdayOf(d localDate) string {
	t := time.Date(d.Year, time.Month(d.Month), d.Day, 0, 0, 0, 0, time.UTC)
	return weekdayCodes[t.Weekday()]
}

func formatTime(t time.Time) string { return t.Format(rfc3339Offset) }
