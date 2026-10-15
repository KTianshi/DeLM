import unittest
from experiments.math_harness.run_queue import interleave

class QueueTests(unittest.TestCase):
    def test_order_balances_variants_without_dropping_repeats(self):
        manifests=[{'runs':[{'repeat':i} for i in (1,2,3)]} for _ in range(2)]
        self.assertEqual([(j,r['repeat']) for j,r in interleave(manifests)],[(0,1),(1,1),(1,2),(0,2),(0,3),(1,3)])
