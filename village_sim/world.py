"""World geometry and A* pathing. No rendering or network dependencies."""

from __future__ import annotations

import heapq
import json
from pathlib import Path

Point = tuple[int, int]


def inside(point: Point, polygon: list[list[int]]) -> bool:
    x, y = point
    hit = False
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        if (a[1] > y) != (b[1] > y):
            cross = (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]
            if x < cross:
                hit = not hit
    return hit


class World:
    def __init__(self, path: str | Path | None = None):
        self.config = json.loads(
            Path(path or Path(__file__).parent / "data/world.json").read_text()
        )
        self.places = {p["id"]: p for p in self.config["places"]}
        self.walkable = {
            (x, y)
            for y in range(self.config["height"])
            for x in range(self.config["width"])
            if any(inside((x, y), p) for p in self.config["walkable_polygons"])
            and not any(
                a <= x <= c and b <= y <= d
                for a, b, c, d in self.config["blocked_rectangles"]
            )
        }
        for p in self.places.values():
            if (p["x"], p["y"]) not in self.walkable:
                raise ValueError(f"Place {p['id']} is not walkable")

    def point(self, place: str) -> Point:
        p = self.places[place]
        return p["x"], p["y"]

    def path(self, start: Point, goal: Point) -> list[Point]:
        if start not in self.walkable or goal not in self.walkable:
            return []
        frontier = [(0, start)]
        parent: dict[Point, Point | None] = {start: None}
        cost = {start: 0}
        while frontier:
            _, current = heapq.heappop(frontier)
            if current == goal:
                path = []
                while current != start:
                    path.append(current)
                    current = parent[current]
                return path[::-1]
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nxt = current[0] + dx, current[1] + dy
                new_cost = cost[current] + 1
                if nxt in self.walkable and new_cost < cost.get(nxt, float("inf")):
                    cost[nxt] = new_cost
                    parent[nxt] = current
                    priority = new_cost + abs(nxt[0] - goal[0]) + abs(nxt[1] - goal[1])
                    heapq.heappush(frontier, (priority, nxt))
        return []
