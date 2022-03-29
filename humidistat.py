# MicroPython humidity control

import time
from micropython import const
from machine import Pin

MODE_OFF = const(0)
MODE_ON = const(1)
MODE_AUTO = const(2)


class Humidistat():
    def __init__(self, gpioPin, mode=MODE_OFF, minimum_run_minutes=15, minimum_off_minutes=15, maximum_run_minutes=240):
        self.gpio_switch = Pin(gpioPin, Pin.OUT)
        self.mode = mode
        self.humidity_desired = -1
        self.enabled = False
        self.__set_minimum_run_minutes(minimum_run_minutes)
        self.__set_minimum_off_minutes(minimum_off_minutes)
        self.__set_maximum_run_minutes(maximum_run_minutes)
        time_current = time.time()
        # don't call set_state here since it updates last_activity_time
        self.state = 0
        self.gpio_switch.value(0)
        self.init_time = time_current
        # backdate initial last_activity_time to simplify comparison logic and allow immediate run if needed
        self.last_activity_time = time_current - self.minimum_run_minutes * 60

    def set_humidity_percent(self, value: int):
        self.humidity_desired = value

    def set_mode(self, mode: int):
        self.mode = mode

    def set_state(self, value: int):
        # update switch state if needed and update last_activity_time
        if self.gpio_switch.value() != value:
            print("set_state: switching from %s to %s" % (self.gpio_switch.value(), value))
            self.gpio_switch.value(value)
            self.state = value
            time_stamp = time.time()
            print('updating on/offtime to %s' % time_stamp)
            self.last_activity_time = time_stamp

    def __set_minimum_run_minutes(self, minutes: int):
        self.minimum_run_minutes = minutes

    def __set_minimum_off_minutes(self, minutes: int):
        self.minimum_off_minutes = minutes

    def __set_maximum_run_minutes(self, minutes: int):
        self.maximum_run_minutes = minutes

    def enable(self):
        self.enabled = True

    def disable(self):
        self.enabled = False

    def get_last_activity_msg(self) -> str:
        '''Returns message reflecting the current running state and human readable duration (in minutes or seconds)'''

        action = "Stopped"
        units = "seconds"
        time_current = time.time()

        if self.last_activity_time < self.init_time:
            action = "No events for"
            duration = time_current - self.init_time
        else:
            if self.state:
                action = "Running"
            duration = time_current - self.last_activity_time

        if duration > 120:
            duration = duration // 60
            if duration > 1:
                units = "minutes"
            else:
                units = "minute"

        return '{0} for {1} {2}'.format(action, duration, units)

    def evaluate(self, humidity_current, override=False) -> bool:
        '''
        Call periodically to evaluate new humidity readings or when settings such as the desired humidity level change.
        Use override = True when settings change to override minimum/maxium on/off restrictions.
        Returns True/False for the resulting state (can also get Humidistat.state).
        '''

        # If mode is not auto, there isn't anything to evaluate except the state (set state, and return state)
        if self.mode != MODE_AUTO:
            print('mode is set to {0}, change to auto to evaluate humidity level'.format(self.mode))
            if self.mode == MODE_ON:
                self.set_state(1)
            if self.mode == MODE_OFF:
                self.set_state(0)
            return self.state

        time_current = time.time()
        last_activity_seconds = time_current - self.last_activity_time
        if self.mode == MODE_AUTO:
            if self.humidity_desired > humidity_current:
                print('self.humidity_desired ({0}) > humidity_current ({1})'.format(self.humidity_desired, humidity_current))
                # humidity is too low
                # check if already running
                if self.state == 1:
                    # check maximum running time
                    if last_activity_seconds > self.maximum_run_minutes * 60 and not override:
                        print('humidity is too low: but stopping due to maximum running time reached at %s for %s seconds (last_activity_time=%s)' % (time_current, last_activity_seconds, self.last_activity_time))
                        self.set_state(0)
                        return False
                    else:
                        print('humidity is too low: already running at %s for %s seconds (last_activity_time=%s)' % (time_current, last_activity_seconds, self.last_activity_time))
                        return True

                # above forces return so can assume not running, but check run time constraints
                if last_activity_seconds <= self.minimum_off_minutes * 60 and not override:
                    print('humidity is too low: but not starting due to minimum off (%s minutes) time at %s (off for %s seconds)' % (self.minimum_off_minutes, time_current, last_activity_seconds))
                    return False

                # okay to turn on
                print('humidity is too low: starting at %s' % (time_current))
                self.set_state(1)
                return True

            else:
                print('self.humidity_desired ({0}) <= humidity_current ({1})'.format(self.humidity_desired, humidity_current))
                # humidity is at desired level (current humidity is <= desired humidity)
                # if running, check if minimum run time has been met
                if self.state == 1 and last_activity_seconds < self.minimum_run_minutes * 60 and not override:
                    # keep running until minimum run time is met
                    print('humidity is ok, but minimum run time has not been met: %s' % (time_current))
                    return True

                # okay to turn off
                print('humidity is ok: %s' % (time_current))
                self.set_state(0)
                return False
