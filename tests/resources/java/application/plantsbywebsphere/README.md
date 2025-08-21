# sample.plantsbywebsphere
Updated Plants By WebSphere showcase sample to run on WebSphere Liberty.

This Repository is for testing the PlantsByWebSphere application
in an open source development environment.

## How to run:

1. Clone the github repo
2. Start the Liberty server and open the application in a web browser by running: 
```
./gradlew start open
```

### Collaborators:
- Dalia A. Abo Sheasha
- Ryan Gallus
- Samuel Ivanecky
- Alex Mortimer

### Overview
This repository contains the PlantsByWebSphere Java EE sample application. There are two versions of the sample application. The main branch contains the original version of PlantsByWebSphere, while the rest branch contains an updated version which is still under development.

### Original
The original version of PlantsByWebSphere is a simple Java EE application which uses CDI managed beans, Java Server Faces (JSF), and Java Server Pages (JSP). The sample runs on both TWAS and Liberty.

### Updated
The updated version of PlantsByWebSphere replaces components of the original with a more modern web application design. JSF and JSP have been replaced by JAX-RS with the application redesigned as a RESTful Web Service. The client is a simple bootstrap framework, and all client JavaScript can be found in application.js. The server's additional REST code can be found in ApplicationResource.java.

Additionally, this new version supports the use of the javaMail-1.5 feature which requires the configuration of a mailSession object in the server.xml. Below is an example mailSession configuration. Make sure to modify the the mail account (Gmail, Yahoo, etc.) settings and allow access of less secure applications in order for it to connect with PlantsByWebSphere.

```xml
<mailSession description="Test of Mail for PBW" from="youremailaddress@gmail.com" host="smtp.gmail.com" jndiName="mail/PlantsByWebSphere" mailSessionID="PBWMailTest" password="password" storeProtocol="imaps" transportProtocol="smtp" user="youremailaddress@gmail.com">
<property name="mail.smtp.auth" value="true"/>
<property name="mail.smtp.starttls.enable" value="true"/>
<property name="mail.smtp.port" value="587"/>
<property name="mail.smtp.ssl.trust" value="smtp.gmail.com">
</property>
</mailSession>
```
