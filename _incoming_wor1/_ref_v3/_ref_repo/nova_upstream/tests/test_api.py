import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


def create_game(client: TestClient, name: str = "Test Player") -> dict:
    response = client.post("/games", json={"player_name": name})
    assert response.status_code == 201
    return response.json()


def test_health_endpoint(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_create_game_returns_initial_state(client: TestClient):
    game = create_game(client, "  Alex  ")
    assert game["player_name"] == "Alex"
    assert game["resources"] == {"water": 100, "oxygen": 80, "power": 60}
    assert game["inventory"] == {"scanner": 1, "wrench": 1, "repair_kit": 1}
    assert game["missions"]["water_leak"]["current_objective"] == "scan_leak"


def test_create_game_rejects_blank_name_and_extra_fields(client: TestClient):
    blank_response = client.post("/games", json={"player_name": "   "})
    extra_response = client.post(
        "/games",
        json={"player_name": "Alex", "admin": True},
    )
    assert blank_response.status_code == 422
    assert extra_response.status_code == 422


def test_game_not_found_returns_404(client: TestClient):
    response = client.get("/games/not-a-real-id/state")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_player_sessions_are_independent(client: TestClient):
    first = create_game(client, "Player A")
    second = create_game(client, "Player B")

    action = client.post(
        f"/games/{first['game_id']}/missions/water-leak/actions",
        json={"action": "scan_leak"},
    )
    assert action.status_code == 200

    first_resources = client.get(f"/games/{first['game_id']}/resources").json()
    second_resources = client.get(f"/games/{second['game_id']}/resources").json()
    assert first_resources["power"] == 58
    assert second_resources["power"] == 60


def test_invalid_order_returns_409_without_state_change(client: TestClient):
    game = create_game(client)
    game_id = game["game_id"]

    response = client.post(
        f"/games/{game_id}/missions/water-leak/actions",
        json={"action": "close_valve"},
    )
    assert response.status_code == 409

    state = client.get(f"/games/{game_id}/state").json()
    assert state["resources"]["power"] == 60
    assert state["missions"]["water_leak"]["valve_closed"] is False


def test_unknown_action_returns_422(client: TestClient):
    game = create_game(client)
    response = client.post(
        f"/games/{game['game_id']}/missions/water-leak/actions",
        json={"action": "fly_to_mars"},
    )
    assert response.status_code == 422


def test_complete_water_leak_mission(client: TestClient):
    game = create_game(client)
    game_id = game["game_id"]
    endpoint = f"/games/{game_id}/missions/water-leak/actions"

    for action in ("scan_leak", "close_valve", "repair_pipe"):
        response = client.post(endpoint, json={"action": action})
        assert response.status_code == 200, response.text

    final_state = client.get(f"/games/{game_id}/state").json()
    mission = final_state["missions"]["water_leak"]

    assert final_state["resources"]["power"] == 53
    assert final_state["inventory"]["repair_kit"] == 0
    assert mission["status"] == "completed"
    assert mission["leak_active"] is False

    after_completion = client.post(endpoint, json={"action": "scan_leak"})
    assert after_completion.status_code == 409
