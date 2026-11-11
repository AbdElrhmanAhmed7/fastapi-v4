"""Shared enums used by the domain, the API and the frontend contract."""

from __future__ import annotations

from enum import Enum


class Difficulty(str, Enum):
    cadet = "cadet"
    standard = "standard"
    veteran = "veteran"


class MissionStatus(str, Enum):
    locked = "locked"
    available = "available"
    active = "active"
    completed = "completed"
    failed = "failed"


class Environment(str, Enum):
    training_facility = "training_facility"
    earth_analog = "earth_analog"
    mars_surface = "mars_surface"
    medical_bay = "medical_bay"
    orbital_station = "orbital_station"
    mars_lab = "mars_lab"
    simulation_center = "simulation_center"
    earth_observation = "earth_observation"


class Activity(str, Enum):
    """Movement state reported by the frontend, used by the oxygen model."""

    idle = "idle"
    walk = "walk"
    run = "run"
    eva = "eva"
    rover = "rover"


class ItemCategory(str, Enum):
    tool = "tool"
    consumable = "consumable"
    protection = "protection"
    component = "component"
    sample = "sample"
    data = "data"


class SimulationKind(str, Enum):
    lunar_lander = "lunar_lander"
    microgravity_fire = "microgravity_fire"
    crew_vitals = "crew_vitals"
    rover_traverse = "rover_traverse"
    earth_trends = "earth_trends"


class SimulationStatus(str, Enum):
    ready = "ready"
    running = "running"
    paused = "paused"
    succeeded = "succeeded"
    failed = "failed"


class Grade(str, Enum):
    S = "S"
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class DataProvenance(str, Enum):
    """Every scientific payload states where it came from."""

    live = "live"          # fetched from a NASA endpoint just now
    cache = "cache"        # served from the backend cache
    bundled = "bundled"    # curated dataset shipped with the repo
    simulated = "simulated"  # deterministic educational simulation


SIMULATION_DISCLAIMER = (
    "Educational game simulation. Values are simplified for gameplay and must "
    "never be used as real medical, engineering or mission-planning guidance."
)
