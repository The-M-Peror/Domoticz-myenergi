# myenergi Python Plugin for Domoticz
#
# Authors: mvdklip
#
# Based on
#
# https://github.com/rklomp/Domoticz-SMA-SunnyBoy
# https://github.com/twonk/MyEnergi-App-Api

"""
<plugin key="myenergi" name="myenergi" author="mvdklip" version="1.2.0">
    <description>
        <h2>myenergi Plugin</h2><br/>
        <h3>Features</h3>
        <ul style="list-style-type:square">
            <li>Register generation, usage and more</li>
        </ul>
    </description>
    <params>
        <param field="Username" label="Hub serial" width="200px" required="true"/>
        <param field="Password" label="Password as set in the app" width="200px" required="true" password="true"/>
        <param field="Mode3" label="Query interval" width="75px" required="true">
            <options>
                <option label="5 sec" value="1"/>
                <option label="15 sec" value="3"/>
                <option label="30 sec" value="6" default="true"/>
                <option label="1 min" value="12"/>
                <option label="3 min" value="36"/>
                <option label="5 min" value="60"/>
                <option label="10 min" value="120"/>
            </options>
        </param>
        <param field="Mode6" label="Debug" width="75px">
            <options>
                <option label="True" value="Debug"/>
                <option label="False" value="Normal" default="true"/>
            </options>
        </param>
    </params>
</plugin>
"""

import requests
import Domoticz


