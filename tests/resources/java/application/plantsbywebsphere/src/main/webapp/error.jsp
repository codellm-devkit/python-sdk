<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0//EN">

<!--

COPYRIGHT LICENSE: This information contains sample code provided in source code form. You may copy, 
modify, and distribute these sample programs in any form without payment to IBM for the purposes of 
developing, using, marketing or distributing application programs conforming to the application 
programming interface for the operating platform for which the sample code is written. 
Notwithstanding anything to the contrary, IBM PROVIDES THE SAMPLE SOURCE CODE ON AN "AS IS" BASIS 
AND IBM DISCLAIMS ALL WARRANTIES, EXPRESS OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, ANY IMPLIED 
WARRANTIES OR CONDITIONS OF MERCHANTABILITY, SATISFACTORY QUALITY, FITNESS FOR A PARTICULAR PURPOSE, 
TITLE, AND ANY WARRANTY OR CONDITION OF NON-INFRINGEMENT. IBM SHALL NOT BE LIABLE FOR ANY DIRECT, 
INDIRECT, INCIDENTAL, SPECIAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF THE USE OR OPERATION OF THE 
SAMPLE SOURCE CODE. IBM HAS NO OBLIGATION TO PROVIDE MAINTENANCE, SUPPORT, UPDATES, ENHANCEMENTS 
OR MODIFICATIONS TO THE SAMPLE SOURCE CODE.  

(C) COPYRIGHT International Business Machines Corp., 2001,2011
All Rights Reserved * Licensed Materials - Property of IBM

-->

<html>
<head>
<title></title>
<meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1">
<meta name="DC.LANGUAGE" scheme="rfc1766" />
</head>
<body marginwidth="0" leftmargin="0" bgcolor="ffffff">

	<%@ page import="java.io.*, java.lang.reflect.*"%>

	<table width="90%">
		<tbody>
			<tr>
				<td width="2%"></td>
				<td width="98%">
					<hr>
				</td>
			</tr>
			<tr>
				<td bgcolor="#e7e4e7" rowspan="4"></td>
				<td><font color="#000000" size="+2">An Error has occured
						during PlantsByWebSphere processing</font>.</td>
			</tr>
			<tr>
				<td>
					<%
						String message = null;
						int status_code = -1;
						String exception_info = null;
						String url = null;
						String method = null;

						Object myReport = null;

						//ErrorReport is an attribute that is set in WebSphere
						//if it exists we will use it to get information about the error
						//if it does not exist we will use the attributes specified by
						//Servlet 2.2
						myReport = request.getAttribute("ErrorReport");

						int needInfo = 1;
						if (myReport != null) {
							try {
								//Using reflection here so that if the class com.ibm.websphere.servlet.error.ServletErrorReport
								//does not exist at compile time there will not be a problem
								//if this class does not exist we will juse use the attributes specified by Servlet 2.2

								Class myClass = Class.forName("com.ibm.websphere.servlet.error.ServletErrorReport");
								Method myMethod = myClass.getMethod("getErrorCode", (Class[]) null);
								Object o = myMethod.invoke(myReport, (Object[]) null);
								status_code = ((Integer) o).intValue();

								myMethod = myClass.getMethod("getMessage", (Class[]) null);
								o = myMethod.invoke(myReport, (Object[]) null);
								message = (java.lang.String) o;

								myMethod = myClass.getMethod("getStackTrace", (Class[]) null);
								o = myMethod.invoke(myReport, (Object[]) null);
								exception_info = (java.lang.String) o;
								needInfo = 0;
								method = "Using attribute of type com.ibm.websphere.servlet.error.ServletErrorReport to get information.";

							} catch (Exception e) {
								needInfo = 1;
							}

						}
						//if needInfo is set to 1 it means that using the WebSphere ServletErrorReport class has failed
						//and we must get the information in the standard manner.
						if (needInfo == 1) {
							//this means that could not find ibm class.

							Exception theException = null;
							Integer status = null;
							method = "Using attributes javax.servlet.error.message ...status_code ...exception as specified by Servlet 2.2 to get information";
							//these attribute names are specified by Servlet 2.2
							message = (String) request.getAttribute("javax.servlet.error.message");
							status = ((Integer) request.getAttribute("javax.servlet.error.status_code"));
							theException = (Exception) request.getAttribute("javax.servlet.error.exception");
							if (message == null) {
								message = "not available";
							}

							if (status == null) {
								status_code = -1;
							} else {
								status_code = status.intValue();
							}
							if (theException == null) {
								exception_info = "not available";
							} else {
								exception_info = theException.toString();
							}
						}

						try {
							url = request.getRequestURL().toString();
						} catch (Exception e) {
							url = "information not available";
						}

						//output is all done here.

						out.println("<h2>Jsp Error Page</h2>" + method);
						out.println("<br><br><b>Processing request:</b>" + url);
						out.println("<br><b>StatusCode:</b> " + status_code);
						out.println("<br><b>Message:</b>"
								+ message.replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;"));
						out.println("<br><b>Exception:</b>"
								+ exception_info.replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;"));
					%>
				</TD>
			</TR>
			<TR>
				<TD align="left">Please Check the application server log files
					for details...</TD>
			</TR>
			<TR>
				<TD>
					<HR>
				</TD>
			</TR>
		</TBODY>
	</TABLE>
	<table border="0" cellpadding="0" cellspacing="0" width="100%">
		<tr>
			<td bgcolor="ffffff" width="10"><img border="0"
				src="resources/images/1x1_trans.gif" width="10" height="1" alt=""></td>
			<td bgcolor="ffffff" width="100%"><img border="0"
				src="resources/images/pbw.jpg" width="181" height="48"
				alt="Plants by WebSphere"></td>
		</tr>
	</table>
</body>
</html>
