<!-- 
 * (C) Copyright IBM Corporation 2015.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
-->
<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0//EN">
<html>
<head>
<META HTTP-EQUIV="pragma" CONTENT="no-cache">
<META http-equiv="Content-Style-Type" content="text/css">
<!-- Don't cache on netscape! -->
<title>PingJsp</title>
</head>
<BODY>
    <%!String initTime = (new java.util.Date()).toString();%>
    <jsp:useBean id="ab"
        type="com.ibm.websphere.samples.daytrader.web.prims.PingBean"
        scope="request" />
    <HR>
    <FONT size="+2" color="#000066"><BR> Ping Servlet2JSP:<BR>
    </FONT>
    <FONT size="+1" color="#000066">Init time: <%=initTime%></FONT>
    <BR>
    <BR>
    <B>Message from Servlet: </B>
    <%= ab.getMsg() %>

</BODY>
</html>
