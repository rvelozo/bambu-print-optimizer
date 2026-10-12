import unittest

from slicer_optimizer.bambu import SliceResult


def res(total, grams=100.0, noise=None, samples=1, **features):
    return SliceResult(plate=3, total_s=total, model_s=total - 400, grams=grams,
                       feature_s=features or {"Outer wall": total / 2}, objects=[], sha256="x",
                       wall_s=1, samples=samples, noise_s=noise)


class NoiseTest(unittest.TestCase):
    def test_tolerance_follows_measured_noise(self):
        self.assertEqual(res(8671).tolerance_s, 0.5)  # one sample: assume exact
        self.assertEqual(res(8671, noise=0, samples=2).tolerance_s, 0.5)  # repeat was identical
        panel = res(14240, noise=13.79, samples=4)
        self.assertAlmostEqual(panel.tolerance_s, 41.37)
        self.assertAlmostEqual(res(14240, noise=2, samples=2).tolerance_s, 28.48)  # floor: 0.2 %

    def test_same_as_uses_the_noisier_side(self):
        noisy = res(14240, noise=13.79, samples=4)
        self.assertTrue(noisy.same_as(res(14260, **{"Outer wall": 7130})))
        self.assertFalse(noisy.same_as(res(14300, **{"Outer wall": 7150})))
        self.assertFalse(noisy.same_as(res(14240, grams=100.02, **{"Outer wall": 7120})))
        exact = res(8671, noise=0, samples=2, **{"Bridge": 10})
        self.assertFalse(exact.same_as(res(8672, **{"Bridge": 11})))
        self.assertTrue(exact.same_as(res(8671.2, **{"Bridge": 10.1})))


if __name__ == "__main__":
    unittest.main()
