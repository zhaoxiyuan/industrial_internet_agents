from .types import *
from .mock_cv import *
from .mock_sensors import *
from .mock_positioning import *

from . import long_shift
SCENARIOS_CV['F'] = long_shift.cv_schedule()
SCENARIOS_SENSORS['F'] = long_shift.sensor_schedule()
SCENARIOS_POSITIONING['F'] = long_shift.position_schedule()


def scenario_duration(scenario):
    return long_shift.DURATION if scenario == 'F' else int(SCENARIO_DURATION_SEC)


def sample_available(scenario,source,subject,t):
    return scenario != 'F' or long_shift.available(source,subject,t)
