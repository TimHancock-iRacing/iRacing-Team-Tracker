# Pit Logic v1 Test Pack

This pack adds:
- lap-by-lap burn recalculation
- rolling/stint/fallback burn model
- live tank-range recalculation
- pit recommendation v1
- 4-tyre delta vs fuel-only
- tyre heatmap panel
- same filenames for easier drop-in replacement

Files:
- relay_server.py
- tracker_publisher.py
- config.example.json
- requirements.txt
- README.md

## Test locally
1. Replace your local files with these.
2. Run the publisher in mock mode:
   py tracker_publisher.py --config config.example.json
3. Watch:
   - burn model update lap by lap
   - next stop / fuel next stop recalc
   - tyre heatmap degrade then reset on pit laps

## Railway
Keep start command:
python relay_server.py --host 0.0.0.0 --port $PORT
