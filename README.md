# iRacing Team Tracker — Full Export Pack

This pack consolidates the work so far into the same filenames for easier replacement.

Included:
- relay_server.py
- tracker_publisher.py
- config.example.json
- sample_session.csv
- requirements.txt
- README.md

## What is included
- strategy dashboard
- lap-by-lap burn model
- effective tank capacity learning from trusted fuel snapshots
- time-limited and lap-limited race model support
- projected total laps and projection delta
- trusted tyres only
- pit recommendation v1
- manual regime override buttons on dashboard
- session CSV replay mode
- mock mode

## Important limitations in this export
- live iRacing SDK path is not fully merged into this final pack yet
- dashboard override buttons are currently display-side only and do not round-trip back into publisher strategy math
- tyre wear is only shown when trusted data exists; sample CSV mode has no trusted tyre snapshots

## Modes
### session_csv
Uses the supplied sample_session.csv and makes heavy use of the reported fields:
- Tank (L)
- L/lap
- Average L/lap
- Laps Rem.
- Fuel to add
- PIT
- Trk temp.

### mock
Simulates:
- green start projection
- later wet pace
- trusted tyre snapshots only at pit stops
- effective tank learning

## Quick local test
1. Install requirements:
   py -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt

2. Run relay:
   py relay_server.py --host 0.0.0.0 --port 8000

3. Run publisher:
   py tracker_publisher.py --config config.example.json

4. Open dashboard:
   http://127.0.0.1:8000/session/osr-b12-2026

## Railway
Start command remains:
python relay_server.py --host 0.0.0.0 --port $PORT

## Recommended next step after testing this export
- merge the live iRacing path into the same strategy engine
- round-trip dashboard overrides back to the publisher
- add real trusted tyre snapshot capture from the active driver PC
