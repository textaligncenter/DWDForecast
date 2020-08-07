#
#  Copyright (C) 2020  Kilian Knoll kilian.knoll@gmx.de
#  
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
#
# Purpose 
#Extract weather forecast data from DWD Mosmix for a given Station ID
# 
#
#
# Background information:
# DWD provides 10 day forecast weather - and radiation data at an hourly resolution for over 5000 Stations worldwide (focus is on Germany/Europe though...)
# Description of kml file:
#https://www.dwd.de/DE/leistungen/opendata/help/schluessel_datenformate/kml/mosmix_elemente_pdf.pdf?__blob=publicationFile&v=3
#
#List of available stations:
#https://www.dwd.de/DE/leistungen/met_verfahren_mosmix/mosmix_stationskatalog.cfg?view=nasPublication&nn=495490
#
# How to use this ?
# 1) Find the station close by your geographic location:
#   Go to the website below, zoom to your location - and click on "Mosmix Stationen anzeigen" 
#   Once you found the closest station, please change the station number to  the station number 
#   https://wettwarn.de/mosmix/mosmix.html
#   In my case, I picked Station P755 (which is close to Munich)
# 2) Make changes in code below to reflect your station number - and the corresponding URL
#   change
#       self.mystation = P755
#   below to the one you identified during step 1
#   change the URL further down below to reflect the station:
# self.urlpath = 'https://opendata.dwd.de/weather/local_forecasts/mos/MOSMIX_L/single_stations/P755/kml' 
# Your one time setup is done...
# 
# Implementation
# DWD provides two types of kml files
# single station kml files. These get updated approx every 6 hours
# all stations. These get updated hourly. However the file is pretty large. On embedded systems such as raspberry pi, I ran out of memory trying to parse XML files that size (exceeded 1GB of memory). Hence the decision to use the single station files
# The maiin routine creates a subthread. That subthread  constantly polls the DWD webserver and checks for updates. If an update is found, the file gets downloaded, unzipped and the kml file (which is sort of an XML file gets parsed)
# we are only looking for a couple of key parameters that are relevant 
#Currently the following Parameters get extracted from the kml file and put into a twodimensional array:
#myTZtimestamp : Timestamp of the forecast  data
#Rad1h       : Radiation Energy [kj/m²]
#TTT         : Temperature 2 m above surface [°C]
#PPPP        : Presssure Values (Surface Pressure reduced)
#FF          : Wind speed [m/s]
# 
# 
# Update July 30 2020
# Added Option to perform 'Simple' or 'Complex' mode of operation:
#Simple : Try to get weather data once only - then terminate
#Complex : Start a seperate queue that continuously polls the DWD server on the internet to get updated data 
#
# Update August 9 2020
# Added the following capabilities and options
#
# Use PVLIB for more precise prediction of power generation: AC data, DC data, Celltemperature of Panels
# Please see : https://pvlib-python.readthedocs.io/en/stable/
# This provides the following benefits
#   Leverage the other parameters TTT, PPPP, FF from DWD  in predicting power generation
#   Leverage actual solar generation parameters such as
#       Location of the solar system, inclination & orientation
#       Use of inverter- and solar panel characteristics
#
# Added the options to output resulting information of the prediction to -print-output -output-tocsv -output into mariaDB
# 
# Installation dependencies & requirements
# see import modules below
# some hints on import modules (modules that may not be straight forward to identify from the actual import list below:
# sudo pip3 install mysql
# sudo pip3 install mysql.connector 
# sudo pip3 install pvlib
# also requires scipy - easiest way on e.g. raspberry is:
# sudo apt-get install python3-numpy python3-scipy
#


import urllib.request
import shutil
import zipfile
from bs4 import BeautifulSoup
import requests
import xml.etree.ElementTree as ET
import time
import datetime
import queue
import threading
import logging
import pprint

import numpy as np
import pandas as pd
import pandas as pd
import pvlib
from pvlib.pvsystem import PVSystem
from pvlib.location import Location
from pvlib.modelchain import ModelChain
import mysql.connector
from mysql.connector import Error
from mysql.connector import errorcode
   


pp = pprint.PrettyPrinter(indent=4)

def connvertINTtimestamptoDWD(inputstring):
    # Purpose: Convert a timestamp as presented by the UTC: 1545030000.0
    # and return it to a UTC representation: 2018-12-17T08:00:00.000000Z
    #mynewtime =time.mktime(datetime.datetime.strptime(inputstring, "%Y-%m-%dT%H:%M:%S.%fZ").timetuple())
    #print ("neue Zeit ", mynewtime)
    mysecondtime = (datetime.datetime.fromtimestamp(inputstring).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]) + "Z"     
    return (mysecondtime) 
def loggerdate():
    myloggingtimestamp = datetime.datetime.fromtimestamp(time.time()).strftime('%Y-%m-%d_%H:%M:%S')    
    return (myloggingtimestamp)
    

