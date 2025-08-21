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
<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">
<%@ page
    import="java.io.StringWriter,
                 java.io.PrintWriter"%>

<TABLE width="740">
    <TBODY>
        <TR>
            <TD bgcolor="#c93333" align="left" width="640" height="10"><B><FONT
                    color="#ffffff">DayTrader
                        Error</FONT></B></TD>
            <TD align="center" bgcolor="#000000" width="100" height="10"><FONT
                color="#ffffff"><B>DayTrader</B></FONT></TD>
        </TR>
</TABLE>
<DIV align="left"></DIV>
<TABLE width="740">
    <TBODY>
        <TR>
            <TD width="3"></TD>
            <TD>
                <HR>
            </TD>
            <TD width="3"></TD>
        </TR>
        <TR>
            <TD bgcolor="#e7e4e7" rowspan="4" width="3"></TD>
            <TD><B><FONT color="#000000">An Error has
                        occurred during DayTrader processing</FONT><FONT
                    size="-2">.</FONT></B><BR> The
                stack trace detailing the error follows.
                <p>
                    <b>Please consult the application server error
                        logs for further details.</b>
                </p></TD>
            <TD bgcolor="#e7e4e7" width="3" rowspan="4"></TD>
        </TR>
        <TR>
            <TD><FONT size="-1"> <%
     String message = null;
     int status_code = -1;
     String exception_info = null;
     String url = null;

     try {
         Exception theException = null;
         Integer status = null;

         //these attribute names are specified by Servlet 2.2
         message = (String) request.getAttribute("javax.servlet.error.message");
         status = ((Integer) request.getAttribute("javax.servlet.error.status_code"));
         theException = (Exception) request.getAttribute("javax.servlet.error.exception");
         url = (String) request.getAttribute("javax.servlet.error.request_uri");

         // convert the stack trace to a string
         StringWriter sw = new StringWriter();
         PrintWriter pw = new PrintWriter(sw);
         theException.printStackTrace(pw);
         pw.flush();
         pw.close();

         if (message == null) {
             message = "not available";
         }

         if (status == null) {
             status_code = -1;
         } else {
             status_code = status.intValue();
         }
        
         exception_info = theException.toString();
         exception_info = exception_info + "<br>" + sw.toString();
         sw.close();
         
     } catch (Exception e) {
         e.printStackTrace();
     }

     out.println("<br><br><b>Processing request:</b>" + url);
     out.println("<br><b>StatusCode:</b> " + status_code);
     out.println("<br><b>Message:</b>" + message);
     out.println("<br><b>Exception:</b>" + exception_info);
 %>
            </FONT><FONT size="-1"> </FONT></TD>
        </TR>
        <TR>
            <TD align="left"></TD>
        </TR>
        <TR>
            <TD>
                <HR>
            </TD>
        </TR>
    </TBODY>
</TABLE>
<TABLE>
    <TBODY>
        <TR>
            <TD bgcolor="#c93333" align="left" width="640" height="10"><B><FONT
                    color="#ffffff">DayTrader
                        Error</FONT></B></TD>
            <TD align="center" bgcolor="#000000" width="100" height="10"><FONT
                color="#ffffff"><B>DayTrader</B></FONT></TD>
        </TR>
    </TBODY>
</TABLE>
