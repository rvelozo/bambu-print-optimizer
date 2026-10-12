import unittest

from slicer_optimizer.metrics import stiffness_proxy


def layer(i, z, vol, bbox=(0, 0, 100, 100), h=0.2):
    return {"index": i, "z": z, "h": h, "volume_mm3": vol, "ext_count": 1, "bbox": list(bbox)}


def plate(fractions, solid=2000.0, extra=()):
    layers = [{"index": 0, "z": 0, "h": 0, "volume_mm3": 0, "ext_count": 0, "bbox": None}]
    for i, f in enumerate(fractions, start=1):
        layers.append(layer(i, round(0.2 * i, 3), solid * f))
    for j, (vol, bbox) in enumerate(extra, start=len(fractions) + 1):
        layers.append(layer(j, round(0.2 * j, 3), vol, bbox))
    return {"layers": layers}


class StiffnessProxyTest(unittest.TestCase):
    def test_solid_plate_is_100(self):
        self.assertAlmostEqual(stiffness_proxy(plate([1] * 10)), 1.0, places=6)

    def test_sandwich_matches_beam_theory(self):
        # 2 mm plate, 0.6 mm skins, 0.8 mm core at 0 %: I = 2*(0.6^3/12 + 0.6*0.7^2) / (2^3/12)
        got = stiffness_proxy(plate([1, 1, 1, 0, 0, 0, 0, 1, 1, 1]))
        expected = 2 * (0.6 ** 3 / 12 + 0.6 * 0.7 ** 2) / (2 ** 3 / 12)
        self.assertAlmostEqual(got, expected, places=6)

    def test_thick_bridge_layer_does_not_shrink_the_reference(self):
        # v7 lid: the bridge layer holds ~1.4x a solid layer's plastic
        base = stiffness_proxy(plate([1, 1, 1, 0.14, 0.14, 0.14, 1, 1, 1, 1, 1]))
        bridged = stiffness_proxy(plate([1, 1, 1, 0.14, 0.14, 0.14, 1.4, 1, 1, 1, 1]))
        self.assertAlmostEqual(base, bridged, places=6)
        self.assertGreater(bridged, 0.9)

    def test_a_tall_rib_is_not_part_of_the_slab(self):
        # v7 lid: a 3 mm wide bar above a 100 x 100 plate
        rib = [(30.0, (0, 0, 100, 3))] * 15
        self.assertAlmostEqual(stiffness_proxy(plate([1] * 10, extra=rib)), 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
