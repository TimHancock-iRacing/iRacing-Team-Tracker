# Corrected CSV Export

This fixes the problem you called out:

- uses the original uploaded CSV again
- keeps it inside a `samples/` folder
- does **not** roll/loop by default
- stops at end-of-race so the replay remains representative of one whole race

## Included
- `tracker_publisher.py`
- `relay_server.py`
- `config.example.json`
- `samples/0084229273_2026-03-14 21h15 UTC_porsche992rgt3-Race_RACE@bathurst.csv`
- `samples/bathurst_session.csv`
- `requirements.txt`
- `README.md`

## Important behaviour change
The CSV replay reader now has:
- `loop: false` by default
- end-of-file stop behaviour
- progress metadata in state

## Config
The publisher is set to use:
`samples/0084229273_2026-03-14 21h15 UTC_porsche992rgt3-Race_RACE@bathurst.csv`

You can switch to the shorter alias if you want:
`samples/bathurst_session.csv`

## Run
1. Start relay
2. Start publisher
3. The replay will walk the race once and stop at the end

## Why the earlier version felt wrong
It reused a simplified sample and looped, which made the session feel like a rolling slice rather than a whole-race replay.
This corrected pack restores the original-race behaviour.
