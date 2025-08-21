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
<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0 Transitional//EN">
<HTML>
<HEAD>
<TITLE>DayTrader Registration</TITLE>
<link rel="shortcut icon" href="./favicon.ico" />
</HEAD>
<BODY bgcolor="#ffffff" link="#000099">
    <%@ page session="false"%>
    <%
        String blank = "";
        String fakeCC = "123-fake-ccnum-456";
        String fullname = request.getParameter("Full Name");
        String snailmail = request.getParameter("snail mail");
        String email = request.getParameter("email");
        String userID = request.getParameter("user id");
        String money = request.getParameter("money");
        String creditcard = request.getParameter("Credit Card Number");
        String results = (String) request.getAttribute("results");
    %>
    <TABLE style="font-size: smaller">
        <TBODY>
            <TR>
                <TD bgcolor="#c93333" align="left" width="640"
                    height="10"><B><FONT color="#ffffff">DayTrader
                            Register</FONT></B></TD>
                <TD align="center" bgcolor="#000000" width="100"
                    height="10"><FONT color="#ffffff"><B>DayTrader</B></FONT></TD>
            </TR>
        </TBODY>
    </TABLE>
    <TABLE width="740" height="30">
        <TBODY>
            <TR>
                <TD align="center"></TD>
                <TD><FONT color="#ff3333"><%=results == null ? blank : results%></FONT></TD>
                <TD></TD>
            </TR>
        </TBODY>
    </TABLE>
    <TABLE width="665">
        <TBODY>
            <TR>
                <TD width="2%" bgcolor="#e7e4e7"></TD>
                <TD width="98%" colspan="8"><B>Register</B>
                    <HR></TD>
            </TR>
        </TBODY>
    </TABLE>
    <FORM action="app">
        <TABLE width="665">
            <TBODY align="right">
                <TR>
                    <TD width="2%" bgcolor="#e7e4e7" rowspan="11"></TD>
                    <TD width="33%" colspan="4" align="right"><FONT
                        COLOR="#FF0000">*</FONT><B>Full name:</B></TD>
                    <TD width="20%" colspan="2" align="right"><INPUT
                        size="40" type="text" name="Full Name"
                        value="<%=fullname == null ? blank : fullname%>"></TD>
                    <TD width="2%" bgcolor="#e7e4e7" rowspan="11"></TD>
                </TR>
                <TR>
                    <TD colspan="4" align="right"><FONT
                        COLOR="#FF0000">*</FONT><B>Address:</B></TD>
                    <TD colspan="2" align="right"><INPUT size="40"
                        type="text" name="snail mail"
                        value="<%=snailmail == null ? blank : snailmail%>"></TD>
                </TR>
                <TR>
                    <TD colspan="4" align="right"><FONT
                        COLOR="#FF0000">*</FONT><B>E-Mail address:</B></TD>
                    <TD colspan="2" align="right"><INPUT size="40"
                        type="text" name="email"
                        value="<%=email == null ? blank : email%>"></TD>
                </TR>
                <TR>
                    <TD colspan="4">&nbsp;</TD>
                    <TD colspan="2" align="right">&nbsp;</TD>
                </TR>
                <TR>
                    <TD colspan="4" align="right"><FONT
                        COLOR="#FF0000">*</FONT><B>User ID:</B></TD>
                    <TD colspan="2" align="right"><INPUT size="40"
                        type="text" name="user id"
                        value="<%=userID == null ? blank : userID%>"></TD>
                </TR>
                <TR>
                    <TD colspan="4" align="right"><B><FONT
                            COLOR="#FF0000">*</FONT>Password:</B></TD>
                    <TD colspan="2" align="right"><INPUT size="40"
                        type="password" name="passwd"></TD>
                </TR>
                <TR>
                    <TD colspan="4" align="right"><B><FONT
                            COLOR="#FF0000">*</FONT>Confirm password:</B></TD>
                    <TD colspan="2" align="right"><INPUT size="40"
                        type="password" name="confirm passwd"></TD>
                </TR>
                <TR>
                    <TD colspan="4">&nbsp;</TD>
                    <TD colspan="2" align="right">&nbsp;</TD>
                </TR>
                <TR>
                    <TD colspan="4" align="right"><FONT
                        COLOR="#FF0000">*</FONT><B>Opening account
                            balance:</B></TD>
                    <TD colspan="2" align="right">$<B> </B><INPUT
                        size="20" type="text" name="money"
                        value='<%= money==null ? "10000" : money %>'></TD>
                </TR>
                <TR>
                    <TD colspan="4" align="right"><B><FONT
                            COLOR="#FF0000">*</FONT>Credit card number:</B></TD>
                    <TD colspan="2" align="right">&nbsp;&nbsp;<INPUT
                        size="40" type="text" name="Credit Card Number"
                        value="<%= creditcard==null ? fakeCC : creditcard %>"
                        readonly></TD>
                </TR>
                <TR>
                    <TD align="center"></TD>
                    <TD align="center"></TD>
                    <TD align="center"></TD>
                    <TD align="center"></TD>
                    <TD align="center"></TD>
                    <TD align="center"><INPUT type="submit"
                        value="Submit Registration"></TD>
                </TR>
                <TR>
                    <TD align="right" colspan="6"></TD>
                </TR>
            </TBODY>
        </TABLE>
        <INPUT type="hidden" name="action" value="register">
    </FORM>
    <TABLE height="54" style="font-size: smaller">
        <TBODY>
            <TR>
                <TD colspan="2">
                    <HR>
                </TD>
            </TR>
            <TR>
                <TD colspan="2"></TD>
            </TR>
            <TR>
                <TD bgcolor="#c93333" align="left" width="640"
                    height="10"><B><FONT color="#ffffff">DayTrader
                            Register</FONT></B></TD>
                <TD align="center" bgcolor="#000000" width="100"
                    height="10"><FONT color="#ffffff"><B>DayTrader</B></FONT></TD>
            </TR>
        </TBODY>
    </TABLE>
</BODY>
</HTML>
