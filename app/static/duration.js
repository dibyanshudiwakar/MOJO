// Durations read in minutes and seconds, not raw seconds.
//
// Agent runs in this factory are routinely minutes long -- a code-generation
// step against a real ACE repository takes ten to twenty, and the full suite of
// fifteen agents takes an hour -- and every one of them rendered as "847.3s",
// which nobody reads as "just over fourteen minutes".
//
// This lives in its own file because it is pure arithmetic worth testing, and
// app.js cannot be loaded outside a browser: its tail wires DOM handlers at
// script-evaluation time, so anything defined there is unreachable from a test.
// tests/test_ui_duration_format.py runs these in node.

/** Human duration for a millisecond span. ``''`` for anything that is not one. */
function fmtDur(ms) {
 if (typeof ms !== 'number' || !isFinite(ms) || ms < 0) return '';
 const total = Math.floor(ms / 1000);
 // Sub-second reads as '<1s'. A step that ran is not a step that took no time,
 // and a bare '0s' next to a COMPLETED badge looks like a missing value.
 if (total < 1) return '<1s';
 if (total < 60) return total + 's';
 const mins = Math.floor(total / 60), secs = total % 60;
 // The seconds stay even at a round minute ('5m 0s'), so a column of these
 // keeps one shape and the digits line up under tabular-nums.
 if (mins < 60) return mins + 'm ' + secs + 's';
 // Past an hour the seconds stop being information, so they give way to hours.
 // A pipeline that has been parked at a gate overnight is the case here.
 return Math.floor(mins / 60) + 'h ' + (mins % 60) + 'm';
}

/** Milliseconds between two ISO stamps. An empty ``to`` means "until now". */
function spanMs(from, to, now) {
 if (!from) return NaN;
 const start = new Date(from).getTime();
 if (isNaN(start)) return NaN;
 if (to) {
  const end = new Date(to).getTime();
  if (isNaN(end)) return NaN;
  // NOT clamped. Both stamps come from the server's own clock, so one before
  // the other is not skew -- it is contradictory data, and there is no honest
  // duration to show. NaN blanks the chip.
  //
  // Clamping here hid a real bug: a retried step kept the finish time of the
  // attempt that failed, so its new start was *after* its recorded finish, and
  // every retried step displayed "<1s". A blank would have been read as
  // missing data and investigated; "<1s" read as a fast step.
  return end < start ? NaN : end - start;
 }
 // Clamped only against the browser's clock, which is the one case where a
 // small negative is expected rather than wrong: the server stamps started_at
 // in UTC from its clock, and a browser a few seconds behind would otherwise
 // blank the live counter for the first seconds of every run.
 const end = typeof now === 'number' ? now : Date.now();
 return Math.max(0, end - start);
}

/** ``spanMs`` formatted. ``''`` when there is nothing honest to show. */
function fmtSpan(from, to, now) { return fmtDur(spanMs(from, to, now)); }

// Browser: these are globals on a classic script. Node: the test requires them.
if (typeof module !== 'undefined' && module.exports) module.exports = {fmtDur, spanMs, fmtSpan};
