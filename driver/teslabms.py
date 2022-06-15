#!/usr/bin/env python3

import argparse, os, platform, serial, signal, time
from datetime import datetime as dt

os.environ['TZ'] = 'UTC'
time.tzset()

driver = {
    'name'        : 'Tesla BMS',
    'servicename' : 'teslabms',
    'instance'    : 1,
    'id'          : 1,
    'version'     : 1.0,
    'serial'      : 'tesla4s',
    'connection'  : 'com.victronenergy.battery.ttyTESLABMS01'
}

def signal_handler(signal, frame):
    print('You pressed Ctrl+C!  Exiting...')
    print('')
    exit(0)

signal.signal(signal.SIGINT, signal_handler)

class SHUNT_proto():
    current      = 0.0
    voltage      = 0.0
    netamphours  = 0.0
    netwatthours = 0.0

    def __getitem__(self, item):
        return getattr(self,item)

    def decode(self, packet_buffer):
        self.decoded      = 1
        self.current      = float(packet_buffer[1])
        self.voltage      = float(packet_buffer[2])
        self.netamphours  = float(packet_buffer[3])
        self.netwatthours = float(packet_buffer[4])

class STAT_proto():
    isFaulted = 0      # 1
    numModules = 0     # 2
    packVdc = 0.0      # 3
    avgCellVdc = 0.0   # 4
    avgTempC = 0.0      # 5
    decoded=0
    def __getitem__(self, item):
        return getattr(self, item)

    def decode(self, packet_buffer):
        self.decoded=1
        self.isFaulted=int(packet_buffer[1])
        self.numModules=int(packet_buffer[2])
        self.packVdc=float(packet_buffer[3])
        self.avgCellVdc=float(packet_buffer[4])
        self.avgTempC=float(packet_buffer[5])

class MODULE_proto():
    moduleVdc = 0.0      # 2
    cellVdc = [ 0.0, 0.0, 0.0, 0.0, 0.0, 0.0 ]
    cellBal = [   0,   0,   0,   0,   0,   0 ]
    negTempC = 0.0
    posTempC = 0.0
    decoded=0
    def __getitem__(self, item):
        return getattr(self, item)

    def decode(self, packet_buffer):
        if(len(packet_buffer)<16):
            return
        self.decoded=1
        self.moduleVdc = float(packet_buffer[2])
        for i in range(6):
            self.cellVdc[i]=float(packet_buffer[(i*2)+3])
            self.cellBal[i]=int(packet_buffer[(i*2)+4])
        self.negTempC = float(packet_buffer[15])
        self.posTempC = float(packet_buffer[16])