# Main class that holds the required information 
class dwdforecast(threading.Thread):
    def __init__ (self, myqueue):
        print ("Starting dwdforecast init ...")          
        # You need to change Parameters to adjust to your environment:
        self.mystation= 'P755'                              # See description above how to find your station 
        # Only use the "all_stations" if you got decent hardware
        #self.urlpath = 'http://opendata.dwd.de/weather/local_forecasts/mos/MOSMIX_S/all_stations/kml'
        #On Raspberries & alikes, use the one for your specific station: 
        self.urlpath = 'http://opendata.dwd.de/weather/local_forecasts/mos/MOSMIX_L/single_stations/P755/kml'
        self.sleeptime = 15                                 #Time interval we poll the server [seconds]- please increase time since updates from DWD are hourly at best
        # Your solar plant location data goes here :
        self.mylongitude = 11.600000                        #GPS longitude data of your plant 
        self.mylatitude = 48.1000000                         #GPS latitude data of your plant 
        self.myaltitude = 491                               #Elevation [m] above sea
        self.mytimezone = 'Europe/Berlin'                   #Timezone of plant location 
        self.mypv_elevation = 35                            #Inclination angle of solar panels
        self.mypv_azimuth = 177                             #Orientation - where 270=West, 180=South, 90=Eaat
        self.PVtotalEfficiency = 0.002391571                #Only needed for crude DWD only based calculation from Rad1h to actual powergen. This is used for Rad1Energy calculation. 
        #Parameter setup for your plant
        # Note: once you installed pvlib on your system, please have a look at the ...\pvlib\data directory and open the corresponding csv files to find your inverter / solar panel
        
        # Please also substitute any special character that you find in the csv file and replace it with underscores _ in the definitions below.
        # Once you made changes to the csv file, put the changes into where pvlib data is installed to - e.g.:
        #/usr/local/lib/python3.5/dist-packages/pvlib/data
        #
        self.sandia_modules = pvlib.pvsystem.retrieve_sam('cecmod')
        self.sandia_module = self.sandia_modules['LG_Electronics_Inc__LG335E1C_A5'] # is "LG Electronics Inc. LG335E1C-A5" in sam-library-cec-modules-2019-03-05.csv
        self.cec_inverters = pvlib.pvsystem.retrieve_sam('cecinverter')
        self.cec_inverter = self.cec_inverters['Kostal__Plenticore_plus_10']
        #self.cec_inverter = self.cec_inverters['SMA_America__SB10000TL_US__240V_']  # is "SMA America: SB10000TL-US [240V]" in sam-library-cec-inverters-2019-03-05.csv         
        #
        # Which output do you want the script to generate ?
        self.PrintOutput = 1                                #0 = no output 1 = print output 
        self.CSVOutput = 1                                  #0 = no output 1 = output to csv file 
        self.CSVFile = 'outputdwdforecast.csv'              #CSV filename 
        self.DBOutput = 0                                   #0 = no output 1 = output to mariaDB 
        #Existance of a mariaDB is required 
        if (self.DBOutput ==1):
            self.db = mysql.connector.connect(user='yourdbuser',passwd="yourdbpwd", host='192.168.178.39', database='yourdbname',autocommit=True)           #Connect string to the database - we are setting
            self.cur = self.db.cursor()   
        #
        """
        A Table with the following definition is what we are populating to:
        describe dwd;
        +-------------+------------+------+-----+---------+-------+
        | Field       | Type       | Null | Key | Default | Extra |
        +-------------+------------+------+-----+---------+-------+
        | mydatetime  | datetime   | NO   | PRI | NULL    |       |
        | mytimestamp | int(11)    | NO   | PRI | 0       |       |
        | Rad1h       | float(8,2) | NO   |     | 0.00    |       |
        | PPPP        | float(8,2) | NO   |     | 0.00    |       |
        | FF          | float(5,2) | NO   |     | 0.00    |       |
        | TTT         | float(5,2) | NO   |     | 0.00    |       |
        | Rad1wh      | float(8,2) | NO   |     | 0.00    |       |
        | Rad1Energy  | float(8,2) | NO   |     | 0.00    |       |
        | ACSim       | float(8,2) | NO   |     | 0.00    |       |
        | DCSim       | float(8,2) | NO   |     | 0.00    |       |
        | CellTempSim | float(5,2) | NO   |     | 0.00    |       |
        +-------------+------------+------+-----+---------+-------+
        """
        #
        # No more configurable stuff beyond this point 
        # XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
        self.mypvliblocation   = Location(latitude  = self.mylatitude, 
                           longitude = self.mylongitude,
                           tz        = self.mytimezone,
                            altitude = self.myaltitude)
        self.lasttimecheck = 1534800680.0                   # Dec 14th 2018 (pure initialization)
        self.myqueue = myqueue
        self.event = threading.Event()
        self.ext = 'kmz' 
        self.myinit = 0                                                                                     #So we can populate the queue initially / subsequently
        threading.Thread.__init__ (self)

        
        self.mysolarsystem= PVSystem(      surface_tilt                             = self.mypv_elevation, 
                                        surface_azimuth                             = self.mypv_azimuth,
                                        module                                      = self.sandia_module,
                                        inverter                                    = self.cec_inverter,
                                        module_parameters                           = self.sandia_module,
                                        inverter_parameters                         = self.cec_inverter,
                                        albedo                                      = 0.15,
                                        modules_per_string                          = 14,
                                        strings_per_inverter                        = 2)        
                
                
        
        print ("I am looking for data from DWD for the following station: ", self.mystation)
        print ("I will be polling the following URL for the latest updates ", self.urlpath)

    # Based on the user specified URL, find the latest file file with it´s timestamp 
    def GetURLForLatest(self,urlpath, ext=''):
        try:
            page = requests.get(urlpath).text
        except Exception as ErrorGetWebdata:
            logging.error("%s %s",",GetURLForLatest Error getting data from the internet:", ErrorGetWebdata)
        soup = BeautifulSoup(page, 'html.parser')
        soup_reduced= soup.find_all('pre')
        soup_reduced = soup_reduced[0]
        counter = 0
        for elements in soup_reduced:
            elements = str(elements)
            if (counter >0):
                words =elements.split()
                mytime = words[0] +"-" + words[1]
                logging.debug("%s %s" ,",GetURLForLatest :DWD Filetimestamp found :", mytime)
                mynewtime =time.mktime(datetime.datetime.strptime(mytime, "%d-%b-%Y-%H:%M").timetuple())
                logging.debug("%s %s" ,",GetURLForLatest :DWD Filetimestamp found :", mynewtime)
                #print ("From function GetURLForLatest -mynewtime", 2*mynewtime)
            
            if (elements.find("LATEST") >0):
                #print ("My element", elements)
                counter = 1
        myurl = [urlpath + '/' + node.get('href') for node in soup.find_all('a') if node.get('href').endswith(ext)]
        return (myurl, mynewtime)

        
    def changeDWDTimestamp(self,inputstring):
        #Purpose: Convert a timestamp as presented by the DWD: 2018-12-25T07:00:00.000Z
        #and return it in a format of 2018-12-25 07:00:00.000
        mynewstring = inputstring.replace('T',' ')
        mynewstring = mynewstring.replace('Z','')
        return (mynewstring)


    
    
    def connvertDWDtimestamptoINT(self,inputstring):
        # Purpose: Convert a timestamp as presented by the DWD: 2018-12-25T07:00:00.000Z
        # and return it to a UTC representation
        mynewtime =time.mktime(datetime.datetime.strptime(inputstring, "%Y-%m-%d %H:%M:%S.%f").timetuple())
        mycurrentINTtimestamp =int(mynewtime)
        return (mycurrentINTtimestamp)

    def findlastDBtimestamp(self,cursor, mytable):
        # Purpose: See what the last timestamp is (if any on the given table)
        # Returns: 
        #     0: If the table is empty
        #    Int: Integer of the timestamp of last row
        mytimestamp = 'mytimestamp'
        myquery = "select %s from %s order by mytimestamp desc limit 1" % (mytimestamp, mytable)
        cursor.execute(myquery)
        myresult = cursor.fetchall()
        rowcount = cursor.rowcount                                  #In case we have no rows selected, we seem to have an empty table
        if (rowcount <1):
            #print ("Nothing found in Database")
            timestamp = 0
        else:
            # We got a result - however this looks like : [(1544947737,)]   - so we need to "massage it to return the integer value of it
            myresult = str(myresult[0])
            myresult = myresult.split('(')
            myresult = myresult[1]
            myresult = myresult.split(',')
            myresult = int(myresult[0])        
            timestamp = int(myresult)
            #print ("We found a timestamp in the database - routine findlastDBtimestamp", timestamp)
        return (timestamp)
        
    def checkTimestampExistence(self,cursor, mytable, timetocheck):
        # Purpose: Check to see if the timestamp already exists in the database. If yes - we need to update the rows - if no, we need to add the rows
        # Returns:
        #   If nothing is found: 0
        #   If there is a match: 1
        mytimestamp = 'mytimestamp'
        myquery = "select %s from %s where %s = %s" % (mytimestamp, mytable, mytimestamp, timetocheck )
        cursor.execute(myquery)
        myresult = cursor.fetchall()
        rowcount = cursor.rowcount                                  #In case we have no rows selected, we seem to have an empty table
        if (rowcount <1):
            #print ("Nothing found in Database")
            timestamp = 0
        else:
            #print ("In unterroutine checkTimestampExistence", myresult)
            timestamp = 1
        return (timestamp)  

    def addsingleRow2DB(self,cursor, tablename, content):
        cursor.execute("describe %s" % tablename)
        allowed_keys = set(row[0] for row in cursor.fetchall())
        keys = allowed_keys.intersection(content)
        #print ("My allowed keys are", keys)
        if len(content) > len(keys):
            unknown_keys = set(content) - allowed_keys
            #print >> sys.stderr, "skipping keys:", ", ".join(unknown_keys)
            badkeys = ",".join(unknown_keys)
            #print ("Skipping keys :",badkeys)
        columns = "`,`".join(keys)
        columns = "`" +columns + "`"
        values_template = ", ".join(["%s"] * len(keys))
        #print ("columns sind", columns)
        #print ("tablename ist", tablename)
        #print ("values_template ist", values_template)
        sql = "insert into %s (%s) values (%s)" % (
            tablename, columns, values_template)
        # INSERT into TABLE (columname1, columnname2) VALUES (value1, value2)
        values = tuple(content[key] for key in keys)
        #print ("values sind", values)
        try:
            cursor.execute(sql, values)
            #print ("In routine addsingleRow2DB - sqlstatement und Werte sind: ", sql, ".....", values) 
        except mysql.connector.Error as error :
            #print("Routine addsingleRow2DB -Failed to update records to database: {}".format(error))
            logging.error("%s %s %s", loggerdate(), ",subroutine dwdweather, addsingleRow2DB ", error)

    def updatesingleRowinDB(self,cursor, tablename, TTT, Rad1h, FF, PPPP, mytimestamp, Rad1Energy, ACSim, DCSim, CellTempSim, Rad1wh):
        sql = "UPDATE "+ str(tablename) + " SET " +  " Rad1h= " + str(Rad1h) +", PPPP = " + str(PPPP)+ ", FF= " + str(FF) + ", TTT= " + str(TTT) + ", Rad1Energy= " + str(Rad1Energy) + ", ACSim= " + str(ACSim) + ", DCSim = " + str(DCSim) + ", CellTempSim =" + str(CellTempSim) + ", Rad1wh =" + str(Rad1wh)     +" WHERE mytimestamp= " + str(mytimestamp)
        #print ("In Routine updatesingleRowinDB -mein update string ist", sql)
        try:
            cursor.execute(sql)
            #print ("In subroutine updatesingleRowinDB -sql is : ", sql)
        except mysql.connector.Error as error :
            logging.error("%s %s %s", loggerdate(), ",subroutine dwdweather, updatesingleRowinDB ", error)
            #print("Failed to update records to database: {}".format(error))
    
                        
            
    try:
        def run(self):
            while not self.event.is_set():            #In case the main process wants to shut us down...
                if (self.myinit== 0):                 #We populate the first timestamp to signal to main that we are up & running
                    temptimestamp = time.time()
                    print ("From dwdforecast - initial queue population", temptimestamp)
                    self.myqueue.put(temptimestamp)
                    self.myinit = 1
                time.sleep(1)
                # =============================================================================
                # Getting the file download from DWD setup
                # =============================================================================
                try:
                    self.mydownloadfiles, self.mynewtime = self.GetURLForLatest(self.urlpath, self.ext)
                    #print ("Downloadfiles = ", self.mydownloadfiles)
                    #print ("Timestamp    = ", self.mynewtime)
                except Exception as ErrorReadFromDWD:
                    logging.error("%s %s" ,",dwdforecast  :", ErrorReadFromDWD)
            
                self.myarray =[]
                for self.file in self.mydownloadfiles:
                    self.myarray.append(self.file)
                self.temp_length = len(self.myarray)
                self.url = self.myarray[self.temp_length-1]

                logging.debug("%s %s %s",",dwdforecast : -BEFORE  if- time comparison :", self.mynewtime, self.lasttimecheck)                
                if (self.mynewtime > self.lasttimecheck):
                    logging.debug("%s %s %s" ,",dwdforecast : -in if- time comparison :", self.mynewtime, self.lasttimecheck)
                    #print ("DWD Weather - we have found a new kml file that we will download - timestamp was :", self.mynewtime)
                    #print ("DWD Weather -  self.lasttimecheck was ", self.lasttimecheck)
                    self.lasttimecheck = self.mynewtime
                    self.file_name = "temp1.gz"
                    self.out_file = "temp2.gz"
                    self.targetdir ="./"
                    try:
                        time.sleep(10)                                          #Assumption is - we see the file on the DWD server - but it has not yet been copied over
                        # Download the file from `url` and save it locally under `self.file_name`:
                        with urllib.request.urlopen(self.url) as self.response, open(self.file_name, 'wb') as self.out_file:
                            shutil.copyfileobj(self.response, self.out_file)
                        time.sleep(5)                                           #not sure if this gets rid of the access problems                  
                        with zipfile.ZipFile(self.file_name,"r") as zip_ref:
                            Myzipfilename = (zip_ref.namelist())
                            Myzipfilename = str(Myzipfilename[0])
                            zip_ref.extractall(self.targetdir)    
                        logging.debug("%s %s" ,",dwdforecast : -File that I extract is zipfile :", Myzipfilename)
                        time.sleep(5)                                           #not sure if this gets rid of the access problems
                    except Exception as MyException:
                        logging.error("%s %s", ",subroutine dwdforecast exception getting the data from server : ", MyException)    
                    # =============================================================================
                    # Parsing DWD File content
                    # =============================================================================                        
                    self.tree = ET.parse(Myzipfilename) 
                    self.root = self.tree.getroot()
                    self.root.tag     
                    """      
                        <kml:kml xmlns:dwd="https://opendata.dwd.de/weather/lib/pointforecast_dwd_extension_V1_0.xsd" xmlns:gx="http://www.google.com/kml/ext/2.2" xmlns:xal="urn:oasis:names:tc:ciq:xsdschema:xAL:2.0" xmlns:kml="http://www.opengis.net/kml/2.2" xmlns:atom="http://www.w3.org/2005/Atom">
                        
                        <kml:kml xmlns:dwd="https://opendata.dwd.de/weather/lib/pointforecast_dwd_extension_V1_0.xsd" xmlns:gx="http://www.google.com/kml/ext/2.2" xmlns:xal="urn:oasis:names:tc:ciq:xsdschema:xAL:2.0" xmlns:kml="http://www.opengis.net/kml/2.2" xmlns:atom="http://www.w3.org/2005/Atom">
                    """
                    #--------------------------------------------------
                    #Namespace definition for kml file:
                    #
                    self.ns = {'dwd': 'https://opendata.dwd.de/weather/lib/pointforecast_dwd_extension_V1_0.xsd', 'gx': 'http://www.google.com/kml/ext/2.2',
                    'kml': 'http://www.opengis.net/kml/2.2', 'atom': 'http://www.w3.org/2005/Atom', 'xal':'urn:oasis:names:tc:ciq:xsdschema:xAL:2.0'}
                    #--------------------------------------------------
                    # We get the timestamps
                    #
                    self.timestamps = self.root.findall('kml:Document/kml:ExtendedData/dwd:ProductDefinition/dwd:ForecastTimeSteps/dwd:TimeStep',self.ns)
                    self.i = 0
                    self.timevalue=[]
                    for self.child in self.timestamps:
                        #print ("TIMESTAMPS",  child.text)
                        self.timevalue.append(self.child.text)
                    """
                    for j in timevalue:
                        print ("Zeit",i, " ", timevalue[i])
                        i = i+1
                    """
                        
                    for self.elem in self.tree.findall('./kml:Document/kml:Placemark',self.ns):                    #Position us at the Placemark
                        #print ("SUCERJH ", sucher)
                        #print ("Elemente ", elem.tag, elem.attrib, elem.text)
                        self.mylocation = self.elem.find('kml:name',self.ns).text                                  #Look for the station Number
                        
                        # Here we pull the required data out of the xml file
                        if (self.mylocation == self.mystation):   
                            #print ("meine location", self.mylocation)
                            self.myforecastdata = self.elem.find('kml:ExtendedData',self.ns)
                            for self.elem in self.myforecastdata:                                         
                                #We may get the following strings and are only interested in the right hand quoted property name WPcd1:
                                #{'{https://opendata.dwd.de/weather/lib/pointforecast_dwd_extension_V1_0.xsd}elementName': 'WPcd1'}
                                self.trash = str(self.elem.attrib)
                                self.trash1,self.mosmix_element = self.trash.split("': '")
                                self.mosmix_element, self.trash = self.mosmix_element.split("'}")
                                #-------------------------------------------------------------
                                # Currently looking at the following key Data:
                                # Looking for the following mosmix_elements 
                                #FF : Wind Speed            [m/s]
                                #Rad1h : Global irridance   [kJ/m²]
                                #TTT : Temperature 2m above ground [Kelvin]
                                #PPPP : Pressure reduced    [Pa]
                                #-------------------------------------------------------------
                                if ('FF' == self.mosmix_element):
                                    self.FF_temp = self.elem[0].text
                                    self.FF = list (self.FF_temp.split())
                                if ('Rad1h' == self.mosmix_element):
                                    self.Rad1h_temp = self.elem[0].text
                                    self.Rad1h = list (self.Rad1h_temp.split())
                                if ('TTT' == self.mosmix_element):
                                    self.TTT_temp = self.elem[0].text
                                    self.TTT = list(self.TTT_temp.split())
                                    counter = 0 
                                    # We convert from Kelvin to Celcius...:
                                    for i in self.TTT:
                                        self.TTT[counter]=round((float(self.TTT[counter])-273.13),2)
                                        #print (self.TTT[counter])
                                        counter = counter +1
                                if ('PPPP' == self.mosmix_element):
                                    self.PPPP_temp = self.elem[0].text
                                    self.PPPP = list (self.PPPP_temp.split())
                    
                    
                    #------------------------------------
                    # Define empty array                
                    self.mosmixdata =[]
                    for self.j in range(6):                                      #Right now we have timevalue, myTZtimestamp, self.FF Rad1h TTT PPPP
                        self.column = []
                        self.counter = 0
                        for self.i in self.timevalue:
                            self.column.append(0)
                        self.mosmixdata.append(self.column)
                    #------------------------------------
                    #Populate values
                    counter = 0
                    
                    for self.i in self.timevalue:
                        #self.myTZtimestamp = self.connvertDWDtimestamptoINT(self.timevalue[counter])
                        self.myTZtimestamp = self.changeDWDTimestamp(self.timevalue[counter])
                        
                        self.mosmixdata[0][counter]=self.timevalue[counter]
                        self.mosmixdata[1][counter]=self.myTZtimestamp
                        self.mosmixdata[2][counter]=self.Rad1h[counter]
                        self.mosmixdata[3][counter]=self.TTT[counter]
                        self.mosmixdata[4][counter]=self.PPPP[counter]
                        self.mosmixdata[5][counter]=self.FF[counter]
                        counter = counter + 1
                    #------------------------------------------
                    # START PrintOutput
                    if (self.PrintOutput == 1):
                        try:
                            self.cols = len(self.mosmixdata)
                            rows = 0
                            if self.cols:
                                self.rows = len(self.mosmixdata[0])
                            self.MosmixFileFirsttimestamp = self.mosmixdata[1][0]
                            #print ("My first stamp from the file is:",self.mosmixdata[0][0],"Endstring",self.mosmixdata[1][0] )       
                            #print ("-------------------------------------------------")
                            #print (self.mosmixdata)
                            self.indexcounter_addrows=1
                            self.MyWeathervalues = {}
        
                            print ("Here is the raw data  what we got from DWD :")
                            for j in range(self.rows):
                                if (self.indexcounter_addrows >0):                                       #We are adding from the point onward - see self.indexcounter_addrows if check below
                                    #print ("counting indices", self.indexcounter_addrows)
                                    self.MyWeathervalues.update({'mydatetime':self.mosmixdata[0][j]})
                                    self.MyWeathervalues.update({'myTZtimestamp':self.mosmixdata[1][j]})
                                    self.MyWeathervalues.update({'Rad1h':self.mosmixdata[2][j]})
                                    self.MyWeathervalues.update({'TTT':self.mosmixdata[3][j]})
                                    self.MyWeathervalues.update({'PPPP':self.mosmixdata[4][j]})
                                    self.MyWeathervalues.update({'FF':self.mosmixdata[5][j]})  
                                    print ('mydatetime',self.mosmixdata[0][j],'myTZtimestamp ',self.mosmixdata[1][j],'Rad1h ',self.mosmixdata[2][j],'TTT ',self.mosmixdata[3][j], 'PPPP',self.mosmixdata[4][j],'FF',self.mosmixdata[5][j])
                        except Exception as ErrorPrintOutput:
                            print ("Shit happened  ?", ErrorPrintOutput)
                            logging.error ("%s %s", ",subroutine dwdforecast final exception : ", ErrorPrintOutput)
                    #------------------------------------------
                    # START Processing data for PVLIB
                    try:                        
                        self.mycolumns= {'mydatetime':np.array(self.mosmixdata[0]),'myTZtimestamp':np.array(self.mosmixdata[1]),'Rad1h':np.array(self.mosmixdata[2]),'TTT':np.array(self.mosmixdata[3]),'PPPP':np.array(self.mosmixdata[4]),'FF':np.array(self.mosmixdata[5])}
                        self.PandasDF= pd.DataFrame(data=self.mycolumns)
                        self.PandasDF.Rad1h = self.PandasDF.Rad1h.astype(float) #Need to ensure we get a float value from Rad1h
                        self.PandasDF.FF = self.PandasDF.FF.astype(float)
                        self.PandasDF.PPPP = self.PandasDF.PPPP.astype(float)
                        self.PandasDF['Rad1wh'] = 0.277778*self.PandasDF.Rad1h  #Converting from KJ/m² to Wh/m² -and adding as new column Rad1wh
                        self.PandasDF.myTZtimestamp = pd.to_datetime(pd.Series(self.PandasDF.myTZtimestamp))
                        # A horrific hack to get the time series working
                        self.first = self.PandasDF.myTZtimestamp.iloc[0]
                        self.last  = self.PandasDF.myTZtimestamp.index[-1]
                        self.last  = self.PandasDF.myTZtimestamp.iloc[self.last]
                        #Gathering time series from start - and end hours (240 rows):
                        self.local_timestamp= pd.date_range(start=self.first, end=self.last, freq='1h',tz='Europe/Berlin')
                        self.PandasDF['Rad1wh'] = 0.277778*self.PandasDF.Rad1h
                        self.PandasDF['Rad1Energy'] = self.PVtotalEfficiency*self.PandasDF.Rad1h
                        self.PandasDF.index = self.local_timestamp
                        #Now creating list of unixtimestamps
                        self.local_unixtimestamp= []
                        self.i = 0 
                        for self.elems in (self.local_timestamp):
                            self.local_unixtimestamp.append(time.mktime(self.local_timestamp[self.i].timetuple()))
                            self.i = self.i+1                      
                        self.PandasDF['mytimestamp'] = np.array(self.local_unixtimestamp)

                        # =============================================================================
                        # STARTING  SOLAR POSITION AND ATMOSPHERIC MODELING
                        # =============================================================================
                        self.solpos          = pvlib.solarposition.get_solarposition(time      = self.local_timestamp, 
                                                                                latitude  = self.mylatitude,
                                                                                longitude = self.mylongitude,
                                                                                altitude  = self.myaltitude)
                        self.myGHI =self.PandasDF.Rad1wh                      
                        # DNI and DHI calculation from GHI data
                        self.DNI = pvlib.irradiance.disc(ghi= self.PandasDF.Rad1wh, solar_zenith = self.solpos.zenith, datetime_or_doy = self.local_timestamp, pressure=self.PandasDF.PPPP, min_cos_zenith=0.065, max_zenith=87, max_airmass=12)
                        self.DHI = self.PandasDF.Rad1wh - self.DNI.dni*np.cos(np.radians(self.solpos.zenith.values))
                        self.dataheader= {'ghi': self.PandasDF.Rad1wh,'dni': self.DNI.dni,'dhi': self.DHI,'temp_air':self.PandasDF.TTT,'wind_speed':self.PandasDF.FF}
                        self.mc_weather   = pd.DataFrame(data=self.dataheader)
                        self.mc_weather.index =self.local_timestamp 
                        #Simulating the PV system using pvlib modelchain 
                        self.myModelChain = ModelChain(self.mysolarsystem, self.mypvliblocation,aoi_model='no_loss',orientation_strategy="None",spectral_model='no_loss')
                        self.myModelChain.run_model(times=self.mc_weather.index, weather=self.mc_weather)
                        self.PandasDF['ACSim']= self.myModelChain.ac
                        self.PandasDF['CellTempSim']= self.myModelChain.cell_temperature
                        #modelchain provides DC data too - but no doc was found for the other values below
                        #i_sc        v_oc          i_mp        v_mp         p_mp           i_x          i_xx
                        self.PandasDF['DCSim']= self.myModelChain.dc.p_mp
                        if (self.CSVOutput ==1):
                            try:
                                self.PandasDF.to_csv(self.CSVFile)
                            except Exception as ErrorCSVOutput:
                                print ("Shit happened  ?", ErrorCSVOutput)
                                logging.error ("%s %s", ",subroutine dwdforecast  exception during CSVOutput : ", ErrorCSVOutput)
                        if (self.PrintOutput == 1):
                            try:
                                print ("Here are the combined results from DWD - as well as PVLIB:")
                                print (self.PandasDF)
                            except Exception as ErrorPrintOutput:
                                print ("Shit happened  ?", ErrorPrintOutput)
                                logging.error ("%s %s", ",subroutine dwdforecast  exception during CSVOutput : ", ErrorPrintOutput)

                        # =============================================================================
                        # STARTING  Database Processing
                        # =============================================================================
                        if (self.DBOutput == 1):
                            self.Databaselasttimestamp= self.findlastDBtimestamp(self.cur, "dwd")
                            print ("self.Databaselasttimestamp",self.Databaselasttimestamp)
                            self.PandasDFFirstTimestamp = self.PandasDF['mytimestamp'].iloc[0]
                            self.Database_found_filetimestamp = self.checkTimestampExistence(self.cur, "dwd", int(self.PandasDFFirstTimestamp))
                            print ("self.Database_found_filetimestamp",self.Database_found_filetimestamp)
                            self.indexcounter_addrows=0     #pure initialization
                            self.MyWeathervalues ={}
                            for index, row in self.PandasDF.iterrows():
                                self.PandasDFFirstTimestamp = row['mytimestamp']
                                self.Database_found_filetimestamp = self.checkTimestampExistence(self.cur, "dwd", int(self.PandasDFFirstTimestamp))
                                if (self.Database_found_filetimestamp ==0):
                                    self.MyWeathervalues.update({'mydatetime':row['mydatetime'],'Rad1h':row['Rad1h'],'TTT':row['TTT'],'PPPP':row['PPPP'],'FF':row['FF'],'Rad1wh':row['Rad1wh'],'Rad1Energy':row['Rad1Energy'],'mytimestamp':row['mytimestamp'],'ACSim':row['ACSim'],'CellTempSim':row['CellTempSim'],'DCSim':row['DCSim']})
                                    self.addsingleRow2DB(self.cur, "dwd", self.MyWeathervalues)
                                """    
                                if (self.Databaselasttimestamp == int(row['mytimestamp'])): # We already have this timestamp in the database
                                    self.indexcounter_addrows = self.indexcounter_addrows+1
                                    print ("We have in DB",self.Databaselasttimestamp)

                                if (self.Database_found_filetimestamp == 0):                             #Meaning We did not find any data in the database table - so we just "add"
                                    print ("Populating clean table ??", self.Database_found_filetimestamp, )
                                    self.MyWeathervalues.update({'mydatetime':row['mydatetime'],'Rad1h':row['Rad1h'],'TTT':row['TTT'],'PPPP':row['PPPP'],'FF':row['FF'],'Rad1wh':row['Rad1wh'],'Rad1Energy':row['Rad1Energy'],'mytimestamp':row['mytimestamp'],'ACSim':row['ACSim'],'CellTempSim':row['CellTempSim'],'DCSim':row['DCSim']})
                                    self.addsingleRow2DB(self.cur, "dwd", self.MyWeathervalues)
                                """                                    
                                if (self.Database_found_filetimestamp == 1):                           #Meaning we found the timestamp and need to update from the point onward...
                                    self.MyWeathervalues.update({'mydatetime':row['mydatetime'],'Rad1h':row['Rad1h'],'TTT':row['TTT'],'PPPP':row['PPPP'],'FF':row['FF'],'Rad1wh':row['Rad1wh'],'Rad1Energy':row['Rad1Energy'],'mytimestamp':row['mytimestamp'],'ACSim':row['ACSim'],'CellTempSim':row['CellTempSim'],'DCSim':row['DCSim']})
                                    #self.updatesingleRowinDB(self.cur, "dwd",
                                    #self.updatesingleRowinDB(self.cur, "dwd", TTT, Rad1h, FF, PPPP, mytimestamp, Rad1Energy, ACSim, DCSim, CellTempSim)
                                    self.updatesingleRowinDB(self.cur, "dwd", row['TTT'], row['Rad1h'], row['FF'], row['PPPP'], row['mytimestamp'], row['Rad1Energy'], row['ACSim'], row['DCSim'], row['CellTempSim'], row['Rad1wh'])
                        # =============================================================================                            
                        self.myTZtimestamp = connvertINTtimestamptoDWD(self.mynewtime)
                        logging.debug ("%s %s %s %s", ",Subroutine dwdforecast -we have used DWD file from time : ", self.mynewtime, " ", self.myTZtimestamp)
                    except Exception as ErrorDWDArray:
                        print ("Shit happened  ?", ErrorDWDArray)
                        logging.error ("%s %s", ",subroutine dwdforecast final exception : ", ErrorDWDArray)
                    logging.debug("%s %s", "From dwdforecast - we have found a true commit and have updated the database at the following dwd time :", self.mynewtime)
                    time.sleep(self.sleeptime)          # We are putting in a sleep 
                    self.myqueue.put(self.mynewtime)
                else:
                    pass
                    #print("No new data.....")
                time.sleep(self.sleeptime)              # We are pausing to not constantly cause internet traffic
            print ("Thread is going down ...")
    except Exception as ExceptionError:
            print ("XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX")
            print ("XXX-Aus Subroutine dwdforecast -verrant ? ", ExceptionError)
            print ("XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX")
            logging.error("%s %s", ",subroutine dwdforecast final exception : ", ExceptionError)

                    

