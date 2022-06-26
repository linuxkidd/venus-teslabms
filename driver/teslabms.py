#!/usr/bin/env python3

import argparse, os, platform, re, serial, signal, sys, time
from datetime import datetime as dt
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib as gobject

# Victron packages
sys.path.insert(1, os.path.join(os.path.dirname(__file__), './ext/velib_python'))
from vedbus import VeDbusService


os.environ['TZ'] = 'UTC'
time.tzset()

driver = {
    'name'        : 'Tesla BMS',
    'servicename' : 'teslabms',
    'instance'    : 0,
    'id'          : 0x01,
    'version'     : '1.0',
    'serial'      : 'tesla4p',
    'connection'  : 'com.victronenergy.battery.ttyTESLABMS'
}

battery = {
    'min_battery_voltage': 19.6,
    'max_battery_voltage': 25.2,
    'max_charge_current': 800,
    'max_discharge_current': 800,
    'cell_count': 6,
    'module_count': 4,
    'installed_capacity': 800,
    'installed_capacity_wh': 800
}

def signal_handler(signal, frame):
    print('You pressed Ctrl+C!  Exiting...')
    print('')
    exit(0)

signal.signal(signal.SIGINT, signal_handler)

class SHUNT_proto():
    voltage      = 0.0
    current      = 0.0
    netAmpHours  = 0.0
    netWattHours = 0.0

    def __getitem__(self, item):
        return getattr(self,item)

    def decode(self, packet_buffer):
        self.decoded      = 1
        self.voltage      = float(packet_buffer[1])
        self.current      = float(packet_buffer[2])
        self.netAmpHours  = float(packet_buffer[3])
        self.netWattHours = float(packet_buffer[4])

class STAT_proto():
    isFaulted  = 0      # 1
    numModules = 0      # 2
    packVdc    = 0.0    # 3
    avgCellVdc = 0.0    # 4
    avgTempC   = 0.0    # 5
    decoded=0
    def __getitem__(self, item):
        return getattr(self, item)

    def decode(self, packet_buffer):
        self.decoded=1
        self.isFaulted   =   int(packet_buffer[1])
        self.numModules  =   int(packet_buffer[2])
        self.packVdc     = float(packet_buffer[3])
        self.avgCellVdc  = float(packet_buffer[4])
        self.avgTempC    = float(packet_buffer[5])

class MODULE_proto():
    moduleVdc = 0.0      # 2
    cellVdc   = [ 0.0, 0.0, 0.0, 0.0, 0.0, 0.0 ]
    cellBal   = [   0,   0,   0,   0,   0,   0 ]
    negTempC  = 0.0
    posTempC  = 0.0
    decoded   = 0
    def __getitem__(self, item):
        return getattr(self, item)

    def decode(self, packet_buffer):
        if len(packet_buffer) < 16:
            return
        self.decoded        = 1
        self.moduleVdc      = float(packet_buffer[2])
        for i in range(6):
            self.cellVdc[i] = float(packet_buffer[(i*2)+3])
            self.cellBal[i] =   int(packet_buffer[(i*2)+4])
        self.negTempC       = float(packet_buffer[15])
        self.posTempC       = float(packet_buffer[16])

