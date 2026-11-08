import unittest
from unittest.mock import Mock
from robot_arm.kitchen.platform import KitchenPlatform


class WashDurationTests(unittest.TestCase):
    def test_two_seconds_are_required_and_only_with_water_and_supported_fruit(self):
        p=KitchenPlatform(layout='kitchen_v3_norice_candidate_013',seed=0)
        try:
            dev=p.controller.devices
            # Isolate the timer from motion; other device inputs remain zero.
            dev.angle=Mock(side_effect=lambda name:1. if name=='faucet_switch' else 0.)
            dev.in_basket=Mock(side_effect=lambda name:name=='pear')
            dev.held=Mock(return_value=False)
            for _ in range(49):dev.update(.04)
            self.assertNotIn('pear',dev.cleaned)
            dev.update(.04);self.assertIn('pear',dev.cleaned)
            self.assertNotIn('apple',dev.cleaned)
            dev.cleaned.clear();dev.clean_seconds['pear']=0.
            dev.angle=Mock(return_value=0.)
            for _ in range(60):dev.update(.04)
            self.assertEqual(dev.clean_seconds['pear'],0.)
            dev.angle=Mock(side_effect=lambda name:1. if name=='faucet_switch' else 0.)
            dev.held=Mock(return_value=True)
            for _ in range(60):dev.update(.04)
            self.assertEqual(dev.clean_seconds['pear'],0.)
        finally:p.close()
