"""Runs the nightly jobs on the server (its own container).

  futures + cash bids  6:10 PM Central, Monday to Friday (after JSA's evening futures load)
  weather              5:30 AM Central, every day

Run one job now:  python -m app.jobs.scheduler futures|bids|weather
"""
import logging
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .. import config, store
from . import bids, futures, weather

log = logging.getLogger('grain.jobs')
JOBS = {'futures': futures.run, 'bids': bids.run, 'weather': weather.run}
SCHEDULE = [  # (job, hour, minute, weekdays 0=Mon)
    ('futures', 18, 10, range(0, 5)),
    ('bids', 18, 15, range(0, 5)),
    ('weather', 5, 30, range(0, 7)),
]


def run_job(name):
    try:
        log.info('%s: %s', name, JOBS[name]().replace('\n', ' | '))
    except Exception:
        log.exception('%s failed', name)


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    store.init()
    if len(sys.argv) > 1:
        run_job(sys.argv[1])
        return
    tz, done = ZoneInfo(config.TIMEZONE), set()
    log.info('scheduler started (%s)', config.TIMEZONE)
    while True:
        now = datetime.now(tz)
        for name, h, m, days in SCHEDULE:
            tag = (name, now.date())
            if now.weekday() in days and (now.hour, now.minute) >= (h, m) and tag not in done:
                # catch up once if the server was down at the scheduled minute, but not hours later
                if (now.hour * 60 + now.minute) - (h * 60 + m) <= 120:
                    run_job(name)
                done.add(tag)
        done = {t for t in done if t[1] == now.date()}
        time.sleep(30)


if __name__ == '__main__':
    main()
