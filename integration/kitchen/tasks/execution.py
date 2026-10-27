"""Task preparation, state-dependent skill selection and composition."""
from __future__ import annotations
import math



from integration.kitchen.planning.common import ExpertFailure

class TaskExecution:
    def prepare(self, case):
        task,meal=case["task"],case["meal"]
        self.mark("initial_settle")
        self.wait(1.)
        if task in ("R1","R2","R4") or task=="R3" and case["initial_occupancy"]!="empty":
            self.open_door()
        if task in ("R2","R4") or task=="R3" and case["initial_occupancy"]!="empty":
            self.meal(meal,"oven")
        # R3 is a closed-door opening task, including an empty oven. The
        # layout's current default is 100deg open, so prepare it explicitly.
        if task in ("R3", "R4"):
            self.close_door()
        self.mark("preparation_complete")


    def skill(self, name, case, quality):
        from integration.kitchen.validation.success import validate_skill
        fruit,meal=case["fruit"],case["meal"]
        quality.start(name)
        self.mark("skill_started",skill=name)
        if name in ("L1","L2"):
            self.fruit(fruit,wash=name=="L2",slot=-.11 if meal else 0.)
            self.settle_quality(quality,fruit+":tray")
        elif name=="R1":
            self.meal(meal,"oven");self.close_door()
        elif name=="R2":
            self.meal(meal,in_oven=True);self.close_door()
            self.settle_quality(quality,meal+"_container:tray")
        elif name=="R3":self.open_door()
        elif name=="R4":self.heat_open(meal)
        elif name=="R5":
            self.meal(meal);self.settle_quality(quality,meal+"_container:tray")
        elif name=="B1":self.deliver()
        else:raise ExpertFailure("UNKNOWN_EXPERT_SKILL: "+name)
        validate_skill(quality,name,case)
        self.mark("skill_qualified",skill=name)


    def settle_quality(self,quality,key,max_seconds=30.):
        for _ in range(int(math.ceil(max_seconds/.4))):
            if quality.current.get(quality.phase+":"+key,0.)>=2.-1e-9:return
            self.wait(.4)
        raise ExpertFailure("SETTLING_WINDOW_TIMEOUT: "+key)


    def heat_meal(self, case, quality):
        # State-dependent high-level chain; device operations are chosen from
        # measured door/occupancy/heat state, never the planned target state.
        meal=case["meal"];dev=self.platform.controller.devices
        if not dev.meal_ready(meal+"_container"):
            if dev.angle("microwave_hinge")<math.radians(70):self.skill("R3",case,quality)
            self.skill("R1",case,quality)
        if meal+"_container" not in dev.heated:
            if dev.angle("microwave_hinge")>math.radians(5):self.close_door()
            self.skill("R4",case,quality)
        elif dev.angle("microwave_hinge")<math.radians(70):self.skill("R3",case,quality)
        self.skill("R2",case,quality)


    def run_case(self, case, quality):
        from integration.kitchen.tasks.catalog import ATOMIC,COMPOSITE
        task=case["task"]
        if task in ATOMIC:self.skill(task,case,quality)
        else:
            chain = COMPOSITE[task][1]
            for index, skill in enumerate(chain):
                if skill=="heat_meal_state_dependent":self.heat_meal(case,quality)
                else:self.skill(skill,case,quality)
                if skill in ("L1", "L2") and index + 1 < len(chain):
                    # Releasing fruit leaves the long left gripper above the
                    # tray, inside the right forearm's placement corridor.
                    # Leave that corridor before planning the next skill.
                    self.mark("composite_left_clearance", fruit=case["fruit"])
                    self.clear_left_corridor()
                    self.settle_quality(quality,case["fruit"]+":tray")
                    quality.tray_object(case["fruit"])
        # Later skills must not undo earlier subgoals in a composite.
        from integration.kitchen.validation.success import validate_final
        if case["fruit"]:self.settle_quality(quality,case["fruit"]+":tray")
        validate_final(quality,case)