def main():
    yn                       = ["No","Yes"]
    value_collection['STAT'] = STAT_proto()

    def openPort(serial_port):
        try:
            ser = serial.Serial(serial_port,115200)
            return ser
        except:
            print('Error: Failed to open communications port, exiting')
            exit()

    def dbusPublishShunt():
        dbusservice['/Dc/0/Current'] = value_collection['SHUNT'].current
        # dbusservice['/DC/0/Power']   = value_collection['SHUNT'].power
        try:
            power = round(value_collection['SHUNT'].current * value_collection['STAT'].packVdc,1)
        except:
            power = 0
        dbusservice['/Dc/0/Power'] = power
        dbusservice['/Capacity']   = round( battery["installed_capacity"] - value_collection['SHUNT'].netAmpHours, 2 )
        dbusservice['/CapacityWh'] = round( battery["installed_capacity_wh"] - value_collection['SHUNT'].netWattHours, 2 )

    def dbusPublishStat():
        if value_collection['STAT'].packVdc == 0:
            return
        Soc = round(((value_collection['STAT'].packVdc-battery["min_battery_voltage"])/(battery["max_battery_voltage"]-battery["min_battery_voltage"]))*100,1)
        dbusservice['/Soc']=Soc
        dbusservice['/Dc/0/Voltage']=value_collection['STAT'].packVdc
        if 'SHUNT' not in value_collection:
            power = 0
            dbusservice['/Capacity']   = round(Soc * battery["installed_capacity"]/100,2)
            dbusservice['/TimeToGo']   = 0
            dbusservice['/CapacityWh'] = round( Soc * battery["installed_capacity_wh"], 2 )
        else:
            power = round(value_collection['SHUNT'].current * value_collection['STAT'].packVdc,1)
        dbusservice['/Dc/0/Power'] = power
        dbusservice['/Dc/0/Temperature']=value_collection['STAT'].avgTempC

    def dbusPublishModules(moduleID):
        if value_collection["MODULES"][str(moduleID)].moduleVdc == 0:
            return
        dbusservice[f"/Module/{moduleID}/Sum"]=value_collection["MODULES"][str(moduleID)].moduleVdc
        dbusservice[f"/Module/{moduleID}/Temperature/Neg"]=value_collection["MODULES"][str(moduleID)].negTempC
        dbusservice[f"/Module/{moduleID}/Temperature/Pos"]=value_collection["MODULES"][str(moduleID)].posTempC
        for cellid in range(6):
            dbusservice[f"/Module/{moduleID}/Cell_{cellid+1}/Volts"]=value_collection["MODULES"][str(moduleID)].cellVdc[cellid]
            dbusservice[f"/Module/{moduleID}/Cell_{cellid+1}/Balancing"]= value_collection["MODULES"][str(moduleID)].cellBal[cellid]
        if moduleID==4:
            dbusPublishMinMax()

    def dbusPublishMinMax():
        balCellCount = 0
        minCellVolt = 99
        maxCellVolt = 0
        minCellVoltId = ""
        maxCellVoltId = ""
        minCellTemp = 9999
        maxCellTemp = -999
        minCellTempId = ""
        maxCellTempId = ""
        for moduleID in range(1,5):
            if value_collection["MODULES"][str(moduleID)].posTempC < minCellTemp:
                minCellTemp = value_collection["MODULES"][str(moduleID)].posTempC
                minCellTempId = f"{moduleID}.Pos"
            if value_collection["MODULES"][str(moduleID)].negTempC < minCellTemp:
                minCellTemp = value_collection["MODULES"][str(moduleID)].negTempC
                minCellTempId = f"{moduleID}.Neg"
            if value_collection["MODULES"][str(moduleID)].posTempC > maxCellTemp:
                maxCellTemp = value_collection["MODULES"][str(moduleID)].posTempC
                maxCellTempId = f"{moduleID}.Pos"
            if value_collection["MODULES"][str(moduleID)].negTempC > maxCellTemp:
                maxCellTemp = value_collection["MODULES"][str(moduleID)].negTempC
                maxCellTempId = f"{moduleID}.Neg"

            for cellID in range(6):
                if value_collection["MODULES"][str(moduleID)].cellVdc[cellID] > maxCellVolt:
                    maxCellVolt = value_collection["MODULES"][str(moduleID)].cellVdc[cellID]
                    maxCellVoltId = f"{moduleID}.{cellID}"
                if value_collection["MODULES"][str(moduleID)].cellVdc[cellID] < minCellVolt:
                    minCellVolt = value_collection["MODULES"][str(moduleID)].cellVdc[cellID]
                    minCellVoltId = f"{moduleID}.{cellID}"
            balCellCount = balCellCount + sum(value_collection["MODULES"][str(moduleID)].cellBal)

        dbusservice["/System/MinCellVoltage"] = minCellVolt
        dbusservice["/System/MinVoltageCellId"] = minCellVoltId
        dbusservice["/System/MaxCellVoltage"] = maxCellVolt
        dbusservice["/System/MaxVoltageCellId"] = maxCellVoltId

        dbusservice["/System/MinCellTemperature"] = minCellTemp
        dbusservice["/System/MinTemperatureCellId"] = minCellTempId
        dbusservice["/System/MaxCellTemperature"] = maxCellTemp
        dbusservice["/System/MaxTemperatureCellId"] = maxCellTempId

    def handle_serial_data():
        myline=ser.readline()
        myparts=myline.decode('ascii').rstrip().split(',')
        for mpidx in range(len(myparts)):  
            if re.match("^[0-9]*$",myparts[mpidx]):
                myparts[mpidx]=int(myparts[mpidx])
            elif re.match("^[0-9\.]*$",myparts[mpidx]):
                myparts[mpidx]=float(myparts[mpidx])
        if args.debug:
            print(myparts)
        if myparts[0]=="STAT":
            value_collection['STAT'].decode(myparts)
            dbusPublishStat()

        elif myparts[0] == "SHUNT":
            if "SHUNT" not in value_collection:
                value_collection["SHUNT"]=SHUNT_proto()
            value_collection["SHUNT"].decode(myparts)
            dbusPublishShunt()

        elif myparts[0]=="Module":
            if "MODULES" not in value_collection:
                value_collection["MODULES"]={}
            if str(myparts[1]) not in value_collection["MODULES"]:
                value_collection["MODULES"][str(myparts[1])]=MODULE_proto()
            value_collection["MODULES"][str(myparts[1])].decode(myparts)
            dbusPublishModules(myparts[1])

        gobject.timeout_add(50,handle_serial_data)

    def mainLoop():
        gobject.timeout_add(50,handle_serial_data)
        mainloop = gobject.MainLoop()
        mainloop.run()

        #while True:
        #    handle_serial_data()
        #    time.sleep(1)

    ser=openPort(serial_port)
    ser.flushInput()
    mainLoop()

