# iRacing Team Tracker — Dashboard v3 Spec

## Design intent
A race-engineering dashboard should answer, in order:

1. Who is in the car, and is the telemetry trustworthy?
2. When do we pit?
3. What service do we take?
4. What is the race context around us?
5. Where is everyone on track?

This layout prioritises decision-making over raw data density.

## Layout

### 1. Header strip
Compact top bar only:
- session ID / event
- current driver
- current lap
- laps remaining
- fuel source
- live / stale / disconnected status

### 2. Primary left panel — Strategy
Largest module on screen.

Show:
- next stop lap
- laps left in tank
- stops remaining
- fuel next stop
- fuel final stop
- 4-tyre delta
- tyres covered by fuel
- pit loss average

### 3. Primary right panel — Track map placeholder
Keep the panel clean for future live map work.
For now it should show:
- a placeholder card
- current lap
- next stop
- pit loss / tyre delta callout
- future note that this panel will host live track map and nearby-car context

### 4. Bottom left — Nearby race context
Do not show full-field timing by default.
Show:
- position
- class position
- gap ahead
- gap behind
- best / last / avg pace if available later
- status indicators when richer data is added

### 5. Bottom right — Stint and fuel trust
Show:
- stint laps
- last stop lap
- last fill added
- current burn
- rolling burn source
- fuel source confidence
- active publisher / client
- connected client count

## Visual hierarchy

### Primary
Large numbers:
- next stop
- fuel now
- laps left
- stops remaining

### Secondary
Medium labels:
- tyre delta
- fuel next stop
- pit loss average
- current driver

### Tertiary
Small labels:
- source confidence
- session metadata
- notes / future expansion

## UI rules
- dark background
- minimal colour use
- reserve bright colours for status only
- no dense rainbow metric grid
- avoid duplicated numbers
- prefer glanceable tiles over spreadsheet-like clutter

## What changed from previous version
- removes oversized raw-state emphasis
- strategy gets dominant visual weight
- connected clients remain visible but compact
- nearby race context becomes a focused panel, not a full tower
- track map has a reserved panel so the layout will scale cleanly later
