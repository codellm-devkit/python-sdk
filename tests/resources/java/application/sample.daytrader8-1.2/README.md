# Java EE8: DayTrader8 Sample

This sample contains the DayTrader 8 benchmark, which is an application built around the paradigm of an online stock trading system. The application allows users to login, view their portfolio, lookup stock quotes, and buy or sell stock shares. With the aid of a Web-based load driver such as Apache JMeter, the real-world workload provided by DayTrader can be used to measure and compare the performance of Java Platform, Enterprise Edition (Java EE) application servers offered by a variety of vendors. In addition to the full workload, the application also contains a set of primitives used for functional and performance testing of various Java EE components and common design patterns.

DayTrader is an end-to-end benchmark and performance sample application. It provides a real world Java EE workload. DayTrader's new design spans Java EE 8.

This sample can be installed onto Liberty runtime versions 18.0.0.2 and later. A prebuilt derby database is provided in resources/data


To run this sample, first [download](https://github.com/OpenLiberty/sample.daytrader8/archive/master.zip) or clone this repo - to clone:
```
git clone git@github.com:OpenLiberty/sample.daytrader8.git
```

From inside the sample.daytrader8 directory, build and start the application in Open Liberty with the following command:
```
mvn clean package liberty:run
```

The server will listen on port 9080 by default.  You can change the port (for example, to port 9081) by adding `mvn clean package liberty:run -DtestServerHttpPort=9081` to the end of the Maven command.

Once the server is started, you should be able to access the application at:
http://localhost:9080/daytrader



## Notice

© Copyright IBM Corporation 2019.

## License

```text
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
````
