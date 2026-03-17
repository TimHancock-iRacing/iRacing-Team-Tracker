# CSV-focused Pit Logic Test Pack

Why burn stayed at 2.6 before:
- the prior pack stayed on fallback burn when it did not see valid lap-based samples
- in mock mode, burn could remain fixed unless the model was fed changing per-lap data
- your session file already provides better fields, so this pack now prefers them

This pack maximises use of reported session CSV fields:
- `L/lap` -> last-lap burn
- `Tank (L)` -> current fuel
- `Average L/lap` -> stint/reported average burn
- `Laps Rem.` -> reported laps remaining
- `Fuel to add` -> reported comparison value
- `PIT` -> pit markers
- `Trk temp.` -> tyre heatmap influence

Modes:
- `session_csv` (default in config) -> replays the supplied session file
- `mock` -> synthetic test mode

Files:
- relay_server.py
- tracker_publisher.py
- config.example.json
- sample_session.csv
- requirements.txt
- README.md

## Test
1. Replace your files with these
2. Run the publisher:
   py tracker_publisher.py --config config.example.json
3. Open the relay dashboard
4. Watch burn move from reported/recalculated lap to lap instead of staying fixed at 2.6

This pack is for testing against the session CSV you described.
