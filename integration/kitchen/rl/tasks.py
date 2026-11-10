"""Physical task progress for autonomous policies, without expert skill labels."""
import copy
from integration.kitchen.tasks.catalog import chain, known_cases, HEAT
from integration.kitchen.validation.quality import ExpertQuality
from integration.kitchen.validation.success import validate_initial, validate_skill, validate_final


class TaskMonitor:
    def __init__(self, platform, case, *, validate_start=True):
        if case not in known_cases(case['namespace']):
            raise ValueError('Unknown task definition')
        if validate_start:validate_initial(platform.controller, case)
        self.platform, self.case = platform, copy.deepcopy(case)
        self.skills = list(chain(case))
        self.index = 0
        self.completed = []
        self.success = False
        self.failure_reason = None
        self.pending_reason = None
        self.quality = ExpertQuality(platform)
        self.quality.start(self.skills[0])
        dev = platform.controller.devices
        self.initial_heated = set(dev.heated)
        self.initial_heating = dev.heating
        self.initial_tray_support = dev.supports('wood_tray', ['user_table'])
        self.initial_meal_on_tray = bool(case.get('meal') and dev.supports(case['meal']+'_container',['tray_bottom']))
        self.payloads = [n for n in platform.scene.objects if n!='wood_tray' and dev.supports(n,['tray_bottom'])]

    def _check_order(self):
        dev, case = self.platform.controller.devices, self.case
        future = self.skills[self.index+1:]
        if 'B1' in future and not self.initial_tray_support and dev.supports('wood_tray',['user_table']):
            raise ValueError('TASK_ORDER_DELIVERY_BEFORE_PREPARATION')
        if case.get('meal'):
            meal = case['meal']+'_container'
            if 'R5' in future and not self.initial_meal_on_tray and dev.supports(meal,['tray_bottom']):
                raise ValueError('TASK_ORDER_MEAL_BEFORE_FRUIT')
            if HEAT in future and (meal in dev.heated-self.initial_heated or
                    dev.heating is not None and dev.heating!=self.initial_heating):
                raise ValueError('TASK_ORDER_HEATING_BEFORE_FRUIT')

    def update(self):
        if self.success or self.failure_reason:
            return self.evidence()
        if self.platform.controller.stopped or self.quality.failures:
            self.failure_reason = self.platform.controller.stop_reason or 'CONTROLLER_STOPPED'
            return self.evidence()
        try:
            self._check_order()
        except ValueError as error:
            self.failure_reason = str(error)
            return self.evidence()
        skill = self.skills[self.index]
        try:
            if skill == HEAT:
                meal = self.case['meal']+'_container'
                if meal not in self.platform.controller.devices.heated:
                    raise ValueError('MEAL_NOT_HEATED')
                self.quality.tray_object(meal)
                self.quality.require('door:closed', .5)
            else:
                validate_skill(self.quality, skill, self.case)
            if self.index == len(self.skills)-1:
                validate_final(self.quality, self.case)
                if skill == 'B1':
                    for name in self.payloads:
                        self.quality.tray_object(name)
        except (ValueError, RuntimeError) as error:
            self.pending_reason = str(error)
            return self.evidence()
        self.completed.append({'skill': skill, 'tick': self.platform.controller.tick})
        self.pending_reason = None
        self.index += 1
        if self.index == len(self.skills):
            self.success = True
        else:
            self.quality.start(self.skills[self.index])
        return self.evidence()

    def evidence(self):
        return {'schema': 'kitchen_rl_task_evidence_v1', 'case_id': self.case['case_id'],
                'tick': self.platform.controller.tick, 'success': self.success,
                'failure_reason': self.failure_reason, 'pending_reason': self.pending_reason,
                'phase': None if self.success else self.skills[self.index],
                'completed': copy.deepcopy(self.completed), 'stable_windows_s': self.quality.current.copy()}

    def close(self):
        if self.quality.sample in self.platform.post_step_observers:
            self.platform.post_step_observers.remove(self.quality.sample)
