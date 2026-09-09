import random
import unittest

from app import bait_schedule


class BaitScheduleTests(unittest.TestCase):
    def test_empty_pool_is_all_skips(self):
        self.assertEqual(bait_schedule([], 4, random.Random(1)), [None] * 4)

    def test_pool_is_varied_and_contains_skips(self):
        hooks = [{'name': name} for name in ('a', 'b', 'c')]
        schedule = bait_schedule(hooks, 50, random.Random(4))
        self.assertEqual(len(schedule), 50)
        self.assertIn(None, schedule)
        self.assertTrue(all(hook in schedule for hook in hooks))
        for left, right in zip(schedule, schedule[1:]):
            self.assertFalse(left is not None and left is right)


if __name__ == '__main__':
    unittest.main()
