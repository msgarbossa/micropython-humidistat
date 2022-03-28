# MicroPython humidity control

from micropython import const
from machine import Pin
import time

MODE_OFF = const(0)
MODE_ON = const(1)
MODE_AUTO = const(2)

class Humidistat():
    def __init__(self, gpioPin, mode = MODE_AUTO, minimumRunMinutes = 15, minimumOffMinutes = 15, maximumRunMinutes = 240):
        self.gpioSwitch = Pin(gpioPin, Pin.OUT)
        self.mode = mode
        self.Disable()
        self.__SetMinimumRunMinutes(minimumRunMinutes)
        self.__SetMinimumOffMinutes(minimumOffMinutes)
        self.__SetMaximumRunMinutes(maximumRunMinutes)
        timeCurrent = time.time()
        # don't call SetState here since it updates lastActivityTime
        self.state = 0
        self.gpioSwitch.value(0)
        self.initTime = timeCurrent
        # backdate initial lastActivityTime to simplify comparison logic and allow immediate run if needed
        self.lastActivityTime = timeCurrent - self.minimumRunMinutes * 60

    def SetHumidityPercent(self, value: int):
        self.humdityDesired = value

    def SetState(self, value: int):        
        # update switch state if needed and update lastActivityTime
        if self.gpioSwitch.value() != value:
            print("SetState: switching from %s to %s" % (self.gpioSwitch.value(), value))
            self.gpioSwitch.value(value)
            self.state = value
            timeStamp = time.time()
            print('updating on/offtime to %s' % timeStamp)
            self.lastActivityTime = timeStamp

    def __SetMinimumRunMinutes(self, minutes: int):
        self.minimumRunMinutes = minutes

    def __SetMinimumOffMinutes(self, minutes: int):
        self.minimumOffMinutes = minutes

    def __SetMaximumRunMinutes(self, minutes: int):
        self.maximumRunMinutes = minutes

    def Enable(self):
        self.enabled = True

    def Disable(self):
        self.enabled = False

    def GetLastActivityMsg(self) -> str:
        '''Returns message reflecting the current running state and human readable duration (in minutes or seconds)'''

        action = "Stopped"
        units = "seconds"
        timeCurrent = time.time()

        if self.lastActivityTime < self.initTime and self.lastActivityTime < self.initTime:
            action = "No events for"
            duration = timeCurrent - self.initTime
        else:
            if self.state:
                action = "Running"
                duration = timeCurrent - self.lastActivityTime
            else:
                duration = timeCurrent - self.lastActivityTime

        if duration > 120:
            duration = duration // 60
            if duration > 1:
                units = "minutes"
            else:
                units = "minute"

        return '{0} for {1} {2}'.format(action, duration, units)

    def Evaluate(self, humidityCurrent, override = False) -> bool:
        '''
        Call periodically to evaluate new humidity readings or when settings such as the desired humidity level change.
        Use override = True when settings change to override minimum/maxium on/off restrictions.
        Returns True/False for the resulting state (can also get Humidistat.state). 
        '''

        # If mode is not auto, there isn't anything to evaluate except the state (set state, and return state)
        if self.mode != MODE_AUTO:
            print('mode is set to {0}, change to auto to evaluate humidity level'.format(self.mode))
            if self.mode == MODE_ON:
                self.SetState(1)
            if self.mode == MODE_OFF:
                self.SetState(0)
            return self.state

        timeCurrent = time.time()
        lastActivitySeconds = timeCurrent - self.lastActivityTime
        if self.mode == MODE_AUTO:
            if self.humdityDesired > humidityCurrent:
                print('self.humdityDesired ({0}) > humidityCurrent ({1})'.format(self.humdityDesired, humidityCurrent))
                # humidity is too low
                # check if already running
                if self.state == 1:
                    # check maximum running time
                    if lastActivitySeconds > self.maximumRunMinutes * 60 and override == False:
                        print('humidity is too low: but stopping due to maximum running time reached at %s for %s seconds (lastActivityTime=%s)' % (timeCurrent, lastActivitySeconds, self.lastActivityTime))
                        self.SetState(0)
                        return False
                    else:
                        print('humidity is too low: already running at %s for %s seconds (lastActivityTime=%s)' % (timeCurrent, lastActivitySeconds, self.lastActivityTime))
                        return True

                # above forces return so can assume not running, but check run time constraints
                if lastActivitySeconds <= self.minimumOffMinutes * 60 and override == False:
                    print('humidity is too low: but not starting due to minimum off (%s minutes) time at %s (off for %s seconds)' % (self.minimumOffMinutes, timeCurrent, lastActivitySeconds))
                    return False

                # okay to turn on
                print('humidity is too low: starting at %s' % (timeCurrent))
                self.SetState(1)
                return True
        
            else:
                print('self.humdityDesired ({0}) <= humidityCurrent ({1})'.format(self.humdityDesired, humidityCurrent))
                # humidity is at desired level (current humidity is <= desired humidity)
                # if running, check if minimum run time has been met
                if self.state == 1 and lastActivitySeconds < self.minimumRunMinutes * 60 and override == False:
                    # keep running until minimum run time is met
                    print('humidity is ok, but minimum run time has not been met: %s' % (timeCurrent))
                    return True  

                # okay to turn off
                print('humidity is ok: %s' % (timeCurrent))
                self.SetState(0)
                return False
     