def main():
    current_mode=["Discharge","Charge","Storage"]
    yn=["No","Yes"]
    value_collection['STAT']=STAT_proto()

    def openPort(serial_port):
        try:
            ser = serial.Serial(serial_port,115200)
            return ser
        except:
            print('Error: Failed to open communications port, exiting')
            exit()

    def mainLoop():
        ser=openPort(serial_port)

        while True:
            myline=ser.readline()
            myparts=myline.decode('ascii').rstrip().split(',')
            if myparts[0]=="STAT":
                value_collection['STAT'].decode(myparts)
                dbusservice["/Voltages/Sum"]=f"{value_collection['STAT'].packVdc} V"
                dbusservice["/Raw/Voltages/Sum"]=value_collection['STAT'].packVdc
                dbusservice["/Voltages/UpdateTimestamp"]=dt.now().strftime('%a %d.%m.%Y %H:%M:%S')
                dbusservice["/Raw/Voltages/UpdateTimestamp"]=time.time()
            elif myparts[0] == "SHUNT":
                if("SHUNT" not in value_collection):
                    value_collection["SHUNT"]=SHUNT_proto()
                value_collection["SHUNT"].decode(myparts)
                dbusservice["/Info/Current"]=f"{value_collection['STAT'].current} A"
                dbusservice["/Raw/Info/Current"]=value_collection['STAT'].current
                current_mode_id=2
                if value_collection['STAT'].current>0:
                    current_mode_id=0
                elif value_collection['STAT'].current<0:
                    current_mode_id=1
                dbusservice["/Info/CurrentMode"]=f"{current_mode[current_mode_id]} A"
                dbusservice["/Raw/Info/CurrentMode"]=current_mode_id
            elif myparts[0]=="Module":
                if("MODULES" not in value_collection):
                    value_collection["MODULES"]={}
                if str(myparts[1]) not in value_collection["MODULES"]:
                    value_collection["MODULES"][str(myparts[1])]=MODULE_proto()
                value_collection["MODULES"][str(myparts[1])].decode(myparts)
                dbusservice[f"/Voltages/Sum{myparts[1]}"]=f'{value_collection["MODULES"][str(myparts[1])].moduleVdc} V'
                dbusservice[f"/Raw/Voltages/Sum{myparts[1]}"]=value_collection["MODULES"][str(myparts[1])].moduleVdc
                dbusservice[f"/Info/Temp/Sensor{myparts[1]*2}"]=f'{value_collection["MODULES"][str(myparts[1])].negTempC} C'
                dbusservice[f"/Raw/Info/Temp/Sensor{myparts[1]*2}"]=value_collection["MODULES"][str(myparts[1])].negTempC
                dbusservice[f"/Info/Temp/Sensor{myparts[1]*2+1}"]=f'{value_collection["MODULES"][str(myparts[1])].posTempC} C'
                dbusservice[f"/Raw/Info/Temp/Sensor{myparts[1]*2+1}"]=value_collection["MODULES"][str(myparts[1])].posTempC
                for cellid in range(6):
                    dbusservice[f"/Voltages/Cell{myparts[1]}.{cellid}"]=f'{value_collection["MODULES"][str(myparts[1])].cellVdc[cellid]} V'
                    dbusservice[f"/Raw/Voltages/Cell{myparts[1]}.{cellid}"]=value_collection["MODULES"][str(myparts[1])].cellVdc[cellid]
                    dbusservice[f"/Balancing/Cell{myparts[1]}.{cellid}"]=f'{yn[value_collection["MODULES"][str(myparts[1])].cellBal[cellid]]}'
                    dbusservice[f"/Raw/Balancing/Cell{myparts[1]}.{cellid}"]=value_collection["MODULES"][str(myparts[1])].cellBal[cellid]


    mainLoop()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--port", default = "/dev/ttyACM0", help="commuications port descriptor, e.g /dev/ttyACM0 or COM1")
    parser.add_argument("-d", "--debug", default = 0, type=int, choices=[0, 1, 2], help="debug data")

    args = parser.parse_args()

    serial_port = args.port

    # Victron packages
    sys.path.insert(1, os.path.join(os.path.dirname(__file__), './ext/velib_python'))
    from vedbus import VeDbusService


    from dbus.mainloop.glib import DBusGMainLoop
    DBusGMainLoop(set_as_default=True)

    dbusservice = VeDbusService(driver['connection'])

    # Create the management objects, as specified in the ccgx dbus-api document
    dbusservice.add_path('/Mgmt/ProcessName', __file__)
    dbusservice.add_path('/Mgmt/ProcessVersion', 'Python ' + platform.python_version())
    dbusservice.add_path('/Mgmt/Connection', driver['connection'])

    # Create the mandatory objects
    dbusservice.add_path('/DeviceInstance',  driver['instance'])
    dbusservice.add_path('/ProductId',       driver['id'])
    dbusservice.add_path('/ProductName',     driver['name'])
    dbusservice.add_path('/FirmwareVersion', driver['version'])
    dbusservice.add_path('/HardwareVersion', driver['version'])
    dbusservice.add_path('/Serial',          driver['serial'])
    dbusservice.add_path('/Connected',       1)

    # Create device list
    dbusservice.add_path('/Devices/0/DeviceInstance',  driver['instance'])
    dbusservice.add_path('/Devices/0/FirmwareVersion', driver['version'])
    dbusservice.add_path('/Devices/0/ProductId',       driver['id'])
    dbusservice.add_path('/Devices/0/ProductName',     driver['name'])
    dbusservice.add_path('/Devices/0/ServiceName',     driver['servicename'])
    dbusservice.add_path('/Devices/0/VregLink',        "(API)")


    # Create the Tesla BMS paths
    dbusservice.add_path('/Info/Soc',                      -1)
    dbusservice.add_path('/Raw/Info/Soc',                  -1)
    for sensorid in range(8):
        dbusservice.add_path(f'/Info/Temp/Sensor{sensorid}',     -1)
        dbusservice.add_path(f'/Raw/Info/Temp/Sensor{sensorid}', -1)

    dbusservice.add_path('/Info/UpdateTimestamp',          -1)
    dbusservice.add_path('/Raw/Info/UpdateTimestamp',      -1)

    for moduleid in range(4):
        for cellid in range(6):
            dbusservice.add_path(f'/Voltages/Cell{moduleid}.{cellid}',      -1)
            dbusservice.add_path(f'/Raw/Voltages/Cell{moduleid}.{cellid}',  -1)
            dbusservice.add_path(f'/Balancing/Cell{moduleid}.{cellid}',     -1)
            dbusservice.add_path(f'/Raw/Balancing/Cell{moduleid}.{cellid}', -1)
        dbusservice.add_path(f'/Voltages/Sum{moduleid}',                  -1)
        dbusservice.add_path(f'/Raw/Voltages/Sum{moduleid}',              -1)

    dbusservice.add_path(f'/Info/Balancing/CellsBalancingCount', -1)
    dbusservice.add_path(f'/Raw/Balancing/CellsBalancingCount',  -1)

    dbusservice.add_path('/Info/CurrentMode',              -1)
    dbusservice.add_path('/Raw/Info/CurrentMode',          -1)
    dbusservice.add_path('/Info/Current',                  -1)
    dbusservice.add_path('/Raw/Info/Current',              -1)
    dbusservice.add_path('/Voltages/Sum',                  -1)
    dbusservice.add_path('/Raw/Voltages/Sum',              -1)
    dbusservice.add_path('/Voltages/Diff',                 -1)
    dbusservice.add_path('/Raw/Voltages/Diff',             -1)
    dbusservice.add_path('/Voltages/Max',                  -1)
    dbusservice.add_path('/Raw/Voltages/Max',              -1)
    dbusservice.add_path('/Voltages/Min',                  -1)
    dbusservice.add_path('/Raw/Voltages/Min',              -1)
    dbusservice.add_path('/Voltages/BatteryCapacityWH',    "20 kWh")
    dbusservice.add_path('/Raw/Voltages/BatteryCapacityWH', 20000)
    dbusservice.add_path('/Voltages/BatteryCapacityAH',    "930 Ah")
    dbusservice.add_path('/Raw/Voltages/BatteryCapacityAH', 930)
    dbusservice.add_path('/Voltages/UpdateTimestamp',      -1)
    dbusservice.add_path('/Raw/Voltages/UpdateTimestamp',  -1)

    value_collection = {}
    main()
