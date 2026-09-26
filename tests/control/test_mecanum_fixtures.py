import json
from pathlib import Path
import unittest

from rescuebot.mecanum import mix_mecanum


FIXTURE_PATH = Path(__file__).parents[2] / "fixtures" / "mecanum_vectors.json"


class MecanumFixtureTests(unittest.TestCase):
    def test_shared_vectors_match_the_python_reference(self) -> None:
        vectors = json.loads(FIXTURE_PATH.read_text())
        for vector in vectors:
            with self.subTest(vector=vector["name"]):
                wheels = mix_mecanum(
                    vector["forward"],
                    vector["sideways"],
                    vector["turn"],
                    vector["speed_limit"],
                )
                self.assertEqual(wheels.as_dict(), vector["expected"])
