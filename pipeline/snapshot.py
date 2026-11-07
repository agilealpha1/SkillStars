#!/usr/bin/env python3
"""Step 4: append this week's star counts to data/history/stars.jsonl.

  python3 pipeline/snapshot.py

One line per ISO week: {"week": "2026-W45", "date": "2026-11-07", "stars": {"<repo id>": 12345, ...}}.
Re-running in the same week replaces that week's line, so the file stays one-snapshot-per-week however often
the pipeline runs. Repo ids (not names) are the keys, so renames keep their history. All candidates are recorded,
in scope or not, so a later scope decision doesn't lose history.
"""
import argparse, datetime as dt, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, log, read_jsonl, write_jsonl

HISTORY = os.path.join(DATA, 'history', 'stars.jsonl')


def iso_week(d):
    y, w, _ = d.isocalendar()
    return f'{y}-W{w:02d}'


def add_snapshot(history, repos, today):
    """Return history with today's snapshot added (or replacing the same ISO week's)."""
    snap = {'week': iso_week(today), 'date': today.isoformat(), 'stars': {str(r['id']): r['stars'] for r in repos}}
    rows = [h for h in history if h['week'] != snap['week']] + [snap]
    return sorted(rows, key=lambda h: h['date'])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--repos', default=os.path.join(DATA, 'repos.jsonl'))
    ap.add_argument('--history', default=HISTORY)
    ap.add_argument('--date', help='YYYY-MM-DD (default: today, UTC)')
    a = ap.parse_args(argv)
    repos = read_jsonl(a.repos) or sys.exit('no repos; run enrich.py first')
    today = dt.date.fromisoformat(a.date) if a.date else dt.datetime.now(dt.timezone.utc).date()
    rows = add_snapshot(read_jsonl(a.history), repos, today)
    write_jsonl(a.history, rows)
    log(f'snapshot {iso_week(today)}: {len(repos)} repos; {len(rows)} weekly snapshots in {os.path.relpath(a.history)}')


if __name__ == '__main__':
    main()
