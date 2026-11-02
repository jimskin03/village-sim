"""The canvas village is a manifest of real files on the simulation grid."""

import json
import struct
import unittest
from pathlib import Path

from village_sim.simulation import PERSONAS

ROOT = Path(__file__).parents[1]
ASSETS = ROOT / "village_sim" / "static" / "assets"
ACTIONS = ("idle", "walk", "carry", "collapsed")
DIRECTIONS = ("n", "e", "s", "w")


def png_size(path):
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError(f"{path} is not a PNG")
    return struct.unpack(">II", data[16:24])


class AssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((ASSETS / "manifest.json").read_text())
        cls.world = json.loads((ROOT / "village_sim" / "data" / "world.json").read_text())

    def test_every_place_has_a_building_on_the_same_grid(self):
        places = {place["id"] for place in self.world["places"]}
        buildings = {item["id"] for item in self.manifest["buildings"]}
        self.assertEqual(buildings, places)
        self.assertEqual(self.manifest["view"], "three-quarter")
        self.assertEqual(self.manifest["tile_size"], self.world["tile_size"])
        self.assertEqual(
            len(self.manifest["terrain"]),
            self.world["width"] * self.world["height"],
        )
        self.assertEqual(len(self.manifest["foam"]), len(self.manifest["terrain"]))
        jetty = next(place for place in self.world["places"] if place["id"] == "jetty")
        index = jetty["y"] * self.world["width"] + jetty["x"]
        self.assertEqual(self.manifest["terrain"][index], "pier")
        self.assertEqual(self.manifest["foam"][0], 0)

    def test_shoreline_foam_faces_land(self):
        width = self.manifest["map"]["width"]
        height = self.manifest["map"]["height"]
        terrain = self.manifest["terrain"]
        foam = self.manifest["foam"]
        found = False
        for index, tile in enumerate(terrain):
            if tile != "water" or not foam[index]:
                continue
            x, y = index % width, index // width
            neighbors = []
            for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < width and 0 <= ny < height:
                    neighbors.append(terrain[ny * width + nx])
            self.assertTrue(any(name != "water" for name in neighbors))
            found = True
        self.assertTrue(found)

    def test_declared_files_exist_at_their_declared_size(self):
        seen = set()

        def check(file, size):
            path = ASSETS / file
            self.assertTrue(path.is_file(), file)
            self.assertEqual(png_size(path), tuple(size))
            seen.add(file)

        for name, file in self.manifest["tiles"].items():
            check(file, (32, 32))
            self.assertTrue(name)
        for item in self.manifest["buildings"] + self.manifest["resources"]:
            self.assertIn("anchor", item)
            self.assertIsInstance(item["blocked"], list)
            check(item["file"], item["size"])
            for file in item["states"].values():
                check(file, item["size"])
        for item in self.manifest["scenery"]:
            check(item["file"], item["size"])
        for pid, person in self.manifest["characters"].items():
            for action, views in person["actions"].items():
                self.assertEqual(set(views), set(DIRECTIONS))
                for file in views.values():
                    check(file, person["size"])
            self.assertTrue(pid)
        self.assertEqual(seen, {path.relative_to(ASSETS).as_posix()
                                for path in ASSETS.rglob("*.png")})

    def test_villagers_have_walk_carry_and_collapsed_poses(self):
        ids = {row[0] for row in PERSONAS} | {"visitor"}
        self.assertEqual(set(self.manifest["characters"]), ids)
        for person in self.manifest["characters"].values():
            self.assertEqual(set(person["actions"]), set(ACTIONS))
        supplies = {item["id"]: set(item["states"]) for item in self.manifest["resources"]}
        self.assertEqual(set(supplies), {"fish", "herbs", "water", "pantry"})
        for states in supplies.values():
            self.assertEqual(states, {"full", "half", "empty"})
        buildings = {item["id"]: set(item["states"]) for item in self.manifest["buildings"]}
        self.assertEqual(buildings["fire"], {"lit", "resting"})
        self.assertEqual(buildings["shore"], {"sound", "leaking"})
        self.assertEqual(buildings["nets"], {"sound", "torn"})
        self.assertEqual(buildings["well"], {"ready", "empty", "fouled"})
        self.assertEqual(buildings["rain_barrel"], {"ready", "empty", "fouled"})

    def test_coast_keeps_open_water_and_a_varied_beach(self):
        width, height = self.world["width"], self.world["height"]
        terrain = self.manifest["terrain"]
        solid = sum(tile != "water" for tile in terrain)
        self.assertEqual(solid, 645)
        self.assertEqual(solid, round(0.701 * width * height))
        for x in range(width):
            self.assertEqual(terrain[x], "water")
            self.assertEqual(terrain[(height - 1) * width + x], "water")
        for y in range(height):
            self.assertEqual(terrain[y * width], "water")
            self.assertEqual(terrain[y * width + width - 1], "water")

        def tile(x, y):
            return terrain[y * width + x]

        for point in ((20, 19), (21, 20), (34, 13), (36, 12)):
            self.assertEqual(tile(*point), "water", point)
        places = {place["id"]: place for place in self.world["places"]}
        for name in ("garden", "west_garden", "orchard"):
            self.assertLess(places[name]["x"], 12)
        sand = {"sand", "sand_b"}

        def is_sand(x, y):
            return 0 <= x < width and 0 <= y < height and tile(x, y) in sand

        def is_water(x, y):
            return 0 <= x < width and 0 <= y < height and tile(x, y) == "water"

        one = two = False
        for y in range(height):
            for x in range(width):
                if not is_sand(x, y):
                    continue
                for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
                    if not is_water(x + dx, y + dy):
                        continue
                    if is_sand(x - dx, y - dy):
                        two = True
                    else:
                        one = True
        self.assertTrue(one and two)
