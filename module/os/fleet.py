from module.base.button import *
from module.base.timer import Timer
from module.base.utils import *
from module.exception import MapWalkError
from module.logger import logger
from module.map.fleet import Fleet
from module.map.map_grids import SelectedGrids
from module.map.utils import location_ensure
from module.map_detection.utils import *
from module.os.assets import TEMPLATE_EMPTY_HP
from module.os.camera import OSCamera
from module.os.map_base import OSCampaignMap
from module.os_ash.ash import OSAsh
from module.os_combat.combat import Combat


def limit_walk(location, step=3):
    x, y = location
    if abs(x) > 0:
        x = min(abs(x), step - abs(y)) * x // abs(x)
    return x, y


class OSFleet(OSCamera, Combat, Fleet, OSAsh):
    def _goto(self, location, expected=''):
        super()._goto(location, expected)
        self.predict_radar()
        self.map.show()

        if self.handle_ash_beacon_attack():
            # After ash attack, camera refocus to current fleet.
            self.camera = location
            self.update()

    def map_data_init(self, map_=None):
        """
        Create new map object, and use the shape of current zone
        """
        map_ = OSCampaignMap()
        map_.shape = self.zone.shape
        super().map_data_init(map_)

    def map_control_init(self):
        """
        Remove non-exist things like strategy, round.
        """
        # self.handle_strategy(index=1 if not self.fleets_reversed() else 2)
        self.update()
        # if self.handle_fleet_reverse():
        #     self.handle_strategy(index=1)
        self.hp_reset()
        self.hp_get()
        self.lv_reset()
        self.lv_get()
        self.ensure_edge_insight(preset=self.map.in_map_swipe_preset_data)
        # self.full_scan(must_scan=self.map.camera_data_spawn_point)
        self.find_current_fleet()
        self.find_path_initial()
        # self.map.show_cost()
        # self.round_reset()
        # self.round_battle()

    def find_current_fleet(self):
        self.fleet_1 = self.camera

    @property
    def _walk_sight(self):
        sight = (-4, -1, 3, 2)
        return sight

    _os_map_event_handled = False

    def ambush_color_initial(self):
        self._os_map_event_handled = False

    def handle_ambush(self):
        """
        Treat map events as ambush, to trigger walk retrying
        """
        if self.handle_map_get_items():
            self._os_map_event_handled = True
            self.device.sleep(0.3)
            self.device.screenshot()
            return True
        elif self.handle_map_event():
            self.ensure_no_map_event()
            self._os_map_event_handled = True
            return True
        else:
            return False

    def handle_mystery(self, button=None):
        """
        After handle_ambush, if fleet has arrived, treat it as mystery, otherwise just ambush.
        """
        if self._os_map_event_handled and button.predict_fleet() and button.predict_current_fleet():
            return 'get_item'
        else:
            return False

    @staticmethod
    def _get_goto_expected(grid):
        """
        Argument `expected` used in _goto()
        """
        if grid.is_enemy:
            return 'combat'
        elif grid.is_resource or grid.is_meowfficer or grid.is_exclamation:
            return 'mystery'
        else:
            return ''

    def _hp_grid(self):
        hp_grid = super()._hp_grid()

        # Location of six HP bar, according to respective server for os
        if self.config.SERVER == 'en':
            hp_grid = ButtonGrid(origin=(35, 205), delta=(0, 100), button_shape=(66, 3), grid_shape=(1, 6))
        elif self.config.SERVER == 'jp':
            pass
        else:
            pass

        return hp_grid

    def hp_retreat_triggered(self):
        return False

    def hp_get(self):
        """
        Calculate current HP, also detects the wrench (Ship died, need to repair)
        """
        super().hp_get()
        ship_icon = self._hp_grid().crop((0, -67, 67, 0))
        need_repair = [TEMPLATE_EMPTY_HP.match(self.image_area(button)) for button in ship_icon.buttons]
        logger.attr('Repair icon', need_repair)

        if any(need_repair):
            for index, repair in enumerate(need_repair):
                if repair:
                    self._hp_has_ship[self.fleet_current_index][index] = True
                    self._hp[self.fleet_current_index][index] = 0

            logger.attr('HP', ' '.join(
                [str(int(data * 100)).rjust(3) + '%' if use else '____'
                 for data, use in zip(self.hp, self.hp_has_ship)]))

        return self.hp

    def lv_get(self, after_battle=False):
        pass

    def get_sea_grids(self):
        """
        Get sea grids on current view

        Returns:
            SelectedGrids:
        """
        sea = []
        for local in self.view:
            if not local.predict_sea() or local.predict_current_fleet():
                continue
            # local = np.array(location) - self.camera + self.view.center_loca
            location = np.array(local.location) + self.camera - self.view.center_loca
            location = tuple(location.tolist())
            if location == self.fleet_current or location not in self.map:
                continue
            sea.append(self.map[location])

        if len(self.fleet_current):
            center = self.fleet_current
        else:
            center = self.camera
        return SelectedGrids(sea).sort_by_camera_distance(center)

    def wait_until_camera_stable(self, skip_first_screenshot=True):
        """
        Wait until homo_loca stabled.
        DETECTION_BACKEND must be 'homography'.
        """
        logger.hr('Wait until camera stable')
        record = None
        confirm_timer = Timer(0.3, count=0).start()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            self.update_os()
            current = self.view.backend.homo_loca
            logger.attr('homo_loca', current)
            if record is None or (current is not None and np.linalg.norm(np.subtract(current, record)) < 3):
                if confirm_timer.reached():
                    break
            else:
                confirm_timer.reset()

            record = current

        logger.info('Camera stabled')

    def wait_until_walk_stable(self, skip_first_screenshot=False):
        """
        Wait until homo_loca stabled.
        DETECTION_BACKEND must be 'homography'.

        Raises:
            MapWalkError: If unable to goto such grid.
        """
        logger.hr('Wait until walk stable')
        record = None
        enemy_searching_appear = False

        confirm_timer = Timer(0.8, count=2).start()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # Map event
            if self.handle_map_event():
                confirm_timer.reset()
                continue
            if self.handle_walk_out_of_step():
                raise MapWalkError('walk_out_of_step')

            # Enemy searching
            if not enemy_searching_appear and self.enemy_searching_appear():
                enemy_searching_appear = True
                confirm_timer.reset()
                continue
            else:
                if enemy_searching_appear:
                    self.handle_enemy_flashing()
                    self.device.sleep(0.3)
                    logger.info('Enemy searching appeared.')
                    enemy_searching_appear = False
                if self.is_in_map():
                    self.enemy_searching_color_initial()

            # Combat
            if self.combat_appear():
                # Use ui_back() for testing, because there are too few abyssal loggers every month.
                # self.ui_back(check_button=self.is_in_map)
                self.combat(expected_end=self.is_in_map, fleet_index=self.fleet_show_index)
                confirm_timer.reset()
                continue

            # Arrive
            if self.is_in_map():
                self.update_os()
                current = self.view.backend.homo_loca
                logger.attr('homo_loca', current)
                if record is None or (current is not None and np.linalg.norm(np.subtract(current, record)) < 3):
                    if confirm_timer.reached():
                        break
                else:
                    confirm_timer.reset()
                record = current
            else:
                confirm_timer.reset()

        logger.info('Walk stabled')

    def port_goto(self):
        """
        A simple and poor implement to goto port. Searching port on radar.

        In OpSi, camera always focus to fleet when fleet is moving which mess up `self.goto()`.
        In most situation, we use auto search to clear a map in OpSi, and classic methods are deprecated.
        But we still need to move fleet toward port, this method is for this situation.

        Raises:
            MapWalkError: If unable to goto such grid.
                Probably clicking at land, center of port, or fleet itself.
        """
        while 1:
            # Calculate destination
            grid = self.radar.port_predict(self.device.image)
            logger.info(f'Port route at {grid}')
            if np.linalg.norm(grid) == 0:
                logger.info('Arrive port')
                break

            # Update local view
            self.update_os()
            self.predict()

            # Click way point
            grid = point_limit(grid, area=(-4, -2, 3, 2))
            grid = self.convert_radar_to_local(grid)
            self.device.click(grid)

            # Wait until arrived
            self.wait_until_walk_stable()

    def fleet_set(self, index=1, skip_first_screenshot=True):
        """
        Args:
            index (int): Target fleet_current_index
            skip_first_screenshot (bool):

        Returns:
            bool: If switched.
        """
        logger.hr(f'Fleet set to {index}')
        if self.fleet_selector.ensure_to_be(index):
            self.wait_until_camera_stable()
            return True
        else:
            return False

    def question_goto(self, has_fleet_step=False):
        logger.hr('Question goto')
        while 1:
            # Update local view
            # Not screenshots taking, reuse the old one
            self.update_os()
            self.predict()
            self.predict_radar()

            # Calculate destination
            grids = self.radar.select(is_question=True)
            if grids:
                # Click way point
                grid = location_ensure(grids[0])
                grid = point_limit(grid, area=(-4, -2, 3, 2))
                if has_fleet_step:
                    grid = limit_walk(grid)
                grid = self.convert_radar_to_local(grid)
                self.device.click(grid)
            else:
                logger.info('No question mark to goto, stop')
                break

            # Wait until arrived
            # Having new screenshots
            self.wait_until_walk_stable()

    def boss_goto(self, location=(0, 0), has_fleet_step=False):
        logger.hr('BOSS goto')
        while 1:
            # Update local view
            # Not screenshots taking, reuse the old one
            self.update_os()
            self.predict()
            self.predict_radar()

            # Calculate destination
            grids = self.radar.select(is_enemy=True)
            if grids:
                # Click way point
                grid = np.add(location_ensure(grids[0]), location)
                grid = point_limit(grid, area=(-4, -2, 3, 2))
                if has_fleet_step:
                    grid = limit_walk(grid)
                if grid == (0, 0):
                    logger.info(f'Arrive destination: boss {location}')
                    break
                grid = self.convert_radar_to_local(grid)
                self.device.click(grid)
            else:
                logger.info('No boss to goto, stop')
                break

            # Wait until arrived
            # Having new screenshots
            self.wait_until_walk_stable()

    def boss_leave(self, skip_first_screenshot=True):
        """
        Pages:
            in: is_in_map(), or combat_appear()
            out: is_in_map(), fleet not in boss.
        """
        logger.hr('BOSS leave')
        # Update local view
        self.update_os()

        click_timer = Timer(3)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # End
            if self.is_in_map():
                self.predict_radar()
                if self.radar.select(is_enemy=True):
                    logger.info('Fleet left boss')
                    break

            # Re-enter boss accidently
            if self.combat_appear():
                self.ui_back(check_button=self.is_in_map)

            # Click leave button
            if self.is_in_map() and click_timer.reached():
                grid = self.view[self.view.center_loca]
                # The left half grid next to the center grid.
                area = corner2inner(grid.grid2screen(area2corner((1, 0.25, 1.5, 0.75))))
                button = Button(area=area, color=(), button=area, name='BOSS_LEAVE')
                self.device.click(button)
                click_timer.reset()

    def boss_clear(self, has_fleet_step=True):
        """
        All fleets take turns in attacking the boss.

        Args:
            has_fleet_step (bool):

        Returns:
            bool: If success to clear.

        Pages:
            in: Siren logger (abyssal), boss appeared.
            out: If success, dangerous or safe zone.
                If failed, still in abyssal.
        """
        logger.hr(f'BOSS clear', level=1)
        fleets = [1, 2, 3, 4]
        standby_grids = [(-1, -1), (0, -1), (1, -1), (0, 0)]
        for fleet, standby in zip(fleets, standby_grids):
            logger.hr(f'Try boss with fleet {fleet}', level=2)
            self.fleet_set(fleet)
            self.boss_goto(location=(0, 0), has_fleet_step=has_fleet_step)

            # End
            self.predict_radar()
            if self.radar.select(is_question=True):
                logger.info('BOSS clear')
                self.map_exit()
                return True

            # Standby
            self.boss_leave()
            if standby == (0, 0):
                break
            self.boss_goto(location=standby, has_fleet_step=has_fleet_step)

        logger.critical('Unable to clear boss, fleets exhausted')
        return False

    def run_abyssal(self):
        """
        Handle double confirms and attack abyssal (siren logger) boss.

        Returns:
            bool: If success to clear.

        Pages:
            in: Siren logger (abyssal).
            out: If success, in a dangerous or safe zone.
                If failed, still in abyssal.
        """
        self.handle_map_fleet_lock(enable=False)
        self.question_goto(has_fleet_step=True)
        result = self.boss_clear(has_fleet_step=True)
        return result
