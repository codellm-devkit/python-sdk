# Daytrader8: Load Testing
This readme explains how to setup DB2 and load test the Daytrader 8 application with Open Liberty.

## Prerequisites

1. Open Liberty Machine (Server with Open Liberty unzipped at <OPENLIBERTY_HOME>, and a default profile created)
2. DB2 Machine (Server running DB2)
3. Driver Machine (Server running JMeter with jmeter files copied to <JMETER_HOME>/bin, and the WebSocket plugin copied to <JMETER_HOME>/lib/ext, see jmeter_files)

## Setup Open Liberty 

Build Daytrader8;
[Download](https://github.com/OpenLiberty/sample.daytrader8/archive/master.zip) or clone this repo - to clone:
```
git clone git@github.com:OpenLiberty/sample.daytrader8.git
```

From inside the sample.daytrader8 directory, build the application:
```
mvn clean package
```
* Copy target/io.openliberty.sample.daytrader8.war to <OPENLIBERTY_HOME>/usr/servers/defaultServer/apps
* Copy src/main/liberty/config/server.xml_db2 to <OPENLIBERTY_HOME>/usr/servers/defaultServer/server.xml  (overwrite)
* Copy db2 jars from the DB2 Machine to <OPENLIBERTY_HOME>/usr/shared/db2jars
```
db2jcc4.jar
db2jcc_license_cu.jar
```

Create <OPENLIBERTY_HOME>/usr/servers/defaultServer/jvm.options and add any JVM Arguments desired.
```
-Xms1024m
-Xmx1024m
```

Set these environment variables. matching your environment (or hard code them in the server.xml):
```
contextRoot=daytrader
dbUser
dbPass
tradeDbHost
tradeDbPort
tradeDbName
```

## Set up DB2
Sign in to DB2 machine as db2 user and create tradedb database
```
db2 create db tradedb
```

Note: When creating the database, DB2_APM_PERFORMANCE needs to be off, or you'll get: SQL1803N  The requested operation cannot be executed in "No Package Lock"
```
db2set DB2_APM_PERFORMANCE=
db2stop
db2start
```
 
## Load Database

Start OpenLiberty:
```
<OPENLIBERTY_HOME>/bin/server start --clean
```
 
With a web browser go to http://openliberty-hostname:9080/daytrader/configure.html
```
Click (Re)-create  DayTrader Database Tables and Indexes
Click (Re)-populate  DayTrader Database
```
 
Stop Liberty Server
```
<OPENLIBERTY_HOME>/bin/server stop
```
 
On the DB2 Server, Put the following into a script (backupTradeDB.sh) and run as the db2 user
```
DB=tradedb
mkdir -p ~/backups/${DB}
db2 update dbm cfg using notifylevel 0
db2 update dbm cfg using diaglevel 1
db2 update dbm cfg using NUM_POOLAGENTS 500 automatic MAX_COORDAGENTS 500 automatic MAX_CONNECTIONS 500 automatic

db2 -v update db cfg for ${DB} using MAXLOCKS 100 LOCKLIST 100000

db2 connect to ${DB}
db2 update db cfg for ${DB} using maxappls 500 automatic
db2 update db cfg for ${DB} using logfilsiz 8000
db2 update db cfg for ${DB} using logprimary 32
db2 update db cfg for ${DB} using dft_queryopt 0

db2 update db cfg for ${DB} using softmax 3000
db2 update db cfg for ${DB} using chngpgs_thresh 99

db2 -v alter bufferpool IBMDEFAULTBP size -1
db2 -v connect reset
db2 -v update db cfg for ${DB} using BUFFPAGE 262144

db2set DB2_APM_PERFORMANCE=ON
db2set DB2_KEEPTABLELOCK=CONNECTION
db2set DB2_USE_ALTERNATE_PAGE_CLEANING=ON
db2set DB2_MINIMIZE_LISTPREFETCH=YES
db2set DB2_LOGGER_NON_BUFFERED_IO=OFF

db2 connect reset
db2 terminate

db2stop force
db2start

db2 connect to ${DB}
db2 reorgchk update statistics
db2 connect reset

db2 terminate
db2 backup db tradedb to ~/backups/${DB}
```

Put the following into a script (restoreTradeDB.sh) to run before each server restart. (To make sure the database is in the same state every time)
```
db2stop force
db2start
db2 restore db tradedb from ~/backups/tradedb replace existing
``` 

Note: If disk writing/reading becomes a bottleneck, you may need to create a ramdisk and restore the database to the ramdisk.

## Apply Load
On the DB2 Server, restore the database (as db2 user)
```
restoreTradeDB.sh
```

Start Liberty
```
<OPENLIBERTY_HOME>/bin/server start
```

On the JMETER Server, Start JMeter:
```
cd <JMETER_HOME>/bin
./jmeter -n -t daytrader8.jmx -JHOST=openliberty-hostname -JDURATION=180
```

Preferably, do three 180 second warm up runs and then three 180 second measurement runs with a 30 second break in between each.

Also, a best practice is to reset the database between each run, which can be done on the configuration tab of the application.
```
http://openliberty-hostname:9080/daytrader/config?action=resetTrade
```
