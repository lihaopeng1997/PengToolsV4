"""Native hero animation visibility and reduced-motion behavior."""
import unittest

from PyQt6.QtCore import QAbstractAnimation
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QWidget

from ui.motion import set_motion_enabled_for_test


class NativeHeroMotionTests(unittest.TestCase):
    def setUp(self):
        from panels.dashboard_panel import PrismOrbWidget
        self.app = QApplication.instance() or QApplication(['native-hero-test'])
        set_motion_enabled_for_test(True)
        self.addCleanup(set_motion_enabled_for_test, None)
        self.host = QWidget()
        self.host.resize(160, 160)
        self.orb = PrismOrbWidget(self.host)

    def tearDown(self):
        self.host.close()
        self.host.deleteLater()
        self.app.processEvents()

    def test_hidden_construction_and_visibility_resume_one_animation(self):
        animation = self.orb._anim
        self.assertNotEqual(animation.state(), QAbstractAnimation.State.Running)
        self.host.show()
        self.app.processEvents()
        self.assertEqual(animation.state(), QAbstractAnimation.State.Running)
        animation.setCurrentTime(200)
        first = self.orb.grab().toImage()
        animation.setCurrentTime(1400)
        self.assertNotEqual(first, self.orb.grab().toImage())
        self.host.hide()
        self.app.processEvents()
        self.assertEqual(animation.state(), QAbstractAnimation.State.Paused)
        paused_time = animation.currentTime()
        QTest.qWait(60)
        self.assertEqual(animation.currentTime(), paused_time)
        self.host.show()
        self.app.processEvents()
        self.assertIs(self.orb._anim, animation)
        self.assertEqual(animation.state(), QAbstractAnimation.State.Running)

    def test_reduced_motion_pauses_visible_orb_without_geometry_changes(self):
        self.host.show()
        self.app.processEvents()
        self.orb._anim.setCurrentTime(1000)
        geometry = self.orb.geometry()
        state = (self.orb._offset_y, self.orb._angle, self.orb._scale)
        set_motion_enabled_for_test(False)
        QTest.qWait(80)
        self.assertEqual(self.orb._anim.state(), QAbstractAnimation.State.Paused)
        self.assertEqual((self.orb._offset_y, self.orb._angle, self.orb._scale), state)
        self.assertEqual(self.orb.geometry(), geometry)
        still = self.orb.grab().toImage()
        QTest.qWait(60)
        self.assertEqual(self.orb.grab().toImage(), still)
        self.host.hide()
        self.host.show()
        self.app.processEvents()
        self.assertEqual(self.orb._anim.state(), QAbstractAnimation.State.Paused)


if __name__ == '__main__':
    unittest.main()
