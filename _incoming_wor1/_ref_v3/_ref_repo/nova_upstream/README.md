# NOVA Gameplay Backend — Prototype 01

A small FastAPI backend prototype for NOVA. It manages in-memory game sessions, player resources, inventory, and the Earth Analog water-leak mission.

## Requirements

- Python 3.10 or newer
- `uv` or `pip`

## Install with uv (Windows PowerShell)

Open a terminal in the project root (`Nova_FastAPI`) and run:

```powershell
uv venv
.\.venv\Scripts\Activate.ps1
uv pip install -r requirements.txt
```

If PowerShell blocks activation, you can run the environment's Python directly instead:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

## Run the API

From the project root:

```powershell
uvicorn app.main:app --reload
```

The API will be available at `http://127.0.0.1:8000`.

- Interactive Swagger docs: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`
- Health check: `http://127.0.0.1:8000/health`

## CORS for the React/Vite frontend

By default, the backend allows these local Vite origins:

- `http://localhost:5173`
- `http://127.0.0.1:5173`

To use a different frontend origin, set `FRONTEND_ORIGINS` before starting the API. Separate multiple origins with commas:

```powershell
$env:FRONTEND_ORIGINS = "http://localhost:5173,http://localhost:3000"
uvicorn app.main:app --reload
```

For a deployed frontend, add its exact origin. Do not use `*` as a shortcut when credentials are involved.

## API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | Check that the API is running |
| `POST` | `/games` | Create a game session |
| `GET` | `/games/{game_id}/state` | Get the complete game state |
| `GET` | `/games/{game_id}/resources` | Get player resources |
| `GET` | `/games/{game_id}/inventory` | Get player inventory |
| `GET` | `/games/{game_id}/missions/water-leak` | Get water-leak mission state |
| `POST` | `/games/{game_id}/missions/water-leak/actions` | Execute a mission action |

### Create a game

`POST /games`

Request body:

```json
{
  "player_name": "Player A"
}
```

The response contains the new `game_id` and the initial game state. Save that ID in the frontend and use it in later requests.

### Execute a mission action

`POST /games/{game_id}/missions/water-leak/actions`

Request body examples:

```json
{
  "action": "scan_leak"
}
```

Allowed actions, in order:

1. `scan_leak` — requires a scanner and consumes 2 power.
2. `close_valve` — requires the leak to have been detected and a wrench.
3. `repair_pipe` — requires the valve to be closed, a repair kit, and 5 power. It consumes one repair kit and stops the leak.

Successful action responses include the updated resources, inventory, and mission state so the frontend can update its HUD without making extra requests.

## Error responses

| Status | Meaning |
|---|---|
| `404` | The requested `game_id` does not exist in this running process |
| `409` | The action conflicts with the current mission state or available resources/items |
| `422` | The request body or path input failed validation |

Errors use FastAPI's JSON `detail` format.

## Run tests

Install the dependencies (the requirements already include `pytest` and `httpx`), then run from the project root:

```powershell
python -m pytest -q
```

The test suite covers resource and inventory validation, session isolation, API validation, mission action ordering, and successful mission completion.

## Project layout

```text
Nova_FastAPI/
├── app/
│   ├── api/
│   │   ├── games.py
│   │   ├── inventory.py
│   │   ├── missions.py
│   │   └── resources.py
│   ├── missions/
│   │   └── water_leak_mission.py
│   ├── schemas/
│   │   ├── game.py
│   │   └── mission.py
│   ├── services/
│   │   ├── exceptions.py
│   │   ├── game_service.py
│   │   └── game_session.py
│   ├── systems/
│   │   ├── inventory_system.py
│   │   └── resource_manager.py
│   ├── dependencies.py
│   └── main.py
├── tests/
│   ├── test_api.py
│   └── test_core_systems.py
└── requirements.txt
```

## Prototype limitations

- Sessions are stored in server memory. Restarting the API clears all sessions.
- This prototype does not include accounts, authentication, database persistence, multiplayer networking, or the 3D client.
- The frontend owns movement, camera, animations, and 3D interaction. This API owns mission rules, resources, inventory, and mission state.
- The backend currently keeps one in-memory service instance per running app process. Do not run multiple worker processes until session storage is moved to a shared database or cache.