def setupDbusPaths():
    # Create the management objects, as specified in the ccgx dbus-api document
    dbusservice.add_path('/Mgmt/ProcessName', __file__)
    dbusservice.add_path('/Mgmt/ProcessVersion', 'Python ' + platform.python_version())
    dbusservice.add_path('/Mgmt/Connection', 'Serial ttyUSB')

    # Create the mandatory objects
    dbusservice.add_path('/DeviceInstance',    driver['instance'])
    dbusservice.add_path('/ProductId',         driver['id'])
    dbusservice.add_path('/ProductName',       driver['name'])
    dbusservice.add_path('/FirmwareVersion',   driver['version'])
    dbusservice.add_path('/HardwareVersion',   driver['version'])
    dbusservice.add_path('/Serial',            driver['serial'])
    dbusservice.add_path('/Connected', 1)

    # Create static battery info
    dbusservice.add_path('/Info/BatteryLowVoltage', battery['min_battery_voltage'], writeable=True)
    dbusservice.add_path('/Info/MaxChargeVoltage', battery['max_battery_voltage'], writeable=True,
                                gettextcallback=lambda p, v: "{:0.2f}V".format(v))
    dbusservice.add_path('/Info/MaxChargeCurrent', battery['max_charge_current'], writeable=True,
                                gettextcallback=lambda p, v: "{:0.2f}A".format(v))
    dbusservice.add_path('/Info/MaxDischargeCurrent', battery['max_discharge_current'],
                                writeable=True, gettextcallback=lambda p, v: "{:0.2f}A".format(v))
    dbusservice.add_path('/System/NrOfCellsPerBattery', battery['cell_count'], writeable=True)
    dbusservice.add_path('/System/NrOfModulesOnline', battery['module_count'], writeable=True)
    dbusservice.add_path('/System/NrOfModulesOffline', 0, writeable=True)
    dbusservice.add_path('/System/NrOfModulesBlockingCharge', 0, writeable=True)
    dbusservice.add_path('/System/NrOfModulesBlockingDischarge', 0, writeable=True)
    dbusservice.add_path('/Capacity', None, writeable=True,
                                gettextcallback=lambda p, v: "{:0.2f}Ah".format(v))
    dbusservice.add_path('/CapacityWh', None, writeable=True,
                                gettextcallback=lambda p, v: "{:0.2f}Wh".format(v))

    dbusservice.add_path('/InstalledCapacity', battery['installed_capacity'], writeable=True,
                                gettextcallback=lambda p, v: "{:0.0f}Ah".format(v))
    dbusservice.add_path('/InstalledCapacityWh', battery['installed_capacity_wh'], writeable=True,
                                gettextcallback=lambda p, v: "{:0.0f}Ah".format(v))
    dbusservice.add_path('/ConsumedAmphours', 0, writeable=True,
                                gettextcallback=lambda p, v: "{:0.0f}Ah".format(v))
    dbusservice.add_path('/ConsumedWatthours', 0, writeable=True,
                                gettextcallback=lambda p, v: "{:0.0f}Wh".format(v))

    # Create SOC, DC and System items
    dbusservice.add_path('/Soc', None, writeable=True)
    dbusservice.add_path('/Dc/0/Voltage', None, writeable=True, gettextcallback=lambda p, v: "{:2.2f}V".format(v))
    dbusservice.add_path('/Dc/0/Current', 0, writeable=True, gettextcallback=lambda p, v: "{:2.2f}A".format(v))
    dbusservice.add_path('/Dc/0/Power',   0, writeable=True, gettextcallback=lambda p, v: "{:0.0f}W".format(v))
    dbusservice.add_path('/Dc/0/Temperature', None, writeable=True)


    # Create battery extras
    dbusservice.add_path('/System/MinCellTemperature', None, writeable=True)
    dbusservice.add_path('/System/MaxCellTemperature', None, writeable=True)
    dbusservice.add_path('/System/MinTemperatureCellId', None, writeable=True)
    dbusservice.add_path('/System/MaxTemperatureCellId', None, writeable=True)
    dbusservice.add_path('/System/MaxCellVoltage', None, writeable=True,
                                gettextcallback=lambda p, v: "{:0.3f}V".format(v))
    dbusservice.add_path('/System/MaxVoltageCellId', None, writeable=True)
    dbusservice.add_path('/System/MinCellVoltage', None, writeable=True,
                                gettextcallback=lambda p, v: "{:0.3f}V".format(v))
    dbusservice.add_path('/System/MinVoltageCellId', None, writeable=True)
    dbusservice.add_path('/History/ChargeCycles', None, writeable=True)
    dbusservice.add_path('/History/TotalAhDrawn', None, writeable=True)
    dbusservice.add_path('/Balancing', None, writeable=True, gettextcallback=lambda p, v: ["No", "Yes"][v])

    dbusservice.add_path('/Io/AllowToCharge', 1, writeable=True)
    dbusservice.add_path('/Io/AllowToDischarge', 1, writeable=True)
    # dbusservice.add_path('/SystemSwitch',1,writeable=True)

    # Create the alarms
    dbusservice.add_path('/Alarms/LowVoltage', None, writeable=True)
    dbusservice.add_path('/Alarms/HighVoltage', None, writeable=True)
    dbusservice.add_path('/Alarms/LowCellVoltage', None, writeable=True)
    dbusservice.add_path('/Alarms/HighCellVoltage', None, writeable=True)
    dbusservice.add_path('/Alarms/LowSoc', None, writeable=True)
    dbusservice.add_path('/Alarms/HighChargeCurrent', None, writeable=True)
    dbusservice.add_path('/Alarms/HighDischargeCurrent', None, writeable=True)
    dbusservice.add_path('/Alarms/CellImbalance', None, writeable=True)
    dbusservice.add_path('/Alarms/InternalFailure', None, writeable=True)
    dbusservice.add_path('/Alarms/HighChargeTemperature', None, writeable=True)
    dbusservice.add_path('/Alarms/LowChargeTemperature', None, writeable=True)
    dbusservice.add_path('/Alarms/HighTemperature', None, writeable=True)
    dbusservice.add_path('/Alarms/LowTemperature', None, writeable=True)

    #cell voltages
    for m in range(1,battery['module_count']+1):
        for i in range(1,battery['cell_count']+1):
            dbusservice.add_path(f'/Module/{m}/Cell_{i}/Volts', None, writeable=True, gettextcallback=lambda p, v: "{:0.3f}V".format(v))
            dbusservice.add_path(f'/Module/{m}/Cell_{i}/Balancing', None, writeable=True)
        dbusservice.add_path(f'/Module/{m}/Temperature/Neg', None, writeable=True, gettextcallback=lambda p, v: "{:0.1f}C".format(v))
        dbusservice.add_path(f'/Module/{m}/Temperature/Pos', None, writeable=True, gettextcallback=lambda p, v: "{:0.1f}C".format(v))
        dbusservice.add_path(f'/Module/{m}/Sum',  None, writeable=True, gettextcallback=lambda p, v: "{:2.2f}V".format(v))
        dbusservice.add_path(f'/Module/{m}/Diff', None, writeable=True, gettextcallback=lambda p, v: "{:0.3f}V".format(v))
    dbusservice.add_path('/Module/Sum',  None, writeable=True, gettextcallback=lambda p, v: "{:2.2f}V".format(v))
    dbusservice.add_path('/Module/Diff', None, writeable=True, gettextcallback=lambda p, v: "{:0.3f}V".format(v))

    # Create TimeToSoC items
    for num in [0, 10, 20, 30, 50, 80, 90, 100]:
        dbusservice.add_path(f'/TimeToSoC/{num}', None, writeable=True)

    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--port", default = "/dev/ttyACM0", help="commuications port descriptor, e.g /dev/ttyACM0 or COM1")
    parser.add_argument("-d", "--debug", action="store_true", help="Set to show debug output")

    args = parser.parse_args()

    serial_port = args.port

    DBusGMainLoop(set_as_default=True)
    dbusservice = VeDbusService(driver['connection'])
    setupDbusPaths()

    value_collection = {}
    main()
