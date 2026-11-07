#!/usr/bin/env python3
"""Steps 4+5 in one go: record this week's star snapshot, then build site_dist/.

  python3 pipeline/build.py                 # snapshot + build
  python3 pipeline/build.py --no-snapshot   # rebuild the site from existing data only (what a push deploy does)
"""
import argparse, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_site_data, snapshot

ap = argparse.ArgumentParser()
ap.add_argument('--no-snapshot', action='store_true')
ap.add_argument('--out', default=build_site_data.OUT)
a = ap.parse_args()
if not a.no_snapshot:
    snapshot.main([])
build_site_data.main(['--out', a.out])