class BasePlugin:
    enabled = False
    lastPolled = 0
    baseUrl = "https://director.myenergi.net"
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    maxAttempts = 3
    httpTimeout = 3

    zappi_mode_texts = { 1: 'Fast', 2: 'Eco', 3: 'Eco++', 4: 'Stop' }
    zappi_status_texts = { 1 : 'Waiting for export', 2 : 'DSR-Demand Side Response', 3: 'Diverting/Charging', 4: 'Boosting', 5: 'Charge Complete' }
    charge_status_texts = { 'A' : 'EV disconnected', 'B1': 'EV connected', 'B2' : 'Waiting for EV', 'C1': 'EV ready to charge', 'C2': 'Charging', 'F': 'Fault / Restart' }
    # Source - https://myenergi.info/open-energy-monitor-local-emoncms-t2192.html
    # Or... rewrite to combine into a single display status, like this? - https://myenergi.info/how-to-tell-charge-complete-in-api-t1595.html#p13021

    def __init__(self):
        return

    def onStart(self):
        Domoticz.Debug("onStart called")
        if Parameters["Mode6"] == "Debug":
            Domoticz.Debugging(1)
        else:
            Domoticz.Debugging(0)

        # TODO - Find a way to get total counters from the API instead of letting Domoticz compute
        if len(Devices) < 1:
            Domoticz.Device(Name="PV Generation", Unit=1, TypeName='kWh', Switchtype=4, Options={'EnergyMeterMode':'1'}).Create()
        if len(Devices) < 2:
            Domoticz.Device(Name="Grid Import", Unit=2, TypeName='kWh', Options={'EnergyMeterMode':'1'}).Create()
        if len(Devices) < 3:
            Domoticz.Device(Name="Car Charging", Unit=3, TypeName='kWh', Options={'EnergyMeterMode':'1'}).Create()
        if len(Devices) < 4:
            Domoticz.Device(Name="Home Consumption", Unit=4, TypeName='kWh', Options={'EnergyMeterMode':'1'}).Create()
        if len(Devices) < 5:
            Domoticz.Device(Name="Grid Export", Unit=5, TypeName='kWh', Options={'EnergyMeterMode':'1'}).Create()
        if len(Devices) < 6:
            Domoticz.Device(Name="Grid Voltage", Unit=6, TypeName='Voltage').Create()
        if len(Devices) < 7:
            Domoticz.Device(Name="PV Self-consumption", Unit=7, TypeName='kWh', Options={'EnergyMeterMode':'1'}).Create()
        if len(Devices) < 8:
            Domoticz.Device(Name="Zappi Mode", Unit=8, TypeName='Text').Create()
        if len(Devices) < 9:
            Domoticz.Device(Name="Zappi Status", Unit=9, TypeName='Text').Create()
        if len(Devices) < 10:
            Domoticz.Device(Name="Charge Status", Unit=10, TypeName='Text').Create()

        DumpConfigToLog()

        Domoticz.Heartbeat(5)

    def onStop(self):
        Domoticz.Debug("onStop called")

    def onHeartbeat(self):
        Domoticz.Debug("onHeartbeat called %d" % self.lastPolled)

        if self.lastPolled == 0:
            attempt = 1

            while True:
                if attempt <= self.maxAttempts:
                    if attempt > 1:
                        Domoticz.Debug("Previous attempt failed, trying again...")
                else:
                    Domoticz.Error("Failed to retrieve data from %s, cancelling..." % self.baseUrl)
                    break # while True
                attempt += 1

                url = "%s/cgi-jstatus-*" % self.baseUrl
                r = None

                try:
                    r = requests.get(
                        url,
                        auth=requests.auth.HTTPDigestAuth(Parameters["Username"], Parameters["Password"]),
                        headers=self.headers,
                        timeout=self.httpTimeout,
                    )
                    if 'x_myenergi-asn' in r.headers:
                        newUrl = "https://%s" % r.headers['x_myenergi-asn']
                        if (newUrl != self.baseUrl):
                            self.baseUrl = "https://%s" % r.headers['x_myenergi-asn']
                            Domoticz.Debug("Base URL has changed to %s" % self.baseUrl)
                            break # while True
                    r.raise_for_status()
                    j = r.json()
                except Exception as e:
                    if r and r.status_code == 401:
                        Domoticz.Error("Unauthorized! Please check hub serial and password settings!")
                        break # while True
                    else:
                        Domoticz.Log("No data from %s; %s" % (url, e))
                else:
                    Domoticz.Debug("Received data: %s" % j)

                    grid_pwr = 0                                    # Grid Import/Export (W)
                    grid_vol = 0                                    # Grid Voltage (V)

                    zappi_gep_watt = 0                              # PV Generation Positive (W)
                    zappi_gen_watt = 0                              # PV Generation Negative (W)
                    zappi_div_watt = 0                              # Car Charging (W)
                    zappi_hom_watt = 0                              # Home Consumption (W)
                    zappi_slf_watt = 0                              # PV Self-consumption (W)
                    zappi_zmo = 0                                   # Zappi Mode
                    zappi_sta = 0                                   # Zappi Status
                    zappi_pst = ''                                  # Charge Status
                    zappi_che_watt = 0                              # Charge added this session

                    for data in j:

                        # Eddi
                        if 'eddi' in data:
                            pass                                    # TODO

                        # Zappi
                        if 'zappi' in data:
                            for device in data['zappi']:
                                # Grid readings
                                if 'grd' in device:
                                    grid_pwr = device['grd']
                                if 'vol' in device:
                                    grid_vol = device['vol'] / 10
                                # Zappi readings
                                if 'gep' in device:
                                    zappi_gen_watt += device['gep']
                                if 'gen' in device:
                                    zappi_gen_watt += device['gen']
                                if 'div' in device:
                                    zappi_div_watt += device['div']
                                if 'zmo' in device:
                                    zappi_zmo = device['zmo']  
                                if 'sta' in device:
                                    zappi_sta = device['sta']
                                if 'pst' in device:
                                    zappi_pst = device['pst']
                                if 'che' in device:
                                    zappi_che_watt += device['che']

                            zappi_hom_watt = (grid_pwr + zappi_gen_watt) - (zappi_div_watt + zappi_gep_watt)
                            zappi_slf_watt = max(zappi_gen_watt - zappi_gep_watt + min(grid_pwr, 0), 0)

                            # TODO - note will currently only display status of last Zappi (if multiple Zappis exist)
                            zappi_zmo_text = self.zappi_mode_texts.get(zappi_zmo, 'Unknown')
                            zappi_sta_text = self.zappi_status_texts.get(zappi_sta, 'Unknown')
                            zappi_pst_text = self.charge_status_texts.get(zappi_pst, 'Unknown')

                    # Work around negative kWh Domoticz issue #4736 using separate import and export grid meters
                    if (grid_pwr < 0):
                        Devices[5].Update(nValue=0, sValue=str(abs(grid_pwr))+";0")   # (-) Grid Export
                        Devices[2].Update(nValue=0, sValue="0;0")
                    else:
                        Devices[2].Update(nValue=0, sValue=str(grid_pwr)+";0")        # (+) Grid Import
                        Devices[5].Update(nValue=0, sValue="0;0")
                    Devices[6].Update(nValue=0, sValue=str(grid_vol)+";0")

                    # TODO - Find a way to get total counters from the API instead of letting Domoticz compute
                    Devices[1].Update(nValue=0, sValue=str(zappi_gen_watt - zappi_gep_watt)+";0")
                    Devices[3].Update(nValue=0, sValue=str(zappi_div_watt)+";0")
                    Devices[4].Update(nValue=0, sValue=str(zappi_hom_watt)+";0")
                    Devices[7].Update(nValue=0, sValue=str(zappi_slf_watt)+";0")

                    Devices[8].Update(nValue=0, sValue=zappi_zmo_text)
                    Devices[9].Update(nValue=0, sValue=zappi_sta_text)
                    Devices[10].Update(nValue=0, sValue=zappi_pst_text)
                    #Devices[11].Update(nValue=0, sValue=str(zappi_che_watt)+";0")  # TODO

                    break # while True

        self.lastPolled += 1
        self.lastPolled %= int(Parameters["Mode3"])


global _plugin
_plugin = BasePlugin()


def onStart():
    global _plugin
    _plugin.onStart()


def onStop():
    global _plugin
    _plugin.onStop()


def onHeartbeat():
    global _plugin
    _plugin.onHeartbeat()


# Generic helper functions
def DumpConfigToLog():
    for x in Parameters:
        if Parameters[x] != "":
            Domoticz.Debug("'" + x + "':'" + str(Parameters[x]) + "'")
    Domoticz.Debug("Device count: " + str(len(Devices)))
    for x in Devices:
        Domoticz.Debug("Device:           " + str(x) + " - " + str(Devices[x]))
        Domoticz.Debug("Device ID:       '" + str(Devices[x].ID) + "'")
        Domoticz.Debug("Device Name:     '" + Devices[x].Name + "'")
        Domoticz.Debug("Device nValue:    " + str(Devices[x].nValue))
        Domoticz.Debug("Device sValue:   '" + Devices[x].sValue + "'")
        Domoticz.Debug("Device LastLevel: " + str(Devices[x].LastLevel))
    return
