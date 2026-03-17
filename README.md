# iRacing Team Tracker v2

This pack adds both requested upgrades:

1. Strategy v2
   - tank-cap-aware stop planning
   - next-stop fuel and final-stop fuel
   - 4-tyre time delta vs fuel-only
   - 'tyres covered by fuel' output
   - fuel source confidence

2. Multi-driver sync
   - each installed client has its own client_id
   - relay accepts updates from multiple clients
   - relay picks the active source automatically
   - dashboard shows connected clients and active publisher

Files:
- relay_server_v2.py
- tracker_publisher_v2.py
- config.v2.example.json
- requirements.txt
- README.md

Railway start command:
python relay_server_v2.py --host 0.0.0.0 --port $PORT