if __name__ == "__main__":

    logging.basicConfig(filename="dwd_debug.txt",level=logging.DEBUG)
    #
    """
    Interaction can be 'Simple' - or 'Complex'
    Simple : Try to get weather data once only - then terminate
    Complex : Start a seperate queue that continuously polls the DWD server on the internet to get updated data 
    """
    Interaction = 'Simple' #Interaction can be 'Simple' - or 'Complex'
    #

    
    
    #-----------------------------------------------------------------
    # START Queue (To read dwd values and populate them to database):
    try:
        myQueue1 = queue.Queue()                                               
        myThread1= dwdforecast(myQueue1)                          
        myThread1.start()                                                             
        while myQueue1.empty():                                                  
            print(" Waiting on DWD dwdforecastdata Queue results to tell it is started...")
            logging.info("%s " ",Main :Waiting on Queue results to be populated ...")
            time.sleep(1)
        # Queue End (To read values from DWD)
        #_________________________________________________________________
        i = 0 
            
        try:
            while i <1: 
                if not myQueue1.empty():                                      # Falls was in der Queue steht machen wir was
                    quelength = myQueue1.qsize()                               # Wenn da viele Werte angelaufen sind, nehmen wir jetzt einfach den Letzten
                    #print ("LAENGE der QUEUE -XXXXXXXXXXXXXXXXXXXXXXX : ", quelength) 
                    logging.info("%s %s " ,",Main :Queue length is : ", quelength) 
                    
                    for x in range (0,quelength):
                        LastDWDtimestamp = myQueue1.get()                     # Das ist die magische Zeile in der wir den Wert aus der Queue abholen 
                        mylasttimestamp = connvertINTtimestamptoDWD(LastDWDtimestamp)
                    print ("From Main : DWD File access I checked /  got uploaded by DWD was at :", LastDWDtimestamp,mylasttimestamp )
                if (Interaction == 'Simple'):   
                    print ("Interaction is Simple - processing once only")
                    i = i +1
                else:
                    pass
                time.sleep(1)
            time.sleep(60)
            myThread1.event.set()
            print ("Closing thread & exiting")
        except KeyboardInterrupt:
            #Abfangen, wenn der Anwender Ctrl-C drueckt 
            print (" Sub - User is trying to kill me ...  \n") 
            myThread1.event.set()
            print ("Thread from Sub ... stopped")
        except Exception as OtherExceptionError:  
            print ("hit some other error....    !", OtherExceptionError)
            myThread1.event.set()
            
                
    except KeyboardInterrupt:
        #In case user hits Ctrl-C  
        print ("User hit Ctrl-C - and tries to kill me ...- starting to signal thread termination \n") 
        myThread1.event.set()
    except Exception as FinalExceptionError:  
        print ("I am clueless ... Hit some other error ....    !", FinalExceptionError)
        myThread1.event.set()
        
 
 


